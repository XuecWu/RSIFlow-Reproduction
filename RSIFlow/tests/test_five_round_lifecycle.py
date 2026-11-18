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
def supervisor():
    return _load("deepcoder_five_round_supervisor")


@pytest.fixture()
def reconciler():
    return _load("reconcile_deepcoder_run")


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
                               timeout="10s", kill_after="2s", skip_asset_gates=True)
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
    launcher_pid = launch["owners"]["launcher"]["pid"]
    app_pid = launch["owners"]["app_server"]["pid"]
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
    gpu_obs = {
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": ["nvidia-smi", "--query"], "command_result": 0,
        "expected_uuids": [f"GPU-{g}" for g in range(4)],
        "per_gpu": [{"gpu": g, "uuid": f"GPU-{g}", "memory_free": 80000, "memory_total": 81920} for g in range(4)],
        "compute_apps": compute_apps if compute_apps is not None else [], "free_verified": True,
        "stdout_hash": "offline-injected-observation",
    }
    return config, run, paths, gpu_obs, {"launcher_pid": launcher_pid, "app_pid": app_pid, "job_pid": job_pid}


def _gpu_observer(gpu_obs):
    def observe():
        return dict(gpu_obs)
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
                                   stop_services_fn=lambda cfg: None, skip_asset_gates=True)
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
    # spawn distinct real launcher/app-server processes and record production state
    launcher = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        (run / "meta_session/state.json").write_text(json.dumps({
            "status": "running", "launcher_pid": launcher.pid, "codex_pid": app.pid, "thread_id": "real-thread-xyz"}))
        attachment = supervisor.collect_post_launch_identity(config, "0001", timeout=10)
        assert attachment["thread_id"] == "real-thread-xyz"
        assert attachment["owners"]["launcher"]["pid"] == launcher.pid
        assert attachment["owners"]["app_server"]["pid"] == app.pid
        assert attachment["owners"]["launcher"]["pid"] != attachment["owners"]["app_server"]["pid"]
        seal = json.loads((run / "meta_session/supervision/0001/launch.json").read_text())
        assert seal["binding"]["thread_id"] == "real-thread-xyz"
        assert seal["owners"]["launcher"]["pid"] == launcher.pid
        assert seal["post_launch_identity_hash"] == supervisor._sha256(
            run / "meta_session/supervision/0001/post-launch-identity.json")
    finally:
        launcher.terminate(); launcher.wait(timeout=10)
        app.terminate(); app.wait(timeout=10)


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


def test_resume_existing_incomplete_run(tmp_path, supervisor):
    output_root = tmp_path / "out"
    config = _config(tmp_path, output_root, "run")
    supervisor.launch(config, "0001", command=_sleep_command(0.2), timeout="10s",
                      kill_after="2s", skip_asset_gates=True)
    paths = supervisor.derive_paths(config)
    run = paths["run_dir"]
    assert _wait(lambda: (run / "meta_session/supervision/0001/exit").is_file())
    (run / "meta_session/state.json").write_text(json.dumps({"status": "disconnected", "thread_id": "t1"}))
    result = supervisor.launch(config, "0002", command=_sleep_command(0.2), timeout="10s",
                               kill_after="2s", skip_asset_gates=True, mode="resume", resumed_from="0001")
    seal = result["launch_seal"]
    assert seal["binding"]["mode"] == "resume"
    assert seal["binding"]["resumed_from"] == "0001"
    assert (run / "meta_session/supervision/0002").is_dir()


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
    obs_path = reconciler.restore_occupier(config, run, "0001", occupier_start_fn=fake_start,
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
                                        occupier_start_fn=lambda: started.append(1) or {},
                                        gpu_observer=_gpu_observer(gpu_obs))
        assert started == []  # never started occupier on pending cleanup
    finally:
        proc.terminate(); proc.wait(timeout=10)


def test_restore_occupier_refused_without_cleanup(tmp_path, supervisor, reconciler):
    config, run, paths, gpu_obs, pids = _seed_run(tmp_path, supervisor, reconciler)
    started = []
    with pytest.raises(RuntimeError, match="no external cleanup"):
        reconciler.restore_occupier(config, run, "0001",
                                    occupier_start_fn=lambda: started.append(1) or {},
                                    gpu_observer=_gpu_observer(gpu_obs))
    assert started == []