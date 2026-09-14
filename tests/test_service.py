import os
import unittest
import uuid

from yanxu.service import (AuthenticationError, AuthorizationError, NotFoundError, PostgresStore,
                           ValidationError, normalize_snapshot, token_hash)


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

        second_owner = self.store.issue_token(self.owner, "backup-owner", "owner")
        backup_owner = self.store.authenticate(second_owner["token"])
        self.store.revoke_token(backup_owner, issued["id"])
        with self.assertRaises(AuthenticationError):
            self.store.authenticate(issued["token"])

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
