#!/usr/bin/env python3
"""POV-aware context filter.

Reads `story/character_matrix.md` + `story/state/hooks.json` (and optionally
a Composer `context_pkg.json`) and partitions context into:

  - visible: POV character has direct evidence (was in the scene, was told)
  - hidden:  POV character could not have witnessed it
  - inferred: POV character may plausibly deduce it (from clues, rumor)

In `--strict` mode, `inferred` is treated as `hidden`.

Usage:
    python pov_filter.py --book <bookDir> --pov <character-id> \\
        --current-chapter N [--input <context-pkg.json>] [--strict] [--json]

Output JSON:
    {
      "pov": "...",
      "currentChapter": N,
      "filtered_hooks": [...],          # only POV-aware hooks
      "filtered_subplots": [...],       # only POV-aware subplots (if subplot_board present)
      "pov_blindspots": [               # facts the chapter must NOT reveal inadvertently
        {"id": "...", "reason": "...", "category": "hook|subplot"}
      ],
      "filtered_context": [...] | null, # filtered selectedContext from --input pkg
      "summary": "..."
    }

Always exit 0 (advisory). See references/pov-filter.md.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable

# ─────────────────── small parsers ────────────────────────────


def load_json(p: Path, default):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_text(p: Path) -> str:
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def parse_md_table(text: str) -> list[dict]:
    """Parse the first markdown table; returns list of dict keyed by header."""
    rows: list[dict] = []
    headers: list[str] | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells:
            continue
        if all(re.match(r"^:?-+:?$", c) for c in cells):
            continue  # separator row
        if headers is None:
            headers = cells
            continue
        if len(cells) < len(headers):
            cells = cells + [""] * (len(headers) - len(cells))
        rows.append({headers[i]: cells[i] for i in range(len(headers))})
    return rows


def split_terms(s: str) -> list[str]:
    if not s:
        return []
    parts = re.split(r"[,、，;；]+", s)
    return [t.strip() for t in parts if t.strip()]


# ─────────────────── POV chapter set ──────────────────────────


def pov_present_chapters(
    chapter_summaries: list[dict],
    pov: str,
) -> set[int]:
    """Chapters where the POV character appears (in characters/events/title)."""
    present: set[int] = set()
    for s in chapter_summaries:
        ch = int(s.get("chapter", 0) or 0)
        if ch <= 0:
            continue
        blob = " ".join(
            str(s.get(k, "")) for k in
            ("title", "characters", "events", "stateChanges", "hookActivity")
        )
        if pov and pov in blob:
            present.add(ch)
    return present


def parse_chapter_summaries(book_dir: Path) -> list[dict]:
    """Try state/chapter_summaries.json then chapter_summaries.md table."""
    js = load_json(book_dir / "story" / "state" / "chapter_summaries.json", None)
    # inkos `rows` / legacy SKILL `summaries` — read both.
    if isinstance(js, dict):
        for key in ("rows", "summaries"):
            if isinstance(js.get(key), list):
                return js[key]
    if isinstance(js, list):
        return js
    md = load_text(book_dir / "story" / "chapter_summaries.md")
    return parse_md_table(md) if md else []


# ─────────────────── relationship from character_matrix ───────


def pov_relationships(book_dir: Path, pov: str) -> dict[str, str]:
    """Map other-character -> relationship label from character_matrix.md."""
    rel: dict[str, str] = {}
    text = load_text(book_dir / "story" / "character_matrix.md")
    if not text:
        return rel
    rows = parse_md_table(text)
    for row in rows:
        a = row.get("charA", "").strip()
        b = row.get("charB", "").strip()
        rship = row.get("relationship", "").strip()
        if not (a and b and rship):
            continue
        if a == pov:
            rel[b] = rship
        elif b == pov:
            rel[a] = rship
    return rel


# ─────────────────── visibility classifiers ───────────────────


def classify_hook(
    hook: dict,
    pov: str,
    pov_chapters: set[int],
    current_chapter: int,
) -> tuple[str, str]:
    """Returns (visibility, reason). visibility ∈ {visible, inferred, hidden}."""
    involved = set()
    for k in ("involvedCharacters", "characters"):
        v = hook.get(k)
        if isinstance(v, list):
            involved.update(str(x).strip() for x in v if str(x).strip())
        elif isinstance(v, str):
            involved.update(split_terms(v))

    notes_blob = " ".join(
        str(hook.get(k, "")) for k in
        ("notes", "expectedPayoff", "seedBeat", "tags")
    )

    start = int(hook.get("startChapter", 0) or 0)
    last_adv = int(hook.get("lastAdvancedChapter", 0) or 0)
    mention_chapters: set[int] = set()
    raw_mc = hook.get("mentionChapters")
    if isinstance(raw_mc, list):
        for x in raw_mc:
            try:
                mention_chapters.add(int(x))
            except Exception:
                pass
    if start > 0:
        mention_chapters.add(start)
    if last_adv > 0:
        mention_chapters.add(last_adv)

    # 1. POV character is in the involved list → visible
    if pov in involved:
        return "visible", f"POV in involvedCharacters"

    # 2. POV was in any of the hook's mention chapters → visible
    overlap = mention_chapters & pov_chapters
    if overlap:
        return "visible", f"POV present in chapter(s) {sorted(overlap)}"

    # 3. POV name in notes / expectedPayoff → inferred
    if pov and pov in notes_blob:
        return "inferred", "POV name appears in hook notes/payoff"

    # 4. No witness window for POV → hidden
    return "hidden", f"POV not in chapters {sorted(mention_chapters)} and not involved"


def classify_subplot_row(
    row: dict,
    pov: str,
    pov_chapters: set[int],
) -> tuple[str, str]:
    blob = " ".join(str(v) for v in row.values())
    if pov and pov in blob:
        # Try to find a chapter cell
        for k, v in row.items():
            if "chapter" in k.lower() or "章" in k:
                m = re.search(r"\d+", str(v))
                if m and int(m.group()) in pov_chapters:
                    return "visible", "POV explicitly in subplot row"
        return "inferred", "POV name appears in subplot row"
    # No POV name + no chapter overlap heuristic available
    return "hidden", "subplot row does not mention POV"


# ─────────────────── filtering pipeline ───────────────────────


def filter_hooks(
    hooks: list[dict],
    pov: str,
    pov_chapters: set[int],
    current_chapter: int,
    strict: bool,
) -> tuple[list[dict], list[dict]]:
    """Returns (filtered_hooks, blindspot_hooks)."""
    visible: list[dict] = []
    blindspots: list[dict] = []
    for h in hooks:
        status = str(h.get("status", "")).strip().lower()
        # only consider active-ish
        if status in ("resolved", "abandoned", "completed", "closed"):
            continue
        vis, reason = classify_hook(h, pov, pov_chapters, current_chapter)
        record = {**h, "_pov_visibility": vis, "_pov_reason": reason}
        if vis == "visible":
            visible.append(record)
        elif vis == "inferred" and not strict:
            visible.append(record)
        else:
            blindspots.append({
                "id": str(h.get("hookId", h.get("id", "?"))),
                "reason": reason,
                "category": "hook",
                "expectedPayoff": str(h.get("expectedPayoff", ""))[:80],
            })
    return visible, blindspots


def filter_subplots(
    book_dir: Path,
    pov: str,
    pov_chapters: set[int],
    strict: bool,
) -> tuple[list[dict], list[dict]]:
    text = load_text(book_dir / "story" / "subplot_board.md")
    if not text:
        return [], []
    rows = parse_md_table(text)
    visible: list[dict] = []
    blindspots: list[dict] = []
    for row in rows:
        vis, reason = classify_subplot_row(row, pov, pov_chapters)
        record = {**row, "_pov_visibility": vis, "_pov_reason": reason}
        if vis == "visible":
            visible.append(record)
        elif vis == "inferred" and not strict:
            visible.append(record)
        else:
            ident = row.get("subplotId") or row.get("id") or row.get("title") or "?"
            blindspots.append({
                "id": str(ident),
                "reason": reason,
                "category": "subplot",
            })
    return visible, blindspots


def filter_context_pkg(
    pkg: dict,
    pov: str,
    pov_chapters: set[int],
    blindspot_ids: set[str],
    strict: bool,
) -> list[dict]:
    """Strip selectedContext entries that mention only blindspot hooks/subplots."""
    out: list[dict] = []
    sc = pkg.get("selectedContext") if isinstance(pkg, dict) else None
    if not isinstance(sc, list):
        return out
    for item in sc:
        excerpt = str(item.get("excerpt", "")) if isinstance(item, dict) else ""
        source = str(item.get("source", "")) if isinstance(item, dict) else ""
        # If excerpt mentions a blindspot id and does NOT mention POV, drop it
        mentions_blindspot = any(bid and bid in excerpt for bid in blindspot_ids)
        mentions_pov = pov and pov in excerpt
        # In strict mode, also drop hook_debt entries that don't reference POV
        is_hook_debt = "hook_debt" in source
        if mentions_blindspot and not mentions_pov:
            continue
        if strict and is_hook_debt and not mentions_pov:
            continue
        out.append(item)
    return out


# ─────────────────── main ─────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="POV-aware context filter (deterministic, no LLM).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--book", required=True, help="book directory")
    p.add_argument("--pov", required=True, help="POV character id / name")
    p.add_argument("--current-chapter", type=int, required=True)
    p.add_argument("--input", help="optional ContextPackage JSON to filter")
    p.add_argument("--strict", action="store_true",
                   help="treat 'inferred' as 'hidden' (drop)")
    p.add_argument("--json", action="store_true", help="output JSON (default)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        print(json.dumps({
            "error": f"book dir not found: {book_dir}",
            "summary": "error",
        }, ensure_ascii=False, indent=2))
        return 0

    pov = args.pov.strip()
    cur = args.current_chapter

    # 1. Load hooks
    hooks_obj = load_json(book_dir / "story" / "state" / "hooks.json", {"hooks": []})
    hooks = hooks_obj.get("hooks", []) if isinstance(hooks_obj, dict) else []

    # 2. POV chapter presence set
    summaries = parse_chapter_summaries(book_dir)
    pov_chapters = pov_present_chapters(summaries, pov)

    # 3. POV relationships (informational)
    relationships = pov_relationships(book_dir, pov)

    # 4. Filter hooks
    filtered_hooks, hook_blindspots = filter_hooks(
        hooks, pov, pov_chapters, cur, args.strict
    )

    # 5. Filter subplots
    filtered_subs, subplot_blindspots = filter_subplots(
        book_dir, pov, pov_chapters, args.strict
    )

    blindspots = hook_blindspots + subplot_blindspots
    blindspot_ids = {bs["id"] for bs in blindspots if bs.get("id")}

    # 6. Filter input context_pkg if given
    filtered_context: list[dict] | None = None
    if args.input:
        pkg = load_json(Path(args.input), {})
        filtered_context = filter_context_pkg(
            pkg, pov, pov_chapters, blindspot_ids, args.strict
        )

    summary = (
        f"pov={pov}, povChapters={len(pov_chapters)}, "
        f"hooks visible={len(filtered_hooks)}/{len(hooks)}, "
        f"subplots visible={len(filtered_subs)}, "
        f"blindspots={len(blindspots)}, "
        f"strict={args.strict}"
    )

    out = {
        "pov": pov,
        "currentChapter": cur,
        "strict": args.strict,
        "povChapters": sorted(pov_chapters),
        "relationships": relationships,
        "filtered_hooks": filtered_hooks,
        "filtered_subplots": filtered_subs,
        "pov_blindspots": blindspots,
        "filtered_context": filtered_context,
        "summary": summary,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
