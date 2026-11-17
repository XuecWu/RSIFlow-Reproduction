"""Persistent Codex bubblewrap lifecycle and credential-free provider relay."""
from __future__ import annotations

import ctypes
import errno
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from rsiflow.task_meta import bwrap_backend
from rsiflow.task_meta.bwrap_backend import (
    BubblewrapBackend,
    BwrapLimits,
    BwrapMount,
    BwrapRequest,
)
from rsiflow.task_meta.meta_backends import MetaBackendConfig, local_execution
from rsiflow.task_meta.meta_backends.isolation_runtime import regular_digest
from rsiflow.task_meta.meta_backends.transport import ResponsesTransport
from rsiflow.task_meta.sandbox import SandboxUnavailable

BACKEND = "linux_bwrap_seccomp_v1"
UPSTREAM = "http://token.acerobotics.com/v1"
BWRAP_MANIFEST_SHA256 = "58fe2672fd78c12025ce60a84b525019e68979b9860734e4d5cccc63e78e78f1"
BWRAP_LAUNCHER_SHA256 = "08f9904f80110b8459637925a897d5eb7cb4943313707429aea3df5b972a48d0"
_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC.syscall.restype = ctypes.c_long


def _process_group_stat(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return int(fields[2]), fields[19]
    except (OSError, IndexError, ValueError):
        return None


def _group_members(pgid):
    members = []
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            pid = int(path.parent.name)
            fields = path.read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] != "Z":
                members.append((pid, fields[19]))
        except (OSError, IndexError, ValueError):
            continue
    return members


def _pidfd_open(pid):
    descriptor = _LIBC.syscall(434, pid, 0)
    if descriptor < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return descriptor


