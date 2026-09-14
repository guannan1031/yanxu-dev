import json
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from yanxu.core import ReviewError
from yanxu.policy import create_policy
from yanxu.team import (add_project, build_team_board, create_workspace, export_team_bundle,
                        render_html, write_workspace)


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
        self.assertEqual(board["projects"][0]["policy"]["status"], "NOT_CONFIGURED")
        self.assertIn("not aggregated", board["measurement_boundary"])

    def test_exports_sanitized_bundle_with_policy_fingerprint_and_checksums(self):
        runs = self.root / "service-a-runs"
        runs.mkdir()
        secret_source = "THIS_SOURCE_MUST_NOT_BE_EXPORTED"
        (runs / "implementation.json").write_text(json.dumps({
            "kind": "yanxu.implementation_run", "status": "READY_FOR_HUMAN_REVIEW",
            "tests": "PASSED", "remote_modified": False, "auto_merge_allowed": False,
            "source": secret_source,
        }), encoding="utf-8")
        policy_path = self.root / "policy.json"
        policy_path.write_text(json.dumps(create_policy(
            "Service A", ["sample/value.py"], ["python", "-m", "unittest"]
        )), encoding="utf-8")
        add_project(self.workspace, "service-a", runs, policy=policy_path)

        output = self.root / "pilot.zip"
        result = export_team_bundle(self.workspace, output)
        self.assertFalse(result["remote_modified"])
        with zipfile.ZipFile(output) as bundle:
            self.assertEqual(set(bundle.namelist()), {
                "README.txt", "manifest.json", "team-board.html", "team-board.json",
            })
            manifest = json.loads(bundle.read("manifest.json"))
            board = json.loads(bundle.read("team-board.json"))
            self.assertEqual(manifest["kind"], "yanxu.pilot_evidence_bundle")
            self.assertFalse(manifest["auto_merge_allowed"])
            self.assertEqual(board["projects"][0]["policy"]["status"], "CONFIGURED")
            self.assertEqual(len(board["projects"][0]["policy"]["sha256"]), 64)
            for item in manifest["files"]:
                self.assertEqual(hashlib.sha256(bundle.read(item["name"])).hexdigest(), item["sha256"])
            joined = b"\n".join(bundle.read(name) for name in bundle.namelist())
            self.assertNotIn(secret_source.encode(), joined)
            self.assertNotIn(str(runs).encode(), joined)

        with self.assertRaisesRegex(ReviewError, "already exists"):
            export_team_bundle(self.workspace, output)

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
