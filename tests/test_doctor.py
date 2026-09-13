import tempfile
import unittest
from pathlib import Path

from yanxu.doctor import inspect_repo, render_html, render_markdown


class RepositoryDoctorTests(unittest.TestCase):
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

    def test_ready_repository_passes_all_checks(self):
        self.make_ready()
        result = inspect_repo(self.repo)
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["source_files_scanned"], 0)
        self.assertFalse(result["remote_modified"])

    def test_empty_repository_has_actionable_blockers(self):
        result = inspect_repo(self.repo)
        self.assertEqual(result["status"], "NEEDS_WORK")
        self.assertIn("project_rules", result["blockers"])
        self.assertIn("pull_request_ci", result["blockers"])
        self.assertIn("Add AGENTS.md", render_markdown(result))

    def test_reports_are_standalone_and_console_safe(self):
        self.make_ready()
        rendered = render_html(inspect_repo(self.repo))
        self.assertIn('rel="icon" href="data:,"', rendered)
        self.assertIn("READY", rendered)


if __name__ == "__main__":
    unittest.main()
