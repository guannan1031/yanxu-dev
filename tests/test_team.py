import json
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from yanxu.core import ReviewError
from yanxu.policy import create_policy
from yanxu.team import (add_project, build_team_board, create_workspace, export_team_bundle,
                        render_html, set_github_target, sync_team_github, write_workspace)


class TeamWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "team.json"
        write_workspace(self.workspace, create_workspace("Demo Team"))

    def github_snapshot(self, repo="example/demo", number=1):
        return {
            "schema_version": 2, "repo": repo,
            "pr": {"number": number, "url": f"https://github.com/{repo}/pull/{number}",
                   "title": "Demo", "body": "private requirement", "state": "open",
                   "draft": False, "merged": False, "head_sha": "a" * 40,
                   "base_sha": "b" * 40, "base_ref": "main", "changed_files": 1,
                   "mergeable": True, "mergeable_state": "clean"},
            "files": [{"path": "sample/value.py", "status": "modified", "patch": "private diff",
                       "patch_complete": True, "additions": 1, "deletions": 0}],
            "checks": [{"id": 1, "name": "test", "status": "completed", "conclusion": "success",
                        "url": "", "details_url": "", "app": "github-actions", "summary": ""}],
            "statuses": [], "reviews": [], "logs": [], "complete": True, "warnings": [],
            "captured_at": "2026-09-14T00:00:00+00:00", "collection_seconds": 0.1,
            "binding": "c" * 64,
        }

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

    def test_syncs_registered_github_pr_without_persisting_diff_or_body(self):
        runs = self.root / "service-a-runs"
        runs.mkdir()
        add_project(self.workspace, "service-a", runs, github_repo="example/demo", github_pr=7)
        with patch("yanxu.team.capture", return_value=self.github_snapshot(number=7)) as mocked:
            synced = sync_team_github(self.workspace)
        mocked.assert_called_once_with("example/demo", 7, gh=None)
        self.assertEqual(synced["status"], "COMPLETED")
        self.assertEqual(synced["projects"][0]["ci"], "PASSING")
        self.assertEqual(synced["projects"][0]["assessment"], "MANUAL_REVIEW")
        serialized = json.dumps(synced)
        self.assertNotIn("private diff", serialized)
        self.assertNotIn("private requirement", serialized)
        board = build_team_board(self.workspace, synced)
        self.assertEqual(board["summary"]["github_synced_projects"], 1)
        self.assertEqual(board["projects"][0]["github"]["head_sha"], "a" * 40)
        bundle_path = self.root / "github-pilot.zip"
        export_team_bundle(self.workspace, bundle_path, synced)
        with zipfile.ZipFile(bundle_path) as bundle:
            self.assertIn("team-github.json", bundle.namelist())
            github_export = bundle.read("team-github.json").decode()
            self.assertIn("example/demo", github_export)
            self.assertNotIn("private diff", github_export)
            self.assertNotIn("private requirement", github_export)

    def test_github_sync_keeps_other_projects_when_one_read_fails(self):
        first = self.root / "first"
        second = self.root / "second"
        first.mkdir()
        second.mkdir()
        add_project(self.workspace, "first", first, github_repo="example/first", github_pr=1)
        add_project(self.workspace, "second", second, github_repo="example/second", github_pr=2)

        def capture_one(repo, number, gh=None):
            if repo == "example/second":
                raise ReviewError("GitHub access unavailable")
            return self.github_snapshot(repo, number)

        with patch("yanxu.team.capture", side_effect=capture_one):
            synced = sync_team_github(self.workspace)
        self.assertEqual(synced["status"], "PARTIAL")
        self.assertEqual(synced["summary"]["synced_projects"], 1)
        self.assertEqual(synced["summary"]["unavailable_projects"], 1)
        self.assertEqual(synced["projects"][1]["status"], "UNAVAILABLE")

    def test_github_repo_and_pr_must_be_registered_together(self):
        runs = self.root / "runs"
        runs.mkdir()
        with self.assertRaisesRegex(ReviewError, "supplied together"):
            add_project(self.workspace, "repo-only", runs, github_repo="example/demo")
        with self.assertRaisesRegex(ReviewError, "supplied together"):
            add_project(self.workspace, "pr-only", runs, github_pr=1)

    def test_updates_current_github_target_without_recreating_workspace(self):
        runs = self.root / "runs"
        runs.mkdir()
        add_project(self.workspace, "service-a", runs)
        updated = set_github_target(self.workspace, "service-a", "example/demo", 12)
        self.assertEqual(updated["projects"][0]["github"], {"repo": "example/demo", "pr": 12})
        with self.assertRaisesRegex(ReviewError, "not registered"):
            set_github_target(self.workspace, "missing", "example/demo", 1)

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
