import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yanxu.core import ReviewError
from yanxu.draft_pr import inspect_draft_pr, publish_draft_pr


class DraftPrPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        self.git("add", "README.md")
        self.commit("base")
        self.base_sha = self.git("rev-parse", "HEAD").strip()
        self.git("remote", "add", "origin", "https://github.com/example/demo.git")
        self.git("switch", "-q", "-c", "feat/example")
        (self.repo / "code.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.repo / "tests").mkdir()
        (self.repo / "tests/test_code.py").write_text("assert True\n", encoding="utf-8")
        self.git("add", "code.py", "tests/test_code.py")
        self.commit("feature")
        self.body = self.root / "body.md"
        self.body.write_text("Adds a bounded feature.\n", encoding="utf-8")
        self.allowed = ["code.py", "tests/test_code.py"]

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True,
                                       stderr=subprocess.DEVNULL)

    def commit(self, message):
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                 "commit", "-qm", message)

    def fake_gh(self, repo, *args):
        if args[0] == "api":
            return json.dumps({"object": {"sha": self.base_sha}})
        if args[:2] == ("pr", "list"):
            return "[]"
        if args[:2] == ("pr", "create"):
            return "https://github.com/example/demo/pull/7\n"
        raise AssertionError(args)

    @patch("yanxu.draft_pr._push")
    def test_default_is_read_only_plan(self, mocked_push):
        with patch("yanxu.draft_pr._gh", side_effect=self.fake_gh):
            result = publish_draft_pr(self.repo, "example/demo", "main", "feat/example", self.allowed,
                                      "feat: example", self.body, self.root / "plan")
        self.assertEqual(result["status"], "READY_TO_CREATE_DRAFT")
        self.assertFalse(result["remote_modified"])
        mocked_push.assert_not_called()
        saved = json.loads((self.root / "plan" / "draft-pr.json").read_text())
        self.assertEqual(saved["changed_paths"], sorted(self.allowed))
        self.assertFalse(saved["auto_merge_allowed"])

    @patch("yanxu.draft_pr._push")
    def test_explicit_confirmation_pushes_and_creates_draft(self, mocked_push):
        with patch("yanxu.draft_pr._gh", side_effect=self.fake_gh):
            result = publish_draft_pr(self.repo, "example/demo", "main", "feat/example", self.allowed,
                                      "feat: example", self.body, self.root / "created", confirm_create=True)
        self.assertEqual(result["status"], "DRAFT_PR_CREATED")
        self.assertEqual(result["pr_url"], "https://github.com/example/demo/pull/7")
        self.assertTrue(result["remote_modified"])
        mocked_push.assert_called_once_with(self.repo.resolve(), "feat/example")

    @patch("yanxu.draft_pr._push", side_effect=ReviewError("push transport failed"))
    def test_push_error_records_uncertain_remote_state(self, mocked_push):
        with patch("yanxu.draft_pr._gh", side_effect=self.fake_gh), self.assertRaisesRegex(ReviewError, "transport"):
            publish_draft_pr(self.repo, "example/demo", "main", "feat/example", self.allowed,
                             "feat: example", self.body, self.root / "failed", confirm_create=True)
        saved = json.loads((self.root / "failed" / "draft-pr.json").read_text())
        self.assertEqual(saved["status"], "FAILED")
        self.assertEqual(saved["remote_state"], "VERIFY_REQUIRED_AFTER_PUSH_ERROR")
        self.assertFalse(saved["remote_modified"])
        mocked_push.assert_called_once()

    def test_dirty_worktree_and_scope_drift_are_rejected(self):
        (self.repo / "code.py").write_text("VALUE = 2\n", encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "working tree"):
            inspect_draft_pr(self.repo, "example/demo", "main", "feat/example", self.allowed,
                             "feat: example", self.body)
        self.git("checkout", "--", "code.py")
        with patch("yanxu.draft_pr._gh", side_effect=self.fake_gh), self.assertRaisesRegex(ReviewError, "exactly match"):
            inspect_draft_pr(self.repo, "example/demo", "main", "feat/example", ["code.py"],
                             "feat: example", self.body)

    def test_stale_base_and_existing_pr_are_rejected(self):
        stale = lambda repo, *args: json.dumps({"object": {"sha": "f" * 40}}) if args[0] == "api" else "[]"
        with patch("yanxu.draft_pr._gh", side_effect=stale), self.assertRaisesRegex(ReviewError, "current GitHub base"):
            inspect_draft_pr(self.repo, "example/demo", "main", "feat/example", self.allowed,
                             "feat: example", self.body)

        def existing(repo, *args):
            return (json.dumps({"object": {"sha": self.base_sha}}) if args[0] == "api"
                    else '[{"number": 3, "url": "https://github.com/example/demo/pull/3", "isDraft": true}]')
        with patch("yanxu.draft_pr._gh", side_effect=existing), self.assertRaisesRegex(ReviewError, "already exists"):
            inspect_draft_pr(self.repo, "example/demo", "main", "feat/example", self.allowed,
                             "feat: example", self.body)

    def test_remote_sensitive_paths_and_credentials_are_rejected(self):
        self.git("remote", "set-url", "origin", "https://github.com/other/demo.git")
        with self.assertRaisesRegex(ReviewError, "origin"):
            inspect_draft_pr(self.repo, "example/demo", "main", "feat/example", self.allowed,
                             "feat: example", self.body)
        self.git("remote", "set-url", "origin", "https://github.com/example/demo.git")
        with self.assertRaisesRegex(ReviewError, "Sensitive path"):
            inspect_draft_pr(self.repo, "example/demo", "main", "feat/example", [".env"],
                             "feat: example", self.body)
        self.body.write_text("token=ghp_abcdefghijklmnopqrstuvwxyz\n", encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "credential"):
            inspect_draft_pr(self.repo, "example/demo", "main", "feat/example", self.allowed,
                             "feat: example", self.body)


if __name__ == "__main__":
    unittest.main()
