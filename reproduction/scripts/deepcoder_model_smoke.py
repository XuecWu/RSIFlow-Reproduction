"""Plan and execute an isolated MODEL smoke from a sealed prior A0/parent run."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "RSIFlow"
for entry in (PROJECT, PROJECT / "runtime", ROOT / "reproduction/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import training_side_smoke as side
from component_versions import write_json
from evolution_protocol import file_hash, fingerprint, read
from launch_meta import _read_storage_owner
from rsiflow.task_meta.durable import load_task, task_hash
from rsiflow.task_meta.evolution_protocol import stage_manifest, training_provenance
from rsiflow.task_meta.gpu_phases import ensure_services, registry_path, stop_services, verify_services
from rsiflow.task_meta.pipeline import PipelineConfig
from rsiflow.task_meta.sft import select_positive_rows
from rsiflow.task_meta.storage import checkpoint_manifest
from rsiflow.task_meta.task_client import LocalTaskClient
from task_adapter import TaskAdapter
from training_side_smoke import (
    SmokeBlocked,
    _assert_registry_owned,
    _short_generation,
    _signal_guard,
    _stage_lock,
    _terminate_owned_training as _terminate_owned_training_base,
    _validate_output_root,
    _validate_sft_evidence,
    _validate_transport_calls,
    cleanup_owned_services,
    derive_smoke_config,
)

SMOKE_FLAGS = {
    "smoke_only": True,
    "formal_round_completed": False,
    "eligible_for_meta": False,
    "eligible_for_candidate_acceptance": False,
    "feedback_to_training_or_meta": False,
    "is_benchmark_score": False,
}


def _confirmed_exited_training(receipt, root):
    pid = receipt.get("training_pid")
    if type(pid) is not int or pid <= 0:
        return False
    command = [str(item) for item in (receipt.get("command") or [])]
    request_dir = None
    for index, item in enumerate(command):
        if item == "--request-dir" and index + 1 < len(command):
            request_dir = command[index + 1]
    if request_dir is None or not Path(request_dir).resolve().is_relative_to(Path(root).resolve()):
        return False
    if not side._cmdline_matches_root(command, Path(root).resolve(), request_dir):
        return False
    return side.process_identity(pid) is None and not side._group_alive(pid)


def _terminate_owned_training(root):
    result = _terminate_owned_training_base(root)
    receipts = []
    for path in sorted(Path(root).glob("runs/**/gpu_phase_receipt.json")):
        try:
            receipt = read(path)
        except (OSError, ValueError):
            continue
        if receipt.get("training_pid") is not None:
            receipts.append(receipt)
    terminated = {group["pgid"] for group in result["groups"] if group.get("terminated")}
    exited = []
    unresolved = False
    for receipt in receipts:
        pid = receipt["training_pid"]
        if pid in terminated:
            continue
        if _confirmed_exited_training(receipt, root):
            exited.append(pid)
        else:
            unresolved = True
    if side._discover_owned_training_groups(root):
        unresolved = True
    if not unresolved:
        result["error"] = None
        result["training_terminated"] = True
        if exited:
            result["training_owned"] = True
    result["confirmed_exited_groups"] = sorted(set(exited))
    return result


def _validate_central_meta_link(source_run):
    source_run = Path(source_run).resolve()
    link = source_run / "meta"
    target_text = os.readlink(link)
    target = Path(target_text)
    if not target.is_absolute():
        target = link.parent / target
    if target.is_symlink() or target.parent.is_symlink():
        raise SmokeBlocked("Central meta link must have one direct, non-symlink target")
    resolved = link.resolve(strict=True)
    if resolved.name != "meta" or resolved.parent.name != source_run.name or not resolved.is_dir():
        raise SmokeBlocked("Source meta link is not the production central-storage shape")
    owner = resolved.parent / ".run-owner.json"
    expected = {"schema_version": 1, "run_dir": str(source_run)}
    if _read_storage_owner(owner) != expected:
        raise SmokeBlocked("Central meta link owner does not bind this source run")
    identity = resolved.stat()
    return {"schema_version": 1, "target_text": target_text, "resolved_path": str(resolved),
            "st_dev": identity.st_dev, "st_ino": identity.st_ino,
            "owner_path": str(owner), "owner_sha256": file_hash(owner)}


def _source_hashes(source_run):
    source_run = Path(source_run)
    if source_run.is_symlink() or not source_run.is_dir():
        raise SmokeBlocked(f"Source run must be a real directory: {source_run}")
    hashes = {}
    meta_link = source_run / "meta"
    for path in sorted(source_run.rglob("*")):
        if path == meta_link and path.is_symlink():
            seal = _validate_central_meta_link(source_run)
            hashes[str(path)] = fingerprint(seal)
            continue
        if path.is_symlink():
            raise SmokeBlocked(f"Source run contains a symlink: {path}")
        if path.is_file():
            hashes[str(path)] = file_hash(path)
    if not hashes:
        raise SmokeBlocked("Source run is empty")
    return hashes


def _read_rows(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _matching_receipt(source_run, row):
    matches = []
    for path in sorted(Path(source_run).glob("round_1/rollout_cache/*/train_rollouts/*.json")):
        if ".calls" in path.name:
            continue
        value = read(path)
        if value.get("row") == row:
            matches.append((path, value))
    if not matches:
        raise SmokeBlocked(f"Missing exact persisted parent receipt: {row.get('task_id')}")
    return matches[0]


def _file_tree_seals(path):
    path = Path(path).resolve()
    if path.is_symlink() or not path.exists():
        raise SmokeBlocked(f"Sealed A0 input is unavailable or a symlink: {path}")
    if path.is_file():
        return {str(path): file_hash(path)}
    seals = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise SmokeBlocked(f"Sealed A0 input tree contains a symlink: {item}")
        if item.is_file():
            seals[str(item)] = file_hash(item)
    return seals


def _completed_turn(source_run, operation):
    matches = []
    for command_path in sorted((source_run / "meta_session/turns").glob("*/command.json")):
        command = read(command_path)
        request = command.get("request") or {}
        if command.get("action") != "tool" or request.get("operation") != operation:
            continue
        receipt_path = command_path.with_name("receipt.json")
        if not receipt_path.is_file():
            continue
        receipt = read(receipt_path)
        if receipt.get("operation") == operation and receipt.get("status") == "completed":
            matches.append((command_path.parent, request, receipt))
    if len(matches) != 1:
        raise SmokeBlocked(f"MODEL import requires exactly one completed {operation} source turn")
    return matches[0]


def _a0_input_seals(state, parent_harness_path=None):
    checkpoint = _file_tree_seals(Path(state.checkpoint_path))
    harness = _file_tree_seals(Path(state.harness_path))
    if parent_harness_path is not None:
        harness.update(_file_tree_seals(Path(parent_harness_path)))
    artifacts = (_file_tree_seals(Path(state.artifacts.directory))
                 if state.artifacts.directory else {})
    provenance = {}
    if state.artifacts.directory:
        path = Path(state.artifacts.directory).resolve().parent / "artifact_provenance.json"
        if path.is_file():
            provenance = _file_tree_seals(path)
    if not checkpoint or not harness:
        raise SmokeBlocked("A0 checkpoint and Harness seals must not be empty")
    return {"checkpoint": checkpoint, "harness": harness, "artifacts": artifacts,
            "artifact_provenance": provenance}


def _verify_input_seals(source):
    state = load_task(read(Path(source["state_path"])))
    current = _a0_input_seals(state, source["parent_harness_path"])
    if current != source["input_seals"]:
        changed = sorted(key for key in current if current[key] != source["input_seals"].get(key))
        raise SmokeBlocked(f"External A0 input seals changed: {changed}")
    return True


def _validate_source(source_run, release_dir):
    source_run = Path(source_run).resolve()
    release_dir = Path(release_dir).resolve()
    run_state = read(source_run / "meta_session/state.json")
    if run_state.get("status") != "complete":
        raise SmokeBlocked("MODEL import requires source state.status=complete")

    a0_dir = source_run / "snapshots/A0"
    snapshot_path = a0_dir / "snapshot_receipt.json"
    snapshot = read(snapshot_path)
    state_path = a0_dir / "task/task_state_snapshot.json"
    selected = (snapshot.get("combinations") or {}).get("selected") or {}
    if (snapshot.get("status") != "snapshotted"
            or Path(snapshot.get("destination", "")).resolve() != a0_dir
            or Path(snapshot.get("restorable_task_state_path", "")).resolve() != state_path
            or Path(selected.get("state_path", "")).resolve() != state_path):
        raise SmokeBlocked("Source state must come only from the canonical A0 snapshot receipt")
    sealed_state = (snapshot.get("files_sha256") or {}).get("task/task_state_snapshot.json")
    if not sealed_state or sealed_state != file_hash(state_path):
        raise SmokeBlocked("A0 snapshot state hash does not match its snapshot receipt")
    state_value = read(state_path)
    state = load_task(state_value)
    if state.generation != 0:
        raise SmokeBlocked("Canonical A0 Task must have generation=0")
    a0_task_hash = task_hash(state)
    checkpoint = Path(state.checkpoint_path).resolve()
    harness = Path(state.harness_path).resolve()
    if not checkpoint.is_dir() or not harness.is_file():
        raise SmokeBlocked("A0 checkpoint or Harness is unavailable")
    harness_hash = file_hash(harness)
    policy_hash = fingerprint(state.checkpoint_manifest)

    bootstrap_turn, bootstrap_request, bootstrap_receipt = _completed_turn(source_run, "bootstrap")
    run_parent_turn, run_parent_request, run_parent_receipt = _completed_turn(source_run, "run_parent")
    source_state = Path(selected.get("source_state", "")).resolve()
    if (Path(bootstrap_request.get("state_path", "")).resolve() != source_state
            or Path(bootstrap_receipt.get("state_path", "")).resolve() != source_state
            or Path(run_parent_request.get("state_path", "")).resolve() != source_state
            or Path(run_parent_receipt.get("state_path", "")).resolve() != source_state):
        raise SmokeBlocked("Bootstrap/run_parent state_path does not match the A0 source state")
    if (Path(bootstrap_request.get("config_path", "")).resolve()
            != Path(run_parent_request.get("config_path", "")).resolve()):
        raise SmokeBlocked("Bootstrap/run_parent config_path mismatch")
    if Path(bootstrap_receipt.get("checkpoint_path", "")).resolve() != checkpoint:
        raise SmokeBlocked("Bootstrap checkpoint does not match the A0 checkpoint")
    parent_harness = Path(bootstrap_receipt.get("harness_path", "")).resolve()
    if not parent_harness.is_file():
        raise SmokeBlocked("Bootstrap parent Harness is unavailable")
    if file_hash(parent_harness) != harness_hash:
        raise SmokeBlocked("Bootstrap parent and relocated A0 Harness bytes differ")
    parent_state = load_task(dict(state_value, harness_path=str(parent_harness)))
    parent_task_hash = task_hash(parent_state)

    parent_dir = source_run / "round_1/parent"
    if parent_dir.is_symlink() or not parent_dir.is_dir():
        raise SmokeBlocked("Natural positives must come only from round_1/parent")
    trajectories_path = parent_dir / "train_trajectories.jsonl"
    performance_path = parent_dir / "performance.json"
    if (run_parent_request.get("round_number") != 1
            or run_parent_receipt.get("round_number") != 1
            or run_parent_receipt.get("rollout_stage") != "parent"
            or run_parent_receipt.get("stage") != "rollout"
            or run_parent_receipt.get("collection_stage") != "parent_pre_update"
            or Path(run_parent_request.get("output_dir", "")).resolve() != parent_dir
            or Path(run_parent_receipt.get("output_dir", "")).resolve() != parent_dir
            or Path(run_parent_receipt.get("performance_path", "")).resolve() != performance_path
            or Path(run_parent_receipt.get("trajectories_path", "")).resolve() != trajectories_path):
        raise SmokeBlocked("run_parent round/output_dir is not the canonical B1 parent rollout")
    rows = _read_rows(trajectories_path)
    performance = read(performance_path)
    manifest = stage_manifest(release_dir, 1)
    if len(manifest["tasks"]) != 16 or any(
            task["source"] != "deepcoder_taco" or task["domain"] != "code"
            or (task.get("strata") or {}).get("interface") != "function"
            for task in manifest["tasks"]):
        raise SmokeBlocked("MODEL import requires the frozen 16 DeepCoder function tasks")
    members = {task["task_id"]: task for task in manifest["tasks"]}
    if len(rows) != 16 or {row.get("task_id") for row in rows} != set(members):
        raise SmokeBlocked("Parent rollout must contain exactly the frozen 16 unique task IDs")
    if (performance.get("round_id") != 1
            or performance.get("training_manifest") != manifest["manifest_hash"]
            or performance.get("feedback_role") != "train_evolution"
            or performance.get("collection_stage") != "parent_pre_update"
            or performance.get("total_rollouts") != 16
            or performance.get("evaluation_protocol") != "same_round_full_B_parent_child_v2"
            or run_parent_receipt.get("tasks_observed") != 16
            or run_parent_receipt.get("macro_success") != performance.get("macro_success")):
        raise SmokeBlocked("Parent performance is not complete B1 parent_pre_update evidence")
    training_provenance(rows, manifest, round_id=1, branch_id="parent",
                        harness_hash=harness_hash, parent_policy_hash=policy_hash)

    successes = 0
    transport_count = 0
    receipt_paths = {}
    for row in rows:
        task_id = row["task_id"]
        if row.get("infrastructure_error") or row.get("execution_error_type") == "infrastructure":
            raise SmokeBlocked(f"Source parent contains infrastructure failure: {task_id}")
        verification = row.get("verification")
        if not isinstance(verification, dict) or verification.get("status") != "completed":
            raise SmokeBlocked(f"Missing completed official scoring: {task_id}")
        if not isinstance(verification.get("success"), bool):
            raise SmokeBlocked(f"Official scoring success is not boolean: {task_id}")
        if verification["success"] is True:
            successes += 1
            if verification.get("full_verifier") is not True:
                raise SmokeBlocked(f"Positive trajectory lacks full official verification: {task_id}")
        calls = row.get("model_call_count")
        if type(calls) is not int or calls < 0:
            raise SmokeBlocked(f"Parent trajectory has invalid model call count: {task_id}")
        if calls == 0:
            if verification["success"] is not False:
                raise SmokeBlocked(f"Zero-call successful trajectory is not importable: {task_id}")
            if row.get("infrastructure_error") is not False:
                raise SmokeBlocked(
                    f"Zero-call negative must retain infrastructure_error=false: {task_id}")
            if any(row.get(key) not in (None, []) for key in ("transport_calls", "model_calls")):
                raise SmokeBlocked(
                    f"Zero-call negative must have empty transport_calls/model_calls: {task_id}")
        else:
            _validate_transport_calls(row)
            if len(row["transport_calls"]) != calls:
                raise SmokeBlocked(f"Transport call count disagrees with the parent row: {task_id}")
            for call in row["transport_calls"]:
                response = call.get("response") or {}
                binding = response.get("binding") or {}
                if (Path(str(binding.get("checkpoint_path", ""))).resolve() != checkpoint
                        or binding.get("weights") != state.checkpoint_manifest
                        or response.get("model_ref_requested") != state.model_ref
                        or response.get("model_ref_response") != state.model_ref):
                    raise SmokeBlocked(f"Transport response is not bound to the A0 checkpoint: {task_id}")
            transport_count += calls
        if (row.get("model_ref") != parent_state.model_ref
                or row.get("harness_sha256") != harness_hash
                or row.get("artifact_input_manifest") != parent_state.artifacts.manifest):
            raise SmokeBlocked(f"Parent trajectory is not bound to the reconstructed parent Task: {task_id}")
        receipt_path, persisted = _matching_receipt(source_run, row)
        binding = persisted.get("binding") or {}
        scope = binding.get("round_scope") or {}
        task = members[task_id]
        if (binding.get("task") != task_id or binding.get("rollout") != row.get("rollout_id")
                or binding.get("probe") is not False
                or binding.get("task_record") != task["content_hash"]
                or binding.get("task_source_hash") != row.get("task_source_hash")
                or scope.get("manifest_hash") != manifest["manifest_hash"]
                or scope.get("round_id") != 1
                or scope.get("collection_stage") != "parent_pre_update"
                or scope.get("task_hash") != parent_task_hash
                or scope.get("harness_sha256") != harness_hash
                or scope.get("checkpoint_manifest") != parent_state.checkpoint_manifest
                or scope.get("artifacts") != parent_state.artifacts.manifest
                or binding.get("state_hash") != fingerprint(scope)
                or row.get("state_hash") != binding.get("state_hash")):
            raise SmokeBlocked(f"Persisted rollout receipt has the wrong round/parent Task binding: {task_id}")
        receipt_paths[task_id] = str(receipt_path)

    benchmark = (performance.get("benchmarks") or {}).get("deepcoder_taco") or {}
    if (performance.get("macro_success") != successes / 16
            or benchmark.get("count") != 16 or benchmark.get("successes") != successes):
        raise SmokeBlocked("Parent performance totals disagree with the 16 official scores")
    positives = select_positive_rows(rows, profile="multidomain")
    if not positives:
        raise SmokeBlocked("No natural positive parent trajectory is eligible for MODEL SFT")
    return {
        "source_run": str(source_run),
        "meta_link_seal": (_validate_central_meta_link(source_run)
                           if (source_run / "meta").is_symlink() else None),
        "state_path": str(state_path),
        "state_sha256": sealed_state,
        "snapshot_receipt_path": str(snapshot_path),
        "snapshot_receipt_sha256": file_hash(snapshot_path),
        "parent_dir": str(parent_dir),
        "trajectories_path": str(trajectories_path),
        "performance_path": str(performance_path),
        "manifest_path": str(release_dir / "B1/manifest.json"),
        "manifest_hash": manifest["manifest_hash"],
        "task_ids": list(members),
        "task_count": 16,
        "official_success_count": successes,
        "transport_calls_verified": transport_count,
        "a0_task_hash": a0_task_hash,
        "parent_task_hash": parent_task_hash,
        "checkpoint_path": str(checkpoint),
        "harness_path": str(harness),
        "parent_harness_path": str(parent_harness),
        "bootstrap_turn": str(bootstrap_turn.relative_to(source_run)),
        "run_parent_turn": str(run_parent_turn.relative_to(source_run)),
        "relocated_identity": {
            "snapshot_task_hash": a0_task_hash,
            "parent_task_hash": parent_task_hash,
            "snapshot_harness_path": str(harness),
            "parent_harness_path": str(parent_harness),
            "harness_sha256": harness_hash,
        },
        "input_seals": _a0_input_seals(state, parent_harness),
        "receipt_paths": receipt_paths,
        "accepted_candidate_used": False,
        "skills_used": False,
        "positive_parent_count": len(positives),
    }


def _project_root(machine_config):
    candidate = Path(machine_config).resolve().parent
    if (candidate / "runtime").is_dir() and (candidate / "seed_harness").is_dir():
        return candidate
    return PROJECT


def _derive_model_config(machine_config, root, release_dir, source):
    machine_config = Path(machine_config).resolve()
    root = Path(root).resolve()
    project_root = _project_root(machine_config)
    original = read(machine_config)
    derive_input = dict(original)
    derive_input.update(experiment_scope="multidomain", training_schedule="round_disjoint",
                        evaluation_schedule="final_only", retry_rejected_from_round=1)
    derive_path = root / "plan/derive_input_config.json"
    write_json(derive_path, derive_input)
    derive_smoke_config(derive_path, root, release_dir, project_root)

    launcher = dict(original)
    launcher.update(experiment_scope="deepcoder_canary", training_schedule="round_disjoint",
                    rounds=1, allocated_tasks=16, training_tasks_per_pass=16,
                    train_quotas_per_round={"envscaler": 0, "deepcoder_taco": 16,
                                            "hotpotqa": 0, "2wiki": 0},
                    retry_rejected_from_round=2, evaluation_schedule="none",
                    data_release="deepcoder_model_smoke_import",
                    frozen_data_dir=str(Path(release_dir).resolve()),
                    output_root=str(root), run_name="deepcoder_model_smoke")
    smoke_config_path = root / "plan/smoke_launcher_config.json"
    write_json(smoke_config_path, launcher)
    adapter = TaskAdapter(project_root, root / "receipts")
    effective = adapter._config({"config_path": str(smoke_config_path)})
    if (effective.max_generations != 1
            or effective.window_quotas != {"tool_use": 0, "code": 16, "searchqa": 0}
            or Path(effective.output_root).resolve() != root
            or Path(effective.task_checkpoint).resolve() != Path(source["checkpoint_path"])):
        raise SmokeBlocked("Derived MODEL config does not preserve the 16-task A0/root identity")
    effective_path = root / "plan/effective_config.json"
    write_json(effective_path, effective.model_dump())
    base_manifest = checkpoint_manifest(Path(effective.task_checkpoint))
    if base_manifest != load_task(read(source["state_path"])).checkpoint_manifest:
        raise SmokeBlocked("A0 checkpoint weights manifest differs from the derived base checkpoint")
    base_manifest_path = root / "plan/base_checkpoint_manifest.json"
    write_json(base_manifest_path, base_manifest)
    return {
        "project_root": str(project_root),
        "machine_config_path": str(machine_config),
        "smoke_config_path": str(smoke_config_path),
        "effective_config_path": str(effective_path),
        "base_checkpoint_manifest_path": str(base_manifest_path),
        "derivation": "training_side_smoke.derive_smoke_config + TaskAdapter._config",
    }


def _bindings(plan):
    root = Path(plan["output_root"])
    return {
        "runner_sha256": file_hash(Path(__file__)),
        "machine_config_sha256": file_hash(Path(plan["machine_config_path"])),
        "smoke_config_sha256": file_hash(root / "plan/smoke_launcher_config.json"),
        "effective_config_sha256": file_hash(root / "plan/effective_config.json"),
        "base_checkpoint_manifest_sha256": file_hash(root / "plan/base_checkpoint_manifest.json"),
        "base_config_sha256": file_hash(side._base_config_path(plan)),
        "training_side_helper_sha256": file_hash(Path(side.__file__)),
        "production_implementation_sha256": side._production_hashes(),
    }


def _identity(bindings, source):
    return fingerprint({"bindings": bindings, "source": source})


def _assert_output_separate(root, assets):
    for label, value in assets:
        if value is None:
            continue
        asset = Path(value).resolve()
        if root == asset or root.is_relative_to(asset) or asset.is_relative_to(root):
            raise SmokeBlocked(f"MODEL output root overlaps read-only asset: {label}")


def plan_model_smoke(source_run, output_root, machine_config):
    source_run = Path(source_run)
    if source_run.is_symlink():
        raise SmokeBlocked(f"Source run root must not be a symlink: {source_run}")
    source_run = source_run.resolve()
    machine_config = Path(machine_config).resolve()
    validation = _validate_output_root(output_root)
    root = Path(validation["path"])
    if root.is_relative_to(source_run) or source_run.is_relative_to(root):
        raise SmokeBlocked("MODEL output root and source run must be disjoint")
    source_hashes_before = _source_hashes(source_run)
    launcher = read(machine_config)
    release_dir = Path(launcher["frozen_data_dir"]).resolve()
    source = _validate_source(source_run, release_dir)
    project_root = _project_root(machine_config)
    base_config = Path(launcher["base_config"])
    if not base_config.is_absolute():
        base_config = project_root / launcher.get("runtime_source", "runtime") / base_config
    assets = [("frozen release", release_dir), ("machine config", machine_config),
              ("base config", base_config), ("A0 checkpoint", source["checkpoint_path"]),
              ("A0 Harness", source["harness_path"]),
              ("bootstrap parent Harness", source["parent_harness_path"])]
    meta_seal = source.get("meta_link_seal")
    if meta_seal:
        assets.extend([("central meta target", meta_seal["resolved_path"]),
                       ("central meta owner directory", Path(meta_seal["owner_path"]).parent)])
    state = load_task(read(source["state_path"]))
    assets.append(("A0 artifacts", state.artifacts.directory))
    if state.artifacts.directory:
        assets.append(("artifact provenance",
                       Path(state.artifacts.directory).resolve().parent / "artifact_provenance.json"))
    retrieval = launcher.get("retrieval_index")
    if retrieval:
        assets.extend([("retrieval index", retrieval),
                       ("retrieval manifest", Path(retrieval).with_suffix(".sqlite.manifest.json"))])
    for task in read(release_dir / "B1/manifest.json")["tasks"]:
        assets.append((f"record for {task['task_id']}", task["record_file"]))
    _assert_output_separate(root, assets)
    root.mkdir(parents=True)
    paths = _derive_model_config(machine_config, root, release_dir, source)
    if _source_hashes(source_run) != source_hashes_before:
        raise SmokeBlocked("Source run changed while planning MODEL smoke")
    _verify_input_seals(source)

    source["source_hashes_before"] = source_hashes_before
    source["source_hashes_after"] = source_hashes_before
    receipt = {
        "schema_version": 1,
        "stage": "plan",
        "status": "planned",
        "output_root": str(root),
        "output_root_validation": validation,
        **paths,
        "source": source,
        "positive_parent_count": source["positive_parent_count"],
        "model_calls": 0,
        **SMOKE_FLAGS,
    }
    receipt["bindings"] = _bindings(receipt)
    receipt["plan_identity"] = _identity(receipt["bindings"], source)
    registry = Path(registry_path(load_effective_config(root))).resolve()
    if not registry.is_relative_to(root) or registry.is_relative_to(source_run):
        raise SmokeBlocked("Derived service registry is not isolated under the MODEL output root")
    receipt["owned_registry_path"] = str(registry)
    write_json(root / "receipts/plan.json", receipt)
    _verify_input_seals(source)
    if _source_hashes(source_run) != source_hashes_before:
        raise SmokeBlocked("Source run changed while writing MODEL plan")
    return receipt


def load_effective_config(root):
    return PipelineConfig.model_validate(read(Path(root) / "plan/effective_config.json"))


def _audit(stage, error, **extra):
    return {"status": "requires_audit", "stage": stage, "error": error, **SMOKE_FLAGS, **extra}


def _load_execute_plan(root):
    path = Path(root) / "receipts/plan.json"
    if not path.is_file() or path.is_symlink():
        raise SmokeBlocked(f"Final plan receipt does not exist: {path}")
    plan = read(path)
    if (plan.get("status") != "planned"
            or Path(plan.get("output_root", "")).resolve() != Path(root).resolve()
            or plan.get("plan_identity") != _identity(plan.get("bindings") or {},
                                                     plan.get("source") or {})):
        raise SmokeBlocked("Plan identity is invalid or belongs to another output root")
    current = _bindings(plan)
    if current != plan["bindings"]:
        changed = sorted(key for key in current if current[key] != plan["bindings"].get(key))
        raise SmokeBlocked(f"Stage binding hash mismatch since plan: {changed}")
    source_hashes = _source_hashes(Path(plan["source"]["source_run"]))
    if source_hashes != plan["source"]["source_hashes_after"]:
        raise SmokeBlocked("Source run hashes changed since plan; audit before any training")
    expected_source = {key: value for key, value in plan["source"].items()
                       if key not in {"source_hashes_before", "source_hashes_after"}}
    release_dir = Path(plan["source"]["manifest_path"]).resolve().parent.parent
    current_source = _validate_source(Path(plan["source"]["source_run"]), release_dir)
    if current_source != expected_source:
        changed = sorted(key for key in current_source
                         if current_source[key] != expected_source.get(key))
        raise SmokeBlocked(f"Full source/provenance revalidation changed since plan: {changed}")
    if plan.get("positive_parent_count") != current_source["positive_parent_count"]:
        raise SmokeBlocked("Natural-positive count changed since plan")
    return plan


def _stage_state(root, stage):
    final = Path(root) / "receipts" / f"{stage}.json"
    if final.is_file():
        return "final", read(final)
    if (Path(root) / "locks" / f"{stage}.started.json").exists():
        return "started", None
    return "pending", None


def _finish_stage(root, stage, receipt, started, training_cleanup, service_cleanup, plan):
    receipt["training_cleanup"] = training_cleanup
    receipt["cleanup"] = service_cleanup
    source_unchanged = _source_hashes(Path(plan["source"]["source_run"])) == (
        plan["source"]["source_hashes_after"])
    try:
        _verify_input_seals(plan["source"])
        input_seals_unchanged = True
    except (OSError, ValueError, SmokeBlocked):
        input_seals_unchanged = False
    receipt["source_hashes_unchanged"] = source_unchanged
    receipt["input_seals_unchanged"] = input_seals_unchanged
    if not source_unchanged or not input_seals_unchanged:
        receipt["status"] = "requires_audit"
        receipt["error"] = "Source run or external A0 inputs changed during execute; audit required"
    elif (not service_cleanup.get("services_stopped") or service_cleanup.get("error")
          or training_cleanup.get("error")):
        receipt["status"] = "incomplete"
        receipt["error"] = (service_cleanup.get("error") or training_cleanup.get("error")
                            or "Owned services were not confirmed stopped")
    write_json(Path(root) / "receipts" / f"{stage}.json", receipt)
    Path(started).unlink(missing_ok=True)
    return receipt


def _execute_sft(root, plan):
    stage = "sft"
    config = load_effective_config(root)
    started = root / "locks/sft.started.json"
    write_json(started, {"stage": stage, "started_at": time.time(),
                         "plan_identity": plan["plan_identity"]})
    receipt = {"schema_version": 1, "stage": stage, "status": "incomplete",
               "plan_identity": plan["plan_identity"], **SMOKE_FLAGS}
    sft_dir = root / "runs/deepcoder_model_smoke/sft"
    try:
        with _signal_guard():
            if os.environ.get("RSIFLOW_SANDBOX_BACKEND") != "linux_bwrap_seccomp_v1":
                raise SmokeBlocked("Real MODEL execute requires explicit linux_bwrap_seccomp_v1")
            _assert_registry_owned(config, root)
            ensure_services(config, config.task_checkpoint)
            records = verify_services(config, config.task_checkpoint)
            if sorted(record.get("gpu") for record in records) != [0, 1, 2, 3]:
                raise SmokeBlocked("Four base replicas on GPU 0-3 were not verified before SFT")
            adapter = TaskAdapter(plan["project_root"], root / "receipts")
            result = adapter.sft({
                "config_path": str(root / "plan/smoke_launcher_config.json"),
                "state_path": plan["source"]["state_path"],
                "parent_rollout_dir": plan["source"]["parent_dir"],
                "output_dir": str(sft_dir),
            })
            problems, child, child_state, metrics, gpu = _validate_sft_evidence(
                root, config, plan, result, plan["source"]["state_path"],
                plan["positive_parent_count"])
            if problems:
                raise SmokeBlocked("; ".join(problems))
            _assert_registry_owned(config, root)
            ensure_services(config, str(child))
            records = verify_services(config, str(child))
            if sorted(record.get("gpu") for record in records) != [0, 1, 2, 3]:
                raise SmokeBlocked("Four merged-child replicas on GPU 0-3 were not verified")
            generation = _short_generation(config, child_state)
            metrics_path = Path(result["training_metrics_path"])
            gpu_path = sft_dir / "model_update/gpu_phase_receipt.json"
            state_path = Path(result["candidate_state_path"])
            receipt.update(status="completed", world_size=metrics["world_size"],
                           ranks=sorted(record["rank"] for record in metrics["rank_records"]),
                           base_checkpoint_unchanged=True, child_weights_changed=True,
                           restored_checkpoint="child", child_checkpoint=str(child),
                           candidate_state_path=str(state_path),
                           training_metrics_path=str(metrics_path), gpu_phase_receipt_path=str(gpu_path),
                           candidate_state_sha256=file_hash(state_path),
                           training_metrics_sha256=file_hash(metrics_path),
                           gpu_phase_receipt_sha256=file_hash(gpu_path),
                           short_generation=generation)
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        training_cleanup = _terminate_owned_training(root)
        service_cleanup = cleanup_owned_services(config, root)
    return _finish_stage(root, stage, receipt, started, training_cleanup, service_cleanup, plan)


def _execute_recovery(root, plan, sft):
    stage = "recovery"
    config = load_effective_config(root)
    started = root / "locks/recovery.started.json"
    write_json(started, {"stage": stage, "started_at": time.time(),
                         "plan_identity": plan["plan_identity"]})
    receipt = {"schema_version": 1, "stage": stage, "status": "incomplete",
               "plan_identity": plan["plan_identity"], "retrained": False, **SMOKE_FLAGS}
    try:
        with _signal_guard():
            unchanged = all(file_hash(Path(sft[path_key])) == sft[hash_key]
                            for path_key, hash_key in (
                                ("training_metrics_path", "training_metrics_sha256"),
                                ("gpu_phase_receipt_path", "gpu_phase_receipt_sha256"),
                                ("candidate_state_path", "candidate_state_sha256")))
            receipt["training_receipts_unchanged"] = unchanged
            if not unchanged:
                raise SmokeBlocked("Training receipts changed since SFT; recovery must not retrain")
            child_state = read(Path(sft["candidate_state_path"]))
            child = child_state["checkpoint_path"]
            if Path(child).resolve() != Path(sft["child_checkpoint"]).resolve():
                raise SmokeBlocked("Recovery child checkpoint identity changed")
            _assert_registry_owned(config, root)
            ensure_services(config, child)
            records = verify_services(config, child)
            if sorted(record.get("gpu") for record in records) != [0, 1, 2, 3]:
                raise SmokeBlocked("Recovery did not verify four child replicas on GPU 0-3")
            receipt.update(status="completed", child_checkpoint=child,
                           short_generation=_short_generation(config, child_state), retrained=False)
    except Exception as exc:
        receipt["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        training_cleanup = _terminate_owned_training(root)
        service_cleanup = cleanup_owned_services(config, root)
    return _finish_stage(root, stage, receipt, started, training_cleanup, service_cleanup, plan)


def _completed_summary(plan, sft, recovery, status):
    return {"status": status, "stage": "execute", "plan_identity": plan["plan_identity"],
            "sft": sft, "recovery": recovery,
            "short_generations": [sft["short_generation"], recovery["short_generation"]],
            "source_hashes_unchanged": bool(sft.get("source_hashes_unchanged")
                                             and recovery.get("source_hashes_unchanged")),
            **SMOKE_FLAGS}


def execute_model_smoke(output_root):
    root = Path(output_root).resolve()
    try:
        plan = _load_execute_plan(root)
    except (OSError, ValueError, SmokeBlocked) as exc:
        return _audit("plan", str(exc))
    try:
        with _stage_lock(root):
            try:
                plan = _load_execute_plan(root)
            except (OSError, ValueError, SmokeBlocked) as exc:
                return _audit("plan", str(exc))
            sft_state, sft = _stage_state(root, "sft")
            recovery_state, recovery = _stage_state(root, "recovery")
            if sft_state == "started":
                return _audit("sft", "A previous SFT started without a final receipt; audit before rerun")
            if recovery_state == "started":
                return _audit("recovery", "A previous recovery started without a final receipt; audit before rerun")
            if sft_state == "final" and sft.get("status") != "completed":
                return _audit("sft", "Previous SFT final receipt is not completed; automatic rerun is forbidden")
            if recovery_state == "final" and recovery.get("status") != "completed":
                return _audit("recovery", "Previous recovery final receipt is not completed; audit is required")
            if sft_state == "final" and recovery_state == "final":
                return _completed_summary(plan, sft, recovery, "already_completed")
            if os.environ.get("RSIFLOW_SANDBOX_BACKEND") != "linux_bwrap_seccomp_v1":
                return _audit("execute", "Pending execute stages require explicit linux_bwrap_seccomp_v1")
            if sft_state == "pending":
                sft = _execute_sft(root, plan)
                if sft["status"] != "completed":
                    return _audit("sft", "SFT did not complete; inspect its final receipt",
                                  previous_status=sft["status"])
            recovery = _execute_recovery(root, plan, sft)
            if recovery["status"] != "completed":
                return _audit("recovery", "Recovery did not complete; inspect its final receipt",
                              previous_status=recovery["status"])
            return _completed_summary(plan, sft, recovery, "completed")
    except SmokeBlocked as exc:
        return _audit("execute", str(exc))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "execute"])
    parser.add_argument("--source-run", type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--machine-config", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            if not args.source_run or not args.machine_config:
                raise SmokeBlocked("plan requires --source-run and --machine-config")
            receipt = plan_model_smoke(args.source_run, args.output_root, args.machine_config)
        else:
            receipt = execute_model_smoke(args.output_root)
    except (OSError, ValueError, SmokeBlocked) as exc:
        print(json.dumps({"ok": False, "status": "blocked", "error": str(exc)}, sort_keys=True))
        return 1
    ok = receipt.get("status") in {"planned", "completed", "already_completed"}
    print(json.dumps({"ok": ok, "command": args.command, "status": receipt.get("status"),
                      "output_root": str(args.output_root)}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
