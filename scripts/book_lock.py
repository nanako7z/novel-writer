#!/usr/bin/env python3
"""Book-level advisory write lock.

Ports the spirit of inkos's per-book ``.book.lock``: a tiny JSON file
parked at ``<bookDir>/.book.lock`` that mutating scripts (apply_delta,
chapter_index add/update/set-status) consult before touching truth files.

This is **advisory only** — it's a Python file, not an OS-level fcntl /
flock. Concurrent processes that ignore the lock will still race. The
point is to catch human accidents (running 2 Claude Code sessions on
the same book, or a stray manual ``apply_delta`` while a writeNextChapter
pipeline is in flight).

Lock file payload::

    {
      "pid": 12345,
      "operation": "write-chapter-15",
      "acquiredAt": "ISO8601",
      "expiresAt":  "ISO8601",
      "host": "machine-name"
    }

Subcommands:

    acquire    create the lock; refuse if held by another live owner
    release    remove the lock; --force ignores ownership checks
    status     report current lock state

Exit codes:
    0 — operation succeeded
    1 — usage / IO error
    2 — acquire refused (lock held and not expired)
    3 — release refused (not our lock; use --force if you really mean it)
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

LOCK_FILENAME = ".book.lock"
DEFAULT_TTL_SEC = 1800  # 30 minutes


# ───────────────────────── helpers ──────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # Allow both ``Z`` and ``+00:00`` suffixes.
        s2 = s.rstrip("Z")
        if "." in s2:
            head, frac = s2.split(".", 1)
            # truncate to microseconds
            frac = (frac + "000000")[:6]
            s2 = f"{head}.{frac}"
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _lock_path(book_dir: Path) -> Path:
    return book_dir / LOCK_FILENAME


def _read_lock(p: Path) -> dict[str, Any] | None:
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        return None
    except (OSError, json.JSONDecodeError):
        return None


def _atomic_write_lock(p: Path, payload: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _is_expired(lock: dict[str, Any]) -> bool:
    expires = _parse_iso(lock.get("expiresAt"))
    if expires is None:
        # No / malformed expiresAt → treat as expired so a stale lock can
        # always be reclaimed.
        return True
    return datetime.now(timezone.utc) > expires


def _ours(lock: dict[str, Any]) -> bool:
    return (
        lock.get("pid") == os.getpid()
        and lock.get("host") == socket.gethostname()
    )


# ───────────────────────── commands ─────────────────────────────────────


def cmd_acquire(book_dir: Path, args: argparse.Namespace) -> tuple[dict, int]:
    p = _lock_path(book_dir)
    existing = _read_lock(p)
    now = _now_iso()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=max(1, args.ttl))
    ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    if existing is not None and not _is_expired(existing):
        return (
            {
                "ok": False,
                "action": "acquire",
                "result": "refused",
                "reason": "lock held by another owner",
                "lockPath": str(p),
                "currentLock": existing,
                "hint": (
                    "If you're sure no other process is writing, run "
                    "`book_lock.py release --force` to clear it."
                ),
            },
            2,
        )

    payload: dict[str, Any] = {
        "pid": os.getpid(),
        "operation": args.operation or "unspecified",
        "acquiredAt": now,
        "expiresAt": expires_at,
        "host": socket.gethostname(),
    }
    took_over = existing is not None  # existed but expired
    _atomic_write_lock(p, payload)

    return (
        {
            "ok": True,
            "action": "acquire",
            "result": "tookOver" if took_over else "acquired",
            "lockPath": str(p),
            "lock": payload,
            **(
                {"previousLock": existing, "warning": "took over expired lock"}
                if took_over
                else {}
            ),
        },
        0,
    )


def cmd_release(book_dir: Path, args: argparse.Namespace) -> tuple[dict, int]:
    p = _lock_path(book_dir)
    existing = _read_lock(p)
    if existing is None:
        return (
            {
                "ok": True,
                "action": "release",
                "result": "no-lock",
                "lockPath": str(p),
                "note": "lock file did not exist; nothing to release",
            },
            0,
        )

    if not args.force and not _ours(existing):
        return (
            {
                "ok": False,
                "action": "release",
                "result": "refused",
                "reason": "lock not owned by current pid+host",
                "lockPath": str(p),
                "currentLock": existing,
                "hint": "use --force to release a foreign lock",
            },
            3,
        )

    try:
        os.remove(p)
    except OSError as e:
        return (
            {
                "ok": False,
                "action": "release",
                "result": "io-error",
                "error": str(e),
                "lockPath": str(p),
            },
            1,
        )

    return (
        {
            "ok": True,
            "action": "release",
            "result": "released",
            "lockPath": str(p),
            "releasedLock": existing,
            **({"forced": True} if args.force and not _ours(existing) else {}),
        },
        0,
    )


def cmd_status(book_dir: Path, args: argparse.Namespace) -> tuple[dict, int]:
    p = _lock_path(book_dir)
    existing = _read_lock(p)
    if existing is None:
        return (
            {
                "ok": True,
                "action": "status",
                "state": "free",
                "lockPath": str(p),
            },
            0,
        )
    expired = _is_expired(existing)
    return (
        {
            "ok": True,
            "action": "status",
            "state": "expired" if expired else "held",
            "ours": _ours(existing),
            "lockPath": str(p),
            "lock": existing,
        },
        0,
    )


# ───────────────────────── argparse glue ────────────────────────────────


def _print_human_status(result: dict) -> None:
    state = result.get("state", "?")
    p = result.get("lockPath", "")
    if state == "free":
        print(f"book lock: free ({p})")
        return
    lock = result.get("lock", {})
    print(f"book lock: {state}  ours={result.get('ours', False)}  ({p})")
    print(f"  pid={lock.get('pid')}  host={lock.get('host')}")
    print(f"  operation={lock.get('operation')}")
    print(f"  acquiredAt={lock.get('acquiredAt')}  expiresAt={lock.get('expiresAt')}")


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="book_lock.py",
        description=(
            "Advisory book-level write lock. Mutating scripts (apply_delta, "
            "chapter_index add/update/set-status) acquire this before touching "
            "truth files; read-only / single-shot scripts ignore it."
        ),
    )
    ap.add_argument("--book", required=True, help="path to book directory")
    ap.add_argument(
        "--json", action="store_true",
        help="emit JSON (default for acquire/release; status defaults to text)",
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_acq = sub.add_parser("acquire", help="acquire the book write lock")
    p_acq.add_argument("--operation", default=None,
                       help='free-form label, e.g. "apply-delta-ch-15"')
    p_acq.add_argument("--ttl", type=int, default=DEFAULT_TTL_SEC,
                       help=f"seconds until lock auto-expires (default {DEFAULT_TTL_SEC})")

    p_rel = sub.add_parser("release", help="release the book write lock")
    p_rel.add_argument("--force", action="store_true",
                       help="release even if not owned by current pid+host")

    sub.add_parser("status", help="show current lock state")

    return ap.parse_args()


def main() -> int:
    args = parse_args()
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        print(json.dumps({"error": f"book dir not found: {book_dir}"},
                         ensure_ascii=False), file=sys.stderr)
        return 1

    handlers = {
        "acquire": cmd_acquire,
        "release": cmd_release,
        "status": cmd_status,
    }
    result, code = handlers[args.command](book_dir, args)

    # status defaults to human-readable; acquire/release always JSON unless
    # caller flips --json explicitly.
    if args.command == "status" and not args.json:
        _print_human_status(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
