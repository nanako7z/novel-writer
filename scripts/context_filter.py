#!/usr/bin/env python3
"""Context Filter — Python port of `.inkos-src/utils/context-filter.ts`.

Lightweight noise reduction over four truth artifacts before injection into
Composer / Writer / Auditor prompts.  All filters share one guarantee:

    If filtering would empty the data rows, return the original input.

This means a malformed table never silently disappears; the LLM still sees
something to ground on.

Filters:
    hooks            drop pending_hooks rows whose status is resolved/closed/已回收
    summaries        keep only chapter_summaries rows from the last
                     `--keep-recent` chapters (relative to --current-chapter)
    subplots         drop subplot_board rows marked closed/resolved/已回收/已完结
    emotional-arcs   drop emotional_arcs rows older than `--keep-recent`
                     chapters
    all              run all four against their conventional file paths

CLI:
    python context_filter.py --book <bookDir> --current-chapter N \\
        --filter hooks|summaries|subplots|emotional-arcs|all \\
        [--keep-recent 6] [--json]

Output (JSON mode): {"filter": "...", "originalLines": N, "keptLines": M,
                     "content": "..." }
With --filter all the JSON output is a list of those records.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_KEEP_RECENT = 6  # mirrors inkos DEFAULT_CHAPTER_CADENCE_WINDOW

# Default file paths (relative to bookDir) for each filter.
DEFAULT_PATHS = {
    "hooks": "story/pending_hooks.md",
    "summaries": "story/chapter_summaries.md",
    "subplots": "story/subplot_board.md",
    "emotional-arcs": "story/emotional_arcs.md",
}

EMPTY_PLACEHOLDER = "(文件尚未创建)"

_HEADER_RE = re.compile(r"^\|\s*(章节|角色|支线|hook_id|Chapter|Character|Subplot)", re.I)
_CHAPTER_CELL_RE = re.compile(r"\|\s*(\d+)\s*\|")


def _is_header_row(line: str) -> bool:
    return bool(_HEADER_RE.match(line))


def _filter_table_rows(content: str, predicate) -> str:
    """Generic markdown-table row filter.

    Splits lines into non-table / header (separator + column-name) / data
    buckets, runs predicate against data rows, falls back to original if
    predicate empties the table.
    """
    lines = content.split("\n")
    non_table: list[str] = []
    headers: list[str] = []
    data: list[str] = []
    for line in lines:
        if not line.startswith("|"):
            non_table.append(line)
        elif "---" in line or _is_header_row(line):
            headers.append(line)
        else:
            data.append(line)

    kept = [row for row in data if predicate(row)]
    if not kept and data:
        return content  # fallback guarantee
    return "\n".join(non_table + headers + kept)


# ───────────────────────── per-filter logic ──────────────────────────


def filter_hooks(text: str) -> str:
    if not text or text.strip() == EMPTY_PLACEHOLDER:
        return text

    def keep(row: str) -> bool:
        low = row.lower()
        return ("已回收" not in row
                and "resolved" not in low
                and "closed" not in low)

    return _filter_table_rows(text, keep)


def filter_summaries(text: str, current_chapter: int,
                     keep_recent: int = DEFAULT_KEEP_RECENT) -> str:
    if not text or text.strip() == EMPTY_PLACEHOLDER:
        return text
    cutoff = current_chapter - keep_recent

    def keep(row: str) -> bool:
        m = _CHAPTER_CELL_RE.search(row)
        if not m:
            return True
        return int(m.group(1)) > cutoff

    return _filter_table_rows(text, keep)


def filter_subplots(text: str) -> str:
    if not text or text.strip() == EMPTY_PLACEHOLDER:
        return text

    def keep(row: str) -> bool:
        low = row.lower()
        return ("已回收" not in row
                and "已完结" not in row
                and "closed" not in low
                and "resolved" not in low)

    return _filter_table_rows(text, keep)


def filter_emotional_arcs(text: str, current_chapter: int,
                          keep_recent: int = DEFAULT_KEEP_RECENT) -> str:
    if not text or text.strip() == EMPTY_PLACEHOLDER:
        return text
    cutoff = current_chapter - keep_recent

    def keep(row: str) -> bool:
        m = _CHAPTER_CELL_RE.search(row)
        if not m:
            return True
        return int(m.group(1)) > cutoff

    return _filter_table_rows(text, keep)


# ───────────────────────────── runner ────────────────────────────────


def _load_text(path: Path) -> str:
    if not path.exists():
        return EMPTY_PLACEHOLDER
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return EMPTY_PLACEHOLDER


def _count_lines(text: str) -> int:
    if not text:
        return 0
    return len(text.split("\n"))


def run_filter(name: str, book_dir: Path, current_chapter: int,
               keep_recent: int) -> dict:
    rel = DEFAULT_PATHS.get(name)
    if rel is None:
        return {"filter": name, "originalLines": 0, "keptLines": 0,
                "content": "", "error": f"unknown filter: {name}"}
    src = book_dir / rel
    text = _load_text(src)
    original_lines = _count_lines(text)

    if name == "hooks":
        out = filter_hooks(text)
    elif name == "summaries":
        out = filter_summaries(text, current_chapter, keep_recent)
    elif name == "subplots":
        out = filter_subplots(text)
    elif name == "emotional-arcs":
        out = filter_emotional_arcs(text, current_chapter, keep_recent)
    else:
        out = text

    return {
        "filter": name,
        "source": str(src),
        "originalLines": original_lines,
        "keptLines": _count_lines(out),
        "content": out,
    }


# ───────────────────────────── CLI ──────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Filter truth artifacts before LLM injection (port of "
                    "inkos context-filter.ts). Drops resolved hooks, stale "
                    "subplots, and out-of-window summaries / arcs."
    )
    p.add_argument("--book", required=True, help="book directory path")
    p.add_argument("--current-chapter", type=int, required=True,
                   help="current chapter number (used by summaries / arcs)")
    p.add_argument("--filter", required=True,
                   choices=["hooks", "summaries", "subplots",
                            "emotional-arcs", "all"],
                   help="which filter to apply (or 'all' for every filter)")
    p.add_argument("--keep-recent", type=int, default=DEFAULT_KEEP_RECENT,
                   help=f"window size for summaries / arcs "
                        f"(default: {DEFAULT_KEEP_RECENT})")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="emit JSON wrapper (default for --filter all)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    book = Path(args.book).resolve()
    if not book.is_dir():
        print(json.dumps({"ok": False, "error": f"book dir not found: {book}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    if args.filter == "all":
        results = [
            run_filter(name, book, args.current_chapter, args.keep_recent)
            for name in ("hooks", "summaries", "subplots", "emotional-arcs")
        ]
        print(json.dumps({"ok": True, "results": results},
                         ensure_ascii=False, indent=2))
        return 0

    result = run_filter(args.filter, book, args.current_chapter, args.keep_recent)
    if args.as_json:
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    else:
        # Plain mode: emit just the filtered content, suitable for piping.
        sys.stdout.write(result["content"])
        if not result["content"].endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
