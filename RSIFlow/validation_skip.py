import hashlib
from pathlib import Path


def reject_complete_cache_for_skip(out, skip_ace):
    if skip_ace and (Path(out) / "complete.json").exists():
        raise ValueError("ACE-skipped validation cannot reuse a complete evaluation cache; use an independent output directory")


def ace_skip_summary(settings, selected_manifest, results, metrics, out, *, source_role="independent_validation", task_state_hash=None):
    expected = [name for name in settings["benchmark_ids"] if name != "acebench"]
    expected_total = len(selected_manifest["tasks"])
    expected_scored = sum(row["source"] != "acebench" for row in selected_manifest["tasks"])
    ace_result = Path(out) / "scores" / "acebench" / "result.json"
    if ([row["benchmark_id"] for row in results] != expected
            or metrics["overall"]["n_scored"] != expected_scored
            or metrics["overall"]["complete"]
            or ace_result.exists()):
        raise RuntimeError(f"ACE-skipped validation must score exactly {expected_scored}/{expected_total} without ACE")
    metrics_path = Path(out) / "task_metrics.json"
    metrics_sha256 = hashlib.sha256(metrics_path.read_bytes()).hexdigest()
    return {
        "status": "code_search_completed_ace_pending",
        "source_role": source_role,
        "feedback_to_meta": False,
        "task_state_hash": task_state_hash,
        "benchmarks_completed": expected,
        "acebench_status": "incomplete_requires_audit",
        "officially_scored": expected_scored,
        "expected_total": expected_total,
        "metrics_sha256": metrics_sha256,
    }
