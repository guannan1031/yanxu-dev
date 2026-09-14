import json
import tempfile
import unittest
from pathlib import Path

from yanxu.board import build_board, render_html
from yanxu.core import ReviewError


class DeliveryBoardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "runs"
        self.root.mkdir()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_collects_supported_runs_without_claiming_remote_writes(self):
        self.write("workflow.json", {"kind": "yanxu.delivery_workflow", "status": "READY_FOR_MANUAL_REVIEW", "remote_modified": False, "auto_merge_allowed": False})
        self.write("implementation.json", {"kind": "yanxu.implementation_run", "status": "READY_FOR_HUMAN_REVIEW", "tests": "PASSED", "remote_modified": False, "auto_merge_allowed": False})
        self.write("other.json", {"kind": "ignored"})
        board = build_board(self.root)
        self.assertEqual(board["summary"]["recorded_runs"], 2)
        self.assertEqual(board["summary"]["isolated_tests_passed"], 1)
        self.assertEqual(board["summary"]["manual_review_pending"], 2)
        self.assertEqual(board["summary"]["recorded_remote_writes"], 0)

    def test_escapes_artifact_names_and_keeps_insufficient_measurement_exploratory(self):
        self.write("<script>alert(1)</script>.json", {"kind": "yanxu.implementation_run", "status": "READY_FOR_HUMAN_REVIEW", "tests": "PASSED"})
        measurement = self.root / "observed.json"
        measurement.write_text(json.dumps({"evidence_type": "observed", "scope": "one task", "records": [{"task_id": "a", "task_type": "bugfix", "same_scope": True, "baseline": {"human_minutes": 20, "quality_passed": True, "rework_count": 0, "evidence": "base"}, "yanxu": {"human_minutes": 10, "quality_passed": True, "rework_count": 0, "evidence": "yanxu"}}]}), encoding="utf-8")
        board = build_board(self.root, measurement)
        rendered = render_html(board)
        self.assertEqual(board["benchmark"]["status"], "EXPLORATORY")
        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertIn("不可计算", rendered)

    def test_missing_runs_directory_is_rejected(self):
        with self.assertRaisesRegex(ReviewError, "--runs"):
            build_board(self.root / "missing")


if __name__ == "__main__":
    unittest.main()
