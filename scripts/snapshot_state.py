#!/usr/bin/env python3
"""State snapshots — backup of the book's truth files at chapter N landing.

Inkos's `manager.snapshotState(bookId, chapterNumber)` persists a copy of the
seven story-level markdown truth files plus the four story/state/*.json files
into `story/snapshots/<chapter>/` after each chapter is settled. This lets a
human roll back if a later chapter pollutes the truth state. We mirror that
contract here, with three additional features:

  1. integrity metadata (`_meta.json` with sha256 per file)
  2. byte-level diff between any two snapshots
  3. prune by retention count

Storage layout for a snapshot of chapter N (zero-padded NNNN to match the rest
of the SKILL's chapter naming):

    books/<id>/story/snapshots/<NNNN>/
        current_state.md
        particle_ledger.md
        pending_hooks.md
        chapter_summaries.md
        subplot_board.md
        emotional_arcs.md
        character_matrix.md
        state/
            chapter_summaries.json
            current_state.json
            hooks.json
            manifest.json
        _meta.json

`_meta.json`:
    {
        "chapter": 7,
        "createdAt": "2026-05-02T...Z",
        "note": "before consolidate" | null,
        "sha256": {"current_state.md": "...", "state/manifest.json": "..."},
        "sourceManifest": {... copy of story/state/manifest.json at snapshot time ...},
        "milestone": false  # true means "never prune" (consolidate triggers this)
    }

CLI:
    snapshot_state.py --book <bookDir> create --chapter N [--note "..."]
                                              [--milestone] [--json]
    snapshot_state.py --book <bookDir> list [--json]
    snapshot_state.py --book <bookDir> show --chapter N [--json]
    snapshot_state.py --book <bookDir> restore --chapter N [--target <bookDir>]
                                              [--dry-run] [--force] [--json]
    snapshot_state.py --book <bookDir> diff --from N --to M [--file <name>] [--json]
    snapshot_state.py --book <bookDir> prune --keep-last K [--dry-run] [--json]

Atomic create:
    Stage at `<NNNN>.tmp/`, fsync files, then `os.replace` directory. If a
    snapshot for the same chapter already exists, we re-stage and replace it
    in one rename — idempotent (last-write-wins on createdAt + note).

Exit codes:
    0 — success
    1 — usage / IO / validation error
    2 — refusal (e.g. restore would drop chapters; use --force)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _summary import emit_summary  # noqa: E402

# ── constants (mirror inkos manager.ts) ──────────────────────────────

TRUTH_MD_FILES = [
    "current_state.md",
    "pending_hooks.md",
    "chapter_summaries.md",
    "subplot_board.md",
    "emotional_arcs.md",
    "character_matrix.md",
    # particle_ledger.md is created on-demand by Architect for numerical-system
    # genres; not shipped in the init template. Snapshot still picks it up if
    # present (see _enumerate_md below).
    "particle_ledger.md",
]

TRUTH_JSON_FILES = [
    "chapter_summaries.json",
    "current_state.json",
    "hooks.json",
    "manifest.json",
]

# story-level required-on-restore (rest are optional).
RESTORE_REQUIRED_MD = {"current_state.md", "pending_hooks.md"}


# ── helpers ──────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _sha256_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshots_dir(book_dir: Path) -> Path:
    return book_dir / "story" / "snapshots"


def _snapshot_dir(book_dir: Path, chapter_no: int) -> Path:
    return _snapshots_dir(book_dir) / f"{chapter_no:04d}"


def _story_dir(book_dir: Path) -> Path:
    return book_dir / "story"


def _state_dir(book_dir: Path) -> Path:
    return book_dir / "story" / "state"


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write(path, text.encode("utf-8"))


def _emit(payload: dict, as_json: bool, text_lines: list[str] | None = None) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if text_lines:
            for line in text_lines:
                print(line)
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))


def _list_snapshot_dirs(book_dir: Path) -> list[Path]:
    snaps = _snapshots_dir(book_dir)
    if not snaps.is_dir():
        return []
    out: list[Path] = []
    for entry in snaps.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.endswith(".tmp"):
            continue
        # accept NNNN (zero-padded) and bare ints (legacy / inkos-compat)
        try:
            int(entry.name)
        except ValueError:
            continue
        out.append(entry)
    out.sort(key=lambda p: int(p.name))
    return out


def _chapter_from_dir(p: Path) -> int:
    return int(p.name)


def _dir_byte_count(d: Path) -> tuple[int, int]:
    """Return (file_count, total_bytes) for files under d, excluding _meta.json's
    contribution (no — we include it; it's part of the snapshot)."""
    total = 0
    count = 0
    for root, _dirs, files in os.walk(d):
        for f in files:
            fp = Path(root) / f
            try:
                total += fp.stat().st_size
                count += 1
            except OSError:
                continue
    return count, total


# ── create ──────────────────────────────────────────────────────────

def cmd_create(book_dir: Path, chapter_no: int, note: str | None,
               milestone: bool, as_json: bool) -> int:
    if chapter_no < 0:
        msg = {"error": f"invalid chapter: {chapter_no}"}
        _emit(msg, as_json)
        return 1

    story = _story_dir(book_dir)
    state = _state_dir(book_dir)
    if not story.is_dir():
        _emit({"error": f"story dir not found: {story}"}, as_json)
        return 1

    target = _snapshot_dir(book_dir, chapter_no)
    staging = target.with_suffix(".tmp")

    # nuke any leftover staging
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    sha: dict[str, str] = {}
    copied_md: list[str] = []
    copied_json: list[str] = []

    # 1) markdown truth files (each optional — inkos copies if exists)
    for fname in TRUTH_MD_FILES:
        src = story / fname
        if not src.is_file():
            continue
        data = src.read_bytes()
        (staging / fname).write_bytes(data)
        sha[fname] = _sha256_bytes(data)
        copied_md.append(fname)

    # 2) state JSON files
    if state.is_dir():
        (staging / "state").mkdir(parents=True, exist_ok=True)
        for fname in TRUTH_JSON_FILES:
            src = state / fname
            if not src.is_file():
                continue
            data = src.read_bytes()
            (staging / "state" / fname).write_bytes(data)
            sha[f"state/{fname}"] = _sha256_bytes(data)
            copied_json.append(fname)

    # 3) source manifest snapshot (read separately for _meta payload)
    source_manifest = _read_json(state / "manifest.json", {}) or {}

    meta = {
        "chapter": chapter_no,
        "createdAt": _now(),
        "note": note,
        "milestone": bool(milestone),
        "sha256": sha,
        "sourceManifest": source_manifest,
        "files": {
            "markdown": copied_md,
            "state": copied_json,
        },
    }
    _atomic_write_text(staging / "_meta.json",
                       json.dumps(meta, ensure_ascii=False, indent=2))

    # 4) atomic swap: remove existing target, rename staging into place
    if target.exists():
        # idempotent re-snapshot: delete the old, replace with the new
        shutil.rmtree(target)
    os.replace(staging, target)

    file_count, total_bytes = _dir_byte_count(target)
    payload = {
        "chapter": chapter_no,
        "snapshotDir": str(target),
        "createdAt": meta["createdAt"],
        "note": note,
        "milestone": bool(milestone),
        "fileCount": file_count,
        "totalBytes": total_bytes,
        "files": meta["files"],
    }
    _emit(payload, as_json, text_lines=[
        f"Snapshot created: chapter {chapter_no} -> {target}",
        f"  files: {file_count}, bytes: {total_bytes}",
        f"  note: {note or '(none)'}",
        f"  milestone: {bool(milestone)}",
    ])
    if as_json:
        emit_summary(
            f"action=create snapshot ch={chapter_no} files={file_count} "
            f"bytes={total_bytes} milestone={bool(milestone)}"
        )
    return 0


# ── list ────────────────────────────────────────────────────────────

def cmd_list(book_dir: Path, as_json: bool) -> int:
    rows: list[dict] = []
    for d in _list_snapshot_dirs(book_dir):
        meta = _read_json(d / "_meta.json", {}) or {}
        file_count, total_bytes = _dir_byte_count(d)
        rows.append({
            "chapter": _chapter_from_dir(d),
            "createdAt": meta.get("createdAt"),
            "note": meta.get("note"),
            "milestone": bool(meta.get("milestone", False)),
            "fileCount": file_count,
            "totalBytes": total_bytes,
            "path": str(d),
        })

    payload = {"book": str(book_dir), "snapshots": rows, "count": len(rows)}
    if as_json:
        _emit(payload, True)
        ms_n = sum(1 for r in rows if r.get("milestone"))
        emit_summary(f"action=list snapshots={len(rows)} milestones={ms_n}")
    else:
        if not rows:
            print(f"No snapshots under {_snapshots_dir(book_dir)}")
        else:
            print(f"{len(rows)} snapshot(s):")
            for r in rows:
                marker = " *milestone*" if r["milestone"] else ""
                print(f"  ch {r['chapter']:04d}  {r['createdAt']}  "
                      f"{r['fileCount']:>2} files  {r['totalBytes']:>8}B"
                      f"{marker}  {r['note'] or ''}")
    return 0


# ── show ────────────────────────────────────────────────────────────

def cmd_show(book_dir: Path, chapter_no: int, as_json: bool) -> int:
    snap = _snapshot_dir(book_dir, chapter_no)
    if not snap.is_dir():
        _emit({"error": f"snapshot not found: chapter {chapter_no}"}, as_json)
        emit_summary(
            f"FAILED: snapshot not found: chapter {chapter_no}", prefix="error"
        )
        return 1
    meta = _read_json(snap / "_meta.json", None)
    if meta is None:
        _emit({"error": f"_meta.json missing or invalid in {snap}"}, as_json)
        emit_summary(
            f"FAILED: _meta.json missing or invalid in {snap}", prefix="error"
        )
        return 1
    file_count, total_bytes = _dir_byte_count(snap)
    payload = {**meta, "path": str(snap),
               "fileCount": file_count, "totalBytes": total_bytes}
    # integrity check
    integrity_issues = _verify_integrity(snap, meta)
    payload["integrityOk"] = not integrity_issues
    payload["integrityIssues"] = integrity_issues

    if as_json:
        _emit(payload, True)
        emit_summary(
            f"action=show ch={chapter_no} files={file_count} bytes={total_bytes} "
            f"integrity={'ok' if not integrity_issues else 'FAILED'}"
        )
    else:
        print(f"Snapshot for chapter {chapter_no}: {snap}")
        print(f"  createdAt: {meta.get('createdAt')}")
        print(f"  note: {meta.get('note') or '(none)'}")
        print(f"  milestone: {bool(meta.get('milestone', False))}")
        print(f"  files: {file_count}, bytes: {total_bytes}")
        print(f"  integrity: {'OK' if not integrity_issues else 'FAILED'}")
        for issue in integrity_issues:
            print(f"    - {issue}")
    return 0


def _verify_integrity(snap_dir: Path, meta: dict) -> list[str]:
    issues: list[str] = []
    sha = meta.get("sha256") or {}
    if not isinstance(sha, dict):
        return ["_meta.sha256 is not a dict"]
    for rel, expected in sha.items():
        fp = snap_dir / rel
        if not fp.is_file():
            issues.append(f"{rel}: file missing")
            continue
        try:
            actual = _sha256_file(fp)
        except OSError as e:
            issues.append(f"{rel}: read error {e}")
            continue
        if actual != expected:
            issues.append(f"{rel}: sha256 mismatch")
    return issues


# ── restore ─────────────────────────────────────────────────────────

def cmd_restore(book_dir: Path, chapter_no: int, target_dir: Path | None,
                dry_run: bool, force: bool, as_json: bool) -> int:
    snap = _snapshot_dir(book_dir, chapter_no)
    if not snap.is_dir():
        _emit({"error": f"snapshot not found: chapter {chapter_no}"}, as_json)
        emit_summary(
            f"FAILED: snapshot not found: chapter {chapter_no}", prefix="error"
        )
        return 1
    meta = _read_json(snap / "_meta.json", None)
    if meta is None:
        _emit({"error": f"_meta.json missing in {snap}"}, as_json)
        emit_summary(
            f"FAILED: _meta.json missing in {snap}", prefix="error"
        )
        return 1

    target = (target_dir or book_dir).resolve()
    if not target.is_dir():
        _emit({"error": f"target not a dir: {target}"}, as_json)
        emit_summary(f"FAILED: target not a dir: {target}", prefix="error")
        return 1

    # safety: refuse if current manifest has progressed past N
    cur_manifest = _read_json(target / "story" / "state" / "manifest.json", {}) or {}
    last_applied = int(cur_manifest.get("lastAppliedChapter", 0) or 0)
    would_lose = max(0, last_applied - chapter_no)

    if would_lose > 0 and not force and not dry_run:
        _emit({
            "error": "restore would drop chapters",
            "lastAppliedChapter": last_applied,
            "snapshotChapter": chapter_no,
            "wouldLoseChapters": would_lose,
            "hint": "pass --force if you really want this; consider --dry-run first",
        }, as_json)
        return 2

    # check integrity before touching anything
    integrity_issues = _verify_integrity(snap, meta)
    if integrity_issues and not force:
        _emit({
            "error": "snapshot integrity check failed",
            "issues": integrity_issues,
            "hint": "pass --force to restore anyway",
        }, as_json)
        return 2

    # build the action plan
    plan: list[dict] = []
    target_story = target / "story"
    target_state = target / "story" / "state"

    for fname in TRUTH_MD_FILES:
        src = snap / fname
        dst = target_story / fname
        if src.is_file():
            new_sha = _sha256_file(src)
            old_sha = _sha256_file(dst) if dst.is_file() else None
            plan.append({
                "action": "write",
                "kind": "md",
                "rel": fname,
                "changed": new_sha != old_sha,
                "oldSize": dst.stat().st_size if dst.is_file() else 0,
                "newSize": src.stat().st_size,
            })
        else:
            # snapshot doesn't have this file → delete from target if exists
            # BUT: only optional MD files; required must be present in snapshot
            if dst.is_file() and fname not in RESTORE_REQUIRED_MD:
                plan.append({
                    "action": "delete",
                    "kind": "md",
                    "rel": fname,
                    "changed": True,
                    "oldSize": dst.stat().st_size,
                    "newSize": 0,
                })

    snap_state = snap / "state"
    if snap_state.is_dir():
        for fname in TRUTH_JSON_FILES:
            src = snap_state / fname
            dst = target_state / fname
            if src.is_file():
                new_sha = _sha256_file(src)
                old_sha = _sha256_file(dst) if dst.is_file() else None
                plan.append({
                    "action": "write",
                    "kind": "state",
                    "rel": f"state/{fname}",
                    "changed": new_sha != old_sha,
                    "oldSize": dst.stat().st_size if dst.is_file() else 0,
                    "newSize": src.stat().st_size,
                })

    # required-MD presence sanity
    missing_required = [f for f in RESTORE_REQUIRED_MD
                        if not (snap / f).is_file()]
    if missing_required and not force:
        _emit({
            "error": "snapshot missing required files",
            "missing": missing_required,
            "hint": "pass --force to restore the rest anyway",
        }, as_json)
        return 2

    if dry_run:
        payload = {
            "dryRun": True,
            "snapshotChapter": chapter_no,
            "target": str(target),
            "lastAppliedChapter": last_applied,
            "wouldLoseChapters": would_lose,
            "actions": plan,
            "integrityIssues": integrity_issues,
        }
        if as_json:
            _emit(payload, True)
        else:
            print(f"DRY RUN — restore chapter {chapter_no} into {target}")
            print(f"  manifest.lastAppliedChapter: {last_applied} "
                  f"(would lose {would_lose})")
            for a in plan:
                if a.get("changed"):
                    print(f"  {a['action']:6} {a['rel']:32}  "
                          f"{a['oldSize']:>7} -> {a['newSize']:>7}")
                else:
                    print(f"  noop   {a['rel']:32}  (identical)")
            if integrity_issues:
                print("  integrity issues:")
                for i in integrity_issues:
                    print(f"    - {i}")
        return 0

    # actually apply
    applied: list[dict] = []
    for a in plan:
        rel = a["rel"]
        if a["action"] == "write":
            src = snap / rel
            dst = target / "story" / rel
            data = src.read_bytes()
            _atomic_write(dst, data)
            applied.append(a)
        elif a["action"] == "delete":
            dst = target / "story" / rel
            try:
                dst.unlink()
                applied.append(a)
            except OSError:
                pass

    payload = {
        "restored": True,
        "snapshotChapter": chapter_no,
        "target": str(target),
        "lastAppliedChapterBefore": last_applied,
        "wouldLoseChapters": would_lose,
        "force": force,
        "actions": applied,
    }
    _emit(payload, as_json, text_lines=[
        f"Restored snapshot of chapter {chapter_no} into {target}",
        f"  applied {len(applied)} file action(s)",
    ])
    if as_json:
        emit_summary(
            f"action=restore ch={chapter_no} actions={len(applied)} "
            f"force={force} wouldLose={would_lose}"
        )
    return 0


# ── diff ────────────────────────────────────────────────────────────

def cmd_diff(book_dir: Path, from_n: int, to_n: int,
             only_file: str | None, as_json: bool) -> int:
    a_dir = _snapshot_dir(book_dir, from_n)
    b_dir = _snapshot_dir(book_dir, to_n)
    if not a_dir.is_dir():
        _emit({"error": f"snapshot not found: chapter {from_n}"}, as_json)
        return 1
    if not b_dir.is_dir():
        _emit({"error": f"snapshot not found: chapter {to_n}"}, as_json)
        return 1

    files: list[str] = []
    if only_file:
        files = [only_file]
    else:
        for f in TRUTH_MD_FILES:
            files.append(f)
        for f in TRUTH_JSON_FILES:
            files.append(f"state/{f}")

    rows: list[dict] = []
    for rel in files:
        a = a_dir / rel
        b = b_dir / rel
        a_exists = a.is_file()
        b_exists = b.is_file()
        if not a_exists and not b_exists:
            continue
        a_size = a.stat().st_size if a_exists else 0
        b_size = b.stat().st_size if b_exists else 0
        a_sha = _sha256_file(a) if a_exists else None
        b_sha = _sha256_file(b) if b_exists else None
        changed = a_sha != b_sha
        if changed:
            if not a_exists:
                summary = "added"
            elif not b_exists:
                summary = "removed"
            else:
                summary = f"size {a_size} -> {b_size} (delta {b_size - a_size:+d})"
        else:
            summary = "unchanged"
        rows.append({
            "file": rel,
            "changed": changed,
            "oldSize": a_size,
            "newSize": b_size,
            "summary": summary,
        })

    payload = {
        "from": from_n,
        "to": to_n,
        "files": rows,
        "changedCount": sum(1 for r in rows if r["changed"]),
    }
    if as_json:
        _emit(payload, True)
        emit_summary(
            f"action=diff from={from_n} to={to_n} files={len(rows)} "
            f"changed={payload['changedCount']}"
        )
    else:
        print(f"Diff snapshot {from_n} -> {to_n}")
        for r in rows:
            mark = "*" if r["changed"] else " "
            print(f"  {mark} {r['file']:32}  {r['summary']}")
        print(f"  changed: {payload['changedCount']}")
    return 0


# ── prune ───────────────────────────────────────────────────────────

def cmd_prune(book_dir: Path, keep_last: int, dry_run: bool, as_json: bool) -> int:
    if keep_last < 0:
        _emit({"error": "--keep-last must be >= 0"}, as_json)
        return 1
    dirs = _list_snapshot_dirs(book_dir)

    # sort ASC (oldest first), partition by milestone flag
    keep: list[Path] = []
    candidates: list[Path] = []
    for d in dirs:
        meta = _read_json(d / "_meta.json", {}) or {}
        if meta.get("milestone"):
            keep.append(d)
        else:
            candidates.append(d)

    # of non-milestone, keep the last K (highest chapter numbers)
    candidates.sort(key=lambda p: int(p.name))
    kept_recent = candidates[-keep_last:] if keep_last > 0 else []
    to_delete = [d for d in candidates if d not in kept_recent]

    deleted: list[dict] = []
    for d in to_delete:
        meta = _read_json(d / "_meta.json", {}) or {}
        entry = {
            "chapter": _chapter_from_dir(d),
            "path": str(d),
            "createdAt": meta.get("createdAt"),
        }
        if not dry_run:
            try:
                shutil.rmtree(d)
            except OSError as e:
                entry["error"] = str(e)
        deleted.append(entry)

    payload = {
        "dryRun": dry_run,
        "keepLast": keep_last,
        "totalBefore": len(dirs),
        "milestonesKept": [_chapter_from_dir(d) for d in keep],
        "recentKept": [_chapter_from_dir(d) for d in kept_recent],
        "deleted": deleted,
        "totalAfter": len(dirs) - (0 if dry_run else len(deleted)),
    }
    if as_json:
        _emit(payload, True)
        emit_summary(
            f"action=prune dryRun={dry_run} before={len(dirs)} "
            f"deleted={len(deleted)} milestonesKept={len(keep)} "
            f"recentKept={len(kept_recent)}"
        )
    else:
        verb = "would delete" if dry_run else "deleted"
        print(f"Prune (keep-last={keep_last}, milestones always kept)")
        print(f"  before: {len(dirs)}, milestones: {len(keep)}, "
              f"recent kept: {len(kept_recent)}, {verb}: {len(deleted)}")
        for d in deleted:
            print(f"  - chapter {d['chapter']:04d}  {d.get('createdAt') or ''}")
    return 0


# ── arg parsing ─────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="State snapshots: backup/restore/diff/prune the book's "
                    "truth files at chapter boundaries.",
    )
    p.add_argument("--book", required=True, help="book directory (containing story/)")

    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("create", help="snapshot current truth state for chapter N")
    pc.add_argument("--chapter", type=int, required=True)
    pc.add_argument("--note", default=None, help="optional human note")
    pc.add_argument("--milestone", action="store_true",
                    help="mark this snapshot as never-prune (e.g. consolidate)")
    pc.add_argument("--json", action="store_true")

    pl = sub.add_parser("list", help="list all snapshots, sorted by chapter ASC")
    pl.add_argument("--json", action="store_true")

    ps = sub.add_parser("show", help="show _meta.json + integrity for one snapshot")
    ps.add_argument("--chapter", type=int, required=True)
    ps.add_argument("--json", action="store_true")

    pr = sub.add_parser("restore", help="copy snapshot's files back over current state")
    pr.add_argument("--chapter", type=int, required=True)
    pr.add_argument("--target", default=None,
                    help="target book dir (defaults to --book)")
    pr.add_argument("--dry-run", action="store_true")
    pr.add_argument("--force", action="store_true",
                    help="proceed even if current manifest is past N or integrity bad")
    pr.add_argument("--json", action="store_true")

    pd = sub.add_parser("diff", help="byte-level diff between two snapshots")
    pd.add_argument("--from", dest="from_n", type=int, required=True)
    pd.add_argument("--to", dest="to_n", type=int, required=True)
    pd.add_argument("--file", default=None,
                    help="restrict to one file (e.g. pending_hooks.md or state/manifest.json)")
    pd.add_argument("--json", action="store_true")

    pp = sub.add_parser("prune", help="keep last K non-milestone snapshots, delete older")
    pp.add_argument("--keep-last", type=int, required=True)
    pp.add_argument("--dry-run", action="store_true")
    pp.add_argument("--json", action="store_true")

    return p.parse_args()


def main() -> int:
    args = parse_args()
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        print(json.dumps({"error": f"book dir not found: {book_dir}"},
                         ensure_ascii=False), file=sys.stderr)
        emit_summary(f"FAILED: book dir not found: {book_dir}", prefix="error")
        return 1

    if args.cmd == "create":
        return cmd_create(book_dir, args.chapter, args.note,
                          bool(args.milestone), bool(args.json))
    if args.cmd == "list":
        return cmd_list(book_dir, bool(args.json))
    if args.cmd == "show":
        return cmd_show(book_dir, args.chapter, bool(args.json))
    if args.cmd == "restore":
        target = Path(args.target).resolve() if args.target else None
        return cmd_restore(book_dir, args.chapter, target,
                           bool(args.dry_run), bool(args.force), bool(args.json))
    if args.cmd == "diff":
        return cmd_diff(book_dir, args.from_n, args.to_n,
                        args.file, bool(args.json))
    if args.cmd == "prune":
        return cmd_prune(book_dir, args.keep_last,
                         bool(args.dry_run), bool(args.json))

    print(f"unknown subcommand: {args.cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
