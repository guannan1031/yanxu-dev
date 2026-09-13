"""Run the bounded Yanxu delivery stages as one evidence-producing workflow."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .core import ReviewError, assess, capture, compare, now
from .doctor import inspect_repo, render_html as render_doctor_html, render_markdown as render_doctor_markdown
from .task import build_contract, render_contract


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _render_markdown(result: dict) -> str:
    lines = [
        "# Yanxu Delivery Workflow",
        "",
        f"- 状态：`{result['status']}`",
        f"- 当前阶段：`{result['current_stage']}`",
        f"- 项目体检：`{result['doctor']['status']} {result['doctor']['score']}/100`",
        f"- 远端修改：`{str(result['remote_modified']).lower()}`",
        f"- 自动合并授权：`{str(result['auto_merge_allowed']).lower()}`",
        "",
        "## Stages",
        "",
    ]
    lines.extend(f"- `{event['stage']}` · {event['at']}" for event in result["events"])
    lines.extend(["", "## Next action", "", result["next_action"], ""])
    return "\n".join(lines)


def _render_html(result: dict) -> str:
    events = "".join(
        f"<li><b>{html.escape(event['stage'])}</b><span>{html.escape(event['at'])}</span></li>"
        for event in result["events"]
    )
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Yanxu Delivery Workflow</title><link rel=\"icon\" href=\"data:,\"><style>body{{font-family:system-ui,sans-serif;background:#f4f7fb;color:#172033;margin:0;padding:28px}}main{{max-width:900px;margin:auto}}header,section{{background:#fff;border:1px solid #dfe6ef;border-radius:14px;padding:22px;margin-bottom:18px}}.status{{font-size:34px;font-weight:800;color:#087f5b}}ul{{list-style:none;padding:0}}li{{display:flex;justify-content:space-between;gap:16px;padding:12px 0;border-bottom:1px solid #e8edf3}}span,.note{{color:#5d6879}}@media(max-width:560px){{body{{padding:14px}}li{{display:block}}li span{{display:block;margin-top:5px;font-size:13px}}}}</style></head><body><main><header><div class=\"note\">Bounded delivery orchestration</div><h1>Yanxu Workflow</h1><div class=\"status\">{html.escape(result['status'])}</div><p>当前阶段：{html.escape(result['current_stage'])} · 项目体检 {result['doctor']['score']}/100</p></header><section><h2>执行轨迹</h2><ul>{events}</ul></section><section><h2>下一步</h2><p>{html.escape(result['next_action'])}</p><p class=\"note\">远端修改：false · 自动合并授权：false · 业务源码扫描：0</p></section></main></body></html>"""


def run_workflow(
    requirement: str,
    repo: Path,
    output: Path,
    github_repo: str | None = None,
    pr: int | None = None,
    include_failed_logs: bool = False,
) -> dict:
    if (github_repo is None) != (pr is None):
        raise ReviewError("--github-repo and --pr must be provided together")

    folder = output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    events: list[dict] = []

    def event(stage: str) -> None:
        events.append({"stage": stage, "at": now()})
        _write_json(folder / "events.json", events)

    event("DOCTOR_RUNNING")
    doctor = inspect_repo(repo)
    _write_json(folder / "doctor.json", doctor)
    (folder / "doctor.md").write_text(render_doctor_markdown(doctor), encoding="utf-8")
    (folder / "doctor.html").write_text(render_doctor_html(doctor), encoding="utf-8")

    if doctor["status"] != "READY":
        event("BLOCKED_REPOSITORY")
        result = _result("BLOCKED", "doctor", doctor, events,
                         "Resolve the repository doctor blockers, then run the workflow again.")
        return _finish(folder, result)

    event("DOCTOR_READY")
    contract = build_contract(requirement, repo)
    _write_json(folder / "task.json", contract)
    (folder / "task.md").write_text(render_contract(contract), encoding="utf-8")
    event("TASK_CONTRACT_CREATED")

    if github_repo is None:
        event("READY_FOR_IMPLEMENTATION")
        result = _result(
            "READY_FOR_IMPLEMENTATION", "task", doctor, events,
            "Implement the agreed scope in a feature branch. After opening a PR, rerun with --github-repo and --pr.",
        )
        return _finish(folder, result)

    event("PR_EVIDENCE_COLLECTING")
    snapshot = capture(github_repo, pr, include_logs=include_failed_logs)
    assessment = assess(snapshot)
    evidence = {"schema_version": 1, "snapshot": snapshot, "assessment": assessment,
                "ai": {"status": "not_requested", "cost_money": None}}
    _write_json(folder / "evidence.json", evidence)
    event("PR_EVIDENCE_CAPTURED")

    current = capture(github_repo, pr, include_logs=False)
    verification = compare(snapshot, current)
    _write_json(folder / "verify.json", verification)
    event("PR_EVIDENCE_VERIFIED" if verification["status"] == "UNCHANGED" else "BLOCKED_STALE_EVIDENCE")

    if verification["status"] != "UNCHANGED":
        result = _result("BLOCKED", "verify", doctor, events,
                         "The PR changed during the workflow. Review the changes and run it again.", assessment, verification)
    elif assessment["status"] == "BLOCKED":
        event("BLOCKED_PR_POLICY")
        result = _result("BLOCKED", "review", doctor, events,
                         "Resolve the recorded PR or CI blockers, then run the workflow again.", assessment, verification)
    else:
        event("READY_FOR_MANUAL_REVIEW")
        result = _result(
            "READY_FOR_MANUAL_REVIEW", "review", doctor, events,
            "A developer must inspect the diff and acceptance evidence before using the normal GitHub merge controls.",
            assessment, verification,
        )
    return _finish(folder, result)


def _result(status: str, stage: str, doctor: dict, events: list[dict], next_action: str,
            assessment: dict | None = None, verification: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "kind": "yanxu.delivery_workflow",
        "status": status,
        "current_stage": stage,
        "doctor": {"status": doctor["status"], "score": doctor["score"], "blockers": doctor["blockers"]},
        "assessment": assessment,
        "verification": verification,
        "events": events,
        "next_action": next_action,
        "source_files_scanned": 0,
        "remote_modified": False,
        "auto_merge_allowed": False,
    }


def _finish(folder: Path, result: dict) -> dict:
    _write_json(folder / "workflow.json", result)
    (folder / "workflow.md").write_text(_render_markdown(result), encoding="utf-8")
    (folder / "workflow.html").write_text(_render_html(result), encoding="utf-8")
    return {"status": result["status"], "current_stage": result["current_stage"],
            "report": str(folder / "workflow.html"), "run": str(folder / "workflow.json"),
            "remote_modified": False, "auto_merge_allowed": False}
