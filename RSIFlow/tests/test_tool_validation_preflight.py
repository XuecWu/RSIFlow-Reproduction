import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from tool_validation import check_native


def test_native_preflight_allows_cold_shared_filesystem_imports_and_records_two_rpcs():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repository = root / "repository"
        repository.mkdir()
        output = root / "output"
        observed = {}

        def native_layout(identifier, spec):
            return repository, spec, {}, []

        def run_native(command, work, output, log, handler, timeout):
            observed["timeout"] = timeout
            observed["command"] = command
            requests = [
                {"model": "gpt-rsi-preflight", "input": "test_override_cpu_only"},
                {"model": "gpt-rsi-preflight", "input": "second"},
            ]
            for request in requests:
                response = handler(request)
                assert response["choices"][0]["message"]["content"] == "test_override_cpu_only"

        with patch("tool_validation.native_layout", native_layout), patch("tool_validation.run_native", run_native):
            check_native("bfcl_v3", {"python_executable": "python"}, output)
        assert observed["timeout"] == 900
        assert Path(observed["command"][6]).is_absolute()
        assert Path(observed["command"][7]).is_absolute()
        assert Path(observed["command"][8]).is_absolute()
        receipt = json.loads((output / "check.json").read_text(encoding="utf-8"))
        assert receipt == {
            "status": "completed",
            "controlled_rpc_calls": 2,
            "response": "test_override_cpu_only",
            "requests": [
                {"model": "gpt-rsi-preflight", "input": "test_override_cpu_only"},
                {"model": "gpt-rsi-preflight", "input": "second"},
            ],
        }


def test_native_preflight_missing_proc_remains_infrastructure_error():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repository = root / "repository"
        repository.mkdir()
        output = root / "output"

        def native_layout(identifier, spec):
            return repository, spec, {}, []

        def run_native(command, work, output, log, handler, timeout):
            raise RuntimeError("Native evaluator failed: /proc/self/status is absent")

        with patch("tool_validation.native_layout", native_layout), patch(
            "tool_validation.run_native", run_native
        ), pytest.raises(RuntimeError, match="Native evaluator failed.*proc") as failure:
            check_native("bfcl_v3", {"python_executable": "python"}, output)
        assert failure.type is RuntimeError
        assert not (output / "check.json").exists()


def test_native_preflight_does_not_resolve_away_output_symlink():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        repository = root / "repository"
        repository.mkdir()
        (root / "dedicated").mkdir()
        output = root / "output-link"
        output.symlink_to(root / "dedicated", target_is_directory=True)

        def native_layout(identifier, spec):
            return repository, spec, {}, []

        def run_native(command, work, output, log, handler, timeout):
            if Path(output).is_symlink():
                raise ValueError("Native output must not use symlinks")

        with patch("tool_validation.native_layout", native_layout), patch(
            "tool_validation.run_native", run_native
        ), pytest.raises(ValueError, match="Native output must not use symlinks"):
            check_native("bfcl_v3", {"python_executable": "python"}, output)


def test_bfcl_attribution_requires_explicit_context_and_hashes_request():
    from tool_validation import attribution_from_receipts
    from rsiflow.task_meta.durable import value_hash

    request = {"model": "frozen", "messages": [{"role": "user", "content": "Q"}], "tools": []}
    receipts = {"r1": {"status": "completed", "identity": {
        "state_hash": "state", "request": request, "attribution": {"native_id": "n1", "category": "simple"}},
        "user_simulator": False, "calls": [{"status": "completed"}]}}
    attribution = attribution_from_receipts(receipts, {"n1": ["r1"]}, "state", ["n1"], {"n1": "simple"})
    assert attribution["selected_task_ids"] == ["n1"]
    assert attribution["tasks"]["n1"][0]["request_sha256"] == value_hash(request)
    with pytest.raises(RuntimeError, match="native context"):
        attribution_from_receipts({"r1": {"status": "completed", "identity": {
            "state_hash": "state", "request": request}, "user_simulator": False,
            "calls": [{"status": "completed"}]}}, {"n1": ["r1"]}, "state", ["n1"], {"n1": "simple"})


