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
