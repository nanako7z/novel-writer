#!/usr/bin/env python3
"""Audit Round Log — per-chapter audit-revise round persistence.

The audit-revise loop runs <= 3 iterations.  Each iteration produces an
audit result + (if not passed) a reviser action.  Without persistence,
round i+1 only "knows" what round i found through Claude's in-context
memory — fragile if context is compacted or the auditor forgets which
issues were already targeted.

This script serializes each round to a per-round JSON artifact:

    books/<id>/story/runtime/chapter-{NNNN}.audit-r{i}.json

Round indexing is 0-based.  Round 0 is the initial audit before any
revise; round i (i > 0) is the audit AFTER reviser pass i.

File shape (per round):

    {
      "chapter": 12,
      "round": 0,
      "timestamp": "2026-05-02T10:11:12.345Z",
      "audit": {
        "overall_score": 78,
        "passed": false,
        "issues": [
          {"dim": 9, "severity": "critical", "category": "POV violation",
           "description": "...", "evidence": "..."},
          ...
        ]
      },
      "deterministic_gates": {
        "ai_tells":          {"critical": 0, "warning": 2},
        "sensitive":         {"blocked": false},
        "post_write":        {"critical": 0, "warning": 1},
        "fatigue":           {"critical": 0, "warning": 0},
        "commitment_ledger": {"violations": 0}
      },
      "reviser_action": {
        "mode": "polish|rewrite|rework|anti-detect|spot-fix",
        "target_issues": ["..."],
        "outcome": "applied|skipped"
      },
      "delta": {
        "score_change": 5,
        "issues_resolved": ["..."],
        "issues_introduced": ["..."]
      }
    }

CLI:
    python audit_round_log.py --book <bookDir> --chapter N --round i \\
        --write <round-data.json>
    python audit_round_log.py --book <bookDir> --chapter N --list [--json]
    python audit_round_log.py --book <bookDir> --chapter N --read \\
        --round i [--json]
    python audit_round_log.py --book <bookDir> --chapter N --analyze [--json]
    python audit_round_log.py --book <bookDir> --chapter N --clear

Semantics:
    --write    : write round artifact; computes delta vs previous round
                 (round i-1) automatically; atomic write.
    --list     : list all rounds (round number + score + passed + issue count).
    --read     : read one round artifact verbatim.
    --analyze  : cross-round analysis — score progression, stagnation
                 detection, recurring issues.
    --clear    : remove all chapter-{NNNN}.audit-r*.json for this chapter.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Single source of truth for the on-disk schema version.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _schema import SCHEMA_VERSION  # noqa: E402
from _summary import emit_summary  # noqa: E402

# Stagnation detection: if the same critical-issue description appears
# in 2+ consecutive rounds, planner should escalate the reviser mode.
STAGNATION_MIN_CONSECUTIVE = 2
RECURRING_MIN_ROUNDS = 2

VALID_SEVERITIES = ("critical", "warning", "info")
VALID_REVISER_MODES = (
    "polish", "rewrite", "rework", "anti-detect", "spot-fix", "auto",
)
VALID_OUTCOMES = ("applied", "skipped")

ROUND_FILE_RE = re.compile(r"^chapter-(\d{4})\.audit-r(\d+)\.json$")


# ---------- io helpers ----------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _runtime_dir(book_dir: Path) -> Path:
    return book_dir / "story" / "runtime"


def _round_path(book_dir: Path, chapter: int, rnd: int) -> Path:
    return _runtime_dir(book_dir) / f"chapter-{chapter:04d}.audit-r{rnd}.json"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _read_round(book_dir: Path, chapter: int, rnd: int) -> dict | None:
    p = _round_path(book_dir, chapter, rnd)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"error: {p} not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)


def _list_round_paths(book_dir: Path, chapter: int) -> list[tuple[int, Path]]:
    rt = _runtime_dir(book_dir)
    if not rt.is_dir():
        return []
    out: list[tuple[int, Path]] = []
    for entry in rt.iterdir():
        if not entry.is_file():
            continue
        m = ROUND_FILE_RE.match(entry.name)
        if not m:
            continue
        if int(m.group(1)) != chapter:
            continue
        out.append((int(m.group(2)), entry))
    out.sort(key=lambda x: x[0])
    return out


# ---------- validation ----------------------------------------------------

def _validate_payload(data: Any, expected_chapter: int,
                      expected_round: int) -> list[str]:
    """Return a list of error strings; empty means valid."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["payload must be a JSON object"]

    chap = data.get("chapter")
    if chap is None:
        errors.append("missing 'chapter'")
    elif not isinstance(chap, int):
        errors.append(f"'chapter' must be int, got {type(chap).__name__}")
    elif chap != expected_chapter:
        errors.append(
            f"'chapter' = {chap} but --chapter = {expected_chapter}"
        )

    rnd = data.get("round")
    if rnd is None:
        errors.append("missing 'round'")
    elif not isinstance(rnd, int):
        errors.append(f"'round' must be int, got {type(rnd).__name__}")
    elif rnd != expected_round:
        errors.append(f"'round' = {rnd} but --round = {expected_round}")
    elif rnd < 0:
        errors.append(f"'round' must be >= 0, got {rnd}")

    audit = data.get("audit")
    if not isinstance(audit, dict):
        errors.append("'audit' must be an object")
    else:
        score = audit.get("overall_score")
        if not isinstance(score, (int, float)):
            errors.append("'audit.overall_score' must be a number")
        passed = audit.get("passed")
        if not isinstance(passed, bool):
            errors.append("'audit.passed' must be a boolean")
        issues = audit.get("issues")
        if not isinstance(issues, list):
            errors.append("'audit.issues' must be an array")
        else:
            for i, item in enumerate(issues):
                if not isinstance(item, dict):
                    errors.append(f"'audit.issues[{i}]' must be an object")
                    continue
                sev = item.get("severity")
                if sev not in VALID_SEVERITIES:
                    errors.append(
                        f"'audit.issues[{i}].severity' must be one of "
                        f"{VALID_SEVERITIES}, got {sev!r}"
                    )

    gates = data.get("deterministic_gates")
    if gates is not None and not isinstance(gates, dict):
        errors.append("'deterministic_gates' must be an object if present")

    rev = data.get("reviser_action")
    if rev is not None:
        if not isinstance(rev, dict):
            errors.append("'reviser_action' must be an object if present")
        else:
            mode = rev.get("mode")
            if mode is not None and mode not in VALID_REVISER_MODES:
                errors.append(
                    f"'reviser_action.mode' must be in {VALID_REVISER_MODES}, "
                    f"got {mode!r}"
                )
            outcome = rev.get("outcome")
            if outcome is not None and outcome not in VALID_OUTCOMES:
                errors.append(
                    f"'reviser_action.outcome' must be in {VALID_OUTCOMES}, "
                    f"got {outcome!r}"
                )

    return errors


