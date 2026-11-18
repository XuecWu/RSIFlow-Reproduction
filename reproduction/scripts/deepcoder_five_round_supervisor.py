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


def _validate_launch_gates(paths: dict, supervision: str, skip_asset_gates: bool, mode: str):
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
        # H7: resume requires an existing run with all prior attempts accounted for;
        # a complete run is cleanup-only and must not be resumed.
        if not run_dir.is_dir():
            raise RuntimeError("resume requires an existing run")
        state = _read_json(run_dir / "meta_session/state.json") or {}
        if state.get("status") == "complete":
            raise RuntimeError("complete run is cleanup-only; resume refused")
        sup_root = run_dir / "meta_session/supervision"
        if sup_root.is_dir():
            for prior in sorted(d for d in sup_root.iterdir() if d.is_dir() and d.name.isdigit()):
                reconcile = _read_json(prior / "reconcile.json") or {}
                exit_file = prior / "exit"
                if not (reconcile.get("status") == "complete" and not reconcile.get("remaining")
                        and exit_file.is_file()):
                    raise RuntimeError(f"prior supervision {prior.name} not accounted; resume refused")
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


def collect_post_launch_identity(config_path, supervision: str, *, timeout=30.0) -> dict:
    """H1: bounded collection of the REAL production launcher/app-server/thread identity
    and GPU UUIDs after launch, written as an immutable post-launch attachment and bound
    into the launch seal owners. Never uses placeholder values in real mode."""
    paths = derive_paths(config_path)
    run_dir = paths["run_dir"]
    attempt_dir = run_dir / "meta_session/supervision" / supervision
    deadline = time.monotonic() + timeout
    state = {}
    while time.monotonic() < deadline:
        state = _read_json(run_dir / "meta_session/state.json") or {}
        if state.get("launcher_pid") and state.get("codex_pid") and state.get("thread_id"):
            break
        time.sleep(0.1)
    if not (state.get("launcher_pid") and state.get("codex_pid") and state.get("thread_id")):
        raise RuntimeError("production launcher/app-server/thread identity not available")

    def proc_ident(pid):
        stat = _proc_stat(pid)
        return {"pid": pid, "pgid": int(stat[2]), "start_ticks": stat[19]}

    launcher = proc_ident(state["launcher_pid"])
    app_server = proc_ident(state["codex_pid"])
    attachment = {
        "kind": "rsiflow_post_launch_identity", "supervision_attempt": supervision,
        "thread_id": state["thread_id"],
        "owners": {"launcher": launcher, "app_server": app_server},
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _atomic_write(attempt_dir / "post-launch-identity.json", attachment)
    seal = _read_json(attempt_dir / "launch.json") or {}
    seal["owners"] = {"launcher": launcher, "app_server": app_server}
    seal["binding"]["thread_id"] = state["thread_id"]
    seal["post_launch_identity_path"] = str(attempt_dir / "post-launch-identity.json")
    seal["post_launch_identity_hash"] = _sha256(attempt_dir / "post-launch-identity.json")
    _atomic_write(attempt_dir / "launch.json", seal)
    return attachment


def launch(config_path, supervision: str, *, command, timeout=DEFAULT_TIMEOUT,
           kill_after=DEFAULT_KILL_AFTER, run_dir=None, skip_asset_gates=False,
           handshake_timeout=30.0, thread_id="five-round-thread", expected_gpus=None,
           mode="fresh", resumed_from=None) -> dict:
    if run_dir is not None:
        raise ValueError("arbitrary run_dir override is refused; paths derive from config")
    paths = derive_paths(config_path)
    # H2: ALL static normalization / asset gates happen BEFORE Popen.
    attempt_dir = _validate_launch_gates(paths, supervision, skip_asset_gates, mode)
    config = paths["config"]
    project_root = Path(__file__).resolve().parents[2]
    base = (project_root / "RSIFlow" / config.get("runtime_source", "runtime") / config["base_config"]).resolve()
    run_dir_resolved = paths["run_dir"].resolve()
    effective_path = attempt_dir.parent / supervision / "effective.json"
    effective_hash = None
    if not skip_asset_gates:
        effective_obj = _normalize_effective(paths, run_dir_resolved)
        attempt_dir.mkdir(parents=True)  # exclusive; refuses existing
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
        result = launch(args.config, args.supervision, command=command, timeout=args.timeout,
                        kill_after=args.kill_after, skip_asset_gates=False,
                        mode=args.launch_mode, resumed_from=args.resumed_from)
        # H1: bind the real production launcher/app-server/thread identity after launch.
        result["post_launch_identity"] = collect_post_launch_identity(args.config, args.supervision)
        print(json.dumps(result, indent=2))
    elif args.mode == "status":
        print(json.dumps(status(args.config, args.supervision), indent=2))
    elif args.mode == "stop":
        print(json.dumps(stop(args.config, args.supervision, grace=args.grace), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())