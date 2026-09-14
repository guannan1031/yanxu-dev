import hashlib
import io
import json
import os
import unittest
import uuid
import zipfile

from yanxu.service import (AuthenticationError, AuthorizationError, NotFoundError, PostgresStore,
                           ValidationError, normalize_snapshot, parse_cost_amount, token_hash)


def sample_snapshot(name="Platform Team"):
    return {
        "schema_version": 1,
        "kind": "yanxu.team_github_snapshot",
        "status": "COMPLETED",
        "created_at": "2026-09-14T08:00:00Z",
        "workspace_name": name,
        "projects": [{
            "id": "orders-service", "status": "SYNCED", "repo": "example/orders-service",
            "pr": 7, "ci": "PASSING", "checks_total": 2, "checks_passed": 2,
            "head_sha": "a" * 40, "base_sha": "b" * 40, "pr_state": "open",
            "draft": False, "merged": False, "binding": "c" * 64,
            "assessment": "MANUAL_REVIEW", "blockers": [], "auto_merge_allowed": False,
            "captured_at": "2026-09-14T08:00:00Z", "error": None,
            "diff": "must never be persisted", "body": "must never be persisted",
        }],
        "summary": {"registered_projects": 1, "configured_projects": 1,
                    "synced_projects": 1, "unavailable_projects": 0},
        "remote_modified": False,
        "auto_merge_allowed": False,
    }


class ServiceContractTests(unittest.TestCase):
    def test_token_hash_rejects_short_secret(self):
        with self.assertRaisesRegex(Exception, "invalid"):
            token_hash("short")

    def test_snapshot_is_whitelisted_and_keeps_merge_boundary(self):
        result = normalize_snapshot(sample_snapshot(), "Platform Team")
        project = result["projects"][0]
        self.assertNotIn("diff", project)
        self.assertNotIn("body", project)
        self.assertFalse(project["auto_merge_allowed"])
        self.assertFalse(result["auto_merge_allowed"])

    def test_snapshot_rejects_wrong_workspace(self):
        with self.assertRaisesRegex(ValidationError, "does not match"):
            normalize_snapshot(sample_snapshot(), "Another Team")

    def test_invalid_workspace_uuid_is_rejected_before_database_access(self):
        from yanxu.service import Principal
        principal = Principal(str(uuid.uuid4()), "demo", str(uuid.uuid4()), "owner")
        with self.assertRaisesRegex(ValidationError, "UUID"):
            PostgresStore("unused").latest_snapshot(principal, "not-a-uuid")

    def test_invalid_secure_cookie_setting_is_rejected(self):
        try:
            import fastapi  # noqa: F401
            from yanxu.service_api import create_app
        except ImportError as exc:
            self.skipTest(f"Server test dependencies are unavailable: {exc}")
        previous = os.environ.get("YANXU_SECURE_COOKIES")
        os.environ["YANXU_SECURE_COOKIES"] = "typo"
        try:
            with self.assertRaisesRegex(ValidationError, "true or false"):
                create_app("unused")
        finally:
            if previous is None:
                os.environ.pop("YANXU_SECURE_COOKIES", None)
            else:
                os.environ["YANXU_SECURE_COOKIES"] = previous

    def test_cost_amount_parser_is_exact_and_bounded(self):
        self.assertEqual(parse_cost_amount("12.5"), 12_500_000)
        self.assertEqual(parse_cost_amount("0.000001"), 1)
        with self.assertRaises(ValidationError):
            parse_cost_amount("1.0000001")
        with self.assertRaises(ValidationError):
            parse_cost_amount(12.5)


@unittest.skipUnless(os.environ.get("YANXU_TEST_DATABASE_URL"),
                     "YANXU_TEST_DATABASE_URL is required for PostgreSQL integration tests")
class PostgresServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = PostgresStore(os.environ["YANXU_TEST_DATABASE_URL"])
        cls.store.initialize()

    def setUp(self):
        suffix = uuid.uuid4().hex[:10]
        self.owner_token = f"owner-{suffix}-012345678901234567890123456789"
        self.other_token = f"other-{suffix}-012345678901234567890123456789"
        self.owner = self.store.bootstrap(f"team-{suffix}", "Platform Team", self.owner_token)
        self.other = self.store.bootstrap(f"other-{suffix}", "Other Team", self.other_token)

    def test_persistence_idempotency_rbac_and_organization_isolation(self):
        workspace = self.store.create_workspace(self.owner, "Platform Team")
        other_workspace = self.store.create_workspace(self.other, "Platform Team")
        self.assertEqual([workspace["id"]], [item["id"] for item in self.store.list_workspaces(self.owner)])
        self.assertNotEqual(workspace["id"], other_workspace["id"])

        first = self.store.save_snapshot(self.owner, workspace["id"], sample_snapshot())
        second = self.store.save_snapshot(self.owner, workspace["id"], sample_snapshot())
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["id"], second["id"])
        project = self.store.latest_snapshot(self.owner, workspace["id"])["payload"]["projects"][0]
        self.assertNotIn("diff", project)
        with self.assertRaises(NotFoundError):
            self.store.latest_snapshot(self.other, workspace["id"])

        issued = self.store.issue_token(self.owner, "pilot-viewer", "viewer")
        viewer = self.store.authenticate(issued["token"])
        self.assertEqual(viewer.role, "viewer")
        self.assertEqual(len(self.store.list_workspaces(viewer)), 1)
        with self.assertRaises(AuthorizationError):
            self.store.create_workspace(viewer, "Forbidden")

        model_cost = self.store.create_cost_record(self.owner, "model", 12_500_000,
                                                   "CNY", "invoice-2026-09")
        self.store.create_cost_record(self.owner, "ci", 2_000_000, "CNY", "ci-billing-09")
        self.store.create_cost_record(self.owner, "model", 1_000_000, "USD", "model-billing-09")
        self.assertEqual(model_cost["amount"], "12.5")
        self.assertEqual(len(self.store.summarize_costs(self.owner)), 3)
        self.assertEqual(self.store.list_cost_records(self.other), [])
        with self.assertRaises(AuthorizationError):
            self.store.create_cost_record(viewer, "model", 1, "CNY", "forbidden")

        web_session = self.store.create_web_session(issued["token"])
        self.assertEqual(self.store.authenticate_web_session(web_session["session"]).role, "viewer")
        with self.store._connect() as connection:
            plaintext_matches = connection.execute(
                "SELECT count(*) AS count FROM web_sessions WHERE session_hash = %s",
                (web_session["session"],),
            ).fetchone()["count"]
        self.assertEqual(plaintext_matches, 0)

        second_owner = self.store.issue_token(self.owner, "backup-owner", "owner")
        backup_owner = self.store.authenticate(second_owner["token"])
        self.store.revoke_token(backup_owner, issued["id"])
        with self.assertRaises(AuthenticationError):
            self.store.authenticate(issued["token"])
        with self.assertRaises(AuthenticationError):
            self.store.authenticate_web_session(web_session["session"])

        actions = [item["action"] for item in self.store.list_audit(self.owner)]
        self.assertEqual(actions.count("snapshot.saved"), 1)
        self.assertIn("workspace.created", actions)
        self.assertIn("token.issued", actions)
        self.assertIn("token.revoked", actions)

    def test_http_api_requires_auth_and_enforces_roles(self):
        try:
            from fastapi.testclient import TestClient
            from yanxu.service_api import create_app
        except ImportError as exc:
            self.skipTest(f"Server test dependencies are unavailable: {exc}")

        app = create_app(os.environ["YANXU_TEST_DATABASE_URL"])
        with TestClient(app) as client:
            self.assertEqual(client.get("/healthz").status_code, 200)
            self.assertEqual(client.get("/v1/workspaces").status_code, 401)
            owner_headers = {"Authorization": f"Bearer {self.owner_token}"}
            created = client.post("/v1/workspaces", headers=owner_headers, json={"name": "API Team"})
            self.assertEqual(created.status_code, 201, created.text)

            token_response = client.post("/v1/tokens", headers=owner_headers,
                                         json={"label": "readonly", "role": "viewer"})
            self.assertEqual(token_response.status_code, 201, token_response.text)
            viewer_headers = {"Authorization": f"Bearer {token_response.json()['token']}"}
            self.assertEqual(client.get("/v1/workspaces", headers=viewer_headers).status_code, 200)
            self.assertEqual(client.post("/v1/workspaces", headers=viewer_headers,
                                         json={"name": "Denied"}).status_code, 403)
            other_headers = {"Authorization": f"Bearer {self.other_token}"}
            self.assertEqual(client.get("/v1/workspaces", headers=other_headers).json()["items"], [])

    def test_browser_session_dashboard_audit_export_and_logout(self):
        try:
            from fastapi.testclient import TestClient
            from yanxu.service_api import create_app
        except ImportError as exc:
            self.skipTest(f"Server test dependencies are unavailable: {exc}")

        workspace = self.store.create_workspace(self.owner, "Pilot Dashboard")
        self.store.save_snapshot(self.owner, workspace["id"], sample_snapshot("Pilot Dashboard"))
        app = create_app(os.environ["YANXU_TEST_DATABASE_URL"], secure_cookies=False)
        with TestClient(app) as client:
            root = client.get("/", follow_redirects=False)
            self.assertEqual(root.status_code, 303)
            self.assertEqual(root.headers["location"], "/app")
            anonymous = client.get("/app", follow_redirects=False)
            self.assertEqual(anonymous.status_code, 303)
            self.assertEqual(anonymous.headers["location"], "/login")
            login = client.post("/v1/session", json={"token": self.owner_token})
            self.assertEqual(login.status_code, 200, login.text)
            cookie = login.headers["set-cookie"]
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=strict", cookie)
            self.assertNotIn(self.owner_token, cookie)

            dashboard = client.get("/app")
            self.assertEqual(dashboard.status_code, 200, dashboard.text)
            self.assertIn("default-src 'none'", dashboard.headers["content-security-policy"])
            self.assertIn("connect-src 'self'", dashboard.headers["content-security-policy"])
            self.assertIn("Pilot Dashboard", dashboard.text)
            self.assertIn("example/orders-service", dashboard.text)
            self.assertNotIn("must never be persisted", dashboard.text)

            exported = client.get("/v1/audit/export")
            self.assertEqual(exported.status_code, 200, exported.text)
            self.assertEqual(exported.json()["organization_slug"], self.owner.organization_slug)
            export_payload = exported.json()
            fingerprint = export_payload.pop("fingerprint")
            canonical = json.dumps(export_payload, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")).encode("utf-8")
            self.assertEqual(hashlib.sha256(canonical).hexdigest(), fingerprint)
            self.assertEqual(exported.headers["x-yanxu-evidence-fingerprint"], fingerprint)

            recorded = client.post("/v1/costs", json={
                "category": "model", "amount": "12.50", "currency": "CNY",
                "evidence_ref": "pilot-invoice-001",
            })
            self.assertEqual(recorded.status_code, 201, recorded.text)
            self.assertEqual(recorded.headers["cache-control"], "no-store")
            self.assertEqual(recorded.json()["amount_micros"], 12_500_000)
            self.assertEqual(client.post("/v1/costs", json={
                "category": "model", "amount": "12.1234567", "currency": "CNY",
                "evidence_ref": "invalid",
            }).status_code, 422)
            dashboard = client.get("/app")
            self.assertIn("pilot-invoice-001", dashboard.text)
            self.assertIn("CNY 12.5", dashboard.text)

            bundle = client.get("/v1/pilot/export")
            self.assertEqual(bundle.status_code, 200, repr(bundle.content[:200]))
            with zipfile.ZipFile(io.BytesIO(bundle.content)) as archive:
                self.assertEqual(set(archive.namelist()),
                                 {"audit.json", "costs.json", "manifest.json", "summary.json"})
                manifest = json.loads(archive.read("manifest.json"))
                for item in manifest["files"]:
                    self.assertEqual(hashlib.sha256(archive.read(item["path"])).hexdigest(),
                                     item["sha256"])
                bundle_fingerprint = manifest.pop("fingerprint")
                manifest_canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")).encode("utf-8")
                self.assertEqual(hashlib.sha256(manifest_canonical).hexdigest(), bundle_fingerprint)
                self.assertEqual(manifest["efficiency_claim_status"], "NOT_MEASURED")
            self.assertEqual(bundle.headers["x-yanxu-evidence-fingerprint"], bundle_fingerprint)

            logout = client.post("/logout", follow_redirects=False)
            self.assertEqual(logout.status_code, 303)
            self.assertEqual(client.get("/app", follow_redirects=False).status_code, 303)
            viewer_token = self.store.issue_token(self.owner, "pilot-viewer", "viewer")["token"]
            self.assertEqual(client.post("/v1/session", json={"token": viewer_token}).status_code, 200)
            viewer_costs = client.get("/v1/costs")
            self.assertEqual(viewer_costs.headers["cache-control"], "no-store")
            self.assertEqual(viewer_costs.json()["items"][0]["evidence_ref"], "pilot-invoice-001")
            self.assertEqual(client.post("/v1/costs", json={
                "category": "ci", "amount": "1", "currency": "CNY",
                "evidence_ref": "viewer-must-not-write",
            }).status_code, 403)
            client.post("/logout", follow_redirects=False)
            other_login = client.post("/v1/session", json={"token": self.other_token})
            self.assertEqual(other_login.status_code, 200)
            other_dashboard = client.get("/app")
            self.assertNotIn("Pilot Dashboard", other_dashboard.text)
            self.assertNotIn("pilot-invoice-001", other_dashboard.text)
            self.assertEqual(client.get("/v1/audit/export").json()["organization_slug"],
                             self.other.organization_slug)