# ---------- delta computation --------------------------------------------

def _issue_id(issue: dict) -> str:
    """Stable identifier for an issue across rounds.

    Prefer description (most semantically distinguishing); fall back to
    (dim, category, severity) tuple.  inkos issues rarely have a real id
    field, so we synthesize one.
    """
    desc = (issue.get("description") or "").strip()
    if desc:
        return desc
    parts = [
        str(issue.get("dim", "")),
        str(issue.get("category", "")),
        str(issue.get("severity", "")),
    ]
    return "|".join(parts)


def _compute_delta(curr: dict, prev: dict | None) -> dict:
    curr_issues = curr.get("audit", {}).get("issues", []) or []
    curr_score = curr.get("audit", {}).get("overall_score", 0) or 0
    curr_ids = {_issue_id(i) for i in curr_issues}

    if prev is None:
        return {
            "score_change": 0,
            "issues_resolved": [],
            "issues_introduced": [_issue_id(i) for i in curr_issues],
        }

    prev_issues = prev.get("audit", {}).get("issues", []) or []
    prev_score = prev.get("audit", {}).get("overall_score", 0) or 0
    prev_ids = {_issue_id(i) for i in prev_issues}

    return {
        "score_change": curr_score - prev_score,
        "issues_resolved": sorted(prev_ids - curr_ids),
        "issues_introduced": sorted(curr_ids - prev_ids),
    }


# ---------- commands ------------------------------------------------------

def cmd_write(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        return {"ok": False, "error": f"book directory not found: {book_dir}"}

    src = Path(args.write)
    if not src.is_file():
        return {"ok": False, "error": f"input file not found: {src}"}
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"invalid JSON in {src}: {e}"}

    errors = _validate_payload(data, args.chapter, args.round)
    if errors:
        return {"ok": False, "errors": errors}

    # Stamp timestamp if missing.
    if "timestamp" not in data or not data["timestamp"]:
        data["timestamp"] = _now()

    # Stamp schema version (forward-compat marker — see scripts/_schema.py).
    # If the caller already provided one, keep theirs; mismatch is the caller's
    # responsibility to handle (this script is an emitter, not a migrator).
    data.setdefault("schemaVersion", SCHEMA_VERSION)

    # Compute delta vs previous round, if any.
    prev = (
        _read_round(book_dir, args.chapter, args.round - 1)
        if args.round > 0 else None
    )
    data["delta"] = _compute_delta(data, prev)

    out_path = _round_path(book_dir, args.chapter, args.round)
    _atomic_write(out_path, json.dumps(data, ensure_ascii=False, indent=2))

    return {
        "ok": True,
        "path": str(out_path),
        "chapter": args.chapter,
        "round": args.round,
        "delta": data["delta"],
        "previousRoundFound": prev is not None,
    }


