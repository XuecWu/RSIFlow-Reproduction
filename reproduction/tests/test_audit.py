import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.audit_root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, name, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), "--audit-root", str(self.audit_root), *args],
            text=True,
            capture_output=True,
            check=False,
        )

    def operations(self):
        path = self.audit_root / "operations.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def make_evidence(self, name="evidence.txt"):
        path = self.audit_root / name
        path.write_text("verified\n", encoding="utf-8")
        return path

    def test_command_success_and_failure_are_recorded_with_sequence(self):
        success = self.run_script("audit_exec.py", "--actor", "test", "--phase", "unit", "--", "printf", "ok")
        self.assertEqual(0, success.returncode, success.stderr)
        failure = self.run_script("audit_exec.py", "--actor", "test", "--phase", "unit", "--", "false")
        self.assertEqual(1, failure.returncode)
        rows = self.operations()
        self.assertEqual([1, 2], [row["sequence"] for row in rows])
        self.assertEqual(["success", "failed"], [row["status"] for row in rows])
        self.assertEqual([0, 1], [row["exit_code"] for row in rows])
        self.assertTrue(all(row["evidence_paths"] for row in rows))
        self.assertTrue(all("evidence" in row["evidence_paths"][0] for row in rows))

    def test_non_utf8_output_is_recorded(self):
        result = self.run_script(
            "audit_exec.py", "--phase", "unit", "--",
            sys.executable, "-c", "import os; os.write(1, b'\\xff')",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        rows = self.operations()
        self.assertEqual(1, len(rows))
        self.assertEqual("success", rows[0]["status"])

    def test_timeout_is_recorded(self):
        result = self.run_script(
            "audit_exec.py", "--phase", "unit", "--timeout-seconds", "0.1", "--",
            sys.executable, "-c", "import time; time.sleep(2)",
        )
        self.assertEqual(124, result.returncode)
        rows = self.operations()
        self.assertEqual(1, len(rows))
        self.assertEqual("timeout", rows[0]["status"])
        self.assertEqual("TimeoutExpired", rows[0]["error_type"])

    def test_manual_operation_redacts_credentials(self):
        result = self.run_script(
            "record_operation.py",
            "--actor", "test",
            "--phase", "unit",
            "--tool-kind", "bash",
            "--action", "secret_check",
            "--input", "OPENAI_API_KEY=sk-test-secret cpolar authtoken secret-token",
            "--note", "--api-key option-secret and sk-note-secret",
            "--status", "failed",
            "--exit-code", "7",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        raw = (self.audit_root / "operations.jsonl").read_text()
        for secret in ("sk-test-secret", "secret-token", "option-secret", "sk-note-secret"):
            self.assertNotIn(secret, raw)
        self.assertIn("REDACTED", raw)

    def test_concurrent_operation_records_have_unique_sequences(self):
        processes = [
            subprocess.Popen(
                [sys.executable, str(SCRIPTS / "record_operation.py"), "--audit-root", str(self.audit_root),
                 "--phase", "unit", "--tool-kind", "bash", "--action", f"concurrent_{index}", "--input", "test"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for index in range(8)
        ]
        results = [process.communicate(timeout=40) + (process.returncode,) for process in processes]
        self.assertTrue(all(returncode == 0 for _, _, returncode in results), results)
        rows = self.operations()
        self.assertEqual(8, len(rows))
        self.assertEqual(list(range(1, 9)), sorted(row["sequence"] for row in rows))

    def test_result_requires_evidence_and_records_absolute_hash(self):
        missing = self.run_script(
            "record_result.py", "--phase", "unit", "--metric", "example", "--value", "1",
            "--verification-command", "test", "--evidence", "missing.txt",
        )
        self.assertNotEqual(0, missing.returncode)
        evidence = self.make_evidence()
        recorded = self.run_script(
            "record_result.py", "--phase", "unit", "--metric", "example", "--value", "1",
            "--verification-command", "test", "--evidence", str(evidence),
            "--review-status", "main_verified",
        )
        self.assertEqual(0, recorded.returncode, recorded.stderr)
        rows = [json.loads(line) for line in (self.audit_root / "key_results.jsonl").read_text().splitlines()]
        self.assertEqual(1, len(rows))
        self.assertEqual("main_verified", rows[0]["review_status"])
        self.assertEqual(64, len(rows[0]["evidence_sha256"][0]))
        self.assertTrue(Path(rows[0]["evidence_paths"][0]).is_absolute())

    def test_result_defaults_unreviewed_and_rejects_invalid_status(self):
        evidence = self.make_evidence()
        recorded = self.run_script(
            "record_result.py", "--phase", "unit", "--metric", "example", "--value", "unconfirmed",
            "--verification-command", "false", "--evidence", str(evidence),
        )
        self.assertEqual(0, recorded.returncode, recorded.stderr)
        rows = [json.loads(line) for line in (self.audit_root / "key_results.jsonl").read_text().splitlines()]
        self.assertEqual("unreviewed", rows[0]["review_status"])
        invalid = self.run_script(
            "record_result.py", "--phase", "unit", "--metric", "example", "--value", "bad",
            "--verification-command", "test", "--evidence", str(evidence),
            "--review-status", "not-a-schema-status",
        )
        self.assertNotEqual(0, invalid.returncode)


if __name__ == "__main__":
    unittest.main()