def test_bfcl_attribution_rejects_duplicate_receipt_ownership():
    from tool_validation import attribution_from_receipts
    receipt = {"status": "completed", "identity": {"state_hash": "state", "request": {},
               "attribution": {"native_id": "n1", "category": "simple"}}, "user_simulator": False,
               "calls": [{"status": "completed"}]}
    with pytest.raises(RuntimeError, match="shared"):
        attribution_from_receipts({"r1": receipt}, {"n1": ["r1"], "n2": ["r1"]}, "state", ["n1", "n2"],
                                  {"n1": "simple", "n2": "simple"})


def test_bfcl_attribution_rejects_unowned_or_incomplete_receipts():
    from tool_validation import attribution_from_receipts
    receipt = {"status": "completed", "identity": {"state_hash": "state", "request": {},
               "attribution": {"native_id": "n1", "category": "simple"}}, "user_simulator": False,
               "calls": [{"status": "completed"}]}
    args = ("state", ["n1"], {"n1": "simple"})
    with pytest.raises(RuntimeError, match="without explicit native task context"):
        attribution_from_receipts({"r1": receipt, "r2": receipt}, {"n1": ["r1"]}, *args)
    incomplete = {**receipt, "calls": [{"status": "started"}]}
    with pytest.raises(RuntimeError, match="completed local model calls"):
        attribution_from_receipts({"r1": incomplete}, {"n1": ["r1"]}, *args)


def test_native_rpc_attribution_is_separate_from_model_request():
    from official_tool_entry import rpc_frame
    request = {"model": "frozen", "messages": [{"role": "user", "content": "same"}], "tools": []}
    frame = rpc_frame(4, request, {"native_id": "n1", "category": "simple"})
    assert frame["request"] == request
    assert frame["attribution"] == {"native_id": "n1", "category": "simple"}
    assert "_rsiflow_attribution" not in json.dumps(frame["request"])


def test_pinned_handler_attribution_context_is_scoped_and_validated():
    from official_tool_entry import instrument_bfcl_handler, current_bfcl_attribution
    observed = []

    class Handler:
        def inference(self, entry, include_input_log=False, exclude_state_log=False):
            observed.append(current_bfcl_attribution())
            return entry["id"]

    instrumented = instrument_bfcl_handler(Handler, {"n1": "simple"})
    assert instrumented().inference({"id": "n1"}, False, False) == "n1"
    assert observed == [{"native_id": "n1", "category": "simple"}]
    assert current_bfcl_attribution() is None
    with pytest.raises(RuntimeError, match="outside the frozen selected queue"):
        instrumented().inference({"id": "other"}, False, False)


def test_pinned_handler_hook_fails_closed_for_commit_or_source_mismatch(tmp_path):
    from official_tool_entry import verify_pinned_handler_source
    source = tmp_path / "base_handler.py"
    source.write_text("different source", encoding="utf-8")
    with pytest.raises(RuntimeError, match="source hash mismatch"):
        verify_pinned_handler_source(source, "ea13468e4423454d0c213704fb87cf7cb3990433")
    with pytest.raises(RuntimeError, match="pinned official commit"):
        verify_pinned_handler_source(source, "other")


