"""Plan and explicitly create a bounded GitHub draft pull request."""

from __future__ import annotations

import html
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

from .core import ReviewError, now, redact, validate_repo


MAX_BODY_BYTES = 20_000


def _run(args: list[str], cwd: Path, timeout: int = 60) -> str:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewError(f"{args[0]} unavailable or timed out") from exc
    if result.returncode:
        raise ReviewError(redact(result.stderr[-1500:]) or f"{args[0]} failed")
    return result.stdout


def _git(repo: Path, *args: str) -> str:
    return _run(["git", *args], repo)


def _gh(repo: Path, *args: str) -> str:
    return _run(["gh", *args], repo)


def _push(repo: Path, head: str) -> None:
    _git(repo, "push", "-u", "origin", head)


def _safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or value != path.as_posix() or any(part in ("", ".", "..") for part in path.parts):
        raise ReviewError(f"Unsafe allowed path: {value}")
    if path.parts[0] == ".git" or path.name == ".env" or path.suffix in (".key", ".pem"):
        raise ReviewError(f"Sensitive path cannot be published: {value}")
    return value


def _remote_matches(remote: str, github_repo: str) -> bool:
    value = remote.strip().removesuffix(".git")
    expected = github_repo.lower()
    patterns = (
        r"https://github\.com/([^/]+/[^/]+)",
        r"git@github\.com:([^/]+/[^/]+)",
        r"ssh://git@github\.com/([^/]+/[^/]+)",
    )
    return any((match := re.fullmatch(pattern, value, re.I)) and match.group(1).lower() == expected
               for pattern in patterns)


