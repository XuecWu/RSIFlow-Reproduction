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
GLOBAL_GPU_LIFECYCLE_LOCK = Path("/tmp/rsiflow-gpu-lifecycle.lock")

# F2: the required prelaunch asset set is fixed by code, never shrunk by caller input.
REQUIRED_PRELAUNCH_ASSETS = (
    "manifest_B1", "manifest_B2", "manifest_B3", "manifest_B4", "manifest_B5",
    "payload", "READY", "seed_harness", "model_weights", "codex_binary",
    "bwrap_install", "bwrap_manifest", "bwrap_launcher", "task5_model_evidence",
)
# F2: required field-receipt evidence keys (success + run/config/host/namespace/GPU/
# provider/ports/occupier binding), verified beyond a mere file hash.
REQUIRED_STAGE_FIELDS = ("status", "run_dir", "config_hash", "host", "boot_id",
                         "pid_namespace", "gpus", "provider", "ports")


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
        with Path(path).open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()
    except OSError:
        return None


def host_identity():
    """Identify the observing host AND namespaces, without calling a GPU/API."""
    return {"host": os.uname().nodename,
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "pid_namespace": os.readlink("/proc/self/ns/pid"),
            "mount_namespace": os.readlink("/proc/self/ns/mnt")}


def write_once(path, value):
    """Publish evidence without replacing an existing inode, including symlinks."""
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".evidence-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_identity(identity):
    if (not isinstance(identity, dict) or type(identity.get("pid")) is not int
            or identity["pid"] <= 0 or type(identity.get("pgid")) is not int
            or identity["pgid"] <= 0 or not str(identity.get("start_ticks", "")).isdigit()):
        raise ValueError("owner_identity_invalid")
    return identity


def validate_gpu_inventory(gpus):
    if (not isinstance(gpus, list) or len(gpus) != 4
            or any(not isinstance(g, dict) for g in gpus)
            or {g.get("gpu") for g in gpus} != {0, 1, 2, 3}
            or any(not isinstance(g.get("uuid"), str) or not g["uuid"] for g in gpus)
            or len({g["uuid"] for g in gpus}) != 4):
        raise ValueError("gpu_inventory_invalid")
    return sorted(gpus, key=lambda g: g["gpu"])


def load_launch_chain(attempt, config_path, run, *, observer_host=None, read_json=None, digest=None):
    """Read the final -> runtime -> launch -> prelaunch DAG; no optional fallback.

    read_json/digest allow the offline consumer to read immutable source snapshots.
    Neither hook changes the schema or any required verification.
    """
    attempt, config_path, run = Path(attempt), Path(config_path).resolve(), Path(run).resolve()
    read_json, digest = read_json or _read_json, digest or _sha256
    def read(name, kind):
        path = attempt / name
        value = read_json(path)
        if path.is_symlink() or not isinstance(value, dict) or value.get("kind") != kind:
            raise ValueError("launch_chain_missing_or_invalid:" + name)
        return value
    final = read("launch-final.json", "rsiflow_launch_final")
    runtime = read("post-launch-identity.json", "rsiflow_post_launch_identity")
    launch = read("launch.json", "rsiflow_launch")
    prelaunch = read("prelaunch-seal.json", "rsiflow_prelaunch_seal")
    for name, expected in (("post-launch-identity.json", final.get("runtime_hash")),
                           ("launch.json", runtime.get("launch_hash")),
                           ("prelaunch-seal.json", launch.get("prelaunch_hash"))):
        if not expected or digest(attempt / name) != expected:
            raise ValueError("launch_chain_hash:" + name)
    binding = launch.get("binding") or {}
    for value in (final, runtime, binding, prelaunch.get("binding") or {}):
        if (value.get("run_dir") != str(run) or value.get("supervision_attempt") != attempt.name
                or value.get("config_path") != str(config_path)
                or value.get("config_hash") != digest(config_path)):
            raise ValueError("launch_chain_binding")
    host = prelaunch.get("host_identity")
    if not isinstance(host, dict) or any(not host.get(k) for k in ("host", "boot_id", "pid_namespace", "mount_namespace")):
        raise ValueError("launch_host_missing")
    if runtime.get("host_identity") != host or (observer_host is not None and observer_host != host):
        raise ValueError("launch_host_mismatch")
    if binding.get("backend") != "linux_bwrap_seccomp_v1":
        raise ValueError("launch_backend")
    thread = runtime.get("thread_id")
    if not isinstance(thread, str) or not thread or thread == "five-round-thread":
        raise ValueError("runtime_thread_missing")
    if binding.get("mode") == "resume" and binding.get("thread_id") != thread:
        raise ValueError("resume_thread_mismatch")
    owners = runtime.get("owners") or {}
    for key in ("launcher", "app_server"):
        validate_identity(owners.get(key))
    if owners["launcher"]["pid"] == owners["app_server"]["pid"]:
        raise ValueError("runtime_owners_not_distinct")
    gpu_inventory = validate_gpu_inventory(runtime.get("expected_gpus"))
    if gpu_inventory != validate_gpu_inventory(prelaunch.get("expected_gpus")):
        raise ValueError("runtime_gpu_binding")
    if not launch.get("supervisor") or owners["launcher"]["pgid"] != launch["supervisor"].get("pgid"):
        raise ValueError("runtime_launcher_group")
    return {"binding": {**binding, "thread_id": thread}, "owners": owners,
            "expected_gpus": gpu_inventory, "host_identity": host,
            "paths": [attempt / n for n in ("launch-final.json", "post-launch-identity.json", "launch.json", "prelaunch-seal.json")]}


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
timeout = sys.argv[3]; kill_after = sys.argv[4]; ack_timeout = float(sys.argv[5])
lifecycle_fd = int(sys.argv[6]) if sys.argv[6] != "-1" else None
command = sys.argv[7:]
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
rc = subprocess.run(["timeout", "--foreground", "--signal=TERM", f"--kill-after={kill_after}", timeout, *command],
                    pass_fds=() if lifecycle_fd is None else (lifecycle_fd,)).returncode
