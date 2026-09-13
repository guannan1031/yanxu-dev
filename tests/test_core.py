import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yanxu.ai import validate_answer
from yanxu.cli import main
from yanxu.core import ReviewError, assess, binding, capture, compare, redact, validate_repo
from yanxu.report import render


class FakeGitHub:
    def __init__(self):
        self.pr = {"number": 1, "html_url": "https://github.com/example/demo/pull/1", "title": "Fix pagination",
                   "body": "A synthetic task", "state": "open", "draft": False, "merged": False,
                   "head": {"sha": "a" * 40}, "base": {"sha": "b" * 40, "ref": "main"},
                   "changed_files": 1, "mergeable": True, "mergeable_state": "clean"}
        self.files = [{"filename": "sample/pagination.py", "status": "modified", "patch": "@@ -1 +1 @@\n-return 0\n+return 1", "additions": 1, "deletions": 1}]
        self.checks = {"total_count": 1, "check_runs": [{"id": 9, "name": "test", "status": "completed", "conclusion": "success", "app": {"slug": "github-actions"}, "output": {}, "html_url": "https://github.com/example/demo/actions/runs/7/job/9", "details_url": "https://github.com/example/demo/actions/runs/7/job/9"}]}
        self.statuses = {"total_count": 0, "statuses": []}
        self.reviews = []
        self.pr_reads = 0
        self.change_during_capture = False
        self.log_reads = 0

    def api(self, endpoint, pages=False):
        if "/files?" in endpoint:
            return copy.deepcopy(self.files)
        if "/check-runs?" in endpoint:
            return copy.deepcopy(self.checks)
        if "/status?" in endpoint:
            return copy.deepcopy(self.statuses)
        if "/reviews?" in endpoint:
            return copy.deepcopy(self.reviews)
        self.pr_reads += 1
        result = copy.deepcopy(self.pr)
        if self.change_during_capture and self.pr_reads == 2:
            result["head"]["sha"] = "c" * 40
        return result

    def failed_logs(self, repo, run_id):
        self.log_reads += 1
        return "AssertionError: expected [1, 2], got [2, 3]"


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.gh = FakeGitHub()

    def snapshot(self):
        return capture("example/demo", 1, self.gh)

    def test_success_is_manual_review_never_merge_authorization(self):
        result = assess(self.snapshot())
        self.assertEqual(result["status"], "MANUAL_REVIEW")
        self.assertFalse(result["auto_merge_allowed"])

    def test_failure_pending_cancelled_and_skipped_checks_block(self):
        for conclusion in ("failure", None, "cancelled", "skipped", "neutral"):
            with self.subTest(conclusion=conclusion):
                self.gh.checks["check_runs"][0]["conclusion"] = conclusion
                self.assertEqual(assess(self.snapshot())["status"], "BLOCKED")

    def test_no_ci_is_not_a_green_light(self):
        self.gh.checks = {"total_count": 0, "check_runs": []}
        self.assertIn("No CI evidence found.", assess(self.snapshot())["blockers"])

    def test_missing_patch_and_check_overflow_are_incomplete(self):
        del self.gh.files[0]["patch"]
        self.assertFalse(self.snapshot()["complete"])
        self.gh.files[0]["patch"] = "a"
        self.gh.checks["total_count"] = 200
        self.assertFalse(self.snapshot()["complete"])

    def test_commit_change_during_capture_rejected(self):
        self.gh.change_during_capture = True
        with self.assertRaisesRegex(ReviewError, "changed"):
            self.snapshot()

    def test_head_and_base_change_invalidate_report(self):
        saved = self.snapshot()
        for key in ("head_sha", "base_sha"):
            new = copy.deepcopy(saved)
            new["pr"][key] = "d" * 40
            self.assertEqual(compare(saved, new)["status"], "STALE")

    def test_check_change_invalidates_even_when_head_unchanged(self):
        saved = self.snapshot()
        new = copy.deepcopy(saved)
        new["checks"][0]["conclusion"] = "failure"
        self.assertEqual(compare(saved, new)["status"], "STALE")

    def test_capture_time_does_not_invalidate(self):
        saved = self.snapshot()
        new = copy.deepcopy(saved)
        new["captured_at"] = "later"
        new["collection_seconds"] = 99
        self.assertEqual(compare(saved, new)["status"], "UNCHANGED")

    def test_requirement_body_change_invalidates(self):
        saved = self.snapshot()
        new = copy.deepcopy(saved)
        new["pr"]["body"] = "New acceptance requirement"
        self.assertEqual(compare(saved, new)["changes"], ["body"])

    def test_previous_evidence_schema_remains_verifiable(self):
        current = self.snapshot()
        legacy = copy.deepcopy(current)
        legacy["schema_version"] = 1
        legacy["binding"] = binding(legacy)
        self.assertEqual(compare(legacy, current)["status"], "UNCHANGED")

    def test_old_approval_not_counted_and_revocation_wins(self):
        self.gh.reviews = [{"id": 1, "user": {"login": "reviewer"}, "state": "APPROVED", "commit_id": "old"}]
        self.assertEqual(assess(self.snapshot())["current_head_approvals"], [])
        self.gh.reviews[0]["commit_id"] = "a" * 40
        self.gh.reviews.append({"id": 2, "user": {"login": "reviewer"}, "state": "CHANGES_REQUESTED", "commit_id": "a" * 40})
        result = assess(self.snapshot())
        self.assertEqual(result["current_head_approvals"], [])
        self.assertEqual(result["status"], "BLOCKED")

    def test_untrusted_log_url_is_never_executed(self):
        self.gh.checks["check_runs"][0].update(conclusion="failure", details_url="https://evil.example/actions/runs/7")
        capture("example/demo", 1, self.gh, include_logs=True)
        self.assertEqual(self.gh.log_reads, 0)

    def test_same_repo_failed_logs_collected_only_when_requested(self):
        self.gh.checks["check_runs"][0]["conclusion"] = "failure"
        self.assertEqual(self.snapshot()["logs"], [])
        snap = capture("example/demo", 1, self.gh, include_logs=True)
        self.assertIn("AssertionError", snap["logs"][0]["excerpt"])

    def test_repo_argument_rejects_shell_and_urls(self):
        for name in ("https://github.com/a/b", "a/b;ls", "--help", "a/b/c"):
            with self.assertRaises(ReviewError):
                validate_repo(name)

    def test_redaction(self):
        secret = "ghp_" + "x" * 30
        self.assertNotIn(secret, redact("token=" + secret))
        self.assertNotIn("sensitive", redact("Authorization: sensitive"))

    def test_html_escapes_untrusted_content_and_urls(self):
        snap = self.snapshot()
        snap["pr"]["title"] = '<img src=x onerror="alert(1)">'
        snap["checks"][0]["url"] = "javascript:alert(1)"
        doc = render({"snapshot": snap, "assessment": assess(snap), "ai": {"status": "not_requested"}, "elapsed_seconds": 1})
        self.assertNotIn("<img", doc)
        self.assertNotIn("javascript:", doc)
        self.assertIn("&lt;img", doc)

    def test_ai_schema_rejects_invalid_output(self):
        with self.assertRaises(ReviewError):
            validate_answer({"summary": "all good"})
        valid = {"summary": "observed", "findings": [], "repair_plan": [], "suggested_patch": "", "limitations": []}
        self.assertEqual(validate_answer(valid), valid)
        valid["findings"] = ["not an object"]
        with self.assertRaises(ReviewError):
            validate_answer(valid)

    def test_ai_redaction_preserves_json_structure(self):
        answer = {"summary": 'token=abc"', "findings": [], "repair_plan": [], "suggested_patch": "", "limitations": []}
        result = validate_answer(answer)
        self.assertIn("[REDACTED]", result["summary"])
        self.assertEqual(set(result), set(answer))

    def test_cli_preserves_evidence_when_ai_fails(self):
        with tempfile.TemporaryDirectory() as temp, patch("yanxu.cli.capture", return_value=self.snapshot()), patch("yanxu.cli.diagnose", side_effect=ReviewError("offline")):
            status = main(["review", "--repo", "example/demo", "--pr", "1", "--ai", "--output", temp])
            self.assertEqual(status, 1)
            result = json.loads(next(Path(temp).glob("*/evidence.json")).read_text())
            self.assertEqual(result["ai"]["status"], "failed")
            self.assertIsNone(result["efficiency_improvement_percent"])
            self.assertTrue(next(Path(temp).glob("*/report.html")).exists())

    def test_verify_detects_tampered_saved_fingerprint(self):
        snap = self.snapshot()
        snap["pr"]["head_sha"] = "tampered"
        self.assertNotEqual(binding(snap), snap["binding"])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "evidence.json"
            path.write_text(json.dumps({"snapshot": snap}))
            self.assertEqual(main(["verify", str(path)]), 1)


if __name__ == "__main__":
    unittest.main()