def test_completed_bfcl_cache_without_attribution_fails_closed(tmp_path, monkeypatch):
    from tool_validation import evaluate_tool, metric_rows
    repo = tmp_path / "repo"
    repo.mkdir()
    output = tmp_path / "out"
    output.mkdir()
    def layout(identifier, spec):
        return repo, {**spec, "categories": ["simple"]}, {"simple": ["n1"]}, []

    monkeypatch.setattr("tool_validation.native_layout", layout)
    (output / "BFCL_v3_simple_result.json").write_text(json.dumps([{"id": "n1", "output": "answer"}]))
    (output / "BFCL_v3_simple_score.json").write_text(json.dumps([
        {"accuracy": 1.0, "total_count": 1, "correct_count": 1}]))
    cached = {**metric_rows("bfcl_v3", output, {"simple": ["n1"]}),
              "benchmark": "bfcl_v3", "state_hash": "s"}
    (output / "result.json").write_text(json.dumps(cached), encoding="utf-8")
    frozen = {"source_role": "final_test", "explicit_full_benchmark": False,
              "purpose": "report_only", "formal_report_evaluation": True, "manifest_hash": "m",
              "evaluation_scope": {"manifest_hash": "m"}, "state_hash": "s"}
    spec = {"selected_tasks": [{"task_id": "bfcl_v3:n1"}], "selected_task_ids": ["n1"], "explicit_full_benchmark": False}
    with pytest.raises(RuntimeError, match="attribution"):
        evaluate_tool("bfcl_v3", object(), frozen, spec, {}, output)


def test_evaluate_tool_completion_uses_primary_receipt_and_verified_call_sidecar(tmp_path, monkeypatch):
    from tool_validation import evaluate_tool
    from rsiflow.task_meta.durable import value_hash
    from common import write

    repo = tmp_path / "repo"
    repo.mkdir()
    native = repo / "BFCL_v3_simple.json"
    native.write_text(json.dumps([{"id": "n1"}]), encoding="utf-8")
    output = tmp_path / "out"
    frozen = {"state_hash": "state", "source_role": "final_test"}
    spec = {"selected_tasks": [{"task_id": "bfcl_v3:n1"}], "selected_task_ids": ["n1"],
            "official_commit": "ea13468e4423454d0c213704fb87cf7cb3990433", "categories": ["simple"]}

    monkeypatch.setattr("tool_validation.native_layout",
                        lambda identifier, value: (repo, value, {"simple": ["n1"]}, [native]))

    class FakeHarness:
        user_client = None
        def __init__(self, config, frozen_value, directory, user_spec):
            self.directory = Path(directory)
            self.directory.mkdir(parents=True, exist_ok=True)
        def complete(self, body):
            request = {key: value for key, value in body.items() if key != "_rsiflow_attribution"}
            identity = {"state_hash": "state", "request": request,
                        "attribution": body["_rsiflow_attribution"]}
            key = value_hash(identity)
            write(self.directory / f"{key}.json", {"status": "completed", "identity": identity,
                  "user_simulator": False, "calls": [{"status": "completed"}]})
            write(self.directory / f"{key}.call_000.json", {"status": "completed", "response": {"ok": True}})
            return {"id": "response"}

    def fake_run_native(command, work, out, log, handler):
        if command[4] == "generate":
            handler({"model": "frozen", "messages": [{"role": "user", "content": "Q"}],
                     "tools": [], "_rsiflow_attribution": {"native_id": "n1", "category": "simple"}})
            result_dir = Path(out) / "result"
            result_dir.mkdir(parents=True)
            (result_dir / "BFCL_v3_simple_result.json").write_text(json.dumps([{"id": "n1", "output": "answer"}]))
        else:
            score_dir = Path(out) / "score"
            score_dir.mkdir(parents=True)
            (score_dir / "BFCL_v3_simple_score.json").write_text(json.dumps([
                {"accuracy": 1.0, "total_count": 1, "correct_count": 1}]))

    monkeypatch.setattr("tool_validation.HarnessAPI", FakeHarness)
    monkeypatch.setattr("tool_validation.run_native", fake_run_native)
    result = evaluate_tool("bfcl_v3", object(), frozen, spec, {}, output)
    assert result["status"] == "completed"
    assert result["completed_model_calls"] == 1
    attribution = json.loads((output / "task_call_attribution.json").read_text())
    entry = attribution["tasks"]["n1"][0]
    assert set(entry) == {"receipt_key", "request_sha256"}
    assert (output / "transport" / f"{entry['receipt_key']}.call_000.json").is_file()
