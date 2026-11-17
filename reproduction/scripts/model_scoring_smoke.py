"""Independent minimal real-model scoring smoke.

This runner composes existing interfaces; it never calls ``validate()`` and never produces a
694/744 benchmark, an A0 baseline, a completed round, or any aggregate accuracy. It is
report-only: results must not feed training, Meta learning, candidate acceptance or checkpoint
selection. Consuming held-out entries here is recorded as exposure and must stay isolated from
the formal evaluation.

Default mode is ``--plan`` (offline assets/config/subset/binding validation, no model, no GPU).
Real scoring requires an explicit ``--execute`` and a non-existent output root.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT
sys.path.insert(0, str(ROOT / "RSIFlow"))
sys.path.insert(0, str(ROOT / "RSIFlow/runtime"))

from data_protocol import public_text, text_hash
from dynamic_predictions import generate_dynamic
from evaluation_results import official_task_scores
from evolution_protocol import file_hash, freeze
from evolution_protocol import manifest as load_registered_manifest
from rsiflow.task_meta.durable import load_task, task_hash, value_hash
from rsiflow.task_meta.gpu_phases import registry_path, start_services, stop_services, verify_services
from rsiflow.task_meta.reporting import OfficialEvaluatorSpec, evaluate_official
from rsiflow.task_meta.rsiflow_manifest import load_manifest
from rsiflow.task_meta.storage import checkpoint_manifest, save_json
from rsiflow.task_meta.types import TaskAgentState
from task_adapter import TaskAdapter
from tool_validation import evaluate_tool, native_layout

SCOPE = "minimal_real_model_scoring_smoke_not_a_benchmark"
BACKEND = "linux_bwrap_seccomp_v1"
CODE_QA = ("humaneval_plus", "mbpp_plus", "hotpotqa_dev", "2wiki_dev")
BFCL = "bfcl_v3"
BFCL_REQUIRED = 50
# Native BFCL submissions may be structured calls rather than text. Only one unambiguous
# non-empty field is accepted; anything else is an evidence failure, never a model zero.
BFCL_SUBMISSION_FIELDS = ("result", "response", "output", "model_response", "function_calls")
# Only this generator, bound by the official evaluate_tool result, may declare task->call
# attribution. A hand-written or tampered mapping file is never accepted as evidence.
TRUSTED_ATTRIBUTION_GENERATOR = "evaluate_tool_native_request_attribution_v1"
SEED_HARNESS = ROOT / "RSIFlow/seed_harness/rsiflow_base_manifest.json"
DEFAULT_CONFIG = ROOT / "reproduction/configs/train_4b.json"
DEFAULT_MANIFEST = ROOT / "RSIFlow/runtime/data/validation_744_v4/manifest.json"
DEFAULT_SETTINGS = ROOT / "reproduction/configs/validation_744.json"
SELECTION_RULE = (
    "first complete task in parent manifest order for each of humaneval_plus/mbpp_plus/hotpotqa_dev/2wiki_dev; "
    "first 50 bfcl_v3 complete tasks in parent manifest order whose native IDs all exist in the pinned official "
    "native_layout; declared before any model output and never reselected from results"
)
TASK_FIELDS = ("source", "domain", "task_id", "native_id", "version", "original_split", "group_id", "role",
               "round_id", "purpose", "exposure", "content_hash", "source_sha256", "record_file",
               "record_sha256", "record_offset", "record_length")


class SmokeBlocked(RuntimeError):
    """Infrastructure or evidence failure; never a model score of zero."""


def native_ids(task):
    raw = task.get("native_ids")
    if raw is not None:
        if not isinstance(raw, list) or not raw:
            raise SmokeBlocked(f"Ambiguous native family for {task.get('task_id')}: empty native_ids")
        ids = [str(item) for item in raw]
        if any(not item.strip() for item in ids) or len(set(ids)) != len(ids):
            raise SmokeBlocked(f"Ambiguous native family for {task.get('task_id')}: blank or duplicate native_ids")
        if task.get("native_id") and str(task["native_id"]) not in ids:
            raise SmokeBlocked(f"Inconsistent native_id/native_ids for {task.get('task_id')}")
        return ids
    single = task.get("native_id")
    if single is None or not str(single).strip():
        raise SmokeBlocked(f"Missing native identity for {task.get('task_id')}")
    return [str(single)]


def select_subset(tasks, official_native_ids):
    """Deterministic 54-task selection; fail closed on any ambiguity or shortfall."""
    official = {str(item) for item in official_native_ids}
    seen = set()
    for task in tasks:
        if task["task_id"] in seen:
            raise SmokeBlocked(f"Duplicate task_id in parent manifest: {task['task_id']}")
        seen.add(task["task_id"])
    chosen = []
    for source in CODE_QA:
        rows = [task for task in tasks if task["source"] == source]
        if not rows:
            raise SmokeBlocked(f"Parent manifest has no {source} task")
        chosen.append(rows[0])
    recognized, unrecognized = [], []
    for task in (t for t in tasks if t["source"] == BFCL):
        ids = native_ids(task)
        if all(item in official for item in ids):
            recognized.append(task)
        else:
            unrecognized.append({"task_id": task["task_id"], "native_ids": ids,
                                 "missing_native_ids": [item for item in ids if item not in official]})
    if len(recognized) < BFCL_REQUIRED:
        raise SmokeBlocked(
            f"Only {len(recognized)} BFCL complete tasks are recognized by the pinned official native data; "
            f"{BFCL_REQUIRED} are required. Parent-manifest protocol gap must not be silently repaired.")
    chosen.extend(recognized[:BFCL_REQUIRED])
    return {"tasks": chosen, "bfcl_unrecognized": unrecognized,
            "bfcl_recognized_total": len(recognized), "rule": SELECTION_RULE}


def load_parent_manifest(path):
    value = load_registered_manifest(Path(path), "independent_validation", None)
    return value, value["manifest_hash"]


def official_native_ids(settings):
    _, _, ids, _ = native_layout(BFCL, settings["tool_benchmarks"][BFCL])
    return {str(item) for items in ids.values() for item in items}


def read_task_record(task):
    path = Path(task["record_file"])
    if path.is_symlink() or not path.is_file():
        raise SmokeBlocked(f"Frozen record pack unavailable: {path}")
    if file_hash(path) != task["record_sha256"]:
        raise SmokeBlocked(f"Frozen record pack hash changed: {path}")
    with path.open("rb") as stream:
        stream.seek(task["record_offset"])
        record = json.loads(stream.read(task["record_length"]))
    if text_hash(public_text(record, source=task["source"])) != task["content_hash"]:
        raise SmokeBlocked(f"Frozen record content hash changed: {task['task_id']}")
    return record


def build_plan_artifacts(root, parent_manifest, parent_manifest_hash, official_ids, parent_evaluators):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    selected = select_subset(parent_manifest["tasks"], official_ids)
    subset = {"smoke_only": True, "scope": SCOPE, "parent_manifest_hash": parent_manifest_hash,
              "selection_rule": selected["rule"],
              "task_ids": [task["task_id"] for task in selected["tasks"]],
              "tasks": [{**{key: task[key] for key in TASK_FIELDS}, "native_ids": native_ids(task)}
                        for task in selected["tasks"]],
              "bfcl_unrecognized": selected["bfcl_unrecognized"],
              "bfcl_recognized_total": selected["bfcl_recognized_total"]}
    subset_path = root / "smoke_subset.json"
    freeze(subset_path, subset)
    subset_hash = file_hash(subset_path)
    specs = {}
    for source in CODE_QA:
        task = next(item for item in selected["tasks"] if item["source"] == source)
        record = read_task_record(task)
        # generate_dynamic builds OfficialEvaluatorSpec(**spec, benchmark=identifier), so the
        # subset spec must not carry a duplicate "benchmark" keyword.
        original = {key: value for key, value in parent_evaluators[source].items() if key != "benchmark"}
        suffix = ".json" if source.endswith("_dev") else ".jsonl"
        data_path = root / f"{source}{suffix}"
        content = (json.dumps([record], ensure_ascii=False) if suffix == ".json"
                   else json.dumps(record, ensure_ascii=False) + "\n")
        data_path.write_text(content, encoding="utf-8")
        ids_path = root / f"{source}_ids.json"
        freeze(ids_path, [task["native_id"]])
        spec = {**original, "data_path": str(data_path), "data_sha256": file_hash(data_path),
                "task_ids_path": str(ids_path), "task_ids_sha256": file_hash(ids_path),
                "subset": "smoke_one_complete_task",
                "metadata": {**original.get("metadata", {}), "smoke_only": True,
                             "parent_manifest_hash": parent_manifest_hash, "smoke_subset_hash": subset_hash,
                             "parent_task_id": task["task_id"]}}
        OfficialEvaluatorSpec(**{**spec, "benchmark": source}).validate()
        specs[source] = spec
    return {"root": root, "selected": selected, "subset_path": subset_path,
            "subset_hash": subset_hash, "specs": specs}


def freeze_smoke_task(root, config, *, parent_manifest_hash, subset_hash, settings_path, pipeline_config_path):
    """Freeze an explicit smoke-only initial Task state; no completed round is invented."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    checkpoint = str(Path(config.task_checkpoint).resolve())
    if not Path(checkpoint).is_dir():
        raise SmokeBlocked(f"Task checkpoint unavailable: {checkpoint}")
    weights = checkpoint_manifest(checkpoint)
    state = TaskAgentState(0, "Qwen3-4B", str(SEED_HARNESS), checkpoint_path=checkpoint,
                           checkpoint_manifest=weights)
    state_path = root / "smoke_task_state.json"
    save_json(state_path, asdict(state))
    source_round_path = root / "source_round.json"
    freeze(source_round_path, {
        "smoke_only": True, "rounds_completed": 0, "status": "smoke_initial_state",
        "generation": 0, "model_ref": "Qwen3-4B", "harness_path": str(SEED_HARNESS),
        "checkpoint_path": checkpoint, "parent_manifest_hash": parent_manifest_hash,
        "smoke_subset_hash": subset_hash,
        "note": "No evolution round, SFT or candidate acceptance was executed; this is a predeclared "
                "smoke-only initial state, not a formal round snapshot.",
    })
    binding = {
        "task_state": asdict(state), "task_state_path": str(state_path),
        "state_hash": task_hash(state),
        "checkpoint_files": weights, "task_harness_identity": load_manifest(SEED_HARNESS).identity,
        "protocol_hash": file_hash(source_round_path), "status": "frozen_for_report_eval",
        "selection": {"rule": "predeclared_deterministic_smoke_subset_without_external_score_selection", "round": 0},
        "validation_config_sha256": file_hash(Path(settings_path)), "explicit_full_benchmark": False,
        "source_role": "independent_validation", "purpose": "report_only",
        "manifest_hash": parent_manifest_hash, "smoke_subset_hash": subset_hash,
        "system_snapshot_sha256": file_hash(state_path),
        "evaluation_config_sha256": file_hash(Path(pipeline_config_path)),
    }
    frozen_path = root / "frozen_task.json"
    freeze(frozen_path, binding)
    return frozen_path, binding


