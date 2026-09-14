import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yanxu.core import ReviewError, binding, capture
from yanxu.patches import patch_paths, prepare, safe_path
from tests.test_core import FakeGitHub


PROPOSAL = """diff --git a/sample/value.py b/sample/value.py
--- a/sample/value.py
+++ b/sample/value.py
@@ -1,2 +1,2 @@
 def value():
-    return 0
+    return 1
"""


class PatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "sample").mkdir()
        self.source = self.repo / "sample/value.py"
        self.source.write_bytes(b"def value():\n    return 0\n")
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        self.git("add", "sample/value.py")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
        sha = self.git("rev-parse", "HEAD").strip()
        snap = capture("example/demo", 1, FakeGitHub())
        snap["pr"]["head_sha"] = sha
        snap["binding"] = binding(snap)
        self.evidence = {"snapshot": snap, "ai": {"status": "completed", "answer": {"suggested_patch": PROPOSAL}}}

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True, stderr=subprocess.DEVNULL)

    def prepare(self, replay=True):
        return prepare(self.evidence, self.repo, ["sample/value.py"], self.root / "results", replay=replay)

    def test_prepare_preserves_dirty_source_and_uses_committed_blob(self):
        self.source.write_text("user's unfinished work\n")
        result = self.prepare()
        self.assertEqual(self.source.read_text(), "user's unfinished work\n")
        self.assertEqual((Path(result["workspace"]) / "sample/value.py").read_text(), "def value():\n    return 1\n")
        self.assertEqual(result["status"], "PREPARED_NOT_TESTED")
        self.assertEqual(result["tests"], "NOT_RUN")
        self.assertFalse(result["remote_modified"])
        self.assertTrue((Path(result["folder"]) / "verified.diff").exists())

    def test_live_mode_rechecks_and_rejects_stale(self):
        current = copy.deepcopy(self.evidence["snapshot"])
        current["pr"]["head_sha"] = "f" * 40
        with patch("yanxu.patches.capture", return_value=current), self.assertRaisesRegex(ReviewError, "stale"):
            self.prepare(replay=False)
        self.assertFalse((self.root / "results").exists())

    def test_live_mode_requires_open_pr_even_if_evidence_matches(self):
        snap = self.evidence["snapshot"]
        snap["pr"]["state"] = "closed"
        snap["binding"] = binding(snap)
        with patch("yanxu.patches.capture", return_value=snap), self.assertRaisesRegex(ReviewError, "closed"):
            self.prepare(replay=False)

    def test_live_mode_matches_current_evidence(self):
        with patch("yanxu.patches.capture", return_value=self.evidence["snapshot"]):
            result = self.prepare(replay=False)
        self.assertEqual(result["mode"], "live_evidence")
        self.assertEqual(result["verification"]["status"], "UNCHANGED")
        self.assertFalse(result["auto_merge_allowed"])

    def test_replay_does_not_call_github(self):
        with patch("yanxu.patches.capture", side_effect=AssertionError("network forbidden")):
            result = self.prepare()
        self.assertEqual(result["mode"], "historical_replay")

    def test_forbidden_paths(self):
        for name in ("../escape.py", "/absolute.py", "sample/../escape.py", "sample/./x.py", "tests/test_x.py", ".github/workflows/ci.yml", "sample/.env", "sample/conftest.py"):
            with self.subTest(name=name), self.assertRaises(ReviewError):
                safe_path(name)

    def test_header_mismatch_rename_and_mode_change_rejected(self):
        proposals = [PROPOSAL.replace("+++ b/sample/value.py", "+++ b/other.py"),
                     PROPOSAL.replace("--- a/", "rename from old.py\n--- a/"),
                     PROPOSAL + "new mode 100755\n"]
        for proposal in proposals:
            with self.subTest(proposal=proposal), self.assertRaises(ReviewError):
                patch_paths(proposal)

    def test_tab_timestamp_headers_preserve_exact_paths(self):
        proposal = PROPOSAL.replace("--- a/sample/value.py\n", "--- a/sample/value.py\t2026-09-13 00:00:00 +0000\n")
        proposal = proposal.replace("+++ b/sample/value.py\n", "+++ b/sample/value.py\t2026-09-13 00:01:00 +0000\n")
        self.evidence["ai"]["answer"]["suggested_patch"] = proposal
        self.assertEqual(self.prepare()["status"], "PREPARED_NOT_TESTED")

    def test_explicit_allowlist_required(self):
        with self.assertRaisesRegex(ReviewError, "allowlist"):
            prepare(self.evidence, self.repo, ["sample/other.py"], self.root / "results", replay=True)

    def test_symlink_target_rejected(self):
        self.source.unlink()
        self.source.symlink_to("/tmp/do-not-read")
        self.git("add", "sample/value.py")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "symlink")
        snap = self.evidence["snapshot"]
        snap["pr"]["head_sha"] = self.git("rev-parse", "HEAD").strip()
        snap["binding"] = binding(snap)
        with self.assertRaisesRegex(ReviewError, "regular file"):
            self.prepare()

    def test_incompatible_patch_leaves_failure_manifest(self):
        self.evidence["ai"]["answer"]["suggested_patch"] = PROPOSAL.replace("-    return 0", "-    return 999")
        with self.assertRaises(ReviewError):
            self.prepare()
        manifest = json.loads(next((self.root / "results").glob("*/manifest.json")).read_text())
        self.assertEqual(manifest["status"], "FAILED")
        self.assertEqual(self.source.read_text(), "def value():\n    return 0\n")

    def test_incomplete_evidence_rejected(self):
        snap = self.evidence["snapshot"]
        snap["complete"] = False
        snap["binding"] = binding(snap)
        with self.assertRaisesRegex(ReviewError, "Complete context"):
            self.prepare()

    def test_binary_and_crlf_sources_fail_without_normalizing(self):
        for content in (b"def value():\r\n    return 0\r\n", b"def value():\n    return 0\x00\n"):
            with self.subTest(content=content):
                self.source.write_bytes(content)
                self.git("add", "sample/value.py")
                self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "encoding fixture")
                snap = self.evidence["snapshot"]
                snap["pr"]["head_sha"] = self.git("rev-parse", "HEAD").strip()
                snap["binding"] = binding(snap)
                with self.assertRaisesRegex(ReviewError, "UTF-8/LF"):
                    self.prepare()
                self.assertEqual(self.source.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
