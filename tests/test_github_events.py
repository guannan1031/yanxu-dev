import hashlib
import hmac
import json
import os
import unittest
import uuid

from yanxu.github_events import GitHubEventStore, normalize_event, verify_signature
from yanxu.service import AuthenticationError, ConflictError


SECRET = "github-webhook-test-secret-0123456789"


def signature(body):
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def event_payload(installation_id, action="opened"):
    return {
        "action": action,
        "installation": {"id": installation_id, "account": {"login": "example"}},
        "repository": {"full_name": "example/orders"},
        "sender": {"login": "developer"},
        "number": 7,
        "pull_request": {
            "title": "must not persist",
            "body": "must not persist",
            "state": "open",
            "draft": False,
            "merged": False,
            "head": {"sha": "a" * 40},
            "base": {"sha": "b" * 40},
        },
    }


class GitHubEventContractTests(unittest.TestCase):
    def test_official_signature_vector(self):
        verify_signature("It's a Secret to Everybody", b"Hello, World!",
                         "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17")

    def test_bad_signature_is_rejected(self):
        with self.assertRaises(AuthenticationError):
            verify_signature(SECRET, b"{}", "sha256=bad")

    def test_pull_request_normalization_excludes_title_and_body(self):
        result = normalize_event("pull_request", event_payload(123))
        self.assertNotIn("title", json.dumps(result))
        self.assertNotIn("must not persist", json.dumps(result))
        self.assertEqual(result["pull_request"]["head_sha"], "a" * 40)


@unittest.skipUnless(os.environ.get("YANXU_TEST_DATABASE_URL"),
                     "YANXU_TEST_DATABASE_URL is required for PostgreSQL integration tests")
class GitHubEventPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = GitHubEventStore(os.environ["YANXU_TEST_DATABASE_URL"])
        cls.store.initialize()

    def setUp(self):
        suffix = uuid.uuid4().hex[:10]
        self.token = f"github-{suffix}-012345678901234567890123456789"
        self.owner = self.store.bootstrap(f"github-{suffix}", "GitHub Team", self.token)
        self.installation_id = int(uuid.uuid4().int % 2_000_000_000) + 1
        self.store.register_installation(self.owner, self.installation_id, "example")

    def test_signed_delivery_is_deduplicated_processed_and_org_scoped(self):
        body = json.dumps(event_payload(self.installation_id)).encode()
        first = self.store.receive(SECRET, signature(body), "delivery-" + uuid.uuid4().hex,
                                   "pull_request", body)
        duplicate = self.store.receive(SECRET, signature(body), first["delivery_id"],
                                       "pull_request", body)
        self.assertEqual(first["status"], "PENDING")
        self.assertTrue(duplicate["duplicate"])
        processed = None
        for _ in range(20):
            candidate = self.store.process_next()
            if candidate is None or candidate["delivery_id"] == first["delivery_id"]:
                processed = candidate
                break
        self.assertIsNotNone(processed)
        self.assertEqual(processed["status"], "COMPLETED")
        deliveries = self.store.list_deliveries(self.owner)
        matching = [item for item in deliveries if item["delivery_id"] == first["delivery_id"]]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["attempt_count"], 1)
        self.assertNotIn("must not persist", json.dumps(matching[0]))

        other_suffix = uuid.uuid4().hex[:10]
        other_token = f"other-github-{other_suffix}-012345678901234567890123456789"
        other = self.store.bootstrap("other-" + other_suffix, "Other", other_token)
        self.assertEqual(self.store.list_deliveries(other), [])
        self.assertEqual(self.store.list_installations(other), [])
        with self.assertRaises(ConflictError):
            self.store.register_installation(other, self.installation_id, "other-account")
        self.assertEqual(self.store.list_installations(self.owner)[0]["account_login"], "example")

    def test_suspend_revokes_installation_and_later_events_are_ignored(self):
        suspend = event_payload(self.installation_id, "suspend")
        body = json.dumps(suspend).encode()
        self.store.receive(SECRET, signature(body), "suspend-" + uuid.uuid4().hex,
                           "installation", body)
        self.assertEqual(self.store.list_installations(self.owner)[0]["status"], "REVOKED")

        later = json.dumps(event_payload(self.installation_id)).encode()
        result = self.store.receive(SECRET, signature(later), "later-" + uuid.uuid4().hex,
                                    "pull_request", later)
        self.assertEqual(result["status"], "IGNORED_REVOKED")
        self.assertFalse(result["accepted"])

    def test_http_webhook_verifies_signature_and_queues_delivery(self):
        from fastapi.testclient import TestClient
        from yanxu.service_api import create_app

        app = create_app(os.environ["YANXU_TEST_DATABASE_URL"], github_webhook_secret=SECRET)
        headers = {"Authorization": f"Bearer {self.token}"}
        with TestClient(app) as client:
            installation_id = self.installation_id + 3_000_000_000
            registered = client.post("/v1/github/installations", headers=headers, json={
                "github_installation_id": installation_id, "account_login": "example-api",
            })
            self.assertEqual(registered.status_code, 201, registered.text)
            body = json.dumps(event_payload(installation_id)).encode()
            webhook_headers = {
                "X-Hub-Signature-256": signature(body),
                "X-GitHub-Delivery": "api-" + uuid.uuid4().hex,
                "X-GitHub-Event": "pull_request",
                "Content-Type": "application/json",
            }
            accepted = client.post("/webhooks/github", headers=webhook_headers, content=body)
            self.assertEqual(accepted.status_code, 202, accepted.text)
            self.assertEqual(accepted.json()["status"], "PENDING")
            bad = client.post("/webhooks/github", headers={**webhook_headers,
                              "X-Hub-Signature-256": "sha256=bad",
                              "X-GitHub-Delivery": "bad-" + uuid.uuid4().hex}, content=body)
            self.assertEqual(bad.status_code, 401, bad.text)
            deliveries = client.get("/v1/github/deliveries", headers=headers)
            self.assertEqual(deliveries.status_code, 200)
            self.assertTrue(any(item["delivery_id"] == accepted.json()["delivery_id"]
                                for item in deliveries.json()["items"]))
