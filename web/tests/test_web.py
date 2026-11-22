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
        self.assertIn("本次未运行演化实验", visible_text)
        self.assertIn("744 冻结 / 694 计划评分", visible_text)
        self.assertIn("复现准备中", visible_text)
        self.assertIn("RSI-Flow", visible_text)
        self.assertIn("ACE 用户模拟凭据需单独批准", visible_text)
        self.assertNotIn("DeepSeek / 用户模拟凭据", visible_text)

    def test_process_page_exposes_audit_visualization(self):
        page = read_text("process.html")
        for expected in ("process-summary", "process-timeline", "process-results", "process.js"):
            self.assertIn(expected, page)
        process = read_json("data/process.json")
        self.assertGreaterEqual(process.get("operations_total", 0), 1)
        self.assertIn("success", process.get("operation_counts", {}))
        self.assertIn("timeline", process)
        self.assertGreaterEqual(process.get("as_of_sequence", 0), process["operations_total"])
        self.assertTrue(process.get("is_snapshot"))
        self.assertIn("sequence", read_text("process.js"))

    def test_process_snapshot_includes_completed_five_round(self):
        process = read_json("data/process.json")
        self.assertGreaterEqual(process.get("as_of_sequence", 0), 1443)
        self.assertIn("deepcoder-five-round-final", {row.get("phase") for row in process["timeline"]})
        self.assertIn("00001444-final_delivery_complete", {row["result_id"] for row in process["key_results"]})
        results = {row["metric"]: row["value"] for row in process["key_results"]}
        expected_fragments = {
            "B1_ARTIFACTS_direct_receipts": ("parent 2/16", "candidate 3/16", "new_Qwen_calls=0"),
            "B2_model_retain_parent_raw_receipts": ("parent=4/16 candidate=3/16", "retain_parent", "no activation"),
            "B3_artifacts_accept_raw_receipts": ("2/16 to 3/16", "accept_candidate and activated"),
            "B4_artifacts_accept_raw_receipts": ("2/16 to 3/16", "accept_candidate/activated"),
            "five_round_engineering_complete": ("completed_rounds=5", "run_complete=true", "B5 4→5 accept ARTIFACTS"),
            "final_full_suite_and_delivery_gate": ("1031 passed, 2 skipped", "Ready Yes"),
        }
        for metric, fragments in expected_fragments.items():
            self.assertIn(metric, results)
            for fragment in fragments:
                self.assertIn(fragment, results[metric])
        run = process["runs"][-1]
        self.assertEqual("deepcoder_function_16_five_round_workspace_v1", run["run_id"])
        self.assertEqual("run_complete", run["status"])
        self.assertEqual(5, run["completed_rounds"])
        self.assertEqual(16, run["unique_tasks"])
        self.assertEqual(2, run["initial_successes"])
        self.assertEqual(5, run["final_successes"])

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
        by_id = {stage["id"]: stage for stage in stages}
        for identifier in ("conda_environment", "external_assets", "offline_tests"):
            self.assertEqual("completed", by_id[identifier]["status"])
            self.assertIsNotNone(by_id[identifier]["evidence"])
        self.assertEqual("297 passed, 2 skipped", by_id["offline_tests"]["test_counts"])
        self.assertEqual("156 passed", by_id["offline_tests"]["focused_counts"])
        self.assertEqual("current_694_launch_check_passed_other_gates_remain", by_id["launch_preflight"]["status"])
        self.assertEqual("reproduction/audit/daily/2026-11-16.md", by_id["launch_preflight"]["evidence"])
        self.assertEqual("legacy_landlock_unavailable_bwrap_validated", by_id["sco_gpu_probe"]["status"])
        self.assertEqual("completed_gpu_diagnostic_only", by_id["model_service_smoke"]["status"])
        self.assertEqual("completed_real_codex_bwrap_resume_smoke", by_id["meta_api_smoke"]["status"])
        self.assertEqual("partial_minimum_infrastructure_smokes_not_scores", by_id["benchmark_smoke"]["status"])
        self.assertFalse(by_id["benchmark_smoke"]["model_benchmark_scores"])
        frozen = by_id["frozen_data"]
        self.assertEqual("completed_protocol_reconstruction_with_caveats", frozen["status"])
        self.assertIn("v4", frozen["evidence"])
        self.assertFalse(frozen["clean_independence_audited"])
        self.assertEqual("exposure_unknown", frozen["exposure"])
        self.assertEqual("694_of_744_ace_pending_audit", by_id["evaluate_4b_694"]["evaluation_scope"])
        five = by_id["run_4b_five_rounds"]
        self.assertEqual("completed_single_domain_16_task_engineering_canary", five["status"])
        self.assertTrue(five["run_complete"])
        self.assertEqual(16, five["unique_tasks"])
        self.assertEqual(80, five["rollout_positions"])
        self.assertEqual("running_tmux_after_user_authorized_direct_start", five["occupier"])
        for identifier in ("evaluate_4b_694", "run_8b_five_rounds", "evaluate_8b_694"):
            self.assertEqual("not_run_pending_approval", by_id[identifier]["status"])
        rounds = self.reproduction["deepcoder_five_round"]["rounds"]
        self.assertEqual([2, 4, 2, 2, 4], [r["parent_successes"] for r in rounds])
        self.assertEqual([3, 3, 3, 3, 5], [r["candidate_successes"] for r in rounds])
        self.assertEqual("retain_parent", rounds[1]["decision"])
        self.assertFalse(rounds[1]["activated"])
        self.assertEqual(20, self.reproduction["deepcoder_five_round"]["model_sft"]["sampled_training_examples"])
        self.assertIn("主分区 8×A800", self.reproduction["current_execution"]["gpu_profile_display"])
        self.assertEqual("main_partition_8xA800_sequential_gpu_0_to_3", self.reproduction["current_execution"]["gpu_profile"])
        meta = self.reproduction["bwrap_isolation"]["persistent_meta"]
        self.assertEqual("huangsiyuan1", meta["submitted_by"])
        self.assertEqual("8xA100", meta["resource"])
        self.assertTrue(meta["root_mapping"])
        self.assertFalse(meta["a800_pass"])
        self.assertIn("not_executed", self.reproduction["bwrap_isolation"]["native_shell_descendant_hygiene"])
        self.assertEqual("passed_ready_to_merge", self.reproduction["compatibility_fixes"]["landlock"]["independent_security_review"])
        self.assertIn("completed_on_313b6bc", self.reproduction["bwrap_isolation"]["affected_path_sco_reruns"])
        self.assertIn("installer_receipt", self.reproduction["bwrap_isolation"]["trusted_security_assets"])
        gates = self.reproduction["bwrap_isolation"]["final_a100_gates"]
        self.assertEqual("313b6bcc00ee641ce99eee04fb9e4490dbb63929", gates["commit"])
        self.assertFalse(gates["root_mapping"])
        combined = gates["combined_job"]
        self.assertEqual("FAILED", combined["terminal"])
        self.assertEqual(0, combined["candidate"]["exit_code"])
        self.assertEqual(0, combined["harness"]["exit_code"])
        self.assertEqual(0, combined["bfcl"]["exit_code"])
        self.assertEqual(1, combined["meta_attempt"]["exit_code"])
        retry = gates["meta_retry"]
        self.assertEqual("pt-x6b4ista", retry["job_id"])
        self.assertEqual("SUCCEEDED", retry["terminal"])
        self.assertEqual("Succeeded", retry["worker"])
        self.assertEqual("0644", retry["installation_id_mode"])
        self.assertTrue(retry["run_owner_exact"])
        self.assertIn("not_rerun_on_313b6bc", gates["evalplus"])

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
