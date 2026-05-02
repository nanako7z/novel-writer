#!/usr/bin/env python3
"""Chapter operational index — books/<id>/chapters/index.json.

This file is the **operational** record of every chapter (status, audit
issues, word count, timestamps, token usage, optional detection score).
It is distinct from `story/state/chapter_summaries.json` which is the
**narrative** record (events / mood / hooks / for next chapter's planner).

Inkos's `inkos review list` / `analytics` / `book delete --json` all read
from this file. Without it we'd have to scan the chapters/*.md files and
guess at status — fine for prototypes, brittle in production.

Schema (per `models/chapter.ts` ChapterMetaSchema):

    {
      "number": 1,                      # required, int >= 1
      "title": "...",                   # required
      "status": "ready-for-review",     # enum, see CHAPTER_STATUS
      "wordCount": 2547,                # int, default 0
      "createdAt": "ISO8601",           # required
      "updatedAt": "ISO8601",           # required
      "auditIssues": ["[critical] ...", "[warning] ..."],  # array of formatted strings
      "lengthWarnings": [...],          # array
      "reviewNote": "...",              # optional
      "detectionScore": 0.42,           # optional, 0-1
      "detectionProvider": "gptzero",   # optional
      "detectedAt": "ISO8601",          # optional
      "lengthTelemetry": {...},         # optional, free-form per LengthTelemetrySchema
      "tokenUsage": {                   # optional
        "promptTokens": 0,
        "completionTokens": 0,
        "totalTokens": 0
      }
    }

CLI commands:
    add         upsert new entry (preserves createdAt on conflict)
    update      patch any field(s) on existing entry
    set-status  shorthand for status + reviewNote
    list        all entries with optional filters
    get         one entry by number
    validate    schema + integrity check

Atomic writes: ".tmp + os.replace". JSON output for all commands when
--json given (text output otherwise where it makes sense).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WRITE_COMMANDS = {"add", "update", "set-status"}

CHAPTER_STATUS = {
    "card-generated", "drafting", "drafted",
    "auditing", "audit-passed", "audit-failed", "state-degraded",
    "revising", "ready-for-review",
    "approved", "rejected",
    "published", "imported",
}


def _import_book_lock():
    """Import book_lock in-process so acquire/release share our pid."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import book_lock  # type: ignore
        return book_lock
    except Exception:  # noqa: BLE001
        return None


def _acquire_lock(book_dir: Path, operation: str) -> tuple[bool, dict]:
    """Acquire advisory book write lock in-process. Returns (ok, payload).

    Falls through (returns ok=True) if book_lock module isn't importable —
    the lock is advisory; absence of the module shouldn't block writes.
    """
    bl = _import_book_lock()
    if bl is None:
        return True, {"skipped": True, "reason": "book_lock module not importable"}
    p = bl._lock_path(book_dir)  # noqa: SLF001
    existing = bl._read_lock(p)  # noqa: SLF001
    if existing is not None and not bl._is_expired(existing):  # noqa: SLF001
        return False, {
            "ok": False,
            "result": "refused",
            "reason": "lock held by another owner",
            "lockPath": str(p),
            "currentLock": existing,
        }
    from datetime import datetime, timedelta, timezone
    import socket
    payload = {
        "pid": os.getpid(),
        "operation": operation,
        "acquiredAt": bl._now_iso(),  # noqa: SLF001
        "expiresAt": (datetime.now(timezone.utc) + timedelta(
            seconds=bl.DEFAULT_TTL_SEC,
        )).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "host": socket.gethostname(),
    }
    bl._atomic_write_lock(p, payload)  # noqa: SLF001
    return True, {
        "ok": True, "result": "acquired", "lockPath": str(p), "lock": payload,
    }


def _release_lock(book_dir: Path) -> None:
    bl = _import_book_lock()
    if bl is None:
        return
    p = bl._lock_path(book_dir)  # noqa: SLF001
    existing = bl._read_lock(p)  # noqa: SLF001
    if existing is None or not bl._ours(existing):  # noqa: SLF001
        return
    try:
        os.remove(p)
    except OSError:
        pass  # advisory; don't crash on release failure


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _index_path(book_dir: Path) -> Path:
    return book_dir / "chapters" / "index.json"


