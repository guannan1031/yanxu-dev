"""Cross-platform Docker Compose backup and restore operations."""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

from . import __version__
from .core import ReviewError, now


def _run(arguments: list[str], compose_dir: Path, runner=subprocess.run):
    try:
        result = runner(arguments, cwd=str(compose_dir), capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise ReviewError("Docker CLI is not available on PATH") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "no command output").strip()[-1200:]
        raise ReviewError(f"Docker Compose operation failed: {' '.join(arguments[:4])}: {detail}")
    return result


def _compose_dir(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir() or not (resolved / "compose.yaml").is_file():
        raise ReviewError("Compose directory must contain compose.yaml")
    return resolved


def _manifest_path(backup: Path) -> Path:
    return backup.with_name(backup.name + ".json")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def backup_service(compose_dir: Path, output: Path, runner=subprocess.run) -> dict:
    compose_dir = _compose_dir(compose_dir)
    output = output.resolve()
    manifest_path = _manifest_path(output)
    if output.exists() or manifest_path.exists():
        raise ReviewError("Backup output and manifest must not already exist")
    output.parent.mkdir(parents=True, exist_ok=True)
    remote = f"/tmp/yanxu-backup-{uuid.uuid4().hex}.dump"
    try:
        _run(["docker", "compose", "exec", "-T", "db", "pg_dump", "-U", "yanxu",
              "-d", "yanxu", "-Fc", "-f", remote], compose_dir, runner)
        _run(["docker", "compose", "cp", f"db:{remote}", str(output)], compose_dir, runner)
    finally:
        try:
            _run(["docker", "compose", "exec", "-T", "db", "rm", "-f", remote],
                 compose_dir, runner)
        except ReviewError:
            pass
    if not output.is_file() or output.stat().st_size == 0:
        raise ReviewError("Docker Compose did not create a non-empty backup file")
    digest = _sha256_file(output)
    manifest = {
        "schema_version": 1,
        "kind": "yanxu.postgres_backup_manifest",
        "created_at": now(),
        "yanxu_version": __version__,
        "database": "yanxu",
        "backup_file": output.name,
        "bytes": output.stat().st_size,
        "sha256": digest,
        "contains_private_service_data": True,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "BACKED_UP", "backup": str(output), "manifest": str(manifest_path),
            "bytes": manifest["bytes"], "sha256": digest}


def _verified_manifest(backup: Path) -> dict:
    manifest_path = _manifest_path(backup)
    if not backup.is_file() or backup.stat().st_size == 0:
        raise ReviewError("Backup file must exist and be non-empty")
    if not manifest_path.is_file():
        raise ReviewError("Backup manifest is required before restore")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError("Backup manifest is not valid JSON") from exc
    if (manifest.get("schema_version") != 1
            or manifest.get("kind") != "yanxu.postgres_backup_manifest"
            or manifest.get("backup_file") != backup.name):
        raise ReviewError("Backup manifest does not match this backup file")
    digest = _sha256_file(backup)
    if manifest.get("bytes") != backup.stat().st_size or manifest.get("sha256") != digest:
        raise ReviewError("Backup size or SHA-256 does not match its manifest")
    return manifest


def restore_service(compose_dir: Path, backup: Path, confirm_restore: bool,
                    runner=subprocess.run) -> dict:
    if not confirm_restore:
        raise ReviewError("Restore requires --confirm-restore")
    compose_dir = _compose_dir(compose_dir)
    backup = backup.resolve()
    manifest = _verified_manifest(backup)
    remote = f"/tmp/yanxu-restore-{uuid.uuid4().hex}.dump"
    stopped = False
    operation_error = None
    try:
        _run(["docker", "compose", "cp", str(backup), f"db:{remote}"], compose_dir, runner)
        _run(["docker", "compose", "exec", "-T", "db", "pg_restore", "--list", remote],
             compose_dir, runner)
        stopped = True
        _run(["docker", "compose", "stop", "app", "worker"], compose_dir, runner)
        _run(["docker", "compose", "exec", "-T", "db", "pg_restore", "-U", "yanxu",
              "-d", "yanxu", "--clean", "--if-exists", "--single-transaction", remote],
             compose_dir, runner)
    except ReviewError as exc:
        operation_error = exc
    finally:
        if stopped:
            try:
                _run(["docker", "compose", "start", "app", "worker"], compose_dir, runner)
            except ReviewError as exc:
                if operation_error is None:
                    operation_error = exc
                else:
                    operation_error = ReviewError(f"{operation_error}; services also failed to restart: {exc}")
        try:
            _run(["docker", "compose", "exec", "-T", "db", "rm", "-f", remote],
                 compose_dir, runner)
        except ReviewError:
            pass
    if operation_error is not None:
        raise operation_error
    _run(["docker", "compose", "exec", "-T", "db", "pg_isready", "-U", "yanxu", "-d", "yanxu"],
         compose_dir, runner)
    return {"status": "RESTORED", "backup": str(backup), "sha256": manifest["sha256"],
            "database_ready": True, "services_restarted": True}
