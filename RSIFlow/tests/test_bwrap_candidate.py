import json
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "RSIFlow/runtime"))
sys.path.insert(0, str(PROJECT / "reproduction/scripts"))

import candidate_bwrap_smoke
from rsiflow.task_meta import bwrap_candidate
from rsiflow.task_meta.sandbox import (
    LinuxSandbox,
    SandboxLimits,
    SandboxResult,
    SandboxUnavailable,
)


def test_bwrap_is_explicit_and_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
    monkeypatch.setattr(
        "rsiflow.task_meta.bwrap_candidate.BubblewrapBackend.validate",
        lambda self: (_ for _ in ()).throw(SandboxUnavailable("namespaces unavailable")),
    )
    runner = LinuxSandbox(scratch_root=tmp_path)
    with pytest.raises(SandboxUnavailable, match="namespaces unavailable"):
        runner.run("print('must-not-run-on-host')")
    assert not (tmp_path / "candidate.py").exists()


def test_unknown_backend_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "host-or-unknown")
    with pytest.raises(SandboxUnavailable, match="unknown sandbox backend"):
        LinuxSandbox(scratch_root=tmp_path)


def test_landlock_remains_the_default_only_when_selection_is_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("RSIFLOW_SANDBOX_BACKEND", raising=False)
    runner = LinuxSandbox(scratch_root=tmp_path)
    assert runner.backend == "linux_landlock_seccomp"
    result = SandboxResult("", "", 0, False, 0.0)
    assert result.backend == "linux_landlock_seccomp"


def bwrap_runner(monkeypatch, tmp_path, **kwargs):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
    return LinuxSandbox(scratch_root=tmp_path, **kwargs)


def test_bwrap_run_returns_real_backend_and_stdout(monkeypatch, tmp_path):
    result = bwrap_runner(monkeypatch, tmp_path).run("print('ok')")
    assert result.stdout == "ok\n"
    assert result.returncode == 0
    assert result.infrastructure_error is None
    assert result.backend == "linux_bwrap_seccomp_v1"


def test_bwrap_timeout_uses_wall_limit(monkeypatch, tmp_path):
    limits = SandboxLimits(wall_seconds=0.2, cpu_seconds=5)
    result = bwrap_runner(monkeypatch, tmp_path, limits=limits).run("import time; time.sleep(5)")
    assert result.timed_out is True
    assert result.wall_seconds < 3
    assert result.infrastructure_error is None


def test_bwrap_output_is_bounded_and_marked_truncated(monkeypatch, tmp_path):
    limits = SandboxLimits(output_bytes=64)
    result = bwrap_runner(monkeypatch, tmp_path, limits=limits).run("print('x' * 10000)")
    assert result.output_truncated is True
    assert len(result.stdout.encode()) == 64


def test_bwrap_candidate_receives_no_parent_provider_key(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "task2-parent-only-nonsecret")
    result = bwrap_runner(monkeypatch, tmp_path).run(
        "import json, os; print(json.dumps(dict(os.environ)))"
    )
    environment = json.loads(result.stdout)
    assert "OPENAI_API_KEY" not in environment
    assert "task2-parent-only-nonsecret" not in result.stdout


def test_bwrap_extra_files_keep_legacy_basename_rules(monkeypatch, tmp_path):
    runner = bwrap_runner(monkeypatch, tmp_path)
    result = runner.run("print(open('input.txt').read(), end='')", extra_files={"input.txt": "value\n"})
    assert result.stdout == "value\n"
    for name in ("../escape", "candidate.py", "stdout", "stderr"):
        with pytest.raises(ValueError, match="Invalid sandbox extra file name"):
            runner.run("pass", extra_files={name: "denied"})


