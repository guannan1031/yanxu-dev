import json
import tempfile
import unittest
from pathlib import Path

from yanxu.benchmark import load_and_analyze
from yanxu.core import ReviewError
from yanxu.measure import record_observation


class MeasurementRecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dataset = Path(self.temp.name) / "measurements.json"
        self.scope = "Python bug fixes in yanxu-dev during 2026-09"

    def record(self, variant, **extra):
        values = {"path": self.dataset, "scope": self.scope, "task_id": "bug-1", "task_type": "bugfix",
                  "variant": variant, "human_minutes": 20 if variant == "baseline" else 12,
                  "quality_passed": True, "rework_count": 0,
                  "evidence": f"evidence/{variant}.json", "same_scope": True}
        values.update(extra)
        return record_observation(**values)

    def test_two_observations_create_analyzable_pair(self):
        first = self.record("baseline")
        second = self.record("yanxu")
        self.assertFalse(first["pair_complete"])
        self.assertTrue(second["pair_complete"])
        result = load_and_analyze(self.dataset)
        self.assertEqual(result["status"], "EXPLORATORY")
        self.assertEqual(result["summary"]["observed_time_reduction_percent"], 40.0)
        self.assertEqual(result["records"][0]["baseline"]["evidence"], "evidence/baseline.json")

    def test_existing_observation_is_not_overwritten(self):
        self.record("baseline")
        before = self.dataset.read_text(encoding="utf-8")
        with self.assertRaisesRegex(ReviewError, "not overwritten"):
            self.record("baseline", human_minutes=1)
        self.assertEqual(self.dataset.read_text(encoding="utf-8"), before)

    def test_scope_and_task_metadata_must_stay_consistent(self):
        self.record("baseline")
        with self.assertRaisesRegex(ReviewError, "scope"):
            self.record("yanxu", scope="different")
        with self.assertRaisesRegex(ReviewError, "task_type"):
            self.record("yanxu", task_type="feature")

    def test_secrets_are_redacted_from_evidence_reference(self):
        self.record("baseline", evidence="token=ghp_secret_value_1234567890")
        payload = json.loads(self.dataset.read_text(encoding="utf-8"))
        self.assertNotIn("ghp_secret_value", payload["records"][0]["baseline"]["evidence"])


if __name__ == "__main__":
    unittest.main()