def _load(book_dir: Path) -> list[dict]:
    p = _index_path(book_dir)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            print(f"error: {p} is not a JSON array", file=sys.stderr)
            sys.exit(2)
        return data
    except json.JSONDecodeError as e:
        print(f"error: {p} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)


def _save(book_dir: Path, index: list[dict]) -> None:
    p = _index_path(book_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _validate_entry(entry: dict, *, full: bool = True) -> list[str]:
    errors: list[str] = []
    if "number" not in entry:
        errors.append("missing required field: number")
    elif not isinstance(entry["number"], int) or entry["number"] < 1:
        errors.append(f"number must be int >= 1, got {entry.get('number')!r}")

    if full:
        for f in ("title", "createdAt", "updatedAt"):
            if f not in entry or not entry[f]:
                errors.append(f"missing required field: {f}")

    if "status" in entry and entry["status"] not in CHAPTER_STATUS:
        errors.append(
            f"invalid status: {entry['status']!r}; must be one of: {sorted(CHAPTER_STATUS)}"
        )
    if "wordCount" in entry and not isinstance(entry["wordCount"], int):
        errors.append("wordCount must be int")
    if "detectionScore" in entry:
        s = entry["detectionScore"]
        if not isinstance(s, (int, float)) or s < 0 or s > 1:
            errors.append(f"detectionScore must be 0-1, got {s!r}")
    return errors


def _parse_json_arg(s: str | None, name: str) -> Any:
    if s is None:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError as e:
        print(f"error: --{name} not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)


# ---------- commands -----------------------------------------------------

def cmd_add(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    index = _load(book_dir)
    now = _now()

    entry: dict[str, Any] = {
        "number": args.chapter,
        "title": args.title or f"第 {args.chapter} 章",
        "status": args.status,
        "wordCount": args.word_count or 0,
        "createdAt": now,
        "updatedAt": now,
        "auditIssues": _parse_json_arg(args.audit_issues, "audit-issues") or [],
        "lengthWarnings": _parse_json_arg(args.length_warnings, "length-warnings") or [],
    }
    if args.review_note:
        entry["reviewNote"] = args.review_note
    if args.detection_score is not None:
        entry["detectionScore"] = args.detection_score
        entry["detectionProvider"] = args.detection_provider or "unknown"
        entry["detectedAt"] = now
    tu = _parse_json_arg(args.token_usage, "token-usage")
    if tu:
        entry["tokenUsage"] = tu
    lt = _parse_json_arg(args.length_telemetry, "length-telemetry")
    if lt:
        entry["lengthTelemetry"] = lt

    errors = _validate_entry(entry, full=True)
    if errors:
        return {"ok": False, "errors": errors}

    existing_idx = next(
        (i for i, e in enumerate(index) if e.get("number") == args.chapter),
        None,
    )
    if existing_idx is None:
        index.append(entry)
        action = "added"
    else:
        # preserve original createdAt; bump updatedAt
        entry["createdAt"] = index[existing_idx].get("createdAt", now)
        index[existing_idx] = entry
        action = "replaced"

    index.sort(key=lambda e: e.get("number", 0))
    _save(book_dir, index)
    return {"ok": True, "action": action, "entry": entry, "totalEntries": len(index)}


def cmd_update(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    index = _load(book_dir)
    idx = next((i for i, e in enumerate(index) if e.get("number") == args.chapter), None)
    if idx is None:
        return {"ok": False, "error": f"chapter {args.chapter} not in index"}

    entry = dict(index[idx])
    changes: dict[str, Any] = {}
    if args.title is not None:
        changes["title"] = args.title
    if args.status is not None:
        if args.status not in CHAPTER_STATUS:
            return {"ok": False, "error": f"invalid status: {args.status}"}
        changes["status"] = args.status
    if args.word_count is not None:
        changes["wordCount"] = args.word_count
    if args.review_note is not None:
        changes["reviewNote"] = args.review_note
    if args.audit_issues is not None:
        changes["auditIssues"] = _parse_json_arg(args.audit_issues, "audit-issues")
    if args.length_warnings is not None:
        changes["lengthWarnings"] = _parse_json_arg(args.length_warnings, "length-warnings")
    if args.detection_score is not None:
        changes["detectionScore"] = args.detection_score
        if args.detection_provider:
            changes["detectionProvider"] = args.detection_provider
        changes["detectedAt"] = _now()
    if args.token_usage is not None:
        changes["tokenUsage"] = _parse_json_arg(args.token_usage, "token-usage")
    if args.length_telemetry is not None:
        changes["lengthTelemetry"] = _parse_json_arg(args.length_telemetry, "length-telemetry")

    if not changes:
        return {"ok": False, "error": "no fields to update; pass at least one --<field>"}

    entry.update(changes)
    entry["updatedAt"] = _now()

    errors = _validate_entry(entry, full=True)
    if errors:
        return {"ok": False, "errors": errors}

    index[idx] = entry
    _save(book_dir, index)
    return {"ok": True, "action": "updated", "entry": entry, "changedFields": list(changes.keys())}


def cmd_set_status(args: argparse.Namespace) -> dict:
    args.title = None
    args.word_count = None
    args.audit_issues = None
    args.length_warnings = None
    args.detection_score = None
    args.detection_provider = None
    args.token_usage = None
    args.length_telemetry = None
    # status + review-note already on args
    return cmd_update(args)


def cmd_list(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    index = _load(book_dir)
    filtered = index

    if args.status:
        wanted = set(args.status.split(","))
        unknown = wanted - CHAPTER_STATUS
        if unknown:
            return {"ok": False, "error": f"unknown status filter(s): {sorted(unknown)}"}
        filtered = [e for e in filtered if e.get("status") in wanted]

    if args.from_chapter is not None:
        filtered = [e for e in filtered if e.get("number", 0) >= args.from_chapter]
    if args.to_chapter is not None:
        filtered = [e for e in filtered if e.get("number", 0) <= args.to_chapter]

    summary = {
        "totalEntries": len(filtered),
        "byStatus": {},
    }
    for e in filtered:
        s = e.get("status", "unknown")
        summary["byStatus"][s] = summary["byStatus"].get(s, 0) + 1

    return {"ok": True, "entries": filtered, "summary": summary}


def cmd_get(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    index = _load(book_dir)
    entry = next((e for e in index if e.get("number") == args.chapter), None)
    if entry is None:
        return {"ok": False, "error": f"chapter {args.chapter} not in index"}
    return {"ok": True, "entry": entry}


def cmd_validate(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    index = _load(book_dir)
    issues: list[dict] = []

    if not index:
        return {
            "ok": True,
            "totalEntries": 0,
            "issues": [],
            "summary": "empty index (no chapters yet)",
        }

    seen_numbers: dict[int, int] = {}
    for i, e in enumerate(index):
        # per-entry schema
        for err in _validate_entry(e, full=True):
            issues.append({
                "severity": "critical",
                "category": "schema",
                "chapter": e.get("number"),
                "description": err,
            })
        # duplicate number
        n = e.get("number")
        if isinstance(n, int):
            if n in seen_numbers:
                issues.append({
                    "severity": "critical",
                    "category": "duplicate-number",
                    "chapter": n,
                    "description": f"chapter number {n} appears at indices {seen_numbers[n]} and {i}",
                })
            else:
                seen_numbers[n] = i

    # sequence gaps (warning, not critical — chapter splitter / state-degraded can leave gaps)
    if seen_numbers:
        nums = sorted(seen_numbers.keys())
        expected_set = set(range(nums[0], nums[-1] + 1))
        missing = sorted(expected_set - set(nums))
        if missing:
            issues.append({
                "severity": "warning",
                "category": "sequence-gap",
                "chapter": None,
                "description": f"chapter numbers missing in [{nums[0]}, {nums[-1]}]: {missing}",
            })

    # cross-check: each entry has a corresponding chapters/{NNNN}.md file
    for n in seen_numbers:
        f = book_dir / "chapters" / f"{n:04d}.md"
        if not f.is_file():
            issues.append({
                "severity": "warning",
                "category": "missing-file",
                "chapter": n,
                "description": f"index references chapter {n} but file not found: {f}",
            })

    counts = {
        "critical": sum(1 for i in issues if i["severity"] == "critical"),
        "warning": sum(1 for i in issues if i["severity"] == "warning"),
    }
    return {
        "ok": counts["critical"] == 0,
        "totalEntries": len(index),
        "issues": issues,
        "counts": counts,
        "summary": f"entries={len(index)} critical={counts['critical']} warning={counts['warning']}",
    }


# ---------- text formatters ----------------------------------------------

def _format_list_text(result: dict) -> str:
    if not result.get("ok"):
        return f"error: {result.get('error', '')}"
    entries = result.get("entries", [])
    if not entries:
        return "(empty)"

    lines = []
    lines.append(f"Total: {result['summary']['totalEntries']} entries")
    by = result["summary"]["byStatus"]
    if by:
        lines.append("By status: " + ", ".join(f"{k}={v}" for k, v in sorted(by.items())))
    lines.append("")

    fmt = "{n:>4}  {st:<18}  {wc:>6}  {issues:<3}  {title}"
    lines.append(fmt.format(n="ch#", st="status", wc="words", issues="iss", title="title"))
    lines.append("-" * 70)
    for e in entries:
        lines.append(fmt.format(
            n=e.get("number", "?"),
            st=e.get("status", "")[:18],
            wc=e.get("wordCount", 0),
            issues=str(len(e.get("auditIssues", []))),
            title=(e.get("title") or "")[:35],
        ))
    return "\n".join(lines)


def _format_validate_text(result: dict) -> str:
    lines = [f"Validate: {result.get('summary', '')}"]
    for i in result.get("issues", []):
        lines.append(
            f"  [{i['severity']}] {i['category']}: ch{i.get('chapter')} {i.get('description')}"
        )
    return "\n".join(lines)


# ---------- argparse glue -------------------------------------------------

def _add_optional_metadata_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--title", default=None, help="chapter title")
    p.add_argument("--word-count", type=int, default=None, dest="word_count")
    p.add_argument("--audit-issues", default=None, dest="audit_issues",
                   help='JSON array, e.g. \'["[critical] xxx", "[warning] yyy"]\'')
    p.add_argument("--length-warnings", default=None, dest="length_warnings",
                   help="JSON array of warning strings")
    p.add_argument("--review-note", default=None, dest="review_note")
    p.add_argument("--detection-score", type=float, default=None, dest="detection_score")
    p.add_argument("--detection-provider", default=None, dest="detection_provider")
    p.add_argument("--token-usage", default=None, dest="token_usage",
                   help='JSON object, e.g. \'{"promptTokens": 100, "completionTokens": 200, "totalTokens": 300}\'')
    p.add_argument("--length-telemetry", default=None, dest="length_telemetry",
                   help="JSON object")


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="chapter_index.py",
        description="Manage books/<id>/chapters/index.json (operational chapter index)",
    )
    ap.add_argument("--book", required=True, help="path to book directory")
    ap.add_argument("--json", action="store_true", help="JSON output even for human-friendly commands")
    ap.add_argument(
        "--skip-lock", action="store_true",
        help="skip the advisory book write lock (only honored by add/update/set-status; "
        "list/get/validate are read-only and never lock)",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    # add
    p_add = sub.add_parser("add", help="upsert new chapter entry")
    p_add.add_argument("--chapter", type=int, required=True)
    p_add.add_argument("--status", required=True,
                       help=f"one of: {','.join(sorted(CHAPTER_STATUS))}")
    _add_optional_metadata_flags(p_add)

    # update
    p_upd = sub.add_parser("update", help="patch fields on existing chapter entry")
    p_upd.add_argument("--chapter", type=int, required=True)
    p_upd.add_argument("--status", default=None,
                       help=f"one of: {','.join(sorted(CHAPTER_STATUS))}")
    _add_optional_metadata_flags(p_upd)

    # set-status (shorthand)
    p_st = sub.add_parser("set-status", help="shorthand: change status (+ optional review-note)")
    p_st.add_argument("--chapter", type=int, required=True)
    p_st.add_argument("--status", required=True)
    p_st.add_argument("--review-note", default=None, dest="review_note")

    # list
    p_ls = sub.add_parser("list", help="list entries with optional filters")
    p_ls.add_argument("--status", default=None,
                      help="comma-separated; e.g. ready-for-review,approved")
    p_ls.add_argument("--from", type=int, default=None, dest="from_chapter")
    p_ls.add_argument("--to", type=int, default=None, dest="to_chapter")

    # get
    p_get = sub.add_parser("get", help="get a single entry by number")
    p_get.add_argument("--chapter", type=int, required=True)

    # validate
    sub.add_parser("validate", help="schema + sequence + file-existence checks")

    args = ap.parse_args()

    handlers = {
        "add": cmd_add,
        "update": cmd_update,
        "set-status": cmd_set_status,
        "list": cmd_list,
        "get": cmd_get,
        "validate": cmd_validate,
    }

    # Acquire advisory lock for write commands.
    lock_acquired = False
    if args.command in WRITE_COMMANDS and not args.skip_lock:
        op = f"chapter-index-{args.command}-ch-{getattr(args, 'chapter', '?')}"
        ok, lock_payload = _acquire_lock(Path(args.book).resolve(), op)
        if not ok:
            print(json.dumps({
                "ok": False,
                "stage": "lock",
                "error": "could not acquire book write lock",
                "lockReport": lock_payload,
                "hint": (
                    "Inspect with `book_lock.py status`; clear with "
                    "`book_lock.py release --force` if you're sure no other "
                    "process is writing. Pass --skip-lock to bypass."
                ),
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            sys.exit(3)
        lock_acquired = bool(lock_payload.get("ok") and not lock_payload.get("skipped"))

    try:
        result = handlers[args.command](args)
    finally:
        if lock_acquired:
            _release_lock(Path(args.book).resolve())

    if args.json or args.command in ("add", "update", "set-status", "get"):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "list":
        print(_format_list_text(result))
    elif args.command == "validate":
        print(_format_validate_text(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result.get("ok"):
        # exit 2 for validation/business errors so CI / orchestration can detect
        sys.exit(2)


if __name__ == "__main__":
    main()
