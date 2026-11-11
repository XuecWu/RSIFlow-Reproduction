import json
import tempfile
import unittest
from pathlib import Path

from build_cohort import ROOT, deduplicate_native_ids, inspect_source, normalize_deepcoder, normalize_rows, pack_allocated_manifests, read_rows, record_from_item
from data_protocol import native_index, text_hash
from evolution_protocol import file_hash, make_manifest
from prepare_600_744 import repack


class BuildCohortTests(unittest.TestCase):
    def test_repository_root_contains_rsiflow(self):
        self.assertTrue((ROOT / "RSIFlow").is_dir())

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

    def test_qa_rows_expose_official_underscore_id(self):
        rows = normalize_rows("hotpotqa", [{"id": "native-1", "question": "q", "context": {"title": [], "sentences": []}}])
        self.assertEqual("native-1", rows[0]["_id"])
        self.assertEqual("native-1", rows[0]["id"])

    def test_hotpot_dev_rows_expose_official_underscore_id(self):
        rows = normalize_rows("hotpotqa_dev", [{"id": "dev-1", "question": "q", "context": {"title": [], "sentences": []}}])
        self.assertEqual("dev-1", rows[0]["_id"])

    def test_duplicate_native_ids_keep_only_first_variant(self):
        rows, dropped = deduplicate_native_ids("bfcl_v3", [
            {"id": "same", "question": "first"},
            {"id": "same", "question": "second"},
            {"id": "other", "question": "third"},
        ])
        self.assertEqual(2, len(rows))
        self.assertEqual("first", rows[0]["question"])
        self.assertEqual(1, len(dropped))
        self.assertEqual("second", dropped[0]["question"])

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

    def test_inspection_accepts_dataset_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.jsonl").write_text(json.dumps({"question": "a"}) + "\n")
            (root / "b.jsonl").write_text(json.dumps({"question": "b"}) + "\n")
            report = inspect_source(root)
            self.assertEqual(2, report["records"])
            self.assertEqual(2, len(report["files"]))

    def test_json_suffix_supports_jsonl_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "BFCL_v3_simple.json"
            path.write_text(json.dumps({"id": "a"}) + "\n" + json.dumps({"id": "b"}) + "\n")
            self.assertEqual(2, len(list(read_rows(path))))

    def test_bfcl_reader_excludes_possible_answers_and_function_docs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "bfcl_v3"
            (root / "possible_answer").mkdir(parents=True)
            (root / "multi_turn_func_doc").mkdir()
            (root / "BFCL_v3_simple.json").write_text(json.dumps([{"id": "task", "question": "q"}]))
            (root / "possible_answer" / "BFCL_v3_simple.json").write_text(json.dumps([{"id": "task", "question": "q"}]))
            (root / "multi_turn_func_doc" / "travel.json").write_text(json.dumps([{"id": "doc"}]))
            rows = list(read_rows(root))
            self.assertEqual(1, len(rows))
            self.assertEqual("task", rows[0]["id"])

    def test_allocated_packs_feed_repack_with_record_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            payload = {"question": "Where?", "context": {"title": ["A"], "sentences": [["There"]]}, "answer": "There"}
            source.write_text(json.dumps(payload) + "\n")
            raw = source.read_bytes()
            item = {
                "source": "hotpotqa", "domain": "searchqa", "kind": "train", "original_split": "train",
                "version": "test", "source_sha256": file_hash(source), "task_id": "hotpotqa:x", "native_id": "x",
                "content_hash": text_hash(payload["question"]), "strata": {}, "path": str(source),
                "offset": 0, "length": len(raw), "feedback_priority": False, "date": None,
                "group_id": "group", "role": "train_evolution", "purpose": "evolution_train", "round_id": 1,
            }
            training = [make_manifest("train_evolution", number, [{**item, "round_id": number}]) for number in range(1, 6)]
            validation = make_manifest("independent_validation", None, [{**item, "role": "independent_validation", "purpose": "report_only", "round_id": None}])
            source_training, source_validation = pack_allocated_manifests(training, validation, root / "allocated")
            self.assertEqual(5, len(source_training["tasks"]))
            for row in source_training["tasks"] + source_validation["tasks"]:
                self.assertIn("record_file", row)
                self.assertIn("record_offset", row)
                self.assertIn("record_length", row)
                self.assertIn("record_sha256", row)
                self.assertNotIn("path", row)
            repack(source_validation, root / "validation-repack")

    def test_native_index_links_expected_retrieval_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.jsonl"
            payload = {"question": "Where?", "context": {"title": ["A"], "sentences": [["There"]]}, "answer": "There"}
            source.write_text(json.dumps(payload) + "\n")
            raw = source.read_bytes()
            item = {
                "source": "hotpotqa", "domain": "searchqa", "kind": "train", "original_split": "train",
                "version": "test", "source_sha256": file_hash(source), "task_id": "hotpotqa:x", "native_id": "x",
                "content_hash": text_hash(payload["question"]), "strata": {}, "path": str(source),
                "offset": 0, "length": len(raw), "feedback_priority": False, "date": None,
                "group_id": "group", "role": "train_evolution", "purpose": "evolution_train", "round_id": 1,
            }
            training = [make_manifest("train_evolution", number, [{**item, "task_id": f"hotpotqa:x{number}", "native_id": f"x{number}", "round_id": number}]) for number in range(1, 6)]
            validation = make_manifest("independent_validation", None, [{**item, "role": "independent_validation", "purpose": "report_only", "round_id": None}])
            source_training, _ = pack_allocated_manifests(training, validation, root / "allocated")
            from rsiflow.task_meta.retrieval import build_search_index
            tasks = [record_from_item(row) for manifest in training for row in manifest["tasks"]]
            retrieval = root / "search.sqlite"
            build_search_index(tasks, retrieval, data_manifest_hash=source_training["manifest_hash"])
            output = root / "native"
            output.mkdir()
            config = {"runtime_source": str(Path(__file__).resolve().parents[2] / "RSIFlow/runtime"), "retrieval_index": str(retrieval)}
            native_index(output, [source_training], config, link_retrieval=True)
            self.assertTrue((output / "search.sqlite.manifest.json").is_file())
            self.assertTrue((output / "search.sqlite").is_symlink())


if __name__ == "__main__":
    unittest.main()
