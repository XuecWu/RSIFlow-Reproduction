"""Offline lifecycle tests for the five-round supervisor and external cleanup writer.

These use temporary configs/runs and short-lived no-GPU controlled processes only.
They never touch the real launcher, GPU, API, or a real five-round run. The cleanup
writer's receipts are validated against the accepted summarizer consumer contract
(`summarize_deepcoder_five_round._validate_cleanup`) so writer and consumer share one
schema. GPU observations are injected offline; a real cleanup must measure actually.
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "reproduction/scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def supervisor(tmp_path):
    module = _load("deepcoder_five_round_supervisor")
    module.GLOBAL_GPU_LIFECYCLE_LOCK = tmp_path / "gpu-lifecycle.lock"
    return module


@pytest.fixture()
def reconciler(tmp_path):
    module = _load("reconcile_deepcoder_run")
    module.GLOBAL_GPU_LIFECYCLE_LOCK = tmp_path / "gpu-lifecycle.lock"
    return module


def _config(tmp_path, output_root, run_name, *, isolated_openai=False):
    """A temp machine config naturally deriving output/run, built from the valid
    five-round launcher value so TaskAdapter._config normalization succeeds."""
    from test_deepcoder_five_round import _five_round_launcher_value
    value = _five_round_launcher_value(tmp_path)
    release = tmp_path / "release"
    release.mkdir(exist_ok=True)
    (release / "protocol.json").write_text(json.dumps({"rounds": 5}))
    value.update(output_root=str(output_root), run_name=run_name, frozen_data_dir=str(release))
    if isolated_openai:
        value["meta_logs_root"] = str(tmp_path / "meta_logs")
        base_path = tmp_path / "base-openai.json"
        base = json.loads((ROOT / "RSIFlow" / value["runtime_source"] / value["base_config"]).read_text())
        base["meta"]["api_key_env"] = "OPENAI_API_KEY"
        base_path.write_text(json.dumps(base))
        value["base_config"] = str(base_path)
    config_path = tmp_path / "machine.json"
    config_path.write_text(json.dumps(value))
    return config_path


def _sleep_command(seconds):
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _wait(predicate, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


# --- supervisor launch / identity handshake --------------------------------

def test_launch_derives_paths_and_writes_identity(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    paths = supervisor.derive_paths(config)
    assert paths["run_dir"] == output_root / "runs" / "run"
    assert paths["registry"] == output_root / "services/four_gpu_services.json"
    result = supervisor.launch(config, "0001", command=_sleep_command(0.2),
                               timeout="10s", kill_after="2s", skip_asset_gates=True,
                               handshake_timeout=60)
    attempt = paths["run_dir"] / "meta_session/supervision/0001"
    assert (attempt / "kind").read_text().strip() == "detached"
    identity = json.loads((attempt / "identity.json").read_text())
    assert identity["pid"] == identity["pgid"]
    assert identity["run_dir"] == str(paths["run_dir"].resolve())
    assert identity["attempt_dir"] == str(attempt.resolve())
    assert str(identity["start_ticks"]).isdigit()
    assert _wait(lambda: (attempt / "exit").is_file())
    assert (attempt / "exit").read_text().strip() == "0"
    reconcile = json.loads((attempt / "reconcile.json").read_text())
    assert reconcile["status"] == "complete"
    assert reconcile["remaining"] == []


def test_launch_holds_global_lifecycle_lock_until_owned_command_exits(tmp_path, supervisor):
    import fcntl
    config = _config(tmp_path, tmp_path / "out", "run")
    paths = supervisor.derive_paths(config)
    lock = tmp_path / "global-gpu.lock"
    supervisor.GLOBAL_GPU_LIFECYCLE_LOCK = lock
    supervisor.launch(config, "0001", command=_sleep_command(1.0), timeout="10s",
                      kill_after="2s", skip_asset_gates=True)
    contender = lock.open("a+")
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
        attempt = supervisor.derive_paths(config)["run_dir"] / "meta_session/supervision/0001"
        assert _wait(lambda: (attempt / "exit").is_file())
        deadline = time.monotonic() + 65
        while True:
            try:
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(contender, fcntl.LOCK_UN)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
    finally:
        contender.close()
    launch = json.loads((attempt / "launch.json").read_text())
    assert launch["kind"] == "rsiflow_launch"
    assert launch["binding"]["run_dir"] == str(paths["run_dir"].resolve())


def test_launch_refuses_existing_attempt(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(0.1),
                      timeout="10s", kill_after="2s", skip_asset_gates=True)
    attempt = supervisor.derive_paths(config)["run_dir"] / "meta_session/supervision/0001"
    assert _wait(lambda: (attempt / "exit").is_file())
    with pytest.raises(Exception):
        supervisor.launch(config, "0001", command=_sleep_command(0.1),
                          timeout="10s", kill_after="2s", skip_asset_gates=True)


def test_launch_refuses_arbitrary_run_override(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    with pytest.raises(Exception):
        supervisor.launch(config, "0001", command=_sleep_command(0.1),
                          run_dir=tmp_path / "elsewhere", skip_asset_gates=True)


def test_launch_timeout_exit_124(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(30),
                      timeout="1s", kill_after="2s", skip_asset_gates=True)
    attempt = supervisor.derive_paths(config)["run_dir"] / "meta_session/supervision/0001"
    assert _wait(lambda: (attempt / "exit").is_file(), timeout=30)
    assert (attempt / "exit").read_text().strip() == "124"
    reconcile = json.loads((attempt / "reconcile.json").read_text())
    assert reconcile["status"] == "complete"


def test_stop_identity_term_exit_143(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(30),
                      timeout="60s", kill_after="2s", skip_asset_gates=True)
    attempt = supervisor.derive_paths(config)["run_dir"] / "meta_session/supervision/0001"
    assert _wait(lambda: (attempt / "identity.json").is_file())
    identity = json.loads((attempt / "identity.json").read_text())
    # Condition-wait for the wrapper to actually spawn its timeout child (PPID==wrapper),
    # rather than a fixed sleep; stop also re-signals to cover any residual spawn race.
    def _child_spawned():
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
                if int(stat[1]) == identity["pid"]:
                    return True
            except (FileNotFoundError, PermissionError, ValueError, IndexError):
                continue
        return False
    assert _wait(_child_spawned), "wrapper did not spawn its command child"
    result = supervisor.stop(config, "0001", grace=20)
    assert (attempt / "stop-requested").is_file()
    assert _wait(lambda: (attempt / "exit").is_file(), timeout=30)
    assert (attempt / "exit").read_text().strip() == "143"
    reconcile = json.loads((attempt / "reconcile.json").read_text())
    assert reconcile["status"] == "complete"


def test_stop_no_resignal_after_identity_change(tmp_path, supervisor, monkeypatch):
    # I3: once the owned identity changes (PID reuse), stop must fail closed and never
    # killpg again. Only the first validated signal may be sent.
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(30),
                      timeout="60s", kill_after="2s", skip_asset_gates=True)
    attempt = supervisor.derive_paths(config)["run_dir"] / "meta_session/supervision/0001"
    assert _wait(lambda: (attempt / "identity.json").is_file())
    identity = json.loads((attempt / "identity.json").read_text())
    real_stat = supervisor._proc_stat(identity["pid"])
    stat_calls = {"n": 0}

    def fake_stat(pid):
        stat_calls["n"] += 1
        if stat_calls["n"] <= 2:  # entry validation + first validated signal
            return list(real_stat)
        changed = list(real_stat)
        changed[19] = str(int(real_stat[19]) + 999999)  # reused PID, different start
        return changed

    killpg_calls = []
    monkeypatch.setattr(supervisor, "_proc_stat", fake_stat)
    monkeypatch.setattr(supervisor.os, "killpg", lambda *a: killpg_calls.append(a))
    try:
        with pytest.raises(RuntimeError, match="identity changed"):
            supervisor.stop(config, "0001", grace=5)
        assert len(killpg_calls) == 1  # no re-send after the identity changed
    finally:
        monkeypatch.undo()
        try:
            os.killpg(identity["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_stop_refuses_identity_mismatch(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(0.2),
                      timeout="10s", kill_after="2s", skip_asset_gates=True)
    attempt = supervisor.derive_paths(config)["run_dir"] / "meta_session/supervision/0001"
    assert _wait(lambda: (attempt / "exit").is_file())
    identity = json.loads((attempt / "identity.json").read_text())
    identity["start_ticks"] = "999999999"
    (attempt / "identity.json").write_text(json.dumps(identity))
    with pytest.raises(Exception):
        supervisor.stop(config, "0001", grace=5)


def test_status_reports_state(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(0.2),
                      timeout="10s", kill_after="2s", skip_asset_gates=True)
    attempt = supervisor.derive_paths(config)["run_dir"] / "meta_session/supervision/0001"
    assert _wait(lambda: (attempt / "exit").is_file())
    status = supervisor.status(config, "0001")
    assert status["exit"] == "0"
    assert status["reconcile_status"] == "complete"


# --- cleanup writer: controlled processes + injected GPU observation --------

def _seed_run(tmp_path, supervisor, reconciler, *, jobs_active=False, services_stopped=True,
              compute_apps=None, training_leader_dead_rank_alive=False, isolated_openai=False):
    """Build a temp run with a completed supervision, jobs, registry, and owners."""
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run", isolated_openai=isolated_openai)
    paths = supervisor.derive_paths(config)
    run = paths["run_dir"]
    run.mkdir(parents=True)
    (run / "meta_session").mkdir(parents=True)
    # launch a real short supervision so identity/exit/reconcile/launch seal exist
    supervisor.launch(config, "0001", command=_sleep_command(0.2),
                      timeout="10s", kill_after="2s", skip_asset_gates=True)
    attempt = run / "meta_session/supervision/0001"
    # Wait for the supervision to fully settle: exit marker AND reconcile complete with no
    # remaining members, so a later cleanup never observes a half-exited owner group.
    def _settled():
        if not (attempt / "exit").is_file():
            return False
        rec = json.loads((attempt / "reconcile.json").read_text()) if (attempt / "reconcile.json").is_file() else {}
        return rec.get("status") == "complete" and rec.get("remaining") == []
    assert _wait(_settled), "supervision did not settle (exit + reconcile complete/remaining=[])"
    # offline launch skips asset gates; seal the effective config the real launch would
    # have written pre-Popen, so the read-only cleanup verification can bind it.
    effective_obj = supervisor._normalize_effective(paths, run.resolve())
    supervisor._atomic_write(attempt / "effective.json", effective_obj.model_dump())
    launch = json.loads((attempt / "launch.json").read_text())
    launch["binding"]["effective_config_hash"] = supervisor._sha256(attempt / "effective.json")
    supervisor._atomic_write(attempt / "launch.json", launch)
    launcher_pid = launch["supervisor"]["pid"]
    # the launcher owner group must be measurably exited (gone or zombie, matching
    # _observe_owner) before cleanup binds it; an unreaped zombie still has a /proc entry.
    def _launcher_exited():
        try:
            stat = Path(f"/proc/{launcher_pid}/stat").read_text().rsplit(")", 1)[1].split()
            return stat[0] == "Z"
        except (FileNotFoundError, PermissionError, ValueError, IndexError):
            return True
    assert _wait(_launcher_exited), "launcher owner group still alive"
    app_pid = 9000001
    from test_deepcoder_five_round import _fixture_launch_chain
    identities = {"launcher": launch["supervisor"], "app_server": {"pid": app_pid, "pgid": app_pid, "start_ticks": "1"}}
    launch["binding"]["thread_id"] = "t1"
    _fixture_launch_chain(attempt, launch["binding"], identities, [{"gpu": g, "uuid": f"GPU-{g}"} for g in range(4)])
    (run / "meta_session/state.json").write_text(json.dumps({
        "status": "complete", "thread_id": launch["binding"]["thread_id"],
        "launcher_pid": launcher_pid, "codex_pid": app_pid, "last_turn_status": "completed",
    }))
    # active_task + registry
    checkpoint = str(tmp_path / "checkpoint")
    Path(checkpoint).mkdir(exist_ok=True)
    (Path(checkpoint) / "model.safetensors").write_bytes(b"weights")
    from rsiflow.task_meta.storage import checkpoint_manifest
    weights = checkpoint_manifest(Path(checkpoint))
    (run / "active_task.json").write_text(json.dumps({
        "generation": 3, "model_ref": checkpoint, "checkpoint_path": checkpoint,
        "checkpoint_manifest": weights, "harness_path": str(tmp_path / "h.json"),
        "artifacts": {"directory": None, "manifest": []},
    }))
    (tmp_path / "h.json").write_text(json.dumps({"harness": 1}))
    registry = paths["registry"]
    registry.parent.mkdir(parents=True, exist_ok=True)
    services = [{"gpu": g, "pid": 30000 + g, "identity": {"pid": 30000 + g, "pgid": 30000 + g, "start_ticks": "1"},
                 "verified_binding": {"checkpoint_path": checkpoint, "weights": weights}} for g in range(4)]
    registry.write_text(json.dumps({
        "phase": "services_stopped" if services_stopped else "inference_ready",
        "checkpoint": checkpoint, "services": services, "stopped_at": 3.0 if services_stopped else None,
    }))
    # one durable job (exited process; result落盘)
    job_dir = run / "meta_session/jobs/run_parent_1"
    job_dir.mkdir(parents=True)
    transport = run / "controller_transport/run_parent_1"
    transport.mkdir(parents=True)
    turn = run / "meta_session/turns/run_parent_1"
    turn.mkdir(parents=True)
    job_pid = 40001
    (transport / "result.json").write_text(json.dumps({"operation": "run_parent", "status": "completed", "returncode": 0}))
    (transport / "request.json").write_text(json.dumps({"operation": "run_parent", "round_number": 1}))
    (turn / "receipt.json").write_text(json.dumps({"operation": "run_parent", "status": "completed", "returncode": 0}))
    (turn / "command.json").write_text(json.dumps({"action": "tool", "request": {"operation": "run_parent", "round_number": 1}}))
    (job_dir / "job.json").write_text(json.dumps({
        "job_id": "run_parent_1", "request": {"operation": "run_parent", "round_number": 1},
        "pid": job_pid, "process_start": "1", "result_path": str(transport / "result.json"),
        "turn_dir": str(turn),
    }))
    import hashlib as _hl
    raw = "0, GPU-0, A800, 81920, 80000, 100, 0\n1, GPU-1, A800, 81920, 80000, 100, 0\n" \
          "2, GPU-2, A800, 81920, 80000, 100, 0\n3, GPU-3, A800, 81920, 80000, 100, 0\n"
    apps = compute_apps if compute_apps is not None else []
    apps_raw = "".join(f"{a['pid']}, {a.get('uuid', '')}\n" for a in apps)
    gpu_obs = {
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": ["nvidia-smi", "--query"], "command_result": 0,
        "expected_uuids": [f"GPU-{g}" for g in range(4)],
        "per_gpu": [{"gpu": g, "uuid": f"GPU-{g}", "memory_free": 80000, "memory_total": 81920,
                     "memory_used": 100, "utilization": 0} for g in range(4)],
        "compute_apps": apps,
        "free_verified": (compute_apps is None or not compute_apps),
        "raw_stdout": raw, "stdout_hash": _hl.sha256(raw.encode()).hexdigest(),
        "apps_command": ["nvidia-smi", "--query-compute-apps=pid,uuid"],
        "apps_raw_stdout": apps_raw, "apps_stdout_hash": _hl.sha256(apps_raw.encode()).hexdigest(),
    }
    return config, run, paths, gpu_obs, {"launcher_pid": launcher_pid, "app_pid": app_pid, "job_pid": job_pid}


def _gpu_observer(gpu_obs):
    # F6: each call returns an independent observation with a distinct real timestamp
    # (the writer separates calls by a real interval, so times fall in the receipt window).
    def observe():
        obs = dict(gpu_obs)
        obs["time"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return obs
    return observe


def test_cleanup_completed_receipt_passes_consumer(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["kind"] == "rsiflow_external_cleanup"
    assert receipt["status"] == "completed"
    assert receipt["blockers"] == []
    # validate against the accepted consumer contract
    summarizer = _load("summarize_deepcoder_five_round")
    cfg = json.loads(config.read_text()); cfg["_config_path"] = str(config.resolve())
    active = json.loads((run / "active_task.json").read_text())
    sup = summarizer._supervision(run)
    summarizer._validate_cleanup(receipt, receipt_path, run, cfg, paths["output_root"], sup, active)


def _seed_not_created_run(tmp_path, supervisor, reconciler, *, compute_apps=None,
                          finish_allowed=False, later_workflow=False,
                          omit_workflow_events=False):
    """A run that failed BEFORE any job/service was created: no jobs, no training phase,
    registry never created, launcher/app-server exited, GPU released, early-failure protocol."""
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler, compute_apps=compute_apps)
    import shutil
    shutil.rmtree(run / "meta_session/jobs", ignore_errors=True)
    if paths["registry"].exists():
        paths["registry"].unlink()
    shutil.rmtree(run / "meta_session/turns", ignore_errors=True)
    shutil.rmtree(run / "controller_transport", ignore_errors=True)
    turns = run / "meta_session/turns"
    turns.mkdir(parents=True)
    config_value = json.loads(config.read_text())
    inline_bootstrap = f"bootstrap(config_path={config},state_path={run / 'active_task.json'})"
    early_turns = [
        ({"operation": inline_bootstrap},
         {"operation": inline_bootstrap, "status": "unknown_operation"}),
        ({"operation": "bootstrap"}, {"operation": "bootstrap", "status": "stage_error",
                                        "error_type": "KeyError", "error": "'config_path'"}),
        ({"operation": "job_status"}, {"operation": "job_status", "status": "read", "jobs": []}),
    ]
    for index, (request, receipt) in enumerate(early_turns):
        turn = turns / f"{index:04d}"
        turn.mkdir()
        (turn / "command.json").write_text(json.dumps({"action": "tool", "request": request}))
        (turn / "receipt.json").write_text(json.dumps(receipt))
    if later_workflow:
        turn = turns / "0003"
        turn.mkdir()
        request = {"operation": "run_parent", "round_number": 1}
        (turn / "command.json").write_text(json.dumps({"action": "tool", "request": request}))
        (turn / "receipt.json").write_text(json.dumps({"operation": "run_parent", "status": "completed"}))
    session = run / "meta_session"
    (session / "workflow_policy.json").write_text(json.dumps({
        "rounds": 5, "experiment_scope": config_value["experiment_scope"],
        "retry_rejected_from_round": config_value["retry_rejected_from_round"],
        "evaluation_schedule": config_value["evaluation_schedule"],
    }))
    from experiment_progress import ExperimentProgress
    workflow = ExperimentProgress(session, rounds=5).rebuild()
    if finish_allowed:
        workflow["current_stage"] = "COMPLETE"
        workflow["finish_allowed"] = True
        (session / "workflow_state.json").write_text(json.dumps(workflow))
        status_path = session / "RUN_STATUS.md"
        status_path.write_text(status_path.read_text().replace("Current stage: **A0**", "Current stage: **COMPLETE**")
                               .replace("Finish allowed: **no**", "Finish allowed: **yes**"))
    if omit_workflow_events:
        (session / "workflow_events.jsonl").unlink()
    state = json.loads((run / "meta_session/state.json").read_text())
    state["status"] = "running"; state["last_turn_status"] = "failed"
    (run / "meta_session/state.json").write_text(json.dumps(state))
    return config, run, paths, gpu_obs, pids


def test_not_created_verified_completed_and_consumer_accepts(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_not_created_run(tmp_path, supervisor, reconciler)
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=lambda cfg: pytest.fail("must not stop services"),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "completed", receipt["blockers"]
    assert receipt["blockers"] == []
    assert receipt["registry"]["phase"] == "not_created_verified"
    assert receipt["registry"]["stop_action_skipped_or_executed"] == "not_created_verified"
    assert receipt["registry"]["after_hash"] is None and receipt["registry"]["before_hash"] is None
    assert receipt["owners"]["jobs"] == [] and receipt["owners"]["training_phases"] == []
    assert receipt["owners"]["inference_services"] == []
    # the accepted consumer contract verifies the not_created proof
    summarizer = _load("summarize_deepcoder_five_round")
    cfg = json.loads(config.read_text()); cfg["_config_path"] = str(config.resolve())
    active = json.loads((run / "active_task.json").read_text())
    sup = summarizer._supervision(run)
    summarizer._validate_cleanup(receipt, receipt_path, run, cfg, paths["output_root"], sup, active)


def test_not_created_gpu_not_released_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_not_created_run(
        tmp_path, supervisor, reconciler, compute_apps=[{"pid": 999999, "uuid": "GPU-0"}])
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed"  # GPU occupied => never certified completed
    assert any("gpu" in b for b in receipt["blockers"])


def test_normal_completion_missing_registry_still_blocked(tmp_path, supervisor, reconciler):
    # finish_allowed True (normal completion) but registry never created => NOT not_created;
    # the missing registry must stay blocked, never certified completed.
    config, run, paths, gpu_obs, pids = _seed_not_created_run(
        tmp_path, supervisor, reconciler, finish_allowed=True)
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed"
    assert receipt["registry"]["phase"] != "not_created_verified"
    assert any("registry" in b or "service" in b for b in receipt["blockers"])


def test_jobs_exist_missing_registry_still_blocked(tmp_path, supervisor, reconciler):
    # A run with jobs but no registry is not a not_created failure; it stays blocked.
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    paths["registry"].unlink()
    (run / "meta_session/workflow_state.json").write_text(json.dumps({
        "current_stage": "B1", "finish_allowed": False}))
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed"
    assert receipt["registry"]["phase"] != "not_created_verified"


def test_not_created_pending_cannot_restore_occupier(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_not_created_run(
        tmp_path, supervisor, reconciler, compute_apps=[{"pid": 999999, "uuid": "GPU-0"}])
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    assert json.loads(receipt_path.read_text())["status"] != "completed"
    started = []
    with pytest.raises(RuntimeError):
        reconciler.restore_occupier(config, run, "0001",
                                    occupier_start_fn=lambda: started.append(1) or {},
                                    shared_identity_path=tmp_path / "occupier-identity.json",
                                    gpu_observer=_gpu_observer(gpu_obs))
    assert started == []


def test_not_created_late_workflow_with_lost_inventory_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(
        tmp_path, supervisor, reconciler, later_workflow=True)
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed", receipt
    assert receipt["registry"]["phase"] != "not_created_verified"
    started = []
    with pytest.raises(RuntimeError):
        reconciler.restore_occupier(config, run, "0001",
                                    occupier_start_fn=lambda: started.append(1) or {},
                                    shared_identity_path=tmp_path / "restore-identity.json",
                                    gpu_observer=_gpu_observer(gpu_obs))
    assert started == []


def test_not_created_missing_ordered_workflow_evidence_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(
        tmp_path, supervisor, reconciler, omit_workflow_events=True)
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed", receipt
    assert any("not_created" in blocker for blocker in receipt["blockers"])


def test_not_created_consumer_and_restore_reject_non_a0_proof(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "completed", receipt["blockers"]
    receipt["not_created_evidence"]["current_stage"] = "B1"
    receipt_path.write_text(json.dumps(receipt))
    summarizer = _load("summarize_deepcoder_five_round")
    cfg = json.loads(config.read_text()); cfg["_config_path"] = str(config.resolve())
    active = json.loads((run / "active_task.json").read_text())
    sup = summarizer._supervision(run)
    with pytest.raises(ValueError, match="not_created"):
        summarizer._validate_cleanup(receipt, receipt_path, run, cfg, paths["output_root"], sup, active)
    started = []
    with pytest.raises(RuntimeError, match="cleanup evidence"):
        reconciler.restore_occupier(config, run, "0001",
                                    occupier_start_fn=lambda: started.append(1) or {},
                                    shared_identity_path=tmp_path / "consumer-restore-identity.json",
                                    gpu_observer=_gpu_observer(gpu_obs))
    assert started == []


def _rebuild_workflow(run):
    from experiment_progress import ExperimentProgress
    return ExperimentProgress(run / "meta_session", rounds=5).rebuild()


def _add_turn(run, name, operation, receipt_status="completed"):
    turn = run / "meta_session/turns" / name
    turn.mkdir(parents=True, exist_ok=True)
    (turn / "command.json").write_text(json.dumps({"action": "tool", "request": {"operation": operation}}))
    (turn / "receipt.json").write_text(json.dumps({"operation": operation, "status": receipt_status}))


def test_not_created_run_command_turn_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    # run_command is a LONG_OPERATION that creates a durable job => creation history.
    _add_turn(run, "0003", "run_command")
    _rebuild_workflow(run)
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed", receipt
    assert receipt["registry"]["phase"] != "not_created_verified"
    assert any("not_created" in b for b in receipt["blockers"])


def test_not_created_turn_gap_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    import shutil
    shutil.rmtree(run / "meta_session/turns/0001")  # gap: 0000, 0002
    _rebuild_workflow(run)
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed", receipt
    assert any("not_created" in b for b in receipt["blockers"])


def test_not_created_corrupt_events_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    (run / "meta_session/workflow_events.jsonl").write_text("{bad json\n")
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed", receipt
    assert any("not_created" in b for b in receipt["blockers"])


def test_not_created_empty_events_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    (run / "meta_session/workflow_events.jsonl").write_text("")
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed", receipt


def test_not_created_consumer_rejects_stale_summary_after_history_change(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "completed", receipt["blockers"]
    # Change the actual history AFTER the receipt was sealed; the summary is now stale.
    rec_path = run / "meta_session/turns/0000/receipt.json"
    rec = json.loads(rec_path.read_text()); rec["tampered"] = True
    rec_path.write_text(json.dumps(rec))
    summarizer = _load("summarize_deepcoder_five_round")
    cfg = json.loads(config.read_text()); cfg["_config_path"] = str(config.resolve())
    active = json.loads((run / "active_task.json").read_text())
    sup = summarizer._supervision(run)
    with pytest.raises(ValueError, match="not_created"):
        summarizer._validate_cleanup(receipt, receipt_path, run, cfg, paths["output_root"], sup, active)
    started = []
    with pytest.raises(RuntimeError, match="cleanup evidence"):
        reconciler.restore_occupier(config, run, "0001",
                                    occupier_start_fn=lambda: started.append(1) or {},
                                    shared_identity_path=tmp_path / "stale-restore-identity.json",
                                    gpu_observer=_gpu_observer(gpu_obs))
    assert started == []


def _pending_not_created(receipt):
    assert receipt["status"] != "completed", receipt
    assert receipt["registry"]["phase"] != "not_created_verified"
    assert any("not_created" in b for b in receipt["blockers"])


def test_not_created_missing_command_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    (run / "meta_session/turns/0001/command.json").unlink()
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    _pending_not_created(receipt)
    assert receipt["not_created_evidence"]["turns_parseable"] is False


def test_not_created_missing_receipt_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    (run / "meta_session/turns/0001/receipt.json").unlink()
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    _pending_not_created(receipt)
    assert receipt["not_created_evidence"]["turns_parseable"] is False


def test_not_created_corrupt_command_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    (run / "meta_session/turns/0001/command.json").write_text("{bad json")
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    _pending_not_created(receipt)
    assert receipt["not_created_evidence"]["turns_parseable"] is False


def test_not_created_corrupt_receipt_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    (run / "meta_session/turns/0001/receipt.json").write_text("{bad json")
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    _pending_not_created(receipt)
    assert receipt["not_created_evidence"]["turns_parseable"] is False


def test_not_created_events_out_of_order_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    events_path = run / "meta_session/workflow_events.jsonl"
    events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
    events[0], events[1] = events[1], events[0]  # decreasing turn order
    events_path.write_text("".join(json.dumps(e) + "\n" for e in events))
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    _pending_not_created(receipt)
    assert receipt["not_created_evidence"]["workflow_events_ok"] is False


def test_not_created_events_operation_mismatch_stays_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    events_path = run / "meta_session/workflow_events.jsonl"
    events = [json.loads(l) for l in events_path.read_text().splitlines() if l.strip()]
    events[0]["operation"] = "sft"  # disagrees with turn 0000's command operation
    events_path.write_text("".join(json.dumps(e) + "\n" for e in events))
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    _pending_not_created(receipt)
    assert receipt["not_created_evidence"]["workflow_events_ok"] is False


def test_not_created_consumer_and_restore_reject_run_command_history(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "completed", receipt["blockers"]
    # A run_command turn (LONG_OPERATION => durable job creation) appears AFTER the receipt
    # was sealed; the consumer must recompute the history and reject, not trust the summary.
    _add_turn(run, "0003", "run_command")
    _rebuild_workflow(run)
    summarizer = _load("summarize_deepcoder_five_round")
    cfg = json.loads(config.read_text()); cfg["_config_path"] = str(config.resolve())
    active = json.loads((run / "active_task.json").read_text())
    sup = summarizer._supervision(run)
    with pytest.raises(ValueError, match="not_created"):
        summarizer._validate_cleanup(receipt, receipt_path, run, cfg, paths["output_root"], sup, active)
    started = []
    with pytest.raises(RuntimeError, match="cleanup evidence"):
        reconciler.restore_occupier(config, run, "0001",
                                    occupier_start_fn=lambda: started.append(1) or {},
                                    shared_identity_path=tmp_path / "run-command-restore-identity.json",
                                    gpu_observer=_gpu_observer(gpu_obs))
    assert started == []


def test_cleanup_active_job_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    # spawn a live process and bind it as the job owner so the group is still alive
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        job_path = run / "meta_session/jobs/run_parent_1/job.json"
        job = json.loads(job_path.read_text())
        stat = Path(f"/proc/{proc.pid}/stat").read_text().rsplit(")", 1)[1].split()
        job["pid"] = proc.pid; job["process_start"] = stat[19]
        job_path.write_text(json.dumps(job))
        receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                              stop_services_fn=lambda cfg: None, skip_asset_gates=True)
        receipt = json.loads(receipt_path.read_text())
        assert receipt["status"] == "pending"
        assert receipt["blockers"]
    finally:
        proc.terminate(); proc.wait(timeout=10)


def test_cleanup_stopped_but_compute_app_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    gpu_obs["compute_apps"] = [{"pid": 99999, "uuid": "GPU-0"}]
    gpu_obs["free_verified"] = False
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed"


def test_cleanup_wrong_registry_root_rejected(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    # point registry checkpoint at a different model than active
    registry = json.loads(paths["registry"].read_text())
    registry["checkpoint"] = "/models/Other"
    paths["registry"].write_text(json.dumps(registry))
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed"


def test_cleanup_duplicate_observation_does_not_overwrite(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    first = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                   stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    first_bytes = first.read_bytes()
    second = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                    stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    assert first != second
    assert first.read_bytes() == first_bytes  # historical receipt preserved


def test_cleanup_missing_exit_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    (run / "meta_session/supervision/0001/exit").unlink()
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "pending"


def test_inspect_reports_without_signal(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    report = reconciler.inspect_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs))
    assert report["mode"] == "inspect"
    assert "owners" in report
    assert "protocol" in report


def test_cleanup_command_failure_failed(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler, services_stopped=False)
    def failing_stop(cfg):
        raise RuntimeError("stop_services failed")
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=failing_stop, skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "failed"


def test_cleanup_historical_143_resume_accounted(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    sup_dir = run / "meta_session/supervision"
    old = sup_dir / "0001"
    (old / "exit").write_text("143")
    (old / "reconcile.json").write_text(json.dumps({"status": "complete", "remaining": []}))
    # new attempt 0002 with exit 0
    new = sup_dir / "0002"
    new.mkdir()
    (new / "kind").write_text("detached")
    identity = json.loads((old / "identity.json").read_text())
    identity["attempt_dir"] = str(new.resolve())
    (new / "identity.json").write_text(json.dumps(identity))
    (new / "exit").write_text("0")
    (new / "reconcile.json").write_text(json.dumps({"status": "complete", "remaining": []}))
    (new / "launch.json").write_text((old / "launch.json").read_text())
    receipt_path = reconciler.cleanup_run(config, run, "0002", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["supervision"]["all_previous_attempts_accounted_for"] is True
    assert (old / "exit").read_text() == "143"  # historical preserved


def test_cleanup_malformed_job_result_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    (run / "controller_transport/run_parent_1/result.json").write_text("{bad json")
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "pending"
    assert any("job" in b for b in receipt["blockers"])


def _spawn_leader_with_rank(tmp_path, *, spawn_delay=0.0):
    """Leader owns a PGID and spawns a rank that reports its identity via a ready
    file, then stays alive until an explicit release file appears (no fixed sleep)."""
    ready = tmp_path / "rank_ready.json"
    release = tmp_path / "rank_release"
    rank_code = (
        "import os, sys, json, time\n"
        "from pathlib import Path\n"
        "stat = Path(f'/proc/{os.getpid()}/stat').read_text().rsplit(')', 1)[1].split()\n"
        "Path(sys.argv[1]).write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgid(0), 'start_ticks': stat[19]}))\n"
        "deadline = time.monotonic() + 120\n"
        "while time.monotonic() < deadline and not Path(sys.argv[2]).exists():\n"
        "    time.sleep(0.02)\n"
    )
    leader_code = (
        "import subprocess, sys, time\n"
        f"time.sleep({spawn_delay})\n"
        "subprocess.Popen([sys.executable, '-c', sys.argv[3], sys.argv[1], sys.argv[2]])\n"
        "time.sleep(120)\n"
    )
    leader = subprocess.Popen([sys.executable, "-c", leader_code, str(ready), str(release), rank_code],
                              start_new_session=True)
    return leader, ready, release


def _wait_ready(ready, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready.is_file():
            try:
                return json.loads(ready.read_text())
            except ValueError:
                pass
        time.sleep(0.02)
    return None


def test_cleanup_ddp_leader_dead_rank_alive_pending(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    leader, ready, release = _spawn_leader_with_rank(tmp_path)
    rank_identity = None
    try:
        # PRECONDITION: rank must report identity AND be confirmed in the leader's PGID
        # before the leader is killed (not a blind sleep).
        rank_identity = _wait_ready(ready)
        assert rank_identity is not None, "rank never reported ready"
        assert rank_identity["pgid"] == leader.pid
        leader_stat = Path(f"/proc/{leader.pid}/stat").read_text().rsplit(")", 1)[1].split()
        assert any(m["pid"] == rank_identity["pid"] for m in reconciler._group_members(leader.pid))
        phase_dir = run / "round_2/attempts/attempt_1/candidate/model_update"
        phase_dir.mkdir(parents=True)
        (phase_dir / "gpu_phase_receipt.json").write_text(json.dumps({
            "status": "completed", "training_pid": leader.pid, "replica_gpus": [0, 1, 2, 3],
            "training_identity": {"pid": leader.pid, "pgid": leader.pid, "start_ticks": leader_stat[19]}}))
        leader.terminate(); leader.wait(timeout=10)  # leader exits, rank remains in PGID
        # rank still alive after leader death (explicit keepalive, not fixed lifetime)
        assert Path(f"/proc/{rank_identity['pid']}").exists()
        receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                              stop_services_fn=lambda cfg: None, skip_asset_gates=True)
        receipt = json.loads(receipt_path.read_text())
        assert receipt["status"] == "pending"
        assert any("training" in b for b in receipt["blockers"])
        # writer never signaled the rank: still alive after cleanup
        assert Path(f"/proc/{rank_identity['pid']}").exists()
        assert any(m["pid"] == rank_identity["pid"] for m in reconciler._group_members(leader.pid))
    finally:
        release.write_text("release")  # explicit event lets the rank exit
        if rank_identity:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and Path(f"/proc/{rank_identity['pid']}").exists():
                time.sleep(0.05)
            if Path(f"/proc/{rank_identity['pid']}").exists():
                try:
                    os.kill(rank_identity["pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for member in reconciler._group_members(leader.pid):
            try:
                os.kill(member["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        if leader.poll() is None:
            leader.terminate(); leader.wait(timeout=10)


def test_cleanup_ddp_spawn_delay_no_false_exited(tmp_path, supervisor, reconciler):
    # Reproduce the old precondition gap: with a 1.0s spawn delay, a blind sleep(0.5)
    # would kill the leader while the group is still empty. The proper ready-file
    # precondition waits for the rank, confirms it in the PGID, then kills the leader;
    # the writer must observe the live rank (pending), never false-exited.
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    leader, ready, release = _spawn_leader_with_rank(tmp_path, spawn_delay=1.0)
    rank_identity = None
    try:
        time.sleep(0.5)
        assert not ready.is_file()  # old blind-sleep test would have killed leader here
        leader_stat = Path(f"/proc/{leader.pid}/stat").read_text().rsplit(")", 1)[1].split()
        phase_dir = run / "round_2/attempts/attempt_1/candidate/model_update"
        phase_dir.mkdir(parents=True)
        (phase_dir / "gpu_phase_receipt.json").write_text(json.dumps({
            "status": "completed", "training_pid": leader.pid, "replica_gpus": [0, 1, 2, 3],
            "training_identity": {"pid": leader.pid, "pgid": leader.pid, "start_ticks": leader_stat[19]}}))
        rank_identity = _wait_ready(ready)  # proper precondition: wait for the slow rank
        assert rank_identity is not None
        assert any(m["pid"] == rank_identity["pid"] for m in reconciler._group_members(leader.pid))
        leader.terminate(); leader.wait(timeout=10)
        assert Path(f"/proc/{rank_identity['pid']}").exists()
        receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                              stop_services_fn=lambda cfg: None, skip_asset_gates=True)
        receipt = json.loads(receipt_path.read_text())
        assert receipt["status"] == "pending"  # live rank => never false-exited
    finally:
        release.write_text("release")
        if rank_identity:
            try:
                os.kill(rank_identity["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
        if leader.poll() is None:
            leader.terminate(); leader.wait(timeout=10)


def test_observe_owner_proc_permission_error_unknown(reconciler, monkeypatch):
    # Controlled /proc read error => unknown + read_errors, never false-exited.
    def fake_members(pgid):
        return [], [f"/proc/999999/stat:permission"]
    monkeypatch.setattr(reconciler, "_group_members_with_errors", fake_members)
    monkeypatch.setattr(reconciler, "_proc_stat", lambda pid: (_ for _ in ()).throw(FileNotFoundError()))
    result = reconciler._observe_owner({"pid": 999999, "pgid": 999999, "start_ticks": "1"}, Path("/tmp"))
    assert result["state"] == "unknown"
    assert result["read_errors"]


def test_observe_owner_live_leader_pgid_mismatch_unknown(reconciler, monkeypatch):
    # Live leader whose actual PGID differs from declared => unknown (not exited).
    fake_stat = ["R"] * 20
    fake_stat[2] = "4242"   # actual pgrp != declared 1234
    fake_stat[19] = "100"   # start_ticks matches
    monkeypatch.setattr(reconciler, "_proc_stat", lambda pid: fake_stat)
    monkeypatch.setattr(reconciler, "_group_members_with_errors", lambda pgid: ([], []))
    result = reconciler._observe_owner({"pid": 1234, "pgid": 1234, "start_ticks": "100"}, Path("/tmp"))
    assert result["state"] == "unknown"
    assert any("pgid mismatch" in e for e in result["read_errors"])


def test_cleanup_lifecycle_lock_conflict_refused(tmp_path, supervisor, reconciler):
    import fcntl
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    lock_path = paths["output_root"] / "locks/lifecycle.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock_path, "a+")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(RuntimeError, match="lifecycle lock"):
            reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                   stop_services_fn=lambda cfg: None, skip_asset_gates=True,
                                   lifecycle_lock=lock_path)
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN); holder.close()


def test_cleanup_stop_spy_zero_on_mismatch(tmp_path, supervisor, reconciler):
    # wrong registry checkpoint => gates fail => stop_services spy must be called 0 times
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler, services_stopped=False)
    registry = json.loads(paths["registry"].read_text())
    registry["checkpoint"] = "/models/Other"
    paths["registry"].write_text(json.dumps(registry))
    calls = []
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=lambda cfg: calls.append(cfg), skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed"
    assert calls == []  # never signaled with mismatched ownership


def test_cleanup_stop_receives_pipeline_config(tmp_path, supervisor, reconciler):
    # when gates pass, stop_services receives a normalized config with .output_root
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler, services_stopped=False)
    received = []
    def spy(cfg):
        received.append(cfg)
        # simulate production stop writing the stopped registry
        reg = json.loads(paths["registry"].read_text())
        reg["phase"] = "services_stopped"; reg["stopped_at"] = 3.0
        paths["registry"].write_text(json.dumps(reg))
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          stop_services_fn=spy, skip_asset_gates=True)
    assert received, "stop_services should be called when gates pass"
    assert getattr(received[0], "output_root", None) == str(paths["output_root"])


def test_cleanup_foreign_group_not_signaled(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        time.sleep(0.2)
        receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                              stop_services_fn=lambda cfg: None, skip_asset_gates=True)
        receipt = json.loads(receipt_path.read_text())
        assert receipt["status"] == "completed"  # foreign group is not an owner
        assert foreign.poll() is None  # never signaled
    finally:
        foreign.terminate(); foreign.wait(timeout=10)


# --- H1/H2: ACK-before-execute handshake, failed-start containment, real identity ---

def test_launch_failed_start_never_executes_experiment(tmp_path, supervisor, monkeypatch):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    marker = tmp_path / "experiment_ran"
    command = [sys.executable, "-c", f"from pathlib import Path; Path(r'{marker}').write_text('ran')"]
    # Force parent identity validation to fail AFTER the child writes identity but
    # BEFORE ACK; the child must be contained and never run the experiment.
    monkeypatch.setattr(supervisor, "_validate_identity",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("forced identity failure")))
    with pytest.raises(RuntimeError, match="forced identity failure"):
        supervisor.launch(config, "0001", command=command, timeout="10s", kill_after="2s",
                          skip_asset_gates=True, handshake_timeout=10)
    attempt = supervisor.derive_paths(config)["run_dir"] / "meta_session/supervision/0001"
    assert _wait(lambda: (attempt / "failed-start.json").is_file())
    failed = json.loads((attempt / "failed-start.json").read_text())
    assert failed["kind"] == "rsiflow_failed_start"
    assert failed["experiment_executed"] is False
    assert not marker.exists()  # experiment never ran
    assert (attempt / "exit").read_text().strip() == "1"


def test_launch_ack_gates_experiment(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    marker = tmp_path / "experiment_ran"
    command = [sys.executable, "-c", f"from pathlib import Path; Path(r'{marker}').write_text('ran')"]
    supervisor.launch(config, "0001", command=command, timeout="10s", kill_after="2s",
                      skip_asset_gates=True)
    attempt = supervisor.derive_paths(config)["run_dir"] / "meta_session/supervision/0001"
    assert (attempt / "ack").is_file()  # parent ACKed
    assert _wait(lambda: marker.exists())  # experiment ran only after ACK
    assert _wait(lambda: (attempt / "exit").is_file())


def test_real_cli_has_no_asset_gate_bypass(supervisor):
    # H2: the real CLI must not expose --skip-asset-gates.
    with pytest.raises(SystemExit):
        supervisor.main(["launch", "--config", "/x.json", "--supervision", "0001", "--skip-asset-gates"])


def test_collect_post_launch_identity_binds_real_owners(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(0.2), timeout="10s",
                      kill_after="2s", skip_asset_gates=True)
    paths = supervisor.derive_paths(config)
    run = paths["run_dir"]
    attempt = run / "meta_session/supervision/0001"
    # spawn a real launcher whose argv binds this run, in the supervisor's PGID
    pid_file = tmp_path / "app-pid"
    launcher_code = ("import subprocess,sys,time; from pathlib import Path; "
                     "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)',sys.argv[1]],start_new_session=True); "
                     "Path(sys.argv[2]).write_text(str(p.pid)); time.sleep(30)")
    launcher = subprocess.Popen([sys.executable, "-c", launcher_code, str(run), str(pid_file)], start_new_session=True)
    assert _wait(pid_file.exists)
    app_pid = int(pid_file.read_text())
    try:
        seal = json.loads((attempt / "launch.json").read_text())
        seal["supervisor"]["pgid"] = launcher.pid  # launcher belongs to supervisor group
        supervisor._atomic_write(attempt / "launch.json", seal)
        common = {"run_dir": str(run), "config_path": str(config), "config_hash": supervisor._sha256(config), "supervision_attempt": "0001"}
        gpus = [{"gpu": g, "uuid": f"GPU-fixture-{g}"} for g in range(4)]
        supervisor._atomic_write(attempt / "prelaunch-seal.json", {"kind": "rsiflow_prelaunch_seal", "binding": common,
                                  "host_identity": supervisor.host_identity(), "expected_gpus": gpus})
        seal["prelaunch_hash"] = supervisor._sha256(attempt / "prelaunch-seal.json")
        seal["binding"]["mode"] = "fresh"
        supervisor._atomic_write(attempt / "launch.json", seal)
        prelaunch_hash = supervisor._sha256(attempt / "launch.json")
        (run / "meta_session/state.json").write_text(json.dumps({
            "status": "running", "launcher_pid": launcher.pid, "codex_pid": app_pid, "thread_id": "real-thread-xyz"}))
        attachment = supervisor.collect_post_launch_identity(config, "0001", timeout=10, gpu_observer=lambda: {"gpus": gpus})
        assert attachment["thread_id"] == "real-thread-xyz"
        assert attachment["owners"]["launcher"]["pid"] == launcher.pid
        assert attachment["owners"]["app_server"]["pid"] == app_pid
        assert attachment["owners"]["launcher"]["pid"] != attachment["owners"]["app_server"]["pid"]
        assert attachment["launch_hash"] == prelaunch_hash
        assert attachment["run_dir"] == str(run)
        # one-time: re-collection refuses to overwrite sealed history
        with pytest.raises(RuntimeError, match="already collected"):
            supervisor.collect_post_launch_identity(config, "0001", timeout=5)
    finally:
        launcher.terminate(); launcher.wait(timeout=10)
        try:
            os.kill(app_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def test_collect_post_launch_refuses_old_state_and_unrelated_pid(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(0.2), timeout="10s",
                      kill_after="2s", skip_asset_gates=True)
    paths = supervisor.derive_paths(config)
    run = paths["run_dir"]
    # old disconnected state must be refused (not collected as current identity)
    (run / "meta_session/state.json").write_text(json.dumps({
        "status": "disconnected", "launcher_pid": 999999, "codex_pid": 999998, "thread_id": "old-thread"}))
    with pytest.raises(RuntimeError, match="old/starting state"):
        supervisor.collect_post_launch_identity(config, "0001", timeout=1)


def test_post_ack_collector_failure_records_identity_pending(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(0.2), timeout="10s",
                      kill_after="2s", skip_asset_gates=True)
    paths = supervisor.derive_paths(config)
    run = paths["run_dir"]
    attempt = run / "meta_session/supervision/0001"
    # collector fails (no current state); R2 records actual-started/identity-pending,
    # NOT experiment_executed=False (the experiment is running after ACK).
    with pytest.raises(RuntimeError):
        supervisor.collect_post_launch_identity(config, "0001", timeout=1)
    record = supervisor.record_post_launch_pending(config, "0001", reason="collector_timeout")
    assert record["kind"] == "rsiflow_actual_started_identity_pending"
    assert record["experiment_executed"] is True
    assert record["identity_pending"] is True
    assert (attempt / "identity-pending.json").is_file()


# --- H7: fresh/resume modes ---

def test_resume_refuses_complete_run(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(0.2), timeout="10s",
                      kill_after="2s", skip_asset_gates=True)
    paths = supervisor.derive_paths(config)
    run = paths["run_dir"]
    assert _wait(lambda: (run / "meta_session/supervision/0001/exit").is_file())
    (run / "meta_session/state.json").write_text(json.dumps({"status": "complete"}))
    with pytest.raises(RuntimeError, match="cleanup-only"):
        supervisor.launch(config, "0002", command=_sleep_command(0.2), timeout="10s",
                          kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")


def test_resume_existing_incomplete_run(tmp_path, supervisor, reconciler):
    config, run, paths, gpu, pids = _seed_run(tmp_path, supervisor, reconciler)
    state_path = run / "meta_session/state.json"
    state = json.loads(state_path.read_text()); state["status"] = "disconnected"
    state_path.write_text(json.dumps(state))
    result = supervisor.launch(config, "0002", command=_sleep_command(0.2), timeout="10s",
                               kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")
    assert result["launch_seal"]["binding"]["resumed_from"] == "0001"
    assert _wait(lambda: (run / "meta_session/supervision/0002/exit").exists())


def test_resume_refuses_wrong_resumed_from(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(0.2), timeout="10s",
                      kill_after="2s", skip_asset_gates=True)
    paths = supervisor.derive_paths(config)
    run = paths["run_dir"]
    assert _wait(lambda: (run / "meta_session/supervision/0001/exit").is_file())
    (run / "meta_session/state.json").write_text(json.dumps({"status": "disconnected", "thread_id": "t1"}))
    (run / "active_task.json").write_text(json.dumps({"generation": 1}))
    # resumed_from must be the latest prior attempt (0001), not an arbitrary value
    with pytest.raises(RuntimeError, match="latest prior attempt"):
        supervisor.launch(config, "0002", command=_sleep_command(0.2), timeout="10s",
                          kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0000")


def test_resume_refuses_missing_active_task(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(0.2), timeout="10s",
                      kill_after="2s", skip_asset_gates=True)
    paths = supervisor.derive_paths(config)
    run = paths["run_dir"]
    assert _wait(lambda: (run / "meta_session/supervision/0001/exit").is_file())
    (run / "meta_session/state.json").write_text(json.dumps({"status": "disconnected", "thread_id": "t1"}))
    # no active_task.json and no trusted early-A0 proof => R7 product gate refuses
    with pytest.raises(RuntimeError, match="active Task"):
        supervisor.launch(config, "0002", command=_sleep_command(0.2), timeout="10s",
                          kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")


def _disconnect(run):
    state_path = run / "meta_session/state.json"
    state = json.loads(state_path.read_text()); state["status"] = "disconnected"
    state_path.write_text(json.dumps(state))


def test_resume_early_a0_without_active_task_allowed(tmp_path, supervisor, reconciler):
    # Real early-A0 scenario: bootstrap failed before A0 sealed, active_task.json NEVER
    # existed, turn history is only failed bootstrap/job_status, no jobs/phase/service,
    # cleanup completed. Same-thread resume must be allowed; the production bootstrap (not
    # the supervisor) creates the active Task once.
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    (run / "active_task.json").unlink()
    _disconnect(run)
    result = supervisor.launch(config, "0002", command=_sleep_command(0.2), timeout="10s",
                               kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")
    assert result["launch_seal"]["binding"]["resumed_from"] == "0001"
    assert _wait(lambda: (run / "meta_session/supervision/0002/exit").exists())
    # the supervisor must NOT forge active state; it stays absent until production bootstrap
    assert not (run / "active_task.json").exists()


def test_resume_late_stage_missing_active_requires_audit(tmp_path, supervisor, reconciler):
    # B1+ protocol stage with a missing active Task is never an early-A0 resume.
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    (run / "active_task.json").unlink()
    wf_path = run / "meta_session/workflow_state.json"
    wf = json.loads(wf_path.read_text()); wf["current_stage"] = "B1"
    wf_path.write_text(json.dumps(wf))
    _disconnect(run)
    with pytest.raises(RuntimeError, match="requires_audit"):
        supervisor.launch(config, "0002", command=_sleep_command(0.2), timeout="10s",
                          kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")


def test_resume_a0_candidate_product_missing_active_requires_audit(tmp_path, supervisor, reconciler):
    # A0 stage but a candidate product exists with missing active Task => requires_audit,
    # even though the turn history alone still looks like early A0.
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    (run / "active_task.json").unlink()
    attempt = run / "round_01/attempts/attempt_1"
    attempt.mkdir(parents=True)
    (attempt / "candidate_state.json").write_text(json.dumps({"generation": 1}))
    _disconnect(run)
    with pytest.raises(RuntimeError, match="requires_audit"):
        supervisor.launch(config, "0002", command=_sleep_command(0.2), timeout="10s",
                          kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")


def test_resume_a0_job_product_missing_active_requires_audit(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    (run / "active_task.json").unlink()
    job_dir = run / "meta_session/jobs/run_parent_1"
    job_dir.mkdir(parents=True)
    (job_dir / "job.json").write_text(json.dumps({"job_id": "run_parent_1"}))
    _disconnect(run)
    with pytest.raises(RuntimeError, match="requires_audit"):
        supervisor.launch(config, "0002", command=_sleep_command(0.2), timeout="10s",
                          kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")


def test_resume_a0_service_registry_missing_active_requires_audit(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    (run / "active_task.json").unlink()
    paths["registry"].parent.mkdir(parents=True, exist_ok=True)
    paths["registry"].write_text(json.dumps({"phase": "inference_ready"}))
    _disconnect(run)
    with pytest.raises(RuntimeError, match="requires_audit"):
        supervisor.launch(config, "0002", command=_sleep_command(0.2), timeout="10s",
                          kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")


def test_resume_refuses_unaccounted_prior_attempt(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(30), timeout="60s",
                      kill_after="2s", skip_asset_gates=True)
    paths = supervisor.derive_paths(config)
    run = paths["run_dir"]
    assert _wait(lambda: (run / "meta_session/supervision/0001/identity.json").is_file())
    (run / "meta_session/state.json").write_text(json.dumps({"status": "running"}))
    # prior attempt 0001 still running (no exit/reconcile) => resume refused
    with pytest.raises(RuntimeError, match="not accounted"):
        supervisor.launch(config, "0002", command=_sleep_command(0.2), timeout="10s",
                          kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")
    supervisor.stop(config, "0001", grace=45)


# --- H8: occupier restore requires completed cleanup; pending refuses ---

def test_restore_occupier_after_completed_cleanup(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                           stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    started = []
    def fake_start():
        ident = {"pid": 50001, "pgid": 50001, "start_ticks": "1", "gpus": [0, 1, 2, 3], "argv": ["torchrun"]}
        started.append(ident)
        return ident
    obs_path = reconciler.restore_occupier(config, run, "0001", occupier_start_fn=fake_start, shared_identity_path=tmp_path / "shared.json",
                                           gpu_observer=_gpu_observer(gpu_obs))
    obs = json.loads(obs_path.read_text())
    assert obs["kind"] == "rsiflow_occupier_restore"
    assert obs["occupier_identity"]["pid"] == 50001
    assert obs["cleanup_receipt_hash"]  # linked to cleanup hash
    assert started  # occupier started only after completed cleanup


def test_restore_occupier_refused_on_pending_cleanup(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    # make cleanup pending: active job
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        job_path = run / "meta_session/jobs/run_parent_1/job.json"
        job = json.loads(job_path.read_text())
        stat = Path(f"/proc/{proc.pid}/stat").read_text().rsplit(")", 1)[1].split()
        job["pid"] = proc.pid; job["process_start"] = stat[19]
        job_path.write_text(json.dumps(job))
        reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                               stop_services_fn=lambda cfg: None, skip_asset_gates=True)
        started = []
        with pytest.raises(RuntimeError, match="pending"):
            reconciler.restore_occupier(config, run, "0001",
                                        occupier_start_fn=lambda: started.append(1) or {}, shared_identity_path=tmp_path / "shared.json",
                                        gpu_observer=_gpu_observer(gpu_obs))
        assert started == []  # never started occupier on pending cleanup
    finally:
        proc.terminate(); proc.wait(timeout=10)


def test_restore_occupier_refused_without_cleanup(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    started = []
    with pytest.raises(RuntimeError, match="no external cleanup"):
        reconciler.restore_occupier(config, run, "0001",
                                    occupier_start_fn=lambda: started.append(1) or {}, shared_identity_path=tmp_path / "shared.json",
                                    gpu_observer=_gpu_observer(gpu_obs))
    assert started == []


def _occupier_script(tmp_path, ranks=4):
    # controlled stand-in for run.sh: spawns `ranks` child ranks in INDEPENDENT sessions/
    # PGIDs (PPID==leader), each with LOCAL_RANK/RANK, mirroring real torchrun topology.
    code = ("import os, subprocess, sys, time\n"
            "if '--rank' in sys.argv:\n"
            "    time.sleep(60); sys.exit(0)\n"
            f"for i in range({ranks}):\n"
            "    env = dict(os.environ, LOCAL_RANK=str(i), RANK=str(i))\n"
            "    subprocess.Popen([sys.executable, __file__, '--rank'], start_new_session=True, env=env)\n"
            "time.sleep(60)\n")
    py = tmp_path / "occupier_run.py"
    py.write_text(code)
    return [sys.executable, str(py)]


def _restore_gpu_observer(command, nvml_offset=0):
    # stateful: first call (restore free-check) => idle; later (start load-check) => 4 ranks.
    # nvml_offset simulates a different NVML PID namespace (compute PID != container rank PID).
    state = {"n": 0}
    def observe():
        from datetime import timedelta
        n = state["n"]; state["n"] += 1
        members = []
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit():
                continue
            try:
                argv = (entry / 'cmdline').read_bytes().split(b'\0')
                if str(command[1]).encode() in argv and b'--rank' in argv:
                    members.append(int(entry.name))
            except FileNotFoundError:
                continue
        apps = [] if n == 0 else [{"pid": pid + nvml_offset, "uuid": f"GPU-{i}"} for i, pid in enumerate(sorted(members))]
        raw = "".join(f"{a['pid']}, {a['uuid']}\n" for a in apps)
        used, util = (100, 0) if n == 0 else (2500, 20)
        gpu_raw = "".join(f"{g}, GPU-{g}, A800, 81920, 78000, {used}, {util}\n" for g in range(4))
        import hashlib as _hl
        return {"time": (datetime.now(timezone.utc) + timedelta(seconds=n)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "command": ["nvidia-smi", "--query"], "command_result": 0,
                "expected_uuids": [f"GPU-{g}" for g in range(4)],
                "per_gpu": [{"gpu": g, "uuid": f"GPU-{g}", "memory_free": 78000, "memory_total": 81920,
                             "memory_used": used, "utilization": util} for g in range(4)],
                "compute_apps": apps, "free_verified": (n == 0),
                "raw_stdout": gpu_raw, "stdout_hash": _hl.sha256(gpu_raw.encode()).hexdigest(),
                "apps_raw_stdout": raw, "apps_stdout_hash": _hl.sha256(raw.encode()).hexdigest()}
    return observe


def test_f8_restore_real_helper_exec_identity_and_shared_identity(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                           stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    shared_identity = tmp_path / "occupier-identity.json"
    command = _occupier_script(tmp_path, ranks=4)
    try:
        obs_path = reconciler.restore_occupier(
            config, run, "0001", gpu_observer=_restore_gpu_observer(command),
            occupier_command=command, shared_identity_path=shared_identity, exec_timeout=15)
        obs = json.loads(obs_path.read_text())
        assert obs["kind"] == "rsiflow_occupier_restore"
        assert obs["cleanup_receipt_hash"]  # linked to cleanup
        ident = obs["occupier_identity"]
        assert ident["pid"] == ident["pgid"] and str(ident["start_ticks"]).isdigit()
        assert ident["argv"] and len(ident["rank_members"]) >= 4
        # shared identity atomically updated with the real exec identity
        shared = json.loads(shared_identity.read_text())
        assert shared["pid"] == ident["pid"] and shared["owner"] == "dev4-rsiflow-idle-occupier"
    finally:
        if shared_identity.exists():
            reconciler._terminate_owned_group(json.loads(shared_identity.read_text()))


def test_f8_restore_failure_rollback_no_four_ranks(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                           stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    shared_identity = tmp_path / "occupier-identity.json"
    command = _occupier_script(tmp_path, ranks=1)  # only 1 rank => never settles to 4
    with pytest.raises(RuntimeError, match="four-rank"):
        reconciler.restore_occupier(
            config, run, "0001", gpu_observer=_restore_gpu_observer(command),
            occupier_command=command, shared_identity_path=shared_identity, exec_timeout=2)
    assert not shared_identity.exists()  # shared identity not updated on failure


# --- Real torchrun layout: independent PGID ranks, NVML PID namespace, gpu_uuid field ---

def test_real_layout_independent_pgid_ranks_nvml_namespace_differs(tmp_path, supervisor, reconciler):
    # A numeric offset is NOT evidence of a namespace mapping; retain unresolved facts.
    config, run, paths, gpu_obs, _ = _seed_run(tmp_path, supervisor, reconciler)
    reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                           stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    shared_identity = tmp_path / "occupier-identity.json"
    command = _occupier_script(tmp_path, ranks=4)
    with pytest.raises(RuntimeError, match="unresolved"):
        reconciler.restore_occupier(config, run, "0001", gpu_observer=_restore_gpu_observer(command, nvml_offset=1000000),
                                    occupier_command=command, shared_identity_path=shared_identity, exec_timeout=15)
    pending = json.loads(shared_identity.with_suffix('.pending.json').read_text())
    ranks = pending['rank_members']
    assert len(ranks) == 4 and len({r['pgid'] for r in ranks}) == 4
    assert all(r['ppid'] == pending['pid'] for r in ranks)
    assert sorted(int(r['local_rank']['LOCAL_RANK']) for r in ranks) == [0, 1, 2, 3]
    assert pending['pid_mapping']['status'] == 'unresolved'
    assert pending['pid_mapping']['compute_apps']
    assert not shared_identity.exists()
    assert all(reconciler._identity_alive(r) is False for r in ranks)


def test_real_layout_wrong_local_rank_topology_rejected(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                           stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    # ranks without LOCAL_RANK => logical rank mapping incomplete => rejected
    code = ("import os, subprocess, sys, time\n"
            "if '--rank' in sys.argv:\n"
            "    time.sleep(60); sys.exit(0)\n"
            "for i in range(4):\n"
            "    subprocess.Popen([sys.executable, __file__, '--rank'], start_new_session=True)\n"
            "time.sleep(60)\n")
    py = tmp_path / "occupier_no_local_rank.py"; py.write_text(code)
    command = [sys.executable, str(py)]
    shared_identity = tmp_path / "occupier-identity.json"
    with pytest.raises(RuntimeError, match="four-rank"):
        reconciler.restore_occupier(
            config, run, "0001", gpu_observer=_restore_gpu_observer(command),
            occupier_command=command, shared_identity_path=shared_identity, exec_timeout=2)
    assert not shared_identity.exists()


def test_real_layout_gpu_compute_apps_uses_gpu_uuid_field(tmp_path, reconciler, monkeypatch):
    import subprocess as _sp
    captured = {}
    real_run = _sp.run
    def fake_run(cmd, *a, **k):
        if isinstance(cmd, list) and any("--query-compute-apps" in str(c) for c in cmd):
            captured["apps_cmd"] = cmd
            return _sp.CompletedProcess(cmd, 0, stdout="", stderr="")
        if isinstance(cmd, list) and any("--query-gpu" in str(c) for c in cmd):
            captured["gpu_cmd"] = cmd
            raw = "".join(f"{g}, GPU-{g}, A800, 81920, 78000, 100, 0\n" for g in range(4))
            return _sp.CompletedProcess(cmd, 0, stdout=raw, stderr="")
        return real_run(cmd, *a, **k)
    monkeypatch.setattr(_sp, "run", fake_run)
    reconciler._default_gpu_observer()
    assert captured["apps_cmd"] == ["nvidia-smi", "--query-compute-apps=pid,gpu_uuid", "--format=csv,noheader,nounits"]
    # the main GPU query uuid field is unchanged (valid)
    assert "--query-gpu=index,uuid,name,memory.total,memory.free,memory.used,utilization.gpu" in " ".join(
        captured["gpu_cmd"])


def test_real_layout_per_identity_termination_no_broad_kill(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                           stop_services_fn=lambda cfg: None, skip_asset_gates=True)
    shared_identity = tmp_path / "occupier-identity.json"
    command = _occupier_script(tmp_path, ranks=4)
    obs_path = reconciler.restore_occupier(
        config, run, "0001", gpu_observer=_restore_gpu_observer(command),
        occupier_command=command, shared_identity_path=shared_identity, exec_timeout=15)
    ident = json.loads(shared_identity.read_text())
    ranks = ident["rank_members"]
    # an unrelated bystander process must NOT be killed by the owned-group reconciliation
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    try:
        result = reconciler._terminate_owned_group(ident, grace=10)
        assert result["state"] == "exited"
        # leader and all four ranks exited by saved identity
        assert reconciler._identity_alive(ident) is False
        assert all(reconciler._identity_alive(r) is False for r in ranks)
        assert bystander.poll() is None  # bystander untouched
    finally:
        bystander.terminate(); bystander.wait(timeout=10)
        for r in ranks:
            if reconciler._identity_alive(r):
                reconciler._signal_identity(r, __import__("signal").SIGKILL)


# --- R2: complete prelaunch asset/preflight seal before Popen -----------------

REQUIRED_ASSETS = ["manifest_B1", "manifest_B2", "manifest_B3", "manifest_B4", "manifest_B5",
                   "payload", "READY", "seed_harness", "model_weights", "codex_binary",
                   "bwrap_install", "bwrap_manifest", "bwrap_launcher", "task5_model_evidence"]


def _prelaunch_evidence(tmp_path, *, commit="a" * 40, backend="linux_bwrap_seccomp_v1",
                        omit=None, wrong_hash=None, stage2_no_ref=False):
    assets = {}
    for name in REQUIRED_ASSETS:
        p = tmp_path / f"asset_{name}"
        p.write_text(f"content-{name}")
        import hashlib as _hl
        digest = _hl.sha256(p.read_bytes()).hexdigest()
        if wrong_hash == name:
            digest = "0" * 64
        assets[name] = {"path": str(p), "sha256": digest}
    if omit:
        assets.pop(omit, None)
    import hashlib as _hl
    field_base = {"status": "success", "run_dir": str(tmp_path / "run"), "config_hash": "c" * 64,
                  "host": "dev4", "boot_id": "boot-1", "pid_namespace": "host",
                  "gpus": [{"gpu": g, "uuid": f"GPU-{g}"} for g in range(4)],
                  "provider": "gpt-6-astra", "ports": [8271, 8272, 8273, 8274], "backend": backend}
    stage1 = tmp_path / "preflight_stage1.json"
    stage1.write_text(json.dumps({**field_base, "stage": 1, "occupier_running": True}))
    stage1_hash = _hl.sha256(stage1.read_bytes()).hexdigest()
    stage2 = tmp_path / "preflight_stage2.json"
    stage2.write_text(json.dumps({**field_base, "stage": 2, "occupier_stopped": True,
                                  "stage1_receipt_hash": "bad" if stage2_no_ref else stage1_hash}))
    stage2_hash = _hl.sha256(stage2.read_bytes()).hexdigest()
    return {"reviewed_commit": commit, "backend": backend,
            "required_assets": REQUIRED_ASSETS, "assets": assets,
            "preflight_receipts": {"stage1": {"path": str(stage1), "sha256": stage1_hash},
                                   "stage2": {"path": str(stage2), "sha256": stage2_hash}}}


def test_prelaunch_seal_positive(tmp_path, supervisor):
    attempt = tmp_path / "attempt"; attempt.mkdir()
    evidence = _prelaunch_evidence(tmp_path)
    seal = supervisor.verify_prelaunch_seal(evidence, expected_commit="a" * 40,
                                            expected_backend="linux_bwrap_seccomp_v1", attempt_dir=attempt)
    assert seal["kind"] == "rsiflow_prelaunch_seal"
    assert (attempt / "prelaunch-seal.json").is_file()
    with pytest.raises(RuntimeError, match="already exists"):
        supervisor.verify_prelaunch_seal(evidence, expected_commit="a" * 40,
                                         expected_backend="linux_bwrap_seccomp_v1", attempt_dir=attempt)


@pytest.mark.parametrize("kwargs,match", [
    ({"omit": "manifest_B3"}, "asset manifest_B3 missing"),
    ({"omit": "bwrap_install"}, "asset bwrap_install missing"),
    ({"wrong_hash": "model_weights"}, "hash mismatch"),
    ({"commit": "b" * 40}, "reviewed commit mismatch"),
    ({"backend": "legacy_landlock"}, "backend mismatch"),
    ({"stage2_no_ref": True}, "stage2 does not reference stage1"),
])
def test_prelaunch_seal_negative_experiment_not_executed(tmp_path, supervisor, kwargs, match):
    attempt = tmp_path / "attempt"; attempt.mkdir()
    evidence = _prelaunch_evidence(tmp_path, **kwargs)
    with pytest.raises(RuntimeError, match=match):
        supervisor.verify_prelaunch_seal(evidence, expected_commit="a" * 40,
                                         expected_backend="linux_bwrap_seccomp_v1", attempt_dir=attempt)
    assert not (attempt / "prelaunch-seal.json").exists()  # seal not written on failure


def test_launch_real_mode_requires_prelaunch_seal(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    paths = supervisor.derive_paths(config)
    (paths["ready"]).write_text(json.dumps({"ready": True}))  # satisfy fresh asset gate
    marker = tmp_path / "experiment_ran"
    command = [sys.executable, "-c", f"from pathlib import Path; Path(r'{marker}').write_text('ran')"]
    # real mode (skip_asset_gates=False) without prelaunch evidence => refused before Popen
    with pytest.raises(RuntimeError, match="prelaunch evidence missing"):
        supervisor.launch(config, "0001", command=command, timeout="10s", kill_after="2s",
                          skip_asset_gates=False, prelaunch_evidence=None, expected_commit="a" * 40)
    assert not marker.exists()  # experiment command never executed


# --- resume-specific current preflight (replaces stale stage1/2; no occupier handoff) -----

def _resume_evidence(tmp_path, *, commit="a" * 40, backend="linux_bwrap_seccomp_v1",
                     cleanup_status="completed", add_stages=False):
    import hashlib as _hl
    assets = {}
    for name in REQUIRED_ASSETS:
        p = tmp_path / f"asset_{name}"; p.write_text(f"content-{name}")
        assets[name] = {"path": str(p), "sha256": _hl.sha256(p.read_bytes()).hexdigest()}
    run_dir, config_hash = str(tmp_path / "run"), "c" * 64
    field_base = {"status": "success", "stage": "resume", "run_dir": run_dir, "config_hash": config_hash,
                  "host": "dev4", "boot_id": "boot-1", "pid_namespace": "host",
                  "gpus": [{"gpu": g, "uuid": f"GPU-{g}"} for g in range(4)],
                  "provider": "gpt-6-astra", "ports": [8271, 8272, 8273, 8274], "backend": backend}
    cleanup = tmp_path / "cleanup.json"
    cleanup.write_text(json.dumps({"kind": "rsiflow_external_cleanup", "status": cleanup_status,
                                   "binding": {"run_dir": run_dir, "config_hash": config_hash}}))
    resume = tmp_path / "preflight_resume.json"
    resume.write_text(json.dumps({**field_base, "cleanup_receipt": {
        "path": str(cleanup), "sha256": _hl.sha256(cleanup.read_bytes()).hexdigest()}}))
    receipts = {"resume": {"path": str(resume), "sha256": _hl.sha256(resume.read_bytes()).hexdigest()}}
    if add_stages:
        s1 = tmp_path / "preflight_stage1.json"
        s1.write_text(json.dumps({**field_base, "stage": 1, "occupier_running": True}))
        receipts["stage1"] = {"path": str(s1), "sha256": _hl.sha256(s1.read_bytes()).hexdigest()}
    return {"reviewed_commit": commit, "backend": backend, "required_assets": REQUIRED_ASSETS,
            "assets": assets, "preflight_receipts": receipts}


def test_prelaunch_seal_resume_positive(tmp_path, supervisor):
    attempt = tmp_path / "attempt"; attempt.mkdir()
    seal = supervisor.verify_prelaunch_seal(_resume_evidence(tmp_path), expected_commit="a" * 40,
                                            expected_backend="linux_bwrap_seccomp_v1",
                                            attempt_dir=attempt, mode="resume")
    assert seal["kind"] == "rsiflow_prelaunch_seal"
    assert seal["binding"]["launch_mode"] == "resume"
    assert seal["expected_gpus"] == [{"gpu": g, "uuid": f"GPU-{g}"} for g in range(4)]
    assert "resume" in seal["preflight_receipts"] and "stage1" not in seal["preflight_receipts"]


def test_prelaunch_seal_fresh_refuses_resume_receipt(tmp_path, supervisor):
    attempt = tmp_path / "attempt"; attempt.mkdir()
    with pytest.raises(RuntimeError, match="fresh launch requires two-stage"):
        supervisor.verify_prelaunch_seal(_resume_evidence(tmp_path), expected_commit="a" * 40,
                                         expected_backend="linux_bwrap_seccomp_v1",
                                         attempt_dir=attempt, mode="fresh")
    assert not (attempt / "prelaunch-seal.json").exists()


def test_prelaunch_seal_resume_refuses_mixed_receipts(tmp_path, supervisor):
    attempt = tmp_path / "attempt"; attempt.mkdir()
    with pytest.raises(RuntimeError, match="ambiguous"):
        supervisor.verify_prelaunch_seal(_resume_evidence(tmp_path, add_stages=True),
                                         expected_commit="a" * 40,
                                         expected_backend="linux_bwrap_seccomp_v1",
                                         attempt_dir=attempt, mode="resume")


def test_prelaunch_seal_resume_incomplete_cleanup_refused(tmp_path, supervisor):
    attempt = tmp_path / "attempt"; attempt.mkdir()
    with pytest.raises(RuntimeError, match="cleanup not completed"):
        supervisor.verify_prelaunch_seal(_resume_evidence(tmp_path, cleanup_status="pending"),
                                         expected_commit="a" * 40,
                                         expected_backend="linux_bwrap_seccomp_v1",
                                         attempt_dir=attempt, mode="resume")


def test_prelaunch_seal_resume_without_receipt_requires_stages(tmp_path, supervisor):
    attempt = tmp_path / "attempt"; attempt.mkdir()
    evidence = _resume_evidence(tmp_path); evidence["preflight_receipts"] = {}
    # no resume receipt => still must satisfy the original two-stage gate (no general bypass)
    with pytest.raises(RuntimeError, match="stage1 receipt missing"):
        supervisor.verify_prelaunch_seal(evidence, expected_commit="a" * 40,
                                         expected_backend="linux_bwrap_seccomp_v1",
                                         attempt_dir=attempt, mode="resume")


def test_preflight_input_selects_resume_receipt(tmp_path, supervisor):
    r = tmp_path / "resume.json"; r.write_text("{}")
    receipts = supervisor._select_preflight_receipts(None, None, r)
    assert set(receipts) == {"resume"} and receipts["resume"]["sha256"] == supervisor._sha256(r)


def test_preflight_input_fresh_requires_both_stages(tmp_path, supervisor):
    s1 = tmp_path / "s1.json"; s1.write_text("{}")
    with pytest.raises(RuntimeError, match="both stage1 and stage2"):
        supervisor._select_preflight_receipts(s1, None, None)
    with pytest.raises(RuntimeError, match="both stage1 and stage2"):
        supervisor._select_preflight_receipts(None, None, None)
    s2 = tmp_path / "s2.json"; s2.write_text("{}")
    assert set(supervisor._select_preflight_receipts(s1, s2, None)) == {"stage1", "stage2"}


def test_preflight_input_refuses_mixed_and_missing(tmp_path, supervisor):
    r = tmp_path / "resume.json"; r.write_text("{}"); s1 = tmp_path / "s1.json"; s1.write_text("{}")
    with pytest.raises(RuntimeError, match="cannot be combined"):
        supervisor._select_preflight_receipts(s1, None, r)
    with pytest.raises(RuntimeError, match="resume receipt missing"):
        supervisor._select_preflight_receipts(None, None, tmp_path / "absent.json")


def _resume_preflight_live_setup(tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    cleanup_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    assert json.loads(cleanup_path.read_text())["status"] == "completed"
    # canonical script module (supervisor imports reconcile/summarize by name at call time)
    rmod = importlib.import_module("reconcile_deepcoder_run")
    monkeypatch.setattr(rmod, "_default_gpu_observer", lambda: gpu_obs)
    monkeypatch.setattr(rmod, "_default_ports_observer",
                        lambda: {"status": "closed", "closed_ports": [8271, 8272, 8273, 8274]})
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
    provider = tmp_path / "provider.json"
    provider.write_text(json.dumps({
        "kind": "rsiflow_provider_probe", "http_status": 200,
        "response": {"status": "completed", "model": "gpt-6-astra", "output": ["READY"]},
        "endpoint": "http://token.acerobotics.com/v1/responses",
        "config_hash": supervisor._sha256(config), "host_identity": supervisor.host_identity(),
        "observed_at": time.time()}))
    return config, run, paths, cleanup_path, provider, rmod


def test_observe_resume_preflight_positive_and_consumer_recompute(tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, cleanup_path, provider, _ = _resume_preflight_live_setup(
        tmp_path, supervisor, reconciler, monkeypatch)
    output = tmp_path / "resume-preflight.json"
    value = supervisor.observe_resume_preflight(config, provider, cleanup_path, output)
    assert value["stage"] == "resume" and value["status"] == "success"
    assert value["cleanup_receipt"]["sha256"] == supervisor._sha256(cleanup_path)
    # the seal consumer independently recomputes the cleanup contract in real mode
    entry = {"path": str(output), "sha256": supervisor._sha256(output)}
    got = supervisor._verify_resume_receipt(entry, expected_backend="linux_bwrap_seccomp_v1",
                                            run_dir=run, config_path=config)
    assert got["stage"] == "resume"


def test_resume_preflight_expired_provider_refused(tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, cleanup_path, provider, _ = _resume_preflight_live_setup(
        tmp_path, supervisor, reconciler, monkeypatch)
    p = json.loads(provider.read_text()); p["observed_at"] = time.time() - 3600
    provider.write_text(json.dumps(p))
    with pytest.raises(RuntimeError, match="provider completion evidence invalid"):
        supervisor.observe_resume_preflight(config, provider, cleanup_path, tmp_path / "r.json")


def test_resume_preflight_missing_provider_refused(tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, cleanup_path, provider, _ = _resume_preflight_live_setup(
        tmp_path, supervisor, reconciler, monkeypatch)
    provider.unlink()
    with pytest.raises(RuntimeError, match="provider_response"):
        supervisor.observe_resume_preflight(config, provider, cleanup_path, tmp_path / "r.json")


def test_resume_preflight_incomplete_cleanup_refused(tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, cleanup_path, provider, _ = _resume_preflight_live_setup(
        tmp_path, supervisor, reconciler, monkeypatch)
    pending = tmp_path / "pending-cleanup.json"
    pending.write_text(json.dumps({"kind": "rsiflow_external_cleanup", "status": "pending",
                                   "binding": {"run_dir": str(run), "config_hash": supervisor._sha256(config)}}))
    with pytest.raises(RuntimeError, match="cleanup not completed"):
        supervisor.observe_resume_preflight(config, provider, pending, tmp_path / "r.json")


def test_resume_preflight_current_owner_alive_refused(tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, cleanup_path, provider, rmod = _resume_preflight_live_setup(
        tmp_path, supervisor, reconciler, monkeypatch)
    # sealed cleanup evidence still says exited (consumer passes), but the CURRENT owner is alive
    monkeypatch.setattr(rmod, "_observe_owner", lambda identity, run_dir: {"state": "alive"})
    with pytest.raises(RuntimeError, match="current owner still active"):
        supervisor.observe_resume_preflight(config, provider, cleanup_path, tmp_path / "r.json")


# --- prelaunch-rejected empty attempt: shared classification + reviewed marker producer -----

def _cleanup_consumer_args(config, run, paths):
    summarizer = _load("summarize_deepcoder_five_round")
    cfg = json.loads(config.read_text()); cfg["_config_path"] = str(config.resolve())
    active = json.loads((run / "active_task.json").read_text()) if (run / "active_task.json").is_file() else {}
    return summarizer, cfg, active


def test_preflight_rejected_empty_dir_restores_current_attempt(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text()); assert receipt["status"] == "completed"
    summarizer, cfg, active = _cleanup_consumer_args(config, run, paths)
    # an empty 0002 left by a preflight rejection makes the 0001 cleanup non-current
    rejected = run / "meta_session/supervision/0002"; rejected.mkdir()
    sup = summarizer._supervision(run)
    assert sup["attempts"] == ["0001", "0002"] and sup["rejected_attempts"] == []
    with pytest.raises(ValueError, match="not_current_attempt"):
        summarizer._validate_cleanup(receipt, receipt_path, run, cfg, paths["output_root"], sup, active)
    # reviewed producer marks the genuine empty rejection from actual evidence (no delete/forge)
    marker = reconciler.mark_preflight_rejected(config, run, "0002")
    assert marker["kind"] == "rsiflow_preflight_rejected"
    assert (rejected / "preflight-rejected.json").is_file() and not (rejected / "identity.json").exists()
    sup = summarizer._supervision(run)
    assert sup["attempts"] == ["0001"] and sup["rejected_attempts"] == ["0002"]
    assert sup["all_attempts_accounted_for"] is True
    # historical 0001 completed cleanup is current again and passes (incl. rejected re-proof)
    summarizer._validate_cleanup(receipt, receipt_path, run, cfg, paths["output_root"], sup, active)
    assert summarizer.next_supervision_number(run) == "0003"  # rejected number never reused


def test_preflight_rejected_with_identity_is_real_attempt(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    rejected = run / "meta_session/supervision/0002"; rejected.mkdir()
    (rejected / "identity.json").write_text(json.dumps({"pid": 1, "pgid": 1, "start_ticks": "1"}))
    # identity present => never an empty rejection; producer refuses, classifier keeps it real
    with pytest.raises(RuntimeError, match="directory not empty"):
        reconciler.mark_preflight_rejected(config, run, "0002")
    summarizer, cfg, active = _cleanup_consumer_args(config, run, paths)
    sup = summarizer._supervision(run)
    assert sup["attempts"] == ["0001", "0002"] and sup["rejected_attempts"] == []
    ok, proof = summarizer.preflight_rejected_proof(rejected, run)
    assert ok is False and "identity.json" in proof["extra_entries"]


def test_preflight_rejected_with_launch_product_is_real_attempt(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    rejected = run / "meta_session/supervision/0002"; rejected.mkdir()
    (rejected / "launch.json").write_text(json.dumps({"kind": "rsiflow_launch"}))
    with pytest.raises(RuntimeError, match="directory not empty"):
        reconciler.mark_preflight_rejected(config, run, "0002")
    summarizer, _, _ = _cleanup_consumer_args(config, run, paths)
    assert summarizer._supervision(run)["attempts"] == ["0001", "0002"]


def test_preflight_rejected_marker_cannot_hide_failed_owner(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    receipt_path = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    rejected = run / "meta_session/supervision/0002"; rejected.mkdir()
    reconciler.mark_preflight_rejected(config, run, "0002")
    # a real exit/reconcile appears in the "rejected" dir => it is no longer an empty rejection
    (rejected / "exit").write_text("1\n")
    (rejected / "reconcile.json").write_text(json.dumps({"status": "complete", "remaining": ["job"]}))
    summarizer, cfg, active = _cleanup_consumer_args(config, run, paths)
    sup = summarizer._supervision(run)
    assert sup["attempts"] == ["0001", "0002"] and sup["rejected_attempts"] == []
    with pytest.raises(ValueError, match="not_current_attempt"):
        summarizer._validate_cleanup(receipt, receipt_path, run, cfg, paths["output_root"], sup, active)


def test_preflight_rejected_marker_immutable_and_absent_refused(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    with pytest.raises(RuntimeError, match="attempt directory absent"):
        reconciler.mark_preflight_rejected(config, run, "0002")
    (run / "meta_session/supervision/0002").mkdir()
    reconciler.mark_preflight_rejected(config, run, "0002")
    with pytest.raises(RuntimeError, match="already exists"):
        reconciler.mark_preflight_rejected(config, run, "0002")


def test_inspect_reports_rejected_and_next_supervision(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    (run / "meta_session/supervision/0002").mkdir()
    reconciler.mark_preflight_rejected(config, run, "0002")
    info = reconciler.inspect_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs))
    assert info["supervision"]["attempts"] == ["0001"]
    assert info["supervision"]["rejected_attempts"] == ["0002"]
    assert info["supervision"]["next_supervision"] == "0003"


def test_future_prelaunch_rejection_leaves_no_directory(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    paths = supervisor.derive_paths(config)
    paths["ready"].write_text(json.dumps({"ready": True}))
    attempt_dir = paths["run_dir"] / "meta_session/supervision/0001"
    command = [sys.executable, "-c", "pass"]
    # real mode without prelaunch evidence => refused BEFORE the attempt dir is created
    with pytest.raises(RuntimeError, match="prelaunch evidence missing"):
        supervisor.launch(config, "0001", command=command, timeout="10s", kill_after="2s",
                          skip_asset_gates=False, prelaunch_evidence=None, expected_commit="a" * 40)
    assert not attempt_dir.exists()  # no empty attempt left behind


def test_resume_preflight_after_rejected_attempt_uses_historical_cleanup(tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, cleanup_path, provider, _ = _resume_preflight_live_setup(
        tmp_path, supervisor, reconciler, monkeypatch)
    # an empty 0002 rejection exists; the completed 0001 cleanup must still serve resume preflight
    (run / "meta_session/supervision/0002").mkdir()
    reconciler.mark_preflight_rejected(config, run, "0002")
    output = tmp_path / "resume-preflight.json"
    value = supervisor.observe_resume_preflight(config, provider, cleanup_path, output)
    assert value["stage"] == "resume" and value["status"] == "success"
    entry = {"path": str(output), "sha256": supervisor._sha256(output)}
    got = supervisor._verify_resume_receipt(entry, expected_backend="linux_bwrap_seccomp_v1",
                                            run_dir=run, config_path=config)
    assert got["stage"] == "resume"


# --- Critical: actual resume launch gate binds latest REAL prior, skips rejected 0002 -----

def _disconnected_seed(tmp_path, supervisor, reconciler):
    config, run, paths, gpu, pids = _seed_run(tmp_path, supervisor, reconciler)
    state_path = run / "meta_session/state.json"
    state = json.loads(state_path.read_text()); state["status"] = "disconnected"
    state_path.write_text(json.dumps(state))
    return config, run, paths, state


def test_resume_launch_gate_skips_rejected_attempt(tmp_path, supervisor, reconciler):
    config, run, paths, state = _disconnected_seed(tmp_path, supervisor, reconciler)
    (run / "meta_session/supervision/0002").mkdir()
    reconciler.mark_preflight_rejected(config, run, "0002")
    # real continuity is 0001 -> new 0003; rejected 0002 must not block history/chain/resumed_from
    result = supervisor.launch(config, "0003", command=_sleep_command(0.2), timeout="10s",
                               kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")
    binding = result["launch_seal"]["binding"]
    assert binding["resumed_from"] == "0001" and binding["thread_id"] == state["thread_id"]
    assert _wait(lambda: (run / "meta_session/supervision/0003/exit").exists())
    # thread seal binds the REAL prior 0001, never the rejected 0002
    assert (run / "meta_session/supervision/0001/resumed-thread-seal.json").is_file()
    assert not (run / "meta_session/supervision/0002/resumed-thread-seal.json").exists()


def test_resume_launch_gate_real_failed_0002_refused(tmp_path, supervisor, reconciler):
    config, run, paths, state = _disconnected_seed(tmp_path, supervisor, reconciler)
    failed = run / "meta_session/supervision/0002"; failed.mkdir()
    (failed / "identity.json").write_text(json.dumps({"pid": 1, "pgid": 1, "start_ticks": "1", "run_dir": str(run)}))
    (failed / "exit").write_text("1\n")
    (failed / "reconcile.json").write_text(json.dumps({"status": "complete", "remaining": ["job"]}))
    # a REAL failed 0002 (identity + unaccounted reconcile) is never skipped
    with pytest.raises(RuntimeError, match="not accounted"):
        supervisor.launch(config, "0003", command=_sleep_command(0.2), timeout="10s",
                          kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")


def test_resume_launch_gate_resumed_from_must_be_latest_real(tmp_path, supervisor, reconciler):
    config, run, paths, state = _disconnected_seed(tmp_path, supervisor, reconciler)
    (run / "meta_session/supervision/0002").mkdir()
    reconciler.mark_preflight_rejected(config, run, "0002")
    with pytest.raises(RuntimeError, match="resumed_from must be latest prior attempt 0001"):
        supervisor.launch(config, "0003", command=_sleep_command(0.2), timeout="10s",
                          kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0002")


def test_resume_launch_gate_rejected_number_not_reused(tmp_path, supervisor, reconciler):
    config, run, paths, state = _disconnected_seed(tmp_path, supervisor, reconciler)
    (run / "meta_session/supervision/0002").mkdir()
    reconciler.mark_preflight_rejected(config, run, "0002")
    with pytest.raises(RuntimeError, match="already exists"):
        supervisor.launch(config, "0002", command=_sleep_command(0.2), timeout="10s",
                          kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")


# --- Important: true emptiness (nested/unknown entries) + lock-inner re-verification -----

def test_preflight_rejected_nested_external_cleanup_is_real(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    rejected = run / "meta_session/supervision/0002"; rejected.mkdir()
    nested = rejected / "external-cleanup/obs-x"; nested.mkdir(parents=True)
    (nested / "receipt.json").write_text(json.dumps({"status": "completed"}))
    with pytest.raises(RuntimeError, match="directory not empty"):
        reconciler.mark_preflight_rejected(config, run, "0002")
    summarizer, _, _ = _cleanup_consumer_args(config, run, paths)
    assert summarizer._supervision(run)["attempts"] == ["0001", "0002"]
    # even WITH a marker, a nested entry => not a rejection (content-based, not allowlist)
    (rejected / "preflight-rejected.json").write_text(json.dumps({
        "kind": "rsiflow_preflight_rejected", "run_dir": str(run), "supervision_attempt": "0002",
        "config_hash": "c" * 64, "host_identity": {"host": "h"}, "observed_at": 1.0}))
    ok, proof = summarizer.preflight_rejected_proof(rejected, run)
    assert ok is False and "external-cleanup" in proof["extra_entries"]
    assert summarizer._supervision(run)["rejected_attempts"] == []


def test_preflight_rejected_unknown_entry_is_real(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    rejected = run / "meta_session/supervision/0002"; rejected.mkdir()
    (rejected / "preflight-rejected.json").write_text(json.dumps({
        "kind": "rsiflow_preflight_rejected", "run_dir": str(run), "supervision_attempt": "0002",
        "config_hash": "c" * 64, "host_identity": {"host": "h"}, "observed_at": 1.0}))
    (rejected / "mystery.bin").write_text("unknown product")
    summarizer, _, _ = _cleanup_consumer_args(config, run, paths)
    ok, proof = summarizer.preflight_rejected_proof(rejected, run)
    assert ok is False and "mystery.bin" in proof["extra_entries"]
    assert summarizer._supervision(run)["attempts"] == ["0001", "0002"]


def test_preflight_rejected_reverified_under_lock(tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, gpu_obs, _ = _seed_not_created_run(tmp_path, supervisor, reconciler)
    rejected = run / "meta_session/supervision/0002"; rejected.mkdir()
    real_acquire = reconciler._acquire_global_lock
    def acquire_then_mutate(lock_file, timeout=2.0):
        real_acquire(lock_file, timeout)
        # a concurrent product appears inside the lock window; the inner re-check must catch it
        (rejected / "late-product.json").write_text("{}")
    monkeypatch.setattr(reconciler, "_acquire_global_lock", acquire_then_mutate)
    with pytest.raises(RuntimeError, match="directory not empty"):
        reconciler.mark_preflight_rejected(config, run, "0002")
    assert not (rejected / "preflight-rejected.json").exists()


# --- R7: SFT product-level joint resume drill (controlled Meta transport) -----

_RESUME_SCRIPT = '''
import json, sys
from pathlib import Path
run = Path(sys.argv[1]); spy = Path(sys.argv[2])
phases = sorted(run.glob("round_*/attempts/attempt_*/**/gpu_phase_receipt.json"))
for phase in phases:
    v = json.loads(phase.read_text())
    if v.get("status") not in ("completed", "training_complete"):
        (run / "meta_session" / "requires_audit.json").write_text(json.dumps({"reason": "training_unsealed"}))
        sys.exit(2)
    if not (phase.parent / "checkpoint_trained.json").is_file():
        (run / "meta_session" / "requires_audit.json").write_text(json.dumps({"reason": "no_trained_checkpoint"}))
        sys.exit(2)
# Training already complete: continue from the NEXT milestone. Re-dispatching training
# would increment the spy here; we do NOT (no retrain).
count = int(spy.read_text()) if spy.is_file() else 0
# (no dispatch => spy unchanged)
(run / "meta_session" / "resume_continued.json").write_text(
    json.dumps({"continued": True, "retrained": False, "dispatch_count": count}))
sys.exit(0)
'''


def _seed_sft_resume_run(tmp_path, supervisor, reconciler, *, training_sealed=True):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    attempt = run / "meta_session/supervision/0001"
    # seal historical supervision 0001 with exit 143 + complete reconcile
    (attempt / "exit").write_text("143")
    (attempt / "reconcile.json").write_text(json.dumps({"status": "complete", "remaining": []}))
    (run / "meta_session/state.json").write_text(json.dumps({
        "status": "disconnected", "thread_id": "t1",
        "launcher_pid": pids["launcher_pid"], "codex_pid": pids["app_pid"]}))
    # successful SFT product receipts (production shape)
    model_update = run / "round_2/attempts/attempt_1/candidate/model_update"
    model_update.mkdir(parents=True)
    status = "completed" if training_sealed else "training_dispatched"
    (model_update / "gpu_phase_receipt.json").write_text(json.dumps({
        "status": status, "training_pid": 40001, "training_identity": {"pid": 40001, "pgid": 40001, "start_ticks": "150"},
        "training_request_dir": str(model_update), "training_run_dir": str(run),
        "training_argv": ["python", "train.py", "--request-dir", str(model_update)],
        "command": ["python", "train.py", "--request-dir", str(model_update)],
        "replica_gpus": [0, 1, 2, 3]}))
    if training_sealed:
        (model_update / "checkpoint_trained.json").write_text(json.dumps({
            "checkpoint_path": "/models/trained", "training_method": "positive_sft_lora", "training_steps": 3}))
        (model_update / "training_metrics.json").write_text(json.dumps({"world_size": 4, "optimizer_steps": 3}))
    spy = tmp_path / "training_dispatch_spy"
    spy.write_text("0")
    script = tmp_path / "controlled_resume.py"
    script.write_text(_RESUME_SCRIPT)
    return config, run, paths, gpu_obs, pids, spy, script


def test_r7_sft_completed_resume_no_retrain(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids, spy, script = _seed_sft_resume_run(tmp_path, supervisor, reconciler)
    model_update = run / "round_2/attempts/attempt_1/candidate/model_update"
    before_bytes = (model_update / "checkpoint_trained.json").read_bytes()
    before_hash = supervisor._sha256(model_update / "checkpoint_trained.json")
    command = [sys.executable, str(script), str(run), str(spy)]
    result = supervisor.launch(config, "0002", command=command, timeout="10s", kill_after="2s",
                               skip_asset_gates=True, mode="resume", resumed_from="0001")
    attempt2 = run / "meta_session/supervision/0002"
    assert _wait(lambda: (attempt2 / "exit").is_file())
    assert (attempt2 / "exit").read_text().strip() == "0"
    # training dispatch spy NOT incremented; child/receipt bytes/hash unchanged; no retrain
    assert spy.read_text() == "0"
    assert (model_update / "checkpoint_trained.json").read_bytes() == before_bytes
    assert supervisor._sha256(model_update / "checkpoint_trained.json") == before_hash
    continued = json.loads((run / "meta_session/resume_continued.json").read_text())
    assert continued["continued"] is True and continued["retrained"] is False
    # no second training directory created
    assert len(list(run.glob("round_*/attempts/attempt_*/**/gpu_phase_receipt.json"))) == 1
    # historical 143 preserved
    assert (run / "meta_session/supervision/0001/exit").read_text() == "143"


def test_r7_unsealed_training_requires_audit_before_experiment(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids, spy, script = _seed_sft_resume_run(
        tmp_path, supervisor, reconciler, training_sealed=False)
    command = [sys.executable, str(script), str(run), str(spy)]
    # F7: unsealed training => requires_audit BEFORE Popen; experiment command never runs.
    with pytest.raises(RuntimeError, match="requires_audit"):
        supervisor.launch(config, "0002", command=command, timeout="10s", kill_after="2s",
                          skip_asset_gates=True, mode="resume", resumed_from="0001")
    assert spy.read_text() == "0"  # no training dispatch
    assert not (run / "meta_session/supervision/0002").exists()  # experiment never started
    assert not (run / "meta_session/resume_continued.json").exists()


def test_r7_dead_no_final_job_resume_refused_before_experiment(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids, spy, script = _seed_sft_resume_run(tmp_path, supervisor, reconciler)
    # make a job dead-without-final result => R7 gate refuses resume before experiment
    job_path = run / "meta_session/jobs/run_parent_1/job.json"
    job = json.loads(job_path.read_text())
    (run / "controller_transport/run_parent_1/result.json").unlink()
    command = [sys.executable, str(script), str(run), str(spy)]
    with pytest.raises(RuntimeError, match="no terminal result"):
        supervisor.launch(config, "0002", command=command, timeout="10s", kill_after="2s",
                          skip_asset_gates=True, mode="resume", resumed_from="0001")
    assert spy.read_text() == "0"
    assert not (run / "meta_session/supervision/0002").exists()  # experiment never started


def test_r7_intermediate_protocol_false_and_cleanup_pending_separation(tmp_path, supervisor, reconciler):
    # intermediate B1/B2 products => protocol_complete=false even with valid cleanup;
    # protocol complete but cleanup pending => run_complete=false (separation preserved).
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    summarizer = _load("summarize_deepcoder_five_round")
    cfg = json.loads(config.read_text()); cfg["_config_path"] = str(config.resolve())
    # only B1/B2 present (intermediate) => protocol_complete false
    result = summarizer.summarize(run, config_path=config)
    assert result["protocol_complete"] is False
    assert result["run_complete"] is False


def test_r7_production_sft_dispatch_spy_not_called_on_resume(tmp_path, supervisor, reconciler, monkeypatch):
    # F7: the spy is bound to the PRODUCTION training dispatch entry (TaskAdapter.sft),
    # not a test script. A completed-training resume must not re-dispatch training; the
    # resume gate binds the real prior thread and the child/training bytes stay unchanged.
    import task_adapter
    calls = []
    def spy_sft(self, arguments):
        calls.append(arguments)
        raise AssertionError("production training dispatch must not be re-called on resume")
    monkeypatch.setattr(task_adapter.TaskAdapter, "sft", spy_sft)
    config, run, paths, gpu_obs, pids, spy, script = _seed_sft_resume_run(tmp_path, supervisor, reconciler)
    model_update = run / "round_2/attempts/attempt_1/candidate/model_update"
    before_hash = supervisor._sha256(model_update / "checkpoint_trained.json")
    command = [sys.executable, str(script), str(run), str(spy)]
    result = supervisor.launch(config, "0002", command=command, timeout="10s", kill_after="2s",
                               skip_asset_gates=True, mode="resume", resumed_from="0001")
    attempt2 = run / "meta_session/supervision/0002"
    assert _wait(lambda: (attempt2 / "exit").is_file())
    # production training dispatch entry was never called; training bytes unchanged
    assert calls == []
    assert supervisor._sha256(model_update / "checkpoint_trained.json") == before_hash
    # resume binds the real prior thread (not the placeholder default)
    assert result["launch_seal"]["binding"]["thread_id"] == "t1"
    assert (run / "meta_session/supervision/0001/resumed-thread-seal.json").is_file()
    assert (run / "meta_session/supervision/0001/exit").read_text() == "143"
# Fix4: exercise the real phase entry; only GPU/process effects are replaced.
@pytest.mark.parametrize('identity', [None, {'pid': 123, 'pgid': 123},
                                      {'pid': 124, 'pgid': 123, 'start_ticks': '7'}])
def test_fix4_phase_missing_identity_never_serves_or_publishes(tmp_path, monkeypatch, identity):
    import contextlib
    from types import SimpleNamespace
    monkeypatch.syspath_prepend(str(ROOT / 'RSIFlow/runtime/scripts'))
    import train_four_gpu_phase as phase
    directory = tmp_path / 'runs/run/round_1/attempts/attempt_1/candidate/model_update'
    directory.mkdir(parents=True)
    (directory / 'training_request.json').write_text('{}')
    (directory / 'checkpoint_trained.json').write_text(json.dumps({
        'checkpoint_path': str(tmp_path / 'child'), 'training_metrics': str(directory / 'metrics.json'),
        'training_summary': 'trained'}))
    (directory / 'metrics.json').write_text(json.dumps({'world_size': 4, 'rank_records': [{'rank': i} for i in range(4)]}))
    monkeypatch.setattr(sys, 'argv', ['phase', '--request-dir', str(directory), '--config', 'unused'])
    monkeypatch.setattr(phase.signal, 'signal', lambda *a: None)
    monkeypatch.setattr(phase, 'load_config', lambda _: SimpleNamespace(
        output_root=str(tmp_path), gpu_execution='phased_four', trainer_python=sys.executable,
        task_base_url='http://127.0.0.1', training_timeout_seconds=10))
    monkeypatch.setattr(phase, 'resolve_training_base', lambda _: tmp_path / 'base')
    monkeypatch.setattr(phase, 'read_positive_rows', lambda *a: [])
    monkeypatch.setattr(phase, 'exclusive_lock', lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(phase, 'training_gpu_lease', lambda *a: contextlib.nullcontext())
    monkeypatch.setattr(phase, 'stop_services', lambda *a: None)
    monkeypatch.setattr(phase.subprocess, 'Popen', lambda *a, **k: SimpleNamespace(pid=123, wait=lambda **k: 0))
    monkeypatch.setattr(phase, 'process_identity', lambda _: identity)
    monkeypatch.setattr(phase, '_wait_group_exited', lambda *a, **k: True)
    served = []
    monkeypatch.setattr(phase, 'start_services', lambda *a: served.append(a) or [{'gpu': i} for i in range(4)])
    with pytest.raises(RuntimeError, match='identity'):
        phase.main()
    assert served == []
    assert not (directory / 'checkpoint.json').exists()
    receipt = json.loads((directory / 'gpu_phase_receipt.json').read_text())
    assert receipt['training_pid'] == 123
    assert receipt['status'] == 'training_failed_requires_audit'


def test_fix4_status_permission_is_unknown(tmp_path, supervisor, monkeypatch):
    config = _config(tmp_path, tmp_path / 'out', 'run')
    attempt = supervisor.derive_paths(config)['run_dir'] / 'meta_session/supervision/0001'
    attempt.mkdir(parents=True)
    (attempt / 'identity.json').write_text(json.dumps({'pid': 123, 'start_ticks': '7'}))
    monkeypatch.setattr(supervisor, '_proc_stat', lambda _: (_ for _ in ()).throw(PermissionError()))
    result = supervisor.status(config, '0001')
    assert result['alive'] is None
    assert result['owner_state'] == 'unknown'


def test_fix4_restore_rejects_resource_flags_without_evidence(tmp_path, reconciler):
    config = tmp_path / 'config.json'; config.write_text('{}')
    receipt = {'kind': 'rsiflow_external_cleanup', 'status': 'completed', 'blockers': [], 'errors': [],
               'binding': {'run_dir': str(tmp_path / 'run'), 'supervision_attempt': '0002',
                           'config_hash': reconciler._sha256(config)},
               'evidence_files_sha256': {str(config): reconciler._sha256(config)},
               'owners': {'launcher': {'state': 'exited'}, 'app_server': {'state': 'exited'}},
               'registry': {'phase': 'services_stopped'}, 'gpu_observations': [{}, {}],
               'ports_observation': {'status': 'closed'}}
    with pytest.raises((ValueError, RuntimeError)):
        reconciler._verify_cleanup_evidence(receipt, tmp_path / 'receipt.json', config, tmp_path / 'run', '0002')


def test_fix4_job_wrong_turn_blocks_service_stop(tmp_path, supervisor, reconciler):
    config, run, paths, gpu, _ = _seed_run(tmp_path, supervisor, reconciler, services_stopped=False)
    command = run / 'meta_session/turns/run_parent_1/command.json'
    command.write_text(json.dumps({'request': {'operation': 'foreign'}}))
    stopped = []
    receipt = reconciler.cleanup_run(config, run, '0001', gpu_observer=_gpu_observer(gpu),
                                     stop_services_fn=lambda c: stopped.append(c), skip_asset_gates=True)
    assert not stopped
    assert json.loads(receipt.read_text())['status'] != 'completed'


@pytest.mark.parametrize('field,value', [('gpus', []), ('host', None), ('ports', []), ('provider', 'wrong')])
def test_fix4_prelaunch_rejects_unusable_field_evidence(tmp_path, supervisor, field, value):
    evidence = _prelaunch_evidence(tmp_path)
    for stage in ('stage1', 'stage2'):
        path = Path(evidence['preflight_receipts'][stage]['path'])
        record = json.loads(path.read_text()); record[field] = value
        path.write_text(json.dumps(record))
        evidence['preflight_receipts'][stage]['sha256'] = supervisor._sha256(path)
    path = Path(evidence['preflight_receipts']['stage2']['path'])
    record = json.loads(path.read_text()); record['stage1_receipt_hash'] = evidence['preflight_receipts']['stage1']['sha256']
    path.write_text(json.dumps(record)); evidence['preflight_receipts']['stage2']['sha256'] = supervisor._sha256(path)
    with pytest.raises(RuntimeError):
        supervisor.verify_prelaunch_seal(evidence, expected_commit='a'*40,
                                        expected_backend='linux_bwrap_seccomp_v1', attempt_dir=tmp_path / 'attempt')


def test_fix4_restore_receipt_write_failure_reconciles_started_owner(tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, gpu, _ = _seed_run(tmp_path, supervisor, reconciler)
    reconciler.cleanup_run(config, run, '0001', gpu_observer=_gpu_observer(gpu), skip_asset_gates=True)
    command = _occupier_script(tmp_path)
    shared = tmp_path / 'shared-identity.json'
    written_identity = []
    real_write = reconciler._atomic_write
    def write(path, value):
        if Path(path) == shared:
            written_identity.append(value)
        if Path(path).name == 'restore.json':
            raise OSError('controlled receipt disk failure')
        real_write(path, value)
    monkeypatch.setattr(reconciler, '_atomic_write', write)
    with pytest.raises(OSError, match='disk failure'):
        reconciler.restore_occupier(config, run, '0001', gpu_observer=_restore_gpu_observer(command),
                                    occupier_command=command, shared_identity_path=shared)
    assert written_identity
    assert not shared.exists()
    assert reconciler._observe_owner(written_identity[-1], run)['state'] == 'exited'


def test_fix4_restore_directory_failure_reconciles_started_owner(tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, gpu, _ = _seed_run(tmp_path, supervisor, reconciler)
    reconciler.cleanup_run(config, run, '0001', gpu_observer=_gpu_observer(gpu), skip_asset_gates=True)
    command = _occupier_script(tmp_path)
    shared = tmp_path / 'directory-failure-identity.json'
    started = []
    real_start = reconciler._start_occupier_process
    def start(*a, **k):
        identity = real_start(*a, **k); started.append(identity); return identity
    real_mkdir = Path.mkdir
    def mkdir(path, *a, **k):
        if path.name.startswith('restore-'):
            raise OSError('controlled observation mkdir failure')
        return real_mkdir(path, *a, **k)
    monkeypatch.setattr(reconciler, '_start_occupier_process', start)
    monkeypatch.setattr(Path, 'mkdir', mkdir)
    try:
        with pytest.raises(OSError, match='mkdir failure'):
            reconciler.restore_occupier(config, run, '0001', gpu_observer=_restore_gpu_observer(command),
                                        occupier_command=command, shared_identity_path=shared)
        assert started and reconciler._observe_owner(started[0], run)['state'] == 'exited'
        assert not shared.exists()
    finally:
        for identity in started:
            reconciler._terminate_owned_group(identity)


def test_fix4_provider_response_requires_transport_binding(tmp_path, supervisor, reconciler):
    config, run, paths, gpu, _ = _seed_run(tmp_path, supervisor, reconciler)
    stage = {'gpus': [{'gpu': g, 'uuid': f'GPU-{g}'} for g in range(4)],
             'ports': [8271, 8272, 8273, 8274], 'config_hash': supervisor._sha256(config)}
    for key, value in {'provider_response': {'status': 'completed', 'model': 'gpt-6-astra', 'output': ['READY']},
                       'gpu_observation': gpu,
                       'ports_observation': {'status': 'closed', 'closed_ports': stage['ports']}}.items():
        path = tmp_path / (key + '.json'); path.write_text(json.dumps(value))
        stage[key] = {'path': str(path), 'sha256': supervisor._sha256(path)}
    with pytest.raises(RuntimeError, match='provider'):
        supervisor._validate_stage_measurements(stage, require_idle=True)


def test_fix4_resume_rejects_thread_changed_from_runtime_seal(tmp_path, supervisor, reconciler):
    config, run, paths, gpu, _ = _seed_run(tmp_path, supervisor, reconciler)
    state_path = run / 'meta_session/state.json'
    state = json.loads(state_path.read_text()); state.update(status='disconnected', thread_id='different-thread')
    state_path.write_text(json.dumps(state))
    attempt = run / 'meta_session/supervision/0002'
    try:
        with pytest.raises((ValueError, RuntimeError), match='thread'):
            supervisor.launch(config, '0002', command=_sleep_command(.05), skip_asset_gates=True,
                              mode='resume', resumed_from='0001')
    finally:
        if attempt.exists():
            assert _wait(lambda: (attempt / 'exit').exists())


def test_fix4_initial_identity_failure_blocks_duplicate_restore(tmp_path, reconciler, monkeypatch):
    command = _occupier_script(tmp_path)
    shared = tmp_path / 'initial-identity.json'
    pending = shared.with_suffix('.pending.json')
    processes = []
    real_popen = subprocess.Popen
    real_record = reconciler._process_record
    def popen(*a, **k):
        process = real_popen(*a, **k); processes.append(process); return process
    calls = []
    def record(pid):
        calls.append(pid)
        if len(calls) == 1:
            raise PermissionError('controlled initial identity unreadable')
        return real_record(pid)
    monkeypatch.setattr(reconciler.subprocess if hasattr(reconciler, 'subprocess') else subprocess, 'Popen', popen)
    monkeypatch.setattr(reconciler, '_process_record', record)
    try:
        with pytest.raises(PermissionError):
            reconciler._start_occupier_process(command, shared_identity_path=shared,
                                               gpu_observer=lambda: pytest.fail('GPU must not be queried'))
        assert pending.is_file()
        assert json.loads(pending.read_text())['spawned_pid'] == processes[0].pid
        with pytest.raises((RuntimeError, FileExistsError)):
            reconciler._start_occupier_process(command, shared_identity_path=shared,
                                               gpu_observer=lambda: pytest.fail('must refuse duplicate'))
        assert len(processes) == 1
    finally:
        for process in processes:
            if process.poll() is None:
                reconciler._terminate_owned_group(real_record(process.pid))
                process.wait(timeout=10)


def test_layout_fix1_reused_leader_refuses_all_signals(reconciler, monkeypatch):
    signals = []
    monkeypatch.setattr(reconciler.os, 'kill', lambda *a: signals.append(a))
    monkeypatch.setattr(reconciler, '_group_members_with_errors', lambda _: ([{'pid': 100, 'start_ticks': '2'}], []))
    monkeypatch.setattr(reconciler, '_process_record', lambda _: {'pid': 100, 'pgid': 100, 'start_ticks': '2'})
    monkeypatch.setattr(reconciler, '_observe_owner', lambda *a: {'state': 'exited'})
    with pytest.raises(RuntimeError, match='identity|reused'):
        reconciler._terminate_owned_group({'pid': 100, 'pgid': 100, 'start_ticks': '1'}, grace=.01)
    assert signals == []


def test_layout_fix1_foreign_rank_refuses_before_leader_signal(reconciler, monkeypatch):
    signals = []
    leader = {'pid': 100, 'pgid': 100, 'start_ticks': '1', 'ppid': 1, 'argv': ['python', '/owned/train.py']}
    rank = {'pid': 200, 'pgid': 200, 'start_ticks': '2', 'ppid': 999, 'argv': ['python', '/owned/train.py']}
    records = {100: leader, 200: rank}
    monkeypatch.setattr(reconciler, '_process_record', lambda pid: records[pid])
    monkeypatch.setattr(reconciler.os, 'kill', lambda *a: signals.append(a))
    monkeypatch.setattr(reconciler, '_identity_alive', lambda _: False)
    with pytest.raises(RuntimeError, match='topology|parent|seal'):
        reconciler._terminate_owned_group({**leader, 'rank_members': [rank]}, grace=.01)
    assert signals == []


def test_layout_fix1_unknown_rank_discovery_is_not_empty(reconciler, monkeypatch):
    class Proc:
        def iterdir(self):
            return [type('Entry', (), {'name': '200'})()]
    real_path = reconciler.Path
    monkeypatch.setattr(reconciler, 'Path', lambda p: Proc() if p == '/proc' else real_path(p))
    monkeypatch.setattr(reconciler, '_process_record', lambda _: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(RuntimeError, match='unknown|unreadable'):
        reconciler._discover_ranks(100, '/owned/train.py')


def test_layout_fix1_nvml_offset_without_evidence_stays_pending(tmp_path, reconciler):
    command = _occupier_script(tmp_path)
    identity_path = tmp_path / 'identity.json'
    observe = _restore_gpu_observer(command, nvml_offset=1000000)
    observe()  # helper-only test: load observation, without restore's idle check
    try:
        with pytest.raises(RuntimeError, match='unresolved'):
            reconciler._start_occupier_process(command, shared_identity_path=identity_path,
                                               gpu_observer=observe, exec_timeout=2)
        pending = json.loads(identity_path.with_suffix('.pending.json').read_text())
        assert pending['pid_mapping']['status'] == 'unresolved'
        assert pending['pid_mapping']['compute_apps']
        assert len(pending['rank_members']) == 4
        assert not identity_path.exists()
    finally:
        if identity_path.with_suffix('.pending.json').exists():
            p = json.loads(identity_path.with_suffix('.pending.json').read_text())
            if p.get('topology'):
                reconciler._terminate_owned_group(p)


def test_layout_fix1_legacy_stage1_handoff_captures_independent_ranks(tmp_path, supervisor, reconciler, monkeypatch):
    command = _occupier_script(tmp_path)
    leader = subprocess.Popen(command, start_new_session=True)
    saved = None
    try:
        assert _wait(lambda: len(reconciler._discover_ranks(leader.pid, str(command[1]))) == 4)
        old = reconciler._process_record(leader.pid)
        old.update(owner='dev4-rsiflow-idle-occupier', gpus=[0, 1, 2, 3])
        saved = reconciler.snapshot_occupier(old, str(command[1]))
        shared = tmp_path / 'legacy-identity.json'; shared.write_text(json.dumps(old))
        config = _config(tmp_path, tmp_path / 'out', 'run')
        # Field measurements are independent of this test's real process topology.
        module = __import__('reconcile_deepcoder_run')
        monkeypatch.setattr(module, '_default_gpu_observer', lambda: {'per_gpu': [{'gpu':i,'uuid':f'GPU-{i}'} for i in range(4)]})
        monkeypatch.setattr(module, '_default_ports_observer', lambda: {'status':'closed'})
        monkeypatch.setattr(supervisor, '_validate_stage_measurements', lambda *a, **k: None)
        monkeypatch.setattr(module, 'snapshot_occupier', lambda identity: reconciler.snapshot_occupier(identity, str(command[1])))
        stage = tmp_path / 'stage1.json'
        supervisor.observe_preflight(config, 'stage1', tmp_path/'provider.json', shared, stage)
        record = json.loads(stage.read_text())
        snapshot = json.loads(Path(record['occupier_identity']['path']).read_text())
        assert len(snapshot['rank_members']) == 4
        assert snapshot['topology_sha256']
        handoff = tmp_path/'handoff.json'
        supervisor.handoff_occupier(stage, handoff)
        stop = json.loads(handoff.read_text())
        assert stop['after']['state'] == 'exited'
        assert len(stop['after']['identities']) == 5
        assert all(reconciler._identity_alive(rank) is False for rank in saved['rank_members'])
        assert json.loads(shared.read_text()) == old  # no manual patching of legacy identity
    finally:
        if saved:
            reconciler._terminate_owned_group(saved, grace=.2)
        leader.wait(timeout=10)


def _layout_fix1_topology(reconciler, monkeypatch):
    leader = {'pid': 100, 'pgid': 100, 'start_ticks': '1', 'ppid': 1, 'argv': ['python', '/owned/train.py']}
    ranks = [{'pid': 200+i, 'pgid': 200+i, 'start_ticks': str(10+i), 'ppid': 100,
              'argv': ['python','/owned/train.py'], 'local_rank': {'LOCAL_RANK': str(i), 'RANK': str(i)}} for i in range(4)]
    records = {r['pid']: r for r in [leader, *ranks]}
    monkeypatch.setattr(reconciler, '_process_record', lambda pid: records[pid])
    saved = reconciler._seal_topology(leader, ranks, '/owned/train.py', complete=True)
    return saved, records


@pytest.mark.parametrize('mode', ['direct', 'nspid', 'missing', 'partial', 'foreign', 'extra', 'wrong_gpu'])
def test_layout_fix1_gpu_ownership_requires_independent_pid_evidence(reconciler, monkeypatch, mode):
    saved, records = _layout_fix1_topology(reconciler, monkeypatch)
    def view(pid):
        if pid >= 900:
            if mode == 'missing' or (mode == 'partial' and pid == 903):
                raise FileNotFoundError('NVML PID not visible')
            rank = records[pid - 700]
            return {'identity': {**rank, 'pid': pid}, 'nspid': [pid, rank['pid']],
                    'pid_namespace': 'pid:[wrong]' if mode == 'foreign' else 'pid:[target]',
                    'status_raw': f"NSpid:\t{pid}\t{rank['pid']}"}
        return {'identity': records[pid], 'nspid': [pid], 'pid_namespace': 'pid:[target]', 'status_raw': f'NSpid:\t{pid}'}
    monkeypatch.setattr(reconciler, '_pid_view', view)
    apps = [{'pid': (200 if mode == 'direct' else 900)+i, 'uuid': f'GPU-{i}'} for i in range(4)]
    if mode == 'extra': apps.append({'pid': 999, 'uuid': 'GPU-0'})
    if mode == 'wrong_gpu': apps[0]['uuid'], apps[1]['uuid'] = apps[1]['uuid'], apps[0]['uuid']
    gpu = {'compute_apps': apps, 'per_gpu':[{'gpu':i,'uuid':f'GPU-{i}'} for i in range(4)]}
    mapping = reconciler._gpu_owner_mapping(saved, gpu)
    assert mapping['status'] == {'direct':'direct','nspid':'nspid_verified'}.get(mode, 'unresolved')
    assert mapping['compute_apps'] == apps
    if mode not in ('direct','nspid'):
        assert mapping['errors']


def test_layout_fix1_sealed_ranks_survive_leader_exit_and_reparent(tmp_path, reconciler):
    command = _occupier_script(tmp_path)
    leader = subprocess.Popen(command, start_new_session=True)
    saved = None
    try:
        assert _wait(lambda: len(reconciler._discover_ranks(leader.pid, str(command[1]))) == 4)
        saved = reconciler.snapshot_occupier(reconciler._process_record(leader.pid), str(command[1]))
        leader.terminate(); leader.wait(timeout=10)
        assert all(reconciler._identity_alive(r) for r in saved['rank_members'])
        result = reconciler._terminate_owned_group(saved, grace=.2)
        assert result['state'] == 'exited' and len(result['identities']) == 5
        assert all(reconciler._identity_alive(r) is False for r in saved['rank_members'])
    finally:
        if saved: reconciler._terminate_owned_group(saved, grace=.2)
        if leader.poll() is None: leader.terminate(); leader.wait(timeout=10)


def test_layout_fix1_unreadable_rank_refuses_all_signals(reconciler, monkeypatch):
    saved, records = _layout_fix1_topology(reconciler, monkeypatch)
    def read(pid):
        if pid == 202: raise PermissionError('rank unreadable')
        return records[pid]
    monkeypatch.setattr(reconciler, '_process_record', read)
    signals = []
    monkeypatch.setattr(reconciler.os, 'kill', lambda *a: signals.append(a))
    with pytest.raises(RuntimeError, match='unknown'):
        reconciler._terminate_owned_group(saved, grace=.01)
    assert not signals


def test_layout_fix1_rank_ignores_term_only_saved_identity_gets_kill(reconciler, monkeypatch):
    saved, records = _layout_fix1_topology(reconciler, monkeypatch)
    current = dict(records)
    def read(pid):
        if pid not in current: raise FileNotFoundError()
        return current[pid]
    signals = []
    def kill(pid, sig):
        signals.append((pid,sig))
        if pid != 202 or sig == signal.SIGKILL: current.pop(pid,None)
    monkeypatch.setattr(reconciler,'_process_record',read)
    monkeypatch.setattr(reconciler.os,'kill',kill)
    result = reconciler._terminate_owned_group(saved, grace=.01)
    assert result['state'] == 'exited'
    assert (202,signal.SIGKILL) in signals
    assert {pid for pid,_ in signals} <= set(records)


def test_layout_fix1_rollback_unknown_keeps_topology_and_pending(tmp_path, reconciler, monkeypatch):
    command = _occupier_script(tmp_path)
    shared = tmp_path / 'identity.json'
    observer = _restore_gpu_observer(command, nvml_offset=1000000); observer()
    real_terminate = reconciler._terminate_owned_group
    attempts = []
    def unknown(identity, *a, **k):
        attempts.append(identity)
        raise RuntimeError('rank observation unknown')
    monkeypatch.setattr(reconciler,'_terminate_owned_group',unknown)
    try:
        with pytest.raises(RuntimeError,match='unresolved'):
            reconciler._start_occupier_process(command,shared_identity_path=shared,gpu_observer=observer,exec_timeout=3)
        pending = json.loads(shared.with_suffix('.pending.json').read_text())
        assert len(attempts) == 1 and len(attempts[0]['rank_members']) == 4
        assert pending['rollback']['state'] == 'unknown'
        assert pending['topology_sha256']
        with pytest.raises(FileExistsError):
            reconciler._start_occupier_process(command,shared_identity_path=shared,gpu_observer=observer)
    finally:
        if attempts: real_terminate(attempts[0],grace=.2)


# --- resume launcher pre-state missing-key failure: shared cleanup proof -------------------


def _seed_prestate_missing_key(tmp_path, supervisor, reconciler, monkeypatch, *, legacy=False):
    config, run, paths, gpu_obs, _ = _seed_run(
        tmp_path, supervisor, reconciler, isolated_openai=True)
    old_turn = run / "meta_session/turns/run_parent_1"
    new_turn = run / "meta_session/turns/0000"
    old_turn.rename(new_turn)
    job_path = run / "meta_session/jobs/run_parent_1/job.json"
    job = json.loads(job_path.read_text())
    job["turn_dir"] = str(new_turn)
    job_path.write_text(json.dumps(job))
    (new_turn / "receipt.json").write_text(json.dumps({
        "operation": "run_parent", "status": "running", "job_id": "run_parent_1",
        "result_path": job["result_path"]}))
    if legacy:
        _legacy_initial_history(run, config)
    state_path = run / "meta_session/state.json"
    state = json.loads(state_path.read_text())
    state["status"] = "disconnected"
    state_path.write_text(json.dumps(state))
    (run / "meta_session/workflow_state.json").write_text(json.dumps({
        "current_stage": "A0" if legacy else "B2", "finish_allowed": False}))
    previous = reconciler.cleanup_run(config, run, "0001", gpu_observer=_gpu_observer(gpu_obs),
                                      skip_asset_gates=True)
    assert json.loads(previous.read_text())["status"] == "completed"
    if legacy:
        old = json.loads(previous.read_text())
        old["protocol"].pop("turn_inventory")
        _legacy_keep_only_job_turn_snapshots(old)
        previous.write_text(json.dumps(old))
        _legacy_complete_b1_history(run, config)
    common = {"config_path": str(config.resolve()), "config_hash": supervisor._sha256(config),
              "run_dir": str(run.resolve()), "supervision_attempt": "0002"}
    expected_gpus = [{"gpu": g, "uuid": f"GPU-{g}"} for g in range(4)]
    resume_receipt = tmp_path / "prestate-resume-preflight.json"
    resume_receipt.write_text(json.dumps({
        "stage": "resume", "status": "success", **common,
        "host": supervisor.host_identity()["host"],
        "boot_id": supervisor.host_identity()["boot_id"],
        "pid_namespace": supervisor.host_identity()["pid_namespace"],
        "backend": "linux_bwrap_seccomp_v1", "provider": "gpt-6-astra",
        "ports": [8271, 8272, 8273, 8274], "gpus": expected_gpus,
        "cleanup_receipt": {"path": str(previous), "sha256": supervisor._sha256(previous)},
    }))
    prelaunch = {
        "kind": "rsiflow_prelaunch_seal",
        "reviewed_commit": "beebd9b42b734c0f5a1dd6ba927931ee76b81f43" if legacy else "a" * 40,
        "backend": "linux_bwrap_seccomp_v1", "binding": {**common, "launch_mode": "resume"},
        "host_identity": supervisor.host_identity(), "expected_gpus": expected_gpus,
        "preflight_receipts": {"resume": {"path": str(resume_receipt),
                                          "sha256": supervisor._sha256(resume_receipt)}},
    }
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    supervisor.launch(
        config, "0002",
        command=[sys.executable, str(ROOT / "RSIFlow/launch_meta.py"),
                 "--config", str(config), "--run-dir", str(run)],
        timeout="10s", kill_after="2s", skip_asset_gates=True,
        mode="resume", resumed_from="0001", prelaunch_evidence=prelaunch)
    attempt = run / "meta_session/supervision/0002"
    with pytest.raises(RuntimeError, match="old/starting state"):
        supervisor.collect_post_launch_identity(config, "0002", timeout=0.1)
    supervisor.record_post_launch_pending(config, "0002", reason="old disconnected state")

    def settled():
        if not (attempt / "exit").is_file() or not (attempt / "reconcile.json").is_file():
            return False
        reconcile = json.loads((attempt / "reconcile.json").read_text())
        return reconcile.get("status") == "complete" and reconcile.get("remaining") == []

    assert _wait(settled), "missing-key supervision did not reconcile"
    assert (attempt / "exit").read_text().strip() == "1"
    log = (attempt / "launcher.log").read_text()
    assert "in _autodl_key" in log
    assert "RuntimeError: OPENAI_API_KEY is unset and no compatible provider key was found" in log
    assert not (attempt / "post-launch-identity.json").exists()
    assert not (attempt / "launch-final.json").exists()
    return config, run, paths, gpu_obs, previous, attempt


def _prestate_consumer(config, run, paths, receipt_path):
    summarizer, cfg, active = _cleanup_consumer_args(config, run, paths)
    receipt = json.loads(receipt_path.read_text())
    sup = summarizer._supervision(run)
    summarizer._validate_cleanup(receipt, receipt_path, run, cfg, paths["output_root"], sup, active)


def test_prestate_missing_key_cleanup_consumer_and_next_resume(
        tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, gpu_obs, _, _ = _seed_prestate_missing_key(
        tmp_path, supervisor, reconciler, monkeypatch)
    receipt_path = reconciler.cleanup_run(config, run, "0002", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "completed", receipt["blockers"]
    assert receipt["prestate_launch_evidence"]["missing_provider_key"] == "OPENAI_API_KEY"
    assert receipt["owners"]["launcher"]["state"] == "exited"
    assert receipt["owners"]["app_server"]["state"] == "not_created"
    assert receipt["owners"]["app_server"]["identity"] is None
    _prestate_consumer(config, run, paths, receipt_path)
    result = supervisor.launch(config, "0003", command=_sleep_command(0.2),
                               timeout="10s", kill_after="2s", skip_asset_gates=True,
                               mode="resume", resumed_from="0002")
    assert result["launch_seal"]["binding"]["resumed_from"] == "0002"
    assert _wait(lambda: (run / "meta_session/supervision/0003/exit").is_file())


@pytest.mark.parametrize("mutation", [
    "state", "new_job", "new_turn", "wrong_error", "empty_log", "fake_app_server",
    "previous_cleanup", "host",
])
def test_prestate_missing_key_fail_closed(
        tmp_path, supervisor, reconciler, monkeypatch, mutation):
    config, run, paths, gpu_obs, previous, attempt = _seed_prestate_missing_key(
        tmp_path, supervisor, reconciler, monkeypatch)
    if mutation == "state":
        state_path = run / "meta_session/state.json"
        state = json.loads(state_path.read_text())
        state["last_turn_status"] = "changed-after-previous-cleanup"
        state_path.write_text(json.dumps(state))
    elif mutation == "new_job":
        import shutil
        shutil.copytree(run / "meta_session/jobs/run_parent_1",
                        run / "meta_session/jobs/run_parent_2")
    elif mutation == "new_turn":
        _add_turn(run, "0001", "job_status", "read")
    elif mutation == "wrong_error":
        (attempt / "launcher.log").write_text("RuntimeError: some other launcher failure\n")
    elif mutation == "empty_log":
        (attempt / "launcher.log").write_text("")
    elif mutation == "fake_app_server":
        (attempt / "post-launch-identity.json").write_text(json.dumps({
            "kind": "rsiflow_post_launch_identity", "owners": {"app_server": {"pid": 123}}}))
    elif mutation == "previous_cleanup":
        value = json.loads(previous.read_text())
        value["status"] = "pending"
        previous.write_text(json.dumps(value))
    elif mutation == "host":
        prelaunch_path = attempt / "prelaunch-seal.json"
        prelaunch = json.loads(prelaunch_path.read_text())
        prelaunch["host_identity"]["host"] = "different-host"
        prelaunch_path.write_text(json.dumps(prelaunch))
        launch_path = attempt / "launch.json"
        launch = json.loads(launch_path.read_text())
        launch["prelaunch_hash"] = supervisor._sha256(prelaunch_path)
        launch_path.write_text(json.dumps(launch))
    receipt_path = reconciler.cleanup_run(config, run, "0002", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed", receipt
    assert receipt["registry"]["phase"] != "not_created_verified"


def test_prestate_consumer_rejects_changed_state_and_new_turn(
        tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, gpu_obs, _, _ = _seed_prestate_missing_key(
        tmp_path, supervisor, reconciler, monkeypatch)
    receipt_path = reconciler.cleanup_run(config, run, "0002", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    assert json.loads(receipt_path.read_text())["status"] == "completed"
    state_path = run / "meta_session/state.json"
    original = state_path.read_bytes()
    state = json.loads(original)
    state["last_turn_status"] = "tampered"
    state_path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="prestate"):
        _prestate_consumer(config, run, paths, receipt_path)
    state_path.write_bytes(original)
    _add_turn(run, "0001", "job_status", "read")
    with pytest.raises(ValueError, match="prestate|evidence_hash"):
        _prestate_consumer(config, run, paths, receipt_path)


def test_prestate_observe_resume_preflight_uses_same_consumer(
        tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, gpu_obs, _, _ = _seed_prestate_missing_key(
        tmp_path, supervisor, reconciler, monkeypatch)
    cleanup = reconciler.cleanup_run(config, run, "0002", gpu_observer=_gpu_observer(gpu_obs),
                                     skip_asset_gates=True)
    assert json.loads(cleanup.read_text())["status"] == "completed"
    canonical = importlib.import_module("reconcile_deepcoder_run")
    monkeypatch.setattr(canonical, "_default_gpu_observer", lambda: gpu_obs)
    monkeypatch.setattr(canonical, "_default_ports_observer",
                        lambda: {"status": "closed", "closed_ports": [8271, 8272, 8273, 8274]})
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
    provider = tmp_path / "prestate-provider.json"
    provider.write_text(json.dumps({
        "kind": "rsiflow_provider_probe", "http_status": 200,
        "response": {"status": "completed", "model": "gpt-6-astra", "output": ["READY"]},
        "endpoint": "http://token.acerobotics.com/v1/responses",
        "config_hash": supervisor._sha256(config), "host_identity": supervisor.host_identity(),
        "observed_at": time.time()}))
    output = tmp_path / "prestate-resume-observation.json"
    value = supervisor.observe_resume_preflight(config, provider, cleanup, output)
    assert value["cleanup_receipt"]["sha256"] == supervisor._sha256(cleanup)
    entry = {"path": str(output), "sha256": supervisor._sha256(output)}
    verified = supervisor._verify_resume_receipt(
        entry, expected_backend="linux_bwrap_seccomp_v1", run_dir=run, config_path=config)
    assert verified["stage"] == "resume" and verified["status"] == "success"


# --- independent review: durable artifacts, complete turn baseline, canonical traceback ---


def _relink_prestate_previous(previous, attempt, supervisor):
    prelaunch_path = attempt / "prelaunch-seal.json"
    prelaunch = json.loads(prelaunch_path.read_text())
    resume_path = Path(prelaunch["preflight_receipts"]["resume"]["path"])
    resume = json.loads(resume_path.read_text())
    resume["cleanup_receipt"]["sha256"] = supervisor._sha256(previous)
    resume_path.write_text(json.dumps(resume))
    prelaunch["preflight_receipts"]["resume"]["sha256"] = supervisor._sha256(resume_path)
    prelaunch_path.write_text(json.dumps(prelaunch))
    launch_path = attempt / "launch.json"
    launch = json.loads(launch_path.read_text())
    launch["prelaunch_hash"] = supervisor._sha256(prelaunch_path)
    launch_path.write_text(json.dumps(launch))


def _mutate_prestate_job_artifact(run, artifact):
    job = json.loads((run / "meta_session/jobs/run_parent_1/job.json").read_text())
    result_path = Path(job["result_path"])
    path = result_path if artifact == "result" else result_path.parent / "request.json"
    value = json.loads(path.read_text())
    value["independent_review_mutation"] = True
    path.write_text(json.dumps(value))


@pytest.mark.parametrize("mutation", [
    "job_result", "job_request", "backdated_new_turn", "mtime_preserved_turn",
    "mixed_error", "ambiguous_traceback", "missing_turn_baseline",
])
def test_prestate_review_producer_fail_closed(
        tmp_path, supervisor, reconciler, monkeypatch, mutation):
    config, run, paths, gpu_obs, previous, attempt = _seed_prestate_missing_key(
        tmp_path, supervisor, reconciler, monkeypatch)
    if mutation in {"job_result", "job_request"}:
        _mutate_prestate_job_artifact(run, "result" if mutation == "job_result" else "request")
    elif mutation == "backdated_new_turn":
        _add_turn(run, "0001", "job_status", "read")
        old = previous.stat().st_mtime_ns - 1
        turn = run / "meta_session/turns/0001"
        for path in (turn / "command.json", turn / "receipt.json", turn):
            os.utime(path, ns=(old, old))
    elif mutation == "mtime_preserved_turn":
        path = run / "meta_session/turns/0000/receipt.json"
        stat = path.stat()
        value = json.loads(path.read_text())
        value["independent_review_mutation"] = True
        path.write_text(json.dumps(value))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    elif mutation == "mixed_error":
        path = attempt / "launcher.log"
        path.write_text("RuntimeError: an earlier unrelated error\n" + path.read_text())
    elif mutation == "ambiguous_traceback":
        path = attempt / "launcher.log"
        lines = path.read_text().splitlines()
        lines[-1:-1] = ['  File "/tmp/unexpected.py", line 1, in unexpected',
                        '    raise ValueError("ambiguous")']
        path.write_text("\n".join(lines) + "\n")
    elif mutation == "missing_turn_baseline":
        value = json.loads(previous.read_text())
        value.get("protocol", {}).pop("turn_inventory", None)
        previous.write_text(json.dumps(value))
        _relink_prestate_previous(previous, attempt, supervisor)
    receipt_path = reconciler.cleanup_run(config, run, "0002", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed", receipt


@pytest.mark.parametrize("artifact", ["result", "request"])
def test_prestate_review_consumer_rejects_job_artifact_mutation(
        tmp_path, supervisor, reconciler, monkeypatch, artifact):
    config, run, paths, gpu_obs, _, _ = _seed_prestate_missing_key(
        tmp_path, supervisor, reconciler, monkeypatch)
    receipt_path = reconciler.cleanup_run(config, run, "0002", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    assert json.loads(receipt_path.read_text())["status"] == "completed"
    _mutate_prestate_job_artifact(run, artifact)
    with pytest.raises(ValueError, match="prestate|job_terminal_binding|evidence_hash"):
        _prestate_consumer(config, run, paths, receipt_path)


# --- legacy previous cleanup: anchored causal proof + historical supervision prefix --------


LEGACY_JOB_TURNS = (18, 35, 37, 42, 45, 49, 52, 61, 65)


def _legacy_initial_history(run, config):
    first = json.loads((run / "meta_session/jobs/run_parent_1/job.json").read_text())
    for index in range(75):
        turn = run / f"meta_session/turns/{index:04d}"
        turn.mkdir(parents=True, exist_ok=True)
        request = {"operation": "job_status", "round_number": 1}
        (turn / "command.json").write_text(json.dumps({"action": "tool", "request": request}))
        (turn / "receipt.json").write_text(json.dumps({
            "operation": "job_status", "status": "read", "jobs": []}))
    for index, turn_number in enumerate(LEGACY_JOB_TURNS):
        job_id = f"run_parent_{index + 1}"
        turn = run / f"meta_session/turns/{turn_number:04d}"
        transport = run / f"controller_transport/{job_id}"
        job_dir = run / f"meta_session/jobs/{job_id}"
        transport.mkdir(parents=True, exist_ok=True)
        job_dir.mkdir(parents=True, exist_ok=True)
        request = {"operation": "run_parent", "round_number": 1,
                   "output_dir": str(run / "round_1/parent")}
        result = {"operation": "run_parent", "status": "completed", "returncode": 0,
                  "output_dir": str(run / "round_1/parent")}
        (turn / "command.json").write_text(json.dumps({"action": "tool", "request": request}))
        (turn / "receipt.json").write_text(json.dumps({
            "operation": "run_parent", "status": "running", "job_id": job_id,
            "result_path": str(transport / "result.json")}))
        (transport / "request.json").write_text(json.dumps(request))
        (transport / "result.json").write_text(json.dumps(result))
        job = {**first, "job_id": job_id, "request": request, "pid": 40001 + index,
               "result_path": str(transport / "result.json"), "turn_dir": str(turn)}
        (job_dir / "job.json").write_text(json.dumps(job))
    _legacy_complete_b1_history(run, config)


def _legacy_complete_b1_history(run, config):
    config_value = json.loads(config.read_text())
    (run / "meta_session/workflow_policy.json").write_text(json.dumps({
        "rounds": 5, "experiment_scope": config_value["experiment_scope"],
        "retry_rejected_from_round": config_value["retry_rejected_from_round"],
        "evaluation_schedule": config_value["evaluation_schedule"]}))
    attempt = run / "round_1/attempts/attempt_1"
    attempt.mkdir(parents=True, exist_ok=True)
    evidence = attempt / "meta_evidence.json"
    candidate = attempt / "candidate/candidate_state.json"
    performance = attempt / "candidate/performance.json"
    for path in (evidence, candidate, performance):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}")
    (attempt / "decision.json").write_text("{}")
    selection = attempt / "selection.json"
    selection.write_text(json.dumps({"accept_or_retain": "accept_candidate"}))
    skills = run / "meta/skills.jsonl"
    context = run / "meta/context.json"
    skills.parent.mkdir(parents=True, exist_ok=True)
    skills.write_text("")
    context.write_text("{}")
    snapshot = run / "snapshots/round_1_attempt_1"
    snapshot.mkdir(parents=True, exist_ok=True)
    (run / "snapshots/A0").mkdir(parents=True, exist_ok=True)
    (run / "round_1/parent").mkdir(parents=True, exist_ok=True)

    def emit(index, operation, request=None, receipt=None, round_number=1):
        turn = run / f"meta_session/turns/{index:04d}"
        request = {"operation": operation, "round_number": round_number, **(request or {})}
        receipt = {"operation": operation, "status": "completed", **(receipt or {})}
        (turn / "command.json").write_text(json.dumps({"action": "tool", "request": request}))
        (turn / "receipt.json").write_text(json.dumps(receipt))

    emit(0, "bootstrap", {"state_path": str(run / "active_task.json")},
         {"state_path": str(run / "active_task.json")}, round_number=0)
    emit(1, "snapshot_task_meta", {"destination": str(run / "snapshots/A0")},
         {"status": "snapshotted", "destination": str(run / "snapshots/A0")}, round_number=0)
    emit(2, "run_parent", {"output_dir": str(run / "round_1/parent")},
         {"output_dir": str(run / "round_1/parent")})
    emit(9, "prepare_meta_evidence", {"path": str(evidence)}, {"path": str(evidence)})
    emit(10, "write_text", {"path": str(attempt / "decision.json")},
         {"status": "written", "path": str(attempt / "decision.json")})
    emit(11, "artifacts", {"candidate_state_path": str(candidate)},
         {"candidate_state_path": str(candidate)})
    emit(12, "run_candidate", {"performance_path": str(performance)},
         {"performance_path": str(performance)})
    emit(13, "compare_scores", {"before": str(performance), "after": str(performance)},
         {"status": "compared", "before": str(performance), "after": str(performance),
          "comparison_path": str(performance)})
    emit(14, "write_text", {"path": str(selection), "content": selection.read_text()},
         {"status": "written", "path": str(selection)})
    emit(15, "append_skills", {"path": str(skills)},
         {"status": "appended", "appended_count": 1, "path": str(skills)})
    emit(16, "write_text", {"path": str(context)}, {"status": "written", "path": str(context)})
    emit(17, "snapshot_task_meta", {"destination": str(snapshot), "context_path": str(context)},
         {"status": "snapshotted", "destination": str(snapshot),
          "context_reference": {"source": str(context), "sha256": "context"}})
    from experiment_progress import ExperimentProgress
    workflow = ExperimentProgress(run / "meta_session", rounds=5).rebuild()
    assert workflow["current_stage"] == "B2" and workflow["observed_turns"] == 75


def _legacy_keep_only_job_turn_snapshots(receipt):
    keep = {f"/meta_session/turns/{turn:04d}/" for turn in LEGACY_JOB_TURNS}
    for raw, reference in list(receipt["source_snapshots"].items()):
        if "/meta_session/turns/" not in raw or any(marker in raw for marker in keep):
            continue
        receipt["source_snapshots"].pop(raw)
        receipt["evidence_files_sha256"].pop(reference["path"], None)


def _launch_additional_missing_key(config, run, previous, supervision, supervisor, monkeypatch,
                                   *, reviewed_commit="a" * 40):
    attempt_number = supervision
    common = {"config_path": str(config.resolve()), "config_hash": supervisor._sha256(config),
              "run_dir": str(run.resolve()), "supervision_attempt": attempt_number}
    expected_gpus = [{"gpu": g, "uuid": f"GPU-{g}"} for g in range(4)]
    resume_receipt = run / f"meta_session/supervision/{attempt_number}-resume-preflight.json"
    resume_receipt.write_text(json.dumps({
        "stage": "resume", "status": "success", **common,
        "host": supervisor.host_identity()["host"],
        "boot_id": supervisor.host_identity()["boot_id"],
        "pid_namespace": supervisor.host_identity()["pid_namespace"],
        "backend": "linux_bwrap_seccomp_v1", "provider": "gpt-6-astra",
        "ports": [8271, 8272, 8273, 8274], "gpus": expected_gpus,
        "cleanup_receipt": {"path": str(previous), "sha256": supervisor._sha256(previous)}}))
    prelaunch = {
        "kind": "rsiflow_prelaunch_seal", "reviewed_commit": reviewed_commit,
        "backend": "linux_bwrap_seccomp_v1", "binding": {**common, "launch_mode": "resume"},
        "host_identity": supervisor.host_identity(), "expected_gpus": expected_gpus,
        "preflight_receipts": {"resume": {"path": str(resume_receipt),
                                          "sha256": supervisor._sha256(resume_receipt)}}}
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    prior = f"{int(attempt_number) - 1:04d}"
    supervisor.launch(
        config, attempt_number,
        command=[sys.executable, str(ROOT / "RSIFlow/launch_meta.py"),
                 "--config", str(config), "--run-dir", str(run)],
        timeout="10s", kill_after="2s", skip_asset_gates=True,
        mode="resume", resumed_from=prior, prelaunch_evidence=prelaunch)
    attempt = run / "meta_session/supervision" / attempt_number
    with pytest.raises(RuntimeError, match="old/starting state"):
        supervisor.collect_post_launch_identity(config, attempt_number, timeout=0.1)
    supervisor.record_post_launch_pending(config, attempt_number, reason="old disconnected state")

    def settled():
        if not (attempt / "exit").is_file() or not (attempt / "reconcile.json").is_file():
            return False
        reconcile = json.loads((attempt / "reconcile.json").read_text())
        return reconcile.get("status") == "complete" and reconcile.get("remaining") == []

    assert _wait(settled)
    assert (attempt / "exit").read_text().strip() == "1"
    return attempt


def test_prestate_legacy_a0_to_b2_real_schema_cleanup(
        tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, gpu_obs, previous, _ = _seed_prestate_missing_key(
        tmp_path, supervisor, reconciler, monkeypatch, legacy=True)
    old = json.loads(previous.read_text())
    assert "turn_inventory" not in old["protocol"]
    assert old["protocol"]["current_stage"] == "A0"
    anchored = {p for p in old["source_snapshots"] if "/meta_session/turns/" in p}
    assert len(anchored) == 18
    assert {Path(p).parent.name for p in anchored} == {f"{n:04d}" for n in LEGACY_JOB_TURNS}
    receipt_path = reconciler.cleanup_run(config, run, "0002", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "completed", receipt["blockers"]
    proof = receipt["prestate_launch_evidence"]
    assert len(proof["turns"]) == 75
    assert proof["legacy_reconstruction"]["previous_stage"] == "A0"
    assert proof["legacy_reconstruction"]["current_stage"] == "B2"
    _prestate_consumer(config, run, paths, receipt_path)


def test_prestate_consecutive_attempts_use_historical_prefix(
        tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, gpu_obs, _, _ = _seed_prestate_missing_key(
        tmp_path, supervisor, reconciler, monkeypatch)
    previous = reconciler.cleanup_run(config, run, "0002", gpu_observer=_gpu_observer(gpu_obs),
                                      skip_asset_gates=True)
    assert json.loads(previous.read_text())["status"] == "completed"
    _launch_additional_missing_key(config, run, previous, "0003", supervisor, monkeypatch)
    receipt_path = reconciler.cleanup_run(config, run, "0003", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "completed", receipt["blockers"]
    _prestate_consumer(config, run, paths, receipt_path)


@pytest.mark.parametrize("mutation", ["stage", "anchor_turn"])
def test_prestate_legacy_rejects_unvalidated_history(
        tmp_path, supervisor, reconciler, monkeypatch, mutation):
    config, run, paths, gpu_obs, _, _ = _seed_prestate_missing_key(
        tmp_path, supervisor, reconciler, monkeypatch, legacy=True)
    if mutation == "stage":
        path = run / "meta_session/workflow_state.json"
        value = json.loads(path.read_text())
        value["current_stage"] = "B3"
        path.write_text(json.dumps(value))
    else:
        path = run / "meta_session/turns/0018/receipt.json"
        value = json.loads(path.read_text())
        value["unsupported_anchor_change"] = True
        path.write_text(json.dumps(value))
    receipt_path = reconciler.cleanup_run(config, run, "0002", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    assert json.loads(receipt_path.read_text())["status"] != "completed"


def test_prestate_legacy_consecutive_attempts_recompute_historical_proof(
        tmp_path, supervisor, reconciler, monkeypatch):
    config, run, paths, gpu_obs, _, _ = _seed_prestate_missing_key(
        tmp_path, supervisor, reconciler, monkeypatch, legacy=True)
    previous = reconciler.cleanup_run(config, run, "0002", gpu_observer=_gpu_observer(gpu_obs),
                                      skip_asset_gates=True)
    assert json.loads(previous.read_text())["status"] == "completed"
    _launch_additional_missing_key(
        config, run, previous, "0003", supervisor, monkeypatch,
        reviewed_commit="beebd9b42b734c0f5a1dd6ba927931ee76b81f43")
    receipt_path = reconciler.cleanup_run(config, run, "0003", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] == "completed", receipt["blockers"]
    assert receipt["prestate_launch_evidence"]["legacy_reconstruction"] is None
    _prestate_consumer(config, run, paths, receipt_path)


@pytest.mark.parametrize("mutation", ["missing_anchor", "extra_anchor"])
def test_prestate_legacy_requires_exact_previous_job_turn_snapshot_set(
        tmp_path, supervisor, reconciler, monkeypatch, mutation):
    config, run, paths, gpu_obs, previous, attempt = _seed_prestate_missing_key(
        tmp_path, supervisor, reconciler, monkeypatch, legacy=True)
    value = json.loads(previous.read_text())
    if mutation == "missing_anchor":
        raw = str(run / "meta_session/turns/0018/command.json")
        reference = value["source_snapshots"].pop(raw)
        value["evidence_files_sha256"].pop(reference["path"], None)
    else:
        raw_path = run / "meta_session/turns/0019/command.json"
        captured = previous.parent / "sources/extra-anchor-command.json"
        captured.write_bytes(raw_path.read_bytes())
        digest = supervisor._sha256(captured)
        value["source_snapshots"][str(raw_path)] = {"path": str(captured), "sha256": digest}
        value["evidence_files_sha256"][str(captured)] = digest
    previous.write_text(json.dumps(value))
    _relink_prestate_previous(previous, attempt, supervisor)
    receipt_path = reconciler.cleanup_run(config, run, "0002", gpu_observer=_gpu_observer(gpu_obs),
                                          skip_asset_gates=True)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["status"] != "completed", receipt
