import argparse
import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "RSIFlow/runtime"))

from rsiflow.task_meta.evalplus_isolated import (
    EvalPlusInfrastructureError,
    evaluate_evalplus_isolated,
    load_pinned_evalplus,
)
from rsiflow.task_meta.reporting import OfficialEvaluatorSpec
from rsiflow.task_meta.storage import digest

BACKEND = "linux_bwrap_seccomp_v1"


def load_spec(config_path):
    configured = json.loads(Path(config_path).read_text(encoding="utf-8"))["evaluators"]
    spec = OfficialEvaluatorSpec(**{**configured["humaneval_plus"], "benchmark": "humaneval_plus"})
    spec.validate()
    return spec


def run_case(native, spec, task, name, solution, output_dir):
    output_dir.mkdir()
    data_path = output_dir / "data.jsonl"
    predictions_path = output_dir / "predictions.jsonl"
    data_path.write_text(json.dumps(task) + "\n", encoding="utf-8")
    predictions_path.write_text(
        json.dumps({"task_id": task["task_id"], "solution": solution}) + "\n",
        encoding="utf-8",
    )
    case = {
        "status": "infrastructure_error",
        "base_status": None,
        "plus_status": None,
        "worker_isolation": None,
        "infrastructure_error": None,
        "official_results_path": None,
        "official_results_sha256": None,
        "data_sha256": digest(data_path),
        "prediction_sha256": digest(predictions_path),
    }
    official_path = output_dir / "official_results.json"
    try:
        receipt = evaluate_evalplus_isolated(
            replace(
                spec,
                data_path=str(data_path),
                data_sha256=digest(data_path),
                subset="official_one_task_bwrap_smoke",
            ),
            predictions_path,
            output_dir,
        )
        results = json.loads(official_path.read_text(encoding="utf-8"))
        expected_source_sha256 = digest(Path(native.__file__))
        if receipt["returncode"] != 0 or receipt["worker_isolation"] != BACKEND:
            raise EvalPlusInfrastructureError("Official evaluator receipt has the wrong backend or return code")
        if results["source_commit"] != spec.commit or results["source_sha256"] != expected_source_sha256:
            raise EvalPlusInfrastructureError("Official evaluator source identity mismatch")
        items = results["eval"][task["task_id"]]
        if len(results["eval"]) != 1 or len(items) != 1:
            raise EvalPlusInfrastructureError("Official smoke denominator is not one task")
        item = items[0]
        case.update({
            "base_status": item["base_status"],
            "plus_status": item["plus_status"],
            "worker_isolation": receipt["worker_isolation"],
            "official_results_path": str(official_path.resolve()),
            "official_results_sha256": hashlib.sha256(official_path.read_bytes()).hexdigest(),
        })
        expected = native.PASS if name == "canonical" else native.FAIL
        if item["base_status"] == item["plus_status"] == expected:
            case["status"] = "official_pass" if expected == native.PASS else "official_fail"
        else:
            case["status"] = "unexpected_official_status"
    except (EvalPlusInfrastructureError, ImportError, OSError, ValueError, KeyError) as exc:
        case["status"] = "infrastructure_error"
        case["infrastructure_error"] = str(exc)
    return case


def run_smoke(spec, output_dir):
    if os.environ.get("RSIFLOW_SANDBOX_BACKEND") != BACKEND:
        raise EvalPlusInfrastructureError(f"Explicit RSIFLOW_SANDBOX_BACKEND={BACKEND} is required")
    output_dir = Path(output_dir).absolute()
    output_dir.mkdir(parents=True, exist_ok=False)
    native = load_pinned_evalplus(Path(spec.entrypoint).resolve().parents[1], spec.commit)
    task = json.loads(next(line for line in Path(spec.data_path).read_text(encoding="utf-8").splitlines() if line))
    solutions = {
        "canonical": task["prompt"] + task["canonical_solution"],
        "wrong": task["prompt"] + "    return None\n",
    }
    report = {
        "schema_version": 1,
        "scope": "official_one_task_smoke_not_a_benchmark_score",
        "backend": BACKEND,
        "source_commit": spec.commit,
        "source_sha256": digest(Path(native.__file__)),
        "task_id": task["task_id"],
        "cases": {},
        "ok": False,
    }
    for name, solution in solutions.items():
        report["cases"][name] = run_case(native, spec, task, name, solution, output_dir / name)
    report["ok"] = (
        report["cases"]["canonical"]["status"] == "official_pass"
        and report["cases"]["wrong"]["status"] == "official_fail"
    )
    report_path = output_dir / "smoke_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=PROJECT / "reproduction/configs/report-evaluators-v4.json")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = run_smoke(load_spec(args.config), args.output)
    except EvalPlusInfrastructureError as exc:
        print(json.dumps({"ok": False, "infrastructure_error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({
        "ok": report["ok"],
        "output": str(Path(args.output).absolute()),
        "cases": {name: case["status"] for name, case in report["cases"].items()},
    }, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
