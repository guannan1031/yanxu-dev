import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yanxu.core import ReviewError
from yanxu.implement import run_implementation
from yanxu.policy import create_policy
from yanxu.task import build_contract


PATCH = """diff --git a/sample/value.py b/sample/value.py
--- a/sample/value.py
+++ b/sample/value.py
@@ -1,2 +1,2 @@
 def value():
-    return 0
+    return 1
"""

MALFORMED_PATCH = PATCH.replace("@@ -1,2 +1,2 @@", "@@ -1,2 +1,4 @@")


class ControlledImplementationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "sample").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "README.md").write_bytes(b"# Demo\n")
        (self.repo / "sample/value.py").write_bytes(b"def value():\n    return 0\n")
        (self.repo / "sample/other.py").write_bytes(b"OTHER = True\n")
        (self.repo / "tests/test_value.py").write_bytes(
            b"import unittest\nfrom sample.value import value\n\nclass ValueTests(unittest.TestCase):\n"
            b"    def test_value(self):\n        self.assertEqual(value(), 1)\n")
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
        self.contract = build_contract("Make value return 1", self.repo)

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def model(self, proposal=PATCH):
        return {"status": "completed", "executor": "codex-test", "model": "fixture", "elapsed_seconds": 0.1,
                "usage": None, "cost_money": None,
                "answer": {"summary": "Return the expected value.", "suggested_patch": proposal, "limitations": []}}

    def execute(self, proposal=PATCH, allow=None, command=None):
        with patch("yanxu.implement.generate_patch", return_value=self.model(proposal)):
            return run_implementation(
                self.contract, self.repo, allow or ["sample/value.py"],
                command or [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                self.root / "runs", timeout=10,
            )

    def test_generated_patch_is_tested_and_original_is_unchanged(self):
        before = (self.repo / "sample/value.py").read_text()
        result = self.execute()
        self.assertEqual(result["status"], "READY_FOR_HUMAN_REVIEW")
        self.assertTrue(result["ready_for_human_review"])
        self.assertEqual(result["tests"], "PASSED")
        self.assertEqual((self.repo / "sample/value.py").read_text(), before)
        self.assertFalse(result["remote_modified"])
        self.assertTrue(Path(result["folder"], "report.html").exists())

    def test_context_drift_stops_before_model(self):
        (self.repo / "README.md").write_text("# Changed\n", encoding="utf-8")
        with patch("yanxu.implement.generate_patch", side_effect=AssertionError("model called")):
            with self.assertRaisesRegex(ReviewError, "drifted"):
                run_implementation(self.contract, self.repo, ["sample/value.py"],
                                   [sys.executable, "-m", "unittest"], self.root / "runs")

    def test_patch_outside_allowlist_is_rejected(self):
        outside = """diff --git a/sample/other.py b/sample/other.py
--- a/sample/other.py
+++ b/sample/other.py
@@ -1 +1 @@
-OTHER = True
+OTHER = False
"""
        with self.assertRaisesRegex(ReviewError, "outside"):
            self.execute(outside)

    def test_invalid_first_model_patch_is_retried_once(self):
        answers = [self.model(""), self.model(PATCH)]
        with patch("yanxu.implement.generate_patch", side_effect=answers) as mocked:
            result = run_implementation(
                self.contract, self.repo, ["sample/value.py"],
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                self.root / "runs", timeout=10,
            )
        self.assertEqual(result["status"], "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(result["model"]["attempt_count"], 2)
        self.assertEqual(len(result["model"]["rejected_attempts"]), 1)
        self.assertEqual(mocked.call_count, 2)

    def test_malformed_hunk_is_retried_before_isolated_tests(self):
        answers = [self.model(MALFORMED_PATCH), self.model(PATCH)]
        with patch("yanxu.implement.generate_patch", side_effect=answers) as mocked:
            result = run_implementation(
                self.contract, self.repo, ["sample/value.py"],
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                self.root / "runs", timeout=10,
            )
        self.assertEqual(result["status"], "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(result["model"]["attempt_count"], 2)
        self.assertIn("malformed", result["model"]["rejected_attempts"][0])
        self.assertEqual(mocked.call_count, 2)

    def test_two_malformed_hunks_stop_without_running_tests(self):
        with patch("yanxu.implement.generate_patch", return_value=self.model(MALFORMED_PATCH)) as mocked:
            with self.assertRaisesRegex(ReviewError, "after 2 attempts"):
                run_implementation(
                    self.contract, self.repo, ["sample/value.py"],
                    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                    self.root / "runs", timeout=10,
                )
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual((self.repo / "sample/value.py").read_text(), "def value():\n    return 0\n")
        self.assertFalse((self.root / "runs").exists())

    def test_failed_tests_are_recorded(self):
        result = self.execute(command=[sys.executable, "-c", "raise SystemExit(3)"])
        self.assertEqual(result["status"], "FAILED_TESTS")
        self.assertFalse(result["ready_for_human_review"])

    def test_dirty_worktree_and_wrong_repo_are_rejected(self):
        (self.repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "clean"):
            self.execute()
        (self.repo / "untracked.txt").unlink()
        bad = json.loads(json.dumps(self.contract))
        bad["repo"] = str(self.root / "other")
        with self.assertRaisesRegex(ReviewError, "different"):
            run_implementation(bad, self.repo, ["sample/value.py"], [sys.executable, "-m", "unittest"], self.root / "runs")

    def test_forged_context_path_is_rejected(self):
        bad = json.loads(json.dumps(self.contract))
        bad["context_files"].append({"path": "sample/value.py", "sha256": "0" * 64})
        with self.assertRaisesRegex(ReviewError, "context allowlist"):
            run_implementation(bad, self.repo, ["sample/value.py"], [sys.executable, "-m", "unittest"], self.root / "runs")

    def test_invalid_test_command_stops_before_model(self):
        with patch("yanxu.implement.generate_patch", side_effect=AssertionError("model called")):
            with self.assertRaisesRegex(ReviewError, "allowlist"):
                run_implementation(self.contract, self.repo, ["sample/value.py"], ["sh", "-c", "true"], self.root / "runs")

    def test_team_policy_binds_paths_and_test_command_to_the_run(self):
        command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
        policy = create_policy("demo team", ["sample/value.py"], command)
        with patch("yanxu.implement.generate_patch", return_value=self.model()):
            result = run_implementation(self.contract, self.repo, ["sample/value.py"], command,
                                        self.root / "runs", timeout=10, policy=policy)
        manifest = json.loads(Path(result["folder"], "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["team_policy"]["name"], "demo team")
        self.assertEqual(len(manifest["team_policy"]["sha256"]), 64)

    def test_team_policy_rejects_scope_or_test_drift_before_model(self):
        command = [sys.executable, "-m", "unittest"]
        policy = create_policy("demo team", ["sample/value.py"], command)
        with patch("yanxu.implement.generate_patch", side_effect=AssertionError("model called")):
            with self.assertRaisesRegex(ReviewError, "policy"):
                run_implementation(self.contract, self.repo, ["sample/other.py"], command,
                                   self.root / "runs", policy=policy)
            with self.assertRaisesRegex(ReviewError, "policy"):
                run_implementation(self.contract, self.repo, ["sample/value.py"], [sys.executable, "-m", "unittest", "discover"],
                                   self.root / "runs", policy=policy)


if __name__ == "__main__":
    unittest.main()
