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


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only GitHub PR/CI diagnosis. No merge or code execution.")
    sub = parser.add_subparsers(dest="command", required=True)
    review = sub.add_parser("review", help="Collect evidence and generate a local HTML report")
    review.add_argument("--repo", required=True, help="GitHub owner/repo")
    review.add_argument("--pr", required=True, type=int)
    review.add_argument("--ai", action="store_true", help="Send collected repository content to Codex for diagnosis")
    review.add_argument("--include-failed-logs", action="store_true", help="Include up to two failed workflow log excerpts")
    review.add_argument("--output", type=Path, default=Path("runs"))
    verify = sub.add_parser("verify", help="Re-fetch GitHub state; reject stale review evidence")
    verify.add_argument("evidence", type=Path)
    args = parser.parse_args(argv)
    try:
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
