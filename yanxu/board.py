"""Create a static, local delivery board from Yanxu run artifacts."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .benchmark import load_and_analyze
from .core import ReviewError, now, redact


MAX_ARTIFACTS = 200
MAX_ARTIFACT_BYTES = 1_000_000


def _safe_json_files(runs: Path) -> list[Path]:
    root = runs.resolve()
    if not root.is_dir():
        raise ReviewError("--runs must be an existing directory")
    files = []
    for path in sorted(root.rglob("*.json")):
        try:
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
                continue
            if path.stat().st_size > MAX_ARTIFACT_BYTES:
                continue
        except OSError:
            continue
        files.append(path)
        if len(files) >= MAX_ARTIFACTS:
            break
    return files


def _read_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _run_item(path: Path, root: Path, payload: dict) -> dict | None:
    kind = payload.get("kind")
    if kind == "yanxu.delivery_workflow":
        return {
            "artifact": redact(path.relative_to(root).as_posix()),
            "type": "delivery_workflow",
            "status": redact(str(payload.get("status", "UNKNOWN")))[:80],
            "tests": "NOT_RUN",
            "remote_modified": bool(payload.get("remote_modified")),
            "auto_merge_allowed": bool(payload.get("auto_merge_allowed")),
        }
    if kind == "yanxu.implementation_run":
        return {
            "artifact": redact(path.relative_to(root).as_posix()),
            "type": "controlled_implementation",
            "status": redact(str(payload.get("status", "UNKNOWN")))[:80],
            "tests": redact(str(payload.get("tests", "UNKNOWN")))[:80],
            "remote_modified": bool(payload.get("remote_modified")),
            "auto_merge_allowed": bool(payload.get("auto_merge_allowed")),
        }
    return None


def build_board(runs: Path, measurements: Path | None = None) -> dict:
    root = runs.resolve()
    items = []
    skipped = 0
    for path in _safe_json_files(root):
        payload = _read_json(path)
        if payload is None:
            skipped += 1
            continue
        item = _run_item(path, root, payload)
        if item is not None:
            items.append(item)

    benchmark = None
    if measurements is not None:
        benchmark = load_and_analyze(measurements)
    tests_passed = sum(item["tests"] == "PASSED" for item in items)
    manual_review = sum(item["status"] in {"READY_FOR_HUMAN_REVIEW", "READY_FOR_MANUAL_REVIEW"} for item in items)
    remote_writes = sum(item["remote_modified"] for item in items)
    return {
        "schema_version": 1,
        "kind": "yanxu.delivery_board",
        "created_at": now(),
        "runs_root": str(root),
        "artifacts_considered": len(_safe_json_files(root)),
        "artifacts_skipped": skipped,
        "runs": items,
        "summary": {
            "recorded_runs": len(items),
            "isolated_tests_passed": tests_passed,
            "manual_review_pending": manual_review,
            "recorded_remote_writes": remote_writes,
        },
        "benchmark": benchmark,
        "data_boundary": "Reads only the explicitly supplied local artifact directory and optional measurement file. It does not read repository source, send artifacts, create PRs, merge, or deploy.",
    }


def _value(value: object) -> str:
    return html.escape(str(value))


def render_html(board: dict) -> str:
    summary = board["summary"]
    benchmark = board["benchmark"]
    reduction = "不可计算"
    benchmark_status = "NOT_MEASURED"
    claim_note = "尚未提供真实配对测量数据。"
    if benchmark is not None:
        benchmark_status = benchmark["status"]
        observed = benchmark["summary"]["observed_time_reduction_percent"]
        if benchmark["claim_allowed"] and observed is not None:
            reduction = f"{observed:.2f}%"
        claim_note = benchmark["claim_note"]
    rows = "".join(
        "<tr>" + "".join(f"<td>{_value(value)}</td>" for value in (
            item["artifact"], item["type"], item["status"], item["tests"],
            "yes" if item["remote_modified"] else "no",
        )) + "</tr>"
        for item in board["runs"]
    ) or "<tr><td colspan=\"5\">未找到支持的 Yanxu run artifact。</td></tr>"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Yanxu Delivery Board</title><link rel="icon" href="data:,"><style>body{{margin:0;background:#f4f8f6;color:#102d27;font:15px/1.6 system-ui,-apple-system,"PingFang SC",sans-serif}}main{{max-width:1100px;margin:auto;padding:30px 20px 55px}}header,section{{background:#fff;border:1px solid #dce8e2;border-radius:14px;padding:22px;margin-bottom:18px}}header{{background:#102d27;color:#eafff2}}h1{{margin:0;font-size:29px}}h2{{font-size:18px;margin:0 0 8px}}.muted{{color:#638078}}header .muted{{color:#c0d9ce}}.cards{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-top:20px}}.card{{background:#fff;border:1px solid #dce8e2;border-radius:11px;padding:16px}}.label{{font-size:12px;color:#638078;font-weight:700;text-transform:uppercase}}.value{{font-size:26px;font-weight:800;margin-top:3px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{text-align:left;padding:10px 7px;border-bottom:1px solid #e8f0ec;overflow-wrap:anywhere}}th{{color:#638078;font-size:12px}}.note{{background:#fff3d9;border:1px solid #efd79c;color:#714b08;padding:12px 14px;border-radius:8px}}code{{font:12px ui-monospace,monospace;background:#edf4f0;padding:3px 5px;border-radius:4px}}@media(max-width:720px){{.cards{{grid-template-columns:repeat(2,1fr)}}table{{font-size:12px}}}}@media(max-width:450px){{.cards{{grid-template-columns:1fr}}main{{padding:16px 12px}}}}</style></head><body><main><header><div class="label" style="color:#bde7d0">LOCAL STATIC EXPORT</div><h1>Yanxu Delivery Board</h1><p class="muted">团队交付证据汇总 · 生成于 {_value(board['created_at'])}</p></header><div class="cards"><div class="card"><div class="label">记录运行</div><div class="value">{summary['recorded_runs']}</div></div><div class="card"><div class="label">隔离测试通过</div><div class="value">{summary['isolated_tests_passed']}</div></div><div class="card"><div class="label">待人工复核</div><div class="value">{summary['manual_review_pending']}</div></div><div class="card"><div class="label">可发布提效结论</div><div class="value">{_value(reduction)}</div></div></div><section><h2>运行证据</h2><p class="muted">只展示 Yanxu 工作流与受控实现产物。`remote_modified` 是记录字段，不替代 GitHub 的实际状态核验。</p><table><thead><tr><th>Artifact</th><th>类型</th><th>状态</th><th>测试</th><th>记录远端写入</th></tr></thead><tbody>{rows}</tbody></table></section><section><h2>测量口径</h2><p><b>{_value(benchmark_status)}</b> · {_value(claim_note)}</p><div class="note">此看板是面向试点团队的本地证据出口，不是托管平台。{_value(board['data_boundary'])}</div></section><section><h2>商业试点价值</h2><p>客户可将看板部署在自身内网或附在交付复盘中，统一查看任务范围、测试、人工复核状态与测量口径，而无需把源代码或凭据上传到 Yanxu 服务。</p></section></main></body></html>"""
