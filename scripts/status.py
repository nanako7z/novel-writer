#!/usr/bin/env python3
"""Project status: per-book progress, hooks, chapters.

Read-only.  No LLM.  Looks up `inkos.json` in cwd or any ancestor; lists books
under `books/<id>/` (or treats cwd as a single-book dir if it has `book.json`).

Outputs:
  text  (default)  — human checklist, one section per book
  json             — structured, machine-readable

Per-book stats:
  bookId, title, genre, language, targetChapters, chapterWordCount,
  lastAppliedChapter, totalChapters, totalWords, progressPercent,
  activeHooks, staleHooks, pendingReviewChapters, lastModified

Length counting honours `book.lengthCountingMode`
(`zh_chars` | `en_words`); falls back to `language` (`zh` -> chars,
`en` -> words).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _chapter_files import CHAPTER_NAME_RE, all_chapter_files  # noqa: E402


# --------------------------- discovery -------------------------------------

def find_project_root(start: Path) -> Path | None:
    """Walk up from `start` looking for `inkos.json`."""
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / "inkos.json").is_file():
            return p
    return None


def list_books(project_root: Path) -> list[Path]:
    books_dir = project_root / "books"
    if not books_dir.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(books_dir.iterdir()):
        if child.is_dir() and (child / "book.json").is_file():
            out.append(child)
    return out


# --------------------------- length counting -------------------------------

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


def count_length(text: str, mode: str) -> int:
    body = _strip_metadata(text)
    if mode == "en_words":
        return len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", body))
    # zh_chars: strip whitespace
    return len(re.sub(r"\s+", "", body))


def resolve_counting_mode(book: dict) -> str:
    raw = book.get("lengthCountingMode")
    if raw in ("zh_chars", "en_words"):
        return raw
    lang = book.get("language") or "zh"
    return "en_words" if lang == "en" else "zh_chars"


# --------------------------- per-book stats --------------------------------


def _safe_load_json(p: Path, default: Any) -> Any:
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _iso_mtime(path: Path) -> str | None:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).isoformat()


def collect_book_stats(book_dir: Path, *, with_chapters: bool) -> dict:
    book = _safe_load_json(book_dir / "book.json", {}) or {}
    state_dir = book_dir / "story" / "state"

    manifest = _safe_load_json(state_dir / "manifest.json", {}) or {}
    last_applied = int(manifest.get("lastAppliedChapter", 0) or 0)

    target_chapters = int(book.get("targetChapters", 0) or 0)
    chapter_word_count = int(book.get("chapterWordCount", 0) or 0)
    counting_mode = resolve_counting_mode(book)

    chapter_files = all_chapter_files(book_dir)
    total_chapters = len(chapter_files)

    # Per-chapter status: prefer chapters/index.json (operational index) when
    # present; fall back to chapter_summaries.json status field.  index.json is
    # the authoritative source per references/schemas/chapter-index.md.
    index_obj = _safe_load_json(book_dir / "chapters" / "index.json", []) or []
    index_by_ch: dict[int, dict] = {}
    if isinstance(index_obj, list):
        for row in index_obj:
            if isinstance(row, dict) and isinstance(row.get("number"), int):
                index_by_ch[row["number"]] = row

    summaries_obj = _safe_load_json(state_dir / "chapter_summaries.json",
                                    {"rows": []}) or {}
    # inkos `rows` / legacy SKILL `summaries` — read both.
    summaries = (summaries_obj.get("rows", summaries_obj.get("summaries", []))
                 if isinstance(summaries_obj, dict) else [])
    summary_by_ch: dict[int, dict] = {}
    if isinstance(summaries, list):
        for row in summaries:
            if isinstance(row, dict) and isinstance(row.get("chapter"), int):
                summary_by_ch[row["chapter"]] = row

    total_words = 0
    chapter_rows: list[dict] = []
    last_modified: str | None = None
    pending_review = 0

    for f in chapter_files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            text = ""
        wc = count_length(text, counting_mode)
        total_words += wc
        m = CHAPTER_NAME_RE.match(f.name)
        ch_num = int(m.group(1)) if m else 0

        mtime = _iso_mtime(f)
        if mtime and (last_modified is None or mtime > last_modified):
            last_modified = mtime

        # index.json wins; chapter_summaries.json is the fallback
        idx_row = index_by_ch.get(ch_num) or {}
        sum_row = summary_by_ch.get(ch_num) or {}
        status = idx_row.get("status") or sum_row.get("status")
        title = idx_row.get("title") or sum_row.get("title") or ""
        if status and status not in ("approved", "published"):
            pending_review += 1

        if with_chapters:
            chapter_rows.append({
                "chapter": ch_num,
                "title": title,
                "wordCount": wc,
                "status": status,
                "lastModified": mtime,
            })

    hooks_obj = _safe_load_json(state_dir / "hooks.json", {"hooks": []}) or {}
    hooks = hooks_obj.get("hooks", []) if isinstance(hooks_obj, dict) else []
    active_hooks = 0
    stale_hooks = 0
    if isinstance(hooks, list):
        for h in hooks:
            if not isinstance(h, dict):
                continue
            status = (h.get("status") or "").strip().lower()
            if status in {"resolved", "closed", "done", "已回收", "已解决"}:
                continue
            active_hooks += 1
            if h.get("stale"):
                stale_hooks += 1

    progress = (last_applied / target_chapters * 100.0) if target_chapters > 0 else 0.0

    out = {
        "bookId": book.get("id") or book_dir.name,
        "title": book.get("title") or book_dir.name,
        "genre": book.get("genre"),
        "language": book.get("language", "zh"),
        "platform": book.get("platform"),
        "targetChapters": target_chapters,
        "chapterWordCount": chapter_word_count,
        "lastAppliedChapter": last_applied,
        "totalChapters": total_chapters,
        "totalWords": total_words,
        "countingMode": counting_mode,
        "progressPercent": round(progress, 1),
        "activeHooks": active_hooks,
        "staleHooks": stale_hooks,
        "pendingReviewChapters": pending_review,
        "lastModified": last_modified,
        "bookDir": str(book_dir),
    }
    if with_chapters:
        out["chapters"] = chapter_rows
    return out


# --------------------------- text rendering --------------------------------

def _word_label(mode: str) -> str:
    return "words" if mode == "en_words" else "字"


def render_book_text(stats: dict, *, with_chapters: bool) -> str:
    lines: list[str] = []
    lines.append(f"{stats['title']} ({stats['bookId']})")
    lines.append(f"  Genre: {stats.get('genre') or '-'}  | Language: {stats.get('language')}  | Platform: {stats.get('platform') or '-'}")

    target = stats["targetChapters"] or 0
    last = stats["lastAppliedChapter"]
    pct = stats["progressPercent"]
    progress_str = f"{last}/{target} ({pct}%)" if target else f"{last} (no target set)"
    warn = ""
    if target and last == 0:
        warn = "  (not started)"
    elif target and stats["totalChapters"] < last:
        warn = "  (warning: missing chapter files)"
    lines.append(f"  Progress: {progress_str}{warn}")

    label = _word_label(stats["countingMode"])
    lines.append(f"  Chapters on disk: {stats['totalChapters']}  | Total {label}: {stats['totalWords']}  | Per-chapter target: {stats['chapterWordCount']}")

    hook_warn = ""
    if stats["activeHooks"] > 12:
        hook_warn = "  (over soft cap 12)"
    elif stats["staleHooks"] > 0:
        hook_warn = f"  (stale: {stats['staleHooks']})"
    lines.append(f"  Active hooks: {stats['activeHooks']}{hook_warn}")

    if stats["pendingReviewChapters"]:
        lines.append(f"  Pending review chapters: {stats['pendingReviewChapters']}")

    if stats["lastModified"]:
        lines.append(f"  Last modified: {stats['lastModified']}")

    if with_chapters and stats.get("chapters"):
        lines.append("")
        lines.append("  Chapters:")
        lines.append(f"    {'#':>4}  {'words':>6}  {'status':<18}  {'modified':<28}  title")
        for row in stats["chapters"]:
            mod = row.get("lastModified") or "-"
            status = row.get("status") or "-"
            num = row.get("chapter") or 0
            wc = row.get("wordCount") or 0
            title = row.get("title") or ""
            lines.append(f"    {num:>4}  {wc:>6}  {status:<18}  {mod:<28}  {title}")
    return "\n".join(lines)


# --------------------------- main ------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Show project / book status (read-only).",
    )
    p.add_argument("--book", default=None,
                   help="Path to a single book dir (overrides project lookup)")
    p.add_argument("--all", action="store_true",
                   help="Show every book under project root (default if no --book)")
    p.add_argument("--chapters", action="store_true",
                   help="Include per-chapter table")
    p.add_argument("--json", action="store_true",
                   help="Emit structured JSON instead of text")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cwd = Path.cwd()

    book_dirs: list[Path] = []
    project_root: Path | None = None

    if args.book:
        bd = Path(args.book).resolve()
        if not bd.is_dir() or not (bd / "book.json").is_file():
            print(json.dumps({"error": f"not a book dir: {bd}"},
                             ensure_ascii=False), file=sys.stderr)
            return 1
        book_dirs = [bd]
        # try to infer project root for context (optional)
        project_root = find_project_root(bd)
    else:
        project_root = find_project_root(cwd)
        if project_root is not None:
            book_dirs = list_books(project_root)
            if not book_dirs:
                # project shell exists but empty
                book_dirs = []
        else:
            # No inkos.json anywhere — try cwd as a single book dir
            if (cwd / "book.json").is_file():
                book_dirs = [cwd]
            else:
                print(json.dumps({
                    "error": "no inkos.json in cwd or ancestors and cwd has no book.json",
                    "cwd": str(cwd),
                }, ensure_ascii=False), file=sys.stderr)
                return 1

    payload = {
        "projectRoot": str(project_root) if project_root else None,
        "bookCount": len(book_dirs),
        "books": [],
    }
    for bd in book_dirs:
        payload["books"].append(collect_book_stats(bd, with_chapters=args.chapters))

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # text rendering
    out: list[str] = []
    if project_root:
        out.append(f"Project: {project_root}")
    out.append(f"Books: {len(book_dirs)}")
    out.append("")
    if not book_dirs:
        out.append("(no books found — run scripts/init_book.py first)")
    for stats in payload["books"]:
        out.append(render_book_text(stats, with_chapters=args.chapters))
        out.append("")
    print("\n".join(out).rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
