import json
import tempfile
import unittest
from pathlib import Path

from yanxu.core import ReviewError
from yanxu.team import add_project, build_team_board, create_workspace, render_html, write_workspace


class TeamWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "team.json"
        write_workspace(self.workspace, create_workspace("Demo Team"))

    def test_registers_projects_and_aggregates_delivery_evidence_without_efficiency_claim(self):
        runs = self.root / "service-a-runs"
        runs.mkdir()
        (runs / "implementation.json").write_text(json.dumps({"kind": "yanxu.implementation_run", "status": "READY_FOR_HUMAN_REVIEW", "tests": "PASSED", "remote_modified": False, "auto_merge_allowed": False}), encoding="utf-8")
        add_project(self.workspace, "service-a", runs)
        board = build_team_board(self.workspace)
        self.assertEqual(board["summary"]["registered_projects"], 1)
        self.assertEqual(board["summary"]["isolated_tests_passed"], 1)
        self.assertEqual(board["projects"][0]["benchmark_status"], "NOT_MEASURED")
        self.assertIn("not aggregated", board["measurement_boundary"])

    def test_duplicate_project_and_missing_artifacts_are_visible_or_rejected(self):
        runs = self.root / "service-a-runs"
        runs.mkdir()
        add_project(self.workspace, "service-a", runs)
        with self.assertRaisesRegex(ReviewError, "already exists"):
            add_project(self.workspace, "service-a", runs)
        runs.rmdir()
        board = build_team_board(self.workspace)
        self.assertEqual(board["projects"][0]["health"], "UNAVAILABLE")
        self.assertIn("runs", board["projects"][0]["error"])

    def test_render_escapes_workspace_and_project_content(self):
        workspace = self.root / "unsafe.json"
        write_workspace(workspace, create_workspace("<script>alert(1)</script>"))
        runs = self.root / "runs"
        runs.mkdir()
        add_project(workspace, "<script>alert(2)</script>", runs)
        rendered = render_html(build_team_board(workspace))
        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", rendered)


if __name__ == "__main__":
    unittest.main()
