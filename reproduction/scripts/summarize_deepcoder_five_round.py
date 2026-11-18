"""Read-only summarizer for the DeepCoder single-domain five-round canary.

`summarize(run_dir) -> dict` recomputes per-round facts from the real run files
(trajectories, persisted call receipts, performance, decision/selection, snapshots,
meta state/jobs). It never modifies scoring or selection, never invents scores, and
never claims `run_complete` unless every required real fact is present. The CLI only
reads a real run and prints JSON (default) or a Markdown fact sheet.

Protocol values such as `infrastructure_error` are preserved verbatim; process
anomalies (EAGAIN/ENOENT, zero model calls) are counted and associated with outcome
transitions without re-scoring.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

EXPECTED_ROUNDS = 5
TERMINAL_DECISIONS = {"accept_candidate", "retain_parent"}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _success(row: dict) -> bool:
    return bool((row.get("verification") or {}).get("success"))


def _side_stats(rows: list[dict]) -> dict:
    denominator = len(rows)
    successes = sum(1 for r in rows if _success(r))
    verification = {
        "completed": sum(1 for r in rows if (r.get("verification") or {}).get("status") == "completed"),
        "valid_answer": sum(1 for r in rows if r.get("valid_answer")),
        "full_verifier": sum(1 for r in rows if (r.get("verification") or {}).get("full_verifier")),
        "success": successes,
    }
    execution_error_type = Counter(r.get("execution_error_type") or "none" for r in rows)
    errors = {
        "execution_error_type": dict(sorted(execution_error_type.items())),
        "zero_call": sum(1 for r in rows if (r.get("model_call_count") or 0) == 0),
        "eagain": sum(1 for r in rows if "EAGAIN" in str(r.get("error") or "") or "Errno 11" in str(r.get("error") or "")),
        "enoent": sum(1 for r in rows if "ENOENT" in str(r.get("error") or "") or "Errno 2" in str(r.get("error") or "")
                      or "FileNotFoundError" in str(r.get("error") or "")),
        "infrastructure_error_true": sum(1 for r in rows if r.get("infrastructure_error")),
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


def _persisted_receipts(round_dir: Path, rows: list[dict]) -> int:
    """Count persisted call receipts for this side via the rollout-cache binding.

    Real rollout cache stores `<key>.json` = {binding:{task,state_hash,...}, row}
    plus a sibling `<key>.calls/` directory of per-call receipts. Match by the
    binding's task_id and state_hash (not the trajectory row's trajectory_id).
    Parent receipts live under `round_N/rollout_cache/<parent_state>/`; candidate
    receipts under `round_N/attempts/attempt_K/rollout_cache/<candidate_state>/`,
    so search recursively and let the binding select the correct side.
    """
    side_tasks = {r.get("task_id") for r in rows}
    side_states = {r.get("state_hash") for r in rows}
    total = 0
    for cache_json in round_dir.glob("**/rollout_cache/*/train_rollouts/*.json"):
        receipt = _read_json(cache_json)
        if not receipt:
            continue
        binding = receipt.get("binding") or {}
        if binding.get("task") not in side_tasks or binding.get("state_hash") not in side_states:
            continue
        calls_dir = cache_json.parent / (cache_json.stem + ".calls")
        if calls_dir.is_dir():
            total += sum(1 for _ in calls_dir.glob("*.json"))
    return total


def _transitions(parent_rows: list[dict], candidate_rows: list[dict]) -> dict:
    parent = {r.get("task_id"): _success(r) for r in parent_rows}
    candidate = {r.get("task_id"): _success(r) for r in candidate_rows}
    ids = set(parent) | set(candidate)
    counts = {"new_success": 0, "new_regression": 0, "retained_failure": 0, "retained_success": 0}
    for tid in ids:
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


def _zero_call_overlap(parent_rows: list[dict], candidate_rows: list[dict], transitions: dict) -> dict:
    parent_calls = {r.get("task_id"): int(r.get("model_call_count") or 0) for r in parent_rows}
    candidate_calls = {r.get("task_id"): int(r.get("model_call_count") or 0) for r in candidate_rows}
    parent_success = {r.get("task_id"): _success(r) for r in parent_rows}
    candidate_success = {r.get("task_id"): _success(r) for r in candidate_rows}

    def zero(tid):
        return parent_calls.get(tid, 0) == 0 or candidate_calls.get(tid, 0) == 0

    new_success_zero = sum(1 for tid in candidate_success
                           if candidate_success[tid] and not parent_success.get(tid, False) and zero(tid))
    new_regression_zero = sum(1 for tid in parent_success
                              if parent_success[tid] and not candidate_success.get(tid, False) and zero(tid))
    return {"new_success_zero_call": new_success_zero, "new_regression_zero_call": new_regression_zero}


def _round_summary(run_dir: Path, number: int) -> dict | None:
    round_dir = run_dir / f"round_{number}"
    if not round_dir.is_dir():
        return None
    parent_rows = _read_jsonl(round_dir / "parent/train_trajectories.jsonl")
    attempts = sorted(round_dir.glob("attempts/attempt_*"))
    attempt = attempts[0] if attempts else None
    candidate_rows = _read_jsonl(attempt / "candidate/train_trajectories.jsonl") if attempt else []
    decision = _read_json(attempt / "decision.json") if attempt else None
    selection = _read_json(attempt / "selection.json") if attempt else None
    parent = _side_stats(parent_rows)
    candidate = _side_stats(candidate_rows)
    parent["persisted_receipts"] = _persisted_receipts(round_dir, parent_rows)
    candidate["persisted_receipts"] = _persisted_receipts(round_dir, candidate_rows)

    def side_ok(stats):
        return (stats["trajectory_calls"] == stats["transport_calls"] == stats["persisted_receipts"])

    cross_check_ok = side_ok(parent) and side_ok(candidate)
    denominator_match = parent["denominator"] == candidate["denominator"] and parent["denominator"] > 0
    ids_match = parent["ids"] == candidate["ids"]
    source_hashes_match = (parent["task_source_hashes"] == candidate["task_source_hashes"]
                           and parent["manifest_hashes"] == candidate["manifest_hashes"])
    transitions = _transitions(parent_rows, candidate_rows)
    delta = None
    if parent["macro_success"] is not None and candidate["macro_success"] is not None:
        delta = candidate["macro_success"] - parent["macro_success"]
    decision_value = (selection or {}).get("decision")
    terminal = decision_value in TERMINAL_DECISIONS
    activated = decision_value == "accept_candidate"
    snapshot_receipt = run_dir / "snapshots" / f"round_{number}_attempt_1/snapshot_receipt.json"
    snapshot_present = snapshot_receipt.is_file()
    candidate_performance = (attempt / "candidate/performance.json") if attempt else None
    products_complete = bool(
        terminal and decision and selection and candidate_performance and candidate_performance.is_file()
        and snapshot_present and denominator_match and ids_match and source_hashes_match and cross_check_ok)
    evidence_hashes = {
        "parent_trajectories": _sha256(round_dir / "parent/train_trajectories.jsonl"),
        "parent_performance": _sha256(round_dir / "parent/performance.json"),
        "candidate_trajectories": _sha256(attempt / "candidate/train_trajectories.jsonl") if attempt else None,
        "candidate_performance": _sha256(candidate_performance) if candidate_performance else None,
        "decision": _sha256(attempt / "decision.json") if attempt else None,
        "selection": _sha256(attempt / "selection.json") if attempt else None,
        "snapshot_receipt": _sha256(snapshot_receipt),
    }
    return {
        "round": number,
        "component": (decision or {}).get("component"),
        "attempt_count": len(attempts),
        "selection": selection,
        "activated": activated,
        "terminal": terminal,
        "products_complete": products_complete,
        "parent": parent,
        "candidate": candidate,
        "denominator_match": denominator_match,
        "ids_match": ids_match,
        "source_hashes_match": source_hashes_match,
        "calls": {"parent": {k: parent[k] for k in ("trajectory_calls", "transport_calls", "persisted_receipts")},
                  "candidate": {k: candidate[k] for k in ("trajectory_calls", "transport_calls", "persisted_receipts")},
                  "cross_check_ok": cross_check_ok},
        "verification": {"parent": parent["verification"], "candidate": candidate["verification"]},
        "errors": {"parent": parent["errors"], "candidate": candidate["errors"]},
        "delta": delta,
        "transitions": transitions,
        "zero_call_overlap": _zero_call_overlap(parent_rows, candidate_rows, transitions),
        "snapshot_present": snapshot_present,
        "evidence_hashes": evidence_hashes,
    }


def _run_status(run_dir: Path) -> dict:
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
        "current_stage": stage.group(1).strip() if stage else None,
        "finish_allowed": (finish.group(1).strip().lower() == "yes") if finish else False,
    }


def _jobs_final_receipts(run_dir: Path) -> dict:
    jobs_dir = run_dir / "meta_session/jobs"
    total = 0
    missing = []
    if jobs_dir.is_dir():
        for job_dir in sorted(jobs_dir.iterdir()):
            job = _read_json(job_dir / "job.json")
            if not job:
                continue
            total += 1
            result_path = job.get("result_path")
            if not result_path or not Path(result_path).is_file():
                missing.append(job.get("job_id") or job_dir.name)
    return {"total": total, "missing_final_receipts": missing}


def _model_sft(rounds: list[dict], run_dir: Path) -> dict:
    recorded = []
    for summary in rounds:
        if summary.get("component") != "MODEL":
            continue
        attempt = run_dir / f"round_{summary['round']}/attempts/attempt_1"
        metrics = _read_json(attempt / "candidate/training_metrics.json")
        decision = _read_json(attempt / "decision.json")
        if metrics and decision and decision.get("component") == "MODEL":
            recorded.append(summary["round"])
    return {"status": "recorded" if recorded else "not_run", "rounds": recorded}


def summarize(run_dir) -> dict:
    run_dir = Path(run_dir)
    rounds = [s for s in (_round_summary(run_dir, n) for n in range(1, EXPECTED_ROUNDS + 1)) if s]
    completed_rounds = sum(1 for s in rounds if s["terminal"] and s["products_complete"])
    present = {s["round"] for s in rounds}
    missing_rounds = [n for n in range(1, EXPECTED_ROUNDS + 1) if n not in present]
    status = _run_status(run_dir)
    jobs = _jobs_final_receipts(run_dir)
    active = _read_json(run_dir / "active_task.json") or {}
    services_stopped = _read_json(run_dir / "meta_session/services_stopped.json")
    services_ok = bool(services_stopped and services_stopped.get("status") == "stopped")

    blockers = []
    if missing_rounds:
        blockers.append("missing rounds: " + ", ".join(f"B{n}" for n in missing_rounds))
    incomplete_products = [s["round"] for s in rounds if not s["products_complete"]]
    if incomplete_products:
        blockers.append("rounds lacking required products: " + ", ".join(f"B{n}" for n in incomplete_products))
    if completed_rounds != EXPECTED_ROUNDS:
        blockers.append(f"completed_rounds={completed_rounds} != expected {EXPECTED_ROUNDS}")
    if status["status"] != "complete":
        blockers.append(f"meta state status={status['status']!r} != 'complete'")
    if not status["finish_allowed"]:
        blockers.append("finish_allowed is not true")
    if jobs["missing_final_receipts"]:
        blockers.append("jobs missing final receipts: " + ", ".join(jobs["missing_final_receipts"]))
    if not services_ok:
        blockers.append("stopped services evidence missing")

    run_complete = (completed_rounds == EXPECTED_ROUNDS and not blockers)
    return {
        "schema_version": 1,
        "run_dir": str(run_dir),
        "expected_rounds": EXPECTED_ROUNDS,
        "completed_rounds": completed_rounds,
        "run_complete": run_complete,
        "run_complete_blockers": blockers,
        "rounds": rounds,
        "active_identity": {k: active.get(k) for k in ("generation", "model_ref", "harness_path", "checkpoint_path")},
        "skills_present": (run_dir / "meta/skills.jsonl").is_file(),
        "context_present": (run_dir / "meta/context.json").is_file(),
        "state": status,
        "jobs": jobs,
        "services_stopped": services_ok,
        "model_sft": _model_sft(rounds, run_dir),
        "evidence_hashes": {
            "active_task": _sha256(run_dir / "active_task.json"),
            "meta_state": _sha256(run_dir / "meta_session/state.json"),
            "skills": _sha256(run_dir / "meta/skills.jsonl"),
            "context": _sha256(run_dir / "meta/context.json"),
        },
    }


def _markdown(result: dict) -> str:
    lines = [f"# DeepCoder five-round summary: {result['run_dir']}",
             f"- completed_rounds: {result['completed_rounds']}/{result['expected_rounds']}",
             f"- run_complete: {result['run_complete']}",
             f"- model_sft: {result['model_sft']['status']} rounds={result['model_sft']['rounds']}"]
    if result["run_complete_blockers"]:
        lines.append("- blockers:")
        lines.extend(f"  - {b}" for b in result["run_complete_blockers"])
    for s in result["rounds"]:
        lines.append(f"\n## B{s['round']} component={s['component']} decision={(s['selection'] or {}).get('decision')}")
        lines.append(f"- denominator parent/candidate: {s['parent']['denominator']}/{s['candidate']['denominator']} "
                     f"match={s['denominator_match']} ids_match={s['ids_match']} source_hashes_match={s['source_hashes_match']}")
        lines.append(f"- delta: {s['delta']} transitions: {s['transitions']}")
        lines.append(f"- calls cross_check_ok: {s['calls']['cross_check_ok']}")
        lines.append(f"- verification parent: {s['verification']['parent']}")
        lines.append(f"- errors parent: {s['errors']['parent']}")
        lines.append(f"- zero_call_overlap: {s['zero_call_overlap']}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args(argv)
    result = summarize(args.run_dir)
    if args.markdown:
        print(_markdown(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
