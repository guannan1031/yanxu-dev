"""Keep an explicit local registry of authorized Yanxu project artifacts."""

from __future__ import annotations

import html
import json
import uuid
from pathlib import Path

from .board import build_board
from .core import ReviewError, now, redact


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
        if not isinstance(project, dict) or set(project) != {"id", "runs", "measurements"}:
            raise ReviewError("Team workspace project is malformed")
        project_id = _text(project["id"], "Project id")
        if project_id in seen or not isinstance(project["runs"], str) or not project["runs"]:
            raise ReviewError("Team workspace project is malformed")
        if project["measurements"] is not None and not isinstance(project["measurements"], str):
            raise ReviewError("Team workspace project is malformed")
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


def add_project(path: Path, project_id: str, runs: Path, measurements: Path | None = None) -> dict:
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
    workspace["projects"].append({
        "id": project_id,
        "runs": str(runs),
        "measurements": str(measurements) if measurements is not None else None,
    })
    workspace["updated_at"] = now()
    write_workspace(path, workspace)
    return workspace


def build_team_board(path: Path) -> dict:
    workspace = load_workspace(path)
    projects = []
    for project in workspace["projects"]:
        try:
            board = build_board(Path(project["runs"]), Path(project["measurements"]) if project["measurements"] else None)
            projects.append({
                "id": project["id"],
                "health": "AVAILABLE",
                "summary": board["summary"],
                "benchmark_status": board["benchmark"]["status"] if board["benchmark"] else "NOT_MEASURED",
                "error": None,
            })
        except ReviewError as exc:
            projects.append({
                "id": project["id"],
                "health": "UNAVAILABLE",
                "summary": {"recorded_runs": 0, "isolated_tests_passed": 0, "manual_review_pending": 0, "recorded_remote_writes": 0},
                "benchmark_status": "UNAVAILABLE",
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
        },
        "measurement_boundary": "Efficiency percentages are not aggregated across projects because task scope differs. Open each project's board and use only its matched, quality-passing records.",
        "data_boundary": "Reads only runs and optional measurement files explicitly listed in the local workspace. It does not read source code, send artifacts, create PRs, merge, or deploy.",
    }


def render_html(board: dict) -> str:
    summary = board["summary"]
    rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in (
            project["id"], project["health"], project["summary"]["recorded_runs"],
            project["summary"]["isolated_tests_passed"], project["summary"]["manual_review_pending"],
            project["benchmark_status"], project["error"] or "—",
        )) + "</tr>"
        for project in board["projects"]
    ) or "<tr><td colspan=\"7\">当前工作空间尚未登记项目。</td></tr>"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Yanxu Team Delivery Board</title><link rel="icon" href="data:,"><style>body{{margin:0;background:#f4f8f6;color:#102d27;font:15px/1.6 system-ui,-apple-system,"PingFang SC",sans-serif}}main{{max-width:1120px;margin:auto;padding:30px 20px 55px}}header,section{{background:#fff;border:1px solid #dce8e2;border-radius:14px;padding:22px;margin-bottom:18px}}header{{background:#102d27;color:#eafff2}}h1{{margin:0;font-size:29px}}h2{{font-size:18px;margin:0 0 8px}}.muted{{color:#638078}}header .muted{{color:#c0d9ce}}.cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-top:20px}}.card{{background:#fff;border:1px solid #dce8e2;border-radius:11px;padding:16px}}.label{{font-size:12px;color:#638078;font-weight:700;text-transform:uppercase}}.value{{font-size:26px;font-weight:800;margin-top:3px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{text-align:left;padding:10px 7px;border-bottom:1px solid #e8f0ec;overflow-wrap:anywhere}}th{{color:#638078;font-size:12px}}.note{{background:#fff3d9;border:1px solid #efd79c;color:#714b08;padding:12px 14px;border-radius:8px}}@media(max-width:720px){{.cards{{grid-template-columns:repeat(2,1fr)}}table{{font-size:12px}}}}@media(max-width:450px){{.cards{{grid-template-columns:1fr}}main{{padding:16px 12px}}}}</style></head><body><main><header><div class="label" style="color:#bde7d0">PRIVATE TEAM ALPHA · LOCAL STATIC EXPORT</div><h1>{html.escape(board['workspace_name'])}</h1><p class="muted">Yanxu Team Delivery Board · 生成于 {html.escape(board['created_at'])}</p></header><div class="cards"><div class="card"><div class="label">登记项目</div><div class="value">{summary['registered_projects']}</div></div><div class="card"><div class="label">可用项目</div><div class="value">{summary['available_projects']}</div></div><div class="card"><div class="label">记录运行</div><div class="value">{summary['recorded_runs']}</div></div><div class="card"><div class="label">待人工复核</div><div class="value">{summary['manual_review_pending']}</div></div></div><section><h2>项目交付状态</h2><table><thead><tr><th>项目</th><th>健康状态</th><th>运行</th><th>隔离测试通过</th><th>待复核</th><th>测量状态</th><th>说明</th></tr></thead><tbody>{rows}</tbody></table></section><section><h2>边界</h2><p class="note">{html.escape(board['measurement_boundary'])}</p><p class="muted">{html.escape(board['data_boundary'])}</p></section></main></body></html>"""
