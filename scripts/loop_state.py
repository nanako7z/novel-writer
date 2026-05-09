#!/usr/bin/env python3
"""loop_state — step-checkpoint enforcement for the writeNextChapter main loop.

Records per-chapter step progress to `story/runtime/loop_state-{NNNN}.json`
and refuses to advance when prerequisite steps are missing. Turns the LLM's
self-discipline into a system check: skipping a step makes the next `require`
exit non-zero, forcing the LLM to go back and fill the gap.

Stdlib only. State schema:

    {
      "chapter": 12,
      "currentStep": "5c",
      "completed": ["1", "2", "3", "5", "5b"],
      "lastUpdate": "2026-05-09T12:34:56Z",
      "stepArtifacts": {
        "2": "story/runtime/chapter_memo.md",
        "5": "story/raw_writer/chapter-0012.md"
      }
    }

Step IDs follow 00-orchestration.md numbering: 1, 2, 3, 4, 5, 5b, 5c, 6, 7,
7.5, 8, 9, 10, 10.1, 10.5, 11, 11.0a, 11.0b, 11.0c, 11.05, 11.1, 11.2.

Usage:

    loop_state.py begin   --book <bd> --chapter N [--allow-replay]
    loop_state.py mark    --book <bd> --chapter N --step <id> [--artifact <path>]
    loop_state.py require --book <bd> --chapter N --step <id>
    loop_state.py end     --book <bd> --chapter N
    loop_state.py status  --book <bd> [--chapter N] [--json]

Exit codes:
    0 — ok / state advanced
    1 — usage / IO error
    2 — mark: artifact missing on disk
    3 — require: prerequisite steps missing (lists which)
    4 — end: critical steps not completed (5b/5c/7/9/10/11/11.05)
    5 — begin: state file already exists for an in-flight chapter (use --allow-replay)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Step ordering of the main loop (writeNextChapter). Entry order = required
# execution order; later entries depend on all earlier ones being marked.
STEP_ORDER: list[str] = [
    "1",      # preflight
    "2",      # plan
    "3",      # compose
    "4",      # architect (conditional)
    "5",      # write
    "5b",     # writer_parse
    "5c",     # post_write_validate
    "6",      # length normalize
    "7",      # audit-revise (whole loop)
    "7.5",    # auto-write chapters/index.json auditRoundAnalysis
    "8",      # observe
    "9",      # settle
    "10",     # apply_delta
    "10.1",   # hook_governance promote-pass
    "10.5",   # polisher (conditional)
    "11",     # final write chapters/{NNNN}.md + chapter_index add
    "11.0a",  # snapshot_state
    "11.0b",  # audit_drift write
    "11.0c",  # docops_drift write
    "11.05",  # chapter analyzer
    "11.1",   # consolidate suggestion (advisory)
    "11.2",   # volume-payoff (conditional, volume finale only)
]

# Steps that may be conditionally skipped — `end` will not punish if missing.
CONDITIONAL_STEPS: set[str] = {
    "4",       # only first chapter / volume transition
    "10.5",    # only audit ≥ 88
    "11.0a",   # snapshot_state failure is non-fatal
    "11.0b",   # may be empty when audit clean
    "11.0c",   # may be empty when no drift
    "11.1",    # advisory only
    "11.2",    # only volume finale
    "7.5",     # advisory write to chapter_index
}

# Steps that MUST be completed before `end` accepts the chapter.
CRITICAL_STEPS: set[str] = {
    "1", "2", "3", "5", "5b", "5c", "7", "9", "10", "10.1", "11", "11.05",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_path(book_dir: Path, chapter: int) -> Path:
    return book_dir / "story" / "runtime" / f"loop_state-{chapter:04d}.json"


def history_dir(book_dir: Path) -> Path:
    return book_dir / "story" / "runtime" / "loop_state.history"


def load_state(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"loop_state: failed to read {path}: {exc}\n")
        return None


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["lastUpdate"] = now_iso()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def step_index(step: str) -> int:
    try:
        return STEP_ORDER.index(step)
    except ValueError:
        sys.stderr.write(
            f"loop_state: unknown step id '{step}'. Known: {', '.join(STEP_ORDER)}\n"
        )
        sys.exit(1)


def missing_prereqs(state: dict, target: str) -> list[str]:
    """Return the list of step IDs that should have been marked but weren't."""
    target_idx = step_index(target)
    completed = set(state.get("completed", []))
    missing: list[str] = []
    for prior in STEP_ORDER[:target_idx]:
        if prior in CONDITIONAL_STEPS:
            continue
        if prior not in completed:
            missing.append(prior)
    return missing


# ── command handlers ──────────────────────────────────────────────────────


def cmd_begin(args) -> int:
    sp = state_path(Path(args.book), args.chapter)
    existing = load_state(sp)
    if existing is not None and not existing.get("ended") and not args.allow_replay:
        sys.stderr.write(
            f"loop_state: in-flight state exists for chapter {args.chapter} at {sp}.\n"
            f"  currentStep={existing.get('currentStep')}, "
            f"completed={existing.get('completed')}\n"
            f"  Use --allow-replay to overwrite, or call `recover_chapter.py` first.\n"
        )
        return 5
    state = {
        "chapter": args.chapter,
        "currentStep": "1",
        "completed": [],
        "stepArtifacts": {},
        "started": now_iso(),
    }
    save_state(sp, state)
    print(json.dumps({"ok": True, "stateFile": str(sp)}))
    return 0


