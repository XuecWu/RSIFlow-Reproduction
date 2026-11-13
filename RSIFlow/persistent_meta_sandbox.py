"""Persistent Codex bubblewrap lifecycle and credential-free provider relay."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
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
        self.writable_outputs = tuple(Path(path).absolute() for path in writable_outputs)
        self.temporary.mkdir(parents=True, exist_ok=True)
        self.codex_home.mkdir(parents=True, exist_ok=True)
        for output in self.writable_outputs:
            output.mkdir(parents=True, exist_ok=True)
        roots = {
            self.project.resolve(), self.run.resolve(), self.meta_logs,
            self.codex_home.resolve(), self.temporary,
            *(output.resolve() for output in self.writable_outputs),
        }
        self.backend = BubblewrapBackend(self.repository, allowed_write_roots=tuple(sorted(roots)))
        self.runtime = None
        self.transport = None
        self.relay_directory = None
        self.relay_socket = None
        self.processes = []

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
        bwrap_evidence = self.backend.validate()
        if bwrap_evidence.get("backend") != BACKEND:
            raise SandboxUnavailable("bubblewrap backend identity mismatch")
        self.runtime = local_execution.runtime(self.config)
        self.runtime.validate()
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
        self.processes.append(process)
        return process

    def close(self) -> None:
        for process in self.processes:
            if process.poll() is not None:
                continue
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=10)
        self.processes.clear()
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
