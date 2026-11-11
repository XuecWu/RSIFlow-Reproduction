import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from validation_skip import ace_skip_summary, evaluation_scope, reject_complete_cache_for_skip
from rsiflow.task_meta.round_validation import validate_round
from rsiflow.task_meta.storage import digest
from rsiflow.task_meta.types import TaskAgentState


class SkipAceProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.out = Path(self.temp.name)
        (self.out / "task_metrics.json").write_text(json.dumps({"overall": {"complete": False}}))

    def tearDown(self):
        self.temp.cleanup()

    def manifest(self, counts):
        return {
            "tasks": [
                {"source": source, "task_id": f"{source}-{index}"}
                for source, count in counts.items()
                for index in range(count)
            ]
        }

    def test_744_partial_uses_manifest_counts(self):
        settings = {"benchmark_ids": ["bfcl_v3", "acebench", "humaneval_plus", "mbpp_plus", "hotpotqa_dev", "2wiki_dev"]}
        manifest = self.manifest({"bfcl_v3": 152, "acebench": 50, "humaneval_plus": 164, "mbpp_plus": 178, "hotpotqa_dev": 100, "2wiki_dev": 100})
        results = [{"benchmark_id": name} for name in ["bfcl_v3", "humaneval_plus", "mbpp_plus", "hotpotqa_dev", "2wiki_dev"]]
        metrics = {"overall": {"n_scored": 694, "complete": False}}
        partial = ace_skip_summary(settings, manifest, results, metrics, self.out)
        self.assertEqual("code_search_completed_ace_pending", partial["status"])
        self.assertEqual(694, partial["officially_scored"])
        self.assertEqual(744, partial["expected_total"])
        self.assertEqual(results and [row["benchmark_id"] for row in results], partial["benchmarks_completed"])
        self.assertEqual(64, len(partial["metrics_sha256"]))
        self.assertFalse(partial["feedback_to_meta"])

    def test_frozen_receipt_distinguishes_total_from_scored(self):
        manifest = self.manifest({"bfcl_v3": 152, "acebench": 50, "humaneval_plus": 164, "mbpp_plus": 178, "hotpotqa_dev": 100, "2wiki_dev": 100})
        self.assertEqual({
            "status": "partial_ace_pending_audit",
            "skip_acebench": True,
            "frozen_validation_tasks": 744,
            "scored_validation_tasks": 694,
            "pending_validation_tasks": 50,
        }, evaluation_scope({"skip_acebench": True}, manifest))
        self.assertEqual({
            "status": "complete_protocol_pending_execution",
            "skip_acebench": False,
            "frozen_validation_tasks": 744,
            "scored_validation_tasks": 744,
            "pending_validation_tasks": 0,
        }, evaluation_scope({"skip_acebench": False}, manifest))

    def test_legacy_300_partial_remains_supported(self):
        settings = {"benchmark_ids": ["bfcl_v3", "acebench", "livecodebench", "humaneval_plus", "mbpp_plus", "hotpotqa_dev", "2wiki_dev"]}
        manifest = self.manifest({"bfcl_v3": 50, "acebench": 50, "livecodebench": 50, "humaneval_plus": 25, "mbpp_plus": 25, "hotpotqa_dev": 50, "2wiki_dev": 50})
        results = [{"benchmark_id": name} for name in ["bfcl_v3", "livecodebench", "humaneval_plus", "mbpp_plus", "hotpotqa_dev", "2wiki_dev"]]
        metrics = {"overall": {"n_scored": 250, "complete": False}}
        partial = ace_skip_summary(settings, manifest, results, metrics, self.out)
        self.assertEqual(250, partial["officially_scored"])
        self.assertEqual(300, partial["expected_total"])

    def test_missing_metrics_file_is_rejected(self):
        (self.out / "task_metrics.json").unlink()
        settings = {"benchmark_ids": ["acebench", "bfcl_v3"]}
        manifest = self.manifest({"acebench": 50, "bfcl_v3": 152})
        results = [{"benchmark_id": "bfcl_v3"}]
        with self.assertRaises(FileNotFoundError):
            ace_skip_summary(settings, manifest, results, {"overall": {"n_scored": 152, "complete": False}}, self.out)

    def test_incomplete_or_unexpected_ace_result_is_rejected(self):
        settings = {"benchmark_ids": ["acebench", "bfcl_v3"]}
        manifest = self.manifest({"acebench": 50, "bfcl_v3": 152})
        results = [{"benchmark_id": "bfcl_v3"}]
        with self.assertRaises(RuntimeError):
            ace_skip_summary(settings, manifest, results, {"overall": {"n_scored": 1, "complete": False}}, self.out)
        (self.out / "scores" / "acebench").mkdir(parents=True)
        (self.out / "scores" / "acebench" / "result.json").write_text(json.dumps({"status": "completed"}))
        with self.assertRaises(RuntimeError):
            ace_skip_summary(settings, manifest, results, {"overall": {"n_scored": 152, "complete": False}}, self.out)

    def test_complete_cache_conflicts_with_skip_mode(self):
        (self.out / "complete.json").write_text(json.dumps({"status": "completed"}))
        with self.assertRaises(ValueError):
            reject_complete_cache_for_skip(self.out, True)
        reject_complete_cache_for_skip(self.out, False)

    def test_round_validation_accepts_744_partial_receipt(self):
        settings = {"benchmark_ids": ["bfcl_v3", "acebench", "humaneval_plus", "mbpp_plus", "hotpotqa_dev", "2wiki_dev"]}
        manifest = self.manifest({"bfcl_v3": 152, "acebench": 50, "humaneval_plus": 164, "mbpp_plus": 178, "hotpotqa_dev": 100, "2wiki_dev": 100})
        settings_path = self.out / "validation.json"
        settings_path.write_text(json.dumps(settings))
        manifest_path = self.out / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        run_dir = self.out / "run-4b"
        run_dir.mkdir()
        harness = self.out / "harness.md"
        harness.write_text("seed")
        task = TaskAgentState(0, "model", str(harness), checkpoint_path="model")
        state_hash = __import__("rsiflow.task_meta.durable", fromlist=["task_hash"]).task_hash(task)
        validation_out = self.out / "validation" / run_dir.name / "round_00"
        validation_out.mkdir(parents=True)
        benchmark_ids = ["bfcl_v3", "humaneval_plus", "mbpp_plus", "hotpotqa_dev", "2wiki_dev"]
        partial = {
            "status": "code_search_completed_ace_pending",
            "source_role": "independent_validation",
            "feedback_to_meta": False,
            "task_state_hash": state_hash,
            "benchmarks_completed": benchmark_ids,
            "officially_scored": 694,
            "expected_total": 744,
        }
        metrics_path = validation_out / "task_metrics.json"
        metrics_path.write_text(json.dumps({"overall": {"n_scored": 694, "complete": False}}))
        partial["metrics_sha256"] = digest(metrics_path)
        (validation_out / "code_search_partial_complete.json").write_text(json.dumps(partial))
        for name in benchmark_ids:
            score_dir = validation_out / "scores" / name
            score_dir.mkdir(parents=True)
            (score_dir / "result.json").write_text(json.dumps({
                "status": "completed", "state_hash": state_hash, "feedback_to_meta": False,
            }))
        config = SimpleNamespace(
            round_validation_config=str(settings_path),
            round_protocol={"skip_acebench": True, "validation_manifest": str(manifest_path)},
            output_root=str(self.out),
            model_dump=lambda: {},
        )
        record = {
            "task_after": asdict(task),
            "meta_after": {"harness_path": "meta", "version": 0},
            "status": "committed",
            "chosen_component": None,
        }
        validate_round(config, run_dir, -1, record)


if __name__ == "__main__":
    unittest.main()
