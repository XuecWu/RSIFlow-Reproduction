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
        self.temp = tempfile.TemporaryDirectory()
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

    def test_manual_operation_redacts_credentials(self):
        result = self.run_script(
            "record_operation.py",
            "--actor", "test",
            "--phase", "unit",
            "--tool-kind", "bash",
            "--action", "secret_check",
            "--input", "OPENAI_API_KEY=sk-test-secret cpolar authtoken secret-token",
            "--status", "failed",
            "--exit-code", "7",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        raw = (self.audit_root / "operations.jsonl").read_text()
        self.assertNotIn("sk-test-secret", raw)
        self.assertNotIn("secret-token", raw)
        self.assertIn("REDACTED", raw)

    def test_result_requires_evidence_and_records_hash(self):
        missing = self.run_script(
            "record_result.py", "--phase", "unit", "--metric", "example", "--value", "1",
            "--verification-command", "test", "--evidence", "missing.txt",
        )
        self.assertNotEqual(0, missing.returncode)
        evidence = self.audit_root / "evidence.txt"
        evidence.write_text("verified\n", encoding="utf-8")
        recorded = self.run_script(
            "record_result.py", "--phase", "unit", "--metric", "example", "--value", "1",
            "--verification-command", "test", "--evidence", str(evidence),
        )
        self.assertEqual(0, recorded.returncode, recorded.stderr)
        rows = [json.loads(line) for line in (self.audit_root / "key_results.jsonl").read_text().splitlines()]
        self.assertEqual(1, len(rows))
        self.assertEqual("main_verified", rows[0]["review_status"])
        self.assertEqual(64, len(rows[0]["evidence_sha256"][0]))


if __name__ == "__main__":
    unittest.main()
