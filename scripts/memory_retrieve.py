#!/usr/bin/env python3
"""Sliding-window memory retrieval for the Composer phase.

Replaces inkos's SQLite-backed `utils/memory-retrieval.ts` with a stdlib-only
pass over our markdown / JSON truth files. Given a current chapter number,
selects:

  * recent chapter summaries (full)
  * relevant deeper-history summaries (events-only) by character/hook overlap
  * active hooks within the chapter window
  * (optional) recently resolved hooks for "just-payoff" continuity
  * character roster touched by the recent + relevant windows
  * a snapshot of story/state/current_state.json

Output is JSON (default) or a markdown digest meant for direct prompt injection.

CLI:
  python memory_retrieve.py --book <bookDir> --current-chapter N \
    [--window-recent 6] [--window-relevant 8] \
    [--include-resolved-hooks] [--format json|markdown]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ACTIVE_STATUSES = {"open", "progressing", "deferred"}
RESOLVED_LOOKBACK_CHAPTERS = 3
HOOK_WINDOW_CHAPTERS = 12


# ───────────────────────── IO helpers ──────────────────────────


def load_json(p: Path, default: Any) -> Any:
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def read_text(p: Path) -> str:
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


# ──────────────────────── Selection logic ───────────────────────


def split_terms(s: str) -> list[str]:
    """Split a comma-or-Chinese-comma-separated list into clean tokens."""
    if not s:
        return []
    parts = re.split(r"[,、，;]+", s)
    return [t.strip() for t in parts if t.strip()]


def hook_active(hook: dict) -> bool:
    status = str(hook.get("status", "")).strip().lower()
    return status in ACTIVE_STATUSES


def hook_in_window(hook: dict, current_chapter: int) -> bool:
    """Port of inkos isHookWithinChapterWindow + coreHook clause."""
    last_adv = int(hook.get("lastAdvancedChapter", 0) or 0)
    start = int(hook.get("startChapter", 0) or 0)
    core = bool(hook.get("coreHook", False))
    threshold = current_chapter - HOOK_WINDOW_CHAPTERS
    return last_adv >= threshold or start >= threshold or core


def hook_resolved_recently(hook: dict, current_chapter: int) -> bool:
    if str(hook.get("status", "")).strip().lower() != "resolved":
        return False
    last_adv = int(hook.get("lastAdvancedChapter", 0) or 0)
    return last_adv >= current_chapter - RESOLVED_LOOKBACK_CHAPTERS


def select_recent_summaries(summaries: list[dict], current_chapter: int, n: int) -> list[dict]:
    """Last `n` chapter summaries strictly before `current_chapter`."""
    eligible = [s for s in summaries if int(s.get("chapter", 0) or 0) < current_chapter]
    eligible.sort(key=lambda s: int(s.get("chapter", 0) or 0))
    return eligible[-n:] if n > 0 else []


def collect_anchor_terms(summaries: Iterable[dict], hooks: Iterable[dict]) -> set[str]:
    """Characters + hookIds + hook expectedPayoff fragments to use for relevance."""
    terms: set[str] = set()
    for s in summaries:
        for c in split_terms(str(s.get("characters", ""))):
            terms.add(c.lower())
    for h in hooks:
        hid = str(h.get("hookId", "")).strip()
        if hid:
            terms.add(hid.lower())
    return terms


def summary_mentions(summary: dict, terms: set[str]) -> bool:
    if not terms:
        return False
    blob = " ".join(
        str(summary.get(k, ""))
        for k in ("title", "characters", "events", "stateChanges", "hookActivity", "chapterType")
    ).lower()
    return any(t in blob for t in terms if t)


def select_relevant_summaries(
    summaries: list[dict],
    current_chapter: int,
    recent_cutoff: int,
    anchor_terms: set[str],
    n: int,
) -> list[dict]:
    """Deeper history (chapter < current - window_recent) summaries that mention an anchor.

    Output is truncated to the `events` field only — these are cheap context
    pings, not full memories.
    """
    deep = [
        s for s in summaries
        if int(s.get("chapter", 0) or 0) < recent_cutoff
        and summary_mentions(s, anchor_terms)
    ]
    deep.sort(key=lambda s: int(s.get("chapter", 0) or 0), reverse=True)
    picked = deep[:n] if n > 0 else []
    picked.sort(key=lambda s: int(s.get("chapter", 0) or 0))
    return [
        {
            "chapter": int(s.get("chapter", 0) or 0),
            "title": str(s.get("title", "")),
            "events": str(s.get("events", "")),
        }
        for s in picked
    ]


def select_active_hooks(hooks: list[dict], current_chapter: int) -> list[dict]:
    return [
        h for h in hooks
        if hook_active(h) and hook_in_window(h, current_chapter)
    ]


def select_recently_resolved_hooks(hooks: list[dict], current_chapter: int) -> list[dict]:
    return [h for h in hooks if hook_resolved_recently(h, current_chapter)]


# ─────────────────── Character roster (markdown table) ────────


def parse_character_matrix(md: str) -> list[dict]:
    """Parse the character_matrix.md markdown table.

    Columns: charA | charB | relationship | intimacy | lastInteraction | notes
    Skips header / separator lines.
    """
    rows: list[dict] = []
    for line in md.splitlines():
        line = line.rstrip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 6:
            continue
        # Skip header and divider rows
        if cells[0].lower() in {"chara", "char a", "角色a", "角色 a"}:
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        rows.append({
            "charA": cells[0],
            "charB": cells[1],
            "relationship": cells[2],
            "intimacy": cells[3],
            "lastInteraction": cells[4],
            "notes": cells[5] if len(cells) > 5 else "",
        })
    return rows


def select_character_roster(matrix_rows: list[dict], characters_in_window: set[str]) -> list[dict]:
    if not characters_in_window:
        return []
    wanted = {c.lower() for c in characters_in_window if c}
    out: list[dict] = []
    for row in matrix_rows:
        a = row.get("charA", "").lower()
        b = row.get("charB", "").lower()
        if a in wanted or b in wanted:
            out.append(row)
    return out


# ─────────────────────── Markdown rendering ────────────────────


def render_markdown(payload: dict) -> str:
    lines: list[str] = []
    cn = payload["currentChapter"]
    lines.append(f"# Memory window for chapter {cn}")
    stats = payload.get("stats", {})
    lines.append(
        f"_recent={stats.get('recentCount', 0)} "
        f"relevant={stats.get('relevantCount', 0)} "
        f"activeHooks={stats.get('activeHookCount', 0)} "
        f"chars≈{stats.get('totalChars', 0)}_"
    )
    lines.append("")

    lines.append("## Recent chapter summaries")
    for s in payload["recentSummaries"]:
        ch = s.get("chapter", "?")
        title = s.get("title", "")
        events = s.get("events", "")
        chars = s.get("characters", "")
        hook_act = s.get("hookActivity", "")
        lines.append(f"- **ch{ch} {title}** — {events}")
        if chars:
            lines.append(f"  - characters: {chars}")
        if hook_act:
            lines.append(f"  - hooks: {hook_act}")
    lines.append("")

    if payload["relevantSummaries"]:
        lines.append("## Relevant earlier summaries (events-only)")
        for s in payload["relevantSummaries"]:
            lines.append(f"- ch{s['chapter']} {s['title']}: {s['events']}")
        lines.append("")

    lines.append("## Active hooks")
    for h in payload["activeHooks"]:
        hid = h.get("hookId", "?")
        typ = h.get("type", "")
        status = h.get("status", "")
        last = h.get("lastAdvancedChapter", "?")
        payoff = h.get("expectedPayoff", "")
        lines.append(f"- `{hid}` [{typ}/{status}, last advanced ch{last}] → {payoff}")
    lines.append("")

    if payload.get("recentlyResolvedHooks"):
        lines.append("## Recently resolved hooks (last 3 chapters)")
        for h in payload["recentlyResolvedHooks"]:
            lines.append(
                f"- `{h.get('hookId','?')}` resolved ch{h.get('lastAdvancedChapter','?')}: "
                f"{h.get('expectedPayoff','')}"
            )
        lines.append("")

    if payload["characterRoster"]:
        lines.append("## Character roster (last interaction)")
        for r in payload["characterRoster"]:
            lines.append(
                f"- {r.get('charA','')} ↔ {r.get('charB','')} "
                f"[{r.get('relationship','')}, intimacy {r.get('intimacy','')}]: "
                f"{r.get('lastInteraction','')}"
            )
        lines.append("")

    cs = payload.get("currentState") or {}
    if cs:
        lines.append("## Current state snapshot")
        facts = cs.get("facts") or []
        if isinstance(facts, list) and facts:
            for f in facts:
                if isinstance(f, dict):
                    lines.append(
                        f"- {f.get('subject','')} / {f.get('predicate','')} / {f.get('object','')}"
                    )
                else:
                    lines.append(f"- {f}")
        else:
            # Fall back to dumping known keys flatly
            for k, v in cs.items():
                if k == "facts":
                    continue
                lines.append(f"- {k}: {v}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ─────────────────────────── CLI driver ────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Sliding-window memory retrieval over markdown/JSON truth files.",
    )
    p.add_argument("--book", required=True, help="book directory (containing story/)")
    p.add_argument("--current-chapter", type=int, required=True, help="chapter number being composed")
    p.add_argument("--window-recent", type=int, default=6, help="how many recent summaries to include in full")
    p.add_argument("--window-relevant", type=int, default=8, help="max deeper-history summaries to include")
    p.add_argument(
        "--include-resolved-hooks", action="store_true",
        help="also include hooks resolved in the last 3 chapters (for cliff-resolution chapters)",
    )
    p.add_argument("--format", choices=["json", "markdown"], default="json")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    book = Path(args.book).resolve()
    if not book.is_dir():
        print(json.dumps({"error": f"book dir not found: {book}"}, ensure_ascii=False), file=sys.stderr)
        return 1
    if args.current_chapter < 1:
        print(json.dumps({"error": "--current-chapter must be >= 1"}, ensure_ascii=False), file=sys.stderr)
        return 1

    state = book / "story" / "state"
    summaries_obj = load_json(state / "chapter_summaries.json", {"summaries": []})
    hooks_obj = load_json(state / "hooks.json", {"hooks": []})
    current_state = load_json(state / "current_state.json", {"facts": []})

    summaries = summaries_obj.get("summaries", []) if isinstance(summaries_obj, dict) else []
    hooks = hooks_obj.get("hooks", []) if isinstance(hooks_obj, dict) else []

    # 1. Recent window — full content
    recent = select_recent_summaries(summaries, args.current_chapter, args.window_recent)
    recent_cutoff = args.current_chapter - args.window_recent

    # 2. Active hooks within the chapter window
    active_hooks = select_active_hooks(hooks, args.current_chapter)

    # 3. Anchor terms = characters in recent window + active hookIds
    anchor_terms = collect_anchor_terms(recent, active_hooks)

    # 4. Relevant deeper-history summaries (events-only)
    relevant = select_relevant_summaries(
        summaries, args.current_chapter, recent_cutoff, anchor_terms, args.window_relevant,
    )

    # 5. Recently resolved hooks (optional)
    recently_resolved = (
        select_recently_resolved_hooks(hooks, args.current_chapter)
        if args.include_resolved_hooks else []
    )

    # 6. Character roster — characters mentioned in recent + relevant summaries
    chars_in_window: set[str] = set()
    for s in recent:
        chars_in_window.update(split_terms(str(s.get("characters", ""))))
    matrix_md = read_text(book / "story" / "character_matrix.md")
    matrix_rows = parse_character_matrix(matrix_md)
    roster = select_character_roster(matrix_rows, chars_in_window)

    payload = {
        "currentChapter": args.current_chapter,
        "recentSummaries": recent,
        "relevantSummaries": relevant,
        "activeHooks": active_hooks,
        "recentlyResolvedHooks": recently_resolved,
        "characterRoster": roster,
        "currentState": current_state if isinstance(current_state, dict) else {},
        "stats": {
            "recentCount": len(recent),
            "relevantCount": len(relevant),
            "activeHookCount": len(active_hooks),
            "totalChars": 0,  # filled in below
        },
    }

    if args.format == "markdown":
        out = render_markdown(payload)
    else:
        out = json.dumps(payload, ensure_ascii=False, indent=2)
    payload["stats"]["totalChars"] = len(out)
    if args.format == "json":
        # Re-serialize so totalChars reflects final size (best-effort: tiny drift is fine)
        out = json.dumps(payload, ensure_ascii=False, indent=2)

    sys.stdout.write(out)
    if not out.endswith("\n"):
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
