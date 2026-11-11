import json
import tempfile
import unittest
from pathlib import Path

from build_cohort import inspect_source, normalize_deepcoder, normalize_rows, record_from_item


class BuildCohortTests(unittest.TestCase):
    def test_deepcoder_function_interface_and_entrypoint(self):
        row = {
            "problem": "add",
            "tests": json.dumps({"inputs": ["[1, 2]"], "outputs": ["3"]}),
            "metadata": json.dumps({"func_name": "add"}),
        }
        value = normalize_deepcoder(row, 0)
        self.assertEqual("function", value["interface"])
        self.assertEqual("add", value["tests"]["fn_name"])
        self.assertEqual("deepcoder-taco-0", value["id"])
        self.assertEqual([], value["public_tests"])

    def test_deepcoder_stdio_interface(self):
        row = {"problem": "echo", "tests": json.dumps({"inputs": ["1\n"], "outputs": ["1\n"]})}
        self.assertEqual("stdio", normalize_deepcoder(row, 4)["interface"])

    def test_envscaler_checklist_is_required(self):
        with self.assertRaises(ValueError):
            normalize_rows("envscaler", [{"task": "do something"}])
        rows = normalize_rows("envscaler", [{"task": "do something", "checklist_with_func": [{"check_func": "def check(final): return True"}]}])
        self.assertEqual(1, len(rows))

    def test_record_uses_exact_jsonl_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            payload = {"task_id": "x", "question": "Where?", "context": {"title": ["A"], "sentences": [["There"]]}, "answer": "There"}
            path.write_text(json.dumps(payload) + "\n")
            raw = path.read_bytes()
            item = {
                "task_id": "hotpotqa:x",
                "domain": "searchqa",
                "source": "hotpotqa",
                "path": str(path),
                "offset": 0,
                "length": len(raw),
                "source_sha256": "source",
                "content_hash": "content",
            }
            record = record_from_item(item)
            self.assertEqual("Where?", record.prompt)
            self.assertEqual(payload, record.payload)

    def test_inspection_reports_interfaces(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "code.jsonl"
            rows = [
                {"problem": "a", "tests": json.dumps({"inputs": [], "outputs": []}), "metadata": {"func_name": "a"}},
                {"problem": "b", "tests": json.dumps({"inputs": [], "outputs": []})},
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            report = inspect_source(path)
            self.assertEqual(2, report["records"])
            self.assertEqual({"function": 1, "stdio": 1}, report["interfaces"])


if __name__ == "__main__":
    unittest.main()
