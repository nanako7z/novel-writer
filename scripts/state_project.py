#!/usr/bin/env python3
"""State projections (precomputed cache-style views over truth files).

Stdlib-only. Composer (or any reader) can ask for compressed views instead of
re-reading raw truth files when context budget is tight. Projections are
*derivations* — never edit truth files, never written to disk; recomputed on
every call.

Views supported:

  characters-in-scene      Per-character roll over the last N chapters.
  hooks-grouped            Hooks split into mainLine / subPlots / orphans.
  emotional-trajectories   Per-character (chapter, mood, intensity) tuples.
  subplot-threads          Each subplot with its activity log.

Usage:
    python state_project.py --book <bookDir> --current-chapter N \
        --view characters-in-scene|hooks-grouped|emotional-trajectories|subplot-threads \
        [--window 10] [--json|--markdown]

Output:
    JSON object on stdout (default) or markdown when --markdown.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

WINDOW_DEFAULT = 10
VIEWS = (
    "characters-in-scene",
    "hooks-grouped",
    "emotional-trajectories",
    "subplot-threads",
)


def load_json(p: Path, default: Any) -> Any:
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def read_text(p: Path) -> str:
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def split_terms(s: str) -> list[str]:
    if not s:
        return []
    parts = re.split(r"[,、，;；]+", s)
    return [t.strip() for t in parts if t.strip()]


# ───────────────── characters-in-scene ─────────────────


def view_characters_in_scene(
    summaries: list[dict[str, Any]],
    current_chapter: int,
    window: int,
) -> dict[str, Any]:
    eligible = [
        s for s in summaries
        if int(s.get("chapter", 0) or 0) < current_chapter
    ]
    eligible.sort(key=lambda s: int(s.get("chapter", 0) or 0))
    recent = eligible[-window:] if window > 0 else eligible

    per_char: dict[str, dict[str, Any]] = {}
    for s in recent:
        ch = int(s.get("chapter", 0) or 0)
        mood = str(s.get("mood", "") or "").strip()
        for c in split_terms(str(s.get("characters", ""))):
            slot = per_char.setdefault(c, {
                "character": c,
                "chaptersAppeared": [],
                "lastChapter": ch,
                "moodSamples": [],
            })
            slot["chaptersAppeared"].append(ch)
            slot["lastChapter"] = max(slot["lastChapter"], ch)
            if mood:
                slot["moodSamples"].append(mood)

    # Compute dominantMood + clean up moodSamples scratch field.
    out_rows: list[dict[str, Any]] = []
    for slot in per_char.values():
        moods = slot.pop("moodSamples")
        dominant = ""
        if moods:
            counts = Counter(moods)
            dominant = counts.most_common(1)[0][0]
        slot["dominantMood"] = dominant
        slot["appearanceCount"] = len(slot["chaptersAppeared"])
        out_rows.append(slot)
    out_rows.sort(key=lambda r: (-r["appearanceCount"], -r["lastChapter"], r["character"]))

    return {
        "view": "characters-in-scene",
        "currentChapter": current_chapter,
        "window": window,
        "windowChapterRange": (
            [recent[0].get("chapter"), recent[-1].get("chapter")]
            if recent else []
        ),
        "characters": out_rows,
    }


# ───────────────── hooks-grouped ─────────────────


def view_hooks_grouped(hooks: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Grouping rules (heuristic, conservative):
      - mainLine: hook.coreHook == True  OR  hook.tags contains "main"
      - subPlots: hook.subplotId set OR hook.tags contains "sub" OR has dependsOn
      - orphans: everything else (no upstream, no subplot, no main flag)
    A hook can land in mainLine OR subPlots OR orphans (not both); we apply the
    rules in that priority order.
    """
    main_line: list[dict[str, Any]] = []
    sub_plots: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []

    by_subplot: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for h in hooks:
        tags = h.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        tag_strs = [str(t).lower() for t in tags]
        is_core = bool(h.get("coreHook"))
        has_main_tag = any("main" in t or "主线" in t for t in tag_strs)
        subplot_id = str(h.get("subplotId", "") or "").strip()
        depends_on = h.get("dependsOn") or []
        if not isinstance(depends_on, list):
            depends_on = []
        has_sub_tag = any("sub" in t or "支线" in t for t in tag_strs)

        slim = {
            "hookId": h.get("hookId"),
            "type": h.get("type"),
            "status": h.get("status"),
            "startChapter": h.get("startChapter"),
            "lastAdvancedChapter": h.get("lastAdvancedChapter"),
            "expectedPayoff": h.get("expectedPayoff"),
            "subplotId": subplot_id or None,
            "dependsOn": list(depends_on),
            "tags": list(tags),
            "coreHook": is_core,
        }

        if is_core or has_main_tag:
            main_line.append(slim)
        elif subplot_id or has_sub_tag or depends_on:
            sub_plots.append(slim)
            if subplot_id:
                by_subplot[subplot_id].append(slim)
        else:
            orphans.append(slim)

    # Sort each bucket by lastAdvanced descending so the "freshest" hook leads.
    for bucket in (main_line, sub_plots, orphans):
        bucket.sort(
            key=lambda h: (
                -int(h.get("lastAdvancedChapter") or 0),
                str(h.get("hookId") or ""),
            )
        )

    return {
        "view": "hooks-grouped",
        "mainLine": main_line,
        "subPlots": sub_plots,
        "orphans": orphans,
        "subplotIndex": {
            sid: [h["hookId"] for h in lst]
            for sid, lst in by_subplot.items()
        },
        "totals": {
            "mainLine": len(main_line),
            "subPlots": len(sub_plots),
            "orphans": len(orphans),
        },
    }


