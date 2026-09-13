import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yanxu.core import ReviewError
from yanxu.workflow import run_workflow


class DeliveryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()

    def make_ready(self):
        (self.repo / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
        (self.repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text(".env\n*.key\n", encoding="utf-8")
        (self.repo / "tests").mkdir()
        workflows = self.repo / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: CI\n", encoding="utf-8")

    def test_not_ready_repository_stops_before_task(self):
        output = Path(self.temp.name) / "blocked"
        result = run_workflow("Add pagination", self.repo, output)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse((output / "task.json").exists())
        saved = json.loads((output / "workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["current_stage"], "doctor")
        self.assertFalse(saved["auto_merge_allowed"])

    def test_ready_local_repository_creates_task_contract(self):
        self.make_ready()
        output = Path(self.temp.name) / "local"
        result = run_workflow("Add pagination", self.repo, output)
        self.assertEqual(result["status"], "READY_FOR_IMPLEMENTATION")
        self.assertTrue((output / "task.json").exists())
        self.assertTrue((output / "workflow.html").exists())

    def test_pr_workflow_collects_and_rechecks_evidence(self):
        self.make_ready()
        snapshot = {"binding": "one"}
        output = Path(self.temp.name) / "pr"
        with patch("yanxu.workflow.capture", side_effect=[snapshot, snapshot]) as mocked_capture, \
             patch("yanxu.workflow.assess", return_value={"status": "MANUAL_REVIEW", "blockers": []}), \
             patch("yanxu.workflow.compare", return_value={"status": "UNCHANGED", "changes": []}):
            result = run_workflow("Add pagination", self.repo, output, "owner/repo", 12)
        self.assertEqual(result["status"], "READY_FOR_MANUAL_REVIEW")
        self.assertEqual(mocked_capture.call_count, 2)
        self.assertTrue((output / "evidence.json").exists())
        self.assertTrue((output / "verify.json").exists())
        self.assertFalse(result["remote_modified"])

    def test_stale_pr_evidence_blocks_workflow(self):
        self.make_ready()
        output = Path(self.temp.name) / "stale"
        with patch("yanxu.workflow.capture", side_effect=[{"binding": "one"}, {"binding": "two"}]), \
             patch("yanxu.workflow.assess", return_value={"status": "MANUAL_REVIEW", "blockers": []}), \
             patch("yanxu.workflow.compare", return_value={"status": "STALE", "changes": ["head_sha"]}):
            result = run_workflow("Add pagination", self.repo, output, "owner/repo", 12)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(json.loads((output / "workflow.json").read_text())["current_stage"], "verify")

    def test_partial_github_target_is_rejected(self):
        self.make_ready()
        with self.assertRaises(ReviewError):
            run_workflow("Add pagination", self.repo, Path(self.temp.name) / "bad", github_repo="owner/repo")


if __name__ == "__main__":
    unittest.main()
