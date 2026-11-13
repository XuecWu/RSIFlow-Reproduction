import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "RSIFlow/runtime"))
sys.path.insert(0, str(PROJECT / "reproduction/scripts"))

from rsiflow.task_meta import evalplus_isolated
from rsiflow.task_meta.evalplus_isolated import (
    EvalPlusInfrastructureError,
    evaluate_evalplus_isolated,
)
from rsiflow.task_meta.reporting import OfficialEvaluatorSpec
from rsiflow.task_meta.sandbox import LinuxSandbox
from rsiflow.task_meta.storage import digest


def test_evalplus_receipt_uses_selected_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
    root = tmp_path / "evalplus"
    entrypoint = root / "evalplus" / "evaluate.py"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("# pinned fixture\n")
    task = {
        "task_id": "HumanEval/0",
        "prompt": "def answer():\n",
        "canonical_solution": "    return 1\n",
        "entry_point": "answer",
        "base_input": [[]],
        "plus_input": [[]],
        "atol": 0,
    }
    data = tmp_path / "data.jsonl"
    data.write_text(json.dumps(task) + "\n")
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text(json.dumps({
        "task_id": task["task_id"],
        "solution": task["prompt"] + task["canonical_solution"],
    }) + "\n")
    spec = SimpleNamespace(
        entrypoint=str(entrypoint),
        commit=evalplus_isolated.PINNED_COMMIT,
        benchmark="HumanEval+",
        data_path=str(data),
        timeout_seconds=30,
    )
    native = SimpleNamespace(PASS="pass", __file__=str(entrypoint))
    monkeypatch.setattr(evalplus_isolated, "load_pinned_evalplus", lambda root, commit: native)
    monkeypatch.setattr(LinuxSandbox, "validate", lambda self: None)
    monkeypatch.setattr(
        evalplus_isolated,
        "isolated_check",
        lambda *args, **kwargs: (native.PASS, [True], {"transport": "bounded_tagged_json"}),
    )

    def fake_import(name):
        if name == "evalplus.gen.util":
            return SimpleNamespace(trusted_exec=lambda *args, **kwargs: ([1], [0.01]))
        return SimpleNamespace()

    monkeypatch.setattr(evalplus_isolated.importlib, "import_module", fake_import)
    output = tmp_path / "output"
    output.mkdir()

    receipt = evaluate_evalplus_isolated(spec, predictions, output)

    assert receipt["worker_isolation"] == "linux_bwrap_seccomp_v1"


def smoke_spec(tmp_path, task):
    data = tmp_path / "official.jsonl"
    data.write_text(json.dumps(task) + "\n")
    entrypoint = tmp_path / "evalplus" / "evalplus" / "evaluate.py"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("# pinned fixture\n")
    task_ids = tmp_path / "ids.json"
    task_ids.write_text(json.dumps([task["task_id"]]) + "\n")
    return OfficialEvaluatorSpec(
        benchmark="HumanEval+",
        repository="https://github.com/evalplus/evalplus",
        commit=evalplus_isolated.PINNED_COMMIT,
        entrypoint=str(entrypoint),
        entrypoint_sha256=digest(entrypoint),
        python_executable=sys.executable,
        data_path=str(data),
        data_sha256=digest(data),
        task_ids_path=str(task_ids),
        task_ids_sha256=digest(task_ids),
        protocol="evalplus_official_v1",
    )


def smoke_task():
    return {
        "task_id": "HumanEval/0",
        "prompt": "def answer():\n",
        "canonical_solution": "    return 1\n",
        "entry_point": "answer",
        "base_input": [[]],
        "plus_input": [[], []],
        "base_output": [1],
        "plus_output": [1, 1],
        "atol": 0,
    }


