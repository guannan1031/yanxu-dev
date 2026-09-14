import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yanxu.core import ReviewError
from yanxu.service_client import publish_snapshot
from tests.test_service import sample_snapshot


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({"id": "snapshot-1", "fingerprint": "a" * 64,
                           "created": True}).encode()


class ServiceClientTests(unittest.TestCase):
    def test_publish_uses_environment_token_without_returning_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "snapshot.json"
            path.write_text(json.dumps(sample_snapshot()), encoding="utf-8")
            captured = {}

            def open_request(request, timeout):
                captured["authorization"] = request.headers["Authorization"]
                captured["timeout"] = timeout
                return FakeResponse()

            token = "private-test-token-012345678901234567890"
            with patch.dict(os.environ, {"YANXU_API_TOKEN": token}):
                result = publish_snapshot("http://127.0.0.1:8080", "workspace-1", path,
                                          opener=open_request)
            self.assertEqual(captured["authorization"], f"Bearer {token}")
            self.assertNotIn(token, json.dumps(result))
            self.assertFalse(result["credentials_persisted"])

    def test_remote_plain_http_is_rejected_before_reading_credentials(self):
        with self.assertRaisesRegex(ReviewError, "https"):
            publish_snapshot("http://example.com", "workspace-1", Path("missing.json"))
