"""Persist a small, explicit team policy for controlled implementation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .core import ReviewError, redact
from .patches import safe_path
from .test_runner import test_command


def _name(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 120:
        raise ReviewError("Policy name must be between 1 and 120 characters")
    return redact(value.strip())


def _paths(value: list[str]) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 10:
        raise ReviewError("Policy must contain 1–10 allowed paths")
    paths = [safe_path(item) for item in value]
    if len(paths) != len(set(paths)):
        raise ReviewError("Policy allowed paths must be unique")
    return paths


def create_policy(name: str, allowed_paths: list[str], command: list[str]) -> dict:
    return {
        "schema_version": 1,
        "kind": "yanxu.team_policy",
        "name": _name(name),
        "allowed_paths": _paths(allowed_paths),
        "test_command": test_command(command),
    }


def load_policy(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError("Could not read a valid team policy JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "kind", "name", "allowed_paths", "test_command"}:
        raise ReviewError("Team policy schema is unsupported")
    if payload["schema_version"] != 1 or payload["kind"] != "yanxu.team_policy":
        raise ReviewError("Team policy schema is unsupported")
    return create_policy(payload["name"], payload["allowed_paths"], payload["test_command"])


def enforce_policy(policy: dict, allowed_paths: list[str], command: list[str]) -> dict:
    normalized = create_policy(policy.get("name"), policy.get("allowed_paths"), policy.get("test_command"))
    requested = _paths(allowed_paths)
    if not set(requested).issubset(normalized["allowed_paths"]):
        raise ReviewError("Requested source path is outside the supplied team policy")
    if test_command(command) != normalized["test_command"]:
        raise ReviewError("Test command does not match the supplied team policy")
    fingerprint = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return {"name": normalized["name"], "sha256": fingerprint}
