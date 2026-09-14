"""Explicit client for publishing normalized local evidence to a private service."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .core import ReviewError, redact
from .team import load_github_snapshot


def publish_snapshot(server: str, workspace_id: str, snapshot_path: Path,
                     token_env: str = "YANXU_API_TOKEN", opener=None) -> dict:
    parsed = urllib.parse.urlparse(server)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ReviewError("Private service URL must use http or https")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ReviewError("Remote private service URLs must use https")
    token = os.environ.get(token_env)
    if not token or len(token) < 24:
        raise ReviewError(f"A private service token is required in environment variable {token_env}")
    snapshot_path = snapshot_path.resolve()
    try:
        if snapshot_path.stat().st_size > 2_000_000:
            raise ReviewError("Team GitHub snapshot exceeds the 2 MB client limit")
    except OSError as exc:
        raise ReviewError("Could not read the team GitHub snapshot") from exc
    payload = load_github_snapshot(snapshot_path)
    target = f"{server.rstrip('/')}/v1/workspaces/{workspace_id}/snapshots"
    request = urllib.request.Request(
        target,
        method="POST",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        response = (opener or urllib.request.urlopen)(request, timeout=30)
        with response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "request rejected")
        except Exception:
            detail = "request rejected"
        raise ReviewError(f"Private service rejected the snapshot: {redact(str(detail))}") from exc
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ReviewError("Private service could not be reached or returned invalid JSON") from exc
    return {
        "status": "PUBLISHED",
        "server": f"{parsed.scheme}://{parsed.netloc}",
        "workspace_id": workspace_id,
        "snapshot_id": result.get("id"),
        "fingerprint": result.get("fingerprint"),
        "created": result.get("created"),
        "token_source": token_env,
        "credentials_persisted": False,
        "remote_modified": True,
    }
