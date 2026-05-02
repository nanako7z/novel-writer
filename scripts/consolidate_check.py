#!/usr/bin/env python3
"""Consolidate trigger detector (Phase 12 — Consolidator).

Read-only sanity check that decides whether `chapter_summaries.json` is fat
enough AND has at least one completed volume to make a Consolidator pass
worthwhile. Does NOT call any LLM, does NOT modify any truth file.

Claude reads this output and decides whether to invoke phase 12.

Usage:
    python consolidate_check.py --book <bookDir> [--threshold 60] [--json]

Output (--json, the default-ish for tooling):
    {
      "shouldConsolidate": true|false,
      "totalChapters": N,
      "totalVolumes": M,
      "completedVolumes": K,
      "completedVolumeNumbers": [1, 2, 3],
      "threshold": 60,
      "reason": "60+ chapter summaries, 3 volumes complete"
    }
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Volume header / range regex — mirrors inkos consolidator.parseVolumeBoundaries
VOLUME_HEADER_RE = re.compile(
    r"^(第[一二三四五六七八九十百千万零〇\d]+卷|Volume\s+\d+)",
    re.IGNORECASE,
)
RANGE_RE = re.compile(
    r"[（(]\s*(?:第|[Cc]hapters?\s+)?(\d+)\s*[-–~～—]\s*(\d+)\s*(?:章)?\s*[）)]"
    r"|(?:第|[Cc]hapters?\s+)(\d+)\s*[-–~～—]\s*(\d+)\s*(?:章)?",
    re.IGNORECASE,
)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def parse_volume_map(raw: str) -> list[dict[str, Any]]:
    """Return [{name, startCh, endCh}, ...] in order of appearance."""
    out: list[dict[str, Any]] = []
    for raw_line in raw.split("\n"):
        line = re.sub(r"^#+\s*", "", raw_line).strip()
        if not VOLUME_HEADER_RE.match(line):
            continue
        m = RANGE_RE.search(line)
        if not m:
            continue
        start = int(m.group(1) or m.group(3) or 0)
        end = int(m.group(2) or m.group(4) or 0)
        if start <= 0 or end <= 0 or end < start:
            continue
        name = line[: m.start()].rstrip("（( ").strip()
        if not name:
            continue
        out.append({"name": name, "startCh": start, "endCh": end})
    return out


def read_volume_map(book_dir: Path) -> str:
    """Prefer outline/volume_map.md, fall back to legacy volume_outline.md."""
    new_path = book_dir / "story" / "outline" / "volume_map.md"
    txt = read_text(new_path)
    if txt.strip():
        return txt
    return read_text(book_dir / "story" / "volume_outline.md")


def detect(book_dir: Path, threshold: int) -> dict[str, Any]:
    summaries_obj = load_json(
        book_dir / "story" / "state" / "chapter_summaries.json",
        {"rows": []},
    )
    # inkos `rows` / legacy SKILL `summaries` — read both.
    summaries = (summaries_obj.get("rows", summaries_obj.get("summaries", []))
                 if isinstance(summaries_obj, dict) else [])
    total_chapters = len(summaries)

    manifest = load_json(book_dir / "story" / "state" / "manifest.json", {})
    last_applied = int(manifest.get("lastAppliedChapter", 0) or 0)
    if last_applied == 0 and summaries:
        # Fallback: derive from summaries themselves.
        nums = [int(s.get("chapter", 0) or 0) for s in summaries if isinstance(s, dict)]
        if nums:
            last_applied = max(nums)

    volume_raw = read_volume_map(book_dir)
    volumes = parse_volume_map(volume_raw)
    total_volumes = len(volumes)

    completed = [v for v in volumes if v["endCh"] <= last_applied and last_applied > 0]
    completed_numbers = list(range(1, len(completed) + 1))  # 1-indexed positional

    # An archive already-done check: if all completed volumes are already
    # archived (story/archive/volume-{N}.json exists), nothing left to do.
    archive_dir = book_dir / "story" / "archive"
    pending_completed = []
    for idx, vol in enumerate(completed, start=1):
        archive_path = archive_dir / f"volume-{idx}.json"
        if not archive_path.is_file():
            pending_completed.append(idx)

    over_threshold = total_chapters >= threshold
    has_pending = len(pending_completed) > 0
    should = over_threshold and has_pending

    if not volumes:
        reason = "no volumes parsed from volume_map.md (or it's missing)"
    elif not completed:
        reason = f"{total_chapters} summaries, but no completed volumes yet (lastAppliedChapter={last_applied})"
    elif not has_pending:
        reason = f"{len(completed)} volume(s) complete but all already archived"
    elif not over_threshold:
        reason = f"only {total_chapters} summaries (threshold={threshold})"
    else:
        reason = (
            f"{total_chapters}+ chapter summaries, "
            f"{len(pending_completed)} volume(s) ready to consolidate"
        )

    return {
        "shouldConsolidate": should,
        "totalChapters": total_chapters,
        "totalVolumes": total_volumes,
        "completedVolumes": len(completed),
        "completedVolumeNumbers": pending_completed,
        "threshold": threshold,
        "reason": reason,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Detect whether Phase 12 Consolidator should run on a book.",
    )
    p.add_argument("--book", required=True, help="book directory (containing story/)")
    p.add_argument(
        "--threshold", type=int, default=60,
        help="minimum chapter_summaries row count before suggesting consolidate (default 60)",
    )
    p.add_argument("--json", action="store_true", help="emit JSON to stdout (default behaviour)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    book = Path(args.book).resolve()
    if not book.is_dir():
        print(
            json.dumps({"error": f"book dir not found: {book}"}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    if args.threshold < 1:
        print(
            json.dumps({"error": "--threshold must be >= 1"}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1

    result = detect(book, args.threshold)
    # --json (or default) emits compact JSON; either way we print the dict.
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
