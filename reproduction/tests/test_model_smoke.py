import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from model_service_smoke import run_smoke


class ModelServiceSmokeTests(unittest.TestCase):
    def test_short_generation_is_checked_and_owned_services_are_stopped(self):
        response = {"message": {"content": "RSIFLOW_SMOKE_OK"}, "usage": {"completion_tokens": 2}}
        client = unittest.mock.Mock(return_value=response)
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("model_service_smoke.start_services") as start,
                patch("model_service_smoke.verify_services") as verify,
                patch("model_service_smoke.stop_services") as stop,
                patch("model_service_smoke.registry_path", return_value=Path(directory) / "registry.json"),
                patch("model_service_smoke.LocalTaskClient", return_value=client) as client_type,
            ):
                Path(directory, "registry.json").write_text("{}")
                result = run_smoke(Path(directory) / "out")
            self.assertEqual("RSIFLOW_SMOKE_OK", result["answer"])
            start.assert_called_once()
            verify.assert_called_once()
            stop.assert_called_once()
            self.assertEqual(16, client.call_args.kwargs["max_tokens"])
            self.assertEqual("http://127.0.0.1:8271/v1", client_type.call_args.args[1])

    def test_startup_failure_also_attempts_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch("model_service_smoke.start_services", side_effect=RuntimeError("startup failed")),
                patch("model_service_smoke.stop_services") as stop,
                patch("model_service_smoke.registry_path", return_value=Path(directory) / "registry.json"),
            ):
                Path(directory, "registry.json").write_text("{}")
                with self.assertRaisesRegex(RuntimeError, "startup failed"):
                    run_smoke(Path(directory) / "out")
            stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
