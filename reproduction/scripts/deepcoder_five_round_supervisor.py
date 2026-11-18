"""Parameterized five-round launcher supervision (launch / status / stop).

Paths are naturally derived from the machine config (output_root/runs/run_name); an
arbitrary ``--run-dir`` override is refused. Launch uses ``Popen(start_new_session=True)``
with a parent-child handshake (the child atomically writes its own identity before
running the foreground command), avoiding the nohup/exec identity race. The child
reuses the verified ``timeout --foreground --signal=TERM --kill-after`` algorithm and a
bounded, identity-rechecked PGID TERM/KILL reconcile; it never pkills or kills by port.

This module is offline-testable with short temporary commands and no GPU/API. It does
not stop durable jobs, GPU services, or the bwrap Meta app-server (separate sessions);
those are reconciled by ``reconcile_deepcoder_run.py``. Real launch additionally seals
reviewed HEAD/config/base/release/bwrap and runs the two-stage dev4 preflight; the
``skip_asset_gates`` flag is for offline temporary-process tests only.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_TIMEOUT = "4h"
DEFAULT_KILL_AFTER = "5m"


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _atomic_write(path: Path, value: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _sha256(path):
    import hashlib
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def derive_paths(config_path) -> dict:
    config_path = Path(config_path).resolve()
    config = _read_json(config_path)
    if not isinstance(config, dict):
        raise ValueError("config unreadable")
    output_root = Path(config["output_root"]).resolve()
    run_dir = (output_root / "runs" / config["run_name"]).resolve()
    if not run_dir.is_relative_to(output_root):
        raise ValueError("run_dir not a descendant of output_root")
    return {
        "config_path": config_path,
        "config": config,
        "output_root": output_root,
        "run_dir": run_dir,
        "central": Path(config.get("central_meta_root", str(output_root.parent.parent / "Meta_logs" / config["run_name"]))),
        "registry": output_root / "services" / "four_gpu_services.json",
        "release": Path(config["frozen_data_dir"]).resolve(),
        "ready": Path(config["frozen_data_dir"]).resolve().parent / "READY.json",
    }


def _proc_stat(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()


def _validate_identity(identity: dict, pid: int, run_dir: Path, attempt_dir: Path):
    if identity.get("pid") != pid or identity.get("pgid") != pid:
        raise RuntimeError("supervisor PID/PGID mismatch")
    if identity.get("run_dir") != str(run_dir) or identity.get("attempt_dir") != str(attempt_dir):
        raise RuntimeError("supervisor identity path mismatch")
    if not str(identity.get("start_ticks", "")).isdigit():
        raise RuntimeError("supervisor start_ticks invalid")
    try:
        stat = _proc_stat(pid)
    except FileNotFoundError:
        raise RuntimeError("supervisor process not readable")
    if int(stat[2]) != pid or stat[19] != identity["start_ticks"]:
        raise RuntimeError("supervisor live identity mismatch (PID reuse?)")
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    if str(run_dir).encode() not in cmdline or str(attempt_dir).encode() not in cmdline:
        raise RuntimeError("supervisor argv does not bind run/attempt")


# The child wrapper: write identity, then WAIT for the parent ACK before executing the
# experiment (H2: nothing runs until the parent has validated identity/gates). On cancel
# or ACK timeout it writes a failed-start receipt and exits without running the launcher.
# After ACK it runs the foreground command under timeout, then bounded identity-rechecked
# PGID TERM/KILL reconcile, and writes exit. It ignores TERM so it can always reconcile.
_WRAPPER = r'''
import json, os, signal, subprocess, sys, time
from pathlib import Path
attempt_dir = Path(sys.argv[1]); run_dir = Path(sys.argv[2])
timeout = sys.argv[3]; kill_after = sys.argv[4]; ack_timeout = float(sys.argv[5]); command = sys.argv[6:]
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pid = os.getpid()
stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
identity = {"pid": pid, "pgid": os.getpgid(pid), "start_ticks": stat[19],
            "run_dir": str(run_dir), "attempt_dir": str(attempt_dir), "argv": sys.argv}
tmp = attempt_dir / "identity.json.tmp"
tmp.write_text(json.dumps(identity, indent=2) + "\n"); os.replace(tmp, attempt_dir / "identity.json")
# H2 handshake: block until the parent ACKs (validated) or cancels; never run before ACK.
deadline = time.monotonic() + ack_timeout
ack = False
while time.monotonic() < deadline:
    if (attempt_dir / "ack").is_file():
        ack = True; break
    if (attempt_dir / "cancel").is_file():
        break
    time.sleep(0.02)
if not ack:
    reason = "cancelled_by_parent" if (attempt_dir / "cancel").is_file() else "ack_timeout"
    failed = {"kind": "rsiflow_failed_start", "pid": pid, "pgid": os.getpgid(pid),
              "start_ticks": stat[19], "run_dir": str(run_dir), "attempt_dir": str(attempt_dir),
              "reason": reason, "experiment_executed": False,
              "failed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    tmp = attempt_dir / "failed-start.json.tmp"
    tmp.write_text(json.dumps(failed, indent=2) + "\n"); os.replace(tmp, attempt_dir / "failed-start.json")
    tmp = attempt_dir / "exit.tmp"; tmp.write_text("1\n"); os.replace(tmp, attempt_dir / "exit")
    tmp = attempt_dir / "reconcile.json.tmp"
    tmp.write_text(json.dumps({"status": "complete", "term_targets": [], "kill_targets": [], "remaining": []}) + "\n")
    os.replace(tmp, attempt_dir / "reconcile.json")
    raise SystemExit(1)
rc = subprocess.run(["timeout", "--foreground", "--signal=TERM", f"--kill-after={kill_after}", timeout, *command]).returncode
if rc < 0:
    rc = 128 - rc  # bash-style 128+signal (e.g. SIGTERM -> 143)
wrapper_pid = pid; helper_pid = os.getpid()
def members():
    out = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            s = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            p = int(entry.name)
            if int(s[2]) == wrapper_pid and s[0] != "Z" and p not in {wrapper_pid, helper_pid}:
                out.append({"pid": p, "start_ticks": s[19]})
        except (FileNotFoundError, PermissionError):
            pass
    return out
grace = 30.0
initial = members()
if initial:
    try:
        os.killpg(wrapper_pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
deadline = time.monotonic() + grace
while time.monotonic() < deadline and members():
    time.sleep(0.05)
killed = []
for item in members():
    try:
        s = Path(f"/proc/{item['pid']}/stat").read_text().rsplit(")", 1)[1].split()
        if int(s[2]) == wrapper_pid and s[19] == item["start_ticks"] and s[0] != "Z":
            os.kill(item["pid"], signal.SIGKILL); killed.append(item["pid"])
    except (FileNotFoundError, ProcessLookupError):
        pass
deadline = time.monotonic() + grace
while time.monotonic() < deadline and members():
    time.sleep(0.05)
remaining = members()
report = {"status": "complete" if not remaining else "failed",
          "term_targets": [i["pid"] for i in initial], "kill_targets": killed, "remaining": remaining}
tmp = attempt_dir / "reconcile.json.tmp"
tmp.write_text(json.dumps(report, indent=2) + "\n"); os.replace(tmp, attempt_dir / "reconcile.json")
tmp = attempt_dir / "exit.tmp"; tmp.write_text(f"{rc}\n"); os.replace(tmp, attempt_dir / "exit")
'''


def _validate_launch_gates(paths: dict, supervision: str, skip_asset_gates: bool, mode: str,
                           resumed_from=None):
    run_dir = paths["run_dir"]
    attempt_dir = run_dir / "meta_session/supervision" / supervision
    if attempt_dir.exists():
        raise RuntimeError(f"supervision attempt already exists: {attempt_dir}")
    if mode == "fresh":
        if not skip_asset_gates:
            for p in (paths["output_root"], run_dir, paths["central"]):
                if p.exists() or p.is_symlink():
                    raise RuntimeError(f"fresh launch path already exists: {p}")
    elif mode == "resume":
        # H7/R7: resume requires an existing run with all prior attempts accounted for;
        # a complete run is cleanup-only and must not be resumed.
        if not run_dir.is_dir():
            raise RuntimeError("resume requires an existing run")
        state = _read_json(run_dir / "meta_session/state.json") or {}
        if state.get("status") == "complete":
            raise RuntimeError("complete run is cleanup-only; resume refused")
        sup_root = run_dir / "meta_session/supervision"
        prior_attempts = sorted((d for d in sup_root.iterdir() if d.is_dir() and d.name.isdigit()),
                                key=lambda d: int(d.name)) if sup_root.is_dir() else []
        for prior in prior_attempts:
            reconcile = _read_json(prior / "reconcile.json") or {}
            exit_file = prior / "exit"
            if not (reconcile.get("status") == "complete" and not reconcile.get("remaining")
                    and exit_file.is_file()):
                raise RuntimeError(f"prior supervision {prior.name} not accounted; resume refused")
        # R7: resumed_from must be the latest prior attempt (not arbitrary/missing).
        if not prior_attempts:
            raise RuntimeError("resume requires a prior supervision attempt")
        latest_prior = prior_attempts[-1].name
        if resumed_from != latest_prior:
            raise RuntimeError(f"resumed_from must be latest prior attempt {latest_prior}; resume refused")
        # R7: product/identity continuity — active Task, thread, config hash, terminal jobs.
        if not (run_dir / "active_task.json").is_file():
            raise RuntimeError("resume requires an existing active Task; resume refused")
        if not state.get("thread_id"):
            raise RuntimeError("resume requires a prior thread_id; resume refused")
        prior_launch = _read_json(prior_attempts[-1] / "launch.json") or {}
        if prior_launch.get("binding", {}).get("config_hash") != _sha256(paths["config_path"]):
            raise RuntimeError("resume config hash differs from prior attempt; resume refused")
        if prior_launch.get("binding", {}).get("backend") != "linux_bwrap_seccomp_v1":
            raise RuntimeError("resume backend differs from prior attempt; resume refused")
        for jobpath in (run_dir / "meta_session/jobs").glob("*/job.json"):
            job = _read_json(jobpath) or {}
            result = _read_json(job.get("result_path"))
            if not isinstance(result, dict) or result.get("status") in (None, "running"):
                raise RuntimeError(f"resume blocked: job {jobpath.parent.name} has no terminal result")
    else:
        raise ValueError(f"unknown launch mode: {mode}")
    if not skip_asset_gates:
        if not paths["release"].is_dir():
            raise RuntimeError("frozen release missing")
        if not paths["ready"].is_file():
            raise RuntimeError("READY.json missing")
    return attempt_dir


def _normalize_effective(paths, run_dir_resolved):
    project_root = Path(__file__).resolve().parents[2]
    for entry in (project_root / "RSIFlow", project_root / "RSIFlow/runtime"):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))
    from task_adapter import TaskAdapter
    return TaskAdapter(project_root / "RSIFlow", run_dir_resolved / "controller_receipts")._config(
        {"config_path": str(paths["config_path"])})


def collect_post_launch_identity(config_path, supervision: str, *, timeout=30.0,
                                 gpu_observer=None, expected_thread=None) -> dict:
    """R1/R2: one-time collection of the REAL production launcher/app-server/thread identity
    and GPU UUIDs after launch. Verifies the state is THIS attempt's new state (not an old
    disconnected/starting state), the launcher belongs to the current supervisor PGID and
    argv/run, and resume thread equals the prior value. Writes an immutable post-launch
    attachment referencing the prelaunch seal hash; never rewrites sealed history and
    refuses re-collection. On failure the caller records actual-started/identity-pending."""
    paths = derive_paths(config_path)
    run_dir = paths["run_dir"]
    attempt_dir = run_dir / "meta_session/supervision" / supervision
    attachment_path = attempt_dir / "post-launch-identity.json"
    if attachment_path.exists():
        raise RuntimeError("post-launch identity already collected; refusing to overwrite sealed history")
    seal = _read_json(attempt_dir / "launch.json") or {}
    prelaunch_hash = _sha256(attempt_dir / "launch.json")
    supervisor_identity = seal.get("supervisor") or {}
    supervisor_pgid = supervisor_identity.get("pgid")
    deadline = time.monotonic() + timeout
    state = {}
    while time.monotonic() < deadline:
        state = _read_json(run_dir / "meta_session/state.json") or {}
        # R1: require a NEW current-attempt state, not an old disconnected/starting one.
        if (state.get("launcher_pid") and state.get("codex_pid") and state.get("thread_id")
                and state.get("status") in ("running", "complete")):
            break
        time.sleep(0.1)
    if not (state.get("launcher_pid") and state.get("codex_pid") and state.get("thread_id")
            and state.get("status") in ("running", "complete")):
        raise RuntimeError("current-attempt production state not available (old/starting state refused)")
    if expected_thread is not None and state.get("thread_id") != expected_thread:
        raise RuntimeError("resume thread mismatch; post-launch identity refused")

    def proc_ident(pid):
        stat = _proc_stat(pid)
        return {"pid": pid, "pgid": int(stat[2]), "start_ticks": stat[19]}

    launcher = proc_ident(state["launcher_pid"])
    app_server = proc_ident(state["codex_pid"])
    # R1: launcher must belong to the current supervisor PGID and bind this run via argv.
    if supervisor_pgid is not None and launcher["pgid"] != supervisor_pgid:
        raise RuntimeError("launcher PGID does not belong to current supervisor; identity refused")
    try:
        launcher_argv = Path(f"/proc/{state['launcher_pid']}/cmdline").read_bytes()
        if str(run_dir).encode() not in launcher_argv:
            raise RuntimeError("launcher argv does not bind this run; identity refused")
    except FileNotFoundError:
        raise RuntimeError("launcher process not readable; identity refused")
    # R1: real GPU UUIDs (injected offline; real nvidia-smi in production).
    gpu = (gpu_observer or _default_gpu_observer)() if gpu_observer else None
    expected_gpus = gpu.get("expected_uuids") if gpu else seal.get("expected_gpus")
    attachment = {
        "kind": "rsiflow_post_launch_identity", "supervision_attempt": supervision,
        "run_dir": str(run_dir), "config_path": str(paths["config_path"]),
        "prelaunch_seal_path": str(attempt_dir / "launch.json"),
        "prelaunch_seal_hash": prelaunch_hash,
        "thread_id": state["thread_id"], "state_status": state.get("status"),
        "owners": {"launcher": launcher, "app_server": app_server},
        "expected_gpus": expected_gpus,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _atomic_write(attachment_path, attachment)
    return attachment


def _default_gpu_observer():
    import subprocess
    cmd = ["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader,nounits"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    uuids = [line.split(",")[1].strip() for line in result.stdout.strip().splitlines() if line.strip()]
    return {"expected_uuids": uuids}


def record_post_launch_pending(config_path, supervision: str, *, reason: str) -> dict:
    """R2: after ACK the experiment IS running; a collector failure must not be recorded as
    experiment_executed=False. Record actual-started/identity-pending with the supervisor
    identity and ongoing custody responsibility (controlled stop still reconciles owners)."""
    paths = derive_paths(config_path)
    attempt_dir = paths["run_dir"] / "meta_session/supervision" / supervision
    seal = _read_json(attempt_dir / "launch.json") or {}
    record = {
        "kind": "rsiflow_actual_started_identity_pending", "supervision_attempt": supervision,
        "experiment_executed": True, "identity_pending": True, "reason": reason,
        "supervisor": seal.get("supervisor"),
        "custody": "ongoing; controlled stop must reconcile durable jobs/app-server separately",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _atomic_write(attempt_dir / "identity-pending.json", record)
    return record


def verify_prelaunch_seal(prelaunch_evidence, *, expected_commit, expected_backend, attempt_dir):
    """R2: verify the complete prelaunch asset/preflight seal BEFORE Popen. Missing/wrong
    reviewed commit, any of the five manifests/payload/READY, seed Harness/model weights/
    Codex/bwrap install+manifest+launcher, Task5 MODEL evidence, two-stage preflight
    receipt paths+hashes, or actual backend => raise (experiment command never executes).
    Writes an immutable prelaunch-seal.json; refuses overwrite of sealed history."""
    seal_path = Path(attempt_dir) / "prelaunch-seal.json"
    if seal_path.exists():
        raise RuntimeError("prelaunch seal already exists; refusing to overwrite sealed history")
    if not isinstance(prelaunch_evidence, dict):
        raise RuntimeError("prelaunch evidence missing; experiment not executed")
    if prelaunch_evidence.get("reviewed_commit") != expected_commit:
        raise RuntimeError("prelaunch reviewed commit mismatch; experiment not executed")
    if prelaunch_evidence.get("backend") != expected_backend:
        raise RuntimeError("prelaunch backend mismatch; experiment not executed")
    required_assets = prelaunch_evidence.get("required_assets") or []
    assets = prelaunch_evidence.get("assets") or {}
    for name in required_assets:
        entry = assets.get(name)
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("sha256"):
            raise RuntimeError(f"prelaunch asset {name} missing; experiment not executed")
        if not Path(entry["path"]).is_file():
            raise RuntimeError(f"prelaunch asset {name} path absent; experiment not executed")
        if _sha256(entry["path"]) != entry["sha256"]:
            raise RuntimeError(f"prelaunch asset {name} hash mismatch; experiment not executed")
    receipts = prelaunch_evidence.get("preflight_receipts") or {}
    for stage in ("stage1", "stage2"):
        entry = receipts.get(stage)
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("sha256"):
            raise RuntimeError(f"prelaunch {stage} receipt missing; experiment not executed")
        if not Path(entry["path"]).is_file():
            raise RuntimeError(f"prelaunch {stage} receipt path absent; experiment not executed")
        if _sha256(entry["path"]) != entry["sha256"]:
            raise RuntimeError(f"prelaunch {stage} receipt hash mismatch; experiment not executed")
    # stage2 (post-occupier) must reference the sealed stage1 receipt.
    stage2_value = _read_json(receipts["stage2"]["path"]) or {}
    if stage2_value.get("stage1_receipt_hash") != receipts["stage1"]["sha256"]:
        raise RuntimeError("prelaunch stage2 does not reference stage1; experiment not executed")
    seal = {"kind": "rsiflow_prelaunch_seal", "reviewed_commit": expected_commit,
            "backend": expected_backend, "required_assets": required_assets,
            "assets": assets, "preflight_receipts": receipts,
            "sealed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    _atomic_write(seal_path, seal)
    return seal


def launch(config_path, supervision: str, *, command, timeout=DEFAULT_TIMEOUT,
           kill_after=DEFAULT_KILL_AFTER, run_dir=None, skip_asset_gates=False,
           handshake_timeout=30.0, thread_id="five-round-thread", expected_gpus=None,
           mode="fresh", resumed_from=None, prelaunch_evidence=None, expected_commit=None) -> dict:
    if run_dir is not None:
        raise ValueError("arbitrary run_dir override is refused; paths derive from config")
    paths = derive_paths(config_path)
    # H2: ALL static normalization / asset gates happen BEFORE Popen.
    attempt_dir = _validate_launch_gates(paths, supervision, skip_asset_gates, mode, resumed_from)
    config = paths["config"]
    project_root = Path(__file__).resolve().parents[2]
    base = (project_root / "RSIFlow" / config.get("runtime_source", "runtime") / config["base_config"]).resolve()
    run_dir_resolved = paths["run_dir"].resolve()
    effective_path = attempt_dir.parent / supervision / "effective.json"
    effective_hash = None
    if not skip_asset_gates:
        effective_obj = _normalize_effective(paths, run_dir_resolved)
        attempt_dir.mkdir(parents=True)  # exclusive; refuses existing
        # R2: complete prelaunch asset/preflight seal verified BEFORE Popen.
        verify_prelaunch_seal(prelaunch_evidence, expected_commit=expected_commit,
                              expected_backend="linux_bwrap_seccomp_v1", attempt_dir=attempt_dir)
        _atomic_write(effective_path, effective_obj.model_dump())
        effective_hash = _sha256(effective_path)
    else:
        attempt_dir.mkdir(parents=True)
    (attempt_dir / "kind").write_text("detached\n")
    attempt_resolved = attempt_dir.resolve()
    binding = {
        "config_path": str(paths["config_path"]), "config_hash": _sha256(paths["config_path"]),
        "base_config_path": str(base), "base_config_hash": _sha256(base),
        "effective_config_path": str(effective_path), "effective_config_hash": effective_hash,
        "run_dir": str(run_dir_resolved), "output_root": str(paths["output_root"]),
        "supervision_attempt": supervision, "thread_id": thread_id,
        "backend": "linux_bwrap_seccomp_v1", "mode": mode,
    }
    if resumed_from:
        binding["resumed_from"] = resumed_from
    # Pre-Popen launch seal (owners filled with the supervision leader; real production
    # launcher/app-server identities are bound by collect_post_launch_identity after ACK).
    seal = {
        "kind": "rsiflow_launch", "binding": binding,
        "owners": {"launcher": None, "app_server": None},
        "expected_gpus": expected_gpus or [{"gpu": g, "uuid": f"GPU-{g}"} for g in range(4)],
        "timeout": timeout, "kill_after": kill_after,
    }
    _atomic_write(attempt_dir / "launch.json", seal)
    argv = [sys.executable, "-c", _WRAPPER, str(attempt_resolved), str(run_dir_resolved),
            timeout, kill_after, str(handshake_timeout), *command]
    proc = subprocess.Popen(argv, start_new_session=True,
                            stdin=subprocess.DEVNULL,
                            stdout=open(attempt_dir / "launcher.log", "ab"),
                            stderr=subprocess.STDOUT)
    # parent-child handshake: wait for the child's atomic identity, validate, then ACK.
    deadline = time.monotonic() + handshake_timeout
    identity = None
    while time.monotonic() < deadline:
        identity = _read_json(attempt_dir / "identity.json")
        if identity:
            break
        if proc.poll() is not None:
            raise RuntimeError("supervisor child exited before identity handshake")
        time.sleep(0.05)
    try:
        if not identity:
            raise RuntimeError("supervisor identity handshake timed out")
        _validate_identity(identity, proc.pid, run_dir_resolved, attempt_resolved)
    except Exception:
        # H2: contain the child — it is waiting for ACK and has NOT run the experiment.
        _atomic_write(attempt_dir / "cancel", {"reason": "parent_validation_failed"})
        try:
            proc.wait(timeout=handshake_timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
        raise
    # ACK: only now may the child execute the experiment.
    launcher_identity = {"pid": identity["pid"], "pgid": identity["pgid"], "start_ticks": identity["start_ticks"]}
    seal["owners"] = {"launcher": launcher_identity, "app_server": dict(launcher_identity)}
    seal["supervisor"] = launcher_identity
    _atomic_write(attempt_dir / "launch.json", seal)
    _atomic_write(attempt_dir / "ack", {"acked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return {"identity": identity, "attempt_dir": str(attempt_resolved), "launch_seal": seal}


def status(config_path, supervision: str) -> dict:
    paths = derive_paths(config_path)
    attempt_dir = paths["run_dir"] / "meta_session/supervision" / supervision
    identity = _read_json(attempt_dir / "identity.json") or {}
    reconcile = _read_json(attempt_dir / "reconcile.json") or {}
    exit_value = None
    try:
        exit_value = (attempt_dir / "exit").read_text().strip()
    except OSError:
        pass
    alive = False
    if identity.get("pid"):
        try:
            stat = _proc_stat(identity["pid"])
            alive = stat[19] == identity.get("start_ticks") and stat[0] != "Z"
        except (FileNotFoundError, PermissionError):
            alive = False
    state = _read_json(paths["run_dir"] / "meta_session/state.json") or {}
    return {"attempt": supervision, "identity": identity, "exit": exit_value,
            "reconcile_status": reconcile.get("status"), "alive": alive,
            "protocol_status": state.get("status"), "stop_requested": (attempt_dir / "stop-requested").is_file()}


def stop(config_path, supervision: str, *, grace=90.0) -> dict:
    paths = derive_paths(config_path)
    run_dir = paths["run_dir"].resolve()
    attempt_dir = (run_dir / "meta_session/supervision" / supervision).resolve()
    identity = _read_json(attempt_dir / "identity.json")
    if not identity:
        raise RuntimeError("no supervision identity; stop refused")
    _validate_identity(identity, identity["pid"], run_dir, attempt_dir)
    pid = identity["pid"]
    tmp = attempt_dir / "stop-requested.tmp"
    tmp.write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + "\n")
    os.replace(tmp, attempt_dir / "stop-requested")
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        reconcile = _read_json(attempt_dir / "reconcile.json")
        if (attempt_dir / "exit").is_file() and reconcile and reconcile.get("status") == "complete" \
                and not reconcile.get("remaining"):
            return {"exit": (attempt_dir / "exit").read_text().strip(), "reconcile": reconcile}
        time.sleep(0.1)
    raise RuntimeError("launcher PGID/exit/reconcile not complete; cleanup/resume remain blocked")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("launch", "status", "stop"):
        p = sub.add_parser(name)
        p.add_argument("--config", required=True)
        p.add_argument("--supervision", required=True)
        if name == "launch":
            p.add_argument("--timeout", default=DEFAULT_TIMEOUT)
            p.add_argument("--kill-after", default=DEFAULT_KILL_AFTER)
            p.add_argument("--launch-mode", choices=["fresh", "resume"], default="fresh")
            p.add_argument("--resumed-from", default=None)
            p.add_argument("--preflight-seal", required=True,
                           help="R2: path to the prelaunch asset/preflight evidence JSON")
            # H2: the real CLI exposes NO asset-gate bypass; offline tests call launch()
            # directly with skip_asset_gates=True under controlled temporary processes.
        if name == "stop":
            p.add_argument("--grace", type=float, default=90.0)
    args = parser.parse_args(argv)
    if args.mode == "launch":
        root = Path(args.config).resolve().parents[2]
        command = [sys.executable, "-u", str(root / "RSIFlow_4B/launch.py"),
                   "--config", str(Path(args.config).resolve()),
                   "--run-dir", str(derive_paths(args.config)["run_dir"])]
        prelaunch_evidence = _read_json(args.preflight_seal)
        reviewed = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                                  capture_output=True, text=True, check=True).stdout.strip()
        result = launch(args.config, args.supervision, command=command, timeout=args.timeout,
                        kill_after=args.kill_after, skip_asset_gates=False,
                        mode=args.launch_mode, resumed_from=args.resumed_from,
                        prelaunch_evidence=prelaunch_evidence, expected_commit=reviewed)
        # R1/R2: bind real production launcher/app-server/thread + GPU UUIDs after launch.
        # On collector failure the experiment IS running (post-ACK): record identity-pending
        # with ongoing custody, never a silent失联 or experiment_executed=False.
        try:
            result["post_launch_identity"] = collect_post_launch_identity(
                args.config, args.supervision, gpu_observer=_default_gpu_observer,
                expected_thread=args.resumed_from and result["launch_seal"]["binding"].get("thread_id"))
        except Exception as exc:
            result["identity_pending"] = record_post_launch_pending(
                args.config, args.supervision, reason=f"{type(exc).__name__}:{exc}")
        print(json.dumps(result, indent=2))
    elif args.mode == "status":
        print(json.dumps(status(args.config, args.supervision), indent=2))
    elif args.mode == "stop":
        print(json.dumps(stop(args.config, args.supervision, grace=args.grace), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())