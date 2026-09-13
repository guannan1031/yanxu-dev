import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yanxu.core import binding, capture, ReviewError
from yanxu.test_runner import run_tests, test_command
from tests.test_core import FakeGitHub


PROPOSAL = """diff --git a/sample/value.py b/sample/value.py
--- a/sample/value.py
+++ b/sample/value.py
@@ -1,2 +1,2 @@
 def value():
-    return 0
+    return 1
"""


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        (self.repo / "sample").mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "sample/value.py").write_text("def value():\n    return 0\n")
        (self.repo / "tests/test_value.py").write_text("import unittest\nfrom sample.value import value\n\nclass TestValue(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(value(), 1)\n")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
        sha = self.git("rev-parse", "HEAD").strip()
        snap = capture("example/demo", 1, FakeGitHub())
        snap["pr"]["head_sha"] = sha
        snap["binding"] = binding(snap)
        self.evidence = {"snapshot": snap, "ai": {"status": "completed", "answer": {"suggested_patch": PROPOSAL}}}
        self.root = root

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True, stderr=subprocess.DEVNULL)

    def execute(self, command, replay=True, timeout=10):
        return run_tests(self.evidence, self.repo, ["sample/value.py"], command, self.root / "results", replay=replay, timeout=timeout)

    def test_patched_archive_runs_explicit_tests_and_preserves_checkout(self):
        before = (self.repo / "sample/value.py").read_text()
        result = self.execute(["python3.11", "-m", "unittest", "discover", "-s", "tests", "-v"])
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["tests"], "PASSED")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual((self.repo / "sample/value.py").read_text(), before)
        self.assertIn("OK", Path(result["log"]).read_text())
        self.assertFalse(result["remote_modified"])

    def test_failed_test_is_recorded_and_nonzero(self):
        result = self.execute(["python3.11", "-c", "raise SystemExit(4)"])
        self.assertEqual(result["status"], "FAILED_TESTS")
        self.assertEqual(result["tests"], "FAILED")
        self.assertEqual(result["exit_code"], 4)

    def test_timeout_kills_process_group(self):
        result = self.execute(["python3.11", "-c", "__import__('time').sleep(5)"], timeout=1)
        self.assertEqual(result["status"], "TIMED_OUT")
        self.assertEqual(result["tests"], "TIMEOUT")

    def test_invalid_command_is_rejected_without_output(self):
        with self.assertRaisesRegex(ReviewError, "Shell syntax"):
            self.execute(["python3.11", "-c", "print(1)", "|"])
        with self.assertRaisesRegex(ReviewError, "allowlist"):
            self.execute(["sh", "-c", "echo unsafe"])

    def test_live_stale_evidence_stops_before_archive(self):
        current = copy.deepcopy(self.evidence["snapshot"])
        current["pr"]["head_sha"] = "f" * 40
        with patch("yanxu.test_runner.capture", return_value=current), self.assertRaisesRegex(ReviewError, "stale"):
            self.execute(["python3.11", "-m", "unittest"], replay=False)
        self.assertFalse((self.root / "results").exists())

    def test_replay_never_calls_github(self):
        with patch("yanxu.test_runner.capture", side_effect=AssertionError("network")):
            result = self.execute(["python3.11", "-m", "unittest"])
        self.assertEqual(result["verification"]["status"], "NOT_CHECKED_REPLAY")

    def test_command_requires_separate_arguments(self):
        with self.assertRaises(ReviewError):
            test_command(["python3.11", "-c", "print(1)", "|"])
        with self.assertRaises(ReviewError):
            test_command(["/tmp/custom-runner"])


if __name__ == "__main__":
    unittest.main()