def _pidfd_send_signal(descriptor, sig):
    if _LIBC.syscall(424, descriptor, sig, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


class PersistentMetaSandbox:
    def __init__(self, *, project, run, codex_home, meta_config, writable_outputs=()):
        self.project = Path(project).absolute()
        self.repository = self.project.parent
        self.run = Path(run).absolute()
        self.codex_home = Path(codex_home).absolute()
        self.config = MetaBackendConfig(**meta_config)
        self.meta_logs = (self.run / "meta_session").resolve()
        self.temporary = (self.run / "meta_session/tmp").resolve()
        self.empty_directory = None
        self.writable_outputs = self._validated_outputs(writable_outputs)
        self.temporary.mkdir(parents=True, exist_ok=True)
        self.codex_home.mkdir(parents=True, exist_ok=True)
        for output in self.writable_outputs:
            output.mkdir(parents=True, exist_ok=True)
        self.allowed_write_roots = {
            self.project.resolve(), self.run.resolve(), self.meta_logs,
            self.codex_home.resolve(), self.temporary,
            *(output.resolve() for output in self.writable_outputs),
        }
        self.backend = None
        self.runtime = None
        self.transport = None
        self.relay_directory = None
        self.relay_socket = None
        self.processes = []
        self.process_groups = {}
        self.process_identities = {}

    def _validated_outputs(self, declarations):
        run = self.run.resolve()
        meta_logs = self.meta_logs.resolve()
        codex_home = self.codex_home.resolve()
        protected = (run, meta_logs, codex_home, self.temporary, self.project.resolve())
        outputs = []
        for declaration in declarations:
            value = Path(declaration).absolute()
            output = value.resolve()
            if not (output.is_relative_to(run) or output.is_relative_to(meta_logs)):
                raise ValueError(f"outside experiment output roots: {output}")
            if any(output == root or output in root.parents for root in protected):
                raise ValueError(f"unsafe output overlap: {output}")
            if output.is_relative_to(codex_home) or output.is_relative_to(self.temporary):
                raise ValueError(f"unsafe output overlap: {output}")
            if any(output == other or output in other.parents or other in output.parents for other in outputs):
                raise ValueError(f"unsafe output overlap: {output}")
            existing = value
            while not os.path.lexists(existing):
                if existing == existing.parent:
                    raise ValueError("unsafe output path")
                existing = existing.parent
            for parent in (existing, *existing.parents):
                if parent.is_symlink():
                    raise ValueError(f"unsafe output symlink: {parent}")
            if output.exists() and not output.is_dir():
                raise ValueError(f"unsafe output overlap: {output}")
            outputs.append(output)
        return tuple(outputs)

    def _validate_configuration(self):
        if os.environ.get("RSIFLOW_SANDBOX_BACKEND") != BACKEND:
            raise SandboxUnavailable("persistent Meta requires explicit linux_bwrap_seccomp_v1 selection")
        expected = {
            "provider": "OpenAI",
            "model": "gpt-6-astra",
            "base_url": UPSTREAM,
            "api_key_env": "OPENAI_API_KEY",
            "execution_location": "local_chroot",
        }
        actual = {name: getattr(self.config, name) for name in expected}
        if actual != expected or self.config.allow_model_fallback or self.config.allow_provider_fallback:
            raise SandboxUnavailable("persistent Meta provider identity or fallback policy changed")
        for source, expected_hash, label in (
            (self.config.codex_executable, self.config.codex_binary_sha256, "Codex"),
            (self.config.model_catalog_json, self.config.model_catalog_sha256, "catalog"),
        ):
            if not source or not expected_hash or regular_digest(Path(source)) != expected_hash:
                raise SandboxUnavailable(f"pinned {label} hash mismatch")

    def start(self) -> None:
        if self.transport is not None:
            return
        self._validate_configuration()
        trusted_files = (
            (self.project / "configs/bwrap_isolation.json", "bwrap_isolation.json",
             BWRAP_MANIFEST_SHA256, 0o444),
            (self.project / "runtime/rsiflow/task_meta/bwrap_launcher.py", "bwrap_launcher.py",
             BWRAP_LAUNCHER_SHA256, 0o444),
        )
        self.runtime = local_execution.runtime(self.config, trusted_files=trusted_files)
        self.runtime.validate()
        self.backend = BubblewrapBackend(
            self.repository,
            manifest_path=self.runtime.root / "bwrap_isolation.json",
            launcher_path=self.runtime.root / "bwrap_launcher.py",
            launcher_sha256=BWRAP_LAUNCHER_SHA256,
            allowed_write_roots=tuple(sorted(self.allowed_write_roots)),
        )
        bwrap_evidence = self.backend.validate()
        if bwrap_evidence.get("backend") != BACKEND:
            raise SandboxUnavailable("bubblewrap backend identity mismatch")
        secret = os.environ.get(self.config.api_key_env, "")
        if not secret:
            raise SandboxUnavailable(f"{self.config.api_key_env} is required by the host transport")
        evidence = self.run / "meta_session/provider"
        evidence.mkdir(parents=True, exist_ok=True)
        self.relay_directory = Path(tempfile.mkdtemp(prefix="rsiflow-meta-relay-", dir="/tmp"))
        self.relay_socket = self.relay_directory / "transport.sock"
        self.empty_directory = self.relay_directory / "empty"
        self.empty_directory.mkdir()
        try:
            self.transport = ResponsesTransport(self.config, secret, evidence_dir=evidence)
            self.transport.start(self.relay_socket)
        except BaseException:
            self.transport = None
            shutil.rmtree(self.relay_directory, ignore_errors=True)
            self.relay_directory = self.relay_socket = None
            raise

    def _mounts(self):
        root = self.runtime.root
        mounts = [
            BwrapMount(root / "usr", "/usr"),
            BwrapMount(root / "usr/bin", "/bin"),
            BwrapMount(root / "usr/lib", "/lib"),
            BwrapMount(root / "usr/lib64", "/lib64"),
            BwrapMount(root / "etc", "/etc"),
            BwrapMount(root / "dev", "/dev"),
            BwrapMount(root / "proc", "/proc"),
            BwrapMount(root / "codex", "/codex"),
            BwrapMount(root / "catalog.json", "/catalog.json"),
            BwrapMount(root / "bridge.py", "/bridge.py"),
            BwrapMount(self.relay_directory, "/run/rsiflow"),
        ]
        writable = [
            BwrapMount(self.project.resolve(), str(self.project), "rw"),
            BwrapMount(self.run.resolve(), str(self.run), "rw"),
            BwrapMount(self.meta_logs, str(self.meta_logs), "rw"),
            BwrapMount(self.codex_home.resolve(), "/codex_home", "rw"),
            BwrapMount(self.temporary, "/tmp", "rw"),
            *(BwrapMount(output.resolve(), str(output), "rw") for output in self.writable_outputs),
        ]
        mounts.extend(writable)
        raw_data = self.project / "runtime/data"
        if raw_data.exists():
            mounts.append(BwrapMount(self.empty_directory, str(raw_data)))
        return tuple(mounts)

    def _environment(self):
        return {
            "PATH": "/usr/bin:/bin",
            "HOME": "/codex_home",
            "CODEX_HOME": "/codex_home",
            "LANG": "C.UTF-8",
            "TMPDIR": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
            "RSIFLOW_RELAY_KEY": "pipe-only",
            "TOKIO_WORKER_THREADS": "4",
            "RAYON_NUM_THREADS": "4",
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }

    def _limits(self):
        budget = self.config.budget
        return BwrapLimits(
            wall_seconds=budget.wall_time_seconds,
            cpu_seconds=budget.wall_time_seconds * 8,
            memory_bytes=budget.memory_bytes,
            output_bytes=budget.max_event_bytes,
            file_bytes=budget.max_event_bytes,
            processes=budget.max_processes,
            file_descriptors=256,
        )

    def command(self, argv, cwd=None):
        self.start()
        self.runtime.validate()
        request = BwrapRequest(
            profile="persistent_meta",
            argv=tuple(argv),
            cwd=str(cwd or self.repository),
            env=self._environment(),
            limits=self._limits(),
            mounts=self._mounts(),
        )
        command = self.backend.command(request)
        if not isinstance(command, bwrap_backend.BwrapCommand):
            raise TypeError("persistent Meta requires descriptor-pinned BwrapCommand")
        proc_index = command.index("/proc")
        proc_mount = command[proc_index - 2:proc_index + 1]
        del command[proc_index - 2:proc_index + 1]
        launcher_index = command.index("/opt/rsiflow/bwrap_launcher.py")
        command[launcher_index + 1:launcher_index + 1] = proc_mount
        return command

    def spawn(self, argv, cwd, stdin, stdout, stderr):
        if tuple(argv) != ("/codex", "app-server", "--listen", "stdio://"):
            raise ValueError("persistent Meta spawn only supports the Codex app-server stdio command")
        inner = ("/usr/bin/python3", "/bridge.py", *argv)
        command = self.command(inner, cwd=cwd)
        try:
            process = subprocess.Popen(
                command,
                cwd=self.repository,
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                text=True,
                bufsize=1,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                pass_fds=command.pass_fds,
                start_new_session=True,
            )
        finally:
            command.close()
        self._track_process(process)
        return process

    def _track_process(self, process):
        self.processes.append(process)
        try:
            try:
                descriptor = _pidfd_open(process.pid)
            except OSError as exc:
                if exc.errno != errno.ENOSYS:
                    raise
                leader = _process_group_stat(process.pid)
                if leader is None or leader[0] != process.pid:
                    raise RuntimeError("owned process group leader identity unavailable")
                if _process_group_stat(process.pid) != leader:
                    raise RuntimeError("ambiguous process group leader identity")
                self.process_groups[process.pid] = None
                self.process_identities[process.pid] = leader[1]
            else:
                self.process_groups[process.pid] = descriptor
        except BaseException:
            self._signal_group(process.pid, signal.SIGKILL)
            if process.poll() is None:
                process.wait(timeout=10)
            self.processes.remove(process)
            raise

    def _signal_group_without_pidfd(self, pgid, leader_start, sig):
        expected_leader = (pgid, leader_start)

        def check_leader():
            current = _process_group_stat(pgid)
            if current is not None and current != expected_leader:
                raise RuntimeError("process group leader identity changed")

        check_leader()
        listed = _group_members(pgid)
        members = set(listed)
        if len(members) != len(listed):
            raise RuntimeError("ambiguous process group membership")
        expected = {pid: (pgid, start_time) for pid, start_time in members}
        for pid, identity in expected.items():
            if _process_group_stat(pid) != identity:
                raise RuntimeError("process is not in the owned group or its identity changed")
        if set(_group_members(pgid)) != members:
            raise RuntimeError("ambiguous process group membership snapshot")
        check_leader()
        for pid, identity in expected.items():
            current = _process_group_stat(pid)
            if current is None:
                continue
            if current != identity:
                raise RuntimeError("process is not in the owned group or its identity changed")
            try:
                os.kill(pid, sig)
            except OSError as exc:
                if exc.errno == errno.ESRCH:
                    continue
                raise
            current = _process_group_stat(pid)
            if current is not None and current != identity:
                raise RuntimeError("process identity changed after signal")
        if not set(_group_members(pgid)).issubset(members):
            raise RuntimeError("ambiguous process group membership after signal")
        check_leader()

    def _signal_group(self, pgid, sig):
        if pgid in self.process_identities:
            self._signal_group_without_pidfd(pgid, self.process_identities[pgid], sig)
            return
        for pid, start_time in _group_members(pgid):
            try:
                descriptor = _pidfd_open(pid)
            except OSError as exc:
                if exc.errno == errno.ESRCH:
                    continue
                raise
            try:
                if _process_group_stat(pid) == (pgid, start_time):
                    try:
                        _pidfd_send_signal(descriptor, sig)
                    except OSError as exc:
                        if exc.errno != errno.ESRCH:
                            raise
            finally:
                os.close(descriptor)

    def close(self) -> None:
        try:
            for process in self.processes:
                pgid = process.pid
                self._signal_group(pgid, signal.SIGTERM)
                deadline = time.monotonic() + 10
                while _group_members(pgid) and time.monotonic() < deadline:
                    time.sleep(0.05)
                if _group_members(pgid):
                    self._signal_group(pgid, signal.SIGKILL)
                    deadline = time.monotonic() + 2
                    while _group_members(pgid) and time.monotonic() < deadline:
                        time.sleep(0.05)
                if process.poll() is None:
                    process.wait(timeout=10)
                descriptor = self.process_groups.pop(pgid, None)
                self.process_identities.pop(pgid, None)
                if descriptor is not None:
                    os.close(descriptor)
        finally:
            self.processes.clear()
            for descriptor in self.process_groups.values():
                if descriptor is not None:
                    os.close(descriptor)
            self.process_groups.clear()
            self.process_identities.clear()
            if self.transport is not None:
                self.transport.close()
                self.transport = None
            if self.relay_socket is not None:
                self.relay_socket.unlink(missing_ok=True)
            if self.relay_directory is not None:
                shutil.rmtree(self.relay_directory, ignore_errors=True)
            self.relay_directory = self.relay_socket = None

    def identity(self) -> dict:
        self.start()
        self.runtime.validate()
        return {
            "backend": BACKEND,
            "execution_location": "local_chroot",
            "provider_credentials_in_child": False,
            "bwrap": self.backend.validate(),
            "static_runtime": self.runtime.identity(),
            "codex_sha256": self.config.codex_binary_sha256,
            "catalog_sha256": self.config.model_catalog_sha256,
        }
