#!/usr/bin/env python3
"""Book CRUD: list / show / update / rename / delete / copy.

Multi-book management for the novel-writer SKILL.  Creation lives in
`init_book.py`; this script covers everything else.

All ops are read/write only on the project filesystem (no LLM, no network).
Workdir resolution mirrors `status.py`: walk up from cwd looking for
`inkos.json`; fallback to cwd.

Subcommands
-----------
  list                                  scan books/, list summaries
  show    <bookId>                      one-book detail
  update  <bookId> [--field value ...]  patch book.json (atomic)
  rename  <oldId> <newId>               move dir + patch book.json#id
  delete  <bookId>                      archive by default; --force skips prompt
  copy    <srcId> <newId>               clone setup; chapters optional
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _summary import emit_summary  # noqa: E402
from _constants import BOOK_STATUS, PLATFORM  # noqa: E402  — single source of truth

CHAPTER_NAME_RE = re.compile(r"^(\d{4})\.md$")
KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# --------------------------- discovery -------------------------------------

def find_project_root(start: Path) -> Path | None:
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / "inkos.json").is_file():
            return p
    return None


def resolve_workdir(arg: str | None) -> Path:
    """Resolve workdir: explicit --workdir > project root from cwd > cwd."""
    if arg:
        return Path(arg).resolve()
    root = find_project_root(Path.cwd())
    if root is not None:
        return root
    return Path.cwd().resolve()


def list_book_dirs(workdir: Path) -> list[Path]:
    books_dir = workdir / "books"
    if not books_dir.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(books_dir.iterdir()):
        if child.is_dir() and (child / "book.json").is_file():
            out.append(child)
    return out


# --------------------------- helpers ---------------------------------------

def _load_json(p: Path, default: Any) -> Any:
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_write_json(p: Path, data: Any) -> None:
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _iso_mtime(p: Path) -> str | None:
    try:
        ts = p.stat().st_mtime
    except OSError:
        return None
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat()


_FRONT_MATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_HEADING = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def _strip_metadata(text: str) -> str:
    text = text.replace("\r\n", "\n").lstrip("﻿")
    text = _FRONT_MATTER.sub("", text, count=1)
    text = _FENCE.sub("", text)
    text = _HTML_COMMENT.sub("", text)
    text = _HEADING.sub("", text)
    return text


def _count_length(text: str, mode: str) -> int:
    body = _strip_metadata(text)
    if mode == "en_words":
        return len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", body))
    return len(re.sub(r"\s+", "", body))


def _counting_mode(book: dict) -> str:
    raw = book.get("lengthCountingMode")
    if raw in ("zh_chars", "en_words"):
        return raw
    lang = book.get("language") or "zh"
    return "en_words" if lang == "en" else "zh_chars"


def collect_summary(book_dir: Path, *, deep: bool = False) -> dict:
    """Quick summary used by list/show.  `deep=True` adds hooks + recent titles."""
    book = _load_json(book_dir / "book.json", {}) or {}
    state_dir = book_dir / "story" / "state"

    manifest = _load_json(state_dir / "manifest.json", {}) or {}
    last_applied = int(manifest.get("lastAppliedChapter", 0) or 0)

    counting_mode = _counting_mode(book)

    chap_dir = book_dir / "chapters"
    chapter_files: list[Path] = []
    if chap_dir.is_dir():
        for f in chap_dir.iterdir():
            if f.is_file() and CHAPTER_NAME_RE.match(f.name):
                chapter_files.append(f)
    chapter_files.sort(key=lambda p: p.name)

    total_words = 0
    last_modified: str | None = None
    for f in chapter_files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            text = ""
        total_words += _count_length(text, counting_mode)
        mt = _iso_mtime(f)
        if mt and (last_modified is None or mt > last_modified):
            last_modified = mt

    bj_mtime = _iso_mtime(book_dir / "book.json")
    if bj_mtime and (last_modified is None or bj_mtime > last_modified):
        last_modified = bj_mtime

    out: dict[str, Any] = {
        "bookId": book.get("id") or book_dir.name,
        "title": book.get("title") or book_dir.name,
        "genre": book.get("genre"),
        "language": book.get("language", "zh"),
        "platform": book.get("platform"),
        "status": book.get("status"),
        "targetChapters": int(book.get("targetChapters", 0) or 0),
        "chapterWordCount": int(book.get("chapterWordCount", 0) or 0),
        "lastAppliedChapter": last_applied,
        "totalChapters": len(chapter_files),
        "totalWords": total_words,
        "countingMode": counting_mode,
        "lastModified": last_modified,
        "bookDir": str(book_dir),
    }

    if deep:
        hooks_obj = _load_json(state_dir / "hooks.json", {"hooks": []}) or {}
        hooks = hooks_obj.get("hooks", []) if isinstance(hooks_obj, dict) else []
        active = stale = resolved = 0
        if isinstance(hooks, list):
            for h in hooks:
                if not isinstance(h, dict):
                    continue
                st = (h.get("status") or "").strip().lower()
                if st in {"resolved", "closed", "done", "已回收", "已解决"}:
                    resolved += 1
                    continue
                active += 1
                if h.get("stale"):
                    stale += 1
        out["hookCount"] = {"active": active, "stale": stale, "resolved": resolved}

        # recent 3 chapter titles from chapter_summaries.json
        # inkos `rows` / legacy SKILL `summaries` — read both.
        sums_obj = _load_json(state_dir / "chapter_summaries.json", {"rows": []}) or {}
        sums = (sums_obj.get("rows", sums_obj.get("summaries", []))
                if isinstance(sums_obj, dict) else [])
        rows: list[dict] = []
        if isinstance(sums, list):
            for r in sums:
                if isinstance(r, dict) and isinstance(r.get("chapter"), int):
                    rows.append(r)
        rows.sort(key=lambda r: r.get("chapter", 0), reverse=True)
        out["recentChapters"] = [
            {"chapter": r.get("chapter"), "title": r.get("title", "")}
            for r in rows[:3]
        ]

        # fanfic / parent book metadata pass-through
        if book.get("fanficMode"):
            out["fanficMode"] = book.get("fanficMode")
        if book.get("parentBookId"):
            out["parentBookId"] = book.get("parentBookId")
    return out


# --------------------------- list ------------------------------------------

def render_list_text(rows: list[dict]) -> str:
    if not rows:
        return "(no books found — run scripts/init_book.py first)"
    headers = ["bookId", "title", "genre", "lang", "applied/total", "words", "modified"]
    data: list[list[str]] = []
    for r in rows:
        applied = r.get("lastAppliedChapter") or 0
        total = r.get("totalChapters") or 0
        data.append([
            str(r.get("bookId") or ""),
            str(r.get("title") or ""),
            str(r.get("genre") or "-"),
            str(r.get("language") or "-"),
            f"{applied}/{total}",
            str(r.get("totalWords") or 0),
            (r.get("lastModified") or "-")[:19].replace("T", " "),
        ])
    widths = [len(h) for h in headers]
    for row in data:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    sep = "  "
    lines = [sep.join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append(sep.join("-" * widths[i] for i in range(len(headers))))
    for row in data:
        lines.append(sep.join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def cmd_list(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    book_dirs = list_book_dirs(workdir)
    rows = [collect_summary(bd, deep=False) for bd in book_dirs]
    if args.json:
        print(json.dumps({
            "workdir": str(workdir),
            "bookCount": len(rows),
            "books": rows,
        }, ensure_ascii=False, indent=2))
        emit_summary(f"action=list workdir={workdir} books={len(rows)}")
        return 0
    print(f"Workdir: {workdir}")
    print(f"Books: {len(rows)}")
    print()
    print(render_list_text(rows))
    return 0


# --------------------------- show ------------------------------------------

def render_show_text(stats: dict) -> str:
    lines: list[str] = []
    lines.append(f"{stats['title']} ({stats['bookId']})")
    lines.append(f"  Path:    {stats.get('bookDir')}")
    lines.append(f"  Genre:   {stats.get('genre') or '-'}  | Language: {stats.get('language')}  | Platform: {stats.get('platform') or '-'}")
    lines.append(f"  Status:  {stats.get('status') or '-'}")
    if stats.get("fanficMode"):
        lines.append(f"  Fanfic:  mode={stats['fanficMode']}  parent={stats.get('parentBookId') or '-'}")
    target = stats.get("targetChapters") or 0
    last = stats.get("lastAppliedChapter") or 0
    lines.append(f"  Progress: {last}/{target}  | Chapters on disk: {stats.get('totalChapters')}")
    lines.append(f"  Words: {stats.get('totalWords')} ({stats.get('countingMode')})  | Per-chapter target: {stats.get('chapterWordCount')}")
    hk = stats.get("hookCount") or {}
    lines.append(f"  Hooks: active={hk.get('active', 0)}  stale={hk.get('stale', 0)}  resolved={hk.get('resolved', 0)}")
    if stats.get("lastModified"):
        lines.append(f"  Last modified: {stats['lastModified']}")
    rec = stats.get("recentChapters") or []
    if rec:
        lines.append("  Recent chapters:")
        for r in rec:
            lines.append(f"    {r.get('chapter'):>4}  {r.get('title') or ''}")
    return "\n".join(lines)


def cmd_show(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    book_dir = workdir / "books" / args.bookId
    if not book_dir.is_dir() or not (book_dir / "book.json").is_file():
        msg = {"error": f"book not found: {args.bookId}", "workdir": str(workdir)}
        print(json.dumps(msg, ensure_ascii=False), file=sys.stderr)
        emit_summary(f"FAILED: book not found: {args.bookId}", prefix="error")
        return 1
    stats = collect_summary(book_dir, deep=True)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        emit_summary(
            f"action=show id={args.bookId} chapters={stats.get('totalChapters', 0)} "
            f"words={stats.get('totalWords', 0)} status={stats.get('status', '?')}"
        )
        return 0
    print(render_show_text(stats))
    return 0


# --------------------------- update ----------------------------------------

VALID_STATUSES = BOOK_STATUS
VALID_PLATFORMS = PLATFORM
VALID_LANGS = {"zh", "en"}

# (book.json field name, argparse attr, type, validator)
_UPDATE_FIELDS: list[tuple[str, str, type, set | None]] = [
    ("title", "title", str, None),
    ("chapterWordCount", "chapter_words", int, None),
    ("targetChapters", "target_chapters", int, None),
    ("genre", "genre", str, None),
    ("platform", "platform", str, VALID_PLATFORMS),
    ("status", "status", str, VALID_STATUSES),
    ("language", "lang", str, VALID_LANGS),
]


def cmd_update(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    book_dir = workdir / "books" / args.bookId
    bj = book_dir / "book.json"
    if not book_dir.is_dir() or not bj.is_file():
        print(json.dumps({"error": f"book not found: {args.bookId}",
                          "workdir": str(workdir)}, ensure_ascii=False),
              file=sys.stderr)
        emit_summary(f"FAILED: book not found: {args.bookId}", prefix="error")
        return 1

    try:
        data = json.loads(bj.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"book.json invalid: {e}"}), file=sys.stderr)
        return 1

    # Collect requested updates
    updates: dict[str, tuple[Any, Any]] = {}  # field -> (old, new)
    for field, attr, caster, choices in _UPDATE_FIELDS:
        new_raw = getattr(args, attr, None)
        if new_raw is None:
            continue
        if choices is not None and new_raw not in choices:
            print(json.dumps({
                "error": f"invalid value for --{attr.replace('_', '-')}: "
                         f"{new_raw!r}; valid: {sorted(choices)}",
            }, ensure_ascii=False), file=sys.stderr)
            return 1
        try:
            new_val = caster(new_raw)
        except (TypeError, ValueError) as e:
            print(json.dumps({"error": f"cannot cast --{attr}: {e}"}),
                  file=sys.stderr)
            return 1
        old_val = data.get(field)
        if old_val == new_val:
            continue
        updates[field] = (old_val, new_val)

    if not updates:
        print(json.dumps({
            "error": "no fields to update; pass at least one of "
                     "--title --chapter-words --target-chapters --genre "
                     "--platform --status --lang",
        }, ensure_ascii=False), file=sys.stderr)
        return 1

    # Genre fallback warning
    warnings: list[str] = []
    if "genre" in updates:
        new_genre = updates["genre"][1]
        skill_root = Path(__file__).resolve().parent.parent
        gp = skill_root / "templates" / "genres" / f"{new_genre}.md"
        if not gp.is_file():
            fb = skill_root / "templates" / "genres" / "other.md"
            if fb.is_file():
                warnings.append(
                    f"genre '{new_genre}' has no profile in templates/genres/; "
                    "Writer will fall back to other.md"
                )
            else:
                print(json.dumps({
                    "error": f"genre '{new_genre}' has no profile and other.md "
                             "is also missing",
                }, ensure_ascii=False), file=sys.stderr)
                return 1

    # Apply
    for field, (_old, new) in updates.items():
        data[field] = new
    data["updatedAt"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    _atomic_write_json(bj, data)

    payload = {
        "ok": True,
        "bookId": args.bookId,
        "updated": {f: {"from": o, "to": n} for f, (o, n) in updates.items()},
        "warnings": warnings,
        "book": data,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        emit_summary(
            f"action=update id={args.bookId} fields={','.join(sorted(updates.keys()))} "
            f"warnings={len(warnings)}"
        )
    else:
        print(f"Updated book \"{data.get('title', args.bookId)}\" ({args.bookId}):")
        for f, (o, n) in updates.items():
            print(f"  {f}: {o!r} -> {n!r}")
        for w in warnings:
            print(f"  warning: {w}")
    return 0


# --------------------------- rename ----------------------------------------

def _scan_and_patch_id_refs(root: Path, old_id: str, new_id: str) -> list[str]:
    """Walk the new book dir's runtime/ + state/ JSON files, replace bare
    references to old_id with new_id when key suggests bookId."""
    patched: list[str] = []
    targets = [root / "story" / "runtime", root / "story" / "state"]
    candidate_keys = {"bookId", "book_id", "parentBookId", "parent_book_id", "id"}
    for tdir in targets:
        if not tdir.is_dir():
            continue
        for jf in tdir.rglob("*.json"):
            try:
                obj = json.loads(jf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            changed = _walk_replace(obj, candidate_keys, old_id, new_id)
            if changed:
                _atomic_write_json(jf, obj)
                patched.append(str(jf))
    return patched


def _walk_replace(node: Any, keys: set[str], old: str, new: str) -> bool:
    changed = False
    if isinstance(node, dict):
        for k, v in list(node.items()):
            if k in keys and isinstance(v, str) and v == old:
                node[k] = new
                changed = True
            else:
                if _walk_replace(v, keys, old, new):
                    changed = True
    elif isinstance(node, list):
        for item in node:
            if _walk_replace(item, keys, old, new):
                changed = True
    return changed


def cmd_rename(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    if not KEBAB_RE.match(args.newId):
        print(json.dumps({"error": "newId must be kebab-case"}), file=sys.stderr)
        return 1
    src = workdir / "books" / args.oldId
    dst = workdir / "books" / args.newId
    if not src.is_dir() or not (src / "book.json").is_file():
        print(json.dumps({"error": f"source book not found: {args.oldId}"}), file=sys.stderr)
        return 1
    if dst.exists():
        print(json.dumps({"error": f"target id already exists: {args.newId}"}), file=sys.stderr)
        return 1

    # Move directory
    src.rename(dst)

    # Patch book.json#id
    bj = dst / "book.json"
    try:
        data = json.loads(bj.read_text(encoding="utf-8"))
        data["id"] = args.newId
        data["updatedAt"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        _atomic_write_json(bj, data)
    except (OSError, json.JSONDecodeError) as e:
        # Roll back the rename
        dst.rename(src)
        print(json.dumps({"error": f"failed to patch book.json: {e}"}), file=sys.stderr)
        return 1

    patched: list[str] = []
    if args.update_references:
        patched = _scan_and_patch_id_refs(dst, args.oldId, args.newId)

    payload = {
        "renamed": {"from": args.oldId, "to": args.newId},
        "path": str(dst),
        "patchedReferences": patched,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        emit_summary(
            f"action=rename {args.oldId} -> {args.newId} "
            f"patchedReferences={len(patched)}"
        )
    else:
        print(f"Renamed: {args.oldId} -> {args.newId}")
        print(f"Path: {dst}")
        if patched:
            print(f"Patched references in {len(patched)} file(s):")
            for p in patched:
                print(f"  {p}")
    return 0


# --------------------------- delete ----------------------------------------

def cmd_delete(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    book_dir = workdir / "books" / args.bookId
    if not book_dir.is_dir() or not (book_dir / "book.json").is_file():
        print(json.dumps({"error": f"book not found: {args.bookId}"}), file=sys.stderr)
        return 1

    summary = collect_summary(book_dir, deep=True)

    archive = not args.no_archive  # archive by default
    if args.archive:
        archive = True

    if not args.force:
        if args.json:
            print(json.dumps({
                "error": "destructive op requires --force in --json mode",
                "summary": summary,
            }, ensure_ascii=False), file=sys.stderr)
            return 2
        print(f"About to delete book \"{summary['title']}\" ({summary['bookId']})")
        print(f"  chapters: {summary['totalChapters']}  words: {summary['totalWords']}")
        hk = summary.get("hookCount") or {}
        print(f"  hooks: active={hk.get('active', 0)} stale={hk.get('stale', 0)}")
        print(f"  mode: {'archive (move to books_archive/)' if archive else 'permanent delete'}")
        try:
            confirm = input(f"Type 'delete {args.bookId}' to confirm: ").strip()
        except EOFError:
            confirm = ""
        if confirm != f"delete {args.bookId}":
            print("Cancelled.")
            return 1

    if archive:
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_root = workdir / "books_archive"
        archive_root.mkdir(parents=True, exist_ok=True)
        target = archive_root / f"{args.bookId}-{ts}"
        # rename is atomic on same filesystem; fall back to copy+rm if not
        try:
            book_dir.rename(target)
        except OSError:
            shutil.copytree(book_dir, target)
            shutil.rmtree(book_dir)
        action = "archived"
        new_path: str | None = str(target)
    else:
        shutil.rmtree(book_dir)
        action = "deleted"
        new_path = None

    payload = {
        "action": action,
        "bookId": args.bookId,
        "chapters": summary["totalChapters"],
        "words": summary["totalWords"],
        "archivePath": new_path,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        emit_summary(
            f"action={action} id={args.bookId} chapters={summary['totalChapters']} "
            f"words={summary['totalWords']}"
            + (f" archivePath={new_path}" if new_path else "")
        )
    else:
        if action == "archived":
            print(f"Archived \"{summary['title']}\" ({args.bookId}) -> {new_path}")
        else:
            print(f"Deleted \"{summary['title']}\" ({args.bookId}): "
                  f"{summary['totalChapters']} chapter(s), {summary['totalWords']} words")
    return 0


# --------------------------- copy ------------------------------------------

def cmd_copy(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    if not KEBAB_RE.match(args.newId):
        print(json.dumps({"error": "newId must be kebab-case"}), file=sys.stderr)
        return 1
    src = workdir / "books" / args.srcId
    dst = workdir / "books" / args.newId
    if not src.is_dir() or not (src / "book.json").is_file():
        print(json.dumps({"error": f"source book not found: {args.srcId}"}), file=sys.stderr)
        return 1
    if dst.exists():
        print(json.dumps({"error": f"target id already exists: {args.newId}"}), file=sys.stderr)
        return 1

    # Stage in a tmp dir so we either commit fully or leave nothing behind
    tmp_dst = dst.with_name(f".{args.newId}.copying")
    if tmp_dst.exists():
        shutil.rmtree(tmp_dst)

    def _ignore(_dir: str, names: list[str]) -> list[str]:
        return [n for n in names if n in {"runtime"}]

    try:
        # 1) Copy book.json + story/{outline,roles,*.md} + story/state/*
        #    Skip runtime/ (transient) and chapters/ unless --include-chapters
        tmp_dst.mkdir(parents=True, exist_ok=False)
        # book.json
        shutil.copy2(src / "book.json", tmp_dst / "book.json")

        # story/ tree, but exclude runtime/
        src_story = src / "story"
        if src_story.is_dir():
            shutil.copytree(src_story, tmp_dst / "story", ignore=_ignore)
            # ensure runtime/ exists fresh
            (tmp_dst / "story" / "runtime").mkdir(parents=True, exist_ok=True)

        # chapters/
        chap_target = tmp_dst / "chapters"
        chap_target.mkdir(parents=True, exist_ok=True)
        if args.include_chapters:
            src_chap = src / "chapters"
            if src_chap.is_dir():
                for f in src_chap.iterdir():
                    if f.is_file():
                        shutil.copy2(f, chap_target / f.name)

        # 2) Patch book.json: id + createdAt/updatedAt
        bj = tmp_dst / "book.json"
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        try:
            data = json.loads(bj.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"source book.json invalid: {e}")
        data["id"] = args.newId
        # If user is forking a setup (no chapters), reset createdAt; otherwise
        # preserve it.  Always bump updatedAt.
        if not args.include_chapters:
            data["createdAt"] = now
            data["status"] = data.get("status") or "incubating"
        data["updatedAt"] = now
        if args.title:
            data["title"] = args.title
        _atomic_write_json(bj, data)

        # 3) Reset state when chapters not included
        state_dir = tmp_dst / "story" / "state"
        if state_dir.is_dir() and not args.include_chapters:
            # manifest -> chapter 0
            mp = state_dir / "manifest.json"
            mf = _load_json(mp, {}) or {}
            mf["lastAppliedChapter"] = 0
            _atomic_write_json(mp, mf)
            # chapter_summaries -> empty
            csp = state_dir / "chapter_summaries.json"
            _atomic_write_json(csp, {"rows": []})
            # hooks -> drop lastAdvancedChapter on each hook (fresh advancement
            # tracking) but preserve hook content as a setup template
            hp = state_dir / "hooks.json"
            ho = _load_json(hp, {"hooks": []}) or {}
            hooks = ho.get("hooks", []) if isinstance(ho, dict) else []
            if isinstance(hooks, list):
                for h in hooks:
                    if isinstance(h, dict):
                        h.pop("lastAdvancedChapter", None)
                        h.pop("stale", None)
            _atomic_write_json(hp, {"hooks": hooks})

        # 4) Commit: rename tmp -> dst (atomic on same fs)
        tmp_dst.rename(dst)

    except Exception as e:
        # Clean up partial copy
        if tmp_dst.exists():
            shutil.rmtree(tmp_dst, ignore_errors=True)
        print(json.dumps({"error": f"copy failed: {e}"}), file=sys.stderr)
        return 1

    # 5) Touch inkos.json (no registry list maintained, but bump nothing —
    #    init_book.py only ensures inkos.json exists, books are discovered by
    #    scanning books/.  Nothing to update here.)

    payload = {
        "copied": {"from": args.srcId, "to": args.newId},
        "path": str(dst),
        "includeChapters": bool(args.include_chapters),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        emit_summary(
            f"action=copy {args.srcId} -> {args.newId} "
            f"includeChapters={bool(args.include_chapters)}"
        )
    else:
        print(f"Copied: {args.srcId} -> {args.newId}")
        print(f"Path: {dst}")
        if args.include_chapters:
            print("(chapters included)")
        else:
            print("(setup only — chapters/ empty, manifest reset to chapter 0)")
    return 0


# --------------------------- CLI -------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="book.py",
        description="Book CRUD: list / show / rename / delete / copy.",
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="<command>")

    # list
    sp = sub.add_parser("list", help="List all books under <workdir>/books/")
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    # show
    sp = sub.add_parser("show", help="Show one book's details")
    sp.add_argument("bookId")
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_show)

    # update
    sp = sub.add_parser("update",
                        help="Patch fields in books/<id>/book.json (atomic)")
    sp.add_argument("bookId")
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--title", default=None)
    sp.add_argument("--chapter-words", type=int, default=None,
                    help="Per-chapter target word count (book.json#chapterWordCount)")
    sp.add_argument("--target-chapters", type=int, default=None,
                    help="Total chapter target (book.json#targetChapters)")
    sp.add_argument("--genre", default=None,
                    help="Genre id (templates/genres/<id>.md must exist or "
                         "Writer will fall back to other.md)")
    sp.add_argument("--platform", default=None,
                    choices=sorted(VALID_PLATFORMS))
    sp.add_argument("--status", default=None,
                    choices=sorted(VALID_STATUSES))
    sp.add_argument("--lang", default=None, choices=sorted(VALID_LANGS),
                    help="Writing language (book.json#language)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_update)

    # rename
    sp = sub.add_parser("rename", help="Rename bookId (move dir + patch book.json)")
    sp.add_argument("oldId")
    sp.add_argument("newId")
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--update-references", action="store_true",
                    help="Scan runtime/+state/ JSON and rewrite bookId references")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_rename)

    # delete
    sp = sub.add_parser("delete", help="Delete or archive a book")
    sp.add_argument("bookId")
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--force", action="store_true",
                    help="Skip confirmation prompt")
    sp.add_argument("--archive", action="store_true",
                    help="Move to books_archive/ instead of rm -rf (default)")
    sp.add_argument("--no-archive", action="store_true",
                    help="With --force: actually delete files (no archive)")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_delete)

    # copy
    sp = sub.add_parser("copy", help="Clone a book setup under a new id")
    sp.add_argument("srcId")
    sp.add_argument("newId")
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--include-chapters", action="store_true",
                    help="Also copy chapters/*.md (default: chapters/ stays empty)")
    sp.add_argument("--title", default=None,
                    help="Optional new title to set on the copy")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_copy)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