def test_bwrap_command_has_typed_limits_minimal_environment_and_one_writable_mount(monkeypatch, tmp_path):
    limits = SandboxLimits(
        wall_seconds=3.5,
        cpu_seconds=4,
        memory_bytes=128 * 1024 * 1024,
        output_bytes=4096,
        file_bytes=8192,
        processes=1,
    )
    runner = bwrap_runner(monkeypatch, tmp_path, limits=limits)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    captured = {}

    def capture(backend, request):
        captured["backend"] = backend
        captured["request"] = request
        return ["pinned-bwrap"]

    monkeypatch.setattr(bwrap_candidate.BubblewrapBackend, "command", capture)
    command = bwrap_candidate.command(runner, scratch, profile="candidate", seed=17)
    request = captured["request"]
    assert command == ["pinned-bwrap"]
    assert captured["backend"].allowed_write_roots == (scratch.absolute(),)
    assert request.profile == "candidate"
    assert request.argv == (str(runner.python), "-s", "-B", "candidate.py")
    assert request.cwd == "/workspace"
    assert request.env == {
        "PATH": str(runner.python.parent),
        "HOME": "/workspace",
        "TMPDIR": "/workspace",
        "LANG": "C.UTF-8",
        "PYTHONHASHSEED": "17",
    }
    assert request.limits.wall_seconds == 3.5
    assert request.limits.cpu_seconds == 4
    assert request.limits.memory_bytes == 128 * 1024 * 1024
    assert request.limits.output_bytes == 4096
    assert request.limits.file_bytes == 8192
    assert request.limits.processes == 1
    assert request.limits.file_descriptors == 32
    assert request.mounts[-1].source == scratch.absolute()
    assert request.mounts[-1].target == "/workspace"
    assert request.mounts[-1].mode == "rw"
    assert (request.mounts[0].source, request.mounts[0].target, request.mounts[0].mode) == (
        runner.python, "/usr/bin/python3", "ro"
    )
    mount_tuples = {(mount.source, mount.target, mount.mode) for mount in request.mounts}
    for alias in ("/lib", "/lib64"):
        if Path(alias).exists():
            assert (Path(alias).resolve(), alias, "ro") in mount_tuples
    for source in set(runner.runtime_read_paths):
        mode = "dev" if source.parent == Path("/dev") else "ro"
        assert (source, str(source), mode) in mount_tuples


def test_bwrap_session_supports_json_lines_and_persistent_state(monkeypatch, tmp_path):
    runner = bwrap_runner(monkeypatch, tmp_path)
    code = (
        "import json, sys\n"
        "count = 0\n"
        "for line in sys.stdin:\n"
        "    count += 1\n"
        "    print(json.dumps({'echo': json.loads(line), 'count': count}), flush=True)\n"
    )
    with runner.session(code) as session:
        assert session.request({"value": 3}) == {"echo": {"value": 3}, "count": 1}
        assert session.request({"value": 4}) == {"echo": {"value": 4}, "count": 2}
        scratch = session.scratch
        assert scratch.exists()
    assert not scratch.exists()


def test_bwrap_session_extra_files_keep_legacy_rules(monkeypatch, tmp_path):
    runner = bwrap_runner(monkeypatch, tmp_path)
    code = (
        "import json, sys\n"
        "for line in sys.stdin:\n"
        " print(json.dumps({'file': open('input.txt').read().strip()}), flush=True)\n"
    )
    with runner.session(code, extra_files={"input.txt": "value\n", "stdout": "allowed"}) as session:
        assert session.request({}) == {"file": "value"}
    for name in ("../escape", "candidate.py", "stderr"):
        with pytest.raises(ValueError, match="Invalid sandbox extra file name"):
            runner.session("pass", extra_files={name: "denied"})


def test_bwrap_session_command_uses_unbuffered_candidate_profile(monkeypatch, tmp_path):
    runner = bwrap_runner(monkeypatch, tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    captured = {}

    def capture(backend, request):
        captured["request"] = request
        return ["pinned-bwrap"]

    monkeypatch.setattr(bwrap_candidate.BubblewrapBackend, "command", capture)
    bwrap_candidate.command(runner, scratch, profile="candidate_session", seed=0)
    request = captured["request"]
    assert request.profile == "candidate_session"
    assert request.argv == (str(runner.python), "-s", "-B", "-u", "candidate.py")


def test_negative_timeout_reports_exact_probe_stage_and_timing(tmp_path):
    runner = LinuxSandbox(
        backend="linux_bwrap_seccomp_v1",
        scratch_root=tmp_path,
        limits=SandboxLimits(wall_seconds=2.0, cpu_seconds=5, output_bytes=4096),
    )
    program = candidate_bwrap_smoke.negative_program(tmp_path / "marker", PROJECT / "RSIFlow_4B")
    program = program.replace(
        "('runtime_write_denied', runtime_write_denied),",
        "('runtime_write_denied', lambda: (__import__('time').sleep(5), True)[1]),",
    )
    result = runner.run(program)
    with pytest.raises(SandboxUnavailable, match="runtime_write_denied.*timed_out=True"):
        candidate_bwrap_smoke.require_negative_result(result)