if rc < 0:
    rc = 128 - rc  # bash-style 128+signal (e.g. SIGTERM -> 143)
wrapper_pid = pid; helper_pid = os.getpid()
def members():
    out = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        p = int(entry.name)
        if p in {wrapper_pid, helper_pid}:
            continue
        try:
            s = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(s[2]) == wrapper_pid and s[0] != "Z":
                out.append({"pid": p, "start_ticks": s[19]})
        except FileNotFoundError:
            pass
        except (PermissionError, ValueError, IndexError, OSError):
            # F3: unreadable/unparseable member => cannot prove exit; fail closed.
            out.append({"pid": p, "start_ticks": None, "unreadable": True})
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
        # F7: unsealed/dead training phase => requires_audit BEFORE Popen (experiment
        # command never executes). A completed training product must be sealed.
        for phase in run_dir.glob("round_*/attempts/attempt_*/**/gpu_phase_receipt.json"):
            value = _read_json(phase) or {}
            if value.get("status") not in ("completed", "training_complete"):
                raise RuntimeError(f"resume requires_audit: training phase {phase.parent.name} unsealed")
            if not (phase.parent / "checkpoint_trained.json").is_file():
                raise RuntimeError(f"resume requires_audit: training phase {phase.parent.name} no trained checkpoint")
        from reconcile_deepcoder_run import _observe_owner
        from summarize_deepcoder_five_round import _jobs_validation, _validate_phase_owner
        prior_chain = load_launch_chain(prior_attempts[-1], paths["config_path"], run_dir,
                                        observer_host=host_identity())
        if state["thread_id"] != prior_chain["binding"]["thread_id"]:
            raise RuntimeError("resume thread differs from sealed runtime")
        for owner in prior_chain["owners"].values():
            if _observe_owner(owner, run_dir)["state"] != "exited":
                raise RuntimeError("resume owner not exited")
        job_check = _jobs_validation(run_dir)
        if job_check["invalid"] or job_check["missing_final_receipts"]:
            raise RuntimeError("resume job binding requires_audit")
        for jobpath in (run_dir / "meta_session/jobs").glob("*/job.json"):
            job = _read_json(jobpath)
            owner = {"pid": job["pid"], "pgid": job["pid"], "start_ticks": str(job["process_start"])}
            if _observe_owner(owner, run_dir)["state"] != "exited":
                raise RuntimeError("resume job still active/unknown")
        for phase in run_dir.glob("round_*/attempts/attempt_*/**/gpu_phase_receipt.json"):
            owner = _validate_phase_owner(_read_json(phase), phase, run_dir)
            if _observe_owner(owner, run_dir)["state"] != "exited":
                raise RuntimeError("resume training group still active/unknown")
        # F1: seal the prior thread before Popen so the new state is verified against it
        # (no placeholder fallback). The resumed launch binds the real prior thread_id.
        prior_thread = state.get("thread_id")
        thread_seal_path = prior_attempts[-1] / "resumed-thread-seal.json"
        if not thread_seal_path.exists():
            _atomic_write(thread_seal_path, {"kind": "rsiflow_resumed_thread_seal",
                                             "thread_id": prior_thread, "supervision_attempt": resumed_from,
                                             "sealed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
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
    app_argv = Path(f"/proc/{state['codex_pid']}/cmdline").read_bytes()
    if str(run_dir).encode() not in app_argv:
        raise RuntimeError("app-server argv does not bind run")
    ancestor = state["codex_pid"]
    for _ in range(32):
        if ancestor == state["launcher_pid"]:
            break
        ancestor = int(_proc_stat(ancestor)[1])
        if ancestor <= 1:
            raise RuntimeError("app-server is not a launcher descendant")
    else:
        raise RuntimeError("app-server ancestry unproven")
    # R1: real GPU UUIDs (injected offline; real nvidia-smi in production).
    gpu = (gpu_observer or _default_gpu_observer)()
    expected_gpus = validate_gpu_inventory(gpu.get("gpus"))
    attachment = {
        "kind": "rsiflow_post_launch_identity", "supervision_attempt": supervision,
        "run_dir": str(run_dir), "config_path": str(paths["config_path"]),
        "launch_hash": prelaunch_hash, "config_hash": _sha256(paths["config_path"]),
        "host_identity": host_identity(),
        "thread_id": state["thread_id"], "state_status": state.get("status"),
        "owners": {"launcher": launcher, "app_server": app_server},
        "expected_gpus": expected_gpus,
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    validate_identity(launcher)
    validate_identity(app_server)
    write_once(attachment_path, attachment)
    final = {"kind": "rsiflow_launch_final", "run_dir": str(run_dir),
             "supervision_attempt": supervision, "config_path": str(paths["config_path"]),
             "config_hash": _sha256(paths["config_path"]), "runtime_hash": _sha256(attachment_path)}
    final_path = attempt_dir / "launch-final.json"
    load_launch_chain(attempt_dir, paths["config_path"], run_dir, observer_host=host_identity(),
                      read_json=lambda p: final if Path(p) == final_path else _read_json(p))
    write_once(final_path, final)
    return attachment


def _default_gpu_observer():
    import subprocess
    cmd = ["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader,nounits"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    gpus = [{"gpu": int(line.split(",")[0]), "uuid": line.split(",")[1].strip()}
            for line in result.stdout.splitlines() if line.strip()]
    return {"gpus": validate_gpu_inventory(gpus)}


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


def _field_evidence(value, key):
    entry = value.get(key) or {}
    source = Path(entry.get("path", "/missing"))
    if source.is_symlink() or not entry.get("sha256") or _sha256(source) != entry["sha256"]:
        raise RuntimeError("field evidence missing/changed: " + key)
    payload = _read_json(source)
    if not isinstance(payload, dict):
        raise RuntimeError("field evidence invalid: " + key)
    return payload


def _validate_stage_measurements(value, *, require_idle):
    from summarize_deepcoder_five_round import validate_gpu_measurement
    probe = _field_evidence(value, "provider_response")
    provider = probe.get("response") or {}
    if (probe.get("kind") != "rsiflow_provider_probe" or probe.get("http_status") != 200
            or probe.get("endpoint") != "http://token.acerobotics.com/v1/responses"
            or probe.get("config_hash") != value.get("config_hash")
            or probe.get("host_identity") != host_identity()
            or not isinstance(probe.get("observed_at"), (float, int))
            or not 0 <= time.time() - probe["observed_at"] <= 1800
            or provider.get("status") != "completed" or provider.get("model") != "gpt-6-astra"
            or not provider.get("output")):
        raise RuntimeError("provider completion evidence invalid")
    gpu = _field_evidence(value, "gpu_observation")
    validate_gpu_measurement(gpu, value["gpus"], require_idle=require_idle)
    ports = _field_evidence(value, "ports_observation")
    if ports.get("status") != "closed" or ports.get("closed_ports") != value["ports"]:
        raise RuntimeError("field port observation binding")


def validate_field_receipts(stages, receipts, config_path, run_dir):
    evidence = _field_evidence
    for stage in ("stage1", "stage2"):
        _validate_stage_measurements(stages[stage], require_idle=stage == "stage2")
    owner = evidence(stages["stage1"], "occupier_identity")
    from reconcile_deepcoder_run import _validate_topology
    _validate_topology(owner, complete=True)
    if owner.get("owner") != "dev4-rsiflow-idle-occupier" or owner.get("gpus") != [0, 1, 2, 3]:
        raise RuntimeError("occupier identity invalid")
    stop = evidence(stages["stage2"], "occupier_stop")
    if (stop.get("kind") != "rsiflow_occupier_handoff" or stop.get("identity") != owner
            or stop.get("stage1_hash") != receipts["stage1"]["sha256"]
            or stop.get("host_identity") != host_identity()
            or stop.get("after", {}).get("state") != "exited"
            or stop.get("after", {}).get("group_members") != []
            or stop.get("after", {}).get("read_errors") != []):
        raise RuntimeError("occupier handoff evidence invalid")
    expected = [owner, *owner["rank_members"]]
    observed = stop["after"].get("identities") or []
    if (len(observed) != len(expected) or any(o.get("alive") is not False for o in observed)
            or [{k: o["identity"][k] for k in ("pid", "pgid", "start_ticks")} for o in observed]
            != [{k: i[k] for k in ("pid", "pgid", "start_ticks")} for i in expected]):
        raise RuntimeError("handoff missing per-identity terminal evidence")
    gpu = evidence(stages["stage2"], "gpu_observation")
    if gpu.get("compute_apps") != [] or gpu.get("free_verified") is not True:
        raise RuntimeError("handoff GPU still occupied")


def probe_provider(config_path, output):
    """Operator-only fresh HTTP probe. Credentials never enter the evidence file."""
    from urllib.request import Request, urlopen
    config_path = Path(config_path).resolve()
    config = _normalize_effective(derive_paths(config_path), derive_paths(config_path)["run_dir"])
    meta = config.meta
    if meta.model != "gpt-6-astra" or meta.base_url.rstrip("/") != "http://token.acerobotics.com/v1":
        raise RuntimeError("unregistered provider/model")
    key = os.environ[meta.api_key_env]
    endpoint = meta.base_url.rstrip("/") + "/responses"
    payload = json.dumps({"model": meta.model, "input": "Reply with READY.", "max_output_tokens": 128}).encode()
    request = Request(endpoint, data=payload, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urlopen(request, timeout=120) as response:
        status, body = response.status, json.loads(response.read())
    value = {"kind": "rsiflow_provider_probe", "http_status": status, "response": body,
             "endpoint": endpoint, "config_hash": _sha256(config_path),
             "host_identity": host_identity(), "observed_at": time.time()}
    if status != 200 or body.get("status") != "completed" or body.get("model") != meta.model or not body.get("output"):
        raise RuntimeError("provider probe not completed")
    write_once(output, value)
    return value


def observe_preflight(config_path, phase, provider_report, occupier_identity, output,
                      *, stage1=None, stop_receipt=None):
    import fcntl
    with GLOBAL_GPU_LIFECYCLE_LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _observe_preflight_locked(config_path, phase, provider_report, occupier_identity, output,
                                         stage1=stage1, stop_receipt=stop_receipt)


def _observe_preflight_locked(config_path, phase, provider_report, occupier_identity, output,
                              *, stage1=None, stop_receipt=None):
    """Executed by the operator on dev4, never by offline tests or the consumer."""
    from reconcile_deepcoder_run import _default_gpu_observer, _default_ports_observer, snapshot_occupier
    config_path, output = Path(config_path).resolve(), Path(output).resolve()
    paths = derive_paths(config_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    def ref(path):
        path = Path(path).resolve()
        return {"path": str(path), "sha256": _sha256(path)}
    gpu = _default_gpu_observer()
    ports = _default_ports_observer()
    gpu_path, ports_path = output.with_suffix(".gpu.json"), output.with_suffix(".ports.json")
    write_once(gpu_path, gpu); write_once(ports_path, ports)
    identity = _read_json(occupier_identity) or {}
    validate_identity(identity)
    if phase == "stage1":
        try:
            identity = snapshot_occupier(identity)
        except (OSError, ValueError, RuntimeError) as exc:
            write_once(output.with_suffix(".pending.json"), {
                "kind": "rsiflow_preflight_pending", "status": "pending", "error": str(exc),
                "leader": identity, "rank_members": getattr(exc, "ranks", []),
                "read_errors": getattr(exc, "errors", []), "host_identity": host_identity()})
            raise
    identity_snapshot = output.with_suffix(".occupier.json")
    write_once(identity_snapshot, identity)
    value = {"stage": phase, "status": "success", "run_dir": str(paths["run_dir"]),
             "config_path": str(config_path),
             "config_hash": _sha256(config_path), **host_identity(),
             "backend": os.environ.get("RSIFLOW_SANDBOX_BACKEND"), "gpus": [{"gpu": g["gpu"], "uuid": g["uuid"]} for g in gpu["per_gpu"]],
             "provider": "gpt-6-astra", "ports": paths["config"]["ports"],
             "provider_response": ref(provider_report), "gpu_observation": ref(gpu_path),
             "ports_observation": ref(ports_path), "occupier_identity": ref(identity_snapshot),
             "occupier_running": phase == "stage1", "occupier_stopped": phase == "stage2"}
    _validate_stage_measurements(value, require_idle=phase == "stage2")
    if phase == "stage2":
        if not stage1 or not stop_receipt:
            raise RuntimeError("stage2 requires actual stage1/handoff receipts")
        value.update(stage1_receipt_hash=_sha256(stage1), occupier_stop=ref(stop_receipt))
        validate_field_receipts({"stage1": _read_json(stage1), "stage2": value},
                                 {"stage1": ref(stage1)}, config_path, paths["run_dir"])
    write_once(output, value)
    return value


def handoff_occupier(stage1_path, output):
    import fcntl
    from reconcile_deepcoder_run import _observe_topology, _terminate_owned_group, _validate_topology
    with GLOBAL_GPU_LIFECYCLE_LOCK.open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stage1 = _read_json(stage1_path) or {}
        if any(stage1.get(k) != v for k, v in host_identity().items()):
            raise RuntimeError("handoff host binding")
        if stage1.get("status") != "success" or _sha256(stage1.get("config_path", "/missing")) != stage1.get("config_hash"):
            raise RuntimeError("handoff stage1/config invalid")
        _validate_stage_measurements(stage1, require_idle=False)
        ref = stage1.get("occupier_identity") or {}
        if not ref.get("sha256") or _sha256(ref.get("path")) != ref["sha256"]:
            raise RuntimeError("handoff identity hash")
        identity = _read_json(ref["path"])
        _validate_topology(identity, complete=True)
        if identity.get("owner") != "dev4-rsiflow-idle-occupier":
            raise RuntimeError("handoff owner identity")
        before = _observe_topology(identity)
        if before["state"] == "unknown":
            raise RuntimeError("handoff rank identity observation unknown")
        try:
            after = _terminate_owned_group(identity)
        except (OSError, RuntimeError, ValueError) as exc:
            write_once(Path(output).with_suffix(".pending.json"), {
                "kind": "rsiflow_occupier_handoff_pending", "identity": identity,
                "before": before, "after": _observe_topology(identity), "error": str(exc),
                "stage1_hash": _sha256(stage1_path), "host_identity": host_identity()})
            raise
        value = {"kind": "rsiflow_occupier_handoff", "stage1_hash": _sha256(stage1_path),
                 "identity": identity, "before": before, "after": after, "host_identity": host_identity()}
        write_once(output, value)
        return value


def trusted_prelaunch_assets(config_path):
    """Derive asset paths from launcher/runtime configuration and pinned provenance."""
    paths = derive_paths(config_path)
    root = Path(__file__).resolve().parents[2]
    project = root / "RSIFlow"
    config = paths["config"]
    base_path = (project / config.get("runtime_source", "runtime") / config["base_config"]).resolve()
    base = _read_json(base_path)
    effective = _normalize_effective(paths, paths["run_dir"])
    assets = {"config": paths["config_path"], "base": base_path, "READY": paths["ready"],
              "protocol": paths["release"] / "protocol.json",
              "seed_harness": project / base["seed_harness"],
              "codex_binary": Path(effective.meta.codex_executable),
              "codex_provenance": Path(effective.meta.provenance_file),
              "bwrap_install": root / "assets/isolation/install-receipt.json",
              "bwrap_binary": root / "assets/isolation/bin/bwrap",
              "bwrap_manifest": root / "RSIFlow/configs/bwrap_isolation.json",
              "bwrap_launcher": root / "RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py",
              "task5_model_evidence": root / ".superpowers/sdd/2026-11-18-deepcoder-five-round-canary/task-5-real-review-codex.md"}
    from rsiflow.task_meta.storage import checkpoint_manifest
    from rsiflow.task_meta.rsiflow_manifest import load_manifest
    from deepcoder_five_round_preflight import _validate_release
    _validate_release(config_path, paths["release"])
    load_manifest(assets["seed_harness"])
    for number in range(1, 6):
        assets[f"manifest_B{number}"] = paths["release"] / f"B{number}/manifest.json"
        assets[f"payload_B{number}"] = paths["release"] / f"B{number}/deepcoder_taco.jsonl"
    for item in checkpoint_manifest(effective.task_checkpoint):
        assets["weights:" + item["path"]] = Path(effective.task_checkpoint) / item["path"]
    provenance = _read_json(assets["codex_provenance"]) or {}
    if (provenance.get("commit") != effective.meta.codex_commit
            or provenance.get("binary_sha256") != effective.meta.codex_binary_sha256
            or _sha256(assets["codex_binary"]) != effective.meta.codex_binary_sha256):
        raise RuntimeError("Codex pinned provenance mismatch")
    install = _read_json(assets["bwrap_install"]) or {}
    for name, key in (("bwrap_binary", "destination_sha256"), ("bwrap_manifest", "manifest_sha256"), ("bwrap_launcher", "launcher_sha256")):
        if _sha256(assets[name]) != install.get(key):
            raise RuntimeError("bwrap pinned install mismatch")
    output = {}
    for name, source in assets.items():
        source = source.resolve()
        digest = _sha256(source)
        if not digest:
            raise RuntimeError("prelaunch asset missing: " + name)
        output[name] = {"path": str(source), "sha256": digest}
    return output


def verify_prelaunch_seal(prelaunch_evidence, *, expected_commit, expected_backend, attempt_dir,
                          run_dir=None, config_path=None):
    """R2/F2: verify the complete prelaunch asset/preflight seal BEFORE Popen. The required
    asset set is fixed by code (caller input cannot shrink it). Verifies reviewed commit,
    every required asset bytes/hash, two-stage field receipts (success + run/config/host/
    namespace/GPU/provider/ports/occupier binding, stage2 references stage1), and actual
    backend. Missing/wrong => raise (experiment command never executes). Writes an
    immutable prelaunch-seal.json; refuses overwrite of sealed history."""
    seal_path = Path(attempt_dir) / "prelaunch-seal.json"
    if seal_path.exists():
        raise RuntimeError("prelaunch seal already exists; refusing to overwrite sealed history")
    if not isinstance(prelaunch_evidence, dict):
        raise RuntimeError("prelaunch evidence missing; experiment not executed")
    if prelaunch_evidence.get("reviewed_commit") != expected_commit:
        raise RuntimeError("prelaunch reviewed commit mismatch; experiment not executed")
    if prelaunch_evidence.get("backend") != expected_backend:
        raise RuntimeError("prelaunch backend mismatch; experiment not executed")
    # F2: required assets fixed by code; input required_assets cannot remove any.
    assets = prelaunch_evidence.get("assets") or {}
    trusted = trusted_prelaunch_assets(config_path) if config_path is not None else None
    required = tuple(trusted) if trusted is not None else REQUIRED_PRELAUNCH_ASSETS
    for name in required:
        entry = assets.get(name)
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("sha256"):
            raise RuntimeError(f"prelaunch asset {name} missing; experiment not executed")
        if trusted is not None and entry != trusted[name]:
            raise RuntimeError(f"prelaunch asset {name} not the configured provenance")
        if not Path(entry["path"]).is_file():
            raise RuntimeError(f"prelaunch asset {name} path absent; experiment not executed")
        if _sha256(entry["path"]) != entry["sha256"]:
            raise RuntimeError(f"prelaunch asset {name} hash mismatch; experiment not executed")
    receipts = prelaunch_evidence.get("preflight_receipts") or {}
    stage_values = {}
    for stage in ("stage1", "stage2"):
        entry = receipts.get(stage)
        if not isinstance(entry, dict) or not entry.get("path") or not entry.get("sha256"):
            raise RuntimeError(f"prelaunch {stage} receipt missing; experiment not executed")
        if not Path(entry["path"]).is_file():
            raise RuntimeError(f"prelaunch {stage} receipt path absent; experiment not executed")
        if _sha256(entry["path"]) != entry["sha256"]:
            raise RuntimeError(f"prelaunch {stage} receipt hash mismatch; experiment not executed")
        value = _read_json(entry["path"]) or {}
        stage_values[stage] = value
        # F2: field receipt must be a successful, bound现场 snapshot, not just a hash.
        if value.get("status") != "success":
            raise RuntimeError(f"prelaunch {stage} status not success; experiment not executed")
        for field in REQUIRED_STAGE_FIELDS:
            if field not in value:
                raise RuntimeError(f"prelaunch {stage} missing field {field}; experiment not executed")
        if run_dir is not None and value.get("run_dir") != str(run_dir):
            raise RuntimeError(f"prelaunch {stage} run_dir mismatch; experiment not executed")
        if config_path is not None and value.get("config_hash") != _sha256(config_path):
            raise RuntimeError(f"prelaunch {stage} config_hash mismatch; experiment not executed")
        if (any(not isinstance(value.get(k), str) or not value[k].strip()
                for k in ("host", "boot_id", "pid_namespace"))
                or value.get("provider") != "gpt-6-astra"
                or value.get("ports") != [8271, 8272, 8273, 8274]):
            raise RuntimeError("prelaunch invalid host/provider/ports evidence")
        try:
            validate_gpu_inventory(value.get("gpus"))
        except ValueError as exc:
            raise RuntimeError("prelaunch invalid GPU evidence") from exc
        if value.get("backend") != expected_backend:
            raise RuntimeError(f"prelaunch {stage} backend mismatch; experiment not executed")
    # stage2 (post-occupier) must reference the sealed stage1 receipt and confirm handoff.
    if stage_values["stage2"].get("stage1_receipt_hash") != receipts["stage1"]["sha256"]:
        raise RuntimeError("prelaunch stage2 does not reference stage1; experiment not executed")
    if stage_values["stage2"].get("occupier_stopped") is not True:
        raise RuntimeError("prelaunch stage2 occupier not stopped; experiment not executed")
    if stage_values["stage1"].get("occupier_running") is not True:
        raise RuntimeError("prelaunch stage1 occupier not running; experiment not executed")
    for field in ("run_dir", "config_hash", "host", "boot_id", "pid_namespace", "gpus", "provider", "ports"):
        if stage_values["stage1"][field] != stage_values["stage2"][field]:
            raise RuntimeError("prelaunch stage continuity: " + field)
    if config_path is not None:
        validate_field_receipts(stage_values, receipts, config_path, run_dir)
        observed_host = host_identity()
        if any(stage_values["stage2"].get(k) != v for k, v in observed_host.items()):
            raise RuntimeError("prelaunch actual host/namespace mismatch")
        if os.environ.get("RSIFLOW_SANDBOX_BACKEND") != expected_backend:
            raise RuntimeError("prelaunch actual sandbox backend mismatch")
    seal = {"kind": "rsiflow_prelaunch_seal", "reviewed_commit": expected_commit,
            "backend": expected_backend, "required_assets": list(required),
            "assets": {name: assets[name] for name in required},
            "binding": {"config_path": str(config_path), "config_hash": _sha256(config_path) if config_path else None,
                        "run_dir": str(run_dir), "supervision_attempt": Path(attempt_dir).name},
            "host_identity": host_identity(), "expected_gpus": stage_values["stage2"]["gpus"],
            "preflight_receipts": receipts,
            "sealed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    write_once(seal_path, seal)
    return seal


def launch(config_path, supervision: str, **kwargs):
    """Serialize launch with handoff/cleanup/restore; wrapper inherits the lock."""
    import fcntl
    GLOBAL_GPU_LIFECYCLE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with GLOBAL_GPU_LIFECYCLE_LOCK.open("a+") as handle:
        deadline = time.monotonic() + 1.0
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                # exit/reconcile are durable before the wrapper closes its last fd.
                # A short bounded wait covers that ordering; no work runs unlocked.
                if time.monotonic() >= deadline:
                    raise RuntimeError("GPU lifecycle owner not accounted: still active") from exc
                time.sleep(0.02)
        # Closing this descriptor leaves the child's duplicate locked until it exits.
        return _launch_locked(config_path, supervision, _lock_fd=handle.fileno(), **kwargs)


def _launch_locked(config_path, supervision: str, *, command, timeout=DEFAULT_TIMEOUT,
           kill_after=DEFAULT_KILL_AFTER, run_dir=None, skip_asset_gates=False,
           handshake_timeout=30.0, thread_id=None, expected_gpus=None,
           mode="fresh", resumed_from=None, prelaunch_evidence=None, expected_commit=None, _lock_fd=None) -> dict:
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
                              expected_backend="linux_bwrap_seccomp_v1", attempt_dir=attempt_dir,
                              run_dir=run_dir_resolved, config_path=paths["config_path"])
        _atomic_write(effective_path, effective_obj.model_dump())
        effective_hash = _sha256(effective_path)
        # Handoff and launch can be separate invocations. Recheck the actual live
        # boundary while holding the launch lock, immediately before Popen.
        from reconcile_deepcoder_run import _default_gpu_observer as observe_gpu, _default_ports_observer
        from summarize_deepcoder_five_round import validate_gpu_measurement
        prelaunch = _read_json(attempt_dir / "prelaunch-seal.json")
        for sample in range(2):
            if sample:
                time.sleep(1.0)
            validate_gpu_measurement(observe_gpu(), prelaunch["expected_gpus"])
        if _default_ports_observer().get("status") != "closed":
            raise RuntimeError("launch ports occupied after handoff")
    else:
        attempt_dir.mkdir(parents=True)
        if prelaunch_evidence is not None:
            write_once(attempt_dir / "prelaunch-seal.json", prelaunch_evidence)
        effective_obj = _normalize_effective(paths, run_dir_resolved)
        write_once(effective_path, effective_obj.model_dump())
        effective_hash = _sha256(effective_path)
    (attempt_dir / "kind").write_text("detached\n")
    attempt_resolved = attempt_dir.resolve()
    # F1: resume binds the real prior thread (sealed before Popen), never a placeholder.
    effective_thread = thread_id
    if mode == "resume":
        prior_state = _read_json(run_dir_resolved / "meta_session/state.json") or {}
        effective_thread = prior_state.get("thread_id") or thread_id
    binding = {
        "config_path": str(paths["config_path"]), "config_hash": _sha256(paths["config_path"]),
        "base_config_path": str(base), "base_config_hash": _sha256(base),
        "effective_config_path": str(effective_path), "effective_config_hash": effective_hash,
        "run_dir": str(run_dir_resolved), "output_root": str(paths["output_root"]),
        "supervision_attempt": supervision, "thread_id": effective_thread,
        "backend": "linux_bwrap_seccomp_v1", "mode": mode,
    }
    if resumed_from:
        binding["resumed_from"] = resumed_from
        binding["prelaunch_seal_hash"] = _sha256(attempt_dir / "prelaunch-seal.json") if (attempt_dir / "prelaunch-seal.json").is_file() else None
    # Supervisor identity is separate from the actual launcher/app-server identities.
    # Only the runtime attachment supplies the latter; never publish placeholders.
    seal = {
        "kind": "rsiflow_launch", "binding": binding,
        "prelaunch_hash": _sha256(attempt_dir / "prelaunch-seal.json"),
        "expected_gpus": expected_gpus or [],
        "timeout": timeout, "kill_after": kill_after,
    }
    _atomic_write(attempt_dir / "launch.json", seal)
    argv = [sys.executable, "-c", _WRAPPER, str(attempt_resolved), str(run_dir_resolved),
            timeout, kill_after, str(handshake_timeout), str(_lock_fd if _lock_fd is not None else -1), *command]
    proc = subprocess.Popen(argv, start_new_session=True,
                            pass_fds=() if _lock_fd is None else (_lock_fd,),
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
        except FileNotFoundError:
            alive = False
        except (PermissionError, ValueError, IndexError, OSError):
            alive = None
    state = _read_json(paths["run_dir"] / "meta_session/state.json") or {}
    return {"attempt": supervision, "identity": identity, "exit": exit_value,
            "reconcile_status": reconcile.get("status"), "alive": alive,
            "owner_state": "unknown" if alive is None else "alive" if alive else "exited",
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
        # The wrapper ignores TERM and may not have spawned the timeout child yet when the
        # first TERM was sent (spawn race). Re-signal the owned PGID each poll so a late-born
        # child still receives TERM; this is a conditional wait on exit/reconcile, not a sleep.
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
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
    probe = sub.add_parser("probe-provider")
    probe.add_argument("--config", required=True)
    probe.add_argument("--output", required=True)
    observe = sub.add_parser("observe-preflight")
    observe.add_argument("--config", required=True)
    observe.add_argument("--phase", choices=["stage1", "stage2"], required=True)
    observe.add_argument("--provider-report", required=True)
    observe.add_argument("--occupier-identity", required=True)
    observe.add_argument("--stage1")
    observe.add_argument("--stop-receipt")
    observe.add_argument("--output", required=True)
    handoff = sub.add_parser("handoff")
    handoff.add_argument("--stage1", required=True)
    handoff.add_argument("--output", required=True)
    package = sub.add_parser("preflight-input")
    package.add_argument("--config", required=True)
    package.add_argument("--stage1", required=True)
    package.add_argument("--stage2", required=True)
    package.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.mode == "probe-provider":
        probe_provider(args.config, args.output)
        return 0
    if args.mode == "observe-preflight":
        observe_preflight(args.config, args.phase, args.provider_report, args.occupier_identity,
                          args.output, stage1=args.stage1, stop_receipt=args.stop_receipt)
        return 0
    if args.mode == "handoff":
        handoff_occupier(args.stage1, args.output)
        return 0
    if args.mode == "preflight-input":
        root = Path(__file__).resolve().parents[2]
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        write_once(args.output, {"reviewed_commit": commit, "backend": os.environ.get("RSIFLOW_SANDBOX_BACKEND"),
                   "assets": trusted_prelaunch_assets(args.config), "preflight_receipts": {
                       name: {"path": str(Path(path).resolve()), "sha256": _sha256(path)}
                       for name, path in (("stage1", args.stage1), ("stage2", args.stage2))}})
        return 0
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
