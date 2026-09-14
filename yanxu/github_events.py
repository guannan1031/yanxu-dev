"""Verified, organization-bound GitHub webhook queue."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from typing import Any

from .core import redact, validate_repo
from .service import (AuthenticationError, AuthorizationError, ConflictError,
                      NotFoundError, PostgresStore, Principal, ValidationError)


GITHUB_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS github_installations (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    github_installation_id bigint NOT NULL UNIQUE,
    account_login varchar(120) NOT NULL,
    status varchar(16) NOT NULL CHECK (status IN ('ACTIVE', 'REVOKED')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS github_installations_org_idx
    ON github_installations(organization_id);
CREATE TABLE IF NOT EXISTS github_deliveries (
    id uuid PRIMARY KEY,
    delivery_id varchar(100) NOT NULL UNIQUE,
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    installation_id uuid NOT NULL REFERENCES github_installations(id) ON DELETE CASCADE,
    event_name varchar(60) NOT NULL,
    action varchar(60),
    body_fingerprint char(64) NOT NULL,
    status varchar(24) NOT NULL CHECK (status IN ('PENDING', 'COMPLETED', 'IGNORED_REVOKED')),
    facts jsonb NOT NULL,
    attempt_count integer NOT NULL DEFAULT 0,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX IF NOT EXISTS github_deliveries_queue_idx
    ON github_deliveries(status, received_at);
CREATE INDEX IF NOT EXISTS github_deliveries_org_idx
    ON github_deliveries(organization_id, received_at DESC);
"""


def verify_signature(secret: str, body: bytes, signature: str | None) -> None:
    if not isinstance(secret, str) or len(secret) < 24:
        raise ValidationError("GitHub webhook secret must contain at least 24 characters")
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(expected, signature):
        raise AuthenticationError("GitHub webhook signature is invalid")


def _small_text(value: Any, limit: int = 160) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("GitHub event text field is malformed")
    return redact(value.strip())[:limit]


def _nested(payload: dict, *keys):
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def normalize_event(event_name: str, payload: dict) -> dict:
    supported = {"ping", "pull_request", "check_run", "check_suite", "status",
                 "installation", "installation_repositories"}
    if event_name not in supported:
        raise ValidationError("GitHub webhook event is not subscribed by Yanxu")
    repo = _nested(payload, "repository", "full_name")
    if repo is not None:
        try:
            repo = validate_repo(repo)
        except Exception as exc:
            raise ValidationError("GitHub event repository is malformed") from exc
    facts = {
        "event": event_name,
        "action": _small_text(payload.get("action"), 60),
        "repository": repo,
        "sender": _small_text(_nested(payload, "sender", "login"), 120),
        "installation_id": _nested(payload, "installation", "id"),
    }
    if event_name == "pull_request":
        facts["pull_request"] = {
            "number": payload.get("number"),
            "state": _small_text(_nested(payload, "pull_request", "state"), 20),
            "draft": _nested(payload, "pull_request", "draft"),
            "merged": _nested(payload, "pull_request", "merged"),
            "head_sha": _small_text(_nested(payload, "pull_request", "head", "sha"), 64),
            "base_sha": _small_text(_nested(payload, "pull_request", "base", "sha"), 64),
        }
    elif event_name == "check_run":
        facts["check"] = {
            "id": _nested(payload, "check_run", "id"),
            "name": _small_text(_nested(payload, "check_run", "name"), 160),
            "status": _small_text(_nested(payload, "check_run", "status"), 30),
            "conclusion": _small_text(_nested(payload, "check_run", "conclusion"), 30),
            "head_sha": _small_text(_nested(payload, "check_run", "head_sha"), 64),
        }
    elif event_name == "check_suite":
        facts["check"] = {
            "id": _nested(payload, "check_suite", "id"),
            "status": _small_text(_nested(payload, "check_suite", "status"), 30),
            "conclusion": _small_text(_nested(payload, "check_suite", "conclusion"), 30),
            "head_sha": _small_text(_nested(payload, "check_suite", "head_sha"), 64),
        }
    elif event_name == "status":
        facts["check"] = {
            "state": _small_text(payload.get("state"), 30),
            "context": _small_text(payload.get("context"), 160),
            "sha": _small_text(payload.get("sha"), 64),
        }
    elif event_name == "installation":
        facts["account"] = _small_text(_nested(payload, "installation", "account", "login"), 120)
    return facts


