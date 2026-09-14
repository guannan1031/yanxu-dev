import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from yanxu.core import ReviewError
from yanxu.operations import backup_service, restore_service


class FakeRunner:
    def __init__(self, backup_bytes=b"synthetic-postgres-backup"):
        self.backup_bytes = backup_bytes
        self.calls = []
        self.fail_on = None

    def __call__(self, arguments, **kwargs):
        self.calls.append(arguments)
        if self.fail_on and self.fail_on in arguments:
            return subprocess.CompletedProcess(arguments, 1, "", "synthetic failure")
        if arguments[2] == "cp" and arguments[3].startswith("db:"):
            Path(arguments[4]).write_bytes(self.backup_bytes)
        return subprocess.CompletedProcess(arguments, 0, "ok", "")


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def test_backup_creates_hash_manifest_without_overwriting(self):
        output = self.root / "backups" / "pilot.dump"
        runner = FakeRunner()
        result = backup_service(self.root, output, runner)
        self.assertEqual(result["status"], "BACKED_UP")
        manifest = json.loads((output.with_name("pilot.dump.json")).read_text())
        self.assertEqual(manifest["sha256"], hashlib.sha256(runner.backup_bytes).hexdigest())
        self.assertEqual(manifest["bytes"], len(runner.backup_bytes))
        self.assertEqual(manifest["yanxu_version"], "0.18.0")
        self.assertTrue(manifest["contains_private_service_data"])
        self.assertIn("pg_dump", runner.calls[0])
        with self.assertRaisesRegex(ReviewError, "must not already exist"):
            backup_service(self.root, output, runner)

    def test_restore_requires_confirmation_and_matching_manifest(self):
        backup = self.root / "pilot.dump"
        runner = FakeRunner()
        backup_service(self.root, backup, runner)
        with self.assertRaisesRegex(ReviewError, "confirm-restore"):
            restore_service(self.root, backup, False, runner)
        backup.write_bytes(b"tampered")
        with self.assertRaisesRegex(ReviewError, "does not match"):
            restore_service(self.root, backup, True, runner)

    def test_restore_validates_archive_and_restarts_services(self):
        backup = self.root / "pilot.dump"
        backup.write_bytes(b"verified-backup")
        (self.root / "pilot.dump.json").write_text(json.dumps({
            "schema_version": 1,
            "kind": "yanxu.postgres_backup_manifest",
            "backup_file": backup.name,
            "bytes": backup.stat().st_size,
            "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        }))
        runner = FakeRunner()
        result = restore_service(self.root, backup, True, runner)
        self.assertEqual(result["status"], "RESTORED")
        commands = [call[2] for call in runner.calls]
        self.assertIn("stop", commands)
        self.assertIn("start", commands)
        restore = next(call for call in runner.calls if "--single-transaction" in call)
        self.assertIn("--clean", restore)
        self.assertIn("--if-exists", restore)
        self.assertIn("pg_isready", runner.calls[-1])

    def test_restore_restarts_services_after_restore_failure(self):
        backup = self.root / "pilot.dump"
        backup.write_bytes(b"verified-backup")
        (self.root / "pilot.dump.json").write_text(json.dumps({
            "schema_version": 1,
            "kind": "yanxu.postgres_backup_manifest",
            "backup_file": backup.name,
            "bytes": backup.stat().st_size,
            "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        }))
        runner = FakeRunner()
        runner.fail_on = "--single-transaction"
        with self.assertRaisesRegex(ReviewError, "synthetic failure"):
            restore_service(self.root, backup, True, runner)
        self.assertTrue(any(call[2] == "start" for call in runner.calls))

    def test_restore_restarts_after_partial_stop_failure(self):
        backup = self.root / "pilot.dump"
        backup.write_bytes(b"verified-backup")
        (self.root / "pilot.dump.json").write_text(json.dumps({
            "schema_version": 1,
            "kind": "yanxu.postgres_backup_manifest",
            "backup_file": backup.name,
            "bytes": backup.stat().st_size,
            "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        }))
        runner = FakeRunner()
        runner.fail_on = "stop"
        with self.assertRaisesRegex(ReviewError, "synthetic failure"):
            restore_service(self.root, backup, True, runner)
        self.assertTrue(any(call[2] == "start" for call in runner.calls))


if __name__ == "__main__":
    unittest.main()