def test_smoke_builder_preserves_official_rows_and_constructs_both_predictions(monkeypatch, tmp_path):
    import evalplus_bwrap_smoke

    task = smoke_task()
    spec = smoke_spec(tmp_path, task)
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
    native = SimpleNamespace(PASS="pass", FAIL="fail", __file__=spec.entrypoint)
    monkeypatch.setattr(evalplus_bwrap_smoke, "load_pinned_evalplus", lambda root, commit: native)
    observed = []

    def fake_evaluate(case_spec, predictions_path, output_dir):
        row = json.loads(Path(case_spec.data_path).read_text())
        prediction = json.loads(Path(predictions_path).read_text())
        observed.append((row, prediction, Path(case_spec.data_path), Path(predictions_path)))
        status = native.PASS if prediction["solution"].endswith(task["canonical_solution"]) else native.FAIL
        results = {
            "source_commit": case_spec.commit,
            "source_sha256": digest(Path(native.__file__)),
            "eval": {task["task_id"]: [{
                "task_id": task["task_id"],
                "solution": prediction["solution"],
                "base_status": status,
                "plus_status": status,
            }]},
            "pass_at_k": {"plus": {"pass@1": float(status == native.PASS)}},
        }
        Path(output_dir, "official_results.json").write_text(json.dumps(results))
        return {"returncode": 0, "worker_isolation": "linux_bwrap_seccomp_v1", "source_commit": case_spec.commit}

    monkeypatch.setattr(evalplus_bwrap_smoke, "evaluate_evalplus_isolated", fake_evaluate)
    output = tmp_path / "smoke"

    report = evalplus_bwrap_smoke.run_smoke(spec, output)

    assert [row for row, _, _, _ in observed] == [task, task]
    assert observed[0][1] == {
        "task_id": task["task_id"],
        "solution": task["prompt"] + task["canonical_solution"],
    }
    assert observed[1][1] == {
        "task_id": task["task_id"],
        "solution": task["prompt"] + "    return None\n",
    }
    assert len({data for _, _, data, _ in observed}) == 2
    assert len({predictions for _, _, _, predictions in observed}) == 2
    assert report["ok"] is True
    assert report["cases"]["canonical"]["status"] == "official_pass"
    assert report["cases"]["wrong"]["status"] == "official_fail"
    assert report["cases"]["canonical"]["base_status"] == "pass"
    assert report["cases"]["canonical"]["plus_status"] == "pass"
    assert report["cases"]["wrong"]["base_status"] == "fail"
    assert report["cases"]["wrong"]["plus_status"] == "fail"
    for name, _, data, _ in observed:
        assert json.loads(data.read_text()) == task
    for name in ("canonical", "wrong"):
        official = output / name / "official_results.json"
        expected_hash = hashlib.sha256(official.read_bytes()).hexdigest()
        assert report["cases"][name]["official_results_sha256"] == expected_hash
        assert report["cases"][name]["worker_isolation"] == "linux_bwrap_seccomp_v1"
        assert report["cases"][name]["infrastructure_error"] is None


def test_smoke_builder_keeps_infrastructure_errors_out_of_model_score(monkeypatch, tmp_path):
    import evalplus_bwrap_smoke

    spec = smoke_spec(tmp_path, smoke_task())
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
    native = SimpleNamespace(PASS="pass", FAIL="fail", __file__=spec.entrypoint)
    monkeypatch.setattr(evalplus_bwrap_smoke, "load_pinned_evalplus", lambda root, commit: native)

    for error in (
        EvalPlusInfrastructureError("candidate RPC unavailable"),
        ModuleNotFoundError("No module named 'datasets'"),
    ):
        def fail_infrastructure(*args, error=error):
            raise error

        monkeypatch.setattr(evalplus_bwrap_smoke, "evaluate_evalplus_isolated", fail_infrastructure)

        report = evalplus_bwrap_smoke.run_smoke(spec, tmp_path / f"smoke-{type(error).__name__}")

        assert report["ok"] is False
        assert set(report["cases"]) == {"canonical", "wrong"}
        for case in report["cases"].values():
            assert case["status"] == "infrastructure_error"
            assert case["infrastructure_error"] == str(error)
            assert case["official_results_sha256"] is None
        assert "pass@1" not in json.dumps(report)
