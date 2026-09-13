"""Create a small, versioned task contract from a local project."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .core import ReviewError, redact


CONTEXT_NAMES = (
    "AGENTS.md", "README.md", "CONTRIBUTING.md", "pyproject.toml", "package.json",
    "pom.xml", "build.gradle", "build.gradle.kts", "Makefile",
)
MAX_FILE_BYTES = 20_000
MAX_TOTAL_BYTES = 80_000
MAX_DOCS = 12


def _safe_file(path: Path, root: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.resolve().parent.is_relative_to(root)
    except OSError:
        return False


def _candidates(root: Path) -> list[Path]:
    paths = [root / name for name in CONTEXT_NAMES]
    workflows = root / ".github" / "workflows"
    if workflows.is_dir() and not workflows.is_symlink():
        paths.extend(sorted(workflows.glob("*.y*ml")))
    docs = root / "docs"
    if docs.is_dir() and not docs.is_symlink():
        paths.extend(sorted(docs.glob("*.md")))
    return [p for p in paths if _safe_file(p, root)][:MAX_DOCS]


def collect_context(repo: Path) -> list[dict]:
    root = repo.resolve()
    if not root.is_dir() or not (root / ".git").exists():
        raise ReviewError("--repo must be an existing Git repository")
    total = 0
    context = []
    for path in _candidates(root):
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES or total + len(raw) > MAX_TOTAL_BYTES:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        total += len(raw)
        context.append({
            "path": path.relative_to(root).as_posix(),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content": redact(text),
        })
    return context


def test_commands(repo: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    if (repo / "pyproject.toml").exists() or (repo / "tests").is_dir():
        commands.append(["python", "-m", "unittest", "discover", "-s", "tests", "-v"])
    package = repo / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            scripts = {}
        if "test" in scripts:
            commands.append(["npm", "test"])
    if (repo / "pom.xml").exists():
        commands.append(["mvn", "test"])
    if (repo / "build.gradle").exists() or (repo / "build.gradle.kts").exists():
        commands.append(["gradle", "test"])
    return commands


def build_contract(requirement: str, repo: Path) -> dict:
    requirement = requirement.strip()
    if not requirement or len(requirement) > 4_000:
        raise ReviewError("Requirement must be between 1 and 4000 characters")
    root = repo.resolve()
    context = collect_context(root)
    commands = test_commands(root)
    return {
        "schema_version": 1,
        "kind": "yanxu.task_contract",
        "requirement": redact(requirement),
        "repo": str(root),
        "context_files": context,
        "suggested_test_commands": commands,
        "acceptance": [
            "Implementation scope and changed files are explicit before merge.",
            "The suggested test command passes, or a documented reason explains why it cannot run.",
            "PR CI completes successfully on the recorded commit.",
            "A reviewer confirms behavior against the requirement and project rules.",
        ],
        "next_actions": [
            "Review the context files and refine the acceptance conditions.",
            "Implement only the agreed paths in a feature branch.",
            "Use review, verify, prepare-fix, and test-fix to produce delivery evidence.",
        ],
        "remote_modified": False,
    }


def render_contract(contract: dict) -> str:
    lines = ["# Yanxu Task Contract", "", "## Requirement", "", contract["requirement"], "", "## Project context", ""]
    if contract["context_files"]:
        for item in contract["context_files"]:
            lines.append(f"- `{item['path']}` · {item['bytes']} bytes · `{item['sha256'][:12]}`")
    else:
        lines.append("- No allowlisted project context file was found.")
    lines.extend(["", "## Suggested tests", ""])
    if contract["suggested_test_commands"]:
        lines.extend(f"- `{' '.join(command)}`" for command in contract["suggested_test_commands"])
    else:
        lines.append("- No test command was detected; record the manual verification command before implementation.")
    lines.extend(["", "## Acceptance", ""])
    lines.extend(f"- {item}" for item in contract["acceptance"])
    lines.extend(["", "## Next actions", ""])
    lines.extend(f"{index}. {item}" for index, item in enumerate(contract["next_actions"], 1))
    return "\n".join(lines) + "\n"