def _read_body(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ReviewError("--body-file must be a regular UTF-8 file")
    raw = path.read_bytes()
    if len(raw) > MAX_BODY_BYTES:
        raise ReviewError("PR body exceeds 20000 bytes")
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReviewError("--body-file must be a regular UTF-8 file") from exc
    if redact(body) != body:
        raise ReviewError("PR body appears to contain a credential")
    return body


def inspect_draft_pr(repo: Path, github_repo: str, base: str, head: str, allow_paths: list[str],
                     title: str, body_file: Path) -> dict:
    root = repo.resolve()
    validate_repo(github_repo)
    if not root.is_dir() or Path(_git(root, "rev-parse", "--show-toplevel").strip()).resolve() != root:
        raise ReviewError("--repo must be the root of an existing Git repository")
    for branch in (base, head):
        _git(root, "check-ref-format", "--branch", branch)
    if base == head:
        raise ReviewError("--head must differ from --base")
    if _git(root, "branch", "--show-current").strip() != head:
        raise ReviewError("Current branch must match --head")
    if _git(root, "status", "--porcelain").strip():
        raise ReviewError("Commit or remove all working tree changes before publishing")

    title = title.strip()
    if not title or len(title) > 256 or redact(title) != title:
        raise ReviewError("PR title must be 1-256 characters and contain no credential")
    body_file = body_file.resolve()
    body = _read_body(body_file)
    approved = sorted({_safe_path(value) for value in allow_paths})
    if not approved:
        raise ReviewError("At least one --allow-path is required")

    base_sha = _git(root, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
    head_sha = _git(root, "rev-parse", "--verify", "HEAD^{commit}").strip()
    if int(_git(root, "rev-list", "--count", f"{base}..HEAD").strip()) < 1:
        raise ReviewError("Feature branch has no commits ahead of base")
    changed = sorted(filter(None, _git(root, "diff", "--name-only", "-z", f"{base}...HEAD").split("\0")))
    if not changed:
        raise ReviewError("Feature branch has no changed files")
    for value in changed:
        _safe_path(value)
    if changed != approved:
        raise ReviewError(f"Changed paths must exactly match --allow-path: {changed}")

    remote = _git(root, "remote", "get-url", "origin").strip()
    if not _remote_matches(remote, github_repo):
        raise ReviewError("origin does not match --github-repo")
    try:
        remote_base = json.loads(_gh(root, "api", f"repos/{github_repo}/git/ref/heads/{base}"))["object"]["sha"]
        existing = json.loads(_gh(root, "pr", "list", "--repo", github_repo, "--head", head,
                                  "--state", "open", "--json", "number,url,isDraft"))
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ReviewError("GitHub returned invalid draft PR planning data") from exc
    if remote_base != base_sha:
        raise ReviewError("Local base does not match the current GitHub base commit")
    if existing:
        raise ReviewError(f"An open PR already exists for --head: {existing[0].get('url', 'unknown URL')}")

    return {
        "schema_version": 1,
        "kind": "yanxu.draft_pr_plan",
        "created_at": now(),
        "status": "READY_TO_CREATE_DRAFT",
        "repo": str(root),
        "github_repo": github_repo,
        "base": base,
        "head": head,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "title": title,
        "body_file": str(body_file),
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "changed_paths": changed,
        "approved_paths": approved,
        "remote_modified": False,
        "draft_pr_created": False,
        "auto_merge_allowed": False,
    }


def _render_markdown(result: dict) -> str:
    lines = ["# Yanxu Draft PR Publisher", "", f"- 状态：`{result['status']}`",
             f"- 仓库：`{result['github_repo']}`", f"- 分支：`{result['head']}` → `{result['base']}`",
             f"- HEAD：`{result['head_sha']}`", "", "## Confirmed paths", ""]
    lines.extend(f"- `{path}`" for path in result["approved_paths"])
    lines.extend(["", "## Boundary", "", f"- 远端修改：`{str(result['remote_modified']).lower()}`",
                  f"- Draft PR：`{str(result['draft_pr_created']).lower()}`",
                  "- 自动合并授权：`false`", ""])
    if result.get("pr_url"):
        lines.extend([f"- PR：{result['pr_url']}", ""])
    return "\n".join(lines)


def _render_html(result: dict) -> str:
    paths = "".join(f"<li><code>{html.escape(path)}</code></li>" for path in result["approved_paths"])
    link = f"<p><a href=\"{html.escape(result['pr_url'], quote=True)}\">Open Draft PR</a></p>" if result.get("pr_url") else ""
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Yanxu Draft PR Publisher</title><link rel=\"icon\" href=\"data:,\"><style>body{{font-family:system-ui,sans-serif;background:#f4f7fb;color:#172033;margin:0;padding:28px}}main{{max-width:860px;margin:auto}}header,section{{background:#fff;border:1px solid #dfe6ef;border-radius:14px;padding:22px;margin-bottom:18px}}.status{{font-size:32px;font-weight:800;color:#087f5b}}code{{overflow-wrap:anywhere}}.note{{color:#5d6879}}a{{color:#087f5b}}@media(max-width:560px){{body{{padding:14px}}}}</style></head><body><main><header><div class=\"note\">Explicit remote mutation gate</div><h1>Draft PR Publisher</h1><div class=\"status\">{html.escape(result['status'])}</div><p>{html.escape(result['head'])} → {html.escape(result['base'])}</p>{link}</header><section><h2>已确认文件</h2><ul>{paths}</ul></section><section><h2>边界</h2><p>远端修改：{str(result['remote_modified']).lower()} · Draft PR：{str(result['draft_pr_created']).lower()} · 自动合并授权：false</p></section></main></body></html>"""


def publish_draft_pr(repo: Path, github_repo: str, base: str, head: str, allow_paths: list[str], title: str,
                     body_file: Path, output: Path, confirm_create: bool = False) -> dict:
    plan = inspect_draft_pr(repo, github_repo, base, head, allow_paths, title, body_file)
    folder = output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    result = dict(plan)
    try:
        if confirm_create:
            result["status"] = "PUSHING_BRANCH"
            _write(folder, result)
            if hashlib.sha256(_read_body(body_file.resolve()).encode()).hexdigest() != plan["body_sha256"]:
                raise ReviewError("PR body changed after planning; nothing was pushed")
            try:
                current_base = json.loads(_gh(repo.resolve(), "api",
                                              f"repos/{github_repo}/git/ref/heads/{base}"))["object"]["sha"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise ReviewError("GitHub returned invalid base data before publishing") from exc
            if current_base != plan["base_sha"]:
                raise ReviewError("GitHub base changed after planning; nothing was pushed")
            _push(repo.resolve(), head)
            result["remote_modified"] = True
            result["remote_branch_pushed"] = True
            if _git(repo.resolve(), "rev-parse", "HEAD^{commit}").strip() != plan["head_sha"]:
                raise ReviewError("HEAD changed while publishing; Draft PR was not created")
            url = _gh(repo.resolve(), "pr", "create", "--draft", "--repo", github_repo, "--base", base,
                      "--head", head, "--title", title.strip(), "--body-file", str(body_file.resolve())).strip()
            if not re.fullmatch(r"https://github\.com/[^/]+/[^/]+/pull/\d+", url):
                raise ReviewError("GitHub did not return a valid pull request URL")
            result.update(status="DRAFT_PR_CREATED", draft_pr_created=True, pr_url=url)
        _write(folder, result)
        return {"status": result["status"], "plan": str(folder / "draft-pr.json"),
                "report": str(folder / "draft-pr.html"), "pr_url": result.get("pr_url"),
                "remote_modified": result["remote_modified"], "auto_merge_allowed": False}
    except ReviewError as exc:
        result.update(status="FAILED", error=str(exc))
        _write(folder, result)
        raise


def _write(folder: Path, result: dict) -> None:
    (folder / "draft-pr.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "draft-pr.md").write_text(_render_markdown(result), encoding="utf-8")
    (folder / "draft-pr.html").write_text(_render_html(result), encoding="utf-8")
