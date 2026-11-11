import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


WEB = Path(__file__).resolve().parents[1]


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.references = []
        self.external_assets = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        for attribute in ("href", "src"):
            value = values.get(attribute)
            if not value:
                continue
            self.references.append((tag, attribute, value))
            if tag in {"script", "link"} and value.startswith(("http://", "https://", "//")):
                self.external_assets.append(value)

    def handle_data(self, data):
        self.text.append(data)


def read_text(relative):
    path = WEB / relative
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def read_json(relative):
    text = read_text(relative)
    return json.loads(text) if text else {}


class WebsiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = PageParser()
        cls.page.feed(read_text("index.html"))
        cls.repository = read_json("data/repository.json")
        cls.reproduction = read_json("data/reproduction.json")

    def test_required_deliverables_exist(self):
        required = {
            "index.html",
            "styles.css",
            "app.js",
            "README.md",
            "RESULTS.md",
            "REPRODUCTION.md",
            "ENVIRONMENT.md",
            "ASSETS.md",
            "OPERATIONS.md",
            "data/repository.json",
            "data/reproduction.json",
        }
        missing = sorted(path for path in required if not (WEB / path).is_file())
        self.assertEqual([], missing)

    def test_page_exposes_research_sections_and_execution_boundary(self):
        expected = {
            "overview",
            "architecture",
            "workflow",
            "benchmarks",
            "resources",
            "reproduction",
            "verification",
        }
        self.assertTrue(expected <= self.page.ids)
        visible_text = " ".join(self.page.text)
        self.assertIn("本次未运行实验", visible_text)
        self.assertIn("RSI-Flow", visible_text)

    def test_process_page_exposes_audit_visualization(self):
        page = read_text("process.html")
        for expected in ("process-summary", "process-timeline", "process-results", "process.js"):
            self.assertIn(expected, page)
        process = read_json("data/process.json")
        self.assertGreaterEqual(process.get("operations_total", 0), 1)
        self.assertIn("success", process.get("operation_counts", {}))
        self.assertIn("timeline", process)

    def test_protocol_and_gpu_facts_match_source(self):
        protocol = self.repository.get("protocol", {})
        self.assertEqual(5, protocol.get("rounds"))
        self.assertEqual(600, protocol.get("training_tasks_per_round"))
        self.assertEqual(3000, protocol.get("allocated_tasks"))
        self.assertEqual(744, protocol.get("validation_tasks"))
        self.assertEqual(
            ["Qwen3-4B", "Qwen3-8B"],
            [item.get("name") for item in self.repository.get("backbones", [])],
        )
        gpu = self.repository.get("gpu", {})
        self.assertEqual(4, gpu.get("required_count"))
        self.assertEqual(20000, gpu.get("inference_min_free_mib"))
        self.assertEqual(40000, gpu.get("sft_min_free_mib"))

    def test_effective_sft_records_config_override(self):
        sft = self.repository.get("effective_sft", {})
        self.assertEqual(8, sft.get("lora_rank"))
        self.assertEqual(2e-6, sft.get("learning_rate"))
        self.assertEqual(16384, sft.get("max_length"))
        self.assertEqual(4, sft.get("ddp_ranks"))

    def test_experiment_status_tracks_real_evidence(self):
        stages = self.reproduction.get("experiment_stages", [])
        self.assertGreaterEqual(len(stages), 13)
        completed = {"conda_environment", "external_assets", "offline_tests", "launch_preflight", "frozen_data"}
        self.assertEqual("queued", next(stage for stage in stages if stage["id"] == "sco_gpu_probe")["status"])
        self.assertEqual("blocked_missing_credentials", next(stage for stage in stages if stage["id"] == "meta_api_smoke")["status"])
        for stage in stages:
            if stage["id"] in completed:
                expected_status = "completed_with_landlock_limitation" if stage["id"] == "offline_tests" else "completed"
                self.assertEqual(expected_status, stage["status"])
                self.assertIsNotNone(stage["evidence"])
            else:
                self.assertIn(stage["status"], {"not_run_pending_approval", "queued", "blocked_missing_credentials"})

    def test_local_assets_documents_and_anchors_resolve(self):
        missing = []
        for tag, attribute, reference in self.page.references:
            if reference.startswith("#"):
                if reference[1:] not in self.page.ids:
                    missing.append(reference)
                continue
            parsed = urlsplit(reference)
            if parsed.scheme or reference.startswith(("//", "mailto:", "data:")):
                continue
            target = unquote(parsed.path)
            if not target:
                continue
            if not (WEB / target).is_file():
                missing.append(reference)
        self.assertEqual([], missing)

    def test_page_has_no_third_party_asset_dependency(self):
        self.assertEqual([], self.page.external_assets)

    def test_reproduction_guide_covers_decision_path(self):
        guide = read_text("REPRODUCTION.md")
        for expected in (
            "conda create -n rsiflow python=3.11",
            "N3lS.Ii.I60.4",
            "N3lS.Ii.I60.8",
            "--check",
            "api-smoke",
            "complete.json",
            "CUDA_VISIBLE_DEVICES",
            "source manifests",
        ):
            self.assertIn(expected, guide)

    def test_raw_runtime_logs_and_client_ids_are_not_public(self):
        self.assertFalse((WEB / "logs").exists(), "raw runtime logs must live outside the public web root")
        for path in WEB.rglob("*"):
            if not path.is_file() or path.suffix not in {".html", ".css", ".js", ".json", ".md", ".log", ".txt", ".yml", ".yaml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("client id:", text, f"cpolar client metadata leaked in {path.relative_to(WEB)}")

    def test_public_text_has_no_assigned_credentials(self):
        patterns = [
            re.compile(r"(?i)(?:api[_-]?key|access[_-]?key[_-]?secret|authtoken)\s*[=:]\s*['\"](?!<|your|example|xxx)[^'\"\s]{8,}"),
            re.compile(r"(?i)cpolar\s+authtoken\s+(?!<|your|example|xxx)\S+"),
            re.compile(r"(?i)https?://\S*(?:subscription|subscribe|access_token=)\S*"),
        ]
        findings = []
        for path in WEB.rglob("*"):
            if not path.is_file() or path.suffix not in {".html", ".css", ".js", ".json", ".md", ".log", ".txt", ".yml", ".yaml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in patterns:
                if pattern.search(text):
                    findings.append(str(path.relative_to(WEB)))
        self.assertEqual([], sorted(set(findings)))


if __name__ == "__main__":
    unittest.main()
