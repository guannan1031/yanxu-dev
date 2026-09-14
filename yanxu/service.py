"""PostgreSQL persistence and organization boundaries for the private service."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from .core import ReviewError, redact, validate_repo


class ServiceError(ReviewError):
    """Base error safe to translate into an API response."""


class AuthenticationError(ServiceError):
    pass


class AuthorizationError(ServiceError):
    pass


class ConflictError(ServiceError):
    pass


class NotFoundError(ServiceError):
    pass


class ValidationError(ServiceError):
    pass


@dataclass(frozen=True)
class Principal:
    organization_id: str
    organization_slug: str
    token_id: str
    role: str


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS organizations (
    id uuid PRIMARY KEY,
    slug varchar(63) NOT NULL UNIQUE,
    name varchar(120) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS api_tokens (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    label varchar(120) NOT NULL,
    token_hash char(64) NOT NULL UNIQUE,
    role varchar(16) NOT NULL CHECK (role IN ('owner', 'viewer')),
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS api_tokens_org_idx ON api_tokens(organization_id);
CREATE TABLE IF NOT EXISTS web_sessions (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    api_token_id uuid NOT NULL REFERENCES api_tokens(id) ON DELETE CASCADE,
    session_hash char(64) NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS web_sessions_org_idx ON web_sessions(organization_id, created_at DESC);
CREATE TABLE IF NOT EXISTS cost_records (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    actor_token_id uuid REFERENCES api_tokens(id) ON DELETE SET NULL,
    category varchar(24) NOT NULL CHECK (category IN ('model', 'ci', 'infrastructure', 'support', 'custom')),
    amount_micros bigint NOT NULL CHECK (amount_micros >= 0),
    currency char(3) NOT NULL,
    evidence_ref varchar(500) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cost_records_org_idx ON cost_records(organization_id, created_at DESC);
CREATE TABLE IF NOT EXISTS workspaces (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name varchar(120) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (organization_id, name)
);
CREATE INDEX IF NOT EXISTS workspaces_org_idx ON workspaces(organization_id);
CREATE TABLE IF NOT EXISTS team_snapshots (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    workspace_id uuid NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    fingerprint char(64) NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS snapshots_org_workspace_idx
    ON team_snapshots(organization_id, workspace_id, created_at DESC);
CREATE TABLE IF NOT EXISTS audit_events (
    id bigserial PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    actor_token_id uuid REFERENCES api_tokens(id) ON DELETE SET NULL,
    action varchar(80) NOT NULL,
    resource_type varchar(40) NOT NULL,
    resource_id uuid,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_events_org_idx
    ON audit_events(organization_id, created_at DESC);
"""


def token_hash(token: str) -> str:
    if not isinstance(token, str) or len(token) < 24 or len(token) > 512:
        raise AuthenticationError("Bearer token is invalid")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _text(value: Any, label: str, limit: int = 120) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValidationError(f"{label} must be between 1 and {limit} characters")
    return redact(value.strip())


def _optional_text(value: Any, label: str, limit: int = 500) -> str | None:
    if value is None:
        return None
    return _text(value, label, limit)


def _optional_nonnegative_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(f"{label} must be a non-negative integer")
    return value


def validate_slug(value: Any) -> str:
    value = _text(value, "Organization slug", 63).lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ValidationError("Organization slug must contain lowercase letters, numbers, or internal hyphens")
    return value


def validate_uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError(f"{label} must be a UUID") from exc


def parse_cost_amount(value: Any) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,12}(?:\.\d{1,6})?", value):
        raise ValidationError("Cost amount must be a non-negative decimal with up to 6 places")
    whole, _, fraction = value.partition(".")
    amount_micros = int(whole) * 1_000_000 + int(fraction.ljust(6, "0") or "0")
    if amount_micros > 9_000_000_000_000_000_000:
        raise ValidationError("Cost amount is too large")
    return amount_micros


def format_cost_amount(amount_micros: int) -> str:
    whole, fraction = divmod(amount_micros, 1_000_000)
    return f"{whole}.{fraction:06d}".rstrip("0").rstrip(".")


