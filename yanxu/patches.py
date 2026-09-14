"""Prepare a proposed fix in a minimal copy; never execute repository code."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path, PurePosixPath

from .core import ReviewError, binding, capture, command, compare, now


def git(*args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    return command(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, *args], env=env)


def safe_path(name: str) -> str:
    parts = PurePosixPath(name).parts
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_./-]*", name) or not parts or any(p in (".", "..") for p in name.split("/")):
        raise ReviewError("Only simple relative file paths are supported")
    if any(p.startswith(".") or p.lower().startswith("test") or p.lower() == "conftest.py" for p in parts):
        raise ReviewError("Hidden files and independent tests cannot be patched")
    return name


def patch_paths(patch: str) -> list[str]:
    if not isinstance(patch, str) or not patch or len(patch.encode()) > 100000:
        raise ReviewError("Patch is empty or exceeds 100 KB")
    paths = []
    before, after = [], []
    forbidden = ("new file mode ", "deleted file mode ", "old mode ", "new mode ",
                 "rename from ", "rename to ", "copy from ", "copy to ", "GIT binary patch", "Binary files ")
    for line in patch.splitlines():
        if line.startswith(forbidden):
            raise ReviewError("Only text modifications of existing regular files are supported")
        if line.startswith("diff --git "):
            m = re.fullmatch(r"diff --git a/(\S+) b/(\S+)", line)
            if not m or m[1] != m[2]:
                raise ReviewError("Renames or quoted/ambiguous patch paths are unsupported")
            paths.append(safe_path(m[1]))
        elif line.startswith("--- "):
            before.append(line[4:].split("\t", 1)[0])
        elif line.startswith("+++ "):
            after.append(line[4:].split("\t", 1)[0])
    if not paths or len(paths) > 10 or len(paths) != len(set(paths)):
        raise ReviewError("Patch requires 1–10 unique files")
    if before != ["a/" + p for p in paths] or after != ["b/" + p for p in paths]:
        raise ReviewError("Patch headers disagree about target paths")
    return paths


def prepare(evidence: dict, checkout: Path, allow_paths: list[str], output: Path, replay: bool = False) -> dict:
    snapshot = evidence["snapshot"]
    if binding(snapshot) != snapshot["binding"]:
        raise ReviewError("Saved evidence fingerprint mismatch")
    if not snapshot["complete"] or evidence["ai"]["status"] != "completed":
        raise ReviewError("Complete context and a completed AI diagnosis are required")
    proposal = evidence["ai"]["answer"]["suggested_patch"]
    paths = patch_paths(proposal)
    allowed = {safe_path(p) for p in allow_paths}
    if not set(paths).issubset(allowed):
        raise ReviewError("Patch includes a path outside the explicit allowlist")
    sha = snapshot["pr"]["head_sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ReviewError("Invalid recorded commit SHA")
    checkout = checkout.resolve()
    if git("-C", str(checkout), "cat-file", "-t", sha).strip() != "commit":
        raise ReviewError("Recorded commit is unavailable in this checkout")
    verification = {"status": "NOT_CHECKED_REPLAY", "auto_merge_allowed": False}
    if not replay:
        current = capture(snapshot["repo"], snapshot["pr"]["number"])
        verification = compare(snapshot, current)
        if verification["status"] != "UNCHANGED" or current["pr"]["state"] != "open" or current["pr"]["merged"]:
            raise ReviewError("Evidence is stale or PR is closed; generate a new diagnosis")
    # Read immutable blobs, not the caller's possibly dirty working tree.
    originals = {}
    executable = set()
    for name in paths:
        tree = git("-C", str(checkout), "ls-tree", "-z", sha, "--", name)
        entry, _, filename = tree.rstrip("\0").partition("\t")
        meta = entry.split()
        if filename != name or len(meta) != 3 or meta[0] not in ("100644", "100755") or meta[1] != "blob":
            raise ReviewError("Target must be an existing regular file, not a symlink or submodule")
        size = int(git("-C", str(checkout), "cat-file", "-s", meta[2]))
        if size > 1000000:
            raise ReviewError("Target exceeds 1 MB")
        originals[name] = git("-C", str(checkout), "cat-file", "blob", meta[2])
        raw = originals[name].encode("utf-8")
        if b"\0" in raw or hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() != meta[2]:
            raise ReviewError("Only unchanged UTF-8/LF text decoding is supported")
        if meta[0] == "100755":
            executable.add(name)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="fix-", dir=output))
    workspace = folder / "workspace"
    workspace.mkdir()
    result = {"schema_version": 1, "created_at": now(), "status": "PREPARING",
              "mode": "historical_replay" if replay else "live_evidence", "repo": snapshot["repo"],
              "pr": snapshot["pr"]["number"], "head_sha": sha, "evidence_binding": snapshot["binding"],
              "proposal_sha256": hashlib.sha256(proposal.encode()).hexdigest(), "paths": paths,
              "verification": verification, "tests": "NOT_RUN", "original_checkout_modified": False,
              "remote_modified": False, "auto_merge_allowed": False}
    try:
        for name, content in originals.items():
            dest = workspace / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content.encode("utf-8"))
            if name in executable:
                dest.chmod(0o755)
        # This fresh index has no inherited remotes, hooks, credentials or checkout filters.
        git("init", "--quiet", "--template=", str(workspace))
        git("-C", str(workspace), "-c", "core.autocrlf=false", "add", "--", *paths)
        patch_file = folder / "proposal.diff"
        patch_file.write_bytes(proposal.encode("utf-8"))
        git("-C", str(workspace), "apply", "--check", "--", str(patch_file))
        git("-C", str(workspace), "apply", "--", str(patch_file))
        changed = git("-C", str(workspace), "diff", "--name-only").splitlines()
        if sorted(changed) != sorted(paths):
            raise ReviewError("Applied patch changed unexpected files or had no effect")
        diff = git("-C", str(workspace), "diff", "--no-ext-diff", "--no-textconv")
        (folder / "verified.diff").write_text(diff, encoding="utf-8")
        result.update(status="PREPARED_NOT_TESTED", diff_sha256=hashlib.sha256(diff.encode()).hexdigest())
    except ReviewError as exc:
        result.update(status="FAILED", error=str(exc))
        raise
    finally:
        (folder / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**result, "folder": str(folder), "workspace": str(workspace)}
