import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from launch_meta import SubprocessToolExecutor, _autodl_key, prepare_codex_home


class CustomProviderTests(unittest.TestCase):
    def test_custom_codex_home_and_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / "host.toml"
            host.write_text('model = "gpt-6-astra"\nmodel_provider = "OpenAI"\nmodel_catalog_json = "__MODEL_CATALOG_JSON__"\n')
            catalog = root / "astra.json"
            catalog.write_text(json.dumps({"models": [{"slug": "gpt-6-astra"}]}))
            run = root / "run"
            home = prepare_codex_home(run, host_path=host, catalog_path=catalog)
            config = (home / "config.toml").read_text()
            self.assertIn('model = "gpt-6-astra"', config)
            self.assertIn('model_provider = "OpenAI"', config)
            self.assertIn(str(catalog.resolve()), config)
            host.write_text('model = "should-not-overwrite"\n')
            self.assertEqual(home, prepare_codex_home(run, host_path=host, catalog_path=catalog))
            self.assertIn("gpt-6-astra", (home / "config.toml").read_text())

    def test_api_key_environment_name_is_configurable(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}, clear=True):
            self.assertEqual("secret", _autodl_key("OPENAI_API_KEY"))
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
            _autodl_key("OPENAI_API_KEY")

    def test_openai_key_is_not_forwarded_to_controller(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            received = {}

            def fake_run(argv, *, input, text, stdout, stderr, cwd, check, env):
                received.update(env)
                stdout.write("")
                stderr.write("")
                Path(argv[argv.index("--result-file") + 1]).write_text('{"status":"fact"}')
                from types import SimpleNamespace
                return SimpleNamespace(returncode=0)

            adapter = SubprocessToolExecutor(project_root=project, run_dir=root / "run")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "secret"}, clear=True), patch("launch_meta.subprocess.run", side_effect=fake_run):
                self.assertEqual({"status": "fact"}, adapter.execute({"operation": "compare_scores"}))
            self.assertNotIn("OPENAI_API_KEY", received)

    def test_backend_accepts_registered_custom_endpoint_offline(self):
        from types import SimpleNamespace
        from task_adapter import TaskAdapter
        from rsiflow.task_meta.meta_backends.codex_openrouter import CodexOpenRouterBackend

        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as directory:
            adapter = TaskAdapter(root / "RSIFlow", Path(directory) / "receipts")
            config = adapter._config({"config_path": str(root / "reproduction/configs/train_4b.json")})
            backend = CodexOpenRouterBackend.__new__(CodexOpenRouterBackend)
            backend.config = config.meta
            backend.bundle_manager = Mock()
            backend.bundle_manager.active.return_value.hash = "bundle"
            prepared = SimpleNamespace(
                directory=Path(directory),
                bundle=Mock(),
                request=SimpleNamespace(meta_harness_hash="bundle"),
            )
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "offline-fixture"}),
                patch("rsiflow.task_meta.meta_backends.local_execution.validate_local", return_value={"status": "fixture"}),
            ):
                backend.validate(prepared)
            prepared.bundle.verify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
