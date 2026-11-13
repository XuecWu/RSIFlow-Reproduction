import hashlib
import json
import sys
import tomllib
from pathlib import Path
from typing import ClassVar

import pytest

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "RSIFlow"))
sys.path.insert(0, str(PROJECT / "RSIFlow/runtime"))

import persistent_meta_sandbox as sandbox_module
from persistent_meta_sandbox import PersistentMetaSandbox
from rsiflow.task_meta.bwrap_backend import BwrapCommand
from rsiflow.task_meta.sandbox import SandboxUnavailable


class FakeRuntime:
    def __init__(self, root):
        self.root = root
        self.valid = True
        self.validations = 0

    def validate(self):
        self.validations += 1
        if not self.valid:
            raise ValueError("runtime_manifest_mismatch")
        return "static-runtime-hash"

    def identity(self):
        return {"backend": "static-runtime-only", "runtime_manifest_sha256": self.validate()}


class FakeTransport:
    instances: ClassVar[list] = []

    def __init__(self, config, secret, operation_budget=None, evidence_dir=None):
        self.config = config
        self.secret = secret
        self.evidence_dir = evidence_dir
        self.started = None
        self.closed = False
        type(self).instances.append(self)

    def start(self, socket_path):
        self.started = Path(socket_path)

    def close(self):
        self.closed = True


class FakeBubblewrapBackend:
    instances: ClassVar[list] = []

    def __init__(self, project, *, manifest_path=None, allowed_write_roots=()):
        self.project = Path(project)
        self.allowed_write_roots = tuple(allowed_write_roots)
        self.requests = []
        type(self).instances.append(self)

    def validate(self):
        return {"backend": "linux_bwrap_seccomp_v1", "namespaces": True}

    def command(self, request):
        self.requests.append(request)
        payload = json.dumps({
            "argv": request.argv,
            "cwd": request.cwd,
            "env": request.env,
            "profile": request.profile,
        })
        values = ["/pinned/bwrap", "--unshare-all", "--new-session", "--die-with-parent", "--clearenv"]
        for index, mount in enumerate(request.mounts):
            flag = {"ro": "--ro-bind", "rw": "--bind", "dev": "--dev-bind"}[mount.mode]
            values.extend([flag, f"/proc/self/fd/fake-{index}", mount.target])
        values.extend([
            "--ro-bind", "/proc/self/fd/launcher", "/opt/rsiflow/bwrap_launcher.py",
            "/usr/bin/python3", "-I", "-B", "/opt/rsiflow/bwrap_launcher.py", "--", payload,
        ])
        return BwrapCommand(values, (), ())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_meta_sandbox(tmp_path, monkeypatch, *, outputs=True):
    FakeTransport.instances = []
    FakeBubblewrapBackend.instances = []
    repository = tmp_path / "repository"
    project = repository / "RSIFlow"
    project.mkdir(parents=True, exist_ok=True)
    run = tmp_path / "run"
    central = tmp_path / "Meta_logs/run"
    central.mkdir(parents=True, exist_ok=True)
    run.mkdir(exist_ok=True)
    if not (run / "meta_session").exists():
        (run / "meta_session").symlink_to(central, target_is_directory=True)
    codex_home = run / "meta_session/codex_home"
    codex_home.mkdir(exist_ok=True)
    output = tmp_path / "declared-output"
    output.mkdir(exist_ok=True)
    codex = tmp_path / "codex"
    codex.write_bytes(b"offline-codex-fixture")
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"models": []}\n')
    meta = {
        "provider": "OpenAI",
        "model": "gpt-6-astra",
        "base_url": "http://token.acerobotics.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "wire_api": "responses",
        "response_delivery": "buffered_json",
        "allow_model_fallback": False,
        "allow_provider_fallback": False,
        "codex_executable": str(codex),
        "codex_binary_sha256": digest(codex),
        "model_catalog_json": str(catalog),
        "model_catalog_sha256": digest(catalog),
        "execution_location": "local_chroot",
    }
    runtime = FakeRuntime(tmp_path / "static-runtime")
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
    monkeypatch.setenv("OPENAI_API_KEY", "parent-only-secret")
    monkeypatch.setattr(sandbox_module.local_execution, "runtime", lambda config: runtime)
    monkeypatch.setattr(sandbox_module, "ResponsesTransport", FakeTransport)
    monkeypatch.setattr(sandbox_module, "BubblewrapBackend", FakeBubblewrapBackend)
    sandbox = PersistentMetaSandbox(
        project=project,
        run=run,
        codex_home=codex_home,
        meta_config=meta,
        writable_outputs=(output,) if outputs else (),
    )
    return sandbox, FakeBubblewrapBackend.instances[0], runtime


