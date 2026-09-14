"""Keep an explicit local registry of authorized Yanxu project artifacts."""

from __future__ import annotations

import html
import hashlib
import json
import uuid
import zipfile
from pathlib import Path

from .board import build_board
from .core import GitHub, ReviewError, assess, capture, now, redact, validate_repo
from .policy import load_policy


def _text(value: str, label: str, limit: int = 120) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ReviewError(f"{label} must be between 1 and {limit} characters")
    return redact(value.strip())


def create_workspace(name: str) -> dict:
    return {
        "schema_version": 1,
        "kind": "yanxu.team_workspace",
        "name": _text(name, "Workspace name"),
        "projects": [],
        "created_at": now(),
        "updated_at": now(),
    }


def load_workspace(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError("Could not read a valid team workspace JSON") from exc
    required = {"schema_version", "kind", "name", "projects", "created_at", "updated_at"}
    if not isinstance(payload, dict) or set(payload) != required or payload["schema_version"] != 1 or payload["kind"] != "yanxu.team_workspace":
        raise ReviewError("Team workspace schema is unsupported")
    _text(payload["name"], "Workspace name")
    if not isinstance(payload["projects"], list) or len(payload["projects"]) > 100:
        raise ReviewError("Team workspace projects are malformed")
    seen = set()
    for project in payload["projects"]:
        if not isinstance(project, dict) or set(project) not in (
            {"id", "runs", "measurements"},
            {"id", "runs", "measurements", "policy"},
            {"id", "runs", "measurements", "policy", "github"},
        ):
            raise ReviewError("Team workspace project is malformed")
        project_id = _text(project["id"], "Project id")
        if project_id in seen or not isinstance(project["runs"], str) or not project["runs"]:
            raise ReviewError("Team workspace project is malformed")
        if project["measurements"] is not None and not isinstance(project["measurements"], str):
            raise ReviewError("Team workspace project is malformed")
        if project.get("policy") is not None and not isinstance(project["policy"], str):
            raise ReviewError("Team workspace project is malformed")
        github = project.get("github")
        if github is not None:
            if not isinstance(github, dict) or set(github) != {"repo", "pr"}:
                raise ReviewError("Team workspace GitHub target is malformed")
            validate_repo(github["repo"])
            if not isinstance(github["pr"], int) or isinstance(github["pr"], bool) or github["pr"] < 1:
                raise ReviewError("Team workspace GitHub target is malformed")
        seen.add(project_id)
    return payload


def write_workspace(path: Path, workspace: dict) -> None:
    output = path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(workspace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)
    except OSError as exc:
        raise ReviewError("Could not write team workspace JSON") from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def add_project(path: Path, project_id: str, runs: Path, measurements: Path | None = None,
                policy: Path | None = None, github_repo: str | None = None,
                github_pr: int | None = None) -> dict:
    workspace = load_workspace(path)
    project_id = _text(project_id, "Project id")
    if any(item["id"] == project_id for item in workspace["projects"]):
        raise ReviewError("Project id already exists in this team workspace")
    runs = runs.resolve()
    if not runs.is_dir():
        raise ReviewError("Project runs directory must exist")
    if measurements is not None:
        measurements = measurements.resolve()
        if not measurements.is_file():
            raise ReviewError("Project measurement file must exist")
    if policy is not None:
        policy = policy.resolve()
        if not policy.is_file():
            raise ReviewError("Project policy file must exist")
        load_policy(policy)
    if (github_repo is None) != (github_pr is None):
        raise ReviewError("GitHub repository and PR must be supplied together")
    github = None
    if github_repo is not None:
        github = {"repo": validate_repo(github_repo), "pr": github_pr}
        if not isinstance(github_pr, int) or isinstance(github_pr, bool) or github_pr < 1:
            raise ReviewError("GitHub PR number must be positive")
    workspace["projects"].append({
        "id": project_id,
        "runs": str(runs),
        "measurements": str(measurements) if measurements is not None else None,
        "policy": str(policy) if policy is not None else None,
        "github": github,
    })
    workspace["updated_at"] = now()
    write_workspace(path, workspace)
    return workspace


def set_github_target(path: Path, project_id: str, github_repo: str, github_pr: int) -> dict:
    workspace = load_workspace(path)
    project_id = _text(project_id, "Project id")
    repo = validate_repo(github_repo)
    if not isinstance(github_pr, int) or isinstance(github_pr, bool) or github_pr < 1:
        raise ReviewError("GitHub PR number must be positive")
    for project in workspace["projects"]:
        if project["id"] == project_id:
            project["github"] = {"repo": repo, "pr": github_pr}
            project.setdefault("policy", None)
            workspace["updated_at"] = now()
            write_workspace(path, workspace)
            return workspace
    raise ReviewError("Project id is not registered in this team workspace")


def _policy_summary(project: dict) -> dict:
    policy_path = project.get("policy")
    if policy_path is None:
        return {"status": "NOT_CONFIGURED", "sha256": None, "allowed_path_count": 0}
    try:
        policy = load_policy(Path(policy_path))
    except ReviewError:
        return {"status": "UNAVAILABLE", "sha256": None, "allowed_path_count": 0}
    fingerprint = hashlib.sha256(
        json.dumps(policy, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {"status": "CONFIGURED", "sha256": fingerprint,
            "allowed_path_count": len(policy["allowed_paths"])}


def _github_summary(project: dict, synced: dict | None) -> dict:
    target = project.get("github")
    if target is None:
        return {"status": "NOT_CONFIGURED", "repo": None, "pr": None, "ci": "NOT_SYNCED",
                "head_sha": None, "binding": None, "assessment": None, "error": None}
    if synced is None:
        return {"status": "NOT_SYNCED", "repo": target["repo"], "pr": target["pr"],
                "ci": "NOT_SYNCED", "head_sha": None, "binding": None,
                "assessment": None, "error": None}
    return synced


def sync_team_github(path: Path, gh: GitHub | None = None) -> dict:
    workspace = load_workspace(path)
    projects = []
    for project in workspace["projects"]:
        target = project.get("github")
        if target is None:
            projects.append(_github_summary(project, None))
            projects[-1]["id"] = project["id"]
            continue
        try:
            snapshot = capture(target["repo"], target["pr"], gh=gh)
            decision = assess(snapshot)
            check_total = len(snapshot["checks"]) + len(snapshot["statuses"])
            check_passed = sum(
                item["status"] == "completed" and item["conclusion"] == "success"
                for item in snapshot["checks"]
            ) + sum(item["state"] == "success" for item in snapshot["statuses"])
            projects.append({
                "id": project["id"], "status": "SYNCED", "repo": target["repo"],
                "pr": target["pr"], "ci": "PASSING" if check_total and check_total == check_passed else "BLOCKED",
                "checks_total": check_total, "checks_passed": check_passed,
                "head_sha": snapshot["pr"]["head_sha"], "base_sha": snapshot["pr"]["base_sha"],
                "pr_state": snapshot["pr"]["state"], "draft": snapshot["pr"]["draft"],
                "merged": snapshot["pr"]["merged"], "binding": snapshot["binding"],
                "assessment": decision["status"], "blockers": decision["blockers"],
                "auto_merge_allowed": False, "captured_at": snapshot["captured_at"], "error": None,
            })
        except ReviewError as exc:
            projects.append({
                "id": project["id"], "status": "UNAVAILABLE", "repo": target["repo"],
                "pr": target["pr"], "ci": "UNAVAILABLE", "head_sha": None,
                "binding": None, "assessment": None, "error": redact(str(exc)),
            })
    configured = sum(project.get("github") is not None for project in workspace["projects"])
    synced = sum(project["status"] == "SYNCED" for project in projects)
    unavailable = sum(project["status"] == "UNAVAILABLE" for project in projects)
    status = "NOT_CONFIGURED" if configured == 0 else "COMPLETED"
    if unavailable:
        status = "FAILED" if synced == 0 else "PARTIAL"
    return {
        "schema_version": 1, "kind": "yanxu.team_github_snapshot", "status": status,
        "created_at": now(), "workspace_name": workspace["name"], "projects": projects,
        "summary": {"registered_projects": len(projects), "configured_projects": configured,
                    "synced_projects": synced, "unavailable_projects": unavailable},
        "data_boundary": (
            "Uses GitHub read APIs for explicitly registered PRs. Stores normalized PR, commit, CI and "
            "assessment facts; excludes diffs, PR bodies and logs. It does not comment, approve, merge, or deploy."
        ),
        "remote_modified": False, "auto_merge_allowed": False,
    }


def load_github_snapshot(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError("Could not read a valid team GitHub snapshot JSON") from exc
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or payload.get("kind") != "yanxu.team_github_snapshot"
            or not isinstance(payload.get("projects"), list)):
        raise ReviewError("Team GitHub snapshot schema is unsupported")
    return payload


def build_team_board(path: Path, github_snapshot: dict | None = None) -> dict:
    workspace = load_workspace(path)
    github_projects = {}
    if github_snapshot is not None:
        if (not isinstance(github_snapshot, dict) or github_snapshot.get("schema_version") != 1
                or github_snapshot.get("kind") != "yanxu.team_github_snapshot"
                or github_snapshot.get("workspace_name") != workspace["name"]
                or not isinstance(github_snapshot.get("projects"), list)):
            raise ReviewError("Team GitHub snapshot does not match this workspace")
        github_projects = {item.get("id"): item for item in github_snapshot["projects"] if isinstance(item, dict)}
    projects = []
    for project in workspace["projects"]:
        policy = _policy_summary(project)
        github = _github_summary(project, github_projects.get(project["id"]))
        try:
            board = build_board(Path(project["runs"]), Path(project["measurements"]) if project["measurements"] else None)
            projects.append({
                "id": project["id"],
                "health": "AVAILABLE",
                "summary": board["summary"],
                "benchmark_status": board["benchmark"]["status"] if board["benchmark"] else "NOT_MEASURED",
                "policy": policy,
                "github": github,
                "error": None,
            })
        except ReviewError as exc:
            projects.append({
                "id": project["id"],
                "health": "UNAVAILABLE",
                "summary": {"recorded_runs": 0, "isolated_tests_passed": 0, "manual_review_pending": 0, "recorded_remote_writes": 0},
                "benchmark_status": "UNAVAILABLE",
                "policy": policy,
                "github": github,
                "error": redact(str(exc)),
            })
    return {
        "schema_version": 1,
        "kind": "yanxu.team_delivery_board",
        "created_at": now(),
        "workspace_name": workspace["name"],
        "projects": projects,
        "summary": {
            "registered_projects": len(projects),
            "available_projects": sum(item["health"] == "AVAILABLE" for item in projects),
            "recorded_runs": sum(item["summary"]["recorded_runs"] for item in projects),
            "isolated_tests_passed": sum(item["summary"]["isolated_tests_passed"] for item in projects),
            "manual_review_pending": sum(item["summary"]["manual_review_pending"] for item in projects),
            "github_configured_projects": sum(item["github"]["status"] != "NOT_CONFIGURED" for item in projects),
            "github_synced_projects": sum(item["github"]["status"] == "SYNCED" for item in projects),
            "github_blocked_projects": sum(item["github"].get("assessment") == "BLOCKED" for item in projects),
        },
        "measurement_boundary": "Efficiency percentages are not aggregated across projects because task scope differs. Open each project's board and use only its matched, quality-passing records.",
        "data_boundary": "Reads only explicitly registered local artifacts and optional normalized GitHub snapshot facts. It does not store GitHub diffs, PR bodies or logs, send artifacts, create PRs, merge, or deploy.",
    }


def export_team_bundle(path: Path, output: Path, github_snapshot: dict | None = None) -> dict:
    output = output.resolve()
    if output.suffix.lower() != ".zip":
        raise ReviewError("Team evidence bundle output must use a .zip filename")
    if output.exists():
        raise ReviewError("Team evidence bundle already exists; choose a new output path")

    board = build_team_board(path, github_snapshot)
    board_json = json.dumps(board, ensure_ascii=False, indent=2) + "\n"
    board_html = render_html(board)
    instructions = (
        "Yanxu pilot evidence bundle\n\n"
        "Review manifest.json, team-board.json, and team-board.html together.\n"
        "This bundle contains normalized delivery summaries and policy fingerprints only.\n"
        "It does not contain source code, raw run artifacts, credentials, or registered local paths.\n"
        "A READY state remains subject to human review and the repository's merge rules.\n"
    )
    files = {
        "team-board.json": board_json.encode("utf-8"),
        "team-board.html": board_html.encode("utf-8"),
        "README.txt": instructions.encode("utf-8"),
    }
    if github_snapshot is not None:
        files["team-github.json"] = (
            json.dumps(github_snapshot, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
    manifest = {
        "schema_version": 1,
        "kind": "yanxu.pilot_evidence_bundle",
        "created_at": board["created_at"],
        "workspace_name": board["workspace_name"],
        "summary": board["summary"],
        "projects": board["projects"],
        "files": [
            {"name": name, "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in files.items()
        ],
        "data_boundary": (
            "Contains normalized delivery summaries and policy fingerprints only; excludes source code, "
            "raw run artifacts, credentials, and registered local paths."
        ),
        "remote_modified": False,
        "auto_merge_allowed": False,
    }
    files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, content in files.items():
                bundle.writestr(name, content)
        with zipfile.ZipFile(temporary) as bundle:
            if set(bundle.namelist()) != set(files):
                raise ReviewError("Team evidence bundle verification failed")
        temporary.replace(output)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReviewError("Could not write team evidence bundle") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    return {"bundle": str(output), "projects": board["summary"]["registered_projects"],
            "files": sorted(files), "remote_modified": False}


def render_html(board: dict) -> str:
    summary = board["summary"]
    rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in (
            project["id"], project["health"], project["summary"]["recorded_runs"],
            project["summary"]["isolated_tests_passed"], project["summary"]["manual_review_pending"],
            project["policy"]["status"], project["github"]["status"], project["github"]["ci"],
            project["github"].get("assessment") or "—", project["benchmark_status"],
            project["error"] or project["github"].get("error") or "—",
        )) + "</tr>"
        for project in board["projects"]
    ) or "<tr><td colspan=\"11\">当前工作空间尚未登记项目。</td></tr>"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Yanxu Team Delivery Board</title><link rel="icon" href="data:,"><style>body{{margin:0;background:#f4f8f6;color:#102d27;font:15px/1.6 system-ui,-apple-system,"PingFang SC",sans-serif}}main{{max-width:1120px;margin:auto;padding:30px 20px 55px}}header,section{{background:#fff;border:1px solid #dce8e2;border-radius:14px;padding:22px;margin-bottom:18px}}header{{background:#102d27;color:#eafff2}}h1{{margin:0;font-size:29px}}h2{{font-size:18px;margin:0 0 8px}}.muted{{color:#638078}}header .muted{{color:#c0d9ce}}.cards{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin-top:20px}}.card{{background:#fff;border:1px solid #dce8e2;border-radius:11px;padding:16px}}.label{{font-size:12px;color:#638078;font-weight:700;text-transform:uppercase}}.value{{font-size:26px;font-weight:800;margin-top:3px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{text-align:left;padding:10px 7px;border-bottom:1px solid #e8f0ec;overflow-wrap:anywhere}}th{{color:#638078;font-size:12px}}.note{{background:#fff3d9;border:1px solid #efd79c;color:#714b08;padding:12px 14px;border-radius:8px}}@media(max-width:720px){{.cards{{grid-template-columns:repeat(2,1fr)}}table{{font-size:12px}}}}@media(max-width:450px){{.cards{{grid-template-columns:1fr}}main{{padding:16px 12px}}}}</style></head><body><main><header><div class="label" style="color:#bde7d0">PRIVATE TEAM ALPHA · LOCAL STATIC EXPORT</div><h1>{html.escape(board['workspace_name'])}</h1><p class="muted">Yanxu Team Delivery Board · 生成于 {html.escape(board['created_at'])}</p></header><div class="cards"><div class="card"><div class="label">登记项目</div><div class="value">{summary['registered_projects']}</div></div><div class="card"><div class="label">可用项目</div><div class="value">{summary['available_projects']}</div></div><div class="card"><div class="label">记录运行</div><div class="value">{summary['recorded_runs']}</div></div><div class="card"><div class="label">GitHub 已同步</div><div class="value">{summary['github_synced_projects']}</div></div><div class="card"><div class="label">待人工复核</div><div class="value">{summary['manual_review_pending']}</div></div></div><section><h2>项目交付状态</h2><table><thead><tr><th>项目</th><th>健康状态</th><th>运行</th><th>隔离测试通过</th><th>待复核</th><th>Policy</th><th>GitHub</th><th>CI</th><th>PR 判定</th><th>测量状态</th><th>说明</th></tr></thead><tbody>{rows}</tbody></table></section><section><h2>边界</h2><p class="note">{html.escape(board['measurement_boundary'])}</p><p class="muted">{html.escape(board['data_boundary'])}</p></section></main></body></html>"""
