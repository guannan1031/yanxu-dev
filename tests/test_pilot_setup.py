import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from yanxu.core import ReviewError
from yanxu.pilot_setup import initialize_pilot, inspect_pilot


class FakeRunner:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls = []

    def __call__(self, arguments, **kwargs):
        self.calls.append(arguments)
        return subprocess.CompletedProcess(arguments, self.returncode, "ok", "")


class PilotSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_init_generates_private_values_without_printing_or_overwriting(self):
        result = initialize_pilot(self.root, "pilot-team", "Pilot Team", 58080)
        env_text = (self.root / ".env").read_text()
        self.assertEqual(result["status"], "INITIALIZED")
        self.assertFalse(result["secrets_printed"])
        self.assertNotIn("replace-with", env_text)
        values = dict(line.split("=", 1) for line in env_text.splitlines())
        self.assertGreaterEqual(len(values["YANXU_DB_PASSWORD"]), 24)
        self.assertGreaterEqual(len(values["YANXU_BOOTSTRAP_TOKEN"]), 24)
        self.assertGreaterEqual(len(values["YANXU_GITHUB_WEBHOOK_SECRET"]), 24)
        self.assertNotIn(values["YANXU_BOOTSTRAP_TOKEN"], json.dumps(result))
        with self.assertRaisesRegex(ReviewError, "will not overwrite"):
            initialize_pilot(self.root, "pilot-team", "Pilot Team")

    def test_doctor_writes_redacted_ready_report(self):
        initialize_pilot(self.root, "pilot-team", "Pilot Team")
        token = dict(line.split("=", 1) for line in (self.root / ".env").read_text().splitlines())[
            "YANXU_BOOTSTRAP_TOKEN"]
        output = self.root / "runs" / "pilot-doctor.json"
        runner = FakeRunner()
        result = inspect_pilot(self.root, output, runner)
        report_text = output.read_text()
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["secrets_in_output"])
        self.assertNotIn(token, report_text)
        self.assertEqual(len(runner.calls), 3)
        self.assertEqual(runner.calls[-1], ["docker", "compose", "config", "--quiet"])

    def test_doctor_reports_missing_environment_without_calling_compose_config(self):
        runner = FakeRunner()
        result = inspect_pilot(self.root, self.root / "doctor.json", runner)
        self.assertEqual(result["status"], "NEEDS_WORK")
        self.assertFalse(any(call[-2:] == ["config", "--quiet"] for call in runner.calls))

    def test_doctor_rejects_placeholders_and_docker_failure(self):
        (self.root / ".env").write_text((Path(__file__).parents[1] / ".env.example").read_text())
        result = inspect_pilot(self.root, self.root / "doctor.json", FakeRunner(returncode=1))
        failed = {item["id"] for item in result["checks"] if item["status"] == "FAIL"}
        self.assertIn("placeholder_values", failed)
        self.assertIn("docker", failed)
        self.assertIn("compose_config", failed)


if __name__ == "__main__":
    unittest.main()
