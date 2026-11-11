import tempfile
from pathlib import Path
from unittest.mock import patch

from tool_validation import check_native


def test_native_preflight_allows_cold_shared_filesystem_imports():
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
            handler({"model": "gpt-rsi-preflight"})
            handler({"model": "gpt-rsi-preflight"})

        with patch("tool_validation.native_layout", native_layout), patch("tool_validation.run_native", run_native):
            check_native("bfcl_v3", {"python_executable": "python"}, output)
        assert observed["timeout"] == 900
