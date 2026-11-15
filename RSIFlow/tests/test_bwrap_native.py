import json
import signal
import subprocess
from pathlib import Path

import pytest
import tool_sandbox
from rsiflow.task_meta.bwrap_backend import BubblewrapBackend

BACKEND = "linux_bwrap_seccomp_v1"


def native_files(tmp_path):
    work = tmp_path / "native_work"
    output = tmp_path / "output"
    work.mkdir()
    script = work / "probe.py"
    script.write_text("pass\n", encoding="utf-8")
    return work, output, ["/usr/bin/python3", "-B", str(script)]


def make_request(tool_sandbox_module, work, output, command, timeout=30):
    return tool_sandbox_module._make_native_request(
        command, work, output, request_write=9, response_read=11, timeout=timeout
    )


def test_native_bwrap_preserves_only_declared_rpc_fds_and_output_write(monkeypatch, tmp_path):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", BACKEND)
    work, output, command = native_files(tmp_path)
    request = make_request(tool_sandbox, work, output, command)
    assert request.profile == "native_benchmark"
    assert set(request.preserve_fds) == {9, 11}
    assert request.limits.file_descriptors == 256
    assert request.limits.processes == 128
    assert request.limits.wall_seconds == 30
    assert request.argv == tuple(command)
    assert all(mount.mode != "rw" or mount.source == output.resolve() for mount in request.mounts)
    assert sum(mount.mode == "rw" for mount in request.mounts) == 1
    assert all(mount.target != "/proc" and not mount.target.startswith("/proc/") for mount in request.mounts)


def test_native_bwrap_mounts_inner_launcher_python_with_conda_official_environment(tmp_path):
    work, output, _ = native_files(tmp_path)
    environment = tmp_path / "official-env"
    (environment / "conda-meta").mkdir(parents=True)
    (environment / "bin").mkdir()
    python = environment / "bin" / "python"
    python.write_text("not executed\n", encoding="utf-8")
    script = work / "probe.py"
    request = make_request(tool_sandbox, work, output, [str(python), "-B", str(script)])
    mounts = {(mount.source, mount.target, mount.mode) for mount in request.mounts}
    assert (Path("/usr/bin/python3").resolve(), "/usr/bin/python3", "ro") in mounts
    assert (environment.resolve(), str(environment), "ro") in mounts


def test_native_bwrap_redirects_bfcl_generated_directories_to_dedicated_output(tmp_path):
    work, output, command = native_files(tmp_path)
    request = make_request(tool_sandbox, work, output, command)
    assert request.env["BFCL_PROJECT_ROOT"] == str(output.resolve())
    assert next(mount for mount in request.mounts if mount.source == work.resolve()).mode == "ro"


def test_native_bwrap_uses_dummy_values_without_inheriting_provider_key(monkeypatch, tmp_path):
    secret = "task4-parent-only-nonsecret"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setenv("GPT_AGENT_API_KEY", secret)
    work, output, command = native_files(tmp_path)
    request = make_request(tool_sandbox, work, output, command)
    assert request.env["RSI_RPC_WRITE_FD"] == "9"
    assert request.env["RSI_RPC_READ_FD"] == "11"
    assert request.env["API_KEY"] == "pipe-only"
    assert request.env["RSIFLOW_RELAY_KEY"] == "pipe-only"
    assert "OPENAI_API_KEY" not in request.env
    assert "GPT_AGENT_API_KEY" not in request.env
    assert secret not in json.dumps(request.env)


def test_real_native_bwrap_allows_rpc_threads_and_atomic_rename_but_not_fork(monkeypatch, tmp_path):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", BACKEND)
    work, output, command = native_files(tmp_path)
    output.mkdir()
    script = Path(command[2])
    script.write_text(
        "import errno, json, os, threading\n"
        "from pathlib import Path\n"
        f"output = Path({str(output)!r})\n"
        "assert not Path('/proc/self').exists()\n"
        "assert os.environ['API_KEY'] == 'pipe-only'\n"
        "assert os.environ['RSIFLOW_RELAY_KEY'] == 'pipe-only'\n"
        "assert 'OPENAI_API_KEY' not in os.environ\n"
        "values = []\n"
        "thread = threading.Thread(target=values.append, args=(17,))\n"
        "thread.start(); thread.join()\n"
        "assert values == [17]\n"
        "temporary = output / 'answer.tmp'\n"
        "temporary.write_text('renamed', encoding='utf-8')\n"
        "os.replace(temporary, output / 'answer.json')\n"
        "try:\n"
        "    os.fork()\n"
        "except OSError as exc:\n"
        "    assert exc.errno == errno.EPERM\n"
        "else:\n"
        "    raise AssertionError('fork was permitted')\n"
        "request = json.dumps({'rpc_id': 0, 'request': {'model': 'controlled'}}).encode() + b'\\n'\n"
        "os.write(int(os.environ['RSI_RPC_WRITE_FD']), request)\n"
        "with os.fdopen(int(os.environ['RSI_RPC_READ_FD']), 'rb', closefd=False) as incoming:\n"
        "    response = json.loads(incoming.readline())\n"
        "assert response['response']['content'] == 'test_override_cpu_only'\n",
        encoding="utf-8",
    )
    calls = []

    def handler(body):
        calls.append(body)
        return {"content": "test_override_cpu_only"}

    with (output / "check.log").open("wb") as log:
        tool_sandbox.run_native(command, work, output, log, handler=handler, timeout=30)
    assert (output / "answer.json").read_text(encoding="utf-8") == "renamed"
    assert calls == [{"model": "controlled"}]


