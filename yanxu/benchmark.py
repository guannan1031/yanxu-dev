"""Analyze paired delivery measurements without overstating productivity gains."""

from __future__ import annotations

import html
import json
import statistics
from pathlib import Path

from .core import ReviewError, now, redact


def _minutes(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0 or value > 10_000:
        raise ReviewError(f"{label} must be a positive number no greater than 10000")
    return round(float(value), 3)


def _side(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ReviewError(f"{label} must be an object")
    quality = value.get("quality_passed")
    rework = value.get("rework_count", 0)
    if not isinstance(quality, bool):
        raise ReviewError(f"{label}.quality_passed must be true or false")
    if isinstance(rework, bool) or not isinstance(rework, int) or rework < 0:
        raise ReviewError(f"{label}.rework_count must be a non-negative integer")
    return {
        "human_minutes": _minutes(value.get("human_minutes"), f"{label}.human_minutes"),
        "quality_passed": quality,
        "rework_count": rework,
    }


def analyze(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ReviewError("Benchmark input must be a JSON object")
    evidence_type = payload.get("evidence_type")
    if evidence_type not in {"observed", "synthetic"}:
        raise ReviewError("evidence_type must be observed or synthetic")
    scope = payload.get("scope")
    if not isinstance(scope, str) or not scope.strip() or len(scope) > 500:
        raise ReviewError("scope must be a non-empty string no longer than 500 characters")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list) or not 1 <= len(raw_records) <= 100:
        raise ReviewError("records must contain between 1 and 100 paired tasks")

    records = []
    seen = set()
    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict):
            raise ReviewError(f"records[{index}] must be an object")
        task_id = raw.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 120 or task_id in seen:
            raise ReviewError("Every task_id must be unique and between 1 and 120 characters")
        seen.add(task_id)
        same_scope = raw.get("same_scope")
        if not isinstance(same_scope, bool):
            raise ReviewError(f"{task_id}.same_scope must be true or false")
        baseline = _side(raw.get("baseline"), f"{task_id}.baseline")
        yanxu = _side(raw.get("yanxu"), f"{task_id}.yanxu")
        eligible = same_scope and baseline["quality_passed"] and yanxu["quality_passed"]
        reason = None
        if not same_scope:
            reason = "scope_not_comparable"
        elif not baseline["quality_passed"]:
            reason = "baseline_quality_failed"
        elif not yanxu["quality_passed"]:
            reason = "yanxu_quality_failed"
        reduction = None
        if eligible:
            reduction = round((baseline["human_minutes"] - yanxu["human_minutes"]) / baseline["human_minutes"] * 100, 2)
        records.append({
            "task_id": redact(task_id.strip()),
            "task_type": redact(str(raw.get("task_type", "unspecified")))[:120],
            "same_scope": same_scope,
            "baseline": baseline,
            "yanxu": yanxu,
            "eligible": eligible,
            "excluded_reason": reason,
            "time_reduction_percent": reduction,
        })

    eligible_records = [item for item in records if item["eligible"]]
    count = len(eligible_records)
    total_baseline = round(sum(item["baseline"]["human_minutes"] for item in eligible_records), 3)
    total_yanxu = round(sum(item["yanxu"]["human_minutes"] for item in eligible_records), 3)
    weighted = round((total_baseline - total_yanxu) / total_baseline * 100, 2) if total_baseline else None
    median = round(statistics.median(item["time_reduction_percent"] for item in eligible_records), 2) if count else None
    if evidence_type == "synthetic":
        status = "DEMO_ONLY"
    elif count == 0:
        status = "NOT_MEASURED"
    elif count < 5:
        status = "EXPLORATORY"
    else:
        status = "OBSERVED"
    return {
        "schema_version": 1,
        "kind": "yanxu.efficiency_benchmark",
        "created_at": now(),
        "evidence_type": evidence_type,
        "scope": redact(scope.strip()),
        "status": status,
        "records": records,
        "summary": {
            "paired_tasks": len(records),
            "eligible_tasks": count,
            "excluded_tasks": len(records) - count,
            "baseline_human_minutes": total_baseline,
            "yanxu_human_minutes": total_yanxu,
            "observed_time_reduction_percent": weighted,
            "median_task_reduction_percent": median,
            "baseline_rework_count": sum(item["baseline"]["rework_count"] for item in eligible_records),
            "yanxu_rework_count": sum(item["yanxu"]["rework_count"] for item in eligible_records),
        },
        "claim_allowed": evidence_type == "observed" and count >= 5,
        "claim_note": "Report only this measured scope and sample; do not generalize to total team productivity.",
    }


def render_markdown(result: dict) -> str:
    summary = result["summary"]
    reduction = summary["observed_time_reduction_percent"]
    value = "不可计算" if reduction is None else f"{reduction:.2f}%"
    lines = [
        "# Yanxu Efficiency Benchmark", "", f"- 状态：`{result['status']}`",
        f"- 证据类型：`{result['evidence_type']}`", f"- 范围：{result['scope']}",
        f"- 配对任务：{summary['paired_tasks']}；有效：{summary['eligible_tasks']}；排除：{summary['excluded_tasks']}",
        f"- 观察到的人工时间减少率：{value}", "", "## Records", "",
        "| Task | Type | Baseline min | Yanxu min | Quality gate | Reduction |",
        "| --- | --- | ---: | ---: | --- | ---: |",
    ]
    for item in result["records"]:
        reduction_value = "/" if item["time_reduction_percent"] is None else f"{item['time_reduction_percent']:.2f}%"
        gate = "eligible" if item["eligible"] else item["excluded_reason"]
        lines.append(f"| {item['task_id']} | {item['task_type']} | {item['baseline']['human_minutes']} | {item['yanxu']['human_minutes']} | {gate} | {reduction_value} |")
    lines.extend(["", "## Claim boundary", "", result["claim_note"]])
    return "\n".join(lines) + "\n"


def render_html(result: dict) -> str:
    summary = result["summary"]
    reduction = summary["observed_time_reduction_percent"]
    value = "不可计算" if reduction is None else f"{reduction:.2f}%"
    rows = []
    for item in result["records"]:
        reduction_value = "/" if item["time_reduction_percent"] is None else f"{item['time_reduction_percent']:.2f}%"
        gate = "eligible" if item["eligible"] else item["excluded_reason"]
        rows.append("<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in (
            item["task_id"], item["task_type"], item["baseline"]["human_minutes"],
            item["yanxu"]["human_minutes"], gate, reduction_value)) + "</tr>")
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Yanxu Efficiency Benchmark</title><link rel=\"icon\" href=\"data:,\"><style>body{{font-family:system-ui,sans-serif;background:#f4f7fb;color:#172033;margin:0;padding:32px}}main{{max-width:1080px;margin:auto}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px}}.card,section{{background:white;border:1px solid #dfe6ef;border-radius:14px;padding:20px;margin:16px 0}}.value{{font-size:32px;font-weight:750;color:#087f5b}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #e8edf3}}code{{background:#eef2f7;padding:3px 7px;border-radius:6px}}.note{{color:#5d6879}}</style></head><body><main><h1>Yanxu Efficiency Benchmark</h1><p>{html.escape(result['scope'])}</p><div class=\"cards\"><div class=\"card\"><div class=\"note\">状态</div><div class=\"value\">{html.escape(result['status'])}</div></div><div class=\"card\"><div class=\"note\">有效配对任务</div><div class=\"value\">{summary['eligible_tasks']}</div></div><div class=\"card\"><div class=\"note\">观察时间减少率</div><div class=\"value\">{value}</div></div></div><section><h2>配对记录</h2><table><thead><tr><th>Task</th><th>Type</th><th>Baseline min</th><th>Yanxu min</th><th>Quality gate</th><th>Reduction</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section><section><h2>口径边界</h2><p>{html.escape(result['claim_note'])}</p></section></main></body></html>"""


def load_and_analyze(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError("Could not read valid benchmark JSON") from exc
    return analyze(payload)
