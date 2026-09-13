from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Any


class ReviewError(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def redact(text: str) -> str:
    # Best-effort filtering; reports for private repositories remain private.
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]{15,}|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{16,})\b", "[REDACTED]", text)
    text = re.sub(r"(?im)((?:authorization|api[_-]?key|password|token)\s*[:=]\s*)([^\s,;]+)", r"\1[REDACTED]", text)
    return text


def validate_repo(repo: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ReviewError("Use a GitHub owner/repository name, not a URL or shell command.")
    return repo


def command(args: list[str], timeout: int = 60, env: dict | None = None) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewError(f"{args[0]} unavailable or timed out") from exc
    if result.returncode:
        raise ReviewError(redact(result.stderr[-1500:]) or f"{args[0]} failed")
    return result.stdout


class GitHub:
    def api(self, endpoint: str, pages: bool = False):
        args = ["gh", "api", endpoint]
        if pages:
            args += ["--paginate", "--slurp"]
        try:
            value = json.loads(command(args))
        except json.JSONDecodeError as exc:
            raise ReviewError("GitHub returned invalid JSON") from exc
        return [item for page in value for item in page] if pages else value

    def failed_logs(self, repo: str, run_id: str) -> str:
        return command(["gh", "run", "view", run_id, "--repo", repo, "--log-failed"], 60)


def pr_summary(pr: dict) -> dict:
    return {
        "number": pr["number"], "url": pr["html_url"], "title": redact(pr["title"]),
        "body": redact(pr.get("body") or "")[:10000], "state": pr["state"],
        "draft": pr.get("draft", False), "merged": pr.get("merged", False),
        "head_sha": pr["head"]["sha"], "base_sha": pr["base"]["sha"],
        "base_ref": pr["base"]["ref"], "changed_files": pr["changed_files"],
        "mergeable": pr.get("mergeable"), "mergeable_state": pr.get("mergeable_state"),
    }


def binding(snapshot: dict) -> str:
    """Bind evidence to code, checks and reviews; fetch timestamps are irrelevant."""
    pr_fields = ["number", "head_sha", "base_sha", "state", "draft", "merged"]
    if snapshot.get("schema_version", 1) >= 2:
        pr_fields += ["title", "body"]
    return digest({
        "repo": snapshot["repo"],
        "pr": {k: snapshot["pr"][k] for k in pr_fields},
        "checks": snapshot["checks"], "statuses": snapshot["statuses"],
        "reviews": snapshot["reviews"], "files": snapshot["files"],
        "complete": snapshot["complete"],
    })


def capture(repo: str, number: int, gh: GitHub | None = None, include_logs: bool = False) -> dict:
    validate_repo(repo)
    if number < 1:
        raise ReviewError("PR number must be positive")
    gh = gh or GitHub()
    root = f"repos/{repo}"
    start = time.monotonic()
    pr = pr_summary(gh.api(f"{root}/pulls/{number}"))
    sha = pr["head_sha"]
    raw_files = gh.api(f"{root}/pulls/{number}/files?per_page=100", pages=True)
    # GitHub omits patches for binary/very large files. Never treat that as reviewed.
    files = [{"path": x["filename"], "status": x["status"], "patch": redact(x.get("patch", ""))[:12000],
              "patch_complete": bool(x.get("patch")) and len(x.get("patch", "")) <= 12000,
              "additions": x.get("additions", 0), "deletions": x.get("deletions", 0)} for x in raw_files[:100]]
    raw_checks = gh.api(f"{root}/commits/{sha}/check-runs?per_page=100&filter=latest")
    checks = sorted([{"id": x["id"], "name": x["name"], "status": x["status"],
                      "conclusion": x.get("conclusion"), "url": x.get("html_url", ""),
                      "details_url": x.get("details_url", ""), "app": (x.get("app") or {}).get("slug", "unknown"),
                      "summary": redact((x.get("output") or {}).get("summary") or "")[:3000]}
                     for x in raw_checks["check_runs"]], key=lambda x: x["id"])
    raw_status = gh.api(f"{root}/commits/{sha}/status?per_page=100")
    statuses = sorted([{"id": x["id"], "context": x["context"], "state": x["state"],
                        "url": x.get("target_url"), "description": redact(x.get("description") or "")}
                       for x in raw_status["statuses"]], key=lambda x: x["id"])
    reviews = sorted([{"id": x["id"], "author": x["user"]["login"], "state": x["state"],
                       "commit_id": x.get("commit_id")}
                      for x in gh.api(f"{root}/pulls/{number}/reviews?per_page=100", pages=True)], key=lambda x: x["id"])
    warnings = []
    complete = len(files) == pr["changed_files"] and all(x["patch_complete"] for x in files)
    if raw_checks.get("total_count", 0) > len(checks) or raw_status.get("total_count", 0) > len(statuses):
        complete = False
        warnings.append("Check/status count exceeds one page; evidence incomplete.")
    logs = []
    if include_logs:
        ids = set()
        for check in checks:
            match = re.fullmatch(r"https://github\.com/" + re.escape(repo) + r"/actions/runs/(\d+)(?:/job/\d+)?", check["details_url"])
            if check["conclusion"] in ("failure", "timed_out", "action_required") and match:
                ids.add(match[1])
        for run_id in sorted(ids)[:2]:
            try:
                log = gh.failed_logs(repo, run_id)
                logs.append({"run_id": run_id, "excerpt": redact(log[-14000:]), "truncated": len(log) > 14000})
            except ReviewError:
                warnings.append(f"Could not read failed logs for run {run_id}.")
    end_pr = pr_summary(gh.api(f"{root}/pulls/{number}"))
    if any(pr[k] != end_pr[k] for k in ("head_sha", "base_sha", "state", "merged", "draft", "title", "body")):
        raise ReviewError("PR changed while collecting evidence; run again.")
    if not complete:
        warnings.append("Some diffs/checks are missing or truncated; manual inspection required.")
    snapshot = {"schema_version": 2, "repo": repo, "pr": pr, "files": files, "checks": checks,
                "statuses": statuses, "reviews": reviews, "logs": logs, "complete": complete,
                "warnings": warnings, "captured_at": now(), "collection_seconds": round(time.monotonic() - start, 3)}
    snapshot["binding"] = binding(snapshot)
    return snapshot


def assess(snapshot: dict) -> dict:
    blockers = []
    pr = snapshot["pr"]
    if pr["state"] != "open" or pr["merged"]:
        blockers.append("PR is not open.")
    if pr["draft"]:
        blockers.append("Draft PR.")
    if not snapshot["complete"]:
        blockers.append("Incomplete diff/check evidence.")
    if not snapshot["checks"] and not snapshot["statuses"]:
        blockers.append("No CI evidence found.")
    for check in snapshot["checks"]:
        if check["status"] != "completed" or check["conclusion"] != "success":
            blockers.append(f"Check {check['name']}: {check['conclusion'] or check['status']}")
    for status in snapshot["statuses"]:
        if status["state"] != "success":
            blockers.append(f"Status {status['context']}: {status['state']}")
    if pr.get("mergeable") is False:
        blockers.append("GitHub reports merge conflicts.")
    risky = [f["path"] for f in snapshot["files"] if f["path"].startswith((".github/", "infra/", "migrations/"))
             or f["path"].endswith((".pem", ".key"))]
    latest = {}
    for review in snapshot["reviews"]:
        if review["state"] != "COMMENTED":
            latest[review["author"]] = review
    current_approvals = [r["author"] for r in latest.values() if r["state"] == "APPROVED" and r["commit_id"] == pr["head_sha"]]
    for r in latest.values():
        if r["state"] == "CHANGES_REQUESTED":
            blockers.append(f"Changes requested by {r['author']}.")
    return {"status": "BLOCKED" if blockers else "MANUAL_REVIEW", "blockers": blockers,
            "sensitive_paths": risky, "current_head_approvals": current_approvals,
            "auto_merge_allowed": False,
            "policy_note": "Advisory only. Required checks, CODEOWNERS and GitHub rules are not fully evaluated. Never an approval or merge authorization."}


def compare(saved: dict, current: dict) -> dict:
    changes = []
    for key in ("head_sha", "base_sha", "state", "draft", "merged", "title", "body"):
        if saved["pr"][key] != current["pr"][key]:
            changes.append(key)
    same_schema = dict(current, schema_version=saved.get("schema_version", 1))
    if binding(saved) != binding(same_schema) and not changes:
        changes.append("checks_reviews_or_diff")
    return {"status": "STALE" if changes else "UNCHANGED", "changes": changes,
            "verified_at": now(), "current_binding": binding(current), "auto_merge_allowed": False}
