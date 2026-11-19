"""Read-only summarizer for the DeepCoder single-domain five-round canary.

`summarize(run_dir, config_path=None, cleanup_receipt_path=None) -> dict` recomputes
per-round observed facts from the real run files and validates them against the
production protocol. It never modifies scoring or selection and never invents scores.

Trusted inputs (F1/F5):
* `config_path` — the machine launcher config. The run/output/release paths must be
  naturally derived from it (run_dir == output_root/runs/run_name; release ==
  frozen_data_dir). With a trusted config the frozen release manifest verifies each
  B_r cohort by ID/order/record/payload/hash. WITHOUT a trusted config the observed
  arithmetic is still reported, but products_complete/protocol_complete/run_complete
  are blocked (internal self-consistency cannot prove frozen membership).
* `cleanup_receipt_path` — a `kind=rsiflow_external_cleanup` receipt produced by the
  actual external cleanup writer (shared schema with the launch-hardening writer).

Completion semantics (F5):
* `protocol_complete` — A0+B1-B5 real milestones/selection/active-chain/snapshots +
  finish after B5, verified from raw workflow/turns (not Markdown alone).
* `launcher_exited` — current supervision identity-bound exit/reconcile.
* `run_complete` — protocol_complete AND launcher_exited AND a completed, binding-valid
  external cleanup receipt with all required terminal owner/GPU evidence. A legal
  protocol finish whose external resources are pending is a named blocker, never a
  fabricated receipt; registry `services_stopped` is shown as a fact but cannot alone
  certify run_complete.

Per-round validation requires: attempt set exactly {attempt_1} with round/attempt
binding; frozen 16-unique cohort bound to the trusted manifest; real bool success with
full-verifier/positive-int tests evidence; side-scoped per-task/per-call persisted
receipt reconciliation using the production canonical request hash (no offsetting);
selection/compare/activate receipts bound to exact paths/hashes with strictly positive
gain for acceptance; full Task content identity across the snapshot/active chain; and
hash-verified snapshots. Protocol values such as `infrastructure_error` are preserved
verbatim; process anomalies are layered (all-row mentions vs zero-call negative subset
vs called/success-with-error) and associated with per-task transitions without re-scoring.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve()
_PROJECT = _HERE.parents[2]
for _entry in (_HERE.parent, _PROJECT / "RSIFlow", _PROJECT / "RSIFlow/runtime"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

EXPECTED_ROUNDS = 5
EXPECTED_COHORT = 16
TERMINAL_DECISIONS = {"accept_candidate", "retain_parent"}
REQUIRED_JOB_OPERATIONS = {
    "bootstrap", "run_parent", "run_candidate", "sft",
    "compare_scores", "activate_task", "snapshot_task_meta",
}
SUCCESS_JOB_STATUS = {
    "completed", "compared", "activated_as_requested", "snapshotted", "written", "read",
}
TERMINAL_FAILED_JOB_STATUS = {"exited", "tool_error", "job_failed"}
TASK_IDENTITY_FIELDS = ("generation", "model_ref", "checkpoint_path", "checkpoint_manifest", "artifacts")
REQUEST_ALIGN_FIELDS = ("messages", "tools", "seed", "temperature", "max_tokens", "chat_template_kwargs")


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _read_jsonl(path) -> list[dict]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append({"_parse_error": True})
    return rows


def _sha256(path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _value_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _artifact_manifest(directory):
    from rsiflow.task_meta.storage import artifact_manifest
    return artifact_manifest(directory)


def _protocol_hashes(config):
    """Read the effective production config; no executor/services are constructed.

    Single-round evidence predates the two schedule metadata keys added in Task 6.
    Its precise old projection is supported only for that registered single-round
    scope, never as an alternative protocol for a five-round run.
    """
    if config is None:
        return None
    from task_adapter import TaskAdapter
    value = TaskAdapter(_PROJECT / "RSIFlow", _PROJECT / "unused")._config(
        {"config_path": config["_config_path"]}).model_dump()
    hashes = {_fingerprint({"runtime_config": value, "stage": "train_evolution_v1"})}
    if config.get("experiment_scope") == "deepcoder_canary" and config.get("rounds") == 1:
        import copy
        historical = copy.deepcopy(value)
        for key in ("training_schedule", "unique_tasks"):
            historical["round_protocol"].pop(key, None)
        hashes.add(_fingerprint({"runtime_config": historical, "stage": "train_evolution_v1"}))
    return hashes


def _success(row: dict) -> bool:
    return (row.get("verification") or {}).get("success") is True


def _mentions(row: dict, kind: str) -> bool:
    text = str(row.get("error") or "")
    if kind == "enoent":
        return "ENOENT" in text or "Errno 2" in text or "FileNotFoundError" in text
    if kind == "eagain":
        return "EAGAIN" in text or "Errno 11" in text
    return False


def _side_stats(rows: list[dict]) -> dict:
    denominator = len(rows)
    successes = sum(1 for r in rows if _success(r))
    verification = {
        "completed": sum(1 for r in rows if (r.get("verification") or {}).get("status") == "completed"),
        "valid_answer": sum(1 for r in rows if r.get("valid_answer") is True),
        "full_verifier": sum(1 for r in rows if (r.get("verification") or {}).get("full_verifier") is True),
        "success": successes,
    }
    zero_rows = [r for r in rows if int(r.get("model_call_count") or 0) == 0]
    called_rows = [r for r in rows if int(r.get("model_call_count") or 0) > 0]
    success_rows = [r for r in rows if _success(r)]

    def layer(subset):
        return {"enoent": sum(1 for r in subset if _mentions(r, "enoent")),
                "eagain": sum(1 for r in subset if _mentions(r, "eagain"))}

    errors = {
        "execution_error_type": dict(sorted(Counter(r.get("execution_error_type") or "none" for r in rows).items())),
        "zero_call": len(zero_rows),
        "error_mentions_all_rows": layer(rows),
        "zero_call_negative": {"count": len(zero_rows), **layer(zero_rows)},
        "called_with_error": layer(called_rows),
        "success_with_error": layer(success_rows),
        "infrastructure_error_true": sum(1 for r in rows if r.get("infrastructure_error") is True),
    }
    return {
        "denominator": denominator,
        "successes": successes,
        "macro_success": (successes / denominator) if denominator else None,
        "ids": sorted(r.get("task_id") for r in rows),
        "manifest_hashes": sorted({r.get("manifest_hash") for r in rows}),
        "task_source_hashes": {r.get("task_id"): r.get("task_source_hash") for r in rows},
        "trajectory_calls": sum(int(r.get("model_call_count") or 0) for r in rows),
        "transport_calls": sum(len(r.get("transport_calls") or []) for r in rows),
        "verification": verification,
        "errors": errors,
    }


def _performance_valid(performance, rows, round_n, collection_stage) -> list[str]:
    reasons = []
    if not isinstance(performance, dict):
        return ["performance missing or unparseable"]
    if performance.get("status") != "complete":
        reasons.append("performance status not complete")
    if performance.get("total_rollouts") != EXPECTED_COHORT:
        reasons.append("performance total_rollouts != 16")
    if performance.get("round_id") != round_n:
        reasons.append("performance round_id mismatch")
    if performance.get("feedback_role") != "train_evolution":
        reasons.append("performance feedback_role not train_evolution")
    if performance.get("collection_stage") != collection_stage:
        reasons.append(f"performance collection_stage not {collection_stage}")
    if performance.get("evaluation_protocol") != "same_round_full_B_parent_child_v2":
        reasons.append("performance evaluation_protocol mismatch")
    code = (performance.get("domains") or {}).get("code") or {}
    successes = sum(1 for r in rows if _success(r))
    if code.get("denominator") != EXPECTED_COHORT:
        reasons.append("performance code denominator != 16")
    if code.get("correct") != successes:
        reasons.append("performance code correct inconsistent with trajectories")
    macro = performance.get("macro_success")
    if macro is None or abs(macro - successes / EXPECTED_COHORT) > 1e-9:
        reasons.append("performance macro_success inconsistent with trajectories")
    benchmark = (performance.get("benchmarks") or {}).get("deepcoder_taco") or {}
    if benchmark.get("count") != EXPECTED_COHORT or benchmark.get("successes") != successes:
        reasons.append("performance benchmark count/successes inconsistent")
    return reasons


def _cohort_valid(rows, performance, round_n, collection_stage) -> list[str]:
    reasons = []
    if any(r.get("_parse_error") for r in rows):
        reasons.append("unparseable trajectory row")
    if len(rows) != EXPECTED_COHORT:
        reasons.append(f"cohort size {len(rows)} != {EXPECTED_COHORT}")
    ids = [r.get("task_id") for r in rows]
    if len(set(ids)) != EXPECTED_COHORT or any(not tid for tid in ids):
        reasons.append("task IDs not 16 unique non-empty")
    reasons.extend(_performance_valid(performance, rows, round_n, collection_stage))
    manifest = (performance or {}).get("training_manifest") if isinstance(performance, dict) else None
    for r in rows:
        tid = r.get("task_id")
        if not r.get("task_source_hash"):
            reasons.append(f"empty task_source_hash {tid}")
        if not r.get("manifest_hash") or r.get("manifest_hash") != manifest:
            reasons.append(f"manifest_hash mismatch {tid}")
        if r.get("round_id") != round_n:
            reasons.append(f"row round_id mismatch {tid}")
        if r.get("source_role") != "train_evolution":
            reasons.append(f"row source_role mismatch {tid}")
        if r.get("collection_stage") != collection_stage:
            reasons.append(f"row collection_stage mismatch {tid}")
        verification = r.get("verification") or {}
        if verification.get("status") != "completed":
            reasons.append(f"verification not completed {tid}")
        if not isinstance(verification.get("success"), bool):
            reasons.append(f"success not a real bool {tid}")
        tests_total = verification.get("tests_total")
        tests_completed = verification.get("tests_completed")
        if _success(r):
            if (r.get("valid_answer") is not True or verification.get("full_verifier") is not True
                    or not isinstance(tests_total, int) or isinstance(tests_total, bool) or tests_total <= 0
                    or not isinstance(tests_completed, int) or isinstance(tests_completed, bool)
                    or tests_completed != tests_total):
                reasons.append(f"success lacks valid answer/full verifier/positive-int complete tests {tid}")
        if int(r.get("model_call_count") or 0) == 0 and _success(r):
            reasons.append(f"zero-call row marked success {tid}")
    return reasons


def _trusted_manifest(release_dir, round_n):
    """Load and integrity-check the frozen production manifest for a round."""
    import evolution_protocol as ep
    import data_protocol as dp
    path = Path(release_dir) / f"B{round_n}/manifest.json"
    value = ep.manifest(path, "train_evolution", round_n)  # validates role/round/hash/provenance
    for task in value["tasks"]:
        record_file = Path(task["record_file"])
        if record_file.is_symlink() or ep.file_hash(record_file) != task.get("record_sha256"):
            raise ValueError(f"record file hash mismatch {task['task_id']}")
        with record_file.open("rb") as stream:
            stream.seek(task["record_offset"])
            raw = stream.read(task["record_length"])
        record = json.loads(raw)
        if dp.text_hash(dp.public_text(record, task["source"])) != task.get("content_hash"):
            raise ValueError(f"record content hash mismatch {task['task_id']}")
    return value


def _frozen_cohort_valid(rows, manifest, round_n) -> list[str]:
    reasons = []
    if manifest is None:
        return ["trusted frozen manifest unavailable"]
    tasks = manifest.get("tasks", [])
    expected_ids = [t["task_id"] for t in tasks]
    observed_ids = [r.get("task_id") for r in rows]
    if observed_ids != expected_ids:
        reasons.append(f"B{round_n} task ID order/membership != frozen manifest")
    if manifest.get("manifest_hash") not in {r.get("manifest_hash") for r in rows}:
        reasons.append(f"B{round_n} manifest_hash != frozen manifest hash")
    by_id = {t["task_id"]: t for t in tasks}
    for r in rows:
        task = by_id.get(r.get("task_id"))
        if task is None:
            reasons.append(f"task {r.get('task_id')} absent from frozen manifest")
            continue
        if r.get("task_source_hash") != task.get("record_sha256"):
            reasons.append(f"task_source_hash != frozen record_sha256 {r.get('task_id')}")
        if r.get("manifest_hash") != manifest.get("manifest_hash"):
            reasons.append(f"row manifest_hash != frozen manifest hash {r.get('task_id')}")
    return reasons


def _verified_rows(path, cache_root):
    """Hydrate the production compact projection only through its sealed receipt."""
    rows = _read_jsonl(path)
    for i, row in enumerate(rows):
        ref = row.get("_full_rollout_ref")
        if ref is None:
            continue
        raw = ref.get("path") if isinstance(ref, dict) else None
        if not raw or not _within(raw, cache_root) or _sha256(raw) != ref.get("sha256"):
            row["_parse_error"] = True
            continue
        value = _read_json(raw) or {}
        full = value.get("row")
        if not isinstance(full, dict):
            row["_parse_error"] = True
            continue
        from rsiflow.task_meta.runtime_extensions import compact_rollout
        expected = compact_rollout(full, Path(raw))
        annotations = ("allocation_round_id", "execution_stage_id", "source_role", "purpose", "round_id",
                       "branch_id", "collection_stage", "manifest_hash", "harness_hash", "parent_policy_hash",
                       "success_verifier_version", "trajectory_id")
        expected.update({k: full[k] for k in annotations if k in row and k in full})
        if row != expected:
            row["_parse_error"] = True
            continue
        rows[i] = full
    return rows


def _side_calls(rows, cache_root, round_n, state, manifest=None, protocol_hash=None) -> dict:
    """Reconcile real Qwen calls per task against side-scoped persisted receipts.

    Recomputes state_hash = fingerprint(round_scope) and binds the full rollout
    receipt row/scope to the trusted Task state and frozen manifest member: scope
    harness/checkpoint/artifacts/protocol/manifest, binding.task_record == member
    content_hash, response.binding.checkpoint_path/weights == Task. Each call aligns
    transport<->persisted request payload/response by call_id with the production
    canonical request hash. Persisted outcome must match the trajectory outcome.
    Extra/duplicate/malformed calls block.
    """
    members = {t["task_id"]: t for t in (manifest or {}).get("tasks", [])}
    state_harness_hash = _sha256(state.get("harness_path")) if state and state.get("harness_path") else None
    state_artifacts = _artifact_manifest((state or {}).get("artifacts", {}).get("directory")) if state else None
    receipts = {}
    extra_bad = 0
    accounted_call_files = set()
    for cache_json in cache_root.glob("*/train_rollouts/*.json"):
        receipt = _read_json(cache_json)
        if not isinstance(receipt, dict):
            extra_bad += 1
            continue
        binding = receipt.get("binding") or {}
        key = (binding.get("task"), binding.get("state_hash"), binding.get("rollout"))
        receipts.setdefault(key, []).append((cache_json, receipt))

    checkpoint_manifest = (state or {}).get("checkpoint_manifest")
    model_ref = (state or {}).get("model_ref")
    trajectory_calls = transport_calls = persisted_receipts = 0
    per_task_ok = True
    for r in rows:
        tid, state_hash = r.get("task_id"), r.get("state_hash")
        rollout = r.get("rollout_id", 0)
        calls = int(r.get("model_call_count") or 0)
        trajectory_calls += calls
        transport = r.get("transport_calls") or []
        transport_calls += len(transport)
        matches = receipts.get((tid, state_hash, rollout), [])
        valid = len(matches) == 1
        persisted_count = 0
        if valid:
            cache_json, receipt = matches[0]
            binding = receipt.get("binding") or {}
            scope = binding.get("round_scope") or {}
            row_receipt = receipt.get("row") or {}
            from rsiflow.task_meta.durable import task_hash, load_task
            member = members.get(tid)
            if protocol_hash is not None and scope.get("protocol_hash") not in protocol_hash:
                valid = False
            expected_scope = {
                "role": "train_evolution", "purpose": "evolution_train", "round_id": round_n,
                "manifest_hash": r.get("manifest_hash"), "collection_stage": r.get("collection_stage"),
                "task_hash": task_hash(load_task(state)) if state else scope.get("task_hash"),
                "harness_sha256": state_harness_hash, "checkpoint_manifest": checkpoint_manifest,
                "artifacts": state_artifacts, "protocol_hash": scope.get("protocol_hash"),
            }
            expected_binding = {
                "task": tid, "task_source_hash": r.get("task_source_hash"),
                "state_hash": _fingerprint(expected_scope), "rollout": rollout, "probe": False,
                "round_scope": expected_scope,
                "task_record": member.get("content_hash") if member else binding.get("task_record"),
            }
            if (binding != expected_binding or state_hash != expected_binding["state_hash"]
                    or cache_json.parent.parent.name != state_hash):
                valid = False
            expected_row_identity = {
                "model_ref": model_ref, "harness_sha256": state_harness_hash,
                "harness_hash": state_harness_hash, "artifact_input_manifest": (state or {}).get("artifacts", {}).get("manifest"),
                "parent_policy_hash": _fingerprint(checkpoint_manifest), "success_verifier_version": scope.get("protocol_hash"),
                "trajectory_id": _fingerprint([expected_scope, tid, rollout]),
            }
            if any(r.get(k) != v for k, v in expected_row_identity.items()):
                valid = False
            if row_receipt != r:
                valid = False
            calls_dir = cache_json.parent / (cache_json.stem + ".calls")
            persisted = {}
            if calls_dir.is_dir():
                for call_file in calls_dir.glob("*.json"):
                    accounted_call_files.add(call_file)
                    call = _read_json(call_file)
                    if not isinstance(call, dict):
                        extra_bad += 1
                        valid = False
                        continue
                    persisted.setdefault(call.get("call_id"), []).append(call)
            persisted_count = sum(len(v) for v in persisted.values())
            if len(persisted) != calls or set(persisted) != set(range(calls)):
                valid = False
            else:
                for call_id in range(calls):
                    entries = persisted[call_id]
                    if len(entries) != 1:
                        valid = False
                        break
                    call = entries[0]
                    cbinding = call.get("binding") or {}
                    response = call.get("response") or {}
                    request = call.get("request")
                    tc = transport[call_id] if call_id < len(transport) else {}
                    resp_binding = response.get("binding") or {}
                    # transport status/usage fields evolve after dispatch; align the
                    # canonical request payload (messages/tools/params) by call_id.
                    expected_request = {k: tc.get(k) for k in REQUEST_ALIGN_FIELDS}
                    expected_request.update(call_id=call_id, status="started", usage_complete=False, unknown_usage_calls=1)
                    request_aligned = request == expected_request
                    if (call.get("status") != "completed"
                            or not request_aligned
                            or call.get("request_sha256") != _value_hash(request)
                            or response != tc.get("response")
                            or not response.get("model_ref_response")
                            or response.get("model_ref_requested") != response.get("model_ref_response")
                            or (model_ref is not None and response.get("model_ref_response") != model_ref)
                            or (checkpoint_manifest is not None and resp_binding.get("weights") != checkpoint_manifest)
                            or (state is not None and state.get("checkpoint_path") is not None
                                and resp_binding.get("checkpoint_path") != state.get("checkpoint_path"))
                            or cbinding != expected_binding):
                        valid = False
                        break
        persisted_receipts += persisted_count
        if len(transport) != calls:
            valid = False
        else:
            for i, tc in enumerate(transport):
                if tc.get("call_id") != i or tc.get("status") != "completed":
                    valid = False
                    break
        per_task_ok = per_task_ok and valid
    all_call_files = set(cache_root.glob("*/train_rollouts/*.calls/*.json"))
    cross_check_ok = (per_task_ok and extra_bad == 0 and all_call_files == accounted_call_files
                      and trajectory_calls == transport_calls == persisted_receipts)
    return {"trajectory_calls": trajectory_calls, "transport_calls": transport_calls,
            "persisted_receipts": persisted_receipts, "cross_check_ok": cross_check_ok}


def _transitions(parent_rows, candidate_rows) -> dict:
    parent = {r.get("task_id"): _success(r) for r in parent_rows}
    candidate = {r.get("task_id"): _success(r) for r in candidate_rows}
    counts = {"new_success": 0, "new_regression": 0, "retained_failure": 0, "retained_success": 0}
    for tid in set(parent) | set(candidate):
        before, after = parent.get(tid, False), candidate.get(tid, False)
        if after and not before:
            counts["new_success"] += 1
        elif before and not after:
            counts["new_regression"] += 1
        elif before and after:
            counts["retained_success"] += 1
        else:
            counts["retained_failure"] += 1
    return counts


def _zero_call_overlap(parent_rows, candidate_rows) -> dict:
    parent_calls = {r.get("task_id"): int(r.get("model_call_count") or 0) for r in parent_rows}
    candidate_calls = {r.get("task_id"): int(r.get("model_call_count") or 0) for r in candidate_rows}
    parent_success = {r.get("task_id"): _success(r) for r in parent_rows}
    candidate_success = {r.get("task_id"): _success(r) for r in candidate_rows}

    def zero(tid):
        return parent_calls.get(tid, 0) == 0 or candidate_calls.get(tid, 0) == 0

    return {
        "new_success_zero_call": sum(1 for tid in candidate_success
                                     if candidate_success[tid] and not parent_success.get(tid, False) and zero(tid)),
        "new_regression_zero_call": sum(1 for tid in parent_success
                                        if parent_success[tid] and not candidate_success.get(tid, False) and zero(tid)),
    }


def _task_transition_details(parent_rows, candidate_rows) -> list[dict]:
    parent = {r.get("task_id"): r for r in parent_rows}
    candidate = {r.get("task_id"): r for r in candidate_rows}
    details = []
    for tid in sorted(set(parent) | set(candidate)):
        p, c = parent.get(tid, {}), candidate.get(tid, {})
        before, after = _success(p), _success(c)
        transition = ("new_success" if after and not before else
                      "new_regression" if before and not after else
                      "retained_success" if before and after else "retained_failure")
        details.append({
            "task_id": tid, "transition": transition,
            "parent_calls": int(p.get("model_call_count") or 0),
            "candidate_calls": int(c.get("model_call_count") or 0),
            "parent_success": before, "candidate_success": after,
            "parent_infrastructure_error": p.get("infrastructure_error"),
            "candidate_infrastructure_error": c.get("infrastructure_error"),
            "parent_errno": {"enoent": _mentions(p, "enoent"), "eagain": _mentions(p, "eagain")},
            "candidate_errno": {"enoent": _mentions(c, "enoent"), "eagain": _mentions(c, "eagain")},
        })
    return details


def _find_transport_receipts(run_dir, operation) -> list[dict]:
    found = []
    for result_file in (run_dir / "controller_transport").glob("*/result.json"):
        receipt = _read_json(result_file)
        if isinstance(receipt, dict) and receipt.get("operation") == operation:
            found.append(receipt)
    return found


def _find_turn_receipts(run_dir, operation) -> list[dict]:
    found = []
    for receipt_file in (run_dir / "meta_session/turns").glob("*/receipt.json"):
        receipt = _read_json(receipt_file)
        if isinstance(receipt, dict) and receipt.get("operation") == operation:
            found.append(receipt)
    return found


def _receipt_for_round(receipts, round_n, path_keys) -> dict | None:
    marker = f"round_{round_n}/"
    for receipt in receipts:
        for key in path_keys:
            value = receipt.get(key)
            if isinstance(value, str) and marker in value:
                return receipt
    return None


def _task_identity(state) -> dict:
    if not isinstance(state, dict):
        return {}
    identity = {k: state.get(k) for k in TASK_IDENTITY_FIELDS if k != "artifacts"}
    # Artifacts identity is the content manifest, not the (relocatable) directory.
    identity["artifacts_manifest"] = state.get("artifacts_manifest", (state.get("artifacts") or {}).get("manifest"))
    if "harness_sha256" in state:
        identity["harness_sha256"] = state["harness_sha256"]
    else:
        harness_path = state.get("harness_path")
        identity["harness_sha256"] = _sha256(harness_path) if harness_path else None
    return identity


def _identity_equal(a, b) -> bool:
    ia, ib = _task_identity(a), _task_identity(b)
    if not ia or not ib:
        return False
    for k in TASK_IDENTITY_FIELDS:
        if k == "artifacts":
            if ia.get("artifacts_manifest") != ib.get("artifacts_manifest"):
                return False
        elif ia.get(k) != ib.get(k):
            return False
    # harness content identity allows path relocation but requires equal bytes hash
    return ia.get("harness_sha256") == ib.get("harness_sha256") and ia.get("harness_sha256") is not None


def _within(path, root):
    try:
        return Path(path).resolve().is_relative_to(Path(root).resolve()) and not Path(path).is_symlink()
    except (TypeError, OSError, ValueError):
        return False


def _snapshot_valid(receipt_path, attempt_dir) -> tuple[bool, dict]:
    receipt_path, attempt_dir = Path(receipt_path), Path(attempt_dir)
    info = {"parent": None, "candidate": None, "selected": None}
    receipt = _read_json(receipt_path)
    if not isinstance(receipt, dict) or receipt.get("status") != "snapshotted":
        return False, info
    snap = receipt_path.parent
    roles = ("selected",) if snap.name == "A0" else ("parent", "candidate", "selected")
    combinations, seals = receipt.get("combinations") or {}, receipt.get("files_sha256") or {}
    paths = {role: "task/task_state_snapshot.json" if role == "selected" else f"task/{role}_state.json"
             for role in roles}
    required = {*paths.values(), "versions.json", "task/checkpoint_reference.json"}
    if set(combinations) != set(roles) or not required <= set(seals):
        return False, info
    if receipt.get("destination") != str(snap):
        return False, info
    for rel, expected in seals.items():
        if not _within(snap / rel, snap) or _sha256(snap / rel) != expected:
            return False, info
    versions = _read_json(snap / "versions.json")
    if not isinstance(versions, dict) or any(versions.get(k) != receipt.get(k)
            for k in ("combinations", "meta", "references", "versions_root", "index_path")):
        return False, info
    index = _read_json(receipt.get("index_path")) if receipt.get("index_path") else None
    if not isinstance(index, dict):
        return False, info
    from component_versions import content_hash, fingerprint
    for role, rel in paths.items():
        item = combinations[role]
        state_path = snap / rel
        if item.get("state_path") != str(state_path):
            return False, info
        state = _read_json(state_path)
        if not isinstance(state, dict):
            return False, info
        artifacts = state.get("artifacts") or {}
        try:
            if _artifact_manifest(artifacts.get("directory")) != artifacts.get("manifest"):
                return False, info
        except (OSError, ValueError):
            return False, info
        source = (snap.parent.parent / "active_task.json" if role == "selected"
                  else attempt_dir / ("before/parent_task.json" if role == "parent" else "build/candidate_state.json"))
        if item.get("source_state") != str(source):
            return False, info
        for kind in ("model", "harness", "artifacts"):
            if kind == "artifacts" and not (state.get("artifacts") or {}).get("directory"):
                continue
            entry = next((e for e in index.get(kind, []) if e.get("id") == item.get(kind)), None)
            if not entry or (kind != "artifacts" and not Path(entry.get("path", "")).is_file()):
                return False, info
            try:
                value = ({k: state.get(k) for k in ("model_ref", "checkpoint_path", "checkpoint_manifest")}
                         | {"weights_copied": False}) if kind == "model" else None
                if kind == "model":
                    if _read_json(entry["path"]) != value or fingerprint(value) != entry["sha256"]:
                        return False, info
                elif content_hash(Path(entry["path"])) != entry["sha256"]:
                    return False, info
                elif kind == "harness" and _sha256(state.get("harness_path")) != entry["sha256"]:
                    return False, info
                elif kind == "artifacts" and _artifact_manifest(state["artifacts"]["directory"]) != state["artifacts"]["manifest"]:
                    return False, info
            except (OSError, ValueError, KeyError):
                return False, info
        info[role] = state
    selected = info["selected"]
    reference = {k: selected.get(k) for k in ("model_ref", "checkpoint_path", "checkpoint_manifest")}
    if _read_json(snap / "task/checkpoint_reference.json") != {**reference, "weights_copied": False}:
        return False, info
    meta = receipt.get("meta") or {}
    if not meta.get("path") or _sha256(meta["path"]) != meta.get("sha256"):
        return False, info
    if not any(e == meta for e in index.get("meta", [])):
        return False, info
    context = receipt.get("context_reference")
    if not isinstance(context, dict) or context != (receipt.get("references") or {}).get("context_reference"):
        return False, info
    if not context.get("path") or not Path(context["path"]).is_file() or not context.get("sha256"):
        return False, info
    if _sha256(context["path"]) != context["sha256"]:
        # The production handoff is mutable. Its historical hash is sealed in
        # versions.json and must also match the original successful snapshot turn.
        turns = _find_turn_receipts(snap.parent.parent, "snapshot_task_meta")
        if not any(t.get("status") == "snapshotted" and t.get("destination") == str(snap)
                   and t.get("context_reference") == context for t in turns):
            return False, info
    return True, info


def _sampling_valid(metrics, count):
    try:
        if type(count) is not int or count < 1:
            return False
        ranks = metrics.get("rank_records") or []
        per_rank = math.ceil(count / 4)
        indices = [i for rank in ranks for i in rank["sampled_indices"]]
        accumulation = metrics.get("gradient_accumulation_steps")
        if type(accumulation) is not int or accumulation < 4 or accumulation % 4 or metrics.get("batch_size") != 1:
            return False
        steps = math.ceil(per_rank / (accumulation // 4))
        if (len(ranks) != 4 or any(type(r.get(k)) is not int for r in ranks for k in
                                  ("rank", "local_rank", "optimizer_steps"))
                or any(type(metrics.get(k)) is not int for k in
                       ("optimizer_steps", "sampled_training_examples", "positive_trajectories", "world_size", "batch_size"))
                or type(metrics.get("num_train_epochs_completed")) not in (int, float)):
            return False
        # BatchSamplerShard interleaves ranks, then pads the final batch with
        # the beginning of this same epoch. Verify the observed padding rather
        # than accepting arbitrary duplicate examples with matching totals.
        ordered_ranks = sorted(ranks, key=lambda r: r["rank"])
        if any(len(r["sampled_indices"]) != per_rank for r in ranks):
            return False
        ordered = [r["sampled_indices"][i] for i in range(per_rank) for r in ordered_ranks]
        if len(set(ordered[:count])) != count or ordered[count:] != ordered[:len(ordered) - count]:
            return False
        return (len(ranks) == 4 and {r["rank"] for r in ranks} == {0, 1, 2, 3}
                and all(r.get("local_rank") == r["rank"] and len(r["sampled_indices"]) == per_rank
                        and r.get("optimizer_steps") == steps for r in ranks)
                and all(type(i) is int and i in range(count) for i in indices)
                and set(indices) == set(range(count)) and indices == metrics.get("sampled_row_indices")
                and metrics.get("sampled_training_examples") == per_rank * 4
                and metrics.get("positive_trajectories") == count
                and metrics.get("optimizer_steps") == steps and metrics.get("world_size") == 4
                and metrics.get("num_train_epochs_completed") == 1)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return False


def _model_round_evidence(run_dir, number, parent_state):
    attempt = run_dir / f"round_{number}/attempts/attempt_1"
    pending = (False, "selected_pending")
    failed = (False, "selected_failed")
    records = []
    for jobfile in (run_dir / "meta_session/jobs").glob("*/job.json"):
        job = _read_json(jobfile) or {}
        request = job.get("request") or {}
        if request.get("operation") != "sft" or request.get("round_number") != number:
            continue
        result = _read_json(job.get("result_path")) or {}
        records.append((job, request, result))
    if not records:
        return pending
    if len(records) != 1:
        return failed
    job, request, result = records[0]
    directory = Path(request.get("output_dir", "/missing"))
    model_update = directory / "model_update"
    if (not _within(directory, attempt) or not _within(model_update, attempt)
            or result.get("status") != "completed"
            or result.get("training_metrics_path") != str(model_update / "training_metrics.json")
            or result.get("candidate_state_path") != str(directory / "candidate_state.json")
            or request.get("parent_rollout_dir") != str(run_dir / f"round_{number}/parent")
            or not _identity_equal(_read_json(request.get("state_path")), parent_state)):
        return failed
    identifier = job.get("job_id")
    if (job.get("result_path") != str(run_dir / "controller_transport" / str(identifier) / "result.json")
            or _read_json(Path(job["result_path"]).parent / "request.json") != request):
        return failed
    command = _read_json(Path(job.get("turn_dir", "/missing")) / "command.json") or {}
    turn = _read_json(Path(job.get("turn_dir", "/missing")) / "receipt.json") or {}
    if command.get("request") != request or not _within(job.get("turn_dir"), run_dir / "meta_session/turns"):
        return failed
    if turn != result and not (turn.get("status") == "running" and turn.get("job_id") == identifier
                               and turn.get("result_path") == job.get("result_path")):
        return failed
    metrics = _read_json(model_update / "training_metrics.json")
    training = _read_json(model_update / "training_request.json")
    if not isinstance(metrics, dict) or not isinstance(training, dict):
        return pending
    try:
        from rsiflow.task_meta.sft import select_positive_rows, deduplicate_rows, validate_training_row
        parent_rows = _verified_rows(run_dir / f"round_{number}/parent/train_trajectories.jsonl", run_dir / f"round_{number}/rollout_cache")
        if _read_jsonl(model_update / "rollouts.jsonl") != parent_rows:
            return failed
        expected = select_positive_rows(parent_rows, profile="multidomain")
        eligibility = training.get("length_eligibility") or {}
        excluded = eligibility.get("excluded", [])
        excluded_indices = set()
        for entry in excluded:
            index = entry["row_index"]
            if (type(index) is not int or index not in range(len(expected)) or index in excluded_indices
                    or entry.get("source_sha256") != hashlib.sha256(json.dumps(
                        expected[index], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    or not str(entry.get("reason", "")).startswith(("exceeds_max_length", "ineligible_supervision:"))):
                return failed
            excluded_indices.add(index)
        positives = _read_jsonl(model_update / "sft_positive.jsonl")
        if not positives or positives != [r for i, r in enumerate(expected) if i not in excluded_indices]:
            return failed
        for row in positives:
            validate_training_row(row, "multidomain")
        unique, selection = deduplicate_rows(positives)
        selection["deduplication_applied"] = True
        if (metrics.get("selection") != selection or _read_json(model_update / "sft_selection.json") != selection
                or training.get("positive_samples") != len(positives)
                or training.get("checkpoint_path") != parent_state.get("checkpoint_path")
                or training.get("model_ref") != parent_state.get("model_ref")):
            return failed
        if (not _sampling_valid(metrics, len(unique))
                or type(metrics.get("training_loss")) not in (int, float) or not math.isfinite(metrics["training_loss"])
                or any(type(metrics.get(k)) not in (int, float) or not math.isfinite(metrics[k]) or metrics[k] <= 0
                       for k in ("changed_lora_tensors", "lora_delta_l2", "merged_probe_changed_elements"))):
            return failed
        steps = metrics["optimizer_steps"]
    except (KeyError, TypeError, ValueError, OSError):
        return failed
    before = _read_json(model_update / "checkpoint_before.json")
    child = _read_json(directory / "candidate_state.json")
    if (before != {"path": parent_state.get("checkpoint_path"), "weights": parent_state.get("checkpoint_manifest")}
            or not isinstance(child, dict) or child.get("generation") != parent_state.get("generation", -1) + 1
            or not _identity_equal(child, _read_json(attempt / "build/candidate_state.json"))
            or child.get("checkpoint_path") != str(model_update / "checkpoint")
            or child.get("checkpoint_path") != metrics.get("checkpoint_path")
            or child.get("checkpoint_manifest") == parent_state.get("checkpoint_manifest")):
        return failed
    trained = _read_json(model_update / "checkpoint_trained.json") or {}
    verified = _read_json(model_update / "checkpoint_verified.json") or {}
    binding = {"checkpoint_path": child["checkpoint_path"], "weights": child["checkpoint_manifest"]}
    for receipt in (trained, verified):
        if any(receipt.get(k) != v for k, v in {
                "checkpoint_path": child["checkpoint_path"], "model_ref": child["model_ref"],
                "training_method": "positive_sft_lora", "training_steps": steps}.items()):
            return failed
    if (trained.get("training_metrics") != str(model_update / "training_metrics.json")
            or verified.get("weights") != child["checkpoint_manifest"] or verified.get("served_binding") != binding):
        return failed
    gpu = _read_json(model_update / "gpu_phase_receipt.json") or {}
    if (gpu.get("status") != "completed" or gpu.get("gpus") != [0, 1, 2, 3]
            or gpu.get("replica_gpus") != [0, 1, 2, 3] or gpu.get("restored_checkpoint") != child["checkpoint_path"]):
        return failed
    rows = _verified_rows(attempt / "candidate/train_trajectories.jsonl", attempt / "rollout_cache")
    if not rows or any(r.get("model_ref") != child["model_ref"]
                       or r.get("parent_policy_hash") != _fingerprint(child["checkpoint_manifest"]) for r in rows):
        return failed
    return True, "completed"


def _model_sft(rounds, run_dir, round_parent_states) -> dict:
    model_rounds = [s["round"] for s in rounds if s.get("component") == "MODEL"]
    if not model_rounds:
        return {"status": "not_selected", "rounds": []}
    completed, pending, failed = [], [], []
    for number in model_rounds:
        ok, status = _model_round_evidence(run_dir, number, round_parent_states.get(number))
        if ok:
            completed.append(number)
        elif status == "selected_failed":
            failed.append(number)
        else:
            pending.append(number)
    status = "selected_pending" if pending else ("selected_failed" if failed else "completed")
    return {"status": status, "rounds": completed, "pending_rounds": pending, "failed_rounds": failed}


def _round_summary(run_dir, number, trusted_manifest, protocol_hash=None) -> dict | None:
    round_dir = run_dir / f"round_{number}"
    if not round_dir.is_dir():
        return None
    blockers = []
    parent_rows = _verified_rows(round_dir / "parent/train_trajectories.jsonl", round_dir / "rollout_cache")
    attempts = sorted(round_dir.glob("attempts/attempt_*"))
    attempt_names = {a.name for a in attempts}
    attempt = round_dir / "attempts/attempt_1"
    if attempt_names != {"attempt_1"}:
        blockers.append(f"attempt set {sorted(attempt_names)} != {{attempt_1}}")
    candidate_rows = _verified_rows(attempt / "candidate/train_trajectories.jsonl", attempt / "rollout_cache")
    decision = _read_json(attempt / "decision.json")
    selection = _read_json(attempt / "selection.json")
    parent_performance = _read_json(round_dir / "parent/performance.json")
    candidate_performance = _read_json(attempt / "candidate/performance.json")
    parent_state = _read_json(attempt / "before/parent_task.json")
    candidate_state = _read_json(attempt / "build/candidate_state.json")

    if not isinstance(decision, dict) or decision.get("round") != number or decision.get("attempt") != 1:
        blockers.append("decision round/attempt binding invalid")
    if not isinstance(selection, dict) or selection.get("round") != number or selection.get("attempt") != 1:
        blockers.append("selection round/attempt binding invalid")

    parent = _side_stats(parent_rows)
    candidate = _side_stats(candidate_rows)
    blockers.extend(f"parent: {x}" for x in _cohort_valid(parent_rows, parent_performance, number, "parent_pre_update"))
    blockers.extend(f"candidate: {x}" for x in _cohort_valid(candidate_rows, candidate_performance, number, "child_post_update"))
    if trusted_manifest is not None:
        blockers.extend(f"parent frozen: {x}" for x in _frozen_cohort_valid(parent_rows, trusted_manifest, number))
        blockers.extend(f"candidate frozen: {x}" for x in _frozen_cohort_valid(candidate_rows, trusted_manifest, number))
    else:
        blockers.append("trusted config/release missing: frozen cohort membership unverified")

    parent_calls = _side_calls(parent_rows, round_dir / "rollout_cache", number, parent_state,
                               trusted_manifest, protocol_hash)
    candidate_calls = _side_calls(candidate_rows, attempt / "rollout_cache", number, candidate_state,
                                  trusted_manifest, protocol_hash)
    parent["persisted_receipts"] = parent_calls["persisted_receipts"]
    candidate["persisted_receipts"] = candidate_calls["persisted_receipts"]
    cross_check_ok = parent_calls["cross_check_ok"] and candidate_calls["cross_check_ok"]
    if not cross_check_ok:
        blockers.append("per-task/per-call persisted receipt reconciliation failed")

    denominator_match = (parent["denominator"] == candidate["denominator"] == EXPECTED_COHORT)
    ids_match = parent["ids"] == candidate["ids"] and len(set(parent["ids"])) == EXPECTED_COHORT
    source_hashes_match = (parent["task_source_hashes"] == candidate["task_source_hashes"]
                           and parent["manifest_hashes"] == candidate["manifest_hashes"]
                           and all(parent["task_source_hashes"].values()))
    if not (denominator_match and ids_match and source_hashes_match):
        blockers.append("paired denominator/IDs/source hashes not exactly consistent")

    transitions = _transitions(parent_rows, candidate_rows)
    delta = None
    if parent["macro_success"] is not None and candidate["macro_success"] is not None:
        delta = candidate["macro_success"] - parent["macro_success"]
    decision_value = (selection or {}).get("decision")
    terminal = decision_value in TERMINAL_DECISIONS
    if not terminal:
        blockers.append(f"selection decision {decision_value!r} not terminal")
    if isinstance(selection, dict):
        for key, expected in [("before", parent["macro_success"]), ("after", candidate["macro_success"]),
                              ("delta", delta), ("paired_tasks", EXPECTED_COHORT),
                              ("new_success", transitions["new_success"]),
                              ("new_regression", transitions["new_regression"])]:
            actual = selection.get(key)
            if expected is None or actual is None or abs(actual - expected) > 1e-9:
                blockers.append(f"selection {key} inconsistent with recomputed facts")

    parent_perf_path = round_dir / "parent/performance.json"
    candidate_perf_path = attempt / "candidate/performance.json"
    compare = _receipt_for_round(_find_transport_receipts(run_dir, "compare_scores"),
                                 number, ("before_path", "after_path"))
    if not isinstance(compare, dict) or compare.get("status") != "compared":
        blockers.append("compare_scores receipt missing/not compared")
    else:
        if (compare.get("before_path") != str(parent_perf_path)
                or compare.get("after_path") != str(candidate_perf_path)
                or compare.get("before_sha256") != _sha256(parent_perf_path)
                or compare.get("after_sha256") != _sha256(candidate_perf_path)
                or delta is None or abs(compare.get("delta", 0) - delta) > 1e-9):
            blockers.append("compare_scores paths/hash/delta not bound to current attempt")
    differences = _read_json(attempt / "task_differences/summary.json")
    if not isinstance(differences, dict) or differences.get("status") != "compared":
        blockers.append("task_differences summary missing/not compared")
    else:
        counts = differences.get("counts") or {}
        if (differences.get("paired_tasks") != EXPECTED_COHORT
                or differences.get("same_id_set") is not True
                or differences.get("source_mismatches")
                or (differences.get("duplicates") or {}).get("before")
                or (differences.get("duplicates") or {}).get("after")
                or differences.get("missing_in_after") or differences.get("extra_in_after")):
            blockers.append("task_differences pairing not clean")
        for key in ("new_success", "new_regression", "retained_failure"):
            if counts.get(key) != transitions[key]:
                blockers.append(f"task_differences {key} inconsistent")

    activated = False
    candidate_state_path = attempt / "build/candidate_state.json"
    if decision_value == "accept_candidate":
        if delta is None or delta <= 0:
            blockers.append("accept_candidate without strictly positive gain")
        activate = _receipt_for_round(_find_transport_receipts(run_dir, "activate_task"),
                                      number, ("source_state", "active_state"))
        if (isinstance(activate, dict) and activate.get("status") == "activated_as_requested"
                and activate.get("source_state") == str(candidate_state_path)
                and activate.get("source_sha256") == _sha256(candidate_state_path)
                and activate.get("active_sha256") == activate.get("source_sha256")
                and activate.get("source_sha256")):
            activated = True
        else:
            blockers.append("accept_candidate lacks bound activate_task receipt")
    elif decision_value == "retain_parent":
        activate = _receipt_for_round(_find_transport_receipts(run_dir, "activate_task"),
                                      number, ("source_state", "active_state"))
        if isinstance(activate, dict) and activate.get("status") == "activated_as_requested":
            blockers.append("retain_parent must not activate candidate")

    snapshot_receipt = run_dir / "snapshots" / f"round_{number}_attempt_1/snapshot_receipt.json"
    snapshot_present = snapshot_receipt.is_file()
    snap_ok, snap_states = _snapshot_valid(snapshot_receipt, attempt)
    if not snap_ok:
        blockers.append("snapshot receipt missing/invalid/hash mismatch")
    else:
        if not _identity_equal(snap_states["parent"], parent_state):
            blockers.append("snapshot parent identity != attempt parent state")
        if not _identity_equal(snap_states["candidate"], candidate_state):
            blockers.append("snapshot candidate identity != attempt candidate state")
        expected_selected = candidate_state if decision_value == "accept_candidate" else parent_state
        if not _identity_equal(snap_states["selected"], expected_selected):
            blockers.append("snapshot selected identity != accept candidate / retain parent")

    component = (decision or {}).get("component")
    if component == "MODEL":
        sft_ok, sft_status = _model_round_evidence(run_dir, number, parent_state)
        if not sft_ok:
            blockers.append(f"MODEL selected but SFT evidence {sft_status}")

    products_complete = not blockers
    return {
        "round": number,
        "component": component,
        "attempt_count": len(attempts),
        "selection": selection,
        "activated": activated,
        "terminal": terminal,
        "products_complete": products_complete,
        "product_blockers": blockers,
        "parent": parent,
        "candidate": candidate,
        "parent_state": parent_state,
        "candidate_state": candidate_state,
        "snapshot_states": snap_states,
        "denominator_match": denominator_match,
        "ids_match": ids_match,
        "source_hashes_match": source_hashes_match,
        "calls": {"parent": {k: parent_calls[k] for k in ("trajectory_calls", "transport_calls", "persisted_receipts")},
                  "candidate": {k: candidate_calls[k] for k in ("trajectory_calls", "transport_calls", "persisted_receipts")},
                  "cross_check_ok": cross_check_ok},
        "verification": {"parent": parent["verification"], "candidate": candidate["verification"]},
        "errors": {"parent": parent["errors"], "candidate": candidate["errors"]},
        "delta": delta,
        "transitions": transitions,
        "zero_call_overlap": _zero_call_overlap(parent_rows, candidate_rows),
        "task_transitions": _task_transition_details(parent_rows, candidate_rows),
        "snapshot_present": snapshot_present,
        "evidence_hashes": {
            "parent_trajectories": _sha256(round_dir / "parent/train_trajectories.jsonl"),
            "parent_performance": _sha256(parent_perf_path),
            "candidate_trajectories": _sha256(attempt / "candidate/train_trajectories.jsonl"),
            "candidate_performance": _sha256(candidate_perf_path),
            "decision": _sha256(attempt / "decision.json"),
            "selection": _sha256(attempt / "selection.json"),
            "snapshot_receipt": _sha256(snapshot_receipt),
        },
    }


def _run_status(run_dir) -> dict:
    state = _read_json(run_dir / "meta_session/state.json") or {}
    text = ""
    try:
        text = (run_dir / "meta_session/RUN_STATUS.md").read_text(encoding="utf-8")
    except OSError:
        pass
    stage = re.search(r"Current stage:\s*\*\*([^*]+)\*\*", text)
    finish = re.search(r"Finish allowed:\s*\*\*([^*]+)\*\*", text)
    return {
        "status": state.get("status"),
        "last_turn_status": state.get("last_turn_status"),
        "current_stage": stage.group(1).strip() if stage else None,
        "finish_allowed": (finish.group(1).strip().lower() == "yes") if finish else False,
    }


def _ordered_turns(run_dir) -> list[dict]:
    from experiment_progress import _explicit_round
    turns = []
    for turn_dir in sorted((run_dir / "meta_session/turns").glob("*")):
        if not turn_dir.is_dir() or not turn_dir.name.isdigit():
            continue
        command = _read_json(turn_dir / "command.json") or {}
        receipt = _read_json(turn_dir / "receipt.json") or {}
        request = command.get("request") or {}
        if receipt.get("status") == "running" and _within(receipt.get("result_path"), run_dir / "controller_transport"):
            receipt = _read_json(receipt["result_path"]) or receipt
        turns.append({"name": turn_dir.name, "operation": request.get("operation"),
                      "round_number": _explicit_round(request, receipt),
                      "status": receipt.get("status"), "remaining": receipt.get("remaining"),
                      "request": request, "receipt": receipt})
    return turns


def _workflow_milestones(run_dir, config=None) -> dict:
    # Reuse the production recognizers and ordering rules, without calling the
    # write-producing ExperimentProgress.rebuild().
    import experiment_progress as ep
    workflow = _read_json(run_dir / "meta_session/workflow_state.json") or {}
    policy = _read_json(run_dir / "meta_session/workflow_policy.json") or {}
    invalid = []
    expected_policy = {k: (config or {}).get(k) for k in
                       ("experiment_scope", "rounds", "evaluation_schedule", "retry_rejected_from_round")}
    if config and any(policy.get(k) != v for k, v in expected_policy.items()):
        invalid.append("trusted policy mismatch")
    try:
        ep.validate_workflow_policy(policy)
    except ValueError:
        invalid.append("invalid policy")
    count = policy.get("rounds", EXPECTED_ROUNDS)
    if type(count) is not int or count not in (1, EXPECTED_ROUNDS):
        count = EXPECTED_ROUNDS
        invalid.append("invalid rounds")
    stages = ep._stage_template(count)
    for stage in stages:
        stage["required_milestones"] = [m for m in stage["required_milestones"]
                                        if m not in {"validation_snapshot", "independent_validation"}]
        ep._refresh_stage(stage)
    five = policy.get("experiment_scope") == ep.FIVE_ROUND_CANARY_SCOPE
    for turn in _ordered_turns(run_dir):
        request, receipt = turn["request"], turn["receipt"]
        operation = turn["operation"]
        milestone = ep._milestone(operation, request)
        current = next((s for s in stages if s["remaining_milestones"]), None)
        number = turn["round_number"]
        if number is None:
            number = current["round"] if current else count
        stage = next((s for s in stages if s["round"] == number), None)
        if not stage or milestone not in stage["required_milestones"] or not ep._successful(operation, receipt):
            continue
        if five and ep._five_round_rejection(stage=stage, current=current, milestone=milestone,
                                             request=request, receipt=receipt):
            continue
        if milestone not in stage["completed_milestones"]:
            stage["completed_milestones"].append(milestone)
        if (milestone == "task_meta_snapshot" and stage["round"] > 0
                and request.get("context_path") == (receipt.get("context_reference") or {}).get("source")):
            if "context_updated" not in stage["completed_milestones"]:
                stage["completed_milestones"].append("context_updated")
        evidence = {"milestone": milestone, "turn": turn["name"], "operation": operation,
                    "receipt_status": receipt.get("status"), "paths": ep._path_refs(receipt)}
        stage["evidence"] = [e for e in stage["evidence"] if e["milestone"] != milestone] + [evidence]
        ep._refresh_stage(stage)
    observed = {s.get("id"): s for s in workflow.get("stages", []) if isinstance(s, dict)}
    for stage in stages:
        actual = observed.get(stage["id"], {})
        if stage["remaining_milestones"] or any(actual.get(k) != stage[k] for k in
                ("required_milestones", "completed_milestones", "remaining_milestones", "evidence", "status")):
            invalid.append(stage["id"])
        for proof in stage["evidence"]:
            if not proof["paths"] or any(not Path(p).exists() for p in proof["paths"]):
                invalid.append(stage["id"] + ":missing evidence")
    invalid.extend(f"B{i}" for i in range(count + 1, EXPECTED_ROUNDS + 1))
    return {"current_stage": workflow.get("current_stage"), "finish_allowed": workflow.get("finish_allowed"),
            "final_stage": workflow.get("final_stage"), "incomplete_stages": sorted(set(invalid)),
            "stages_present": sorted(observed)}


def _finish_valid(run_dir) -> tuple[bool, list[str]]:
    turns = _ordered_turns(run_dir)
    finishes = [t for t in turns if t["operation"] == "finish_experiment"]
    if not finishes:
        return False, ["finish_experiment turn missing"]
    finish = finishes[-1]
    reasons = []
    if finish["status"] != "complete" or finish["remaining"] != {}:
        reasons.append("finish_experiment not complete with explicit empty remaining")
    snapshots = [t for t in turns if t["operation"] == "snapshot_task_meta"
                 and t["round_number"] == EXPECTED_ROUNDS and t["status"] == "snapshotted"]
    snap = run_dir / "snapshots/round_5_attempt_1"
    if (not snapshots or turns.index(finish) <= turns.index(snapshots[-1])
            or snapshots[-1]["request"].get("destination") != str(snap)
            or snapshots[-1]["receipt"].get("destination") != str(snap)
            or not _snapshot_valid(snap / "snapshot_receipt.json", run_dir / "round_5/attempts/attempt_1")[0]):
        reasons.append("no successful sealed B5 snapshot ordered before finish")
    return not reasons, reasons


# I2: operations whose invocation creates a compute job or GPU service (run_command included
# because it is a LONG_OPERATION that spawns a durable job). Their presence in the ordered
# turn history proves the run entered a creation stage, so it is NOT a not_created failure.
COMPUTE_SERVICE_OPERATIONS = {
    "run_parent", "run_candidate", "sft", "evaluate", "rollout", "run_command",
    "prepare_evaluation", "prepare_validation_snapshot", "deploy_candidate",
}


def not_created_history_proof(run_dir, workflow):
    """I2 shared producer/consumer helper: prove the run NEVER entered a job/service creation
    stage from the actual sealed history, not from a receipt summary. Requires a trusted early
    protocol state (A0, bootstrap incomplete), a contiguous complete parseable ordered turn
    history (command+receipt) with no compute/service creation call, and a non-empty parseable
    workflow event log ordered-consistent with the turns. Any gap/corruption/inconsistency =>
    not proven. Returns (ok, proof) where proof carries a history_sha256 the consumer recomputes
    to reject a receipt summary that no longer matches the current history."""
    run_dir = Path(run_dir)
    current_stage = (workflow or {}).get("current_stage")
    stages = (workflow or {}).get("stages") or []
    a0 = next((s for s in stages if s.get("id") == "A0"), {})
    bootstrap_complete = "bootstrap" not in (a0.get("remaining_milestones") or [])
    turns_dir = run_dir / "meta_session/turns"
    turn_dirs = sorted((d for d in turns_dir.iterdir() if d.is_dir() and d.name.isdigit()),
                       key=lambda d: int(d.name)) if turns_dir.is_dir() else []
    turn_names = [d.name for d in turn_dirs]
    continuity_ok = bool(turn_dirs) and [int(n) for n in turn_names] == list(range(len(turn_dirs)))
    creations, history_parts, parseable = [], [], True
    cmd_by_turn = {}
    for d in turn_dirs:
        cmd_path, rec_path = d / "command.json", d / "receipt.json"
        cmd, rec = _read_json(cmd_path), _read_json(rec_path)
        if not isinstance(cmd, dict) or not isinstance(rec, dict):
            parseable = False
            continue
        try:
            history_parts.append(cmd_path.read_bytes())
            history_parts.append(rec_path.read_bytes())
        except OSError:
            parseable = False
            continue
        op = (cmd.get("request") or {}).get("operation")
        cmd_by_turn[d.name] = op
        if isinstance(op, str) and "(" in op:
            op = op.split("(", 1)[0].strip()
        if op in COMPUTE_SERVICE_OPERATIONS:
            creations.append({"turn": d.name, "operation": op})
    events_path = run_dir / "meta_session/workflow_events.jsonl"
    events_ok, events_bytes, event_turns = False, b"", []
    if events_path.is_file():
        try:
            events_bytes = events_path.read_bytes()
            lines = [l for l in events_bytes.decode("utf-8").splitlines() if l.strip()]
            parsed = [json.loads(l) for l in lines]
            if parsed and all(isinstance(e, dict) for e in parsed):
                event_turns = [e.get("turn") for e in parsed]
                # ordered correspondence: every event references an existing turn, events are
                # non-decreasing by turn, every turn is covered, and operation matches command.
                referenced_ok = all(t in cmd_by_turn for t in event_turns)
                ordered_ok = all(event_turns[i] <= event_turns[i + 1] for i in range(len(event_turns) - 1))
                covered_ok = set(turn_names) <= set(event_turns)
                consistent_ok = all(
                    (e.get("operation") == cmd_by_turn.get(e.get("turn")))
                    for e in parsed if e.get("turn") in cmd_by_turn)
                events_ok = referenced_ok and ordered_ok and covered_ok and consistent_ok
        except (OSError, ValueError, TypeError, UnicodeError):
            events_ok = False
    history_sha256 = hashlib.sha256(b"".join(history_parts) + events_bytes).hexdigest()
    proof = {"current_stage": current_stage, "bootstrap_complete": bootstrap_complete,
             "compute_service_creations": creations, "ordered_turns": len(turn_dirs),
             "turn_continuity_ok": continuity_ok, "turns_parseable": parseable,
             "workflow_events_ok": events_ok, "history_sha256": history_sha256}
    ok = (current_stage == "A0" and not bootstrap_complete and not creations
          and continuity_ok and parseable and events_ok)
    return ok, proof


def _jobs_validation(run_dir) -> dict:
    jobs_dir = run_dir / "meta_session/jobs"
    missing, invalid, required_failed = [], [], []
    jobs = sorted(jobs_dir.glob("*/job.json"))
    dirs = sorted(p for p in jobs_dir.glob("*") if p.is_dir())
    if not dirs:
        missing.append("<no job inventory>")
    known_terminal = SUCCESS_JOB_STATUS | TERMINAL_FAILED_JOB_STATUS
    for directory in dirs:
        job = _read_json(directory / "job.json")
        if not isinstance(job, dict):
            invalid.append(directory.name + ":job.json")
            continue
        identifier = directory.name
        if job.get("job_id") != identifier:
            invalid.append(identifier + ":job_id")
        request = job.get("request") or {}
        result_path = run_dir / "controller_transport" / identifier / "result.json"
        if job.get("result_path") != str(result_path):
            invalid.append(identifier + ":result_binding")
        result = _read_json(result_path)
        if not isinstance(result, dict):
            missing.append(identifier)
            continue
        turn_dir = job.get("turn_dir")
        command = _read_json(Path(turn_dir) / "command.json") if turn_dir else None
        turn = _read_json(Path(turn_dir) / "receipt.json") if turn_dir else None
        if (not _within(turn_dir, run_dir / "meta_session/turns")
                or _read_json(result_path.parent / "request.json") != request
                or not isinstance(command, dict) or command.get("request") != request):
            invalid.append(identifier + ":request_binding")
        # Original async receipts may remain running; only their exact owned
        # job/result reference can resolve to the terminal result.
        if isinstance(turn, dict) and turn.get("status") == "running":
            if turn.get("job_id") != identifier or turn.get("result_path") != str(result_path):
                invalid.append(identifier + ":async_binding")
        elif turn != result:
            invalid.append(identifier + ":turn_binding")
        operation, status = request.get("operation"), result.get("status")
        if result.get("operation") != operation:
            invalid.append(identifier + ":operation")
        if status not in known_terminal:
            invalid.append(identifier + ":unknown_status")
        if operation in REQUIRED_JOB_OPERATIONS:
            if (status not in SUCCESS_JOB_STATUS and not (status == "exited" and result.get("returncode") == 0)
                    or result.get("ok") is False or result.get("returncode") not in (None, 0)):
                required_failed.append(identifier)
    return {"total": len(jobs), "missing_final_receipts": missing,
            "invalid": invalid, "required_failed": required_failed}


# A prelaunch rejection leaves a TRULY EMPTY attempt directory (no entry at all before the
# marker, no process). Such a dir is marked by a reviewed rsiflow_preflight_rejected observation
# (never agent-edited JSON, never deleted, never a forged identity). The shared classifier lets
# producer/consumer ignore genuine rejections for current-attempt/accounting/inventory while
# NEVER hiding a real attempt or failed owner: emptiness is content-based (only the marker itself
# is permitted), so ANY known/unknown/nested entry => not a rejection (fail closed).
PREFLIGHT_REJECTED_KIND = "rsiflow_preflight_rejected"


def preflight_rejected_proof(attempt_dir, run_dir):
    """Validate that an attempt dir is a GENUINE prelaunch rejection: a bound
    rsiflow_preflight_rejected marker AND TRUE emptiness — the ONLY entry permitted is the
    marker itself. ANY other entry (known real-attempt file, unknown file, or nested dir such
    as external-cleanup/obs-*/receipt.json or owner/product evidence) => not a rejection
    (fail closed). This is content-based, not a filename allowlist, so a real failed owner or
    nested product can never be hidden behind an empty-rejection mark. Returns (ok, proof)."""
    attempt_dir, run_dir = Path(attempt_dir), Path(run_dir)
    marker = _read_json(attempt_dir / "preflight-rejected.json")
    entries = sorted(p.name for p in attempt_dir.iterdir()) if attempt_dir.is_dir() else []
    extra = [n for n in entries if n != "preflight-rejected.json"]
    proof = {"attempt": attempt_dir.name, "marker_present": isinstance(marker, dict),
             "entries": entries, "extra_entries": extra, "empty": not extra, "binding_ok": False}
    if not isinstance(marker, dict) or marker.get("kind") != PREFLIGHT_REJECTED_KIND or extra:
        return False, proof
    binding_ok = (marker.get("run_dir") == str(run_dir)
                  and marker.get("supervision_attempt") == attempt_dir.name
                  and isinstance(marker.get("config_hash"), str) and bool(marker.get("config_hash"))
                  and isinstance(marker.get("host_identity"), dict) and bool(marker.get("host_identity"))
                  and marker.get("observed_at") is not None)
    proof["binding_ok"] = bool(binding_ok)
    return bool(binding_ok), proof


def supervision_attempts(run_dir):
    """Shared enumeration: (real_attempt_dirs, rejected_attempt_dirs, rejected_proofs).
    A dir is rejected ONLY with a valid preflight_rejected proof; otherwise it is a real
    attempt (even if incomplete/unaccounted). Producer and consumer use this same rule."""
    run_dir = Path(run_dir)
    sup = run_dir / "meta_session/supervision"
    dirs = sorted((d for d in sup.iterdir() if d.is_dir() and d.name.isdigit()),
                  key=lambda d: int(d.name)) if sup.is_dir() else []
    real, rejected, proofs = [], [], {}
    for d in dirs:
        ok, proof = preflight_rejected_proof(d, run_dir)
        if ok:
            rejected.append(d); proofs[d.name] = proof
        else:
            real.append(d)
    return real, rejected, proofs


def next_supervision_number(run_dir) -> str:
    """Next legal supervision number = max of ALL numeric dirs (including rejected, which
    still occupy their number and are never reused) + 1, zero-padded to 4 digits."""
    run_dir = Path(run_dir)
    sup = run_dir / "meta_session/supervision"
    numbers = [int(d.name) for d in sup.iterdir() if d.is_dir() and d.name.isdigit()] if sup.is_dir() else []
    return f"{(max(numbers) + 1 if numbers else 1):04d}"


def _supervision(run_dir) -> dict:
    attempts, rejected, _ = supervision_attempts(run_dir)
    all_accounted = True
    latest_reconcile = False
    latest_exit = False
    latest_identity = False
    for attempt in attempts:
        identity = _read_json(attempt / "identity.json")
        reconcile = _read_json(attempt / "reconcile.json")
        try:
            exit_value = (attempt / "exit").read_text(encoding="utf-8").strip()
        except OSError:
            exit_value = None
        identity_ok = isinstance(identity, dict) and identity.get("run_dir") == str(run_dir)
        reconcile_ok = isinstance(reconcile, dict) and reconcile.get("status") == "complete" and not reconcile.get("remaining")
        # A historical attempt is accounted for when identity-bound, terminal (any exit
        # value, e.g. 143) and reconciled; only the latest must have succeeded (exit 0).
        terminal = exit_value is not None and exit_value != ""
        if not (identity_ok and reconcile_ok and terminal):
            all_accounted = False
        if attempt is attempts[-1]:
            latest_reconcile = reconcile_ok
            latest_exit = exit_value == "0"
            latest_identity = identity_ok
    return {"present": bool(attempts), "attempts": [a.name for a in attempts],
            "rejected_attempts": [a.name for a in rejected],
            "all_attempts_accounted_for": all_accounted and bool(attempts),
            "latest_identity_bound": latest_identity,
            "reconcile_complete": latest_reconcile, "exit_zero": latest_exit}


def _service_registry(run_dir, active, output_root) -> dict:
    registry_path = Path(output_root) / "services/four_gpu_services.json"
    registry = _read_json(registry_path)
    ok = False
    reason = "missing"
    if isinstance(registry, dict):
        if registry.get("phase") != "services_stopped":
            reason = "phase_not_stopped"
        else:
            gpus = {s.get("gpu") for s in registry.get("services", [])}
            checkpoint = active.get("checkpoint_path")
            if gpus != {0, 1, 2, 3}:
                reason = "gpu_set"
            elif checkpoint and registry.get("checkpoint") != checkpoint:
                reason = "checkpoint_binding"
            elif not registry.get("stopped_at"):
                reason = "no_stopped_at"
            else:
                ok = True
                reason = "stopped"
    return {"ok": ok, "reason": reason, "path": str(registry_path), "sha256": _sha256(registry_path)}


def _external_cleanup(run_dir, cleanup_receipt_path, config, output_root, supervision, active) -> dict:
    result = {"status": "missing", "ok": False, "blockers": ["external_cleanup_missing"],
              "path": str(cleanup_receipt_path) if cleanup_receipt_path else None, "sha256": None}
    if not cleanup_receipt_path:
        return result
    receipt = _read_json(cleanup_receipt_path)
    result["sha256"] = _sha256(cleanup_receipt_path)
    if not isinstance(receipt, dict):
        result["blockers"] = ["external_cleanup_unparseable"]
        return result
    result["status"] = receipt.get("status")
    try:
        _validate_cleanup(receipt, Path(cleanup_receipt_path), run_dir, config, Path(output_root), supervision, active)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["blockers"] = ["external_cleanup_" + str(exc)]
        return result
    result.update(ok=True, blockers=[])
    return result


def validate_gpu_measurement(obs, expected, *, require_idle=True):
    """Parse both raw nvidia-smi outputs; reported summaries cannot override them."""
    def require(ok, reason):
        if not ok:
            raise ValueError(reason)
    require(obs.get("command_result") == 0, "gpu_command_failed")
    parsed = []
    try:
        for line in obs["raw_stdout"].splitlines():
            if not line.strip():
                continue
            index, gpu_uuid, name, total, free, used, util = [v.strip() for v in line.split(",")]
            parsed.append({"gpu": int(index), "uuid": gpu_uuid, "memory_total": int(total),
                           "memory_free": int(free), "memory_used": int(used), "utilization": int(util)})
        apps = [{"pid": int(line.split(",")[0]), "uuid": line.split(",")[1].strip()}
                for line in obs["apps_raw_stdout"].splitlines() if line.strip()]
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        raise ValueError("gpu_raw_reading_parse") from exc
    fields = ("gpu", "uuid", "memory_total", "memory_free", "memory_used", "utilization")
    require(parsed == [{k: r.get(k) for k in fields} for r in obs.get("per_gpu", [])], "gpu_raw_reading_mismatch")
    require(len(parsed) == 4 and {r["gpu"] for r in parsed} == {0, 1, 2, 3}, "gpu_inventory")
    require([{k: r[k] for k in ("gpu", "uuid")} for r in parsed] == expected, "gpu_uuid_binding")
    require(hashlib.sha256(obs["raw_stdout"].encode()).hexdigest() == obs.get("stdout_hash"), "gpu_raw_hash")
    require(hashlib.sha256(obs["apps_raw_stdout"].encode()).hexdigest() == obs.get("apps_stdout_hash"), "gpu_apps_hash")
    require(apps == obs.get("compute_apps"), "gpu_apps_reconciliation")
    require(all(0 <= r["memory_used"] <= r["memory_total"] and 0 <= r["memory_free"] <= r["memory_total"]
                and 0 <= r["utilization"] <= 100 for r in parsed), "gpu_reading_range")
    idle = not apps and all(r["memory_used"] <= 1024 and r["utilization"] <= 5 and r["memory_free"] >= 40000 for r in parsed)
    require(obs.get("free_verified") == idle, "gpu_idle_summary")
    if require_idle:
        require(idle, "gpu_not_idle")
    return parsed, apps


def _validate_phase_owner(value, phase, run):
    from deepcoder_five_round_supervisor import validate_identity
    identity = validate_identity(value.get("training_identity"))
    if identity["pid"] != value.get("training_pid") or identity["pgid"] != identity["pid"]:
        raise ValueError("training_identity_binding")
    if (Path(value.get("training_request_dir", "/invalid")).resolve() != phase.parent.resolve()
            or Path(value.get("training_run_dir", "/invalid")).resolve() != run.resolve()):
        raise ValueError("training_request_binding")
    argv = value.get("training_argv")
    if not isinstance(argv, list) or argv != value.get("command") or "--request-dir" not in argv:
        raise ValueError("training_argv_binding")
    position = argv.index("--request-dir") + 1
    if position >= len(argv) or argv[position] != str(phase.parent):
        raise ValueError("training_argv_request")
    if value.get("status") not in {"completed", "training_complete"} or value.get("identity_uncertain"):
        raise ValueError("training_requires_audit")
    return identity


def _validate_cleanup(receipt, path, run, config, output, supervision, active):
    """Consumer contract for the external writer; never inspect/signal live owners.

    All observations are sealed files, cross-bound to launch and the actual
    production inventories. The consumer does not create any of these receipts.
    """
    def require(ok, reason):
        if not ok:
            raise ValueError(reason)

    require(receipt.get("kind") == "rsiflow_external_cleanup", "wrong_kind")
    require(receipt.get("status") == "completed" and receipt.get("blockers") == []
            and not receipt.get("errors"), "pending_or_failed")
    evidence = receipt.get("evidence_files_sha256") or {}
    resolved_evidence = {str((path.parent / k).resolve()): v for k, v in evidence.items()}

    snapshots = receipt.get("source_snapshots") or {}
    def captured(raw):
        reference = snapshots.get(str(Path(raw).resolve()))
        return Path(reference["path"]) if reference else Path(raw)

    def sealed(raw, expected=None):
        source = captured(raw)
        digest = _sha256(source)
        require(not source.is_symlink() and digest is not None
                and resolved_evidence.get(str(source.resolve())) == digest
                and (expected is None or digest == expected), "evidence_hash")
        return source

    for raw, digest in evidence.items():
        sealed(path.parent / raw, digest)
    binding = receipt.get("binding") or {}
    require(config is not None and binding.get("config_path") == config.get("_config_path"), "config_binding")
    require(binding.get("config_hash") == _sha256(config["_config_path"]), "config_hash")
    sealed(config["_config_path"], binding["config_hash"])
    require(binding.get("run_dir") == str(run) and binding.get("output_root") == str(output), "run_binding")
    attempts = supervision.get("attempts") or []
    require(bool(attempts) and binding.get("supervision_attempt") == attempts[-1], "not_current_attempt")
    base = (_PROJECT / "RSIFlow" / config.get("runtime_source", "runtime") / config["base_config"]).resolve()
    require(binding.get("base_config_path") == str(base), "base_binding")
    sealed(base, binding.get("base_config_hash"))
    effective_path = sealed(binding["effective_config_path"], binding.get("effective_config_hash"))
    from task_adapter import TaskAdapter
    effective = TaskAdapter(_PROJECT / "RSIFlow", run / "controller_receipts")._config(
        {"config_path": config["_config_path"]}).model_dump()
    require(_read_json(effective_path) == effective, "effective_config_binding")
    launch_path = sealed(binding["launch_receipt_path"], binding.get("launch_receipt_hash"))
    require(_within(launch_path, run / "meta_session/supervision" / attempts[-1]), "launch_path")
    launch = _read_json(launch_path) or {}
    require(launch.get("kind") == "rsiflow_launch", "launch_schema")
    from deepcoder_five_round_supervisor import load_launch_chain
    attempt = run / "meta_session/supervision" / attempts[-1]
    chain = load_launch_chain(attempt, config["_config_path"], run,
                              read_json=lambda p: _read_json(sealed(p)),
                              digest=lambda p: _sha256(sealed(p)))
    for source in chain["paths"]:
        sealed(source)
    fields = ("config_path", "config_hash", "base_config_path", "base_config_hash", "effective_config_path",
              "effective_config_hash", "run_dir", "output_root", "supervision_attempt", "thread_id")
    require(all(binding.get(k) is not None and chain["binding"].get(k) == binding[k] for k in fields), "launch_binding")
    state = _read_json(sealed(run / "meta_session/state.json")) or {}
    require(binding["thread_id"] == state.get("thread_id"), "thread_binding")
    require(receipt.get("host_identity") == chain["host_identity"], "cleanup_host_binding")
    runtime_owners, runtime_gpus = chain["owners"], chain["expected_gpus"]
    jobs_check = _jobs_validation(run)
    not_created_receipt = (receipt.get("registry") or {}).get("phase") == "not_created_verified"
    if not_created_receipt:
        # No job inventory is expected for a not_created failure; only invalid bindings fail.
        require(not jobs_check["invalid"], "job_terminal_binding")
    else:
        require(not jobs_check["invalid"] and not jobs_check["missing_final_receipts"], "job_terminal_binding")
    sup = receipt.get("supervision") or {}
    require(sup.get("all_previous_attempts_accounted_for") is True
            and supervision.get("all_attempts_accounted_for") is True, "supervision_unaccounted")
    history = sup.get("attempts") or []
    require([r.get("attempt") for r in history] == attempts, "supervision_inventory")
    for record, attempt in zip(history, attempts):
        directory = run / "meta_session/supervision" / attempt
        for name, file in (("identity", "identity.json"), ("exit", "exit"), ("reconcile", "reconcile.json")):
            require(record.get(name + "_path") == str(directory / file), "supervision_path")
            sealed(directory / file, record.get(name + "_hash"))
    # Preflight-rejected attempts are excluded from the real inventory above, but the consumer
    # independently re-verifies each is a GENUINE empty rejection (bound marker + no identity/
    # launch/product file), so a rejection can never hide a real attempt or failed owner. A
    # historical receipt may predate later rejections: its rejected list must be a SUBSET of the
    # current genuine rejections (a receipt-listed dir that became a real attempt fails here).
    rejected = supervision.get("rejected_attempts") or []
    require(set(sup.get("rejected_attempts", [])) <= set(rejected), "supervision_rejected_inventory")
    for name in rejected:
        ok, _ = preflight_rejected_proof(run / "meta_session/supervision" / name, run)
        require(ok, "preflight_rejected_invalid")
    registry_path = output / "services/four_gpu_services.json"
    registry = receipt.get("registry") or {}
    require(registry.get("path") == str(registry_path), "registry_binding")
    owners = receipt.get("owners") or {}
    jobs = sorted((run / "meta_session/jobs").glob("*/job.json"))
    phases = sorted(run.glob("round_*/attempts/attempt_*/**/gpu_phase_receipt.json"))
    not_created = registry.get("phase") == "not_created_verified"
    if not_created:
        # Task B consumer contract: failed before any job/service was created. The registry
        # must never have existed, there must be no job/phase/service owner, the run must not
        # be a normal completion, and GPU/ports release is verified below like any cleanup.
        require(not registry_path.exists(), "not_created_registry_exists")
        require(registry.get("after_hash") is None and registry.get("before_hash") is None, "not_created_registry_hash")
        require(registry.get("stop_action_skipped_or_executed") == "not_created_verified", "not_created_stop_action")
        require(not jobs and not phases, "not_created_compute_owners_exist")
        require(owners.get("jobs") == [] and owners.get("training_phases") == []
                and owners.get("inference_services") == [], "not_created_owner_inventory")
        require(not (receipt.get("protocol") or {}).get("finish_allowed"), "not_created_run_completed")
        # I2: recompute the historical proof from the ACTUAL sealed history (shared helper),
        # never trust the receipt summary alone. The receipt's not_created_evidence must match
        # the recomputed proof (history_sha256), so a changed current history with a stale
        # A0/empty-creations summary is rejected.
        evidence = receipt.get("not_created_evidence") or {}
        require(evidence.get("current_stage") == "A0", "not_created_stage_not_a0")
        workflow = _read_json(run / "meta_session/workflow_state.json") or {}
        history_ok, recomputed = not_created_history_proof(run, workflow)
        require(history_ok, "not_created_history_proof_incomplete")
        require(recomputed.get("current_stage") == "A0", "not_created_recomputed_stage")
        require(recomputed.get("bootstrap_complete") is False, "not_created_bootstrap_complete")
        require(not recomputed.get("compute_service_creations"), "not_created_compute_history")
        require(recomputed.get("turn_continuity_ok") is True, "not_created_turn_continuity")
        require(recomputed.get("turns_parseable") is True, "not_created_turns_parseable")
        require(recomputed.get("workflow_events_ok") is True, "not_created_events_missing")
        require(evidence.get("history_sha256") == recomputed.get("history_sha256"),
                "not_created_summary_history_mismatch")
        services = []
    else:
        actual_registry = _read_json(sealed(registry_path, registry.get("after_hash"))) or {}
        require(actual_registry.get("phase") == registry.get("phase") == "services_stopped"
                and actual_registry.get("stopped_at") is not None, "registry_not_stopped")
        require(registry.get("checkpoint") == actual_registry.get("checkpoint") == active.get("checkpoint_path"), "registry_checkpoint")
        require(registry.get("weights") == active.get("checkpoint_manifest") and bool(registry.get("weights")), "registry_weights")
        services = actual_registry.get("services") or []
        require(len(services) == 4 and {s.get("gpu") for s in services} == {0, 1, 2, 3}, "registry_gpus")

    def owner(owner, identity, source):
        require(isinstance(owner, dict) and owner.get("state") == "exited"
                and owner.get("source_path") == str(source) and owner.get("identity") == identity, "owner_binding")
        require(type(identity.get("pid")) is int and identity["pid"] > 0
                and str(identity.get("start_ticks", "")).isdigit(), "owner_identity")
        observation = _read_json(sealed(owner["evidence_path"], owner.get("evidence_sha256"))) or {}
        require(observation.get("kind") == "rsiflow_owner_exit" and observation.get("run_dir") == str(run)
                and observation.get("identity") == identity and observation.get("state") == "exited"
                and observation.get("group_members") == [] and observation.get("read_errors") == []
                and observation.get("observed_at"), "owner_exit_observation")

    for group, pid_key in (("launcher", "launcher_pid"), ("app_server", "codex_pid")):
        identity = runtime_owners.get(group) or {}
        require(identity.get("pid") == state.get(pid_key), "launcher_identity")
        owner(owners.get(group), identity, run / "meta_session/supervision" / attempts[-1] / "launch.json")
    if not not_created:
        inventory = owners.get("jobs")
        require(isinstance(inventory, list) and len(inventory) == len(jobs) and bool(jobs), "jobs_inventory_missing")
        by_source = {o.get("source_path"): o for o in inventory}
        for jobpath in jobs:
            job = _read_json(sealed(jobpath))
            identity = {"pid": job["pid"], "pgid": job["pid"], "start_ticks": str(job["process_start"])}
            owner(by_source.get(str(jobpath)), identity, jobpath)
        inventory = owners.get("inference_services")
        require(isinstance(inventory, list) and len(inventory) == 4, "inference_services_inventory_missing")
        by_pid = {o.get("identity", {}).get("pid"): o for o in inventory}
        for service in services:
            require(service.get("verified_binding") == {"checkpoint_path": active["checkpoint_path"],
                                                        "weights": active["checkpoint_manifest"]}, "service_weights")
            owner(by_pid.get(service["pid"]), service["identity"], registry_path)
        inventory = owners.get("training_phases")
        require(isinstance(inventory, list) and len(inventory) == len(phases), "training_inventory")
        by_source = {o.get("source_path"): o for o in inventory}
        for phase in phases:
            value = _read_json(sealed(phase)) or {}
            item = by_source.get(str(phase)) or {}
            identity = item.get("identity") or {}
            require(identity == _validate_phase_owner(value, phase, run), "training_identity")
            owner(item, identity, phase)
    observations = receipt.get("gpu_observations") or []
    require(bool(observations), "no_gpu_observation")
    # R6/F6: require two INDEPENDENT observations (distinct evidence path AND time, valid
    # interval), each with sealed raw output+hash, used/util/apps reconciliation, near-idle.
    require(len(observations) >= 2, "gpu_requires_two_observations")
    from datetime import datetime
    import hashlib as _hl
    paths_seen = [o.get("evidence_path") for o in observations]
    times_seen = [o.get("time") for o in observations]
    require(len(set(paths_seen)) == len(paths_seen), "gpu_duplicate_observation_path")
    require(len(set(times_seen)) == len(times_seen), "gpu_duplicate_observation_time")
    obs_times = sorted(datetime.fromisoformat(o["time"].replace("Z", "+00:00")) for o in observations)
    require((obs_times[-1] - obs_times[0]).total_seconds() >= 0.5, "gpu_observation_interval_invalid")
    started_at = datetime.fromisoformat(receipt["started_at_utc"].replace("Z", "+00:00"))
    finished_at = datetime.fromisoformat(receipt["observed_at_utc"].replace("Z", "+00:00"))
    expected = runtime_gpus or []
    require(len(expected) == 4 and {g.get("gpu") for g in expected} == {0, 1, 2, 3}
            and len({g.get("uuid") for g in expected}) == 4 and all(g.get("uuid") for g in expected), "launch_gpu_inventory")
    for obs in observations:
        actual = _read_json(sealed(obs["evidence_path"], obs.get("evidence_sha256")))
        require(actual == {k: v for k, v in obs.items() if k not in {"evidence_path", "evidence_sha256"}}, "gpu_evidence_binding")
        observed_at = datetime.fromisoformat(obs["time"].replace("Z", "+00:00"))
        require(obs.get("compute_apps") == [], "gpu_compute_apps")
        require(obs.get("command_result") == 0 and isinstance(obs.get("command"), list)
                and "nvidia-smi" in obs["command"][0] and obs.get("free_verified") is True
                and started_at <= observed_at <= finished_at, "gpu_observation")
        # F6: raw output must be present and reconcile with its hash (not merely non-empty).
        require(isinstance(obs.get("raw_stdout"), str) and bool(obs.get("stdout_hash")), "gpu_raw_missing")
        require(_hl.sha256(obs["raw_stdout"].encode()).hexdigest() == obs["stdout_hash"], "gpu_raw_hash_mismatch")
        # F6: apps raw output must reconcile with the parsed compute_apps list.
        require(isinstance(obs.get("apps_raw_stdout"), str) and bool(obs.get("apps_stdout_hash")), "gpu_apps_raw_missing")
        require(_hl.sha256(obs["apps_raw_stdout"].encode()).hexdigest() == obs["apps_stdout_hash"], "gpu_apps_hash_mismatch")
        parsed_apps = [{"pid": int(l.split(",")[0]), "uuid": l.split(",")[1].strip()}
                       for l in obs["apps_raw_stdout"].strip().splitlines() if l.strip()]
        require(parsed_apps == obs.get("compute_apps"), "gpu_apps_reconciliation")
        require(obs.get("expected_uuids") == [g["uuid"] for g in expected], "gpu_uuids")
        validate_gpu_measurement(obs, expected)
        readings = obs.get("per_gpu") or []
        require(len(readings) == 4 and {g.get("gpu") for g in readings} == {0, 1, 2, 3}, "gpu_readings")
        for reading in readings:
            uuid = next(g["uuid"] for g in expected if g["gpu"] == reading["gpu"])
            require(reading.get("uuid") == uuid and type(reading.get("memory_free")) is int
                    and reading["memory_free"] >= 40000 and type(reading.get("memory_total")) is int
                    and reading["memory_free"] <= reading["memory_total"]
                    and type(reading.get("memory_used")) is int and reading["memory_used"] <= 1024
                    and type(reading.get("utilization")) is int and reading["utilization"] <= 5, "gpu_readings")
    # F6: ports must be actually measured closed with a port list + sealed evidence content.
    ports = receipt.get("ports_observation") or {}
    require(ports.get("status") == "closed", "ports_not_measured_closed")
    require(isinstance(ports.get("closed_ports"), list) and set(ports["closed_ports"]) == {8271, 8272, 8273, 8274},
            "ports_list_invalid")
    ports_actual = _read_json(sealed(ports["evidence_path"], ports.get("evidence_sha256")))
    require(ports_actual == {k: v for k, v in ports.items() if k not in {"evidence_path", "evidence_sha256"}},
            "ports_evidence_binding")


def _active_chain(rounds, active, a0_identity=None) -> list[str]:
    reasons = []
    artifacts = active.get("artifacts") or {}
    try:
        if _artifact_manifest(artifacts.get("directory")) != artifacts.get("manifest"):
            reasons.append("final active artifacts bytes != declared manifest (active chain)")
    except (OSError, ValueError):
        reasons.append("final active artifacts unreadable (active chain)")
    previous_identity = a0_identity
    last_identity = None
    for summary in rounds:
        parent_identity = _task_identity(summary.get("parent_state"))
        candidate_identity = _task_identity(summary.get("candidate_state"))
        selected_identity = _task_identity(summary.get("snapshot_states", {}).get("selected"))
        decision_value = (summary.get("selection") or {}).get("decision")
        expected_selected = candidate_identity if decision_value == "accept_candidate" else parent_identity
        if not _identity_equal(selected_identity, expected_selected):
            reasons.append(f"B{summary['round']} selected identity != accept candidate / retain parent")
        if previous_identity is not None and not _identity_equal(parent_identity, previous_identity):
            reasons.append(f"B{summary['round']} parent identity != previous selected (active chain)")
        previous_identity = selected_identity
        last_identity = selected_identity
    if rounds and not _identity_equal(_task_identity(active), last_identity):
        reasons.append("final active identity != last round selected (active chain)")
    return reasons


def _derive_trusted(config_path, run_dir):
    """Validate config natural derivation; return (config, output_root, release_dir, blockers)."""
    if not config_path:
        return None, None, None, ["trusted config missing"]
    config = _read_json(config_path)
    if not isinstance(config, dict):
        return None, None, None, ["trusted config unparseable"]
    config["_config_path"] = str(Path(config_path).resolve())
    output_root = config.get("output_root")
    run_name = config.get("run_name")
    release_dir = config.get("frozen_data_dir")
    blockers = []
    if not output_root or not run_name:
        blockers.append("trusted config lacks output_root/run_name")
        return config, output_root, release_dir, blockers
    expected_run = Path(output_root) / "runs" / run_name
    if expected_run.resolve() != Path(run_dir).resolve():
        blockers.append("run_dir not naturally derived from config output_root/run_name")
    if not release_dir or not Path(release_dir).is_dir():
        blockers.append("trusted config frozen_data_dir missing")
    return config, output_root, release_dir, blockers


def summarize(run_dir, config_path=None, cleanup_receipt_path=None) -> dict:
    run_dir = Path(run_dir)
    config, output_root, release_dir, trusted_blockers = _derive_trusted(config_path, run_dir)
    if output_root is None:
        output_root = run_dir.parent.parent

    trusted_manifests = {}
    protocol_hash = None
    if release_dir and not trusted_blockers:
        try:
            protocol_hash = _protocol_hashes(config)
        except (OSError, ValueError, KeyError) as exc:
            trusted_blockers.append(f"effective production config invalid: {exc}")
        for n in range(1, EXPECTED_ROUNDS + 1):
            if (run_dir / f"round_{n}").is_dir():
                try:
                    trusted_manifests[n] = _trusted_manifest(release_dir, n)
                except (OSError, ValueError, KeyError) as exc:
                    trusted_blockers.append(f"B{n} frozen manifest invalid: {exc}")
                    trusted_manifests[n] = None

    rounds = [s for s in (_round_summary(run_dir, n, trusted_manifests.get(n), protocol_hash)
                          for n in range(1, EXPECTED_ROUNDS + 1)) if s]
    completed_rounds = sum(1 for s in rounds if s["terminal"] and s["products_complete"])
    present = {s["round"] for s in rounds}
    missing_rounds = [n for n in range(1, EXPECTED_ROUNDS + 1) if n not in present]
    status = _run_status(run_dir)
    workflow = _workflow_milestones(run_dir, config)
    finish_ok, finish_reasons = _finish_valid(run_dir)
    jobs = _jobs_validation(run_dir)
    active = _read_json(run_dir / "active_task.json") or {}
    supervision = _supervision(run_dir)
    registry = _service_registry(run_dir, active, output_root)
    cleanup = _external_cleanup(run_dir, cleanup_receipt_path, config, output_root, supervision, active)
    skills_present = (run_dir / "meta/skills.jsonl").is_file()
    context_present = (run_dir / "meta/context.json").is_file()
    a0_receipt = run_dir / "snapshots/A0/snapshot_receipt.json"
    a0_present = a0_receipt.is_file()
    a0_ok, a0_states = _snapshot_valid(a0_receipt, run_dir / "snapshots/A0")
    a0_selected = a0_states.get("selected") if a0_ok else None
    a0_identity = _task_identity(a0_selected) if a0_ok else None
    chain_reasons = _active_chain(rounds, active, a0_identity)
    round_parent_states = {s["round"]: s.get("parent_state") for s in rounds}

    protocol_blockers = list(trusted_blockers)
    if missing_rounds:
        protocol_blockers.append("missing rounds: " + ", ".join(f"B{n}" for n in missing_rounds))
    incomplete = [s["round"] for s in rounds if not s["products_complete"]]
    if incomplete:
        protocol_blockers.append("rounds lacking required products: " + ", ".join(f"B{n}" for n in incomplete))
    if completed_rounds != EXPECTED_ROUNDS:
        protocol_blockers.append(f"completed_rounds={completed_rounds} != expected {EXPECTED_ROUNDS}")
    if not a0_ok or not isinstance(a0_selected, dict) or a0_selected.get("generation") != 0:
        protocol_blockers.append("A0 snapshot missing/invalid initial state seal (active chain)")
    if not skills_present:
        protocol_blockers.append("skills ledger missing")
    if not context_present:
        protocol_blockers.append("meta context missing")
    if active.get("generation") is None:
        protocol_blockers.append("active identity missing")
    protocol_blockers.extend(chain_reasons)
    if status["status"] != "complete":
        protocol_blockers.append(f"meta state status={status['status']!r} != 'complete'")
    if status["last_turn_status"] != "completed":
        protocol_blockers.append("last_turn_status not completed")
    if workflow["current_stage"] != "COMPLETE" or workflow["finish_allowed"] is not True:
        protocol_blockers.append("workflow current_stage/finish_allowed not COMPLETE/true")
    if workflow["incomplete_stages"]:
        protocol_blockers.append("workflow incomplete stages: " + ", ".join(workflow["incomplete_stages"]))
    if status["current_stage"] != "COMPLETE" or not status["finish_allowed"]:
        protocol_blockers.append("RUN_STATUS stage/finish display not COMPLETE/yes")
    if not finish_ok:
        protocol_blockers.extend(finish_reasons)
    if jobs["missing_final_receipts"]:
        protocol_blockers.append("jobs missing final receipts: " + ", ".join(jobs["missing_final_receipts"]))
    if jobs["invalid"]:
        protocol_blockers.append("jobs invalid/non-terminal: " + ", ".join(jobs["invalid"]))
    if jobs["required_failed"]:
        protocol_blockers.append("required production jobs failed: " + ", ".join(jobs["required_failed"]))
    model_sft = _model_sft(rounds, run_dir, round_parent_states)
    if model_sft["status"] in ("selected_pending", "selected_failed"):
        protocol_blockers.append(f"MODEL/SFT evidence {model_sft['status']}")

    protocol_complete = (completed_rounds == EXPECTED_ROUNDS and not protocol_blockers)
    launcher_exited = (supervision["latest_identity_bound"] and supervision["exit_zero"]
                       and supervision["reconcile_complete"] and supervision["all_attempts_accounted_for"])

    external_blockers = []
    if not launcher_exited:
        external_blockers.append("launcher_exited_pending (supervision identity/exit/reconcile)")
    external_blockers.extend(cleanup["blockers"])
    run_complete = protocol_complete and launcher_exited and cleanup["ok"]

    blockers = protocol_blockers + external_blockers
    return {
        "schema_version": 3,
        "run_dir": str(run_dir),
        "expected_rounds": EXPECTED_ROUNDS,
        "completed_rounds": completed_rounds,
        "trusted_config": config is not None and not trusted_blockers,
        "protocol_complete": protocol_complete,
        "protocol_complete_blockers": protocol_blockers,
        "launcher_exited": launcher_exited,
        "external_cleanup": cleanup,
        "run_complete": run_complete,
        "run_complete_blockers": blockers,
        "rounds": rounds,
        "active_identity": {k: active.get(k) for k in ("generation", "model_ref", "harness_path", "checkpoint_path")},
        "skills_present": skills_present,
        "context_present": context_present,
        "a0_present": a0_present,
        "state": status,
        "workflow": workflow,
        "jobs": jobs,
        "supervision": supervision,
        "services_stopped": registry["ok"],
        "service_registry": registry,
        "model_sft": model_sft,
        "evidence_hashes": {
            "active_task": _sha256(run_dir / "active_task.json"),
            "meta_state": _sha256(run_dir / "meta_session/state.json"),
            "skills": _sha256(run_dir / "meta/skills.jsonl"),
            "context": _sha256(run_dir / "meta/context.json"),
            "service_registry": registry["sha256"],
            "external_cleanup": cleanup["sha256"],
        },
    }


def _markdown(result: dict) -> str:
    lines = [f"# DeepCoder five-round summary: {result['run_dir']}",
             f"- completed_rounds: {result['completed_rounds']}/{result['expected_rounds']}",
             f"- trusted_config: {result['trusted_config']}",
             f"- protocol_complete: {result['protocol_complete']} launcher_exited: {result['launcher_exited']}",
             f"- external_cleanup: {result['external_cleanup']['status']} ok={result['external_cleanup']['ok']}",
             f"- run_complete: {result['run_complete']}",
             f"- active identity: {result['active_identity']}",
             f"- skills_present: {result['skills_present']} context_present: {result['context_present']} "
             f"a0_present: {result['a0_present']}",
             f"- services_stopped: {result['services_stopped']} supervision: {result['supervision']}",
             f"- model_sft: {result['model_sft']['status']} rounds={result['model_sft']['rounds']}"]
    if result["run_complete_blockers"]:
        lines.append("- blockers:")
        lines.extend(f"  - {b}" for b in result["run_complete_blockers"])
    for s in result["rounds"]:
        lines.append(f"\n## B{s['round']} component={s['component']} decision={(s['selection'] or {}).get('decision')}")
        lines.append(f"- attempt_count: {s['attempt_count']} activated: {s['activated']} "
                     f"snapshot_present: {s['snapshot_present']} products_complete: {s['products_complete']}")
        lines.append(f"- denominator parent/candidate: {s['parent']['denominator']}/{s['candidate']['denominator']} "
                     f"match={s['denominator_match']} ids_match={s['ids_match']} source_hashes_match={s['source_hashes_match']}")
        lines.append(f"- delta: {s['delta']} transitions: {s['transitions']}")
        lines.append(f"- calls parent: {s['calls']['parent']}")
        lines.append(f"- calls candidate: {s['calls']['candidate']} cross_check_ok: {s['calls']['cross_check_ok']}")
        lines.append(f"- verification parent: {s['verification']['parent']}")
        lines.append(f"- verification candidate: {s['verification']['candidate']}")
        lines.append(f"- errors parent: {s['errors']['parent']}")
        lines.append(f"- errors candidate: {s['errors']['candidate']}")
        lines.append(f"- zero_call_overlap: {s['zero_call_overlap']}")
        if s["product_blockers"]:
            lines.append("- product_blockers:")
            lines.extend(f"  - {b}" for b in s["product_blockers"])
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--cleanup-receipt", type=Path, default=None)
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)
    result = summarize(args.run_dir, config_path=args.config, cleanup_receipt_path=args.cleanup_receipt)
    if args.markdown:
        print(_markdown(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