def validate_prediction_rows(rows, expected_ids, state_hash):
    """Every task needs one real non-empty submission and a completed local model call."""
    expected = list(expected_ids)
    if len(rows) != len(expected):
        raise SmokeBlocked(f"Prediction denominator {len(rows)} != expected {len(expected)}")
    seen = set()
    for row in rows:
        task_id = row.get("task_id")
        if task_id in seen:
            raise SmokeBlocked(f"Duplicate prediction: {task_id}")
        seen.add(task_id)
        if task_id not in expected:
            raise SmokeBlocked(f"Unexpected prediction id: {task_id}")
        if row.get("state_hash") != state_hash:
            raise SmokeBlocked(f"Prediction is not from the frozen smoke Task: {task_id}")
        if row.get("split") != "report_eval":
            raise SmokeBlocked(f"Prediction split is not report_eval: {task_id}")
        if row.get("infrastructure_error"):
            raise SmokeBlocked(f"Infrastructure failure leaves the report incomplete: {task_id}")
        if row.get("final_submission_count") != 1:
            raise SmokeBlocked(f"Expected exactly one final submission: {task_id}")
        answer = row.get("final_answer")
        if not isinstance(answer, str) or not answer.strip():
            raise SmokeBlocked(f"Empty model submission is an evidence failure, not a model zero: {task_id}")
        if not row.get("record_hash"):
            raise SmokeBlocked(f"Missing prediction record hash: {task_id}")
        calls = row.get("transport_calls") or []
        if not any(call.get("status") == "completed" for call in calls):
            raise SmokeBlocked(f"No completed local model call for {task_id}")
        if any(call.get("status") in ("failed", "requires_audit") for call in calls):
            raise SmokeBlocked(f"Model call requires audit before scoring: {task_id}")
    if seen != set(expected):
        raise SmokeBlocked("Missing prediction ids for the fixed smoke denominator")


