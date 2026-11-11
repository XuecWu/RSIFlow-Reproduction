import json
import tempfile
import unittest
from pathlib import Path

from build_status import build_status


class BuildStatusTests(unittest.TestCase):
    def test_counts_timeline_and_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operations = [
                {"sequence": 1, "phase": "setup", "action": "clone", "status": "success"},
                {"sequence": 2, "phase": "assets", "action": "download", "status": "failed"},
                {"sequence": 3, "phase": "probe", "action": "wait", "status": "timeout"},
            ]
            (root / "operations.jsonl").write_text("".join(json.dumps(row) + "\n" for row in operations))
            (root / "key_results.jsonl").write_text(json.dumps({"metric": "tests", "value": "10/10", "review_status": "main_verified"}) + "\n")
            (root / "runs.json").write_text(json.dumps({"runs": [{"run_id": "run-4b", "status": "probe_queued"}]}))
            result = build_status(root)
            self.assertEqual({"success": 1, "failed": 1, "timeout": 1}, result["operation_counts"])
            self.assertEqual("probe", result["current_phase"])
            self.assertEqual(3, len(result["timeline"]))
            self.assertEqual("run-4b", result["runs"][-1]["run_id"])
            self.assertEqual("10/10", result["key_results"][0]["value"])
            self.assertEqual(3, result["as_of_sequence"])
            self.assertTrue(result["is_snapshot"])
            self.assertIn("截至 sequence", result["snapshot_note"])

    def test_legacy_timeline_uses_historical_evidence_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            operations = [{"sequence": 9, "phase": "setup", "action": "read", "status": "success", "evidence_paths": ["old.log"]}]
            (root / "operations.jsonl").write_text(json.dumps(operations[0]) + "\n")
            (root / "evidence-hashes.json").write_text(json.dumps({"operations": [
                {"sequence": 9, "evidence": [{"path": "old.log", "sha256": "a" * 64}]},
            ]}))
            result = build_status(root)
            self.assertEqual(["a" * 64], result["timeline"][0]["evidence_sha256"])

    def test_invalid_jsonl_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "operations.jsonl").write_text("not-json\n")
            with self.assertRaises(ValueError):
                build_status(root)


if __name__ == "__main__":
    unittest.main()
