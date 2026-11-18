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
for _entry in (_PROJECT / "RSIFlow", _PROJECT / "RSIFlow/runtime"):
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
NON_TERMINAL_JOB_STATUS = {"running", "pending", "starting", "dispatched", "training_dispatched"}
TASK_IDENTITY_FIELDS = ("generation", "model_ref", "checkpoint_path", "checkpoint_manifest", "artifacts")
REQUEST_ALIGN_FIELDS = ("messages", "tools", "seed", "temperature", "max_tokens", "chat_template_kwargs")


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
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


def _side_calls(rows, cache_root, round_n, state) -> dict:
    """Reconcile real Qwen calls per task against side-scoped persisted receipts.

    Uses the production canonical request hash (value_hash of the transport call minus
    its response). Each call aligns transport<->persisted request/response by call_id,
    verifies model requested/response and checkpoint/weights against the Task state,
    and binds the rollout receipt row/scope. Extra/duplicate/malformed calls block.
    """
    receipts = {}
    extra_bad = 0
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
            if (scope.get("role") != r.get("source_role")
                    or scope.get("round_id") != round_n
                    or scope.get("manifest_hash") != r.get("manifest_hash")
                    or scope.get("collection_stage") != r.get("collection_stage")
                    or binding.get("task_source_hash") != r.get("task_source_hash")):
                valid = False
            receipt_count = row_receipt.get("model_call_count")
            if receipt_count is None:
                receipt_count = len(row_receipt.get("model_calls") or [])
            if (row_receipt.get("task_id") != tid or row_receipt.get("state_hash") != state_hash
                    or receipt_count != calls):
                valid = False
            if checkpoint_manifest is not None and row_receipt.get("checkpoint_manifest") not in (None, checkpoint_manifest):
                valid = False
            calls_dir = cache_json.parent / (cache_json.stem + ".calls")
            persisted = {}
            if calls_dir.is_dir():
                for call_file in calls_dir.glob("*.json"):
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
                    request_aligned = isinstance(request, dict) and all(
                        request.get(k) == tc.get(k) for k in REQUEST_ALIGN_FIELDS)
                    if (call.get("status") != "completed"
                            or not request_aligned
                            or call.get("request_sha256") != _value_hash(request)
                            or response != tc.get("response")
                            or not response.get("model_ref_response")
                            or response.get("model_ref_requested") != response.get("model_ref_response")
                            or (model_ref is not None and response.get("model_ref_response") != model_ref)
                            or (checkpoint_manifest is not None and resp_binding.get("weights") != checkpoint_manifest)
                            or cbinding.get("task") != tid
                            or cbinding.get("state_hash") != state_hash
                            or cbinding.get("rollout") != rollout):
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
    cross_check_ok = (per_task_ok and extra_bad == 0
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
    identity = {k: state.get(k) for k in TASK_IDENTITY_FIELDS}
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
        if ia.get(k) != ib.get(k):
            return False
    # harness content identity allows path relocation but requires equal bytes hash
    return ia.get("harness_sha256") == ib.get("harness_sha256") and ia.get("harness_sha256") is not None


def _snapshot_valid(receipt_path, attempt_dir) -> tuple[bool, dict]:
    receipt = _read_json(receipt_path)
    info = {"parent": None, "candidate": None, "selected": None}
    if not isinstance(receipt, dict) or receipt.get("status") != "snapshotted":
        return False, info
    combinations = receipt.get("combinations") or {}
    if not {"parent", "candidate", "selected"} <= set(combinations):
        return False, info
    files_sha256 = receipt.get("files_sha256") or {}
    if not files_sha256:
        return False, info
    snap_dir = receipt_path.parent
    for rel, expected in files_sha256.items():
        actual = _sha256(snap_dir / rel)
        if actual is None or actual != expected:
            return False, info
    for role in ("parent", "candidate", "selected"):
        state_path = combinations[role].get("state_path")
        state = _read_json(state_path) if state_path else None
        if not isinstance(state, dict):
            return False, info
        # selected state must reside within the sealed snapshot directory
        if role == "selected":
            try:
                Path(state_path).resolve().relative_to(snap_dir.resolve())
            except ValueError:
                return False, info
        info[role] = state
    return True, info


def _model_round_evidence(run_dir, number, parent_state):
    attempt = run_dir / f"round_{number}/attempts/attempt_1"
    sft = _receipt_for_round(_find_transport_receipts(run_dir, "sft"), number,
                             ("candidate_state_path", "training_metrics_path"))
    if not isinstance(sft, dict):
        return False, "selected_pending"
    if sft.get("status") not in SUCCESS_JOB_STATUS:
        return False, "selected_failed"
    metrics_path = sft.get("training_metrics_path")
    candidate_state_path = sft.get("candidate_state_path")
    if not metrics_path or "model_update" not in str(metrics_path):
        return False, "selected_pending"
    metrics = _read_json(metrics_path)
    if not isinstance(metrics, dict):
        return False, "selected_pending"
    model_update = Path(metrics_path).parent
    rank_records = metrics.get("rank_records") or []
    ranks = {r.get("rank") for r in rank_records if isinstance(r, dict)}
    loss = metrics.get("training_loss")
    epoch = metrics.get("num_train_epochs_completed")
    finite_loss = isinstance(loss, (int, float)) and not isinstance(loss, bool) and math.isfinite(loss)
    complete = (
        metrics.get("world_size") == 4 and ranks == {0, 1, 2, 3}
        and all(isinstance(r.get("sampled_indices"), list) and r.get("optimizer_steps", 0) > 0 for r in rank_records)
        and isinstance(epoch, (int, float)) and epoch == 1
        and metrics.get("optimizer_steps", 0) > 0 and finite_loss
        and metrics.get("changed_lora_tensors", 0) > 0 and metrics.get("lora_delta_l2", 0) > 0
        and metrics.get("merged_probe_changed_elements", 0) > 0
        and metrics.get("positive_trajectories", 0) > 0
        and isinstance(metrics.get("selection"), dict)
        and metrics.get("checkpoint_path")
    )
    if not complete:
        return False, "selected_failed"
    gpu_phase = _read_json(model_update / "gpu_phase_receipt.json")
    if not isinstance(gpu_phase, dict) or gpu_phase.get("status") != "completed":
        return False, "selected_failed"
    if gpu_phase.get("replica_gpus") != [0, 1, 2, 3]:
        return False, "selected_failed"
    trained = _read_json(model_update / "checkpoint_trained.json") or _read_json(model_update / "checkpoint.json")
    if not isinstance(trained, dict) or trained.get("training_method") != "positive_sft_lora":
        return False, "selected_failed"
    child = _read_json(candidate_state_path) if candidate_state_path else None
    if not isinstance(child, dict):
        return False, "selected_pending"
    parent_generation = (parent_state or {}).get("generation")
    if parent_generation is not None and child.get("generation") != parent_generation + 1:
        return False, "selected_failed"
    if child.get("checkpoint_path") != metrics.get("checkpoint_path"):
        return False, "selected_failed"
    if gpu_phase.get("restored_checkpoint") != metrics.get("checkpoint_path"):
        return False, "selected_failed"
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


def _round_summary(run_dir, number, trusted_manifest) -> dict | None:
    round_dir = run_dir / f"round_{number}"
    if not round_dir.is_dir():
        return None
    blockers = []
    parent_rows = _read_jsonl(round_dir / "parent/train_trajectories.jsonl")
    attempts = sorted(round_dir.glob("attempts/attempt_*"))
    attempt_names = {a.name for a in attempts}
    attempt = round_dir / "attempts/attempt_1"
    if attempt_names != {"attempt_1"}:
        blockers.append(f"attempt set {sorted(attempt_names)} != {{attempt_1}}")
    candidate_rows = _read_jsonl(attempt / "candidate/train_trajectories.jsonl")
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

    parent_calls = _side_calls(parent_rows, round_dir / "rollout_cache", number, parent_state)
    candidate_calls = _side_calls(candidate_rows, attempt / "rollout_cache", number, candidate_state)
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


def _workflow_milestones(run_dir) -> dict:
    workflow = _read_json(run_dir / "meta_session/workflow_state.json") or {}
    stages = {s.get("id"): s for s in workflow.get("stages", []) if isinstance(s, dict)}
    required = ["A0"] + [f"B{n}" for n in range(1, EXPECTED_ROUNDS + 1)]
    incomplete = [sid for sid in required
                  if stages.get(sid, {}).get("status") != "complete"
                  or stages.get(sid, {}).get("remaining_milestones")]
    return {"current_stage": workflow.get("current_stage"),
            "finish_allowed": workflow.get("finish_allowed"),
            "final_stage": workflow.get("final_stage"),
            "incomplete_stages": incomplete,
            "stages_present": sorted(stages)}


def _ordered_turns(run_dir) -> list[dict]:
    turns = []
    turns_dir = run_dir / "meta_session/turns"
    if turns_dir.is_dir():
        for turn_dir in sorted(turns_dir.iterdir()):
            if not turn_dir.is_dir():
                continue
            command = _read_json(turn_dir / "command.json") or {}
            receipt = _read_json(turn_dir / "receipt.json") or {}
            request = command.get("request") or {}
            turns.append({"name": turn_dir.name, "operation": request.get("operation"),
                          "round_number": request.get("round_number"),
                          "status": receipt.get("status"), "remaining": receipt.get("remaining")})
    return turns


def _finish_valid(run_dir) -> tuple[bool, list[str]]:
    reasons = []
    turns = _ordered_turns(run_dir)
    finish_turns = [t for t in turns if t["operation"] == "finish_experiment"]
    if not finish_turns:
        return False, ["finish_experiment turn missing"]
    finish = finish_turns[-1]
    if finish["status"] != "complete":
        reasons.append("finish_experiment status not complete")
    if finish["remaining"]:
        reasons.append("finish_experiment remaining not empty")
    # finish must come after the B5 snapshot turn
    b5_snapshot_idx = max((i for i, t in enumerate(turns)
                           if t["operation"] == "snapshot_task_meta" and t["round_number"] == EXPECTED_ROUNDS),
                          default=-1)
    finish_idx = turns.index(finish)
    if b5_snapshot_idx < 0 or finish_idx <= b5_snapshot_idx:
        reasons.append("finish_experiment not ordered after B5 snapshot")
    return not reasons, reasons


def _jobs_validation(run_dir) -> dict:
    jobs_dir = run_dir / "meta_session/jobs"
    total = 0
    missing, invalid, required_failed = [], [], []
    if not jobs_dir.is_dir() or not any(jobs_dir.iterdir()):
        return {"total": 0, "missing_final_receipts": ["<no job inventory>"],
                "invalid": [], "required_failed": []}
    for job_dir in sorted(jobs_dir.iterdir()):
        if not job_dir.is_dir():
            continue
        job = _read_json(job_dir / "job.json")
        if not isinstance(job, dict):
            invalid.append(job_dir.name + ":job.json")
            continue
        total += 1
        job_id = job.get("job_id") or job_dir.name
        request = job.get("request") or {}
        operation = request.get("operation")
        result_path = job.get("result_path")
        turn_dir = job.get("turn_dir")
        # result must live under this run's controller_transport
        try:
            Path(result_path).resolve().relative_to((run_dir / "controller_transport").resolve())
            path_ok = True
        except (ValueError, TypeError):
            path_ok = False
        result = _read_json(result_path) if result_path else None
        if not isinstance(result, dict):
            missing.append(job_id)
            continue
        if not path_ok or result.get("operation") != operation:
            invalid.append(job_id + ":binding")
        turn_receipt = _read_json(Path(turn_dir) / "receipt.json") if turn_dir else None
        if not isinstance(turn_receipt, dict):
            invalid.append(job_id + ":turn")
        status = result.get("status")
        if status in NON_TERMINAL_JOB_STATUS or status is None:
            invalid.append(job_id + ":non-terminal")
        if operation in REQUIRED_JOB_OPERATIONS:
            ok = status in SUCCESS_JOB_STATUS
            if status == "exited":
                ok = result.get("returncode") == 0
            if status == "completed" and result.get("returncode") not in (None, 0):
                ok = False
            if not ok:
                required_failed.append(job_id)
    return {"total": total, "missing_final_receipts": missing,
            "invalid": invalid, "required_failed": required_failed}


def _supervision(run_dir) -> dict:
    supervision_dir = run_dir / "meta_session/supervision"
    attempts = []
    if supervision_dir.is_dir():
        attempts = sorted((d for d in supervision_dir.iterdir() if d.is_dir() and d.name.isdigit()),
                          key=lambda d: int(d.name))
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
        if not (identity_ok and reconcile_ok and exit_value == "0"):
            all_accounted = False
        if attempt is attempts[-1]:
            latest_reconcile = reconcile_ok
            latest_exit = exit_value == "0"
            latest_identity = identity_ok
    return {"present": bool(attempts), "attempts": [a.name for a in attempts],
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


def _external_cleanup(run_dir, cleanup_receipt_path, config, output_root, supervision) -> dict:
    result = {"status": "missing", "ok": False, "blockers": ["external_cleanup_missing"],
              "path": str(cleanup_receipt_path) if cleanup_receipt_path else None, "sha256": None}
    if not cleanup_receipt_path:
        return result
    receipt = _read_json(cleanup_receipt_path)
    result["sha256"] = _sha256(cleanup_receipt_path)
    if not isinstance(receipt, dict):
        result["blockers"] = ["external_cleanup_unparseable"]
        return result
    blockers = []
    if receipt.get("kind") != "rsiflow_external_cleanup":
        blockers.append("external_cleanup_wrong_kind")
    status = receipt.get("status")
    result["status"] = status
    if status != "completed":
        blockers.append(f"external_cleanup_{status or 'unknown'}")
    if receipt.get("blockers"):
        blockers.append("external_cleanup_has_blockers")
    binding = receipt.get("binding") or {}
    if binding.get("run_dir") != str(run_dir):
        blockers.append("external_cleanup_run_binding")
    if binding.get("output_root") != str(output_root):
        blockers.append("external_cleanup_output_binding")
    if config is not None and binding.get("config_path") != str(config.get("_config_path")):
        blockers.append("external_cleanup_config_binding")
    if binding.get("supervision_attempt") not in supervision.get("attempts", []):
        blockers.append("external_cleanup_supervision_binding")
    registry = receipt.get("registry") or {}
    expected_registry = str(Path(output_root) / "services/four_gpu_services.json")
    if registry.get("path") != expected_registry:
        blockers.append("external_cleanup_registry_binding")
    if registry.get("phase") != "services_stopped":
        blockers.append("external_cleanup_registry_not_stopped")
    sup = receipt.get("supervision") or {}
    if not sup.get("all_previous_attempts_accounted_for"):
        blockers.append("external_cleanup_supervision_unaccounted")
    owners = receipt.get("owners") or {}
    for group in ("launcher", "app_server"):
        if (owners.get(group) or {}).get("state") != "exited":
            blockers.append(f"external_cleanup_{group}_not_exited")
    for group in ("jobs", "training_phases", "inference_services"):
        for owner in owners.get(group, []) or []:
            if owner.get("state") != "exited":
                blockers.append(f"external_cleanup_{group}_not_exited")
                break
    gpu_observations = receipt.get("gpu_observations") or []
    if not gpu_observations:
        blockers.append("external_cleanup_no_gpu_observation")
    else:
        latest = gpu_observations[-1]
        if latest.get("compute_apps"):
            blockers.append("external_cleanup_gpu_compute_apps")
        if not latest.get("free_verified"):
            blockers.append("external_cleanup_gpu_not_free")
    evidence = receipt.get("evidence_files_sha256") or {}
    if not evidence:
        blockers.append("external_cleanup_no_evidence_hashes")
    else:
        for rel, expected in evidence.items():
            actual = _sha256(Path(cleanup_receipt_path).parent / rel)
            if actual is None or actual != expected:
                blockers.append("external_cleanup_evidence_hash")
                break
    result["blockers"] = blockers
    result["ok"] = not blockers
    return result


def _active_chain(rounds, active) -> list[str]:
    reasons = []
    previous_identity = None
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
    if release_dir and not trusted_blockers:
        for n in range(1, EXPECTED_ROUNDS + 1):
            if (run_dir / f"round_{n}").is_dir():
                try:
                    trusted_manifests[n] = _trusted_manifest(release_dir, n)
                except (OSError, ValueError, KeyError) as exc:
                    trusted_blockers.append(f"B{n} frozen manifest invalid: {exc}")
                    trusted_manifests[n] = None

    rounds = [s for s in (_round_summary(run_dir, n, trusted_manifests.get(n))
                          for n in range(1, EXPECTED_ROUNDS + 1)) if s]
    completed_rounds = sum(1 for s in rounds if s["terminal"] and s["products_complete"])
    present = {s["round"] for s in rounds}
    missing_rounds = [n for n in range(1, EXPECTED_ROUNDS + 1) if n not in present]
    status = _run_status(run_dir)
    workflow = _workflow_milestones(run_dir)
    finish_ok, finish_reasons = _finish_valid(run_dir)
    jobs = _jobs_validation(run_dir)
    active = _read_json(run_dir / "active_task.json") or {}
    supervision = _supervision(run_dir)
    registry = _service_registry(run_dir, active, output_root)
    cleanup = _external_cleanup(run_dir, cleanup_receipt_path, config, output_root, supervision)
    skills_present = (run_dir / "meta/skills.jsonl").is_file()
    context_present = (run_dir / "meta/context.json").is_file()
    a0_receipt = run_dir / "snapshots/A0/snapshot_receipt.json"
    a0_present = a0_receipt.is_file()
    a0_state = _read_json(run_dir / "snapshots/A0/task/task_state_snapshot.json")
    chain_reasons = _active_chain(rounds, active)
    round_parent_states = {s["round"]: s.get("parent_state") for s in rounds}

    protocol_blockers = list(trusted_blockers)
    if missing_rounds:
        protocol_blockers.append("missing rounds: " + ", ".join(f"B{n}" for n in missing_rounds))
    incomplete = [s["round"] for s in rounds if not s["products_complete"]]
    if incomplete:
        protocol_blockers.append("rounds lacking required products: " + ", ".join(f"B{n}" for n in incomplete))
    if completed_rounds != EXPECTED_ROUNDS:
        protocol_blockers.append(f"completed_rounds={completed_rounds} != expected {EXPECTED_ROUNDS}")
    if not a0_present or not isinstance(a0_state, dict) or a0_state.get("generation") != 0:
        protocol_blockers.append("A0 snapshot missing/invalid initial state")
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