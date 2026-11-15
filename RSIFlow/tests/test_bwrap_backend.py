import hashlib
import importlib
import json
import os
import resource
import shutil
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "RSIFlow/runtime"))
sys.path.insert(0, str(PROJECT / "reproduction/scripts"))

from rsiflow.task_meta import bwrap_launcher
from rsiflow.task_meta.bwrap_backend import (
    BubblewrapBackend,
    BwrapLimits,
    BwrapMount,
    BwrapRequest,
)
from rsiflow.task_meta.sandbox import SandboxUnavailable

install_bwrap = importlib.import_module("install_bwrap")


MANIFEST = {
    "schema_version": 1,
    "backend": "linux_bwrap_seccomp_v1",
    "binary": {
        "path": "assets/isolation/bin/bwrap",
        "version": "0.6.1",
        "sha256": "d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca",
    },
    "profiles": {
        "candidate": {"seccomp": "candidate", "allow_threads": False, "allow_processes": False},
        "candidate_session": {"seccomp": "candidate", "allow_threads": False, "allow_processes": False},
        "native_benchmark": {"seccomp": "native", "allow_threads": True, "allow_processes": False},
        "persistent_meta": {"seccomp": "meta", "allow_threads": True, "allow_processes": True},
    },
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_open_identity(command, source, expected_path):
    assert source.startswith("/proc/self/fd/")
    descriptor = int(source.rsplit("/", 1)[1])
    assert descriptor in command.pass_fds
    actual = os.fstat(descriptor)
    expected = os.lstat(expected_path)
    assert (actual.st_dev, actual.st_ino) == (expected.st_dev, expected.st_ino)


def make_root(tmp_path):
    root = tmp_path / "project"
    binary = root / "assets/isolation/bin/bwrap"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nprintf 'bubblewrap 0.6.1\\n'\n", encoding="utf-8")
    binary.chmod(0o755)
    launcher = root / "RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py"
    launcher.parent.mkdir(parents=True)
    shutil.copy2(PROJECT / "RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py", launcher)
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["binary"]["sha256"] = sha256(binary)
    manifest_path = root / "RSIFlow/configs/bwrap_isolation.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    write_root = root / "writes"
    (write_root / "output").mkdir(parents=True)
    (root / "trusted").mkdir()
    return root, manifest_path, write_root


def write_trust_receipt(root):
    binary = root / "assets/isolation/bin/bwrap"
    manifest = root / "RSIFlow/configs/bwrap_isolation.json"
    launcher = root / "RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py"
    receipt = {
        "source_sha256": sha256(binary),
        "destination_sha256": sha256(binary),
        "version": "0.6.1",
        "manifest_path": "RSIFlow/configs/bwrap_isolation.json",
        "manifest_sha256": sha256(manifest),
        "launcher_path": "RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py",
        "launcher_sha256": sha256(launcher),
    }
    (root / "assets/isolation/install-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    return receipt


@pytest.mark.parametrize("asset", ["manifest", "launcher"])
def test_default_backend_rejects_tampered_trusted_assets(tmp_path, monkeypatch, asset):
    root, manifest_path, _ = make_root(tmp_path)
    write_trust_receipt(root)
    monkeypatch.setattr(BubblewrapBackend, "_namespaces_available", lambda self: True)
    backend = BubblewrapBackend(root)
    backend.validate()
    target = manifest_path if asset == "manifest" else backend.launcher
    target.write_text(target.read_text() + "\n", encoding="utf-8")
    with pytest.raises(SandboxUnavailable, match=f"{asset}.*hash"):
        backend.validate()


def test_default_backend_requires_installer_trust_receipt(tmp_path, monkeypatch):
    root, _, _ = make_root(tmp_path)
    write_trust_receipt(root)
    (root / "assets/isolation/install-receipt.json").unlink()
    monkeypatch.setattr(BubblewrapBackend, "_namespaces_available", lambda self: True)
    with pytest.raises(SandboxUnavailable, match="receipt"):
        BubblewrapBackend(root).validate()


def make_backend(tmp_path, monkeypatch):
    root, manifest_path, write_root = make_root(tmp_path)
    monkeypatch.setattr(BubblewrapBackend, "_namespaces_available", lambda self: True)
    return BubblewrapBackend(root, manifest_path=manifest_path, allowed_write_roots=(write_root,))


def make_request(backend, **changes):
    root = backend.project
    values = {
        "profile": "candidate",
        "argv": ("/usr/bin/python3", "-c", "pass"),
        "cwd": "/workspace",
        "env": {"LANG": "C.UTF-8"},
        "limits": BwrapLimits(
            wall_seconds=5.0,
            cpu_seconds=4,
            memory_bytes=64 * 1024 * 1024,
            output_bytes=4096,
            file_bytes=8192,
            processes=1,
        ),
        "mounts": (
            BwrapMount(root / "trusted", "/usr/trusted"),
            BwrapMount(backend.allowed_write_roots[0] / "output", "/workspace", "rw"),
        ),
    }
    values.update(changes)
    return BwrapRequest(**values)


def test_bwrap_asset_is_pinned_and_diagnostic_path_is_not_production(tmp_path):
    manifest = json.loads((PROJECT / "RSIFlow/configs/bwrap_isolation.json").read_text())
    assert manifest["backend"] == "linux_bwrap_seccomp_v1"
    assert manifest["binary"] == {
        "path": "assets/isolation/bin/bwrap",
        "version": "0.6.1",
        "sha256": "d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca",
    }
    with pytest.raises(SandboxUnavailable, match="bwrap"):
        BubblewrapBackend(PROJECT, manifest_path=tmp_path / "missing.json", allowed_write_roots=(tmp_path,)).validate()


def test_validate_returns_source_hashes(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    evidence = backend.validate()
    assert evidence["backend"] == "linux_bwrap_seccomp_v1"
    assert evidence["binary_version"] == "0.6.1"
    assert evidence["binary_sha256"] == sha256(backend.binary)
    for key in ("launcher_sha256", "profile_sha256", "limits_sha256"):
        assert len(evidence[key]) == 64
        int(evidence[key], 16)


def test_wrong_binary_hash_fails_closed(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    manifest = json.loads(backend.manifest_path.read_text())
    manifest["binary"]["sha256"] = "0" * 64
    backend.manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SandboxUnavailable, match="hash"):
        backend.validate()


@pytest.mark.parametrize("target", ["/tmp/bwrap", "assets/../bwrap"])
def test_absolute_and_parent_binary_targets_fail_closed(tmp_path, monkeypatch, target):
    backend = make_backend(tmp_path, monkeypatch)
    manifest = json.loads(backend.manifest_path.read_text())
    manifest["binary"]["path"] = target
    backend.manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SandboxUnavailable, match="path"):
        backend.validate()


def test_missing_namespace_support_fails_closed(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    monkeypatch.setattr(backend, "_namespaces_available", lambda: False)
    with pytest.raises(SandboxUnavailable, match="namespace"):
        backend.validate()


def test_non_regular_launcher_fails_closed(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    backend.launcher.unlink()
    backend.launcher.mkdir()
    with pytest.raises(SandboxUnavailable, match="launcher"):
        backend.validate()


def test_command_has_fixed_namespaces_mounts_and_inner_cli(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    request = make_request(backend)
    command = backend.command(request)
    assert command[:4] == [str(backend.binary), "--unshare-all", "--new-session", "--die-with-parent"]
    launcher_index = command.index("/opt/rsiflow/bwrap_launcher.py")
    assert_open_identity(command, command[launcher_index - 1], backend.launcher)
    assert "--ro-bind" in command
    assert "--bind" in command
    separator = command.index("--")
    assert command[separator - 14 : separator + 1] == [
        "/usr/bin/python3",
        "-I",
        "-B",
        "/opt/rsiflow/bwrap_launcher.py",
        "--profile",
        "candidate",
        "--limits-json",
        command[separator - 7],
        "--env-json",
        command[separator - 5],
        "--cwd",
        "/workspace",
        "--preserve-fds",
        "",
        "--",
    ]
    assert json.loads(command[command.index("--limits-json") + 1])["memory_bytes"] == 64 * 1024 * 1024
    assert json.loads(command[command.index("--env-json") + 1]) == {"LANG": "C.UTF-8"}
    assert command[command.index("--cwd") + 1] == "/workspace"
    assert command[command.index("--preserve-fds") + 1] == ""
    assert command[separator + 1 :] == list(request.argv)
    for forbidden in ("--proc", "--dev", "--ro-bind-try", "--share-net"):
        assert forbidden not in command
    assert ["--bind", "/", "/"] not in [command[index : index + 3] for index in range(len(command) - 2)]


def test_command_clears_host_environment_before_inner_launcher(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    command = backend.command(make_request(backend))
    assert command[4] == "--clearenv"


@pytest.mark.parametrize("values", [
    (5.0, 1.5, 1024, 1024, 1024, 1),
    (5.0, 1, 1024, 1024, 1024, True),
])
def test_integer_limits_reject_floats_and_booleans(values):
    with pytest.raises(ValueError, match="integer"):
        BwrapLimits(*values)


def test_broad_allowed_write_root_does_not_authorize_output(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    backend.allowed_write_roots = (Path("/"),)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    request = make_request(backend, mounts=(BwrapMount(outside, "/workspace", "rw"),))
    with pytest.raises(ValueError, match="allowed_write_roots"):
        backend.command(request)


def test_dev_mount_is_a_single_device(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    request = make_request(backend, mounts=(BwrapMount(Path("/dev/null"), "/dev/null", "dev"),))
    command = backend.command(request)
    index = command.index("--dev-bind")
    assert command[index + 2] == "/dev/null"
    assert_open_identity(command, command[index + 1], Path("/dev/null"))
    with pytest.raises((SandboxUnavailable, ValueError)):
        backend.command(make_request(backend, mounts=(BwrapMount(Path("/dev"), "/dev", "dev"),)))


@pytest.mark.parametrize("target", ["relative", "/workspace/../data"])
def test_invalid_mount_targets_never_reduce_the_command(tmp_path, monkeypatch, target):
    backend = make_backend(tmp_path, monkeypatch)
    request = make_request(backend, mounts=(BwrapMount(backend.project / "trusted", target),))
    with pytest.raises(ValueError):
        backend.command(request)


@pytest.mark.parametrize("source", [Path("/data"), Path("/"), Path("/home"), Path("/root")])
def test_broad_writable_host_paths_fail_closed(tmp_path, monkeypatch, source):
    backend = make_backend(tmp_path, monkeypatch)
    with pytest.raises((SandboxUnavailable, ValueError)):
        backend.command(make_request(backend, mounts=(BwrapMount(source, "/workspace", "rw"),)))


def test_repository_root_write_fails_closed(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="repository"):
        backend.command(make_request(backend, mounts=(BwrapMount(backend.project, "/workspace", "rw"),)))


def test_symlink_parent_fails_closed(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    write_root = backend.allowed_write_roots[0]
    (write_root / "real/output").mkdir(parents=True)
    (write_root / "link").symlink_to(write_root / "real", target_is_directory=True)
    with pytest.raises((SandboxUnavailable, ValueError), match="symlink"):
        backend.command(make_request(backend, mounts=(BwrapMount(write_root / "link/output", "/workspace", "rw"),)))


def test_output_outside_allowed_write_roots_fails_closed(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    outside = backend.project / "outside"
    outside.mkdir()
    with pytest.raises(ValueError, match="allowed_write_roots"):
        backend.command(make_request(backend, mounts=(BwrapMount(outside, "/workspace", "rw"),)))


def test_unknown_profile_fails_closed(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="profile"):
        backend.command(make_request(backend, profile="unknown"))


def test_only_native_profile_preserves_exactly_two_descriptors(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="descriptor"):
        backend.command(make_request(backend, preserve_fds=(4, 5)))
    with pytest.raises(ValueError, match="descriptor"):
        backend.command(make_request(backend, profile="native_benchmark", preserve_fds=(4,)))
    command = backend.command(make_request(backend, profile="native_benchmark", preserve_fds=(4, 7)))
    assert command[command.index("--preserve-fds") + 1] == "4,7"


def test_inner_launcher_applies_guards_then_execs_with_explicit_environment(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(bwrap_launcher.os, "umask", lambda mask: events.append(("umask", mask)))
    monkeypatch.setattr(bwrap_launcher, "_drop_credentials", lambda: events.append(("credentials",)))
    monkeypatch.setattr(bwrap_launcher, "_apply_limits", lambda limits: events.append(("limits", limits)))
    monkeypatch.setattr(
        bwrap_launcher, "_close_inherited_descriptors", lambda preserved, maximum: events.append(("close", preserved))
    )
    monkeypatch.setattr(bwrap_launcher, "_restrict_syscalls", lambda profile: events.append(("seccomp", profile)))
    monkeypatch.setattr(bwrap_launcher.os, "chdir", lambda cwd: events.append(("chdir", cwd)))
    monkeypatch.setattr(
        bwrap_launcher.os,
        "execve",
        lambda executable, argv, env: events.append(("exec", executable, argv, env)),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "parent-only-secret")
    limits = {
        "wall_seconds": 5.0,
        "cpu_seconds": 4,
        "memory_bytes": 1024,
        "output_bytes": 2048,
        "file_bytes": 4096,
        "processes": 1,
        "file_descriptors": 32,
    }
    bwrap_launcher.main([
        "--profile", "candidate",
        "--limits-json", json.dumps(limits),
        "--env-json", json.dumps({"LANG": "C.UTF-8"}),
        "--cwd", str(tmp_path),
        "--preserve-fds", "",
        "--", "/bin/echo", "ok",
    ])
    assert [event[0] for event in events] == ["umask", "credentials", "limits", "close", "chdir", "seccomp", "exec"]
    assert events[0] == ("umask", 0o077)
    assert events[-1] == ("exec", "/bin/echo", ["/bin/echo", "ok"], {"LANG": "C.UTF-8"})


def test_inner_launcher_rejects_relative_cwd_and_unknown_profiles(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        bwrap_launcher.main([
            "--profile", "candidate", "--limits-json", "{}", "--env-json", "{}",
            "--cwd", "relative", "--preserve-fds", "", "--", "/bin/true",
        ])
    with pytest.raises(SystemExit):
        bwrap_launcher.main([
            "--profile", "unknown", "--limits-json", "{}", "--env-json", "{}",
            "--cwd", str(tmp_path), "--preserve-fds", "", "--", "/bin/true",
        ])


def test_inner_descriptor_policy_closes_all_but_declared_rpc_fds(monkeypatch):
    closed = []
    monkeypatch.setattr(
        bwrap_launcher.os, "close_range", lambda first, last: closed.append((first, last)), raising=False
    )
    bwrap_launcher._close_inherited_descriptors((4, 7), 10)
    assert closed == [(3, 3), (5, 6), (8, 10)]
    with pytest.raises(ValueError, match="descriptor"):
        bwrap_launcher._validate_descriptor_policy("candidate", (4, 5))
    with pytest.raises(ValueError, match="descriptor"):
        bwrap_launcher._validate_descriptor_policy("native_benchmark", (4, 4))


def test_inner_limits_cover_cpu_memory_files_processes_descriptors_and_core(monkeypatch):
    calls = {}
    monkeypatch.setattr(resource, "setrlimit", lambda name, value: calls.__setitem__(name, value))
    bwrap_launcher._apply_limits({
        "wall_seconds": 5.0,
        "cpu_seconds": 4,
        "memory_bytes": 1024,
        "output_bytes": 2048,
        "file_bytes": 4096,
        "processes": 8,
        "file_descriptors": 32,
    })
    assert calls == {
        resource.RLIMIT_CPU: (4, 5),
        resource.RLIMIT_AS: (1024, 1024),
        resource.RLIMIT_FSIZE: (4096, 4096),
        resource.RLIMIT_NPROC: (8, 8),
        resource.RLIMIT_NOFILE: (32, 32),
        resource.RLIMIT_CORE: (0, 0),
    }


@pytest.mark.parametrize("profile", ["candidate", "native", "meta"])
def test_all_seccomp_profiles_retain_privilege_boundary_denials(profile):
    denied = set(bwrap_launcher._denied_syscalls(profile))
    assert {"mount", "setns", "ptrace", "setuid", "setgid", "capset", "keyctl", "init_module", "bpf"} <= denied


def test_profile_specific_network_process_and_rename_rules():
    candidate = set(bwrap_launcher._denied_syscalls("candidate"))
    native = set(bwrap_launcher._denied_syscalls("native"))
    meta = set(bwrap_launcher._denied_syscalls("meta"))
    assert {"socket", "connect", "clone", "clone3", "fork", "vfork", "rename", "renameat", "renameat2"} <= candidate
    assert {"socket", "connect", "fork", "vfork"} <= native
    assert "clone" not in native
    assert {"rename", "renameat", "renameat2"}.isdisjoint(native)
    assert {"socket", "connect", "clone", "clone3", "fork", "vfork"}.isdisjoint(meta)


def test_installer_copies_regular_file_atomically_and_writes_receipt(tmp_path, monkeypatch):
    source = tmp_path / "source-bwrap"
    source.write_text("#!/bin/sh\nprintf 'bubblewrap 0.6.1\\n'\n", encoding="utf-8")
    source.chmod(0o755)
    monkeypatch.setattr(install_bwrap, "EXPECTED_SHA256", sha256(source))
    root = tmp_path / "installed"
    manifest = PROJECT / "RSIFlow/configs/bwrap_isolation.json"
    launcher = PROJECT / "RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py"
    receipt = install_bwrap.install(source, root, manifest_path=manifest, launcher_path=launcher)
    destination = root / "bin/bwrap"
    assert destination.is_file()
    assert destination.stat().st_mode & 0o111
    assert receipt == {
        "source_sha256": sha256(source),
        "destination_sha256": sha256(destination),
        "version": "0.6.1",
        "manifest_path": "RSIFlow/configs/bwrap_isolation.json",
        "manifest_sha256": sha256(manifest),
        "launcher_path": "RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py",
        "launcher_sha256": sha256(launcher),
    }
    assert json.loads((root / "install-receipt.json").read_text()) == receipt
    assert list(root.glob("bin/.bwrap-*")) == []


def test_installer_rejects_wrong_hash_and_symlink_source(tmp_path):
    source = tmp_path / "source-bwrap"
    source.write_text("#!/bin/sh\nprintf 'bubblewrap 0.6.1\\n'\n", encoding="utf-8")
    source.chmod(0o755)
    with pytest.raises(RuntimeError, match="hash"):
        install_bwrap.install(source, tmp_path / "wrong-hash")
    link = tmp_path / "source-link"
    link.symlink_to(source)
    with pytest.raises(RuntimeError, match="regular"):
        install_bwrap.install(link, tmp_path / "symlink")


def test_parent_source_components_cannot_escape_writable_root(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    write_root = backend.allowed_write_roots[0]
    outside = backend.project / "outside"
    outside.mkdir()
    escaped = write_root / "output/../../outside"
    with pytest.raises(ValueError, match="parent|allowed_write_roots"):
        backend.command(make_request(backend, mounts=(BwrapMount(escaped, "/workspace", "rw"),)))


@pytest.mark.parametrize("descendant", [False, True])
def test_writable_root_and_strict_descendant_are_allowed(tmp_path, monkeypatch, descendant):
    backend = make_backend(tmp_path, monkeypatch)
    write_root = backend.allowed_write_roots[0]
    source = write_root / "output" if descendant else write_root
    command = backend.command(make_request(backend, mounts=(BwrapMount(source, "/workspace", "rw"),)))
    index = command.index("--bind")
    assert command[index + 2] == "/workspace"
    assert_open_identity(command, command[index + 1], source)


@pytest.mark.parametrize("content", [
    "[]",
    json.dumps({**MANIFEST, "binary": []}),
])
def test_non_object_manifest_shapes_raise_sandbox_unavailable(tmp_path, monkeypatch, content):
    backend = make_backend(tmp_path, monkeypatch)
    backend.manifest_path.write_text(content)
    with pytest.raises(SandboxUnavailable, match="manifest"):
        backend.validate()


def test_bwrap_version_timeout_raises_sandbox_unavailable(tmp_path, monkeypatch):
    import subprocess

    from rsiflow.task_meta import bwrap_backend

    backend = make_backend(tmp_path, monkeypatch)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 10)

    monkeypatch.setattr(bwrap_backend.subprocess, "run", timeout)
    with pytest.raises(SandboxUnavailable, match="cannot execute bwrap"):
        backend.validate()


def run_clone_seccomp_probe(profile):
    import subprocess

    runtime = str(PROJECT / "RSIFlow/runtime")
    script = f"""
import ctypes
import errno
import json
import os
import signal
import sys
import threading
sys.path.insert(0, {runtime!r})
from rsiflow.task_meta import bwrap_launcher
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
seccomp = ctypes.CDLL('libseccomp.so.2')
seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
clone_number = seccomp.seccomp_syscall_resolve_name(b'clone')
clone3_number = seccomp.seccomp_syscall_resolve_name(b'clone3')
assert clone_number >= 0 and clone3_number >= 0
assert libc.prctl(38, 1, 0, 0, 0) == 0

def raw_clone(flags):
    ctypes.set_errno(0)
    pid = libc.syscall(clone_number, flags, 0, 0, 0, 0)
    if pid == 0:
        os._exit(0)
    if pid < 0:
        return ctypes.get_errno()
    os.waitpid(pid, 0)
    return 0

def raw_clone3():
    ctypes.set_errno(0)
    result = libc.syscall(clone3_number, 0, 0)
    return ctypes.get_errno() if result < 0 else 0

bwrap_launcher._restrict_syscalls({profile!r})
thread_values = []
try:
    thread = threading.Thread(target=lambda: thread_values.append(True))
    thread.start()
    thread.join()
except RuntimeError:
    pass
result = {{
    'thread_created': thread_values == [True],
    'process_clone_errno': raw_clone(signal.SIGCHLD),
    'namespace_clone_errno': raw_clone(signal.SIGCHLD | 0x10000000),
    'newns_clone_errno': raw_clone(signal.SIGCHLD | 0x00020000),
    'newnet_clone_errno': raw_clone(signal.SIGCHLD | 0x40000000),
    'clone3_errno': raw_clone3(),
}}
print(json.dumps(result, sort_keys=True))
"""
    completed = subprocess.run(
        ["/usr/bin/python3", "-I", "-B", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_native_real_seccomp_allows_threads_but_denies_process_and_namespace_clones():
    assert run_clone_seccomp_probe("native") == {
        "thread_created": True,
        "process_clone_errno": 1,
        "namespace_clone_errno": 1,
        "newns_clone_errno": 1,
        "newnet_clone_errno": 1,
        "clone3_errno": 38,
    }


def test_meta_real_seccomp_allows_local_processes_but_denies_namespace_clones():
    assert run_clone_seccomp_probe("meta") == {
        "thread_created": True,
        "process_clone_errno": 0,
        "namespace_clone_errno": 1,
        "newns_clone_errno": 1,
        "newnet_clone_errno": 1,
        "clone3_errno": 38,
    }


def test_mount_source_replacement_after_validation_fails_closed(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    trusted = backend.project / "trusted"
    replacement = backend.project / "replacement"
    replacement.mkdir()
    original_reject = backend._reject_symlinks
    replaced = False

    def replace_after_validation(path):
        nonlocal replaced
        info = original_reject(path)
        if Path(path) == trusted and not replaced:
            replaced = True
            trusted.rename(backend.project / "original")
            replacement.rename(trusted)
        return info

    monkeypatch.setattr(backend, "_reject_symlinks", replace_after_validation)
    with pytest.raises(SandboxUnavailable, match="identity"):
        backend.command(make_request(backend))


def test_command_mount_sources_remain_open_original_identities_after_path_replacement(tmp_path, monkeypatch):
    backend = make_backend(tmp_path, monkeypatch)
    trusted = backend.project / "trusted"
    (trusted / "identity.txt").write_text("original", encoding="utf-8")
    replacement = backend.project / "replacement"
    replacement.mkdir()
    (replacement / "identity.txt").write_text("replacement", encoding="utf-8")
    command = backend.command(make_request(backend))
    try:
        source = command[command.index("--ro-bind") + 1]
        assert source.startswith("/proc/self/fd/")
        descriptor = int(source.rsplit("/", 1)[1])
        assert descriptor in command.pass_fds
        trusted.rename(backend.project / "original")
        replacement.rename(trusted)
        assert Path(f"/proc/self/fd/{descriptor}/identity.txt").read_text(encoding="utf-8") == "original"
    finally:
        close = getattr(command, "close", None)
        if close:
            close()
