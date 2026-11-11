import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from prepare_report_evaluators import prepare
from rsiflow.task_meta.reporting import OfficialEvaluatorSpec


class ReportEvaluatorTests(unittest.TestCase):
    def test_qa_specs_use_frozen_native_gold_and_ordered_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = root / "records.jsonl"
            rows = [
                {"_id": "qa-1", "question": "q", "answer": "a", "context": [], "supporting_facts": {"title": ["T"], "sent_id": [0]}, "evidences": []},
            ]
            records.write_text(json.dumps(rows[0]) + "\n")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"tasks": [
                {"source": "hotpotqa_dev", "native_id": "qa-1", "record_file": str(records), "record_offset": 0, "record_length": len(json.dumps(rows[0]))},
            ]}))
            script = root / "evaluator.py"
            script.write_text("# official fixture\n")
            sha = lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            specs = root / "specs.json"
            base = {
                "repository": "https://example.invalid/official",
                "commit": "a" * 40,
                "entrypoint": str(script),
                "entrypoint_sha256": sha(script),
                "python_executable": "python",
                "data_path": "unused",
                "data_sha256": "unused",
                "task_ids_path": "unused",
                "task_ids_sha256": "unused",
                "protocol": "official_hotpot_v1",
                "timeout_seconds": 3600,
            }
            specs.write_text(json.dumps({"evaluators": {"hotpotqa_dev": base}}))
            output = root / "out.json"
            result = prepare(specs, manifest, root / "materialized", output)
            self.assertEqual(1, result["hotpotqa_dev"])
            configured = json.loads(output.read_text())["evaluators"]["hotpotqa_dev"]
            self.assertEqual(["qa-1"], OfficialEvaluatorSpec(**configured).validate())
            gold = json.loads(Path(configured["data_path"]).read_text())
            self.assertEqual([["T", 0]], gold[0]["supporting_facts"])


if __name__ == "__main__":
    unittest.main()
