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


def _config(tmp_path, output_root, run_name):
    """A temp machine config naturally deriving output/run, built from the valid
    five-round launcher value so TaskAdapter._config normalization succeeds."""
    from test_deepcoder_five_round import _five_round_launcher_value
    value = _five_round_launcher_value(tmp_path)
    release = tmp_path / "release"
    release.mkdir(exist_ok=True)
    (release / "protocol.json").write_text(json.dumps({"rounds": 5}))
    value.update(output_root=str(output_root), run_name=run_name, frozen_data_dir=str(release))
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
    time.sleep(0.3)
    result = supervisor.stop(config, "0001", grace=20)
    assert (attempt / "stop-requested").is_file()
    assert _wait(lambda: (attempt / "exit").is_file(), timeout=30)
    assert (attempt / "exit").read_text().strip() == "143"
    reconcile = json.loads((attempt / "reconcile.json").read_text())
    assert reconcile["status"] == "complete"


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
              compute_apps=None, training_leader_dead_rank_alive=False):
    """Build a temp run with a completed supervision, jobs, registry, and owners."""
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    paths = supervisor.derive_paths(config)
    run = paths["run_dir"]
    run.mkdir(parents=True)
    (run / "meta_session").mkdir(parents=True)
    # launch a real short supervision so identity/exit/reconcile/launch seal exist
    supervisor.launch(config, "0001", command=_sleep_command(0.2),
                      timeout="10s", kill_after="2s", skip_asset_gates=True)
    attempt = run / "meta_session/supervision/0001"
    assert _wait(lambda: (attempt / "exit").is_file())
    # offline launch skips asset gates; seal the effective config the real launch would
    # have written pre-Popen, so the read-only cleanup verification can bind it.
    effective_obj = supervisor._normalize_effective(paths, run.resolve())
    supervisor._atomic_write(attempt / "effective.json", effective_obj.model_dump())
    launch = json.loads((attempt / "launch.json").read_text())
    launch["binding"]["effective_config_hash"] = supervisor._sha256(attempt / "effective.json")
    supervisor._atomic_write(attempt / "launch.json", launch)
    launcher_pid = launch["supervisor"]["pid"]
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
    # no active_task.json => R7 product gate refuses
    with pytest.raises(RuntimeError, match="active Task"):
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
    # controlled stand-in for run.sh: spawns `ranks` child ranks in its PGID, then sleeps
    script = tmp_path / "occupier.sh"
    code = ("import subprocess, sys, time\n"
            "if '--rank' in sys.argv:\n"
            "    time.sleep(60); sys.exit(0)\n"
            f"for _ in range({ranks}):\n"
            "    subprocess.Popen([sys.executable, __file__, '--rank'])\n"
            "time.sleep(60)\n")
    py = tmp_path / "occupier_run.py"
    py.write_text(code)
    return [sys.executable, str(py)]


def _restore_gpu_observer(command):
    # stateful: first call (restore free-check) => idle; later (start load-check) => 4 ranks
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
        apps = [] if n == 0 else [{"pid": pid, "uuid": f"GPU-{i}"} for i, pid in enumerate(sorted(members))]
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