def test_meta_sandbox_preserves_codex_home_and_hides_real_key(tmp_path, monkeypatch):
    sandbox, backend, _ = make_meta_sandbox(tmp_path, monkeypatch)
    (sandbox.codex_home / "session.jsonl").write_text("persistent-thread-state\n")
    first = sandbox.command(["/codex", "app-server", "--listen", "stdio://"])
    sandbox.close()
    second_sandbox, _, _ = make_meta_sandbox(tmp_path, monkeypatch)
    second = second_sandbox.command(["/codex", "app-server", "--listen", "stdio://"])
    second_sandbox.close()
    assert (sandbox.codex_home / "session.jsonl").read_text() == "persistent-thread-state\n"
    assert "parent-only-secret" not in " ".join(first + second)
    assert "danger-full-access" not in " ".join(first + second)
    request = backend.requests[0]
    assert request.profile == "persistent_meta"
    assert request.env["RSIFLOW_RELAY_KEY"] == "pipe-only"
    assert "OPENAI_API_KEY" not in request.env


def test_meta_sandbox_has_only_declared_writable_mounts(tmp_path, monkeypatch):
    sandbox, backend, _ = make_meta_sandbox(tmp_path, monkeypatch)
    command = sandbox.command(["/codex", "app-server", "--listen", "stdio://"])
    assert command.index("/proc") > command.index("/opt/rsiflow/bwrap_launcher.py")
    assert "--proc" not in command
    request = backend.requests[0]
    writable = {(mount.source, mount.target) for mount in request.mounts if mount.mode == "rw"}
    expected = {
        (sandbox.project.resolve(), str(sandbox.project)),
        (sandbox.run.resolve(), str(sandbox.run)),
        ((sandbox.run / "meta_session").resolve(), str((sandbox.run / "meta_session").resolve())),
        (sandbox.codex_home.resolve(), "/codex_home"),
        ((sandbox.run / "meta_session/tmp").resolve(), "/tmp"),
        ((tmp_path / "declared-output").resolve(), str(tmp_path / "declared-output")),
    }
    assert writable == expected
    mounted = " ".join(f"{mount.source} {mount.target}" for mount in request.mounts)
    for forbidden in ("/.git", "assets/private", "assets/models", "assets/raw", "/root", "/home"):
        assert forbidden not in mounted
    assert all(mount.target != "/" for mount in request.mounts)
    sandbox.close()


def test_raw_data_mask_never_exposes_shared_static_mutable_workspace(tmp_path, monkeypatch):
    sandbox, backend, runtime = make_meta_sandbox(tmp_path, monkeypatch)
    raw_data = sandbox.project / "runtime/data"
    raw_data.mkdir(parents=True)
    shared = runtime.root / "workspace"
    shared.mkdir(parents=True)
    (shared / "unrelated-private-input").write_text("must-not-be-mounted\n")
    sandbox.command(["/codex", "app-server", "--listen", "stdio://"])
    mask = next(mount for mount in backend.requests[0].mounts if mount.target == str(raw_data))
    assert mask.source == sandbox.empty_directory
    assert mask.source != shared
    assert list(mask.source.iterdir()) == []
    sandbox.close()


def test_static_runtime_hash_change_raises_before_spawn(tmp_path, monkeypatch):
    sandbox, _, runtime = make_meta_sandbox(tmp_path, monkeypatch)
    sandbox.start()
    runtime.valid = False

    def unexpected_spawn(*args, **kwargs):
        raise AssertionError("host process must not be spawned")

    monkeypatch.setattr(sandbox_module.subprocess, "Popen", unexpected_spawn)
    with pytest.raises(ValueError, match="runtime_manifest_mismatch"):
        sandbox.spawn(
            ["/codex", "app-server", "--listen", "stdio://"], sandbox.project.parent,
            None, None, None,
        )
    sandbox.close()