def cmd_list(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    # Read-only — missing book dir / runtime dir → empty list, not error.
    if not book_dir.is_dir():
        return {
            "ok": True,
            "chapter": args.chapter,
            "totalRounds": 0,
            "rounds": [],
            "warning": f"book directory not found: {book_dir}",
        }

    pairs = _list_round_paths(book_dir, args.chapter)
    rounds: list[dict] = []
    for rnd, path in pairs:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            rounds.append({
                "round": rnd,
                "path": str(path),
                "error": f"invalid JSON: {e}",
            })
            continue
        audit = doc.get("audit", {}) or {}
        issues = audit.get("issues", []) or []
        rev = doc.get("reviser_action") or {}
        rounds.append({
            "round": rnd,
            "path": str(path),
            "score": audit.get("overall_score"),
            "passed": audit.get("passed"),
            "issueCount": len(issues),
            "criticalCount": sum(
                1 for i in issues if i.get("severity") == "critical"
            ),
            "reviserMode": rev.get("mode"),
            "timestamp": doc.get("timestamp"),
        })
    return {
        "ok": True,
        "chapter": args.chapter,
        "totalRounds": len(rounds),
        "rounds": rounds,
    }


def cmd_read(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        return {"ok": False, "error": f"book directory not found: {book_dir}"}
    if args.round is None:
        return {"ok": False, "error": "--read requires --round"}

    doc = _read_round(book_dir, args.chapter, args.round)
    if doc is None:
        return {
            "ok": False,
            "error": (
                f"no artifact for chapter {args.chapter} round {args.round}"
            ),
            "path": str(_round_path(book_dir, args.chapter, args.round)),
        }
    return {
        "ok": True,
        "chapter": args.chapter,
        "round": args.round,
        "path": str(_round_path(book_dir, args.chapter, args.round)),
        "data": doc,
    }


def _analyze_recurring(rounds: list[dict]) -> list[dict]:
    """Group issues by description across rounds; flag ones that appear
    in >= RECURRING_MIN_ROUNDS distinct rounds."""
    by_desc: dict[str, set[int]] = {}
    by_desc_severity: dict[str, str] = {}
    by_desc_category: dict[str, str] = {}
    for doc in rounds:
        rnd = doc.get("round")
        issues = (doc.get("audit") or {}).get("issues", []) or []
        for issue in issues:
            desc = (issue.get("description") or "").strip()
            if not desc:
                continue
            by_desc.setdefault(desc, set()).add(rnd)
            # Remember worst-seen severity (critical > warning > info)
            sev = issue.get("severity", "info")
            prev_sev = by_desc_severity.get(desc, "info")
            order = {"critical": 3, "warning": 2, "info": 1}
            if order.get(sev, 0) > order.get(prev_sev, 0):
                by_desc_severity[desc] = sev
            by_desc_category.setdefault(desc, issue.get("category", ""))

    recurring: list[dict] = []
    for desc, rs in by_desc.items():
        if len(rs) >= RECURRING_MIN_ROUNDS:
            recurring.append({
                "description": desc,
                "category": by_desc_category.get(desc, ""),
                "severity": by_desc_severity.get(desc, "info"),
                "appearedInRounds": sorted(rs),
                "roundsCount": len(rs),
            })
    recurring.sort(
        key=lambda r: (-r["roundsCount"], r["description"])
    )
    return recurring


def _detect_stagnation(rounds: list[dict], recurring: list[dict]) -> bool:
    """A critical issue appearing in >= 2 consecutive rounds means the
    reviser failed to address it.  Caller should escalate mode."""
    if not recurring:
        return False
    rnd_nums = [r.get("round") for r in rounds]
    for rec in recurring:
        if rec["severity"] != "critical":
            continue
        rs = rec["appearedInRounds"]
        # Check consecutive: any pair (a, a+1) where both are present in
        # actual round set.
        rs_set = set(rs)
        for r in rs:
            if (r + 1) in rs_set and (r + 1) in rnd_nums:
                return True
    return False


def cmd_analyze(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    # Read-only — missing book dir → empty analysis, not error.
    if not book_dir.is_dir():
        return {
            "ok": True,
            "chapter": args.chapter,
            "totalRounds": 0,
            "scoreProgression": [],
            "stagnationDetected": False,
            "recurringIssues": [],
            "summary": "no rounds recorded yet",
            "warning": f"book directory not found: {book_dir}",
        }

    pairs = _list_round_paths(book_dir, args.chapter)
    rounds: list[dict] = []
    for rnd, path in pairs:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rounds.append(doc)

    if not rounds:
        return {
            "ok": True,
            "chapter": args.chapter,
            "totalRounds": 0,
            "scoreProgression": [],
            "stagnationDetected": False,
            "recurringIssues": [],
            "summary": "no rounds recorded yet",
        }

    progression = [
        (r.get("audit") or {}).get("overall_score") for r in rounds
    ]
    recurring = _analyze_recurring(rounds)
    stagnation = _detect_stagnation(rounds, recurring)

    # Extract readerExpectationSignal from the LAST round's audit object —
    # this is what the next chapter's Planner reads to set chapter posture.
    # Field is optional in the audit schema; absent → expose null.
    last_audit = (rounds[-1].get("audit") or {}) if rounds else {}
    reader_expectation_signal = last_audit.get("readerExpectationSignal")

    # Build a one-line summary.
    last_score = progression[-1] if progression else None
    first_score = progression[0] if progression else None
    if last_score is not None and first_score is not None:
        gain = last_score - first_score
    else:
        gain = 0
    crit_recurring = [r for r in recurring if r["severity"] == "critical"]
    summary_bits = [
        f"{len(rounds)} round(s)",
        f"score {first_score} -> {last_score} (delta {gain:+d})"
            if last_score is not None and first_score is not None
            else "no scores",
    ]
    if stagnation:
        summary_bits.append(
            f"STAGNATION: {len(crit_recurring)} critical issue(s) "
            f"persist across consecutive rounds — escalate reviser mode"
        )
    elif recurring:
        summary_bits.append(
            f"{len(recurring)} recurring issue(s)"
        )

    return {
        "ok": True,
        "chapter": args.chapter,
        "totalRounds": len(rounds),
        "scoreProgression": progression,
        "stagnationDetected": stagnation,
        "recurringIssues": recurring,
        "readerExpectationSignal": reader_expectation_signal,
        "summary": "; ".join(summary_bits),
    }


def cmd_clear(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        return {"ok": False, "error": f"book directory not found: {book_dir}"}

    pairs = _list_round_paths(book_dir, args.chapter)
    removed: list[str] = []
    for _, path in pairs:
        try:
            path.unlink()
            removed.append(str(path))
        except OSError as e:
            return {
                "ok": False,
                "error": f"failed to remove {path}: {e}",
                "removed": removed,
            }
    return {
        "ok": True,
        "chapter": args.chapter,
        "removedCount": len(removed),
        "removed": removed,
    }


# ---------- argparse glue --------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="audit_round_log.py",
        description=(
            "Per-round audit-revise persistence "
            "(books/<id>/story/runtime/chapter-{NNNN}.audit-r{i}.json)."
        ),
    )
    ap.add_argument("--book", required=True, help="path to book directory")
    ap.add_argument("--chapter", type=int, required=True,
                    help="chapter number (>=1)")
    ap.add_argument("--round", type=int, default=None,
                    help="0-based round index (required for --write/--read)")
    ap.add_argument("--json", action="store_true",
                    help="JSON output (default; flag accepted for symmetry)")

    # Mode flags (mutually exclusive).
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", metavar="ROUND_DATA_JSON",
                      help="write a round artifact from a JSON file")
    mode.add_argument("--list", action="store_true",
                      help="list all rounds for this chapter")
    mode.add_argument("--read", action="store_true",
                      help="read one round artifact (requires --round)")
    mode.add_argument("--analyze", action="store_true",
                      help="cross-round delta + stagnation analysis")
    mode.add_argument("--clear", action="store_true",
                      help="remove all round artifacts for this chapter")

    args = ap.parse_args()

    if args.write is not None:
        if args.round is None:
            ap.error("--write requires --round")
        result = cmd_write(args)
    elif args.list:
        result = cmd_list(args)
    elif args.read:
        if args.round is None:
            ap.error("--read requires --round")
        result = cmd_read(args)
    elif args.analyze:
        result = cmd_analyze(args)
    elif args.clear:
        result = cmd_clear(args)
    else:  # pragma: no cover — argparse enforces required group
        ap.error("no mode specified")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    action = (
        "write" if args.write is not None else
        "list" if args.list else
        "read" if args.read else
        "analyze" if args.analyze else
        "clear" if args.clear else "?"
    )
    ok = bool(result.get("ok"))
    if ok:
        emit_summary(
            f"action={action} ch={args.chapter} "
            f"round={args.round if args.round is not None else '-'}"
        )
    else:
        emit_summary(
            f"FAILED: action={action} ch={args.chapter} "
            f"error={result.get('error', 'unknown')}",
            prefix="error",
        )
    if not result.get("ok"):
        sys.exit(2)


if __name__ == "__main__":
    main()
