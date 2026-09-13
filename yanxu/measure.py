"""Append non-overwriting observations to an efficiency benchmark dataset."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from .core import ReviewError, now, redact


def _text(value: str, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ReviewError(f"{label} must be between 1 and {limit} characters")
    return redact(value.strip())


def _load(path: Path, scope: str) -> dict:
    if not path.exists():
        return {"schema_version": 1, "evidence_type": "observed", "scope": scope, "records": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError("Could not read the measurement dataset") from exc
    if payload.get("schema_version") != 1 or payload.get("evidence_type") != "observed" or not isinstance(payload.get("records"), list):
        raise ReviewError("Measurement dataset schema is unsupported")
    if payload.get("scope") != scope:
        raise ReviewError("Measurement scope does not match the existing dataset")
    return payload


def _write(path: Path, payload: dict) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        raise ReviewError("Could not write the measurement dataset") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def record_observation(path: Path, scope: str, task_id: str, task_type: str, variant: str,
                       human_minutes: float, quality_passed: bool, rework_count: int,
                       evidence: str, same_scope: bool) -> dict:
    scope = _text(scope, "scope", 500)
    task_id = _text(task_id, "task_id", 120)
    task_type = _text(task_type, "task_type", 120)
    evidence = _text(evidence, "evidence", 500)
    if variant not in {"baseline", "yanxu"}:
        raise ReviewError("variant must be baseline or yanxu")
    if isinstance(human_minutes, bool) or not isinstance(human_minutes, (int, float)) or human_minutes <= 0 or human_minutes > 10_000:
        raise ReviewError("human_minutes must be a positive number no greater than 10000")
    if not isinstance(quality_passed, bool):
        raise ReviewError("quality_passed must be true or false")
    if isinstance(rework_count, bool) or not isinstance(rework_count, int) or rework_count < 0:
        raise ReviewError("rework_count must be a non-negative integer")
    if not isinstance(same_scope, bool):
        raise ReviewError("same_scope must be true or false")

    payload = _load(path, scope)
    if len(payload["records"]) >= 100 and all(item.get("task_id") != task_id for item in payload["records"]):
        raise ReviewError("Measurement dataset already contains 100 tasks")
    record = next((item for item in payload["records"] if item.get("task_id") == task_id), None)
    if record is None:
        record = {"task_id": task_id, "task_type": task_type, "same_scope": same_scope,
                  "baseline": None, "yanxu": None}
        payload["records"].append(record)
    elif record.get("task_type") != task_type or record.get("same_scope") is not same_scope:
        raise ReviewError("task_type and same_scope must match the existing task record")
    if record.get(variant) is not None:
        raise ReviewError(f"{task_id}.{variant} is already recorded; existing observations are not overwritten")
    record[variant] = {
        "human_minutes": round(float(human_minutes), 3),
        "quality_passed": quality_passed,
        "rework_count": rework_count,
        "evidence": evidence,
        "recorded_at": now(),
    }
    payload["updated_at"] = now()
    _write(path, payload)
    complete = record["baseline"] is not None and record["yanxu"] is not None
    return {"dataset": str(path.resolve()), "task_id": task_id, "recorded_variant": variant,
            "pair_complete": complete, "paired_tasks": sum(
                item.get("baseline") is not None and item.get("yanxu") is not None for item in payload["records"]),
            "total_tasks": len(payload["records"]), "remote_modified": False}