def test_start_keeps_real_secret_only_in_host_transport(tmp_path, monkeypatch):
    sandbox, _, _ = make_meta_sandbox(tmp_path, monkeypatch)
    receipt = sandbox.run / "meta_session/provider/receipts/existing.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"state":"completed"}\n')
    sandbox.start()
    transport = FakeTransport.instances[0]
    assert transport.secret == "parent-only-secret"
    assert transport.config.base_url == "http://token.acerobotics.com/v1"
    assert transport.config.provider == "OpenAI"
    assert transport.config.model == "gpt-6-astra"
    assert transport.config.allow_model_fallback is False
    assert transport.config.allow_provider_fallback is False
    assert sandbox.identity()["backend"] == "linux_bwrap_seccomp_v1"
    relay = transport.started
    sandbox.close()
    assert transport.closed is True
    assert not relay.exists()
    assert receipt.read_text() == '{"state":"completed"}\n'
    assert (sandbox.codex_home).is_dir()


def test_spawn_uses_bridge_stdio_and_minimal_host_environment(tmp_path, monkeypatch):
    sandbox, backend, _ = make_meta_sandbox(tmp_path, monkeypatch)
    calls = []

    class Child:
        pid = 43210
        returncode = 0

        def poll(self):
            return self.returncode

    def popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return Child()

    monkeypatch.setattr(sandbox_module.subprocess, "Popen", popen)
    stdin, stdout, stderr = object(), object(), object()
    child = sandbox.spawn(
        ["/codex", "app-server", "--listen", "stdio://"], sandbox.project.parent,
        stdin, stdout, stderr,
    )
    assert child.pid == 43210
    assert backend.requests[-1].argv == (
        "/usr/bin/python3", "/bridge.py", "/codex", "app-server", "--listen", "stdio://",
    )
    argv, kwargs = calls[0]
    assert "parent-only-secret" not in " ".join(argv)
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert kwargs["stdin"] is stdin and kwargs["stdout"] is stdout and kwargs["stderr"] is stderr
    assert kwargs["text"] is True and kwargs["bufsize"] == 1
    assert kwargs["start_new_session"] is True
    sandbox.close()


def test_spawn_rejects_any_non_appserver_command_before_start(tmp_path, monkeypatch):
    sandbox, _, _ = make_meta_sandbox(tmp_path, monkeypatch)
    monkeypatch.setattr(
        sandbox_module.subprocess, "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected host spawn")),
    )
    with pytest.raises(ValueError, match="app-server"):
        sandbox.spawn(["/bin/sh"], sandbox.project.parent, None, None, None)
    assert FakeTransport.instances == []


def test_bwrap_selection_is_explicit_and_fail_closed(tmp_path, monkeypatch):
    sandbox, _, _ = make_meta_sandbox(tmp_path, monkeypatch)
    monkeypatch.delenv("RSIFLOW_SANDBOX_BACKEND")
    with pytest.raises(SandboxUnavailable):
        sandbox.start()
    assert FakeTransport.instances == []


def test_relay_and_host_provider_configuration_are_exact():
    config = tomllib.loads((PROJECT / "reproduction/configs/codex_host.toml").read_text())
    provider = config["model_providers"]["OpenAI"]
    assert config["model"] == "gpt-6-astra"
    assert config["model_provider"] == "OpenAI"
    assert config["sandbox_mode"] == "workspace-write"
    assert config["sandbox_workspace_write"]["network_access"] is False
    assert config["features"]["multi_agent"] is False
    assert config["features"]["multi_agent_v2"] is False
    assert provider == {
        "name": "OpenAI",
        "base_url": "http://127.0.0.1:18443/api/v1",
        "wire_api": "responses",
        "env_key": "RSIFLOW_RELAY_KEY",
        "requires_openai_auth": False,
        "supports_websockets": False,
        "request_max_retries": 0,
        "stream_max_retries": 0,
        "stream_idle_timeout_ms": 600000,
    }
    base = json.loads((PROJECT / "reproduction/configs/base_4b.json").read_text())["meta"]
    assert base["base_url"] == "http://token.acerobotics.com/v1"
    assert base["execution_location"] == "local_chroot"
    assert base["allow_model_fallback"] is False
    assert base["allow_provider_fallback"] is False