# ───────────────── emotional-trajectories ─────────────────


# Markdown table parser for emotional_arcs.md:
# | Character | Chapter | Emotional State | Trigger Event | Intensity | Arc Direction |
def parse_emotional_arcs_md(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 6:
            continue
        # Skip header / separator rows.
        if cells[0].lower() in {"character", "角色"}:
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        try:
            chapter = int(re.sub(r"\D", "", cells[1]) or "0")
        except ValueError:
            chapter = 0
        try:
            intensity = int(re.sub(r"\D", "", cells[4]) or "0")
        except ValueError:
            intensity = 0
        rows.append({
            "character": cells[0],
            "chapter": chapter,
            "mood": cells[2],
            "trigger": cells[3],
            "intensity": intensity,
            "direction": cells[5] if len(cells) > 5 else "",
        })
    return rows


def view_emotional_trajectories(
    book_dir: Path,
    summaries: list[dict[str, Any]],
    current_chapter: int,
    window: int,
) -> dict[str, Any]:
    arcs_raw = read_text(book_dir / "story" / "emotional_arcs.md")
    arcs_rows = parse_emotional_arcs_md(arcs_raw) if arcs_raw.strip() else []

    per_char: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source = "emotional_arcs.md"

    if arcs_rows:
        for r in arcs_rows:
            if 0 < r["chapter"] < current_chapter:
                per_char[r["character"]].append({
                    "chapter": r["chapter"],
                    "mood": r["mood"],
                    "intensity": r["intensity"],
                    "direction": r["direction"],
                    "trigger": r["trigger"],
                })
    else:
        # Fall back to chapter_summaries.json#mood field, infer per character.
        source = "chapter_summaries.json#mood (inferred)"
        eligible = [
            s for s in summaries
            if int(s.get("chapter", 0) or 0) < current_chapter
        ]
        eligible.sort(key=lambda s: int(s.get("chapter", 0) or 0))
        windowed = eligible[-window:] if window > 0 else eligible
        for s in windowed:
            ch = int(s.get("chapter", 0) or 0)
            mood = str(s.get("mood", "") or "").strip()
            for c in split_terms(str(s.get("characters", ""))):
                per_char[c].append({
                    "chapter": ch,
                    "mood": mood,
                    "intensity": None,
                    "direction": "",
                    "trigger": "",
                })

    # Trim each character's track to the window if there are too many.
    out: list[dict[str, Any]] = []
    for char, track in per_char.items():
        track.sort(key=lambda r: r["chapter"])
        if window > 0:
            track = [r for r in track if r["chapter"] >= current_chapter - window]
        if not track:
            continue
        out.append({
            "character": char,
            "trajectory": track,
            "samples": len(track),
        })
    out.sort(key=lambda r: (-r["samples"], r["character"]))

    return {
        "view": "emotional-trajectories",
        "currentChapter": current_chapter,
        "window": window,
        "source": source,
        "characters": out,
    }


# ───────────────── subplot-threads ─────────────────


# Markdown table parser for subplot_board.md:
# | subplotId | name | status | lastAdvancedChapter | characters | notes |
def parse_subplot_board_md(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 6:
            continue
        if cells[0].lower() in {"subplotid", "subplot id"}:
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue
        try:
            last_adv = int(re.sub(r"\D", "", cells[3]) or "0")
        except ValueError:
            last_adv = 0
        rows.append({
            "subplotId": cells[0],
            "name": cells[1],
            "status": cells[2],
            "lastAdvancedChapter": last_adv,
            "characters": cells[4],
            "notes": cells[5] if len(cells) > 5 else "",
        })
    return rows


def view_subplot_threads(
    book_dir: Path,
    summaries: list[dict[str, Any]],
    hooks: list[dict[str, Any]],
    current_chapter: int,
) -> dict[str, Any]:
    raw = read_text(book_dir / "story" / "subplot_board.md")
    subplots = parse_subplot_board_md(raw)

    # Build activity log per subplot from chapter_summaries (if events / hookActivity
    # mentions the subplot id or name) and from hooks (which subplot do they belong to).
    threads: list[dict[str, Any]] = []
    for sp in subplots:
        sid = sp["subplotId"]
        name = sp["name"]
        activity: list[dict[str, Any]] = []
        for s in summaries:
            ch = int(s.get("chapter", 0) or 0)
            if ch >= current_chapter:
                continue
            blob = " ".join(
                str(s.get(k, ""))
                for k in ("events", "hookActivity", "stateChanges", "title")
            )
            if (sid and sid in blob) or (name and name and len(name) >= 2 and name in blob):
                activity.append({
                    "chapter": ch,
                    "title": s.get("title", ""),
                    "events": s.get("events", ""),
                })
        related_hooks = [
            {"hookId": h.get("hookId"), "status": h.get("status"),
             "lastAdvancedChapter": h.get("lastAdvancedChapter")}
            for h in hooks
            if str(h.get("subplotId", "") or "").strip() == sid
        ]
        threads.append({
            "subplotId": sid,
            "name": name,
            "status": sp.get("status", ""),
            "lastAdvancedChapter": sp.get("lastAdvancedChapter", 0),
            "characters": sp.get("characters", ""),
            "notes": sp.get("notes", ""),
            "activity": activity,
            "relatedHooks": related_hooks,
        })

    threads.sort(key=lambda t: (
        0 if str(t.get("status", "")).lower() == "active" else 1,
        -int(t.get("lastAdvancedChapter") or 0),
        str(t.get("subplotId", "")),
    ))

    return {
        "view": "subplot-threads",
        "currentChapter": current_chapter,
        "threads": threads,
        "totals": {
            "subplots": len(threads),
            "active": sum(1 for t in threads if str(t.get("status", "")).lower() == "active"),
        },
    }


# ───────────────── markdown rendering ─────────────────


def render_markdown(payload: dict[str, Any]) -> str:
    view = payload.get("view")
    if view == "characters-in-scene":
        lines = [f"# Characters in scene (last {payload['window']} chapters)", ""]
        if not payload["characters"]:
            lines.append("_no characters recorded in window_")
            return "\n".join(lines) + "\n"
        lines.append("| character | appearances | last chapter | dominant mood | chapters |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r in payload["characters"]:
            lines.append(
                f"| {r['character']} | {r['appearanceCount']} | {r['lastChapter']} "
                f"| {r['dominantMood']} | {r['chaptersAppeared']} |"
            )
        return "\n".join(lines) + "\n"
    if view == "hooks-grouped":
        lines = ["# Hooks grouped", ""]
        for bucket in ("mainLine", "subPlots", "orphans"):
            lines.append(f"## {bucket} ({len(payload[bucket])})")
            for h in payload[bucket]:
                lines.append(
                    f"- `{h['hookId']}` [{h['type']}/{h['status']}] last={h['lastAdvancedChapter']} "
                    f"→ {h['expectedPayoff']}"
                    + (f" (subplot: {h['subplotId']})" if h.get("subplotId") else "")
                    + (f" deps={h['dependsOn']}" if h.get("dependsOn") else "")
                )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
    if view == "emotional-trajectories":
        lines = [f"# Emotional trajectories (source: {payload['source']})", ""]
        if not payload["characters"]:
            lines.append("_no emotional samples_")
            return "\n".join(lines) + "\n"
        for c in payload["characters"]:
            lines.append(f"## {c['character']} ({c['samples']} samples)")
            for r in c["trajectory"]:
                inten = "" if r["intensity"] is None else f" int={r['intensity']}"
                direc = f" [{r['direction']}]" if r.get("direction") else ""
                trig = f" — {r['trigger']}" if r.get("trigger") else ""
                lines.append(f"- ch{r['chapter']}: {r['mood']}{inten}{direc}{trig}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
    if view == "subplot-threads":
        lines = ["# Subplot threads", ""]
        if not payload["threads"]:
            lines.append("_no subplots recorded_")
            return "\n".join(lines) + "\n"
        for t in payload["threads"]:
            lines.append(
                f"## {t['subplotId']} — {t['name']} [{t['status']}, last "
                f"adv ch{t['lastAdvancedChapter']}]"
            )
            if t["characters"]:
                lines.append(f"_characters: {t['characters']}_")
            if t["notes"]:
                lines.append(f"_notes: {t['notes']}_")
            if t["relatedHooks"]:
                lines.append("- related hooks: "
                             + ", ".join(f"`{h['hookId']}`({h['status']})"
                                          for h in t["relatedHooks"]))
            for a in t["activity"]:
                lines.append(f"- ch{a['chapter']} {a['title']}: {a['events']}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


# ───────────────── driver ─────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compute compressed views over the truth files (no writes).",
    )
    p.add_argument("--book", required=True, help="book directory (containing story/)")
    p.add_argument("--current-chapter", required=True, type=int,
                   help="reference chapter; views look strictly before this")
    p.add_argument("--view", required=True, choices=VIEWS,
                   help="which projection to compute")
    p.add_argument("--window", type=int, default=WINDOW_DEFAULT,
                   help=f"lookback window in chapters (default {WINDOW_DEFAULT})")
    out = p.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true", help="emit JSON (default)")
    out.add_argument("--markdown", action="store_true", help="emit markdown digest")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    book = Path(args.book).resolve()
    if not book.is_dir():
        print(json.dumps({"error": f"book dir not found: {book}"}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    if args.current_chapter < 1:
        print(json.dumps({"error": "--current-chapter must be >= 1"}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    if args.window < 1:
        print(json.dumps({"error": "--window must be >= 1"}, ensure_ascii=False),
              file=sys.stderr)
        return 1

    summaries_obj = load_json(book / "story" / "state" / "chapter_summaries.json",
                              {"rows": []})
    # inkos `rows` / legacy SKILL `summaries` — read both.
    summaries = (summaries_obj.get("rows", summaries_obj.get("summaries", []))
                 if isinstance(summaries_obj, dict) else [])
    hooks_obj = load_json(book / "story" / "state" / "hooks.json", {"hooks": []})
    hooks = hooks_obj.get("hooks", []) if isinstance(hooks_obj, dict) else []

    if args.view == "characters-in-scene":
        payload = view_characters_in_scene(summaries, args.current_chapter, args.window)
    elif args.view == "hooks-grouped":
        payload = view_hooks_grouped(hooks)
    elif args.view == "emotional-trajectories":
        payload = view_emotional_trajectories(book, summaries, args.current_chapter, args.window)
    elif args.view == "subplot-threads":
        payload = view_subplot_threads(book, summaries, hooks, args.current_chapter)
    else:  # pragma: no cover — argparse choices guards
        print(json.dumps({"error": f"unknown view: {args.view}"}, ensure_ascii=False),
              file=sys.stderr)
        return 1

    if args.markdown:
        sys.stdout.write(render_markdown(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