def normalize_snapshot(payload: Any, workspace_name: str) -> dict:
    """Keep only the normalized v0.15 fields; never persist diff, body, or logs."""
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValidationError("Team snapshot schema is unsupported")
    if payload.get("kind") != "yanxu.team_github_snapshot":
        raise ValidationError("Expected a Yanxu team GitHub snapshot")
    if payload.get("workspace_name") != workspace_name:
        raise ValidationError("Snapshot workspace name does not match the private workspace")
    source_projects = payload.get("projects")
    if not isinstance(source_projects, list) or len(source_projects) > 100:
        raise ValidationError("Snapshot projects are malformed")

    snapshot_status = payload.get("status")
    if snapshot_status not in {"COMPLETED", "PARTIAL", "FAILED", "NOT_CONFIGURED"}:
        raise ValidationError("Snapshot status is unsupported")
    projects = []
    seen = set()
    allowed_status = {"SYNCED", "UNAVAILABLE", "NOT_CONFIGURED"}
    for source in source_projects:
        if not isinstance(source, dict):
            raise ValidationError("Snapshot project is malformed")
        project_id = _text(source.get("id"), "Project id")
        if project_id in seen:
            raise ValidationError("Snapshot project ids must be unique")
        seen.add(project_id)
        status = source.get("status")
        if status not in allowed_status:
            raise ValidationError("Snapshot project status is unsupported")
        repo = source.get("repo")
        if repo is not None:
            try:
                repo = validate_repo(repo)
            except Exception as exc:
                raise ValidationError("Snapshot repository is malformed") from exc
        blockers = source.get("blockers") or []
        if not isinstance(blockers, list) or len(blockers) > 30:
            raise ValidationError("Snapshot blockers are malformed")
        pull_request = _optional_nonnegative_int(source.get("pr"), "Pull request number")
        if pull_request == 0:
            raise ValidationError("Pull request number must be positive")
        projects.append({
            "id": project_id,
            "status": status,
            "repo": repo,
            "pr": pull_request,
            "ci": _optional_text(source.get("ci"), "CI status", 40),
            "checks_total": _optional_nonnegative_int(source.get("checks_total"), "Check total"),
            "checks_passed": _optional_nonnegative_int(source.get("checks_passed"), "Check passed count"),
            "head_sha": _optional_text(source.get("head_sha"), "Head SHA", 64),
            "base_sha": _optional_text(source.get("base_sha"), "Base SHA", 64),
            "pr_state": _optional_text(source.get("pr_state"), "Pull request state", 20),
            "draft": source.get("draft") if isinstance(source.get("draft"), bool) else None,
            "merged": source.get("merged") if isinstance(source.get("merged"), bool) else None,
            "binding": _optional_text(source.get("binding"), "Evidence binding", 128),
            "assessment": _optional_text(source.get("assessment"), "Assessment", 40),
            "blockers": [_text(item, "Blocker", 500) for item in blockers],
            "auto_merge_allowed": False,
            "captured_at": _optional_text(source.get("captured_at"), "Capture time", 80),
            "error": redact(str(source["error"]))[:500] if source.get("error") else None,
        })

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return {
        "schema_version": 1,
        "kind": "yanxu.team_github_snapshot",
        "status": snapshot_status,
        "created_at": _optional_text(payload.get("created_at"), "Snapshot creation time", 80),
        "workspace_name": workspace_name,
        "projects": projects,
        "summary": {
            "registered_projects": len(projects),
            "configured_projects": summary.get("configured_projects"),
            "synced_projects": sum(item["status"] == "SYNCED" for item in projects),
            "unavailable_projects": sum(item["status"] == "UNAVAILABLE" for item in projects),
        },
        "data_boundary": "Private service stores normalized PR, commit, CI and assessment facts only; source code, diffs, PR bodies, logs and credentials are excluded.",
        "remote_modified": False,
        "auto_merge_allowed": False,
    }


