from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from .ai import diagnose
from .core import ReviewError, assess, binding, capture, compare, now
from .report import render
from .patches import prepare
from .test_runner import run_tests
from .task import build_contract, render_contract
from .benchmark import load_and_analyze, render_html as render_benchmark_html, render_markdown
from .measure import record_observation
from .doctor import inspect_repo, write_report as write_doctor_report
from .workflow import run_workflow
from .draft_pr import publish_draft_pr
from .implement import run_implementation
from .board import build_board, render_html as render_board_html
from .policy import create_policy, load_policy
from .team import (add_project, build_team_board, create_workspace, export_team_bundle,
                   load_github_snapshot, render_html as render_team_html, set_github_target,
                   sync_team_github, write_workspace)


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Evidence-bound AI coding and GitHub delivery workflow.")
    sub = parser.add_subparsers(dest="command", required=True)
    review = sub.add_parser("review", help="Collect evidence and generate a local HTML report")
    review.add_argument("--repo", required=True, help="GitHub owner/repo")
    review.add_argument("--pr", required=True, type=int)
    review.add_argument("--ai", action="store_true", help="Send collected repository content to Codex for diagnosis")
    review.add_argument("--include-failed-logs", action="store_true", help="Include up to two failed workflow log excerpts")
    review.add_argument("--output", type=Path, default=Path("runs"))
    verify = sub.add_parser("verify", help="Re-fetch GitHub state; reject stale review evidence")
    verify.add_argument("evidence", type=Path)
    fix = sub.add_parser("prepare-fix", help="Apply a proposed patch to a minimal independent copy; never run repository code")
    fix.add_argument("evidence", type=Path)
    fix.add_argument("--checkout", type=Path, required=True, help="Authorized local Git repository containing the recorded commit")
    fix.add_argument("--allow-path", action="append", required=True, help="Exact existing file allowed to change; repeat for each file")
    fix.add_argument("--output", type=Path, default=Path("runs"))
    fix.add_argument("--replay", action="store_true", help="Historical demonstration only; skip live GitHub verification and label result as replay")
    run = sub.add_parser("test-fix", help="Apply a proposal to a full commit archive and run an explicit test command")
    run.add_argument("evidence", type=Path)
    run.add_argument("--checkout", type=Path, required=True)
    run.add_argument("--allow-path", action="append", required=True)
    run.add_argument("--output", type=Path, default=Path("runs"))
    run.add_argument("--timeout", type=int, default=120)
    run.add_argument("--replay", action="store_true", help="Historical demonstration only; skip live GitHub verification")
    run.add_argument("--command", dest="test_command", nargs=argparse.REMAINDER, required=True,
                     help="Executable and arguments after --command; shell syntax is rejected")
    task = sub.add_parser("task", help="Create a local requirement and acceptance contract from project context")
    task.add_argument("requirement")
    task.add_argument("--repo", type=Path, default=Path("."))
    task.add_argument("--output", type=Path, default=Path("runs/tasks"))
    benchmark = sub.add_parser("benchmark", help="Analyze paired baseline and Yanxu delivery measurements")
    benchmark.add_argument("input", type=Path)
    benchmark.add_argument("--output", type=Path, default=Path("runs/benchmark"))
    record = sub.add_parser("record", help="Append one baseline or Yanxu observation without overwriting existing data")
    record.add_argument("dataset", type=Path)
    record.add_argument("--scope", required=True)
    record.add_argument("--task-id", required=True)
    record.add_argument("--task-type", required=True)
    record.add_argument("--variant", choices=("baseline", "yanxu"), required=True)
    record.add_argument("--human-minutes", type=float, required=True)
    quality = record.add_mutually_exclusive_group(required=True)
    quality.add_argument("--quality-passed", dest="quality_passed", action="store_true")
    quality.add_argument("--quality-failed", dest="quality_passed", action="store_false")
    record.add_argument("--rework-count", type=int, default=0)
    record.add_argument("--evidence", required=True, help="Local artifact path, commit, PR, run, or other reviewable reference")
    comparability = record.add_mutually_exclusive_group(required=True)
    comparability.add_argument("--same-scope", dest="same_scope", action="store_true")
    comparability.add_argument("--different-scope", dest="same_scope", action="store_false")
    doctor = sub.add_parser("doctor", help="Inspect local repository readiness for bounded AI coding")
    doctor.add_argument("--repo", type=Path, default=Path("."))
    doctor.add_argument("--output", type=Path, default=Path("runs/doctor"))
    workflow = sub.add_parser("workflow", help="Run repository readiness, task contract and optional PR evidence stages")
    workflow.add_argument("requirement")
    workflow.add_argument("--repo", type=Path, default=Path("."))
    workflow.add_argument("--github-repo", help="GitHub owner/repository; requires --pr")
    workflow.add_argument("--pr", type=int, help="Open pull request number; requires --github-repo")
    workflow.add_argument("--include-failed-logs", action="store_true")
    workflow.add_argument("--output", type=Path, default=Path("runs/workflow"))
    draft = sub.add_parser("draft-pr", help="Plan or explicitly publish the current branch as a GitHub Draft PR")
    draft.add_argument("--repo", type=Path, default=Path("."))
    draft.add_argument("--github-repo", required=True)
    draft.add_argument("--base", default="main")
    draft.add_argument("--head", required=True)
    draft.add_argument("--allow-path", action="append", required=True)
    draft.add_argument("--title", required=True)
    draft.add_argument("--body-file", type=Path, required=True)
    draft.add_argument("--output", type=Path, default=Path("runs/draft-pr"))
    draft.add_argument("--confirm-create", action="store_true",
                       help="Push the current branch and create a Draft PR after all planning checks pass")
    implement = sub.add_parser("implement", help="Generate a bounded source patch and test it in an isolated HEAD archive")
    implement.add_argument("contract", type=Path, help="task.json created by the task command")
    implement.add_argument("--checkout", type=Path, required=True)
    implement.add_argument("--allow-path", action="append", required=True,
                           help="Exact existing source file the model may modify; repeat for each file")
    implement.add_argument("--output", type=Path, default=Path("runs/implement"))
    implement.add_argument("--timeout", type=int, default=120, help="Test timeout in seconds")
    implement.add_argument("--ai-timeout", type=int, default=240, help="Model timeout in seconds")
    implement.add_argument("--policy", type=Path, help="Optional local team policy generated by the policy command")
    implement.add_argument("--command", dest="test_command", nargs=argparse.REMAINDER, required=True,
                           help="Explicit test executable and arguments; shell syntax is rejected")
    policy = sub.add_parser("policy", help="Create a local policy that bounds controlled implementation")
    policy.add_argument("--name", required=True)
    policy.add_argument("--allow-path", action="append", required=True)
    policy.add_argument("--output", type=Path, default=Path(".yanxu/team-policy.json"))
    policy.add_argument("--command", dest="test_command", nargs=argparse.REMAINDER, required=True,
                        help="Approved test executable and arguments after --command")
    board = sub.add_parser("board", help="Generate a static local team board from Yanxu run artifacts")
    board.add_argument("--runs", type=Path, required=True, help="Explicit local directory containing Yanxu artifacts")
    board.add_argument("--measurements", type=Path, help="Optional observed measurement JSON created by record")
    board.add_argument("--output", type=Path, default=Path("runs/board.html"))
    team = sub.add_parser("team", help="Manage a local private team workspace")
    team_sub = team.add_subparsers(dest="team_command", required=True)
    team_init = team_sub.add_parser("init", help="Create an empty local team workspace")
    team_init.add_argument("--name", required=True)
    team_init.add_argument("--output", type=Path, default=Path(".yanxu/team-workspace.json"))
    team_add = team_sub.add_parser("add-project", help="Register one authorized local project's artifacts")
    team_add.add_argument("workspace", type=Path)
    team_add.add_argument("--id", required=True)
    team_add.add_argument("--runs", type=Path, required=True)
    team_add.add_argument("--measurements", type=Path)
    team_add.add_argument("--policy", type=Path, help="Optional validated team policy for this project")
    team_add.add_argument("--github-repo", help="Optional GitHub owner/repository for read-only PR sync")
    team_add.add_argument("--pr", type=int, help="Optional pull request number; requires --github-repo")
    team_set = team_sub.add_parser("set-github", help="Set the current read-only GitHub PR for a project")
    team_set.add_argument("workspace", type=Path)
    team_set.add_argument("--id", required=True)
    team_set.add_argument("--github-repo", required=True)
    team_set.add_argument("--pr", type=int, required=True)
    team_board = team_sub.add_parser("board", help="Generate a local multi-project team board")
    team_board.add_argument("workspace", type=Path)
    team_board.add_argument("--output", type=Path, default=Path("runs/team-board.html"))
    team_export = team_sub.add_parser("export", help="Create a sanitized pilot evidence ZIP")
    team_export.add_argument("workspace", type=Path)
    team_export.add_argument("--output", type=Path, default=Path("runs/yanxu-pilot-evidence.zip"))
    team_export.add_argument("--github-snapshot", type=Path,
                             help="Optional team-github.json generated by sync-github")
    team_sync = team_sub.add_parser("sync-github", help="Read registered PR/CI facts into a version-bound team snapshot")
    team_sync.add_argument("workspace", type=Path)
    team_sync.add_argument("--output", type=Path, default=Path("runs/team-github"))
    team_publish = team_sub.add_parser("publish-snapshot", help="Explicitly publish normalized evidence to a private service")
    team_publish.add_argument("snapshot", type=Path)
    team_publish.add_argument("--server", required=True)
    team_publish.add_argument("--workspace-id", required=True)
    team_publish.add_argument("--token-env", default="YANXU_API_TOKEN")
    serve = sub.add_parser("serve", help="Run the optional private PostgreSQL team service")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    worker = sub.add_parser("worker", help="Process verified GitHub webhook deliveries")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .service_api import run_server
            run_server(args.host, args.port)
            return 0
        if args.command == "worker":
            import os
            from .github_events import run_worker
            database_url = os.environ.get("YANXU_DATABASE_URL", "")
            run_worker(database_url, args.once, args.interval)
            return 0
        if args.command == "prepare-fix":
            evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
            result = prepare(evidence, args.checkout, args.allow_path, args.output, replay=args.replay)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "test-fix":
            evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
            result = run_tests(evidence, args.checkout, args.allow_path, args.test_command, args.output,
                               replay=args.replay, timeout=args.timeout)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "COMPLETED" else 2
        if args.command == "task":
            contract = build_contract(args.requirement, args.repo)
            folder = args.output.resolve()
            folder.mkdir(parents=True, exist_ok=True)
            write_json(folder / "task.json", contract)
            (folder / "task.md").write_text(render_contract(contract), encoding="utf-8")
            print(json.dumps({"contract": str(folder / "task.json"), "markdown": str(folder / "task.md"),
                              "context_files": len(contract["context_files"]),
                              "test_commands": len(contract["suggested_test_commands"])}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "benchmark":
            result = load_and_analyze(args.input)
            folder = args.output.resolve()
            folder.mkdir(parents=True, exist_ok=True)
            write_json(folder / "benchmark.json", result)
            (folder / "benchmark.md").write_text(render_markdown(result), encoding="utf-8")
            (folder / "benchmark.html").write_text(render_benchmark_html(result), encoding="utf-8")
            print(json.dumps({"status": result["status"], "claim_allowed": result["claim_allowed"],
                              "summary": result["summary"], "report": str(folder / "benchmark.html")},
                             ensure_ascii=False, indent=2))
            return 0
        if args.command == "record":
            result = record_observation(args.dataset, args.scope, args.task_id, args.task_type, args.variant,
                                        args.human_minutes, args.quality_passed, args.rework_count,
                                        args.evidence, args.same_scope)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "doctor":
            print(json.dumps(write_doctor_report(inspect_repo(args.repo), args.output), ensure_ascii=False, indent=2))
            return 0
        if args.command == "workflow":
            folder = args.output.resolve() / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
            result = run_workflow(args.requirement, args.repo, folder, args.github_repo, args.pr,
                                  args.include_failed_logs)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["status"] != "BLOCKED" else 2
        if args.command == "draft-pr":
            folder = args.output.resolve() / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
            result = publish_draft_pr(args.repo, args.github_repo, args.base, args.head, args.allow_path,
                                      args.title, args.body_file, folder, args.confirm_create)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "implement":
            contract = json.loads(args.contract.read_text(encoding="utf-8"))
            policy = load_policy(args.policy) if args.policy is not None else None
            result = run_implementation(contract, args.checkout, args.allow_path, args.test_command,
                                        args.output, timeout=args.timeout, ai_timeout=args.ai_timeout, policy=policy)
            print(json.dumps({"status": result["status"], "tests": result["tests"],
                              "paths": result["paths"], "folder": result["folder"],
                              "report": str(Path(result["folder"]) / "report.html"),
                              "original_checkout_modified": result["original_checkout_modified"],
                              "remote_modified": result["remote_modified"]}, ensure_ascii=False, indent=2))
            return 0 if result["status"] == "READY_FOR_HUMAN_REVIEW" else 2
        if args.command == "policy":
            policy = create_policy(args.name, args.allow_path, args.test_command)
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            write_json(output, policy)
            print(json.dumps({"policy": str(output), "name": policy["name"],
                              "allowed_paths": policy["allowed_paths"]}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "board":
            board_result = build_board(args.runs, args.measurements)
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            write_json(output.with_suffix(".json"), board_result)
            output.write_text(render_board_html(board_result), encoding="utf-8")
            print(json.dumps({"board": str(output), "data": str(output.with_suffix(".json")),
                              "recorded_runs": board_result["summary"]["recorded_runs"],
                              "remote_modified": False}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "team":
            if args.team_command == "publish-snapshot":
                from .service_client import publish_snapshot
                result = publish_snapshot(args.server, args.workspace_id, args.snapshot, args.token_env)
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            if args.team_command == "init":
                workspace = create_workspace(args.name)
                write_workspace(args.output, workspace)
                print(json.dumps({"workspace": str(args.output.resolve()), "name": workspace["name"]}, ensure_ascii=False, indent=2))
                return 0
            if args.team_command == "add-project":
                workspace = add_project(args.workspace, args.id, args.runs, args.measurements, args.policy,
                                        args.github_repo, args.pr)
                print(json.dumps({"workspace": str(args.workspace.resolve()), "projects": len(workspace["projects"])}, ensure_ascii=False, indent=2))
                return 0
            if args.team_command == "set-github":
                workspace = set_github_target(args.workspace, args.id, args.github_repo, args.pr)
                print(json.dumps({"workspace": str(args.workspace.resolve()), "project": args.id,
                                  "github_repo": args.github_repo, "pr": args.pr,
                                  "projects": len(workspace["projects"])}, ensure_ascii=False, indent=2))
                return 0
            if args.team_command == "export":
                snapshot = load_github_snapshot(args.github_snapshot) if args.github_snapshot else None
                print(json.dumps(export_team_bundle(args.workspace, args.output, snapshot), ensure_ascii=False, indent=2))
                return 0
            if args.team_command == "sync-github":
                folder = args.output.resolve() / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
                folder.mkdir(parents=True, exist_ok=False)
                snapshot = sync_team_github(args.workspace)
                team_result = build_team_board(args.workspace, snapshot)
                write_json(folder / "team-github.json", snapshot)
                write_json(folder / "team-board.json", team_result)
                (folder / "team-board.html").write_text(render_team_html(team_result), encoding="utf-8")
                print(json.dumps({"status": snapshot["status"], "folder": str(folder),
                                  "snapshot": str(folder / "team-github.json"),
                                  "board": str(folder / "team-board.html"),
                                  "synced_projects": snapshot["summary"]["synced_projects"],
                                  "remote_modified": False}, ensure_ascii=False, indent=2))
                return 0 if snapshot["status"] == "COMPLETED" else 2
            team_result = build_team_board(args.workspace)
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            write_json(output.with_suffix(".json"), team_result)
            output.write_text(render_team_html(team_result), encoding="utf-8")
            print(json.dumps({"board": str(output), "data": str(output.with_suffix(".json")),
                              "projects": team_result["summary"]["registered_projects"], "remote_modified": False}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "verify":
            saved = json.loads(args.evidence.read_text(encoding="utf-8"))["snapshot"]
            if binding(saved) != saved["binding"]:
                raise ReviewError("Saved evidence fingerprint mismatch")
            current = capture(saved["repo"], saved["pr"]["number"])
            result = compare(saved, current)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if result["status"] == "STALE" else 0
        started = time.monotonic()
        folder = args.output.resolve() / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        folder.mkdir(parents=True, exist_ok=False)
        events = []

        def event(stage, **extra):
            events.append({"at": now(), "stage": stage, **extra})
            write_json(folder / "events.json", events)

        event("COLLECTING", repo=args.repo, pr=args.pr)
        try:
            snapshot = capture(args.repo, args.pr, include_logs=args.include_failed_logs)
            event("CAPTURED", binding=snapshot["binding"])
            write_json(folder / "snapshot.json", snapshot)
            policy = assess(snapshot)
            ai = {"status": "not_requested", "cost_money": None}
            if args.ai:
                event("AI_RUNNING")
                print("Collected GitHub evidence; Codex diagnosis is running…", flush=True)
                try:
                    ai = diagnose(snapshot)
                except ReviewError as exc:
                    ai = {"status": "failed", "error": str(exc), "cost_money": None}
            event("REPORTING", ai_status=ai["status"])
            evidence = {"schema_version": 1, "snapshot": snapshot, "assessment": policy, "ai": ai,
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "human_baseline_minutes": None, "efficiency_improvement_percent": None}
            write_json(folder / "evidence.json", evidence)
            (folder / "report.html").write_text(render(evidence), encoding="utf-8")
            event("COMPLETED" if ai["status"] != "failed" else "AI_FAILED_REPORT_AVAILABLE")
            print(json.dumps({"report": str(folder / "report.html"), "evidence": str(folder / "evidence.json"),
                              "assessment": policy["status"], "ai": ai["status"],
                              "elapsed_seconds": evidence["elapsed_seconds"]}, ensure_ascii=False, indent=2))
            return 1 if ai["status"] == "failed" else 0
        except ReviewError as exc:
            event("FAILED", error=str(exc))
            raise
    except (ReviewError, OSError, ValueError, KeyError) as exc:
        print(f"yanxu: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
