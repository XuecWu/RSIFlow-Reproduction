"""Training-side minimal real smoke: five fixed mini-B1 tasks through the production TaskAdapter.

This runner only orchestrates and accepts evidence against the *production* contracts. It never
replaces TaskAdapter, the trainer, domain adapters or GPU phases, never runs pipeline.run()/Meta,
and never writes a formal round-complete or 694 marker. A wrong model answer is a valid smoke
outcome; an infrastructure failure is not. With zero natural positives SFT is blocked and the
overall smoke must not be reported as PASS.

Subcommands: plan -> execute-rollout -> execute-sft -> execute-recovery -> status
Only `plan` creates the output root, and it makes no model/service call.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT
for entry in (ROOT / "RSIFlow", ROOT / "RSIFlow/runtime"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from data_protocol import native_index, public_text, text_hash
from evolution_protocol import file_hash, fingerprint, freeze, make_manifest, read
from prepare_600_744 import repack
from rsiflow.task_meta.gpu_phases import (ensure_services, process_identity, registry_path,
                                          start_services, stop_services, verify_services)
from rsiflow.task_meta.pipeline import PipelineConfig
from rsiflow.task_meta.sft import select_positive_rows
from rsiflow.task_meta.storage import checkpoint_manifest
from rsiflow.task_meta.task_client import LocalTaskClient
from task_adapter import TaskAdapter

SOURCE_QUOTAS = {"envscaler": 1, "deepcoder_taco": 2, "hotpotqa": 1, "2wiki": 1}
COVERAGE_KEYS = ("envscaler", "deepcoder_taco:stdio", "deepcoder_taco:function", "hotpotqa", "2wiki")
SMOKE_FLAGS = {"smoke_only": True, "selected_tasks": 5, "formal_round_completed": False,
               "eligible_for_meta": False, "eligible_for_candidate_acceptance": False,
               "feedback_to_training_or_meta": False, "is_benchmark_score": False}
SUCCESS_STATUSES = {"completed", "completed_with_model_failures"}
FAILURE_STATUSES = {"incomplete", "failed", "blocked_no_positive_parent_trajectory", "requires_audit"}
SELECTION_RULE = ("first task in frozen parent B1 manifest order for each of envscaler, "
                  "deepcoder_taco STDIO, deepcoder_taco function, hotpotqa, 2wiki; fixed before "
                  "any model output; never reselected from results")
PRODUCTION_IMPLEMENTATION = (
    "RSIFlow/task_adapter.py",
    "RSIFlow/runtime/rsiflow/task_meta/pipeline_execution.py",
    "RSIFlow/runtime/rsiflow/task_meta/round_evolution.py",
    "RSIFlow/runtime/rsiflow/task_meta/gpu_phases.py",
    "RSIFlow/runtime/rsiflow/task_meta/updaters.py",
    "RSIFlow/runtime/rsiflow/task_meta/sft.py",
    "RSIFlow/runtime/scripts/train_four_gpu_phase.py",
    "RSIFlow/runtime/scripts/train_task_meta_sft.py",
)
SFT_PRESET_EXPECTED = {"num_train_epochs": 1, "max_steps": -1, "learning_rate": 2e-6,
                       "lora_rank": 8, "lora_alpha": 16, "lora_dropout": 0.05,
                       "batch_size": 1, "gradient_accumulation_steps": 8, "max_length": 16384,
                       "seed": 42}
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


class SmokeBlocked(RuntimeError):
    """Evidence, gate or infrastructure failure; never a silent rerun or a model zero."""


class _Interrupted(RuntimeError):
    """Raised by the SIGTERM/SIGINT handler so finally-owned cleanup still runs."""


def _coverage_key(task):
    if task["source"] == "deepcoder_taco":
        interface = (task.get("strata") or {}).get("interface")
        if interface not in {"stdio", "function"}:
            raise SmokeBlocked(f"DeepCoder/TACO task lacks a stdio/function interface: {task['task_id']}")
        return f"deepcoder_taco:{interface}"
    return task["source"]


def select_training_subset(parent_manifest):
    if parent_manifest.get("role") != "train_evolution" or parent_manifest.get("round_id") != 1:
        raise SmokeBlocked("Training smoke requires the frozen train_evolution B1 manifest")
    tasks = parent_manifest["tasks"]
    ids = [task["task_id"] for task in tasks]
    if len(ids) != len(set(ids)):
        raise SmokeBlocked("Parent B1 manifest contains duplicate task identities")
    chosen = {}
    for task in tasks:
        key = _coverage_key(task)
        if key in COVERAGE_KEYS and key not in chosen:
            chosen[key] = task
    missing = [key for key in COVERAGE_KEYS if key not in chosen]
    if missing:
        raise SmokeBlocked(f"Parent B1 cannot cover the required smoke kinds: {missing}")
    selected = [chosen[key] for key in COVERAGE_KEYS]
    if len({task["task_id"] for task in selected}) != 5:
        raise SmokeBlocked("Smoke selection must contain five unique task identities")
    records = []
    for task in selected:
        path = Path(task["record_file"])
        if not path.is_file() or path.is_symlink():
            raise SmokeBlocked(f"Frozen record pack unavailable: {path}")
        if not task.get("record_length"):
            raise SmokeBlocked(f"Empty frozen record: {task['task_id']}")
        if file_hash(path) != task["record_sha256"]:
            raise SmokeBlocked(f"Frozen record pack hash changed: {path}")
        with path.open("rb") as stream:
            stream.seek(task["record_offset"])
            payload = stream.read(task["record_length"])
        row = json.loads(payload)
        if text_hash(public_text(row, task["source"])) != task["content_hash"]:
            raise SmokeBlocked(f"Frozen record content hash changed: {task['task_id']}")
        records.append({"task_id": task["task_id"], "source": task["source"],
                        "coverage_key": _coverage_key(task), "domain": task["domain"],
                        "record_file": str(path), "record_offset": task["record_offset"],
                        "record_length": task["record_length"], "record_sha256": task["record_sha256"],
                        "content_hash": task["content_hash"], "group_id": task["group_id"]})
    return {"tasks": selected, "task_ids": [task["task_id"] for task in selected],
            "coverage": {key: 1 for key in COVERAGE_KEYS}, "rule": SELECTION_RULE,
            "parent_manifest_hash": parent_manifest["manifest_hash"], "parent_round_id": 1,
            "records": records, "model_results_used": False}


def _validate_output_root(root):
    resolved = Path(root).resolve()
    if Path(root).is_symlink():
        raise SmokeBlocked(f"Output root must not be a symlink: {root}")
    if resolved == PROJECT or not resolved.is_relative_to(PROJECT):
        raise SmokeBlocked(f"Output root must be a new directory inside the project: {resolved}")
    if resolved.exists():
        raise SmokeBlocked(f"Output root must not exist: {resolved}")
    ignored = _git_check_ignore(resolved)
    if ignored is False:
        raise SmokeBlocked(f"Output root is not Git-ignored: {resolved}")
    if ignored is None:
        if not _structurally_ignored(resolved):
            raise SmokeBlocked(f"Output root is not provably Git-ignored (git unavailable): {resolved}")
        method = "structural_gitignore_ancestor"
    else:
        method = "git_check_ignore"
    return {"path": str(resolved), "inside_project": True, "preexisting": False,
            "git_ignored": True, "verification": method}


def _git_check_ignore(path):
    try:
        completed = subprocess.run(["git", "-C", str(PROJECT), "check-ignore", "-q", "--no-index", str(path)],
                                   capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def _structurally_ignored(root):
    ignore_file = PROJECT / ".gitignore"
    if not ignore_file.is_file():
        return False
    any_depth, top_level, negations = set(), set(), []
    for raw in ignore_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            negations.append(line[1:].strip().strip("/"))
            continue
        match = re.fullmatch(r"\*\*/([^/]+)/", line)
        if match:
            any_depth.add(match.group(1))
            continue
        match = re.fullmatch(r"([^/]+)/", line)
        if match:
            top_level.add(match.group(1))
    parts = root.relative_to(PROJECT).parts
    for index in range(len(parts) - 1):
        name = parts[index]
        if name not in any_depth and not (index == 0 and name in top_level):
            continue
        prefix = "/".join(parts[:index + 1])
        if any(neg == prefix or neg.startswith(prefix + "/") or prefix.startswith(neg + "/")
               for neg in negations):
            return False
        return True
    return False


def materialize_mini_release(selection, root, launcher):
    root = Path(root)
    release = root / "release"
    if release.exists() or release.is_symlink():
        raise SmokeBlocked(f"Mini release directory already exists: {release}")
    source_paths = {Path(record["record_file"]) for record in selection["records"]}
    retrieval = Path(launcher["retrieval_index"])
    before = {str(path): file_hash(path) for path in source_paths}
    before[str(retrieval)] = file_hash(retrieval)
    before[str(retrieval.with_suffix(".sqlite.manifest.json"))] = file_hash(
        retrieval.with_suffix(".sqlite.manifest.json"))
    mini = make_manifest("train_evolution", 1, selection["tasks"], split_seed=launcher.get("split_seed", 42))
    packed = repack(mini, release / "B1")
    freeze(release / "B1/manifest.json", packed)
    native_index(release, [packed], launcher, link_retrieval=True)
    checks = []
    for record in selection["records"]:
        entry = next(task for task in packed["tasks"] if task["task_id"] == record["task_id"])
        with open(record["record_file"], "rb") as stream:
            stream.seek(record["record_offset"])
            original = stream.read(record["record_length"])
        with open(entry["record_file"], "rb") as stream:
            stream.seek(entry["record_offset"])
            copied = stream.read(entry["record_length"])
        if copied != original:
            raise SmokeBlocked(f"Mini release record bytes differ from the frozen source: {record['task_id']}")
        checks.append({"task_id": record["task_id"], "bytes_identical": True,
                       "record_sha256": entry["record_sha256"]})
    after = {str(path): file_hash(path) for path in source_paths}
    after[str(retrieval)] = file_hash(retrieval)
    after[str(retrieval.with_suffix(".sqlite.manifest.json"))] = file_hash(
        retrieval.with_suffix(".sqlite.manifest.json"))
    if before != after:
        raise SmokeBlocked("Source B1 packs or the retrieval index changed while materializing the mini release")
    return {"release_dir": str(release), "manifest_path": str(release / "B1/manifest.json"),
            "manifest_hash": packed["manifest_hash"], "tasks_sqlite": str(release / "tasks.sqlite"),
            "task_count": len(packed["tasks"]), "record_checks": checks,
            "source_assets_unchanged": True}


def derive_smoke_config(machine_config_path, root, release_dir, project_root):
    root = Path(root)
    launcher = read(machine_config_path)
    smoke_launcher = {**launcher, "rounds": 1, "allocated_tasks": 5, "training_tasks_per_pass": 5,
                      "train_quotas_per_round": dict(SOURCE_QUOTAS),
                      "data_release": "training_side_smoke_mini", "frozen_data_dir": str(release_dir),
                      "output_root": str(root), "run_name": "training_side_smoke",
                      "training_timeout_seconds": launcher.get("training_timeout_seconds", 3600)}
    plan_dir = root / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    smoke_config_path = plan_dir / "smoke_launcher_config.json"
    freeze(smoke_config_path, smoke_launcher)
    adapter = TaskAdapter(project_root, root / "receipts")
    effective = adapter._config({"config_path": str(smoke_config_path)})
    if effective.window_quotas != {"tool_use": 1, "code": 2, "searchqa": 2}:
        raise SmokeBlocked(f"Smoke domain quotas are not 1/2/2: {effective.window_quotas}")
    if effective.max_generations != 1 or sum(effective.window_quotas.values()) != 5:
        raise SmokeBlocked("Smoke effective config must be one round of five tasks")
    effective_path = plan_dir / "effective_config.json"
    freeze(effective_path, effective.model_dump())
    return smoke_config_path, effective_path, effective


def load_effective_config(root):
    """Return a real PipelineConfig so production start_services() can call model_dump()."""
    value = read(Path(root) / "plan/effective_config.json")
    return PipelineConfig.model_validate(value)


def _production_hashes():
    return {name: file_hash(ROOT / name) for name in PRODUCTION_IMPLEMENTATION}


def _bindings(root):
    root = Path(root)
    plan_dir = root / "plan"
    return {"runner_sha256": file_hash(Path(__file__)),
            "smoke_config_sha256": file_hash(plan_dir / "smoke_launcher_config.json"),
            "selection_sha256": file_hash(plan_dir / "selection.json"),
            "effective_config_sha256": file_hash(plan_dir / "effective_config.json"),
            "mini_manifest_hash": read(root / "release/B1/manifest.json")["manifest_hash"],
            "base_checkpoint_manifest_sha256": file_hash(plan_dir / "base_checkpoint_manifest.json"),
            "production_implementation_sha256": _production_hashes()}


def _load_plan(root):
    path = Path(root) / "receipts/plan.json"
    if not path.is_file():
        raise SmokeBlocked(f"No plan receipt; run plan first: {path}")
    return read(path)


def _binding_error(root, plan):
    current = _bindings(root)
    if current != plan["bindings"]:
        changed = sorted(key for key in current if current[key] != plan["bindings"].get(key))
        return f"Stage binding hash mismatch since plan: {changed}"
    return None


def _stage_state(root, stage):
    root = Path(root)
    final = root / "receipts" / f"{stage}.json"
    if final.is_file():
        return "final", read(final)
    if (root / "locks" / f"{stage}.started.json").exists():
        return "started", None
    return "pending", None


@contextlib.contextmanager
def _stage_lock(root):
    """One common mutex for every execute stage; state is re-checked inside the lock."""
    locks = Path(root) / "locks"
    locks.mkdir(parents=True, exist_ok=True)
    lock = locks / "stage.lock"
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise SmokeBlocked("A smoke stage lock is already held; concurrent stages are forbidden")
    os.close(handle)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


@contextlib.contextmanager
def _signal_guard():
    """Turn SIGTERM/SIGINT into an exception so finally-owned cleanup still runs."""
    def _handler(signum, frame):
        raise _Interrupted(f"interrupted by signal {signum}")

    previous = {}
    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, _handler)
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _registry_owned_by_root(config, root):
    """Path containment is necessary but not sufficient: verify the recorded processes too."""
    root = Path(root).resolve()
    registry = Path(registry_path(config)).resolve()
    if not registry.is_relative_to(root):
        return False, "registry file is outside the smoke output root"
    if not registry.exists():
        return True, "no owned registry created"
    try:
        value = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"unreadable registry: {exc}"
    four_gpu = registry.parent / "four_gpu"
    for record in value.get("services", []):
        config_path = Path(str(record.get("config", ""))).resolve()
        if not config_path.is_relative_to(four_gpu.resolve()):
            return False, "recorded serving config is outside this root's four_gpu directory"
        if not config_path.is_file():
            return False, "recorded serving config file is missing"
        serving = json.loads(config_path.read_text(encoding="utf-8"))
        if Path(str(serving.get("output_root"))).resolve() != root:
            return False, "recorded serving config output_root belongs to another root"
        command = record.get("command") or []
        if str(config_path) not in [str(item) for item in command]:
            return False, "recorded launch command does not reference this root's serving config"
        pid = record.get("pid")
        identity = record.get("identity")
        if pid is not None and identity is not None and process_identity(pid) not in (None, identity):
            return False, "recorded service PID identity changed; refusing to signal"
        if pid is not None and process_identity(pid) is not None:
            actual = _live_cmdline(pid)
            if actual is None:
                return False, "cannot read live service cmdline; refusing to signal"
            if str(config_path) not in actual:
                return False, "live service cmdline does not reference this root's serving config"
            if not any(str(item) in actual for item in command if str(item).endswith(".py")):
                return False, "live service cmdline does not match the recorded launcher script"
    return True, "registry processes verified as owned by this smoke root"


def _live_cmdline(pid):
    try:
        raw = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        return None
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _assert_registry_owned(config, root):
    """Must run BEFORE any ensure_services: production ensure may itself stop_services."""
    owned, reason = _registry_owned_by_root(config, root)
    if not owned:
        raise SmokeBlocked(f"Registry ownership check failed before service start: {reason}")


def _cmdline_matches_root(actual, root, request_dir=None):
    if not actual or not any(item.endswith("train_task_meta_sft.py") for item in actual):
        return False
    for index, item in enumerate(actual):
        if item in {"--request-dir", "--runs-dir"} and index + 1 < len(actual):
            if Path(actual[index + 1]).resolve().is_relative_to(root):
                return True
    if request_dir is not None and request_dir in actual:
        return Path(request_dir).resolve().is_relative_to(root)
    return False


def _group_members(pgid):
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        identity = process_identity(int(entry.name))
        if identity is not None and identity.get("pgid") == pgid:
            members.append(int(entry.name))
    return members


def _training_owned_by_root(receipt, root):
    """Verify a recorded DDP/phase process group belongs to this root, even if the leader exited.

    The leader may exit while rank children remain in the same PGID; ownership is then proven
    by a live group member whose cmdline names the trainer and a request/run dir inside root.
    """
    root = Path(root).resolve()
    pid = receipt.get("training_pid")
    if pid is None:
        return False, "no training_pid recorded"
    command = [str(item) for item in (receipt.get("command") or [])]
    request_dir = None
    for index, item in enumerate(command):
        if item == "--request-dir" and index + 1 < len(command):
            request_dir = command[index + 1]
    if request_dir is None or not Path(request_dir).resolve().is_relative_to(root):
        return False, "recorded training request-dir is outside this smoke root"
    identity = process_identity(pid)
    if identity is not None and identity.get("pgid") == pid:
        if _cmdline_matches_root(_live_cmdline(pid), root, request_dir):
            return True, "training process group verified as owned by this smoke root"
    if not _group_alive(pid):
        return False, "training process group is not alive"
    for member in _group_members(pid):
        if _cmdline_matches_root(_live_cmdline(member), root, request_dir):
            return True, "training group owned via live rank member (leader exited)"
    return False, "no live group member proves this root's ownership"


def _group_alive(pgid):
    """True while any NON-ZOMBIE member of the process group is alive (not just the leader)."""
    return bool(_group_members(pgid))


def _discover_owned_training_groups(root):
    """Find owned training PGIDs from ANY live member, even when the leader already exited.

    Scans every live process whose cmdline names the production SFT trainer AND a
    --request-dir/--runs-dir inside this root, then derives its PGID. This covers the window
    where the phase launched DDP but never wrote training_pid and the leader has since exited
    while ranks remain. Ownership is proven per group before any kill; never a global kill.
    """
    root = Path(root).resolve()
    found = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        identity = process_identity(pid)
        if identity is None:
            continue
        actual = _live_cmdline(pid)
        if not _cmdline_matches_root(actual, root):
            continue
        found.setdefault(identity["pgid"], []).append(pid)
    return found


def _discovered_group_owned(pgid, matching_members, root):
    """Re-verify whole-group ownership from the live matching members before killing."""
    if not _group_alive(pgid):
        return False, "discovered training group is not alive"
    for member in matching_members:
        if process_identity(member) is not None and _cmdline_matches_root(_live_cmdline(member), root):
            return True, "discovered group owned via live rank cmdline"
    return False, "no live member proves this root's ownership"


def _kill_owned_group(pgid):
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True, None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and _group_alive(pgid):
        time.sleep(0.2)
    if _group_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return True, None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and _group_alive(pgid):
            time.sleep(0.2)
    return not _group_alive(pgid), None


def _terminate_owned_training(root):
    """Terminate and wait for WHOLE owned training process groups (receipt + discovery)."""
    root = Path(root).resolve()
    result = {"training_terminated": False, "training_owned": None, "error": None,
              "groups": [], "discovered_groups": []}
    pgids = {}
    for path in sorted(root.glob("runs/**/gpu_phase_receipt.json")):
        try:
            receipt = read(path)
        except (OSError, ValueError):
            continue
        if receipt.get("training_pid") is None:
            continue
        owned, reason = _training_owned_by_root(receipt, root)
        result["training_owned"] = owned
        if owned:
            pgids[receipt["training_pid"]] = "receipt"
        else:
            result["error"] = f"refusing to kill training: {reason}"
    discovered = _discover_owned_training_groups(root)
    for pgid, members in discovered.items():
        owned, reason = _discovered_group_owned(pgid, members, root)
        if not owned:
            result["error"] = f"refusing to kill discovered training: {reason}"
            continue
        result["training_owned"] = True
        if pgid not in pgids:
            pgids[pgid] = "discovered"
            result["discovered_groups"].append(pgid)
    if not pgids:
        return result
    all_dead = True
    for pgid, source in pgids.items():
        dead, error = _kill_owned_group(pgid)
        result["groups"].append({"pgid": pgid, "source": source, "terminated": dead})
        all_dead = all_dead and dead
        if error:
            result["error"] = error
    result["training_terminated"] = all_dead
    if not all_dead:
        result["error"] = "owned training process group did not fully exit"
    return result


def cleanup_owned_services(config, root):
    root = Path(root).resolve()
    registry = Path(registry_path(config)).resolve()
    owned, reason = _registry_owned_by_root(config, root)
    result = {"registry_path": str(registry), "registry_owned": owned,
              "owned_registry_created": registry.exists(), "services_stopped": False,
              "four_gpu_memory_released": None, "error": None, "ownership_check": reason}
    if not owned:
        result["error"] = f"Refusing to stop services: {reason}"
        return result
    if not registry.exists():
        result["services_stopped"] = True
        result["note"] = "No owned four-GPU registry was created; nothing to stop"
        return result
    try:
        stop_services(config)
        result["services_stopped"] = True
        result["four_gpu_memory_released"] = True
        result["release_rule"] = "stop_services enforces >=40000 MiB free on each of GPU 0-3"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def _base_receipt(stage, root, plan, bindings):
    return {"schema_version": 1, "stage": stage, "output_root": str(root),
            "bindings": bindings, "parent_manifest_hash": plan["parent_manifest_hash"],
            "mini_manifest_hash": plan["release"]["manifest_hash"], **SMOKE_FLAGS}


def _finish(root, stage, receipt, cleanup):
    if not cleanup["services_stopped"] or cleanup["error"]:
        receipt["status"] = "incomplete"
        receipt["cleanup_failure"] = cleanup["error"] or "owned services were not confirmed stopped"
    receipt["cleanup"] = cleanup
    freeze(Path(root) / "receipts" / f"{stage}.json", receipt)
    (Path(root) / "locks" / f"{stage}.started.json").unlink(missing_ok=True)
    return receipt


def _cached_result(root, stage, plan):
    """Re-verify binding and preserve failure semantics; never report a failed cache as success."""
    state, previous = _stage_state(root, stage)
    if state != "final":
        return None
    error = _binding_error(root, plan)
    if error:
        return {"status": "requires_audit", "stage": stage, "error": error,
                "previous_status": previous["status"]}
    if previous["status"] in SUCCESS_STATUSES:
        return {"status": "already_completed", "stage": stage, "previous_status": previous["status"]}
    return {"status": previous["status"], "stage": stage, "cached_failure": True,
            "error": previous.get("error", "previous stage did not succeed")}


def plan(output_root, machine_config_path, parent_manifest_path, project_root=None):
    project_root = Path(project_root).resolve() if project_root else (ROOT / "RSIFlow").resolve()
    validation = _validate_output_root(output_root)
    root = Path(validation["path"])
    root.mkdir(parents=True)
    parent = read(parent_manifest_path)
    selection = select_training_subset(parent)
    launcher = read(machine_config_path)
    release = materialize_mini_release(selection, root, launcher)
    smoke_config_path, effective_config_path, effective = derive_smoke_config(
        machine_config_path, root, release["release_dir"], project_root)
    base_checkpoint = Path(effective.task_checkpoint)
    base_manifest = checkpoint_manifest(base_checkpoint)
    plan_dir = root / "plan"
    freeze(plan_dir / "selection.json", selection)
    freeze(plan_dir / "base_checkpoint_manifest.json", base_manifest)
    bindings = _bindings(root)
    receipt = {"schema_version": 1, "stage": "plan", "status": "planned",
               "output_root": str(root), "output_root_validation": validation,
               "project_root": str(project_root),
               "selection_path": str(plan_dir / "selection.json"),
               "smoke_config_path": str(smoke_config_path),
               "effective_config_path": str(effective_config_path),
               "parent_manifest_path": str(Path(parent_manifest_path).resolve()),
               "parent_manifest_hash": parent["manifest_hash"],
               "release": release, "bindings": bindings, "model_calls": 0,
               "base_checkpoint": str(base_checkpoint),
               "selection_rule": SELECTION_RULE, **SMOKE_FLAGS}
    freeze(root / "receipts/plan.json", receipt)
    return receipt


def _persisted_rollout_receipts(run_dir):
    """Index the production per-task rollout receipts ({binding, row}) by task_id."""
    receipts = {}
    for path in sorted(Path(run_dir).glob("rollout_cache/*/train_rollouts/*.json")):
        if path.name.endswith(".calls") or ".calls" in path.name:
            continue
        value = read(path)
        row = value.get("row") or {}
        if row.get("task_id"):
            receipts[row["task_id"]] = {"path": str(path), "binding": value.get("binding", {}), "row": row}
    return receipts


def _message_has_evidence(message):
    """A real model message has non-empty text OR a non-empty structured tool_calls list.

    Production qwen_message() emits content='' for a pure tool-call generation; that is a
    valid EnvScaler response and must not be rejected. Only both-empty is an evidence failure.
    """
    if not isinstance(message, dict):
        return False
    if str(message.get("content", "")).strip():
        return True
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return False
    for call in calls:
        function = (call or {}).get("function") or {}
        if not isinstance(function.get("name"), str) or not function["name"].strip():
            return False
        if "arguments" not in function:
            return False
    return True


def _validate_transport_calls(row):
    calls = row.get("transport_calls")
    if not isinstance(calls, list) or not calls:
        raise SmokeBlocked(f"No persisted transport calls: {row.get('task_id')}")
    for call in calls:
        if call.get("status") != "completed":
            raise SmokeBlocked(f"Transport call not completed: {row.get('task_id')}")
        message = (call.get("response") or {}).get("message")
        if not _message_has_evidence(message):
            raise SmokeBlocked(f"Transport call lacks a real model message: {row.get('task_id')}")


def _validate_parent_rows(rows, mini_manifest_hash, expected_ids, coverage_of, receipts):
    if len(rows) != 5 or len({row.get("task_id") for row in rows}) != 5:
        raise SmokeBlocked("Parent rollout must contain exactly five unique tasks")
    coverage = Counter()
    failures = 0
    for row in rows:
        task_id = row.get("task_id")
        if task_id not in expected_ids:
            raise SmokeBlocked(f"Rollout task is outside the frozen mini B1: {task_id}")
        if (row.get("source_role") != "train_evolution" or row.get("purpose") != "evolution_train"
                or row.get("round_id") != 1 or row.get("collection_stage") != "parent_pre_update"
                or row.get("branch_id") != "parent"):
            raise SmokeBlocked(f"Rollout row has wrong role/purpose/round/stage/branch: {task_id}")
        if row.get("manifest_hash") != mini_manifest_hash:
            raise SmokeBlocked(f"Rollout row is bound to a different manifest: {task_id}")
        if row.get("infrastructure_error") or row.get("execution_error_type"):
            raise SmokeBlocked(f"Unexplained infrastructure failure: {task_id}")
        verification = row.get("verification")
        if not isinstance(verification, dict) or verification.get("status") != "completed":
            raise SmokeBlocked(f"Missing or incomplete scoring receipt: {task_id}")
        if not isinstance(verification.get("success"), bool):
            raise SmokeBlocked(f"Scoring success is not a boolean: {task_id}")
        if not isinstance(row.get("model_call_count"), int) or row["model_call_count"] < 1:
            raise SmokeBlocked(f"No real local model call recorded: {task_id}")
        _validate_transport_calls(row)
        receipt = receipts.get(task_id)
        if receipt is None:
            raise SmokeBlocked(f"Missing persisted per-task rollout receipt: {task_id}")
        scope = (receipt["binding"].get("round_scope") or {})
        if scope.get("manifest_hash") != mini_manifest_hash:
            raise SmokeBlocked(f"Persisted receipt bound to a different manifest: {task_id}")
        if not receipt["binding"].get("state_hash"):
            raise SmokeBlocked(f"Persisted receipt lacks a state binding: {task_id}")
        if len(receipt["row"].get("transport_calls") or []) != row["model_call_count"]:
            raise SmokeBlocked(f"Persisted receipt call count disagrees with the row: {task_id}")
        coverage[coverage_of[task_id]] += 1
        if verification["success"] is not True:
            failures += 1
    if set(coverage) != set(COVERAGE_KEYS) or any(count != 1 for count in coverage.values()):
        raise SmokeBlocked(f"Rollout coverage is not exactly one per required kind: {dict(coverage)}")
    return coverage, failures


def _base_config_path(plan_receipt):
    launcher = read(Path(plan_receipt["output_root"]) / "plan/smoke_launcher_config.json")
    base = Path(launcher["base_config"])
    if not base.is_absolute():
        base = Path(plan_receipt["project_root"]) / launcher.get("runtime_source", "runtime") / base
    return base.resolve()


def _training_input_hashes(root, plan_receipt, state_path):
    """Seal every input the production SFT re-reads: a0 state, base config, launcher, harness."""
    root = Path(root)
    state = read(state_path)
    return {"a0_state": file_hash(state_path),
            "a0_checkpoint": state.get("checkpoint_path"),
            "a0_harness_path": state.get("harness_path"),
            "a0_harness_sha256": file_hash(Path(state["harness_path"])),
            "base_config": file_hash(_base_config_path(plan_receipt)),
            "smoke_launcher": file_hash(root / "plan/smoke_launcher_config.json"),
            "effective_config": file_hash(root / "plan/effective_config.json"),
            "base_checkpoint_manifest": file_hash(root / "plan/base_checkpoint_manifest.json")}


def _seal_rollout_evidence(root, parent_dir, run_dir, receipts, plan_receipt, state_path):
    sealed = {"train_trajectories": file_hash(parent_dir / "train_trajectories.jsonl"),
              "performance": file_hash(parent_dir / "performance.json"),
              "production_implementation": _production_hashes(),
              "training_inputs": _training_input_hashes(root, plan_receipt, state_path),
              "rollout_receipts": {task_id: file_hash(item["path"]) for task_id, item in receipts.items()}}
    for name in ("cost.json", "coverage.json"):
        if (parent_dir / name).is_file():
            sealed[name.split(".")[0]] = file_hash(parent_dir / name)
    return sealed


def _verify_rollout_evidence(root, rollout, plan_receipt):
    sealed = rollout.get("evidence_hashes") or {}
    parent_dir = Path(rollout["parent_dir"])
    current = {"train_trajectories": file_hash(parent_dir / "train_trajectories.jsonl"),
               "performance": file_hash(parent_dir / "performance.json"),
               "production_implementation": _production_hashes(),
               "training_inputs": _training_input_hashes(root, plan_receipt, rollout["state_path"]),
               "rollout_receipts": {task_id: file_hash(path) for task_id, path in
                                   (rollout.get("receipt_paths") or {}).items()}}
    for name in ("cost", "coverage"):
        if name in sealed:
            current[name] = file_hash(parent_dir / f"{name}.json")
    if current != sealed:
        changed = sorted(key for key in current if current[key] != sealed.get(key))
        raise SmokeBlocked(f"Parent rollout evidence changed since the rollout receipt: {changed}")


def execute_rollout(output_root):
    root = Path(output_root)
    plan_receipt = _load_plan(root)
    cached = _cached_result(root, "rollout", plan_receipt)
    if cached is not None:
        return cached
    with _stage_lock(root):
        state, _ = _stage_state(root, "rollout")
        if state == "final":
            return _cached_result(root, "rollout", plan_receipt)
        if state == "started":
            return {"status": "requires_audit", "stage": "rollout",
                    "error": "A previous rollout started without a final receipt; audit before any rerun"}
        error = _binding_error(root, plan_receipt)
        if error:
            return {"status": "requires_audit", "stage": "rollout", "error": error}
        (root / "locks/rollout.started.json").write_text(
            json.dumps({"stage": "rollout", "started_at": time.time()}), encoding="utf-8")
        config = load_effective_config(root)
        receipt = _base_receipt("rollout", root, plan_receipt, plan_receipt["bindings"])
        receipt["status"] = "incomplete"
        cleanup = {"services_stopped": False, "error": "cleanup not reached"}
        try:
            with _signal_guard():
                adapter = TaskAdapter(plan_receipt["project_root"], root / "receipts")
                run_dir = root / "runs/training_side_smoke"
                state_path = run_dir / "a0.json"
                run_dir.mkdir(parents=True, exist_ok=True)
                adapter.bootstrap({"config_path": str(root / "plan/smoke_launcher_config.json"),
                                   "checkpoint_path": str(config.task_checkpoint),
                                   "seed_harness": str(Path(plan_receipt["project_root"]) /
                                                       "seed_harness/rsiflow_base_manifest.json"),
                                   "state_path": str(state_path)})
                _assert_registry_owned(config, root)
                ensure_services(config, config.task_checkpoint)
                parent_dir = run_dir / "parent"
                adapter.rollout({"config_path": str(root / "plan/smoke_launcher_config.json"),
                                 "state_path": str(state_path), "stage": "parent", "round_number": 1,
                                 "ensure_services": False, "output_dir": str(parent_dir)})
                rows = [json.loads(line) for line in
                        (parent_dir / "train_trajectories.jsonl").read_text(encoding="utf-8").splitlines()
                        if line.strip()]
                selection = read(root / "plan/selection.json")
                expected = set(selection["task_ids"])
                coverage_of = {task["task_id"]: _coverage_key(task) for task in selection["tasks"]}
                receipts = _persisted_rollout_receipts(run_dir)
                coverage, failures = _validate_parent_rows(
                    rows, plan_receipt["release"]["manifest_hash"], expected, coverage_of, receipts)
                receipt.update(status="completed_with_model_failures" if failures else "completed",
                               coverage=dict(coverage), model_failures=failures,
                               trajectories_path=str(parent_dir / "train_trajectories.jsonl"),
                               performance_path=str(parent_dir / "performance.json"),
                               parent_dir=str(parent_dir), run_dir=str(run_dir), state_path=str(state_path),
                               receipt_paths={task_id: item["path"] for task_id, item in receipts.items()},
                               evidence_hashes=_seal_rollout_evidence(root, parent_dir, run_dir, receipts,
                                                                      plan_receipt, state_path),
                               positive_candidates=len(select_positive_rows(rows, profile="multidomain")))
        except Exception as exc:
            receipt["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            cleanup = cleanup_owned_services(config, root)
        return _finish(root, "rollout", receipt, cleanup)


def _validate_sft_evidence(root, config, plan_receipt, sft_result, parent_state_path, n_positives):
    """Validate against the production trainer/GPU contracts; return problems before any service call."""
    output = Path(sft_result["candidate_state_path"]).parent
    child_state = read(Path(sft_result["candidate_state_path"]))
    child = Path(child_state["checkpoint_path"]).resolve()
    base = Path(config.task_checkpoint).resolve()
    metrics = read(output / "model_update/training_metrics.json")
    gpu = read(output / "model_update/gpu_phase_receipt.json")
    problems = []
    if child == base:
        problems.append("child checkpoint equals the base checkpoint")
    if not child.is_relative_to(Path(root).resolve()):
        problems.append("child checkpoint is outside the smoke output root")
    if checkpoint_manifest(base) != read(root / "plan/base_checkpoint_manifest.json"):
        problems.append("base checkpoint manifest changed during SFT")
    base_weights = {entry["sha256"] for entry in read(root / "plan/base_checkpoint_manifest.json")}
    child_weights = {entry["sha256"] for entry in child_state.get("checkpoint_manifest", [])}
    if not child_weights or child_weights == base_weights:
        problems.append("child weights did not change")
    if metrics.get("world_size") != 4:
        problems.append("SFT world_size is not 4")
    rank_records = metrics.get("rank_records") or []
    ranks = sorted(record.get("rank") for record in rank_records if isinstance(record, dict))
    if ranks != [0, 1, 2, 3]:
        problems.append("SFT rank_records do not cover exactly ranks {0,1,2,3}")
    # Production sampling contract: sft_positive.jsonl is length-filtered upstream, then the
    # trainer deduplicates (sft_selection.json) and DistributedSampler pads to a multiple of
    # world. Coverage is the UNION of sampled indices over the FINAL dataset; padding repeats
    # are legal. We must not demand the pre-filter positive count or global uniqueness.
    request_dir = output / "model_update"
    selection_path = request_dir / "sft_selection.json"
    positive_path = request_dir / "sft_positive.jsonl"
    n_final = metrics.get("positive_trajectories")
    if not isinstance(n_final, int) or isinstance(n_final, bool) or n_final <= 0:
        problems.append("SFT final training dataset size (positive_trajectories) is missing or empty")
        n_final = None
    # The production filtering/dedup evidence is MANDATORY and must reconcile; a missing or
    # empty selection/positive file may never silently skip the cross-check.
    selection = read(selection_path) if selection_path.is_file() else None
    if not isinstance(selection, dict) or not selection:
        problems.append("SFT sft_selection.json evidence is missing or empty")
        selection = {}
    if not positive_path.is_file():
        problems.append("SFT sft_positive.jsonl evidence is missing")
    else:
        input_rows = sum(1 for line in positive_path.read_text(encoding="utf-8").splitlines() if line.strip())
        if selection.get("input_rows") != input_rows:
            problems.append("SFT selection input_rows disagrees with sft_positive.jsonl")
    if selection:
        if selection.get("unique_rows") != n_final:
            problems.append("SFT selection unique_rows disagrees with positive_trajectories")
        if not isinstance(selection.get("input_rows"), int) or not isinstance(selection.get("unique_rows"), int):
            problems.append("SFT selection input_rows/unique_rows evidence is missing")
        elif selection["input_rows"] < selection["unique_rows"]:
            problems.append("SFT selection unique_rows exceeds input_rows")
    if n_final and n_positives and n_final > n_positives:
        problems.append("SFT final dataset larger than the eligible parent positives")
    sampled = []
    for record in rank_records:
        indices = record.get("sampled_indices")
        if not isinstance(indices, list):
            problems.append("SFT rank_records have missing sampled_indices")
            continue
        sampled.extend(indices)
    if n_final:
        if not sampled:
            problems.append("SFT has no sampled training examples")
        elif any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < n_final for i in sampled):
            problems.append("SFT sampled_indices contain non-integer or out-of-range entries")
        elif set(sampled) != set(range(n_final)):
            problems.append("SFT one-epoch sampling did not visit every eligible training sample")
    if metrics.get("num_train_epochs_completed") != 1.0:
        problems.append("SFT did not complete exactly one epoch")
    loss = metrics.get("training_loss")
    if isinstance(loss, bool) or not isinstance(loss, (int, float)) or not math.isfinite(loss):
        problems.append("non-finite training_loss")
    if not isinstance(metrics.get("changed_lora_tensors"), int) or metrics["changed_lora_tensors"] <= 0:
        problems.append("no LoRA tensors changed")
    if not isinstance(metrics.get("merged_probe_changed_elements"), int) or metrics["merged_probe_changed_elements"] <= 0:
        problems.append("merged probe weight did not change")
    resolved = metrics.get("resolved_training_config") or metrics.get("resolved_training")
    if not isinstance(resolved, dict) or not resolved:
        problems.append("SFT resolved_training_config evidence is missing")
    else:
        for key, value in SFT_PRESET_EXPECTED.items():
            if key not in resolved:
                problems.append(f"SFT resolved training config missing preset evidence: {key}")
            elif resolved[key] != value:
                problems.append(f"SFT preset overridden in resolved training config: {key}")
        if resolved.get("lora_target_modules") != TARGET_MODULES:
            problems.append("SFT target modules were overridden or missing")
    if gpu.get("status") != "completed":
        problems.append("gpu_phase_receipt is not completed")
    if Path(str(gpu.get("restored_checkpoint"))).resolve() != child:
        problems.append("restored checkpoint is not the merged child")
    if sorted(gpu.get("replica_gpus") or []) != [0, 1, 2, 3]:
        problems.append("restored replica_gpus are not GPU 0-3")
    parent_state = read(parent_state_path)
    parent_harness = Path(parent_state["harness_path"])
    child_harness = Path(child_state["harness_path"])
    # Production clone_task() copies the Harness into the child directory: identity is the
    # basename plus content hash, not path equality.
    if child_harness.name != parent_harness.name or file_hash(child_harness) != file_hash(parent_harness):
        problems.append("Harness identity/content changed during SFT")
    parent_artifacts = (parent_state.get("artifacts") or {}).get("manifest")
    child_artifacts = (child_state.get("artifacts") or {}).get("manifest")
    if parent_artifacts != child_artifacts:
        problems.append("Task artifact manifest changed during SFT")
    return problems, child, child_state, metrics, gpu


def _short_generation(config, state_value):
    from types import SimpleNamespace
    state = SimpleNamespace(**state_value)
    client = LocalTaskClient(state, config.task_base_url, timeout=config.task_timeout)
    response = client([{"role": "user", "content": "Reply with exactly RSIFLOW_SMOKE_OK"}],
                      seed=42, max_tokens=16, temperature=0)
    message = response["message"]
    if not _message_has_evidence(message):
        raise SmokeBlocked("Short generation returned no real model message")
    content = message.get("content")
    answer = content if isinstance(content, str) and content.strip() else json.dumps(
        message.get("tool_calls"), ensure_ascii=False)
    return {"answer": answer, "usage": response.get("usage"), "model_calls": 1}


def _revalidate_parent_for_sft(root, rollout, plan_receipt):
    """Re-verify the sealed five-row parent evidence before any positive selection."""
    _verify_rollout_evidence(root, rollout, plan_receipt)
    parent_dir = Path(rollout["parent_dir"])
    rows = [json.loads(line) for line in
            (parent_dir / "train_trajectories.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    selection = read(root / "plan/selection.json")
    expected = set(selection["task_ids"])
    coverage_of = {task["task_id"]: _coverage_key(task) for task in selection["tasks"]}
    receipts = _persisted_rollout_receipts(Path(rollout["run_dir"]))
    _validate_parent_rows(rows, rollout["mini_manifest_hash"], expected, coverage_of, receipts)
    return rows


def execute_sft(output_root):
    root = Path(output_root)
    plan_receipt = _load_plan(root)
    rollout_state, rollout = _stage_state(root, "rollout")
    if rollout_state != "final" or rollout["status"] not in SUCCESS_STATUSES:
        raise SmokeBlocked("SFT requires a completed parent rollout receipt")
    cached = _cached_result(root, "sft", plan_receipt)
    if cached is not None:
        return cached
    with _stage_lock(root):
        state, _ = _stage_state(root, "sft")
        if state == "final":
            return _cached_result(root, "sft", plan_receipt)
        if state == "started":
            return {"status": "requires_audit", "stage": "sft",
                    "error": "A previous SFT started without a final receipt; audit before any rerun"}
        error = _binding_error(root, plan_receipt)
        if error:
            return {"status": "requires_audit", "stage": "sft", "error": error}
        (root / "locks/sft.started.json").write_text(
            json.dumps({"stage": "sft", "started_at": time.time()}), encoding="utf-8")
        config = load_effective_config(root)
        receipt = _base_receipt("sft", root, plan_receipt, plan_receipt["bindings"])
        receipt["status"] = "incomplete"
        cleanup = {"services_stopped": False, "error": "cleanup not reached"}
        try:
            with _signal_guard():
                rows = _revalidate_parent_for_sft(root, rollout, plan_receipt)
                positives = select_positive_rows(rows, profile="multidomain")
                receipt["positive_samples"] = len(positives)
                if not positives:
                    receipt["status"] = "blocked_no_positive_parent_trajectory"
                    receipt["error"] = ("No natural positive parent trajectory; SFT is blocked. No task "
                                        "substitution, synthetic label or held-out borrowing is allowed.")
                else:
                    # S1 finally stopped the base services. S2 must verify registry ownership and
                    # re-start + verify the four base replicas BEFORE the production training phase
                    # (which itself calls stop_services) runs. Reject a replaced registry first.
                    _assert_registry_owned(config, root)
                    ensure_services(config, config.task_checkpoint)
                    base_records = verify_services(config, config.task_checkpoint)
                    if sorted(record.get("gpu") for record in base_records) != [0, 1, 2, 3]:
                        raise SmokeBlocked("S2 did not verify four base replicas on GPU 0-3 before training")
                    adapter = TaskAdapter(plan_receipt["project_root"], root / "receipts")
                    sft_dir = root / "runs/training_side_smoke/sft"
                    result = adapter.sft({"config_path": str(root / "plan/smoke_launcher_config.json"),
                                          "state_path": rollout["state_path"],
                                          "parent_rollout_dir": rollout["parent_dir"],
                                          "output_dir": str(sft_dir)})
                    problems, child, child_state, metrics, gpu = _validate_sft_evidence(
                        root, config, plan_receipt, result, rollout["state_path"], len(positives))
                    if problems:
                        receipt["status"] = "incomplete"
                        receipt["error"] = "; ".join(problems)
                    else:
                        _assert_registry_owned(config, root)
                        ensure_services(config, str(child))
                        records = verify_services(config, str(child))
                        if sorted(record.get("gpu") for record in records) != [0, 1, 2, 3]:
                            raise SmokeBlocked("verify_services did not confirm four child replicas on GPU 0-3")
                        generation = _short_generation(config, child_state)
                        receipt.update(status="completed", world_size=metrics["world_size"],
                                       ranks=sorted(r["rank"] for r in metrics["rank_records"]),
                                       base_checkpoint_unchanged=True, child_weights_changed=True,
                                       restored_checkpoint="child", child_checkpoint=str(child),
                                       candidate_state_path=result["candidate_state_path"],
                                       training_metrics_path=result["training_metrics_path"],
                                       gpu_phase_receipt_path=str(sft_dir / "model_update/gpu_phase_receipt.json"),
                                       training_metrics_sha256=file_hash(Path(result["training_metrics_path"])),
                                       gpu_phase_receipt_sha256=file_hash(sft_dir / "model_update/gpu_phase_receipt.json"),
                                       candidate_state_sha256=file_hash(Path(result["candidate_state_path"])),
                                       short_generation=generation)
        except Exception as exc:
            receipt["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            receipt["training_cleanup"] = _terminate_owned_training(root)
            cleanup = cleanup_owned_services(config, root)
        return _finish(root, "sft", receipt, cleanup)


def execute_recovery(output_root):
    root = Path(output_root)
    plan_receipt = _load_plan(root)
    sft_state, sft = _stage_state(root, "sft")
    if sft_state != "final" or sft["status"] != "completed":
        raise SmokeBlocked("Recovery requires a completed SFT receipt with a merged child checkpoint")
    cached = _cached_result(root, "recovery", plan_receipt)
    if cached is not None:
        return cached
    with _stage_lock(root):
        state, _ = _stage_state(root, "recovery")
        if state == "final":
            return _cached_result(root, "recovery", plan_receipt)
        if state == "started":
            return {"status": "requires_audit", "stage": "recovery",
                    "error": "A previous recovery started without a final receipt; audit before any rerun"}
        error = _binding_error(root, plan_receipt)
        if error:
            return {"status": "requires_audit", "stage": "recovery", "error": error}
        (root / "locks/recovery.started.json").write_text(
            json.dumps({"stage": "recovery", "started_at": time.time()}), encoding="utf-8")
        config = load_effective_config(root)
        receipt = _base_receipt("recovery", root, plan_receipt, plan_receipt["bindings"])
        receipt["status"] = "incomplete"
        cleanup = {"services_stopped": False, "error": "cleanup not reached"}
        try:
            with _signal_guard():
                unchanged = (file_hash(Path(sft["training_metrics_path"])) == sft["training_metrics_sha256"]
                             and file_hash(Path(sft["gpu_phase_receipt_path"])) == sft["gpu_phase_receipt_sha256"]
                             and file_hash(Path(sft["candidate_state_path"])) == sft["candidate_state_sha256"])
                receipt["training_receipts_unchanged"] = unchanged
                if not unchanged:
                    raise SmokeBlocked("Training receipts changed since SFT; recovery must not retrain")
                child_state = read(Path(sft["candidate_state_path"]))
                child = child_state["checkpoint_path"]
                _assert_registry_owned(config, root)
                ensure_services(config, child)
                records = verify_services(config, child)
                if sorted(record.get("gpu") for record in records) != [0, 1, 2, 3]:
                    raise SmokeBlocked("Recovery did not confirm four child replicas on GPU 0-3")
                receipt.update(status="completed", child_checkpoint=child,
                               short_generation=_short_generation(config, child_state),
                               retrained=False)
        except Exception as exc:
            receipt["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            cleanup = cleanup_owned_services(config, root)
        return _finish(root, "recovery", receipt, cleanup)


def status(output_root):
    root = Path(output_root)
    stages = {}
    for stage in ("plan", "rollout", "sft", "recovery"):
        state, receipt = _stage_state(root, stage)
        stages[stage] = receipt["status"] if state == "final" else (
            "requires_audit" if state == "started" else "pending")
    return {"output_root": str(root), "stages": stages, **SMOKE_FLAGS}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "execute-rollout", "execute-sft",
                                            "execute-recovery", "status"])
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--machine-config", type=Path)
    parser.add_argument("--parent-manifest", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            if not args.machine_config or not args.parent_manifest:
                raise SmokeBlocked("plan requires --machine-config and --parent-manifest")
            receipt = plan(args.output_root, args.machine_config, args.parent_manifest)
        elif args.command == "execute-rollout":
            receipt = execute_rollout(args.output_root)
        elif args.command == "execute-sft":
            receipt = execute_sft(args.output_root)
        elif args.command == "execute-recovery":
            receipt = execute_recovery(args.output_root)
        else:
            receipt = status(args.output_root)
    except SmokeBlocked as exc:
        print(json.dumps({"ok": False, "status": "blocked", "error": str(exc)}, sort_keys=True))
        return 1
    ok = receipt.get("status") in {"planned", "completed", "completed_with_model_failures",
                                   "already_completed", "pending"}
    print(json.dumps({"ok": ok, "command": args.command, "status": receipt.get("status"),
                      "output_root": str(args.output_root)}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