class PostgresStore:
    def __init__(self, database_url: str):
        if not database_url:
            raise ValidationError("YANXU_DATABASE_URL is required")
        self.database_url = database_url

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise ServiceError("Install the private service dependencies with: pip install '.[server]'") from exc
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(SCHEMA_SQL)

    def health(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 AS ok").fetchone()["ok"] == 1

    def bootstrap(self, slug: str, name: str, token: str) -> Principal:
        slug = validate_slug(slug)
        name = _text(name, "Organization name")
        digest = token_hash(token)
        organization_id = str(uuid.uuid4())
        token_id = str(uuid.uuid4())
        with self._connect() as connection:
            organization = connection.execute(
                "INSERT INTO organizations (id, slug, name) VALUES (%s, %s, %s) "
                "ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug RETURNING id, slug",
                (organization_id, slug, name),
            ).fetchone()
            existing = connection.execute(
                "SELECT t.id, t.role FROM api_tokens t WHERE t.token_hash = %s", (digest,)
            ).fetchone()
            if existing and str(organization["id"]) != self._token_org(connection, digest):
                raise ConflictError("Bootstrap token is already assigned to another organization")
            if not existing:
                connection.execute(
                    "INSERT INTO api_tokens (id, organization_id, label, token_hash, role) "
                    "VALUES (%s, %s, %s, %s, 'owner')",
                    (token_id, organization["id"], "bootstrap-owner", digest),
                )
            else:
                token_id = str(existing["id"])
            return Principal(str(organization["id"]), organization["slug"], token_id, "owner")

    @staticmethod
    def _token_org(connection, digest: str) -> str | None:
        row = connection.execute(
            "SELECT organization_id FROM api_tokens WHERE token_hash = %s", (digest,)
        ).fetchone()
        return str(row["organization_id"]) if row else None

    def authenticate(self, token: str) -> Principal:
        digest = token_hash(token)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT o.id AS organization_id, o.slug, t.id AS token_id, t.role "
                "FROM api_tokens t JOIN organizations o ON o.id = t.organization_id "
                "WHERE t.token_hash = %s AND t.revoked_at IS NULL",
                (digest,),
            ).fetchone()
        if not row:
            raise AuthenticationError("Bearer token is invalid or revoked")
        return Principal(str(row["organization_id"]), row["slug"], str(row["token_id"]), row["role"])

    def create_web_session(self, token: str, lifetime_seconds: int = 28_800) -> dict:
        principal = self.authenticate(token)
        if not isinstance(lifetime_seconds, int) or not 300 <= lifetime_seconds <= 86_400:
            raise ValidationError("Web session lifetime must be between 300 and 86400 seconds")
        plaintext = secrets.token_urlsafe(32)
        session_id = str(uuid.uuid4())
        with self._connect() as connection:
            row = connection.execute(
                "INSERT INTO web_sessions (id, organization_id, api_token_id, session_hash, expires_at) "
                "VALUES (%s, %s, %s, %s, now() + (%s * interval '1 second')) "
                "RETURNING id, expires_at",
                (session_id, principal.organization_id, principal.token_id,
                 hashlib.sha256(plaintext.encode("utf-8")).hexdigest(), lifetime_seconds),
            ).fetchone()
            self._audit(connection, principal, "session.created", "web_session", session_id, {})
        return {"session": plaintext, "expires_at": row["expires_at"].isoformat(),
                "principal": principal}

    def authenticate_web_session(self, session: str) -> Principal:
        if not isinstance(session, str) or len(session) < 24 or len(session) > 512:
            raise AuthenticationError("Web session is invalid or expired")
        digest = hashlib.sha256(session.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT o.id AS organization_id, o.slug, t.id AS token_id, t.role "
                "FROM web_sessions s JOIN api_tokens t ON t.id = s.api_token_id "
                "JOIN organizations o ON o.id = s.organization_id "
                "WHERE s.session_hash = %s AND s.revoked_at IS NULL AND s.expires_at > now() "
                "AND t.revoked_at IS NULL",
                (digest,),
            ).fetchone()
        if not row:
            raise AuthenticationError("Web session is invalid or expired")
        return Principal(str(row["organization_id"]), row["slug"], str(row["token_id"]), row["role"])

    def revoke_web_session(self, session: str) -> None:
        principal = self.authenticate_web_session(session)
        digest = hashlib.sha256(session.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            row = connection.execute(
                "UPDATE web_sessions SET revoked_at = COALESCE(revoked_at, now()) "
                "WHERE session_hash = %s AND organization_id = %s RETURNING id",
                (digest, principal.organization_id),
            ).fetchone()
            self._audit(connection, principal, "session.revoked", "web_session", str(row["id"]), {})

    def create_cost_record(self, principal: Principal, category: str, amount_micros: int,
                           currency: str, evidence_ref: str) -> dict:
        if principal.role != "owner":
            raise AuthorizationError("Owner role is required")
        if category not in {"model", "ci", "infrastructure", "support", "custom"}:
            raise ValidationError("Cost category is unsupported")
        if (not isinstance(amount_micros, int) or isinstance(amount_micros, bool)
                or not 0 <= amount_micros <= 9_000_000_000_000_000_000):
            raise ValidationError("Cost amount_micros must be a non-negative 64-bit integer")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            raise ValidationError("Cost currency must be a three-letter uppercase code")
        evidence_ref = _text(evidence_ref, "Cost evidence reference", 500)
        record_id = str(uuid.uuid4())
        with self._connect() as connection:
            row = connection.execute(
                "INSERT INTO cost_records (id, organization_id, actor_token_id, category, amount_micros, "
                "currency, evidence_ref) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id, category, amount_micros, currency, evidence_ref, created_at",
                (record_id, principal.organization_id, principal.token_id, category,
                 amount_micros, currency, evidence_ref),
            ).fetchone()
            self._audit(connection, principal, "cost.recorded", "cost_record", record_id,
                        {"category": category, "amount_micros": amount_micros, "currency": currency})
        return self._cost_row(row)

    def list_cost_records(self, principal: Principal, limit: int = 100) -> list[dict]:
        limit = max(1, min(limit, 200))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, category, amount_micros, currency, evidence_ref, created_at "
                "FROM cost_records WHERE organization_id = %s ORDER BY created_at DESC LIMIT %s",
                (principal.organization_id, limit),
            ).fetchall()
        return [self._cost_row(row) for row in rows]

    def summarize_costs(self, principal: Principal) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT currency, category, sum(amount_micros) AS amount_micros, count(*) AS records "
                "FROM cost_records WHERE organization_id = %s GROUP BY currency, category "
                "ORDER BY currency, category",
                (principal.organization_id,),
            ).fetchall()
        return [{"currency": row["currency"], "category": row["category"],
                 "amount_micros": int(row["amount_micros"]), "records": int(row["records"])}
                for row in rows]

    def issue_token(self, principal: Principal, label: str, role: str) -> dict:
        if principal.role != "owner":
            raise AuthorizationError("Owner role is required")
        label = _text(label, "Token label")
        if role not in {"owner", "viewer"}:
            raise ValidationError("Token role must be owner or viewer")
        plaintext = secrets.token_urlsafe(32)
        token_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO api_tokens (id, organization_id, label, token_hash, role) VALUES (%s, %s, %s, %s, %s)",
                (token_id, principal.organization_id, label, token_hash(plaintext), role),
            )
            self._audit(connection, principal, "token.issued", "api_token", token_id,
                        {"label": label, "role": role})
        return {"id": token_id, "label": label, "role": role, "token": plaintext,
                "token_visible_once": True}

    def list_tokens(self, principal: Principal) -> list[dict]:
        if principal.role != "owner":
            raise AuthorizationError("Owner role is required")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, label, role, created_at, revoked_at FROM api_tokens "
                "WHERE organization_id = %s ORDER BY created_at",
                (principal.organization_id,),
            ).fetchall()
        return [{"id": str(row["id"]), "label": row["label"], "role": row["role"],
                 "created_at": row["created_at"].isoformat(),
                 "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None}
                for row in rows]

    def revoke_token(self, principal: Principal, token_id: str) -> dict:
        if principal.role != "owner":
            raise AuthorizationError("Owner role is required")
        token_id = validate_uuid(token_id, "Token id")
        if token_id == principal.token_id:
            raise ValidationError("Use another owner token to revoke the current token")
        with self._connect() as connection:
            row = connection.execute(
                "UPDATE api_tokens SET revoked_at = COALESCE(revoked_at, now()) "
                "WHERE id = %s AND organization_id = %s RETURNING id, revoked_at",
                (token_id, principal.organization_id),
            ).fetchone()
            if not row:
                raise NotFoundError("Token was not found")
            self._audit(connection, principal, "token.revoked", "api_token", token_id, {})
        return {"id": str(row["id"]), "revoked_at": row["revoked_at"].isoformat()}

    def create_workspace(self, principal: Principal, name: str) -> dict:
        if principal.role != "owner":
            raise AuthorizationError("Owner role is required")
        name = _text(name, "Workspace name")
        workspace_id = str(uuid.uuid4())
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "INSERT INTO workspaces (id, organization_id, name) VALUES (%s, %s, %s) "
                    "RETURNING id, name, created_at",
                    (workspace_id, principal.organization_id, name),
                ).fetchone()
                self._audit(connection, principal, "workspace.created", "workspace", workspace_id,
                            {"name": name})
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "23505":
                raise ConflictError("Workspace name already exists in this organization") from exc
            raise
        return self._workspace_row(row)

    def list_workspaces(self, principal: Principal) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT w.id, w.name, w.created_at, count(s.id) AS snapshot_count, "
                "max(s.created_at) AS latest_snapshot_at FROM workspaces w "
                "LEFT JOIN team_snapshots s ON s.workspace_id = w.id "
                "WHERE w.organization_id = %s GROUP BY w.id ORDER BY w.created_at",
                (principal.organization_id,),
            ).fetchall()
        return [self._workspace_row(row) for row in rows]

    def save_snapshot(self, principal: Principal, workspace_id: str, payload: dict) -> dict:
        if principal.role != "owner":
            raise AuthorizationError("Owner role is required")
        workspace_id = validate_uuid(workspace_id, "Workspace id")
        with self._connect() as connection:
            workspace = connection.execute(
                "SELECT id, name FROM workspaces WHERE id = %s AND organization_id = %s",
                (workspace_id, principal.organization_id),
            ).fetchone()
            if not workspace:
                raise NotFoundError("Workspace was not found")
            normalized = normalize_snapshot(payload, workspace["name"])
            canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            snapshot_id = str(uuid.uuid4())
            row = connection.execute(
                "INSERT INTO team_snapshots (id, organization_id, workspace_id, fingerprint, payload) "
                "VALUES (%s, %s, %s, %s, %s::jsonb) ON CONFLICT (workspace_id, fingerprint) DO NOTHING "
                "RETURNING id, fingerprint, payload, created_at",
                (snapshot_id, principal.organization_id, workspace_id, fingerprint, canonical),
            ).fetchone()
            created = row is not None
            if row is None:
                row = connection.execute(
                    "SELECT id, fingerprint, payload, created_at FROM team_snapshots "
                    "WHERE workspace_id = %s AND fingerprint = %s",
                    (workspace_id, fingerprint),
                ).fetchone()
            else:
                self._audit(connection, principal, "snapshot.saved", "team_snapshot", snapshot_id,
                            {"workspace_id": workspace_id, "fingerprint": fingerprint})
        return self._snapshot_row(row, created)

    def latest_snapshot(self, principal: Principal, workspace_id: str) -> dict:
        workspace_id = validate_uuid(workspace_id, "Workspace id")
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM workspaces WHERE id = %s AND organization_id = %s",
                (workspace_id, principal.organization_id),
            ).fetchone()
            if not exists:
                raise NotFoundError("Workspace was not found")
            row = connection.execute(
                "SELECT id, fingerprint, payload, created_at FROM team_snapshots "
                "WHERE workspace_id = %s AND organization_id = %s ORDER BY created_at DESC LIMIT 1",
                (workspace_id, principal.organization_id),
            ).fetchone()
        if not row:
            raise NotFoundError("Workspace has no snapshots")
        return self._snapshot_row(row, False)

    def list_audit(self, principal: Principal, limit: int = 100) -> list[dict]:
        limit = max(1, min(limit, 200))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, actor_token_id, action, resource_type, resource_id, detail, created_at "
                "FROM audit_events WHERE organization_id = %s ORDER BY id DESC LIMIT %s",
                (principal.organization_id, limit),
            ).fetchall()
        return [{**row, "id": int(row["id"]),
                 "actor_token_id": str(row["actor_token_id"]) if row["actor_token_id"] else None,
                 "resource_id": str(row["resource_id"]) if row["resource_id"] else None,
                 "created_at": row["created_at"].isoformat()} for row in rows]

    @staticmethod
    def _audit(connection, principal: Principal, action: str, resource_type: str,
               resource_id: str, detail: dict) -> None:
        connection.execute(
            "INSERT INTO audit_events (organization_id, actor_token_id, action, resource_type, resource_id, detail) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
            (principal.organization_id, principal.token_id, action, resource_type, resource_id,
             json.dumps(detail, ensure_ascii=False)),
        )

    @staticmethod
    def _workspace_row(row: dict) -> dict:
        result = {"id": str(row["id"]), "name": row["name"], "created_at": row["created_at"].isoformat()}
        if "snapshot_count" in row:
            result["snapshot_count"] = int(row["snapshot_count"])
            result["latest_snapshot_at"] = row["latest_snapshot_at"].isoformat() if row["latest_snapshot_at"] else None
        return result

    @staticmethod
    def _snapshot_row(row: dict, created: bool) -> dict:
        return {"id": str(row["id"]), "fingerprint": row["fingerprint"], "payload": row["payload"],
                "created_at": row["created_at"].isoformat(), "created": created}

    @staticmethod
    def _cost_row(row: dict) -> dict:
        return {"id": str(row["id"]), "category": row["category"],
                "amount_micros": int(row["amount_micros"]), "currency": row["currency"],
                "amount": format_cost_amount(int(row["amount_micros"])),
                "evidence_ref": row["evidence_ref"], "created_at": row["created_at"].isoformat()}