class GitHubEventStore(PostgresStore):
    def initialize(self) -> None:
        super().initialize()
        with self._connect() as connection:
            connection.execute(GITHUB_SCHEMA_SQL)

    def register_installation(self, principal: Principal, github_installation_id: int,
                              account_login: str) -> dict:
        if principal.role != "owner":
            raise AuthorizationError("Owner role is required")
        if not isinstance(github_installation_id, int) or isinstance(github_installation_id, bool) or github_installation_id < 1:
            raise ValidationError("GitHub installation id must be positive")
        account_login = _small_text(account_login, 120)
        if not account_login:
            raise ValidationError("GitHub account login is required")
        installation_id = str(uuid.uuid4())
        with self._connect() as connection:
            row = connection.execute(
                "INSERT INTO github_installations (id, organization_id, github_installation_id, account_login, status) "
                "VALUES (%s, %s, %s, %s, 'ACTIVE') ON CONFLICT (github_installation_id) DO UPDATE "
                "SET account_login = EXCLUDED.account_login, status = 'ACTIVE', updated_at = now() "
                "WHERE github_installations.organization_id = EXCLUDED.organization_id "
                "RETURNING id, github_installation_id, account_login, status, created_at, updated_at",
                (installation_id, principal.organization_id, github_installation_id, account_login),
            ).fetchone()
            if row is None:
                raise ConflictError("GitHub installation is already assigned to another organization")
            self._audit(connection, principal, "github.installation.registered", "github_installation",
                        str(row["id"]), {"github_installation_id": github_installation_id,
                                         "account_login": account_login})
        return self._installation_row(row)

    def list_installations(self, principal: Principal) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, github_installation_id, account_login, status, created_at, updated_at "
                "FROM github_installations WHERE organization_id = %s ORDER BY created_at",
                (principal.organization_id,),
            ).fetchall()
        return [self._installation_row(row) for row in rows]

    def receive(self, secret: str, signature: str | None, delivery_id: str,
                event_name: str, body: bytes) -> dict:
        if len(body) > 2_000_000:
            raise ValidationError("GitHub webhook body exceeds 2 MB")
        verify_signature(secret, body, signature)
        if not isinstance(delivery_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,100}", delivery_id):
            raise ValidationError("GitHub delivery id is malformed")
        if not isinstance(event_name, str) or len(event_name) > 60:
            raise ValidationError("GitHub event name is malformed")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("GitHub webhook body must be UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ValidationError("GitHub webhook body must be an object")
        github_installation_id = _nested(payload, "installation", "id")
        if not isinstance(github_installation_id, int) or isinstance(github_installation_id, bool):
            raise ValidationError("GitHub webhook installation id is missing")
        facts = normalize_event(event_name, payload)
        fingerprint = hashlib.sha256(body).hexdigest()
        action = facts.get("action")
        with self._connect() as connection:
            installation = connection.execute(
                "SELECT id, organization_id, status FROM github_installations WHERE github_installation_id = %s",
                (github_installation_id,),
            ).fetchone()
            if not installation:
                return {"status": "IGNORED_UNREGISTERED", "delivery_id": delivery_id,
                        "duplicate": False, "accepted": False}
            status = "PENDING"
            revoke = event_name == "installation" and action in {"deleted", "suspend"}
            reactivate = event_name == "installation" and action == "unsuspend"
            if installation["status"] == "REVOKED" and not reactivate:
                status = "IGNORED_REVOKED"
            elif revoke or reactivate:
                status = "COMPLETED"
            row = connection.execute(
                "INSERT INTO github_deliveries (id, delivery_id, organization_id, installation_id, event_name, "
                "action, body_fingerprint, status, facts, processed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, "
                "CASE WHEN %s = 'PENDING' THEN NULL ELSE now() END) ON CONFLICT (delivery_id) DO NOTHING RETURNING id",
                (str(uuid.uuid4()), delivery_id, installation["organization_id"], installation["id"],
                 event_name, action, fingerprint, status, json.dumps(facts), status),
            ).fetchone()
            if row is None:
                prior = connection.execute(
                    "SELECT body_fingerprint, event_name, status FROM github_deliveries WHERE delivery_id = %s",
                    (delivery_id,),
                ).fetchone()
                if prior["body_fingerprint"] != fingerprint or prior["event_name"] != event_name:
                    raise ConflictError("GitHub delivery id was reused with different content")
                return {"status": prior["status"], "delivery_id": delivery_id,
                        "duplicate": True, "accepted": True}
            if revoke or reactivate:
                new_status = "REVOKED" if revoke else "ACTIVE"
                connection.execute(
                    "UPDATE github_installations SET status = %s, updated_at = now() WHERE id = %s",
                    (new_status, installation["id"]),
                )
                connection.execute(
                    "INSERT INTO audit_events (organization_id, action, resource_type, resource_id, detail) "
                    "VALUES (%s, %s, 'github_installation', %s, %s::jsonb)",
                    (installation["organization_id"], f"github.installation.{new_status.lower()}",
                     installation["id"], json.dumps({"delivery_id": delivery_id})),
                )
        return {"status": status, "delivery_id": delivery_id, "duplicate": False,
                "accepted": status != "IGNORED_REVOKED"}

    def process_next(self) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, delivery_id, organization_id, event_name, facts FROM github_deliveries "
                "WHERE status = 'PENDING' ORDER BY received_at FOR UPDATE SKIP LOCKED LIMIT 1"
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE github_deliveries SET status = 'COMPLETED', attempt_count = attempt_count + 1, "
                "processed_at = now() WHERE id = %s", (row["id"],)
            )
            connection.execute(
                "INSERT INTO audit_events (organization_id, action, resource_type, resource_id, detail) "
                "VALUES (%s, 'github.delivery.processed', 'github_delivery', %s, %s::jsonb)",
                (row["organization_id"], row["id"],
                 json.dumps({"delivery_id": row["delivery_id"], "event": row["event_name"]})),
            )
        return {"delivery_id": row["delivery_id"], "event": row["event_name"],
                "status": "COMPLETED", "facts": row["facts"]}

    def list_deliveries(self, principal: Principal, limit: int = 100) -> list[dict]:
        limit = max(1, min(limit, 200))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT delivery_id, event_name, action, status, facts, attempt_count, received_at, processed_at "
                "FROM github_deliveries WHERE organization_id = %s ORDER BY received_at DESC LIMIT %s",
                (principal.organization_id, limit),
            ).fetchall()
        return [{**row, "received_at": row["received_at"].isoformat(),
                 "processed_at": row["processed_at"].isoformat() if row["processed_at"] else None}
                for row in rows]

    @staticmethod
    def _installation_row(row: dict) -> dict:
        return {"id": str(row["id"]), "github_installation_id": int(row["github_installation_id"]),
                "account_login": row["account_login"], "status": row["status"],
                "created_at": row["created_at"].isoformat(), "updated_at": row["updated_at"].isoformat()}


def run_worker(database_url: str, once: bool = False, interval: float = 2.0) -> None:
    if interval < 0.1:
        raise ValidationError("Worker interval must be at least 0.1 seconds")
    store = GitHubEventStore(database_url)
    store.initialize()
    while True:
        processed = store.process_next()
        if once:
            return
        if processed is None:
            time.sleep(interval)
