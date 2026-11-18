"""External cleanup receipt writer for a five-round run (inspect / cleanup).

This is the sole writer of ``kind=rsiflow_external_cleanup`` receipts consumed by
``summarize_deepcoder_five_round._validate_cleanup``; the two share one schema. It
never accepts ``--completed``/``--stopped`` flags: status is derived only from actual
inspection. It does not invent kills; active jobs/DDP/services yield ``pending`` and
no ``stop_services`` call. Owned services are stopped only via the production
``stop_services`` after every non-service owner group is verified exited.

Offline tests inject controlled no-GPU processes and an ``nvidia-smi`` observation;
a real cleanup must measure actually on the target host's visible /proc/GPU namespace
and atomically write a NEW observation directory (never overwrite history).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from deepcoder_five_round_supervisor import host_identity, load_launch_chain, validate_identity, write_once

def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _sha256(path):
    import hashlib
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _atomic_write(path: Path, value: dict):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())  # R6: durable evidence before atomic publish
    os.replace(tmp, path)
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def _proc_stat(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()


def _group_members(pgid):
    members, _ = _group_members_with_errors(pgid)
    return members


def _group_members_with_errors(pgid):
    """Scan a PGID; unreadable members are reported, never silently dropped."""
    members = []
    read_errors = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            if int(stat[2]) == pgid and stat[0] != "Z":
                members.append({"pid": int(entry.name), "start_ticks": stat[19]})
        except FileNotFoundError:
            continue
        except PermissionError:
            read_errors.append(f"/proc/{entry.name}/stat:permission")
        except (ValueError, IndexError, OSError) as exc:
            read_errors.append(f"/proc/{entry.name}/stat:{type(exc).__name__}")
    return members, read_errors


def _derive(config_path, run_dir, supervision):
    config_path = Path(config_path).resolve()
    config = _read_json(config_path)
    if not isinstance(config, dict):
        raise ValueError("config unreadable")
    output_root = Path(config["output_root"]).resolve()
    expected_run = (output_root / "runs" / config["run_name"]).resolve()
    run_dir = Path(run_dir).resolve()
    if run_dir != expected_run:
        raise ValueError("run_dir not naturally derived from config")
    attempt_dir = run_dir / "meta_session/supervision" / supervision
    if attempt_dir.parent.parent != run_dir / "meta_session":
        raise ValueError("supervision attempt outside this run")
    return config, config_path, output_root, run_dir, attempt_dir


def _observe_owner(identity, run_dir):
    """Inspect an owner process group; never signal. A dead leader does NOT imply an
    empty group: live descendants in the same PGID (e.g. DDP ranks) keep it alive.
    Unreadable members / parse errors => unknown + read_errors (never false-exited).
    A live leader must match the declared PGID and start identity. Two snapshots
    bracket the observation so a transient empty scan cannot certify exit."""
    pid = identity.get("pid")
    pgid = identity.get("pgid", pid)
    start_ticks = str(identity.get("start_ticks", ""))
    if not start_ticks.isdigit():
        return {"state": "unknown", "group_members": [], "read_errors": ["start_identity_missing"]}
    try:
        stat = _proc_stat(pid)
    except FileNotFoundError:
        stat = None
    except PermissionError:
        return {"state": "unknown", "group_members": [], "read_errors": [f"/proc/{pid}/stat:permission"]}
    except (ValueError, IndexError, OSError) as exc:
        return {"state": "unknown", "group_members": [], "read_errors": [f"/proc/{pid}/stat:{type(exc).__name__}"]}
    if stat is not None:
        # Field access is guarded: a truncated/malformed stat must yield unknown, never
        # an uncaught IndexError that bypasses the pending/failed receipt path.
        try:
            actual_ticks = stat[19]
            actual_pgid = int(stat[2])
        except (ValueError, IndexError) as exc:
            return {"state": "unknown", "group_members": [], "read_errors": [f"/proc/{pid}/stat:malformed:{type(exc).__name__}"]}
        if actual_ticks != start_ticks:
            return {"state": "unknown", "group_members": [], "read_errors": [f"pid {pid} reused"]}
        if actual_pgid != pgid:
            return {"state": "unknown", "group_members": [], "read_errors": [f"pid {pid} pgid mismatch"]}
    before, errors_before = _group_members_with_errors(pgid)
    after, errors_after = _group_members_with_errors(pgid)
    read_errors = errors_before + errors_after
    snapshots = {"before": before, "after": after}
    if read_errors:
        return {"state": "unknown", "group_members": after or before, "read_errors": read_errors, "snapshots": snapshots}
    if before or after:
        return {"state": "alive", "group_members": after or before, "read_errors": [], "snapshots": snapshots}
    return {"state": "exited", "group_members": [], "read_errors": [], "snapshots": snapshots}


def _supervision_history(run_dir):
    sup_root = run_dir / "meta_session/supervision"
    attempts = sorted((d for d in sup_root.iterdir() if d.is_dir() and d.name.isdigit()),
                      key=lambda d: int(d.name)) if sup_root.is_dir() else []
    history = []
    all_accounted = True
    for attempt in attempts:
        record = {"attempt": attempt.name}
        identity = _read_json(attempt / "identity.json")
        reconcile = _read_json(attempt / "reconcile.json")
        try:
            exit_value = (attempt / "exit").read_text().strip()
        except OSError:
            exit_value = None
        accounted = (isinstance(identity, dict) and identity.get("run_dir")
                     and isinstance(reconcile, dict) and reconcile.get("status") == "complete"
                     and not reconcile.get("remaining") and exit_value not in (None, ""))
        if not accounted:
            all_accounted = False
        for name, file in (("identity", "identity.json"), ("exit", "exit"), ("reconcile", "reconcile.json")):
            record[name + "_path"] = str(attempt / file)
            record[name + "_hash"] = _sha256(attempt / file)
        record["exit_value"] = exit_value
        history.append(record)
    return [a.name for a in attempts], history, all_accounted


def _default_gpu_observer():
    import hashlib
    import subprocess
    cmd = ["nvidia-smi", "--query-gpu=index,uuid,name,memory.total,memory.free,memory.used,utilization.gpu",
           "--format=csv,noheader,nounits"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
    per_gpu = []
    for line in result.stdout.strip().splitlines():
        idx, gpu_uuid, name, total, free, used, util = [x.strip() for x in line.split(",")]
        per_gpu.append({"gpu": int(idx), "uuid": gpu_uuid, "name": name,
                        "memory_total": int(total), "memory_free": int(free),
                        "memory_used": int(used), "utilization": int(util)})
    apps_cmd = ["nvidia-smi", "--query-compute-apps=pid,uuid", "--format=csv,noheader,nounits"]
    apps = subprocess.run(apps_cmd, capture_output=True, text=True, timeout=30, check=True)
    compute_apps = [{"pid": int(l.split(",")[0]), "uuid": l.split(",")[1].strip()}
                    for l in apps.stdout.strip().splitlines() if l.strip()]
    # R6: near-idle is used+util+compute_apps, not merely free>=40000. Raw output is sealed.
    near_idle = all(g["memory_free"] >= 40000 and g["memory_used"] <= 1024 and g["utilization"] <= 5
                    for g in per_gpu) and not compute_apps
    return {"time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "command": cmd, "command_result": 0,
            "raw_stdout": result.stdout, "stdout_hash": hashlib.sha256(result.stdout.encode()).hexdigest(),
            "apps_command": apps_cmd, "apps_raw_stdout": apps.stdout,
            "apps_stdout_hash": hashlib.sha256(apps.stdout.encode()).hexdigest(),
            "expected_uuids": [g["uuid"] for g in per_gpu], "per_gpu": per_gpu,
            "compute_apps": compute_apps, "free_verified": near_idle}


def inspect_run(config_path, run_dir, supervision, *, gpu_observer=None) -> dict:
    config, config_path, output_root, run_dir, attempt_dir = _derive(config_path, run_dir, supervision)
    state = _read_json(run_dir / "meta_session/state.json") or {}
    workflow = _read_json(run_dir / "meta_session/workflow_state.json") or {}
    attempts, history, all_accounted = _supervision_history(run_dir)
    launch = _read_json(attempt_dir / "launch.json") or {}
    jobs = sorted((run_dir / "meta_session/jobs").glob("*/job.json"))
    phases = sorted(run_dir.glob("round_*/attempts/attempt_*/**/gpu_phase_receipt.json"))
    registry = _read_json(output_root / "services/four_gpu_services.json") or {}
    gpu = (gpu_observer or _default_gpu_observer)()
    return {"mode": "inspect", "run_dir": str(run_dir), "supervision": supervision,
            "protocol": {"status": state.get("status"), "current_stage": workflow.get("current_stage"),
                         "finish_allowed": workflow.get("finish_allowed")},
            "supervision": {"attempts": attempts, "all_accounted": all_accounted},
            "owners": {"launcher": launch.get("owners", {}).get("launcher"),
                       "app_server": launch.get("owners", {}).get("app_server"),
                       "jobs": [str(j) for j in jobs], "training_phases": [str(p) for p in phases],
                       "inference_services": registry.get("services", [])},
            "registry": {"path": str(output_root / "services/four_gpu_services.json"),
                         "phase": registry.get("phase")},
            "gpu": gpu}


def cleanup_run(config_path, run_dir, supervision, *, gpu_observer=None, stop_services_fn=None,
                ports_observer=None, occupier=None, skip_asset_gates=False, lifecycle_lock=None) -> Path:
    started_wall = datetime.now(timezone.utc)
    started_mono = time.monotonic()
    config, config_path, output_root, run_dir, attempt_dir = _derive(config_path, run_dir, supervision)
    attempts, history, all_accounted = _supervision_history(run_dir)
    if not attempts or attempts[-1] != supervision:
        raise ValueError("cleanup must target the current supervision attempt")
    # H8: acquire the same target-host lifecycle lock serializing handoff/start/cleanup/
    # restore. A conflicting lock refuses (never concurrent cleanup/restore).
    import fcntl
    lock_path = Path(lifecycle_lock) if lifecycle_lock else GLOBAL_GPU_LIFECYCLE_LOCK
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        raise RuntimeError("lifecycle lock held by another operator; cleanup refused")
    try:
        return _cleanup_locked(config, config_path, output_root, run_dir, attempt_dir, supervision,
                               attempts, history, all_accounted, started_wall, started_mono,
                               gpu_observer=gpu_observer, stop_services_fn=stop_services_fn,
                               ports_observer=ports_observer, occupier=occupier,
                               skip_asset_gates=skip_asset_gates)
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def _cleanup_locked(config, config_path, output_root, run_dir, attempt_dir, supervision,
                    attempts, history, all_accounted, started_wall, started_mono, *,
                    gpu_observer=None, stop_services_fn=None, ports_observer=None,
                    occupier=None, skip_asset_gates=False) -> Path:
    obs_id = "obs-" + uuid.uuid4().hex[:12]
    obs_dir = attempt_dir / "external-cleanup" / obs_id
    obs_dir.mkdir(parents=True)  # exclusive new observation; never overwrite history
    evidence, source_snapshots = {}, {}

    def seal(path):
        import hashlib
        path = Path(path).resolve()
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if path.is_relative_to(obs_dir):
            captured = path
        else:
            captured = obs_dir / "sources" / (hashlib.sha256(str(path).encode()).hexdigest() + "-" + digest)
            captured.parent.mkdir(exist_ok=True)
            if not captured.exists():
                with captured.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            source_snapshots[str(path)] = {"path": str(captured), "sha256": digest}
        evidence[str(captured)] = digest
        return str(path), digest

    def write_sealed(path, value):
        _atomic_write(Path(path), value)
        return seal(path)

    blockers = []
    status = "completed"
    state = _read_json(run_dir / "meta_session/state.json") or {}
    launch = _read_json(attempt_dir / "launch.json") or {}
    try:
        chain = load_launch_chain(attempt_dir, config_path, run_dir, observer_host=host_identity())
        launch = {**launch, **{k: chain[k] for k in ("binding", "owners", "expected_gpus")}}
        for source in chain["paths"]:
            seal(source)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        blockers.append("launch_chain:" + str(exc))
        chain = None
    identity = _read_json(attempt_dir / "identity.json")
    reconcile = _read_json(attempt_dir / "reconcile.json")
    try:
        exit_value = (attempt_dir / "exit").read_text().strip()
    except OSError:
        exit_value = None
    if not (identity and reconcile and reconcile.get("status") == "complete"
            and not reconcile.get("remaining") and exit_value not in (None, "")):
        blockers.append("supervision_not_ended")
        status = "pending"
    if not all_accounted:
        blockers.append("previous_attempts_unaccounted")
        status = "pending"
    if launch.get("kind") != "rsiflow_launch":
        blockers.append("launch_seal_missing")
        status = "pending"

    # effective config: the launch-sealed file is READ-ONLY evidence. Verify it matches
    # the production normalization; never overwrite history (a mismatch is a blocker).
    project_root = Path(__file__).resolve().parents[2]
    launch_binding = launch.get("binding", {})
    effective_path = Path(launch_binding.get("effective_config_path") or (attempt_dir / "effective.json"))
    effective_obj = None
    try:
        import sys
        for entry in (project_root / "RSIFlow", project_root / "RSIFlow/runtime"):
            if str(entry) not in sys.path:
                sys.path.insert(0, str(entry))
        from task_adapter import TaskAdapter
        effective_obj = TaskAdapter(project_root / "RSIFlow", run_dir / "controller_receipts")._config(
            {"config_path": str(config_path)})
        effective = effective_obj.model_dump()
        sealed_effective = _read_json(effective_path)
        if sealed_effective is None:
            blockers.append("effective_config_seal_missing")
            status = "pending"
        elif sealed_effective != effective:
            blockers.append("effective_config_seal_mismatch")
            status = "pending"
        if effective_path.is_file():
            seal(effective_path)
    except Exception as exc:  # offline temp config may lack full assets
        if not skip_asset_gates:
            blockers.append(f"effective_config_invalid:{exc}")
            status = "pending"
        if effective_path.is_file():
            seal(effective_path)

    base = (project_root / "RSIFlow" / config.get("runtime_source", "runtime") / config["base_config"]).resolve()
    binding = {
        "config_path": str(config_path), "config_hash": _sha256(config_path),
        "base_config_path": str(base), "base_config_hash": _sha256(base),
        "effective_config_path": str(effective_path), "effective_config_hash": _sha256(effective_path),
        "run_dir": str(run_dir), "output_root": str(output_root),
        "supervision_attempt": supervision, "thread_id": state.get("thread_id"),
        "launch_receipt_path": str(attempt_dir / "launch.json"),
        "launch_receipt_hash": _sha256(attempt_dir / "launch.json"),
        "backend": "linux_bwrap_seccomp_v1",
    }
    seal(config_path); seal(base); seal(run_dir / "meta_session/state.json"); seal(attempt_dir / "launch.json")
    for record in history:
        for name in ("identity", "exit", "reconcile"):
            p = record[name + "_path"]
            if Path(p).is_file():
                seal(p)

    # reconcile non-service owners (no signals); active groups => pending
    owners = {}

    def make_owner(ident, source, label):
        observation = _observe_owner(ident, run_dir)
        obs = {"kind": "rsiflow_owner_exit", "run_dir": str(run_dir), "identity": ident,
               "state": observation["state"], "group_members": observation["group_members"],
               "read_errors": observation["read_errors"],
               "snapshots": observation.get("snapshots", {}),
               "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        obs_path, digest = write_sealed(obs_dir / f"{label}.json", obs)
        seal(source)
        if observation["state"] != "exited":
            blockers.append(f"owner_{label}_{observation['state']}")
        return {"identity": ident, "source_path": str(source), "state": observation["state"],
                "evidence_path": obs_path, "evidence_sha256": digest}

    launch_path = attempt_dir / "launch.json"
    for group, pid_key in (("launcher", "launcher_pid"), ("app_server", "codex_pid")):
        ident = launch.get("owners", {}).get(group) or {}
        if ident.get("pid") != state.get(pid_key):
            blockers.append(f"{group}_identity_mismatch")
        owners[group] = make_owner(ident, launch_path, group)
    from summarize_deepcoder_five_round import _jobs_validation
    job_check = _jobs_validation(run_dir)
    blockers.extend("job_binding:" + reason for reason in job_check["invalid"] + job_check["missing_final_receipts"])
    jobs = sorted((run_dir / "meta_session/jobs").glob("*/job.json"))
    owners["jobs"] = []
    for i, jobpath in enumerate(jobs):
        job = _read_json(jobpath) or {}
        ident = {"pid": job.get("pid"), "pgid": job.get("pid"), "start_ticks": str(job.get("process_start"))}
        owners["jobs"].append(make_owner(ident, jobpath, f"job_{i}"))
        for source in (job.get("result_path"), str(Path(job.get("result_path", "/invalid")).parent / "request.json"),
                       str(Path(job.get("turn_dir", "/invalid")) / "command.json"),
                       str(Path(job.get("turn_dir", "/invalid")) / "receipt.json")):
            if source and Path(source).is_file():
                seal(source)
    from summarize_deepcoder_five_round import _validate_phase_owner
    phases = sorted(run_dir.glob("round_*/attempts/attempt_*/**/gpu_phase_receipt.json"))
    owners["training_phases"] = []
    for i, phase in enumerate(phases):
        value = _read_json(phase) or {}
        try:
            ident = _validate_phase_owner(value, phase, run_dir)
            if not (phase.parent / "checkpoint_trained.json").is_file():
                raise ValueError("training_checkpoint_missing")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            blockers.append(f"training_{i}_requires_audit:{exc}")
            ident = value.get("training_identity") or {"pid": value.get("training_pid"), "start_ticks": ""}
        owners["training_phases"].append(make_owner(ident, phase, f"training_{i}"))

    # R5: centralized gate BEFORE any service signal. blockers must be EMPTY and every
    # owner/launch/host/hash/service-inventory/weights binding must pass. Save the before
    # registry bytes; pass the normalized PipelineConfig so registry_path resolves here.
    registry_path = output_root / "services/four_gpu_services.json"
    registry = _read_json(registry_path) or {}
    active = _read_json(run_dir / "active_task.json") or {}
    registry_before_bytes = registry_path.read_bytes() if registry_path.is_file() else None
    registry_before_snapshot = None
    if registry_before_bytes is not None:
        before_path = obs_dir / "registry-before.json"
        with before_path.open("xb") as stream:
            stream.write(registry_before_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        seal(before_path)
        registry_before_snapshot = str(before_path)
    registry_before_hash = _sha256(registry_path)
    registry_after_hash = registry_before_hash
    stop_action = "skipped"
    stop_command_result = None
    non_service_exited = all(o["state"] == "exited" for o in
                             [owners["launcher"], owners["app_server"], *owners["jobs"], *owners["training_phases"]])
    registry_owned = True
    if effective_obj is not None:
        try:
            from rsiflow.task_meta.gpu_phases import registry_path as production_registry_path
            if Path(production_registry_path(effective_obj)).resolve() != registry_path.resolve():
                registry_owned = False
                blockers.append("registry_not_owned_by_effective_config")
        except Exception as exc:
            registry_owned = False
            blockers.append(f"registry_path_check_failed:{exc}")
    # launch binding hashes / thread / run / attempt must match current inputs (R5).
    launch_binding = launch.get("binding", {})
    launch_binding_ok = (
        launch.get("kind") == "rsiflow_launch"
        and launch_binding.get("config_hash") == _sha256(config_path)
        and launch_binding.get("base_config_hash") == _sha256(base)
        and launch_binding.get("backend") == "linux_bwrap_seccomp_v1"
        and launch_binding.get("run_dir") == str(run_dir)
        and launch_binding.get("output_root") == str(output_root)
        and launch_binding.get("supervision_attempt") == supervision
        and launch_binding.get("thread_id") == state.get("thread_id")
        and launch_binding.get("effective_config_hash") == _sha256(effective_path)
    )
    if not launch_binding_ok:
        blockers.append("launch_binding_gate_failed")
    # load_launch_chain above is mandatory and binds the observer's actual host,
    # boot and namespace to immutable prelaunch/runtime evidence before any signals.
    if chain is None:
        blockers.append("trusted_launch_chain_missing")
    # full service inventory + weights verified BEFORE stop (R5).
    services = registry.get("services", [])
    inventory_ok = ({s.get("gpu") for s in services} == {0, 1, 2, 3} and len(services) == 4)
    weights_ok = (registry.get("checkpoint") == active.get("checkpoint_path")
                  and all(s.get("verified_binding") == {"checkpoint_path": active.get("checkpoint_path"),
                                                        "weights": active.get("checkpoint_manifest")}
                          for s in services))
    if not inventory_ok:
        blockers.append("service_inventory_incomplete")
    if not weights_ok:
        blockers.append("registry_weights_gate_failed")
    if registry.get("phase") != "services_stopped":
        gates_pass = (not blockers and non_service_exited and registry_owned
                      and launch_binding_ok and inventory_ok and weights_ok)
        if not gates_pass:
            blockers.append("stop_services_gates_not_passed")
            if status == "completed":
                status = "pending"
        else:
            stop_action = "executed"
            try:
                if stop_services_fn is None:
                    from rsiflow.task_meta.gpu_phases import stop_services
                    stop_services_fn = stop_services
                stop_services_fn(effective_obj if effective_obj is not None else config)
                stop_command_result = 0
                registry = _read_json(registry_path) or {}
                registry_after_hash = _sha256(registry_path)
            except Exception as exc:
                stop_command_result = 1
                blockers.append(f"stop_services_failed:{exc}")
                status = "failed"
                registry = _read_json(registry_path) or {}
                registry_after_hash = _sha256(registry_path)
    if registry.get("phase") != "services_stopped" or registry.get("stopped_at") is None:
        blockers.append("registry_not_stopped")
        if status == "completed":
            status = "pending"
    if not weights_ok:
        blockers.append("registry_checkpoint_mismatch")
        if status == "completed":
            status = "pending"
    services = registry.get("services", [])
    owners["inference_services"] = []
    for i, service in enumerate(services):
        if service.get("verified_binding") != {"checkpoint_path": active.get("checkpoint_path"),
                                               "weights": active.get("checkpoint_manifest")}:
            blockers.append(f"service_{i}_weights_mismatch")
        owners["inference_services"].append(make_owner(service.get("identity", {}), registry_path, f"service_{i}"))
    if registry_path.is_file():
        seal(registry_path)

    # GPU measurement: two independent observations (injected offline; real nvidia-smi
    # in production). Each carries its own raw stdout hash; measurement failure still
    # produces a failed/pending receipt rather than raising.
    expected = launch.get("expected_gpus", [])
    gpu_observations = []
    actions = [{"action": "stop_services", "result": stop_action, "command_result": stop_command_result}]
    errors = []
    observe = gpu_observer or _default_gpu_observer
    import hashlib
    gpu_mono = []
    for index in range(2):
        if index == 1:
            time.sleep(1.0)  # R6: two independent samples separated by a real interval
        gpu_mono.append(time.monotonic())
        try:
            gpu = observe()
            from summarize_deepcoder_five_round import validate_gpu_measurement
            validate_gpu_measurement(gpu, expected)
        except Exception as exc:
            errors.append(f"gpu_observation_{index}_failed:{exc}")
            blockers.append(f"gpu_observation_{index}_failed")
            status = "failed"
            gpu = {"time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "command_result": 1, "per_gpu": [], "compute_apps": [], "free_verified": False,
                   "expected_uuids": [], "stdout_hash": None}
        gpu_path, gpu_hash = write_sealed(obs_dir / f"gpu_observation_{index}.json", gpu)
        gpu_observations.append({**gpu, "evidence_path": gpu_path, "evidence_sha256": gpu_hash})
        # R6: raw output must be sealed and its hash must match (no None hash).
        if not gpu.get("stdout_hash"):
            blockers.append(f"gpu_raw_hash_missing_{index}")
            if status == "completed":
                status = "pending"
        elif gpu.get("raw_stdout") is not None and \
                hashlib.sha256(gpu["raw_stdout"].encode()).hexdigest() != gpu["stdout_hash"]:
            blockers.append(f"gpu_raw_hash_mismatch_{index}")
            if status == "completed":
                status = "pending"
        if gpu.get("compute_apps"):
            blockers.append(f"gpu_compute_apps_present_{index}")
            if status == "completed":
                status = "pending"
        if not gpu.get("free_verified"):
            blockers.append(f"gpu_not_free_{index}")
            if status == "completed":
                status = "pending"
        if gpu.get("command_result") != 0:
            blockers.append(f"gpu_command_failed_{index}")
            status = "failed"
        if {g.get("gpu") for g in expected} != {0, 1, 2, 3} or len({g.get("uuid") for g in expected}) != 4:
            blockers.append("launch_gpu_inventory_invalid")
            if status == "completed":
                status = "pending"
        if gpu.get("expected_uuids") != [g["uuid"] for g in expected]:
            blockers.append(f"gpu_uuid_mismatch_{index}")
            if status == "completed":
                status = "pending"
        readings = gpu.get("per_gpu", [])
        if {g.get("gpu") for g in readings} != {0, 1, 2, 3}:
            blockers.append(f"gpu_readings_incomplete_{index}")
            if status == "completed":
                status = "pending"
        for reading in readings:
            if type(reading.get("memory_free")) is not int or reading["memory_free"] < 40000 \
                    or reading["memory_free"] > reading.get("memory_total", 0):
                blockers.append(f"gpu_reading_invalid_{index}")
                if status == "completed":
                    status = "pending"
            # R6: near-idle also requires low used + util, not just free memory.
            if reading.get("memory_used", 0) > 1024 or reading.get("utilization", 0) > 5:
                blockers.append(f"gpu_not_near_idle_{index}")
                if status == "completed":
                    status = "pending"
    # near-idle requires both independent observations to agree (runbook two-check rule)
    if len(gpu_observations) == 2 and gpu_observations[0].get("free_verified") \
            and gpu_observations[1].get("free_verified"):
        pass
    elif status == "completed":
        blockers.append("gpu_two_observations_not_both_free")
        status = "pending"
    if len(gpu_mono) == 2 and (gpu_mono[1] - gpu_mono[0]) < 0.5:
        blockers.append("gpu_observation_interval_too_short")
        if status == "completed":
            status = "pending"

    # ports bind-close observation (injected offline; real bind-close in production)
    try:
        ports = (ports_observer or _default_ports_observer)()
    except Exception as exc:
        errors.append(f"ports_observation_failed:{exc}")
        ports = {"status": "measurement_failed", "error": str(exc)}
        if status == "completed":
            blockers.append("ports_observation_failed")
            status = "pending"
    ports_path, ports_hash = write_sealed(obs_dir / "ports_observation.json", ports)
    if ports.get("status") not in ("closed", "not_measured_offline"):
        if status == "completed":
            blockers.append("ports_not_closed")
            status = "pending"

    if blockers and status == "completed":
        status = "pending"
    finished_wall = datetime.now(timezone.utc)
    receipt = {
        "schema_version": 1, "kind": "rsiflow_external_cleanup", "observation_id": obs_id,
        "status": status,
        "started_at_utc": started_wall.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "observed_at_utc": finished_wall.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "started_monotonic": started_mono, "finished_monotonic": time.monotonic(),
        "blockers": blockers,
        "writer": {"script_path": str(Path(__file__).resolve()), "hash": _sha256(Path(__file__).resolve()),
                   "host": os.uname().nodename, "boot_id": _read_boot_id(),
                   "pid_namespace": host_identity()["pid_namespace"], "uid": os.getuid()},
        "binding": binding, "host_identity": host_identity(),
        "supervision": {"all_previous_attempts_accounted_for": all_accounted, "attempts": history},
        "protocol": {"state_path": str(run_dir / "meta_session/state.json"),
                     "state_hash": _sha256(run_dir / "meta_session/state.json"),
                     "observed_status": state.get("status"),
                     "current_stage": (_read_json(run_dir / "meta_session/workflow_state.json") or {}).get("current_stage"),
                     "finish_allowed": (_read_json(run_dir / "meta_session/workflow_state.json") or {}).get("finish_allowed")},
        "owners": owners,
        "registry": {"path": str(registry_path), "before_path": registry_before_snapshot, "before_hash": registry_before_hash,
                     "after_hash": registry_after_hash, "phase": registry.get("phase"),
                     "checkpoint": registry.get("checkpoint"), "weights": active.get("checkpoint_manifest"),
                     "stop_action_skipped_or_executed": stop_action, "command_result": stop_command_result},
        "gpu_observations": gpu_observations,
        "ports_observation": {**ports, "evidence_path": ports_path, "evidence_sha256": ports_hash},
        "actions": actions, "errors": errors,
        "evidence_files_sha256": evidence, "source_snapshots": source_snapshots,
        "occupier": occupier if occupier is not None else {"prior_identity_hash": None, "stop_receipt_hash": None, "restore_pending": True},
    }
    receipt_path = obs_dir / "receipt.json"
    _atomic_write(receipt_path, receipt)
    return receipt_path


def _default_ports_observer():
    import socket
    closed = []
    for port in (8271, 8272, 8273, 8274):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                closed.append(port)
            except OSError:
                return {"status": "port_in_use", "port": port, "closed_ports": closed}
    return {"status": "closed", "closed_ports": closed}


def _read_boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def _latest_cleanup_receipt(attempt_dir):
    root = attempt_dir / "external-cleanup"
    if not root.is_dir():
        return None, None
    observations = sorted((d for d in root.iterdir() if d.is_dir()),
                          key=lambda d: d.stat().st_mtime)
    for obs in reversed(observations):
        receipt_path = obs / "receipt.json"
        receipt = _read_json(receipt_path)
        if isinstance(receipt, dict):
            return receipt, receipt_path
    return None, None


# R8: a single target-host GPU lifecycle lock coordinates handoff/start/cleanup/restore
# across runs sharing GPU 0-3 (not a per-output_root lock). Its location must not break
# the fresh-output gate (it lives outside the fresh output_root).
GLOBAL_GPU_LIFECYCLE_LOCK = Path("/tmp/rsiflow-gpu-lifecycle.lock")


def _verify_cleanup_evidence(receipt, receipt_path, config_path, run_dir, supervision):
    from summarize_deepcoder_five_round import _validate_cleanup, _supervision
    config = _read_json(config_path) or {}
    config["_config_path"] = str(Path(config_path).resolve())
    try:
        if not config.get("output_root"):
            raise ValueError("cleanup config missing output_root")
        summary = _supervision(Path(run_dir))
        if not summary["attempts"] or summary["attempts"][-1] != supervision:
            raise ValueError("cleanup not current attempt")
        _validate_cleanup(receipt, Path(receipt_path), Path(run_dir), config,
                          Path(config["output_root"]), summary,
                          _read_json(Path(run_dir) / "active_task.json") or {})
        chain = load_launch_chain(Path(run_dir) / "meta_session/supervision" / supervision,
                                  config_path, run_dir, observer_host=host_identity())
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("cleanup evidence invalid/pending: " + str(exc)) from exc
    # Recheck all owners now, after validating the recorded inventory, inside the lock.
    owners = receipt["owners"]
    inventory = [owners["launcher"], owners["app_server"], *owners["jobs"],
                 *owners["training_phases"], *owners["inference_services"]]
    if any(_observe_owner(o["identity"], run_dir)["state"] != "exited" for o in inventory):
        raise RuntimeError("cleanup owners no longer exited")
    if _default_ports_observer().get("status") != "closed":
        raise RuntimeError("cleanup ports no longer closed")
    return chain


DEFAULT_OCCUPIER_COMMAND = ["/data/wuxuecheng/z-occupier-ready/run.sh", "--mem-mb", "2500", "--log-interval", "20"]
DEFAULT_OCCUPIER_IDENTITY = Path("/data/wuxuecheng/z-occupier-ready/runtime/dev4/identity.json")


def _process_record(pid):
    stat = _proc_stat(pid)
    if stat[0] == "Z":
        raise ProcessLookupError("zombie owner")
    identity = {"pid": pid, "pgid": int(stat[2]), "start_ticks": stat[19]}
    validate_identity(identity)
    argv = (Path("/proc") / str(pid) / "cmdline").read_bytes().split(b"\0")
    return {**identity, "argv": [a.decode() for a in argv if a]}


def _terminate_owned_group(identity, grace=5.0):
    """Signal only revalidated members; never kill by name or an unverified PID."""
    validate_identity(identity)
    members, errors = _group_members_with_errors(identity["pgid"])
    if errors:
        raise RuntimeError("rollback owner observation unknown: " + repr(errors))
    targets = []
    for member in members:
        record = _process_record(member["pid"])
        if record["pgid"] != identity["pgid"] or record["start_ticks"] != member["start_ticks"]:
            raise RuntimeError("rollback identity changed")
        targets.append(record)
    try:
        leader = _process_record(identity["pid"])
        if any(leader[k] != identity[k] for k in ("pid", "pgid", "start_ticks")):
            raise RuntimeError("rollback leader reused")
    except (FileNotFoundError, ProcessLookupError):
        pass
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for record in targets:
            try:
                current = _process_record(record["pid"])
                if any(current[k] != record[k] for k in ("pid", "pgid", "start_ticks")):
                    raise RuntimeError("rollback member reused")
                os.kill(record["pid"], sig)
            except (FileNotFoundError, ProcessLookupError):
                continue
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            observed = _observe_owner(identity, None)
            if observed["state"] == "exited":
                return observed
            if observed["state"] == "unknown":
                raise RuntimeError("rollback observation unknown")
            time.sleep(0.05)
    raise RuntimeError("rollback pending: owned group still alive")


def _start_occupier_process(occupier_command=None, *, shared_identity_path=None,
                            gpu_observer=None, exec_timeout=30.0):
    import subprocess
    command = list(occupier_command or DEFAULT_OCCUPIER_COMMAND)
    identity_path = Path(shared_identity_path) if shared_identity_path else DEFAULT_OCCUPIER_IDENTITY
    pending_path = identity_path.with_suffix(".pending.json")
    write_once(pending_path, {"kind": "rsiflow_occupier_start_pending", "command": command,
                              "host_identity": host_identity()})
    # The process identity is captured before waiting for torchrun exec/ranks.
    log_path = identity_path.parent / "occupier.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        proc = subprocess.Popen(command, start_new_session=True,
                                env=dict(os.environ, CUDA_VISIBLE_DEVICES="0,1,2,3", NPROC="4"),
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log)
    initial = None
    try:
        _atomic_write(pending_path, {"kind": "rsiflow_occupier_start_pending", "spawned_pid": proc.pid,
                                     "command": command, "host_identity": host_identity()})
        initial = _process_record(proc.pid)
        if initial["pgid"] != proc.pid:
            raise RuntimeError("occupier group not owned")
        deadline = time.monotonic() + exec_timeout
        while time.monotonic() < deadline:
            leader = _process_record(proc.pid)
            if any(leader[k] != initial[k] for k in ("pid", "pgid", "start_ticks")):
                raise RuntimeError("occupier identity changed")
            members, errors = _group_members_with_errors(proc.pid)
            if errors:
                raise RuntimeError("occupier group unreadable")
            ranks = [_process_record(m["pid"]) for m in members if m["pid"] != proc.pid]
            expected_script = "/data/wuxuecheng/z-occupier-ready/train.py" if occupier_command is None else str(Path(command[1]).resolve())
            def runs_script(record):
                return any((Path(arg).is_absolute() and str(Path(arg).resolve()) == expected_script)
                           or (arg == "train.py" and occupier_command is None) for arg in record["argv"])
            argv_ok = all(runs_script(r) for r in [leader, *ranks])
            if len(ranks) == 4 and argv_ok:
                gpu = (gpu_observer or _default_gpu_observer)()
                from summarize_deepcoder_five_round import validate_gpu_measurement
                expected_inventory = [{"gpu": g["gpu"], "uuid": g["uuid"]} for g in gpu.get("per_gpu", [])]
                validate_gpu_measurement(gpu, expected_inventory, require_idle=False)
                apps = gpu.get("compute_apps") or []
                expected = set(gpu.get("expected_uuids") or [])
                if (len(apps) == 4 and len(expected) == 4
                        and {a["pid"] for a in apps} == {r["pid"] for r in ranks}
                        and {a["uuid"] for a in apps} == expected
                        and all(r["pgid"] == proc.pid for r in ranks)
                        and all(g["memory_used"] > 0 and g["utilization"] > 0 for g in gpu["per_gpu"])):
                    return {**leader, "gpus": [0, 1, 2, 3], "rank_members": ranks,
                            "load_observation": gpu, "owner": "dev4-rsiflow-idle-occupier",
                            "pending_path": str(pending_path)}
            time.sleep(0.1)
        raise RuntimeError("occupier four-rank GPU identity/load not verified")
    except BaseException:
        if initial is not None:
            _terminate_owned_group(initial)
            proc.wait(timeout=5)
            pending_path.unlink(missing_ok=True)
        raise


def restore_occupier(config_path, run_dir, supervision, *, occupier_start_fn=None,
                     gpu_observer=None, lifecycle_lock=None, occupier_command=None,
                     shared_identity_path=None, exec_timeout=30.0) -> Path:
    """R8/F8: restore the idle occupier ONLY after this attempt's external cleanup receipt is
    completed with fully verified kind/binding/config-hash/evidence. Acquires the global
    target-host GPU lock FIRST, re-reads the latest receipt INSIDE the lock, confirms this
    is the current attempt, re-checks owner/GPU/ports, then starts via the real occupier
    start helper (exec identity/shared identity/four-rank load/rollback). pending/failed/
    wrong evidence refuses (nonzero exit) and NEVER starts the occupier."""
    import fcntl
    if occupier_start_fn is not None and shared_identity_path is None:
        raise ValueError("injected occupier start requires an explicit identity path")
    config, config_path, output_root, run_dir, attempt_dir = _derive(config_path, run_dir, supervision)
    lock_path = Path(lifecycle_lock) if lifecycle_lock else GLOBAL_GPU_LIFECYCLE_LOCK
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        raise RuntimeError("lifecycle lock held; occupier restore refused")
    try:
        # R8: re-read the latest receipt INSIDE the lock (not a stale pre-lock read).
        receipt, receipt_path = _latest_cleanup_receipt(attempt_dir)
        if receipt is None:
            raise RuntimeError("no external cleanup receipt; occupier restore refused")
        _verify_cleanup_evidence(receipt, receipt_path, config_path, run_dir, supervision)
        attempts, _, _ = _supervision_history(run_dir)
        if not attempts or attempts[-1] != supervision:
            raise RuntimeError("not the current supervision attempt; occupier restore refused")
        # re-check GPU/ports inside the lock before starting (injected offline; real prod)
        gpu = (gpu_observer or _default_gpu_observer)()
        if gpu.get("compute_apps") or not gpu.get("free_verified"):
            raise RuntimeError("GPU not free at restore time; occupier restore refused")
        # F8: real start helper by default (no longer default-refuse); offline injects fn.
        if occupier_start_fn is not None:
            identity = occupier_start_fn()
        else:
            identity = _start_occupier_process(occupier_command, shared_identity_path=shared_identity_path,
                                               gpu_observer=gpu_observer, exec_timeout=exec_timeout)
        identity_path = Path(shared_identity_path) if shared_identity_path else DEFAULT_OCCUPIER_IDENTITY
        previous_identity = None
        identity_written = False
        try:
            previous_identity = identity_path.read_bytes() if identity_path.is_file() else None
            restore_dir = attempt_dir / "external-cleanup" / ("restore-" + uuid.uuid4().hex[:12])
            restore_dir.mkdir(parents=True)
            observation = {
                "kind": "rsiflow_occupier_restore", "run_dir": str(run_dir),
                "supervision_attempt": supervision, "cleanup_receipt_path": str(receipt_path),
                "cleanup_receipt_hash": _sha256(receipt_path), "occupier_identity": identity,
                "gpu_observation_before": gpu, "gpu_observation": identity.get("load_observation"),
                "restored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            observation_path = restore_dir / "restore.json"
            _atomic_write(identity_path, {**identity, "updated_at": observation["restored_at"]})
            identity_written = True
            _atomic_write(observation_path, observation)
            if identity.get("pending_path"):
                Path(identity["pending_path"]).unlink(missing_ok=True)
        except BaseException:
            if occupier_start_fn is None:
                _terminate_owned_group(identity)
                if identity.get("pending_path"):
                    Path(identity["pending_path"]).unlink(missing_ok=True)
            if identity_written:
                if previous_identity is None:
                    identity_path.unlink(missing_ok=True)
                else:
                    _atomic_write(identity_path, json.loads(previous_identity))
            raise
        return observation_path
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["inspect", "cleanup", "restore"])
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--supervision", required=True)
    args = parser.parse_args(argv)
    if args.mode == "inspect":
        print(json.dumps(inspect_run(args.config, args.run_dir, args.supervision), indent=2))
        return 0
    if args.mode == "restore":
        # R8: real restore CLI; default no start helper => safe refusal until implemented.
        try:
            path = restore_occupier(args.config, args.run_dir, args.supervision)
            print(json.dumps({"restore_path": str(path)}, indent=2))
            return 0
        except RuntimeError as exc:
            print(json.dumps({"restore_refused": str(exc)}, indent=2))
            return 1
    path = cleanup_run(args.config, args.run_dir, args.supervision)
    status = _read_json(path).get("status")
    print(json.dumps({"receipt_path": str(path), "status": status}, indent=2))
    # pending/failed must exit nonzero so `set -e` blocks occupier restore downstream.
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())