def effective_config(config_path, output_root):
    adapter = TaskAdapter(ROOT / "RSIFlow", Path(output_root) / "receipts")
    config = adapter._config({"config_path": str(config_path)})
    config.output_root = str(output_root)
    return config


def _git_check_ignore(path):
    """True/False when git can answer authoritatively; None when git is unavailable."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(PROJECT), "check-ignore", "-q", "--no-index", str(path)],
            capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    return None


def _structurally_ignored(root):
    """Conservative git-independent fallback: an explicitly ignored ancestor directory.

    Used only when git cannot answer (e.g. cross-uid ownership). Any negation touching the
    subtree makes the status unprovable, so the path is refused rather than assumed ignored.
    """
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


def validate_output_root(root):
    """Outputs must go to a NEW Git-ignored directory inside the project, never a tracked path."""
    resolved = Path(root).resolve()
    if resolved == PROJECT or not resolved.is_relative_to(PROJECT):
        raise SmokeBlocked(f"Output root must be a new directory inside the project: {resolved}")
    if resolved.exists():
        raise SmokeBlocked(f"Output root must not exist: {resolved}")
    authoritative = _git_check_ignore(resolved)
    if authoritative is False:
        raise SmokeBlocked(f"Output root is not Git-ignored: {resolved}")
    if authoritative is None:
        if not _structurally_ignored(resolved):
            raise SmokeBlocked(f"Output root is not provably Git-ignored (git unavailable): {resolved}")
        method = "structural_gitignore_ancestor"
    else:
        method = "git_check_ignore"
    return {"path": str(resolved), "inside_project": True, "preexisting": False,
            "git_ignored": True, "verification": method}


def asset_checks(config, settings, root):
    replicas = list(config.task_replicas)
    endpoints = [item["base_url"] if isinstance(item, dict) else item.base_url for item in replicas]
    gpus = {item["gpu"] if isinstance(item, dict) else item.gpu for item in replicas}
    evaluators = json.loads(Path(settings["official_specs"]).read_text())["evaluators"]
    # Retrieval is resolved from the frozen data_dir, not from a launcher "retrieval_index" field.
    search_index = Path(config.data_dir) / "search.sqlite"
    search_manifest = Path(config.data_dir) / "search.sqlite.manifest.json"
    search_hash_ok = False
    if search_index.is_file() and search_manifest.is_file():
        expected = json.loads(search_manifest.read_text(encoding="utf-8")).get("sha256")
        search_hash_ok = bool(expected) and file_hash(search_index) == expected
    checks = {
        "four_distinct_endpoints": len(endpoints) == 4 and len(set(endpoints)) == 4 and gpus == {0, 1, 2, 3},
        "checkpoint_exists": Path(config.task_checkpoint).is_dir(),
        "frozen_search_index_exists": search_index.is_file(),
        "frozen_search_manifest_exists": search_manifest.is_file(),
        "frozen_search_index_hash_matches": search_hash_ok,
        "isolation_receipt_exists": (ROOT / "assets/isolation/install-receipt.json").is_file(),
        "isolation_binary_exists": (ROOT / "assets/isolation/bin/bwrap").is_file(),
        "sandbox_backend_selected": os.environ.get("RSIFLOW_SANDBOX_BACKEND") == BACKEND,
        "official_entrypoints_exist": all(Path(spec["entrypoint"]).is_file() for spec in evaluators.values()),
        "bfcl_repository_exists": Path(settings["tool_benchmarks"][BFCL]["repository_root"]).is_dir(),
    }
    return {"checks": checks, "blockers": sorted(name for name, ok in checks.items() if not ok)}


def _protocol_counts(settings):
    limits = settings["validation_limits"]
    total = sum(limits.values())
    ace = limits.get("acebench", 0)
    return {"frozen_validation_tasks": total, "scored_validation_tasks": total - ace,
            "pending_validation_tasks": ace,
            "note": "Parent protocol declaration only; this smoke scores 54 selected tasks, not that denominator."}


def _base_receipt(mode, root, parent, parent_hash, settings, artifacts, binding, checks):
    return {
        "schema_version": 1, "mode": mode, "scope": SCOPE, "status": "planned",
        "output_root": str(root), "smoke_only": True, "report_only": True,
        "feedback_to_training_or_meta": False, "eligible_for_sft": False,
        "held_out_exposure": {"consumed_formal_held_out_entries": True,
                              "exposure": "exposure_unknown", "clean_independence_audited": False,
                              "must_stay_isolated_from_formal_evaluation": True},
        "parent_manifest": {"path": str(parent.get("_path", "")), "manifest_hash": parent_hash,
                            "task_count": len(parent["tasks"]), **_protocol_counts(settings)},
        "smoke_subset": {"path": str(artifacts["subset_path"]), "sha256": artifacts["subset_hash"],
                         "selection_rule": artifacts["selected"]["rule"],
                         "task_ids": [task["task_id"] for task in artifacts["selected"]["tasks"]]},
        "smoke_selected_tasks": len(artifacts["selected"]["tasks"]),
        "bfcl_unrecognized_count": len(artifacts["selected"]["bfcl_unrecognized"]),
        "bfcl_unrecognized": artifacts["selected"]["bfcl_unrecognized"],
        "bfcl_recognized_total": artifacts["selected"]["bfcl_recognized_total"],
        "binding": binding, "assets": checks, "model_calls": 0,
        "ace": {"executed": False, "user_simulator": False,
                "pending_tasks": settings["validation_limits"].get("acebench", 0),
                "note": "ACE is not executed by this smoke; the parent protocol keeps it pending."},
    }


def plan(output_root, config_path, manifest_path, settings_path):
    root = Path(output_root)
    root_validation = validate_output_root(root)
    root = Path(root_validation["path"])
    root.mkdir(parents=True)
    parent, parent_hash = load_parent_manifest(manifest_path)
    parent["_path"] = str(manifest_path)
    settings = json.loads(Path(settings_path).read_text())
    evaluators = json.loads(Path(settings["official_specs"]).read_text())["evaluators"]
    config = effective_config(config_path, root)
    artifacts = build_plan_artifacts(root / "subset", parent, parent_hash, official_native_ids(settings), evaluators)
    frozen_path, binding = freeze_smoke_task(
        root / "frozen", config, parent_manifest_hash=parent_hash, subset_hash=artifacts["subset_hash"],
        settings_path=settings_path, pipeline_config_path=config_path)
    checks = asset_checks(config, settings, root)
    receipt = _base_receipt("plan", root, parent, parent_hash, settings, artifacts, binding, checks)
    receipt["frozen_task_path"] = str(frozen_path)
    receipt["output_root_validation"] = root_validation
    receipt["execute_ready"] = not checks["blockers"]
    receipt["status"] = "planned"
    save_json(root / "plan_receipt.json", receipt)
    return receipt


def _score_code_qa(source, spec, config, frozen_path, binding, root):
    directory = root / "scores" / source
    directory.mkdir(parents=True)
    single = directory / "spec.json"
    freeze(single, {"evaluators": {source: spec}})
    predictions = root / "predictions" / source
    stage = {"benchmark": source, "expected_tasks": 1, "status": "blocked"}
    try:
        generate_dynamic(config, frozen_path, single, predictions)
        path = predictions / f"{source}.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        expected = json.loads(Path(spec["task_ids_path"]).read_text())
        validate_prediction_rows(rows, expected, binding["state_hash"])
        stage["prediction_sha256"] = file_hash(path)
        stage["completed_model_calls"] = sum(
            1 for row in rows for call in row.get("transport_calls", []) if call.get("status") == "completed")
        spec_object = OfficialEvaluatorSpec(**{**spec, "benchmark": source})
        result = evaluate_official(spec_object, rows, binding, directory)
        stage["official_status"] = result.get("status")
        stage["official_metrics"] = result.get("metrics")
        stage["official_result_sha256"] = file_hash(directory / "result.json")
        if result.get("status") != "completed":
            stage["error"] = result.get("reason") or "official evaluator did not complete"
            return stage
        try:
            scores = official_task_scores(source, spec_object, rows, directory, binding)
        except Exception as exc:
            stage["error"] = f"per-task official score unavailable: {type(exc).__name__}: {exc}"
            return stage
        if set(scores) != set(expected):
            stage["error"] = "per-task official scores do not cover the fixed denominator"
            return stage
        stage["task_scores"] = scores
        stage["status"] = "scored"
    except Exception as exc:
        stage["error"] = f"{type(exc).__name__}: {exc}"
    return stage


def _non_empty_submission(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return False


def _native_submission(row, native_id):
    """Accept only one unambiguous non-empty native submission; never guess a field or order."""
    candidates = [(name, row[name]) for name in BFCL_SUBMISSION_FIELDS if name in row]
    if not candidates:
        raise SmokeBlocked(f"No recognized native submission field for {native_id}")
    non_empty = [(name, value) for name, value in candidates if _non_empty_submission(value)]
    if len(non_empty) != 1:
        raise SmokeBlocked(f"Empty or ambiguous native submission for {native_id}: "
                           f"{[name for name, _ in candidates]}")
    return non_empty[0][0]


def _bfcl_task_evidence(directory, binding, selected_ids, task_results):
    """Verify a non-empty official submission and attributable completed local calls per native ID.

    The upstream HarnessAPI keys transport receipts by request-body hash and carries no native
    task ID, so attribution is only accepted from an explicit, unambiguous artifact. Without it
    the benchmark is blocked; order-based guessing or fabricated attribution is never allowed.
    """
    directory = Path(directory)
    by_id = {}
    for row in task_results:
        native_id = str(row.get("native_id"))
        if native_id in by_id:
            raise SmokeBlocked(f"Duplicate BFCL task result: {native_id}")
        by_id[native_id] = row
    if set(by_id) != set(selected_ids):
        raise SmokeBlocked("BFCL task results do not cover exactly the selected native ids")
    categories = {}
    for native_id, row in by_id.items():
        category, trajectory = row.get("category"), row.get("trajectory_path")
        if not category or not trajectory:
            raise SmokeBlocked(f"BFCL task result lacks category/trajectory_path: {native_id}")
        categories.setdefault(category, {"paths": set(), "ids": set()})
        categories[category]["paths"].add(str(trajectory))
        categories[category]["ids"].add(native_id)
    verified = 0
    for category, group in categories.items():
        if len(group["paths"]) != 1:
            raise SmokeBlocked(f"Ambiguous official prediction file for category {category}")
        path = Path(next(iter(group["paths"])))
        if not path.is_file():
            raise SmokeBlocked(f"Official prediction file missing for category {category}")
        text = path.read_text(encoding="utf-8").lstrip()
        rows = json.loads(text) if text.startswith("[") else [
            json.loads(line) for line in text.splitlines() if line.strip()]
        indexed = {}
        for row in rows:
            native_id = str(row.get("id"))
            if native_id in indexed:
                raise SmokeBlocked(f"Duplicate official prediction id in {category}: {native_id}")
            indexed[native_id] = row
        if set(indexed) != group["ids"]:
            raise SmokeBlocked(f"Official prediction ids do not match the selected subset in {category}")
        for native_id in sorted(group["ids"]):
            _native_submission(indexed[native_id], native_id)
            verified += 1
    attribution_path = directory / "task_call_attribution.json"
    official_result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    declared = official_result.get("task_call_attribution")
    if not isinstance(declared, dict) or not declared.get("path") or not declared.get("sha256"):
        raise SmokeBlocked("Official result does not bind a trusted task-call attribution; "
                           "a self-declared mapping file is not evidence")
    if declared.get("generator") != TRUSTED_ATTRIBUTION_GENERATOR:
        raise SmokeBlocked("Untrusted attribution generator in official result binding: "
                           f"{declared.get('generator')!r}")
    attribution_path = Path(declared["path"]).resolve()
    if not attribution_path.is_relative_to(directory.resolve()):
        raise SmokeBlocked(f"Attribution path escapes the benchmark output directory: {attribution_path}")
    if not attribution_path.is_file():
        raise SmokeBlocked(f"Bound attribution file missing: {attribution_path}")
    if file_hash(attribution_path) != declared["sha256"]:
        raise SmokeBlocked("Attribution file hash does not match the official result binding; "
                           "the mapping was altered or forged after the official run")
    attribution = json.loads(attribution_path.read_text(encoding="utf-8"))
    if attribution.get("generator") != TRUSTED_ATTRIBUTION_GENERATOR:
        raise SmokeBlocked(f"Untrusted attribution generator: {attribution.get('generator')!r}")
    if attribution.get("state_hash") != binding["state_hash"]:
        raise SmokeBlocked("BFCL call attribution is not bound to the frozen smoke state")
    declared_ids = attribution.get("selected_task_ids")
    if (not isinstance(declared_ids, list) or len(set(declared_ids)) != len(declared_ids)
            or set(declared_ids) != set(selected_ids)):
        raise SmokeBlocked("BFCL attribution selected native ids do not match the frozen subset")
    if attribution.get("selected_task_ids_sha256") != value_hash(declared_ids):
        raise SmokeBlocked("BFCL attribution selected-task-id hash mismatch")
    tasks = attribution.get("tasks")
    if not isinstance(tasks, dict) or set(tasks) != set(selected_ids):
        raise SmokeBlocked("BFCL call attribution does not cover exactly the selected native ids")
    entries = []
    owner = {}
    for native_id in sorted(selected_ids):
        items = tasks[native_id]
        if not isinstance(items, list) or not items:
            raise SmokeBlocked(f"BFCL call attribution for {native_id} is empty")
        for item in items:
            if (not isinstance(item, dict) or not isinstance(item.get("receipt_key"), str)
                    or not item["receipt_key"] or not isinstance(item.get("request_sha256"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", item["request_sha256"])):
                raise SmokeBlocked(f"BFCL attribution entry lacks a request-content hash binding for {native_id}")
            key = item["receipt_key"]
            if key in owner:
                raise SmokeBlocked(f"BFCL receipt {key} is claimed by more than one native id")
            owner[key] = native_id
            entries.append((native_id, key, item["request_sha256"]))
    completed_calls = 0
    for native_id, key, request_sha256 in entries:
        receipt_path = directory / "transport" / f"{key}.json"
        if not receipt_path.is_file():
            raise SmokeBlocked(f"BFCL transport receipt missing for {native_id}: {key}")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("status") != "completed":
            raise SmokeBlocked(f"BFCL transport receipt not completed for {native_id}: {key}")
        if receipt.get("user_simulator") is not False:
            raise SmokeBlocked(f"BFCL receipt is not a local Task call for {native_id}: {key}")
        identity = receipt.get("identity") or {}
        if identity.get("state_hash") != binding["state_hash"]:
            raise SmokeBlocked(f"BFCL receipt is not bound to the frozen smoke state: {key}")
        if not isinstance(identity.get("request"), dict):
            raise SmokeBlocked(f"BFCL receipt has no request content to verify: {key}")
        if value_hash(identity["request"]) != request_sha256:
            raise SmokeBlocked(f"BFCL request-content hash mismatch for {native_id}: {key}")
        calls = receipt.get("calls") or []
        if not calls or any(call.get("status") != "completed" for call in calls):
            raise SmokeBlocked(f"BFCL receipt has no completed local model call for {native_id}: {key}")
        completed_calls += len(calls)
    return {"verified_native_submissions": verified, "completed_model_calls": completed_calls,
            "attributed_receipts": len(owner),
            "attribution_generator": TRUSTED_ATTRIBUTION_GENERATOR,
            "task_call_attribution_sha256": file_hash(attribution_path)}


def _score_bfcl(settings, config, binding, selected, root):
    directory = root / "scores" / BFCL
    directory.mkdir(parents=True)
    tasks = [task for task in selected["tasks"] if task["source"] == BFCL]
    ids = [item for task in tasks for item in native_ids(task)]
    stage = {"benchmark": BFCL, "expected_tasks": len(tasks), "expected_native_ids": len(ids), "status": "blocked"}
    spec = {**settings["tool_benchmarks"][BFCL], "selected_tasks": tasks, "selected_task_ids": ids}
    try:
        result = evaluate_tool(BFCL, config, binding, spec, settings["tool_benchmarks"]["acebench"], directory)
        stage["official_status"] = result.get("status")
        stage["official_metrics"] = result.get("metrics")
        stage["categories"] = result.get("categories")
        stage["official_result_sha256"] = file_hash(directory / "result.json")
        if list(directory.rglob("user_*.receipt")):
            stage["error"] = "ACE user-simulator receipt appeared during a BFCL-only smoke"
            return stage
        if result.get("status") != "completed":
            stage["error"] = "official BFCL runner did not complete"
            return stage
        rows = result.get("task_results") or []
        covered = {str(row.get("native_id")) for row in rows}
        if covered != set(ids) or len(rows) != len(ids):
            stage["error"] = "per-task BFCL evidence does not cover every selected native id"
            return stage
        if any(row.get("execution_status") != "completed" or row.get("scoring_status") != "completed" for row in rows):
            stage["error"] = "BFCL task execution/scoring is not completed for every native id"
            return stage
        if result.get("expected") != len(ids) or result.get("completed") != len(ids):
            stage["error"] = "official BFCL denominator mismatch"
            return stage
        if sum(item["denominator"] for item in result.get("categories") or []) != len(ids):
            stage["error"] = "official BFCL category denominators do not reconcile"
            return stage
        stage["completed_native_tasks"] = len(rows)
        stage.update(_bfcl_task_evidence(directory, binding, ids, rows))
        stage["status"] = "scored"
    except Exception as exc:
        stage["error"] = f"{type(exc).__name__}: {exc}"
    return stage


def _cleanup(config, root):
    cleanup = {"registry": str(registry_path(config)) if config is not None else None,
               "services_stopped": False, "four_gpu_memory_released": None, "error": None}
    if config is None:
        cleanup["error"] = "effective config unavailable; no owned registry could be identified"
        return cleanup
    if not registry_path(config).exists():
        cleanup["note"] = "no owned four-GPU registry was created; nothing to stop"
        return cleanup
    try:
        stop_services(config)
        cleanup["services_stopped"] = True
        cleanup["four_gpu_memory_released"] = True
        cleanup["release_rule"] = "stop_services enforces >=40000 MiB free on each of GPU 0-3"
    except Exception as exc:
        cleanup["error"] = f"{type(exc).__name__}: {exc}"
    return cleanup


def execute(output_root, config_path, manifest_path, settings_path):
    root = Path(output_root)
    root_validation = validate_output_root(root)
    root = Path(root_validation["path"])
    if os.environ.get("RSIFLOW_SANDBOX_BACKEND") != BACKEND:
        raise SmokeBlocked(f"Real execution requires explicit RSIFLOW_SANDBOX_BACKEND={BACKEND}")
    root.mkdir(parents=True)
    config = None
    stages = {}
    benchmarks = {}
    failure = None
    previous = {}
    parent = {"tasks": []}
    parent_hash = None
    settings = {"validation_limits": {}}
    artifacts = None
    binding = {}
    checks = {"checks": {}, "blockers": ["setup_incomplete"]}

    def _interrupt(signum, frame):
        raise SmokeBlocked(f"interrupted by signal {signum}; owned services must be cleaned up")

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous[signum] = signal.getsignal(signum)
        signal.signal(signum, _interrupt)
    try:
        config = effective_config(config_path, root)
        parent, parent_hash = load_parent_manifest(manifest_path)
        parent["_path"] = str(manifest_path)
        settings = json.loads(Path(settings_path).read_text())
        evaluators = json.loads(Path(settings["official_specs"]).read_text())["evaluators"]
        checks = asset_checks(config, settings, root)
        if checks["blockers"]:
            raise SmokeBlocked("Execution blockers: " + ", ".join(checks["blockers"]))
        artifacts = build_plan_artifacts(root / "subset", parent, parent_hash,
                                         official_native_ids(settings), evaluators)
        frozen_path, binding = freeze_smoke_task(
            root / "frozen", config, parent_manifest_hash=parent_hash, subset_hash=artifacts["subset_hash"],
            settings_path=settings_path, pipeline_config_path=config_path)
        stages["services"] = {"status": "blocked"}
        start_services(config, config.task_checkpoint)
        fingerprint = verify_services(config, config.task_checkpoint)
        stages["services"] = {"status": "verified", "fingerprint": fingerprint}
        for source in CODE_QA:
            benchmarks[source] = _score_code_qa(source, artifacts["specs"][source], config,
                                                frozen_path, binding, root)
        benchmarks[BFCL] = _score_bfcl(settings, config, binding, artifacts["selected"], root)
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        if "services" not in stages:
            stages["services"] = {"status": "blocked", "error": failure}
        elif stages["services"].get("status") != "verified":
            stages["services"]["error"] = failure
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        cleanup = _cleanup(config, root)
    if artifacts is None:
        artifacts = {"subset_path": root / "subset/smoke_subset.json", "subset_hash": None,
                     "selected": {"tasks": [], "rule": SELECTION_RULE,
                                  "bfcl_unrecognized": [], "bfcl_recognized_total": 0}}
    receipt = _base_receipt("execute", root, parent, parent_hash, settings, artifacts, binding, checks)
    receipt["stages"] = stages
    receipt["benchmarks"] = benchmarks
    receipt["cleanup"] = cleanup
    receipt["output_root_validation"] = root_validation
    receipt["model_calls"] = sum(item.get("completed_model_calls", 0) for item in benchmarks.values())
    receipt["status"] = ("incomplete" if failure or any(item["status"] != "scored" for item in benchmarks.values())
                         or not benchmarks else "completed")
    if failure:
        receipt["error"] = failure
    receipt["no_complete_marker"] = True
    receipt["aggregate_score"] = None
    receipt["note"] = ("Per-benchmark denominators only (1/1/1/1 code+QA tasks, 50 BFCL complete tasks). "
                       "No cross-benchmark accuracy, no 694/744 claim, no training/Meta/candidate feedback.")
    save_json(root / "smoke_report.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    parser.add_argument("--execute", action="store_true",
                        help="run the real local-model smoke; requires a non-existent output root")
    args = parser.parse_args(argv)
    runner = execute if args.execute else plan
    try:
        receipt = runner(args.output_root, args.config, args.manifest, args.settings)
    except SmokeBlocked as exc:
        print(json.dumps({"ok": False, "status": "blocked", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": receipt["status"] in ("planned", "completed"), "mode": receipt["mode"],
                      "status": receipt["status"], "output_root": receipt["output_root"],
                      "selected_tasks": receipt["smoke_selected_tasks"],
                      "bfcl_unrecognized_count": receipt["bfcl_unrecognized_count"],
                      "execute_ready": receipt.get("execute_ready"),
                      "blockers": receipt["assets"]["blockers"]}, sort_keys=True))
    return 0 if receipt["status"] in ("planned", "completed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
