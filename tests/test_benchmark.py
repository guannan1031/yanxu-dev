import unittest

from yanxu.benchmark import analyze, render_html, render_markdown
from yanxu.core import ReviewError


def record(task_id, baseline=20, yanxu=10, same_scope=True, baseline_quality=True, yanxu_quality=True):
    return {
        "task_id": task_id,
        "task_type": "bugfix",
        "same_scope": same_scope,
        "baseline": {"human_minutes": baseline, "quality_passed": baseline_quality, "rework_count": 1,
                     "evidence": f"evidence/{task_id}-baseline.json"},
        "yanxu": {"human_minutes": yanxu, "quality_passed": yanxu_quality, "rework_count": 0,
                  "evidence": f"evidence/{task_id}-yanxu.json"},
    }


class BenchmarkTests(unittest.TestCase):
    def test_synthetic_data_is_demo_only(self):
        result = analyze({"evidence_type": "synthetic", "scope": "pagination demo", "records": [record("a"), record("b", 30, 15)]})
        self.assertEqual(result["status"], "DEMO_ONLY")
        self.assertEqual(result["summary"]["observed_time_reduction_percent"], 50.0)
        self.assertFalse(result["claim_allowed"])

    def test_five_observed_quality_matched_tasks_allow_scoped_claim(self):
        result = analyze({"evidence_type": "observed", "scope": "small Python PR fixes", "records": [record(str(i)) for i in range(5)]})
        self.assertEqual(result["status"], "OBSERVED")
        self.assertTrue(result["claim_allowed"])
        self.assertEqual(result["summary"]["eligible_tasks"], 5)

    def test_quality_and_scope_failures_are_excluded(self):
        result = analyze({"evidence_type": "observed", "scope": "trial", "records": [
            record("scope", same_scope=False), record("quality", yanxu_quality=False), record("ok")
        ]})
        self.assertEqual(result["status"], "EXPLORATORY")
        self.assertEqual(result["summary"]["eligible_tasks"], 1)
        self.assertEqual(result["summary"]["excluded_tasks"], 2)

    def test_renderers_escape_untrusted_labels(self):
        result = analyze({"evidence_type": "synthetic", "scope": "<script>alert(1)</script>", "records": [record("<b>x</b>")]})
        rendered = render_html(result)
        self.assertNotIn("<script>alert(1)</script>", rendered)
        self.assertIn('rel="icon" href="data:,"', rendered)
        self.assertIn("DEMO_ONLY", render_markdown(result))

    def test_invalid_measurements_are_rejected(self):
        with self.assertRaises(ReviewError):
            analyze({"evidence_type": "observed", "scope": "x", "records": [record("a", 0, 1)]})
        with self.assertRaises(ReviewError):
            analyze({"evidence_type": "unknown", "scope": "x", "records": [record("a")]})
        missing = record("missing")
        missing["baseline"].pop("evidence")
        with self.assertRaisesRegex(ReviewError, "evidence"):
            analyze({"evidence_type": "observed", "scope": "x", "records": [missing]})


if __name__ == "__main__":
    unittest.main()
