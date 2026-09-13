import json
import tempfile
import unittest
from pathlib import Path

from yanxu.core import ReviewError
from yanxu.task import build_contract, collect_context, render_contract


class TaskContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        (self.repo / ".git").mkdir()
        (self.repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("token=ghp_secret_value_1234567890\n", encoding="utf-8")
        (self.repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
        (self.repo / "tests").mkdir()
        (self.repo / "private.py").write_text("should not be read\n", encoding="utf-8")

    def test_context_is_allowlisted_and_redacted(self):
        context = collect_context(self.repo)
        paths = {item["path"] for item in context}
        self.assertIn("README.md", paths)
        self.assertIn("AGENTS.md", paths)
        self.assertNotIn("private.py", paths)
        agents = next(item for item in context if item["path"] == "AGENTS.md")
        self.assertNotIn("ghp_secret_value", agents["content"])

    def test_contract_contains_tests_and_acceptance(self):
        contract = build_contract("Add a safe endpoint", self.repo)
        self.assertEqual(contract["kind"], "yanxu.task_contract")
        self.assertEqual(contract["suggested_test_commands"][0][:3], ["python", "-m", "unittest"])
        self.assertFalse(contract["remote_modified"])
        markdown = render_contract(contract)
        self.assertIn("Add a safe endpoint", markdown)
        self.assertIn("## Acceptance", markdown)

    def test_invalid_repo_and_requirement_are_rejected(self):
        with self.assertRaises(ReviewError):
            collect_context(self.repo / "missing")
        with self.assertRaises(ReviewError):
            build_contract("", self.repo)


if __name__ == "__main__":
    unittest.main()
