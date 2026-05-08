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
    [--memo <chapter_memo.md>] \
    [--window-recent 6] [--window-relevant 8] \
    [--include-resolved-hooks] [--scan-volume-summaries] \
    [--format json|markdown]

`--memo` reads YAML frontmatter flags from a chapter_memo file and adjusts
window defaults accordingly (gap item #3). Precedence: explicit CLI flags
> memo flags > defaults.

  isGoldenOpening : true → window-recent=2, window-relevant=0
  cliffResolution : true → --include-resolved-hooks (auto)
  arcTransition   : true → window-relevant=12, --scan-volume-summaries (auto)
  volumeFinale    : true → window-relevant=0 (only this volume's recent)

`--scan-volume-summaries` adds a 7th selection pass over `story/volume_summaries.md`
(written by the Consolidator). It returns volume-level paragraphs whose text
substring-matches any anchor term (characters in recent window + active hookIds).
Useful past 30+ chapters where active hooks may want to reference an event
already pushed into archived volume summaries — pure-lastN substring retrieval
otherwise misses cross-volume callbacks.
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


def parse_memo_flags(memo_path: Path) -> dict[str, bool]:
    """Parse chapter_memo YAML frontmatter for the 5 boolean flags.

    Returns a dict with each known flag (defaulting False). Tolerant of:
      * missing file (returns all-False)
      * missing frontmatter (returns all-False)
      * unknown keys (ignored)
      * "true" / "True" / "yes" / "false" / "False" / "no"
    """
    flags = {
        "isGoldenOpening": False,
        "cliffResolution": False,
        "arcTransition": False,
        "volumeFinale": False,
        "isReshootChapter": False,
    }
    if not memo_path.is_file():
        return flags
    try:
        raw = memo_path.read_text(encoding="utf-8")
    except OSError:
        return flags
    if not raw.startswith("---"):
        return flags
    body = raw[3:]
    end = body.find("\n---")
    if end < 0:
        return flags
    block = body[:end]
    for line in block.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip().strip('"').strip("'").lower()
        if key in flags:
            flags[key] = val in {"true", "yes", "1"}
    return flags


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


# ─────────────────── Volume summaries (markdown sections) ─────


_VOLUME_HEADING_RE = re.compile(
    r"^##\s+(?P<name>.+?)\s*\(Ch\.?\s*(?P<start>\d+)\s*-\s*(?P<end>\d+)\)\s*$",
    re.IGNORECASE,
)


def parse_volume_summaries(md: str) -> list[dict]:
    """Parse `story/volume_summaries.md` into a list of volume entries.

    Format produced by Consolidator (see references/phases/12-consolidator.md §3d):
        ## <name> (Ch.X-Y)

        <paragraph...>

    Returns: [{name, startCh, endCh, paragraph}]. Tolerant of extra blank
    lines and minor heading drift. Lines outside any heading are dropped.
    """
    entries: list[dict] = []
    cur: dict | None = None
    buf: list[str] = []

    def _flush() -> None:
        if cur is None:
            return
        cur["paragraph"] = "\n".join(buf).strip()
        entries.append(cur)

    for line in md.splitlines():
        m = _VOLUME_HEADING_RE.match(line.strip())
        if m:
            _flush()
            cur = {
                "name": m.group("name").strip(),
                "startCh": int(m.group("start")),
                "endCh": int(m.group("end")),
            }
            buf = []
        else:
            if cur is not None:
                buf.append(line)
    _flush()
    return entries


def select_relevant_volume_summaries(
    volumes: list[dict],
    current_chapter: int,
    anchor_terms: set[str],
) -> list[dict]:
    """Volume summaries whose paragraph or name substring-matches any anchor term.

    Only volumes that have already closed strictly before the current chapter
    are considered (endCh < current_chapter). No ranking — returns all matches
    in chronological order. Caller decides how to truncate.
    """
    if not anchor_terms:
        return []
    out: list[dict] = []
    for v in volumes:
        if int(v.get("endCh", 0) or 0) >= current_chapter:
            continue
        blob = (str(v.get("name", "")) + " " + str(v.get("paragraph", ""))).lower()
        if any(t in blob for t in anchor_terms if t):
            out.append({
                "name": v.get("name", ""),
                "startCh": v.get("startCh"),
                "endCh": v.get("endCh"),
                "paragraph": v.get("paragraph", ""),
            })
    out.sort(key=lambda v: int(v.get("startCh", 0) or 0))
    return out


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
        f"vols={stats.get('relevantVolumeCount', 0)} "
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

    if payload.get("relevantVolumeSummaries"):
        lines.append("## Cross-volume callbacks (matched anchors in volume_summaries.md)")
        for v in payload["relevantVolumeSummaries"]:
            name = v.get("name", "")
            sc = v.get("startCh")
            ec = v.get("endCh")
            para = (v.get("paragraph") or "").strip()
            lines.append(f"- **{name}** (Ch.{sc}-{ec}): {para}")
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
    p.add_argument(
        "--memo", type=Path, default=None,
        help=("optional chapter_memo.md path; its YAML frontmatter flags "
              "(isGoldenOpening / cliffResolution / arcTransition / volumeFinale) "
              "auto-adjust window defaults. Explicit CLI flags still win."),
    )
    p.add_argument("--window-recent", type=int, default=None,
                   help="how many recent summaries to include in full (default 6, or memo-flag-driven)")
    p.add_argument("--window-relevant", type=int, default=None,
                   help="max deeper-history summaries to include (default 8, or memo-flag-driven)")
    p.add_argument(
        "--include-resolved-hooks", action="store_true", default=None,
        help="also include hooks resolved in the last 3 chapters (auto-set if memo cliffResolution=true)",
    )
    p.add_argument(
        "--scan-volume-summaries", action="store_true", default=None,
        help=("scan story/volume_summaries.md for cross-volume callbacks "
              "(auto-set if memo arcTransition=true)"),
    )
    p.add_argument("--format", choices=["json", "markdown"], default="json")
    return p.parse_args()


def resolve_windows(
    cli_recent: int | None,
    cli_relevant: int | None,
    cli_resolved: bool | None,
    cli_scan_volumes: bool | None,
    memo_flags: dict[str, bool],
) -> tuple[int, int, bool, bool, list[str]]:
    """Compute final (window_recent, window_relevant, include_resolved,
    scan_volume_summaries, applied).

    Precedence: CLI > memo > default.
    `applied` is a list of human-readable trace strings (for stats) noting
    which memo flag(s) shaped the defaults.
    """
    default_recent = 6
    default_relevant = 8
    default_resolved = False
    default_scan_volumes = False

    # Start with defaults
    recent = default_recent
    relevant = default_relevant
    resolved = default_resolved
    scan_volumes = default_scan_volumes
    applied: list[str] = []

    # Apply memo flags (lower precedence than CLI).
    # cliffResolution: hooks resolved injection + 6/12 window
    if memo_flags.get("cliffResolution"):
        recent = 6
        relevant = 12
        resolved = True
        applied.append("cliffResolution → recent=6 relevant=12 +include-resolved-hooks")
    # isGoldenOpening: tightest window (overrides cliff if both true — golden wins)
    if memo_flags.get("isGoldenOpening"):
        recent = 2
        relevant = 0
        applied.append("isGoldenOpening → recent=2 relevant=0")
    # arcTransition: widen relevant + scan archived volume summaries
    if memo_flags.get("arcTransition") and not memo_flags.get("isGoldenOpening"):
        relevant = max(relevant, 12)
        scan_volumes = True
        applied.append("arcTransition → relevant=12 +scan-volume-summaries")
    # volumeFinale: zero out relevant (focus only on this volume)
    if memo_flags.get("volumeFinale"):
        relevant = 0
        applied.append("volumeFinale → relevant=0")

    # CLI overrides everything explicit
    if cli_recent is not None:
        recent = cli_recent
    if cli_relevant is not None:
        relevant = cli_relevant
    if cli_resolved is not None:
        resolved = bool(cli_resolved)
    if cli_scan_volumes is not None:
        scan_volumes = bool(cli_scan_volumes)

    return recent, relevant, resolved, scan_volumes, applied


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
    summaries_obj = load_json(state / "chapter_summaries.json", {"rows": []})
    hooks_obj = load_json(state / "hooks.json", {"hooks": []})
    current_state = load_json(state / "current_state.json", {"chapter": 0, "facts": []})

    # inkos `rows` / legacy SKILL `summaries` — read both.
    summaries = (summaries_obj.get("rows", summaries_obj.get("summaries", []))
                 if isinstance(summaries_obj, dict) else [])
    hooks = hooks_obj.get("hooks", []) if isinstance(hooks_obj, dict) else []

    # Resolve memo-derived window defaults (CLI > memo > default).
    memo_flags = parse_memo_flags(args.memo) if args.memo else {
        "isGoldenOpening": False, "cliffResolution": False,
        "arcTransition": False, "volumeFinale": False, "isReshootChapter": False,
    }
    window_recent, window_relevant, include_resolved, scan_volume_summaries, memo_trace = resolve_windows(
        args.window_recent, args.window_relevant,
        args.include_resolved_hooks, args.scan_volume_summaries,
        memo_flags,
    )

    # 1. Recent window — full content
    recent = select_recent_summaries(summaries, args.current_chapter, window_recent)
    recent_cutoff = args.current_chapter - window_recent

    # 2. Active hooks within the chapter window
    active_hooks = select_active_hooks(hooks, args.current_chapter)

    # 3. Anchor terms = characters in recent window + active hookIds
    anchor_terms = collect_anchor_terms(recent, active_hooks)

    # 4. Relevant deeper-history summaries (events-only)
    relevant = select_relevant_summaries(
        summaries, args.current_chapter, recent_cutoff, anchor_terms, window_relevant,
    )

    # 5. Recently resolved hooks (optional)
    recently_resolved = (
        select_recently_resolved_hooks(hooks, args.current_chapter)
        if include_resolved else []
    )

    # 6. Character roster — characters mentioned in recent + relevant summaries
    chars_in_window: set[str] = set()
    for s in recent:
        chars_in_window.update(split_terms(str(s.get("characters", ""))))
    matrix_md = read_text(book / "story" / "character_matrix.md")
    matrix_rows = parse_character_matrix(matrix_md)
    roster = select_character_roster(matrix_rows, chars_in_window)

    # 7. (optional) Cross-volume callbacks via volume_summaries.md substring scan
    relevant_volumes: list[dict] = []
    if scan_volume_summaries:
        vol_md = read_text(book / "story" / "volume_summaries.md")
        if vol_md:
            volumes = parse_volume_summaries(vol_md)
            relevant_volumes = select_relevant_volume_summaries(
                volumes, args.current_chapter, anchor_terms,
            )

    payload = {
        "currentChapter": args.current_chapter,
        "recentSummaries": recent,
        "relevantSummaries": relevant,
        "activeHooks": active_hooks,
        "recentlyResolvedHooks": recently_resolved,
        "characterRoster": roster,
        "relevantVolumeSummaries": relevant_volumes,
        "currentState": current_state if isinstance(current_state, dict) else {},
        "stats": {
            "recentCount": len(recent),
            "relevantCount": len(relevant),
            "activeHookCount": len(active_hooks),
            "relevantVolumeCount": len(relevant_volumes),
            "totalChars": 0,  # filled in below
            "windowRecent": window_recent,
            "windowRelevant": window_relevant,
            "includeResolvedHooks": include_resolved,
            "scanVolumeSummaries": scan_volume_summaries,
            "memoFlags": memo_flags,
            "memoFlagApplied": memo_trace,
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