def cmd_mark(args) -> int:
    sp = state_path(Path(args.book), args.chapter)
    state = load_state(sp)
    if state is None:
        sys.stderr.write(
            f"loop_state: no state file for chapter {args.chapter}; call `begin` first.\n"
        )
        return 1
    if args.step not in STEP_ORDER:
        sys.stderr.write(f"loop_state: unknown step '{args.step}'\n")
        return 1
    # artifact existence check (only when --artifact given and not "-")
    if args.artifact and args.artifact != "-":
        artifact_path = Path(args.artifact)
        if not artifact_path.is_absolute():
            artifact_path = Path(args.book) / args.artifact
        if not artifact_path.exists():
            sys.stderr.write(
                f"loop_state: step {args.step} mark failed — artifact missing: "
                f"{artifact_path}\n"
            )
            return 2
        state.setdefault("stepArtifacts", {})[args.step] = args.artifact
    if args.step not in state["completed"]:
        state["completed"].append(args.step)
    state["currentStep"] = args.step
    save_state(sp, state)
    print(json.dumps({"ok": True, "step": args.step, "completed": state["completed"]}))
    return 0


def cmd_require(args) -> int:
    sp = state_path(Path(args.book), args.chapter)
    state = load_state(sp)
    if state is None:
        sys.stderr.write(
            f"loop_state: no state file for chapter {args.chapter}; call `begin` first.\n"
        )
        return 1
    missing = missing_prereqs(state, args.step)
    if missing:
        sys.stderr.write(
            f"loop_state: cannot enter step {args.step} — missing prerequisites: "
            f"{', '.join(missing)}\n"
            f"  Go back and complete those steps (run `mark` after each), then retry.\n"
        )
        # Also print as JSON to stdout so callers can parse.
        print(
            json.dumps({"ok": False, "step": args.step, "missing": missing}),
            file=sys.stdout,
        )
        return 3
    print(json.dumps({"ok": True, "step": args.step}))
    return 0


def cmd_end(args) -> int:
    sp = state_path(Path(args.book), args.chapter)
    state = load_state(sp)
    if state is None:
        sys.stderr.write(
            f"loop_state: no state file for chapter {args.chapter}; nothing to end.\n"
        )
        return 1
    completed = set(state.get("completed", []))
    missing_critical = sorted(CRITICAL_STEPS - completed, key=step_index)
    if missing_critical:
        sys.stderr.write(
            f"loop_state: cannot end chapter {args.chapter} — critical steps not "
            f"completed: {', '.join(missing_critical)}\n"
        )
        return 4
    state["ended"] = now_iso()
    save_state(sp, state)
    # Archive
    hist = history_dir(Path(args.book))
    hist.mkdir(parents=True, exist_ok=True)
    archived = hist / sp.name
    sp.replace(archived)
    print(json.dumps({"ok": True, "archived": str(archived)}))
    return 0


def cmd_status(args) -> int:
    book = Path(args.book)
    if args.chapter is not None:
        sp = state_path(book, args.chapter)
        state = load_state(sp)
        if state is None:
            print(json.dumps({"chapter": args.chapter, "exists": False}))
            return 0
        out = {
            "chapter": args.chapter,
            "exists": True,
            "currentStep": state.get("currentStep"),
            "completed": state.get("completed", []),
            "ended": state.get("ended", False),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2 if not args.json else None))
        return 0
    # all in-flight
    runtime = book / "story" / "runtime"
    inflight = []
    if runtime.is_dir():
        for p in sorted(runtime.glob("loop_state-*.json")):
            st = load_state(p)
            if st is None:
                continue
            inflight.append({
                "chapter": st.get("chapter"),
                "currentStep": st.get("currentStep"),
                "completed": st.get("completed", []),
            })
    print(json.dumps({"inflight": inflight}, ensure_ascii=False, indent=2))
    return 0


# ── argparse wiring ───────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="loop_state.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--book", required=True, help="Path to book directory (books/<id>)")

    pb = sub.add_parser("begin", parents=[common])
    pb.add_argument("--chapter", type=int, required=True)
    pb.add_argument("--allow-replay", action="store_true",
                    help="Overwrite an existing in-flight state file (rerun this chapter)")
    pb.set_defaults(func=cmd_begin)

    pm = sub.add_parser("mark", parents=[common])
    pm.add_argument("--chapter", type=int, required=True)
    pm.add_argument("--step", required=True)
    pm.add_argument("--artifact", default=None,
                    help="Optional path (relative to book dir or absolute) — checked for existence")
    pm.set_defaults(func=cmd_mark)

    pr = sub.add_parser("require", parents=[common])
    pr.add_argument("--chapter", type=int, required=True)
    pr.add_argument("--step", required=True)
    pr.set_defaults(func=cmd_require)

    pe = sub.add_parser("end", parents=[common])
    pe.add_argument("--chapter", type=int, required=True)
    pe.set_defaults(func=cmd_end)

    ps = sub.add_parser("status", parents=[common])
    ps.add_argument("--chapter", type=int, default=None)
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
