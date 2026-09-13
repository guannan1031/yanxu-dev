"""Inspect whether a local repository is ready for bounded AI coding work."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .core import ReviewError, now
from .task import collect_context, test_commands


def _regular(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def inspect_repo(repo: Path) -> dict:
    root = repo.resolve()
    context = collect_context(root)
    paths = {item["path"] for item in context}
    commands = test_commands(root)
    workflows = [path for path in sorted((root / ".github" / "workflows").glob("*.y*ml")) if _regular(path)] if (root / ".github" / "workflows").is_dir() else []
    gitignore = root / ".gitignore"
    ignore_text = gitignore.read_text(encoding="utf-8", errors="replace") if _regular(gitignore) else ""
    build_files = [name for name in ("pyproject.toml", "package.json", "pom.xml", "build.gradle", "build.gradle.kts", "Makefile") if _regular(root / name)]
    safety_files = [name for name in ("SECURITY.md", "docs/SAFETY_BOUNDARY.md") if _regular(root / name)]

    checks = [
        {"id": "project_rules", "title": "AI project rules", "passed": "AGENTS.md" in paths,
         "critical": True, "fix": "Add AGENTS.md with scope, commands, safety boundaries, and acceptance rules."},
        {"id": "readme", "title": "Project start guide", "passed": "README.md" in paths,
         "critical": True, "fix": "Add README.md with setup, run, and verification instructions."},
        {"id": "build_metadata", "title": "Build metadata", "passed": bool(build_files),
         "critical": False, "fix": "Add a standard build file such as pyproject.toml, package.json, or pom.xml."},
        {"id": "test_entry", "title": "Detectable test entry", "passed": bool(commands),
         "critical": True, "fix": "Add an executable test suite and document its command."},
        {"id": "pull_request_ci", "title": "Repository CI workflow", "passed": bool(workflows),
         "critical": True, "fix": "Add a pull_request CI workflow that runs the documented verification."},
        {"id": "secret_boundary", "title": "Secret exclusion boundary",
         "passed": ".env" in ignore_text and ("*.key" in ignore_text or "*.pem" in ignore_text or bool(safety_files)),
         "critical": True, "fix": "Ignore .env and private-key files, and document credential handling."},
    ]
    passed = sum(item["passed"] for item in checks)
    score = round(passed / len(checks) * 100)
    blockers = [item["id"] for item in checks if item["critical"] and not item["passed"]]
    for item in checks:
        item["status"] = "PASS" if item["passed"] else ("FAIL" if item["critical"] else "WARN")
    return {
        "schema_version": 1,
        "kind": "yanxu.repository_doctor",
        "created_at": now(),
        "repo": str(root),
        "status": "READY" if not blockers and score >= 75 else "NEEDS_WORK",
        "score": score,
        "checks": checks,
        "blockers": blockers,
        "context_files": sorted(paths),
        "detected_test_commands": commands,
        "build_files": build_files,
        "workflow_files": [path.relative_to(root).as_posix() for path in workflows],
        "source_files_scanned": 0,
        "remote_modified": False,
    }


def render_markdown(result: dict) -> str:
    lines = ["# Yanxu Repository Doctor", "", f"- 状态：`{result['status']}`", f"- 得分：{result['score']}/100",
             "- 业务源码扫描：0", "", "## Checks", "", "| Check | Status | Suggested action |",
             "| --- | --- | --- |"]
    for item in result["checks"]:
        fix = "/" if item["passed"] else item["fix"].replace("|", "\\|")
        lines.append(f"| {item['title']} | {item['status']} | {fix} |")
    lines.extend(["", "## Detected tests", ""])
    if result["detected_test_commands"]:
        lines.extend(f"- `{' '.join(command)}`" for command in result["detected_test_commands"])
    else:
        lines.append("- 未检测到")
    return "\n".join(lines) + "\n"


def render_html(result: dict) -> str:
    rows = "".join(
        f"<tr><td>{html.escape(item['title'])}</td><td><b>{item['status']}</b></td><td>{html.escape('/' if item['passed'] else item['fix'])}</td></tr>"
        for item in result["checks"]
    )
    return f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Yanxu Repository Doctor</title><link rel=\"icon\" href=\"data:,\"><style>body{{font-family:system-ui,sans-serif;background:#f4f7fb;color:#172033;margin:0;padding:32px}}main{{max-width:980px;margin:auto}}.hero,section{{background:#fff;border:1px solid #dfe6ef;border-radius:14px;padding:22px;margin-bottom:18px}}.score{{font-size:42px;font-weight:800;color:#087f5b}}table{{width:100%;border-collapse:collapse}}th,td{{padding:11px;text-align:left;border-bottom:1px solid #e8edf3}}.note{{color:#5d6879}}</style></head><body><main><div class=\"hero\"><div class=\"note\">AI Coding readiness</div><h1>Repository Doctor</h1><div class=\"score\">{result['score']}/100 · {result['status']}</div><p>业务源码扫描：0；远端修改：false</p></div><section><h2>检查结果</h2><table><thead><tr><th>Check</th><th>Status</th><th>Suggested action</th></tr></thead><tbody>{rows}</tbody></table></section></main></body></html>"""


def write_report(result: dict, output: Path) -> dict:
    folder = output.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "doctor.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (folder / "doctor.md").write_text(render_markdown(result), encoding="utf-8")
    (folder / "doctor.html").write_text(render_html(result), encoding="utf-8")
    return {"status": result["status"], "score": result["score"], "blockers": result["blockers"],
            "report": str(folder / "doctor.html"), "source_files_scanned": 0}