def test_missing_proc_official_runner_failure_is_infrastructure(monkeypatch, tmp_path):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", BACKEND)
    work, output, command = native_files(tmp_path)
    Path(command[2]).write_text("open('/proc/self/status', 'rb').read()\n", encoding="utf-8")
    with (tmp_path / "missing-proc.log").open("wb") as log, pytest.raises(
        RuntimeError, match="Native evaluator failed"
    ) as failure:
        tool_sandbox.run_native(command, work, output, log, timeout=30)
    assert failure.type is RuntimeError
    assert "FileNotFoundError" in (tmp_path / "missing-proc.log").read_text(encoding="utf-8")


def test_native_timeout_kills_complete_process_group_without_host_preexec(monkeypatch, tmp_path):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", BACKEND)
    work, output, command = native_files(tmp_path)
    observed = {}

    class BuiltCommand(list):
        def __init__(self, descriptors):
            super().__init__(["pinned-bwrap"])
            self.pass_fds = tuple(descriptors)
            self.closed = False

        def close(self):
            self.closed = True

    def build(self, request):
        observed["request"] = request
        observed["built"] = BuiltCommand(request.preserve_fds)
        return observed["built"]

    class Process:
        pid = 4242

        def __init__(self):
            self.returncode = None

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired(command, timeout)
            self.returncode = -signal.SIGKILL
            return self.returncode

        def poll(self):
            return self.returncode

    process = Process()

    def popen(argv, **kwargs):
        observed["popen"] = kwargs
        return process

    kills = []
    monkeypatch.setattr(BubblewrapBackend, "command", build)
    monkeypatch.setattr(tool_sandbox.subprocess, "Popen", popen)
    monkeypatch.setattr(tool_sandbox.os, "killpg", lambda pid, sig: kills.append((pid, sig)))
    with (tmp_path / "timeout.log").open("wb") as log, pytest.raises(subprocess.TimeoutExpired):
        tool_sandbox.run_native(command, work, output, log, timeout=0.01)
    assert kills == [(4242, signal.SIGKILL)]
    assert observed["popen"]["start_new_session"] is True
    assert "preexec_fn" not in observed["popen"]
    assert set(observed["popen"]["pass_fds"]) == set(observed["request"].preserve_fds)
    assert observed["built"].closed is True


def test_bwrap_native_shared_output_owner_does_not_require_current_uid_chown(monkeypatch, tmp_path):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", BACKEND)
    work, output, command = native_files(tmp_path)
    output.mkdir()
    existing = output / "existing.json"
    existing.write_text("preserved\n", encoding="utf-8")
    monkeypatch.setattr(tool_sandbox.os, "geteuid", lambda: 12345)
    monkeypatch.setattr(tool_sandbox.os, "getegid", lambda: 54321)

    def shared_fs_chown(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(tool_sandbox.os, "chown", shared_fs_chown)
    with (output / "check.log").open("wb") as log:
        tool_sandbox.run_native(command, work, output, log, timeout=30)
    assert existing.read_text(encoding="utf-8") == "preserved\n"
    assert (output / "sandbox_tmp").is_dir()


def test_bwrap_native_still_reclaims_mismatched_output_entry(monkeypatch, tmp_path):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", BACKEND)
    work, output, command = native_files(tmp_path)
    output.mkdir()
    existing = output / "existing.json"
    existing.write_text("preserved\n", encoding="utf-8")
    original_lstat = Path.lstat

    def shared_fs_lstat(path):
        info = original_lstat(path)
        if path != existing:
            return info
        values = list(info)
        values[4], values[5] = 12345, 54321
        return type(info)(values)

    calls = []
    monkeypatch.setattr(Path, "lstat", shared_fs_lstat)
    monkeypatch.setattr(
        tool_sandbox.os,
        "chown",
        lambda path, uid, gid, follow_symlinks: calls.append((Path(path), uid, gid, follow_symlinks)),
    )
    with (output / "check.log").open("wb") as log:
        tool_sandbox.run_native(command, work, output, log, timeout=30)
    assert calls == [(existing, tool_sandbox.os.geteuid(), tool_sandbox.os.getegid(), False)]


@pytest.mark.parametrize("asset", ["manifest", "launcher"])
def test_native_tampered_security_asset_fails_before_spawn(monkeypatch, tmp_path, asset):
    from rsiflow.task_meta.sandbox import SandboxUnavailable
    from test_bwrap_backend import make_root, write_trust_receipt

    root, manifest_path, _ = make_root(tmp_path)
    write_trust_receipt(root)
    target = manifest_path if asset == "manifest" else root / "RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py"
    target.write_text(target.read_text() + "\n", encoding="utf-8")
    monkeypatch.setattr(tool_sandbox, "PROJECT_ROOT", root, raising=False)
    monkeypatch.setattr(
        "rsiflow.task_meta.bwrap_backend.BubblewrapBackend._namespaces_available", lambda self: True,
    )
    work, output, command = native_files(tmp_path)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("native Popen must not run after security-asset tampering")

    monkeypatch.setattr(tool_sandbox.subprocess, "Popen", forbidden)
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", BACKEND)
    with (tmp_path / "tampered.log").open("wb") as log, pytest.raises(
        SandboxUnavailable, match=f"{asset}.*hash",
    ):
        tool_sandbox.run_native(command, work, output, log, timeout=30)
    assert calls == []
