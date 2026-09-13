"""Run an explicit test command against a patched commit archive."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tarfile
import tempfile
import time
from io import BytesIO
from pathlib import Path, PurePosixPath

from .core import ReviewError, binding, capture, compare, now
from .patches import git, patch_paths, safe_path


def archive_commit(checkout: Path, sha: str, destination: Path) -> None:
    """Extract only regular files from a commit archive into a fresh directory."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    try:
        result = subprocess.run(["git", "-C", str(checkout), "archive", "--format=tar", sha],
                                capture_output=True, timeout=60, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewError("Could not archive the recorded commit") from exc
    if result.returncode:
        raise ReviewError("Could not archive the recorded commit")
    if len(result.stdout) > 50 * 1024 * 1024:
        raise ReviewError("Commit archive exceeds 50 MB")
    total = 0
    try:
        archive = tarfile.open(fileobj=BytesIO(result.stdout), mode="r:")
        for member in archive.getmembers():
            name = member.name
            parts = PurePosixPath(name).parts
            if not name or name.startswith("/") or any(p in ("", ".", "..") for p in parts):
                raise ReviewError("Commit archive contains an unsafe path")
            target = destination / name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isreg() or member.size > 10 * 1024 * 1024:
                raise ReviewError("Test archive contains a non-regular or oversized file")
            total += member.size
            if total > 50 * 1024 * 1024:
                raise ReviewError("Extracted commit exceeds 50 MB")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ReviewError("Could not read commit archive member")
            target.write_bytes(source.read())
            target.chmod(member.mode & 0o777)
    except tarfile.TarError as exc:
        raise ReviewError("Commit archive is not a valid tar archive") from exc


def test_command(command: list[str]) -> list[str]:
    if not command or command[0] == "--":
        raise ReviewError("Provide an explicit test command after --")
    if len(command) > 32 or any(not isinstance(x, str) or len(x) > 2000 for x in command):
        raise ReviewError("Test command is too long")
    if any(x in ("|", "&&", ";", ">", "<", "`", "$", "\n", "\r") for x in command):
        raise ReviewError("Shell syntax is not accepted; pass executable and arguments separately")
    executable = Path(command[0]).name.lower()
    if not ((executable == "python" or executable.startswith("python3")) or
            executable in {"pytest", "node", "npm", "go", "mvn", "gradle", "cargo"}):
        raise ReviewError("Test executable is outside the supported allowlist")
    return command


def run_tests(evidence: dict, checkout: Path, allow_paths: list[str], command: list[str], output: Path,
              replay: bool = False, timeout: int = 120) -> dict:
    snapshot = evidence["snapshot"]
    if binding(snapshot) != snapshot["binding"]:
        raise ReviewError("Saved evidence fingerprint mismatch")
    if not snapshot["complete"] or evidence["ai"]["status"] != "completed":
        raise ReviewError("Complete context and a completed AI diagnosis are required")
    command = test_command(command)
    proposal = evidence["ai"]["answer"]["suggested_patch"]
    paths = patch_paths(proposal)
    allowed = {safe_path(p) for p in allow_paths}
    if not set(paths).issubset(allowed):
        raise ReviewError("Patch includes a path outside the explicit allowlist")
    if timeout < 1 or timeout > 900:
        raise ReviewError("Test timeout must be between 1 and 900 seconds")
    sha = snapshot["pr"]["head_sha"]
    checkout = checkout.resolve()
    if git("-C", str(checkout), "cat-file", "-t", sha).strip() != "commit":
        raise ReviewError("Recorded commit is unavailable in this checkout")
    verification = {"status": "NOT_CHECKED_REPLAY", "auto_merge_allowed": False}
    if not replay:
        current = capture(snapshot["repo"], snapshot["pr"]["number"])
        verification = compare(snapshot, current)
        if verification["status"] != "UNCHANGED" or current["pr"]["state"] != "open" or current["pr"]["merged"]:
            raise ReviewError("Evidence is stale or PR is closed; generate a new diagnosis")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="test-", dir=output))
    workspace = folder / "workspace"
    workspace.mkdir()
    manifest = {"schema_version": 1, "created_at": now(), "status": "PREPARING",
                "mode": "historical_replay" if replay else "live_evidence", "repo": snapshot["repo"],
                "pr": snapshot["pr"]["number"], "head_sha": sha, "evidence_binding": snapshot["binding"],
                "paths": paths, "test_command": command, "timeout_seconds": timeout,
                "verification": verification, "tests": "NOT_RUN", "remote_modified": False,
                "original_checkout_modified": False, "auto_merge_allowed": False}
    try:
        archive_commit(checkout, sha, workspace)
        git("init", "--quiet", "--template=", str(workspace))
        git("-C", str(workspace), "-c", "core.autocrlf=false", "add", "--", ".")
        patch_file = folder / "proposal.diff"
        patch_file.write_text(proposal, encoding="utf-8")
        git("-C", str(workspace), "apply", "--check", "--", str(patch_file))
        git("-C", str(workspace), "apply", "--", str(patch_file))
        changed = git("-C", str(workspace), "diff", "--name-only").splitlines()
        if sorted(changed) != sorted(paths):
            raise ReviewError("Applied patch changed unexpected files or had no effect")
        manifest["status"] = "RUNNING_TESTS"
        (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        env = {"PATH": os.environ.get("PATH", ""), "LANG": os.environ.get("LANG", "C"),
               "LC_ALL": os.environ.get("LC_ALL", "C"), "HOME": str(folder / "home"),
               "TMPDIR": str(folder / "tmp"), "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "NO_PROXY": "*"}
        (folder / "home").mkdir()
        (folder / "tmp").mkdir()
        started = time.monotonic()
        try:
            proc = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    start_new_session=True)
        except OSError as exc:
            raise ReviewError(f"Test executable unavailable: {command[0]}") from exc
        try:
            stdout, _ = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                stdout, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                stdout, _ = proc.communicate()
            timed_out = True
        stdout = stdout or ""
        (folder / "test-output.log").write_text(stdout[-100000:], encoding="utf-8", errors="replace")
        manifest.update(elapsed_seconds=round(time.monotonic() - started, 3), exit_code=proc.returncode,
                        output_truncated=len(stdout) > 100000, tests="TIMEOUT" if timed_out else ("PASSED" if proc.returncode == 0 else "FAILED"),
                        status="COMPLETED" if not timed_out and proc.returncode == 0 else ("TIMED_OUT" if timed_out else "FAILED_TESTS"))
    except ReviewError as exc:
        manifest.update(status="FAILED", error=str(exc))
        raise
    finally:
        (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**manifest, "folder": str(folder), "workspace": str(workspace), "log": str(folder / "test-output.log")}
