#!/usr/bin/env python3
"""Cadence policy probe (Phase 02 — Planner pre-input).

Stdlib-only. Reads the genre profile + recent chapter_summaries + volume_map
and reports cadence pressure: how long since the last "satisfaction" beat,
which chapter types the upcoming chapter SHOULD be, and where we sit inside
the current volume's beat structure.

Planner is meant to consume this *before* writing the chapter_memo so that
it can rebalance pacing / hook payoff / volume mid-point pressure proactively.

Usage:
    python cadence_check.py --book <bookDir> --current-chapter N [--json]

Output (always JSON to stdout when --json; human + JSON tail otherwise):
    {
      "currentChapter": 12,
      "currentVolume": {"index": 1, "name": "...", "startCh": 1, "endCh": 30},
      "chaptersSinceSatisfaction": 6,
      "satisfactionPressure": "high",
      "recommendedChapterTypes": ["战斗章", "悟道章"],
      "pacingNotes": ["..."],
      "volumeBeatStatus": "approaching mid-point (ch 12 of 30)",
      "lastSatisfactionChapter": 6,
      "satisfactionTypes": [...],
      "chapterTypes": [...],
      "pacingRule": "...",
      "lookbackChapters": [...]
    }

Exit codes:
    0 — diagnosis successful
    1 — usage / IO error
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

LOOKBACK_DEFAULT = 20

# Cadence pressure thresholds: how many chapters without a satisfaction beat
# before we tell Planner to fix it.
PRESSURE_HIGH = 5    # "now or never"
PRESSURE_MED = 3     # "should plan one within 1-2 chapters"

# Mid-point window: |progress - 0.5| <= MID_POINT_BAND triggers the warning.
MID_POINT_BAND = 0.10
CLIMAX_BAND = 0.85   # progress >= this → expect the volume climax

VOLUME_HEADER_RE = re.compile(
    r"^(第[一二三四五六七八九十百千万零〇\d]+卷|Volume\s+\d+)",
    re.IGNORECASE,
)
RANGE_RE = re.compile(
    r"[（(]\s*(?:第|[Cc]hapters?\s+)?(\d+)\s*[-–~～—]\s*(\d+)\s*(?:章)?\s*[）)]"
    r"|(?:第|[Cc]hapters?\s+)(\d+)\s*[-–~～—]\s*(\d+)\s*(?:章)?",
    re.IGNORECASE,
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


def parse_genre_profile(book_dir: Path, skill_root: Path) -> dict[str, Any]:
    """Read templates/genres/<genre>.md (or fall back to other.md)."""
    book_json = load_json(book_dir / "book.json", {}) or {}
    genre_id = str(book_json.get("genre", "") or "").strip().lower() or "other"
    candidates = [
        book_dir / "genres" / f"{genre_id}.md",
        skill_root / "templates" / "genres" / f"{genre_id}.md",
        skill_root / "templates" / "genres" / "other.md",
    ]
    raw = ""
    used = ""
    for c in candidates:
        if c.is_file():
            raw = read_text(c)
            used = str(c)
            break
    fm = parse_yaml_frontmatter(raw)
    return {
        "_source": used,
        "_genreId": genre_id,
        "chapterTypes": fm.get("chapterTypes") or [],
        "satisfactionTypes": fm.get("satisfactionTypes") or [],
        "pacingRule": fm.get("pacingRule") or "",
    }


def parse_yaml_frontmatter(raw: str) -> dict[str, Any]:
    """Tiny YAML reader for the few fields we need (lists + scalars)."""
    if not raw.startswith("---"):
        return {}
    body = raw[3:]
    end = body.find("\n---")
    if end < 0:
        return {}
    block = body[:end]
    out: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1]
            items = [
                t.strip().strip('"').strip("'")
                for t in re.split(r",", inner)
                if t.strip()
            ]
            out[key] = items
        else:
            v = val.strip().strip('"').strip("'")
            if v.lower() == "true":
                out[key] = True
            elif v.lower() == "false":
                out[key] = False
            else:
                out[key] = v
    return out


def parse_volume_map(raw: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in raw.split("\n"):
        stripped = re.sub(r"^#+\s*", "", line).strip()
        if not VOLUME_HEADER_RE.match(stripped):
            continue
        m = RANGE_RE.search(stripped)
        if not m:
            continue
        start = int(m.group(1) or m.group(3) or 0)
        end = int(m.group(2) or m.group(4) or 0)
        if start <= 0 or end <= 0 or end < start:
            continue
        name = stripped[: m.start()].rstrip("（( ").strip()
        out.append({"name": name, "startCh": start, "endCh": end})
    return out


def find_current_volume(
    volumes: list[dict[str, Any]],
    chapter: int,
) -> tuple[int, dict[str, Any] | None]:
    for idx, vol in enumerate(volumes):
        if vol["startCh"] <= chapter <= vol["endCh"]:
            return idx + 1, vol
    return 0, None


def lookback_summaries(
    summaries: list[dict[str, Any]],
    current_chapter: int,
    n: int,
) -> list[dict[str, Any]]:
    eligible = [
        s for s in summaries
        if int(s.get("chapter", 0) or 0) < current_chapter
    ]
    eligible.sort(key=lambda s: int(s.get("chapter", 0) or 0))
    return eligible[-n:] if n > 0 else []


def matches_satisfaction(row: dict[str, Any], satisfaction_types: list[str]) -> bool:
    if not satisfaction_types:
        return False
    blob = " ".join(
        str(row.get(k, ""))
        for k in ("chapterType", "mood", "events", "title")
    ).lower()
    return any(t.strip().lower() in blob for t in satisfaction_types if t.strip())


def chapters_since_satisfaction(
    rows: list[dict[str, Any]],
    current_chapter: int,
    satisfaction_types: list[str],
) -> tuple[int, int | None]:
    """Return (gap, lastSatChapter or None)."""
    last_sat: int | None = None
    for s in sorted(rows, key=lambda r: int(r.get("chapter", 0) or 0)):
        if matches_satisfaction(s, satisfaction_types):
            ch = int(s.get("chapter", 0) or 0)
            if ch < current_chapter:
                last_sat = ch
    if last_sat is None:
        # Never seen a satisfaction beat in the window — count from start.
        if rows:
            first_ch = min(int(r.get("chapter", 0) or 0) for r in rows)
            return max(0, current_chapter - first_ch), None
        return 0, None
    return current_chapter - last_sat, last_sat


def classify_pressure(gap: int) -> str:
    if gap >= PRESSURE_HIGH:
        return "high"
    if gap >= PRESSURE_MED:
        return "medium"
    return "low"


def recent_types_distribution(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in rows:
        t = str(s.get("chapterType", "") or "").strip()
        if not t:
            continue
        counts[t] = counts.get(t, 0) + 1
    return counts


def recommend_chapter_types(
    chapter_types: list[str],
    satisfaction_types: list[str],
    last_types: dict[str, int],
    pressure: str,
) -> list[str]:
    if not chapter_types:
        return []
    # Heuristic: under high pressure prefer types that match satisfaction
    # types (substring overlap). Always avoid the most-recent dominant type.
    last_dominant = max(last_types.items(), key=lambda kv: kv[1])[0] if last_types else ""
    candidates = [t for t in chapter_types if t != last_dominant]
    if pressure in {"high", "medium"} and satisfaction_types:
        scored: list[tuple[int, str]] = []
        for t in candidates:
            score = 0
            for st in satisfaction_types:
                if st and (st in t or t in st):
                    score += 2
            scored.append((score, t))
        scored.sort(key=lambda x: (-x[0], x[1]))
        ranked = [t for _, t in scored]
        # Trim: keep top 3 distinct types.
        return ranked[:3] if ranked else candidates[:3]
    # Low pressure: keep mix; suggest 2-3 underused types.
    underused = sorted(candidates, key=lambda t: last_types.get(t, 0))
    return underused[:3]


def volume_beat_status(volume: dict[str, Any] | None, chapter: int) -> str:
    if not volume:
        return "no current volume mapped"
    span = max(1, volume["endCh"] - volume["startCh"])
    progress = (chapter - volume["startCh"]) / span
    mid = volume["startCh"] + span // 2
    if progress < 0:
        return f"before volume {volume['name']} starts (ch {volume['startCh']})"
    if abs(progress - 0.5) <= MID_POINT_BAND:
        return f"approaching mid-point (ch {chapter} of {volume['startCh']}-{volume['endCh']})"
    if progress < 0.5:
        return f"early-volume ({volume['startCh']}-{mid} band)"
    if progress >= 1.0:
        return f"past volume end (ch {chapter} > endCh {volume['endCh']})"
    if progress >= CLIMAX_BAND:
        return f"climax window (last 15% of {volume['name']})"
    return f"late-volume buildup (ch {chapter} of {volume['startCh']}-{volume['endCh']})"


def build_pacing_notes(
    pressure: str,
    gap: int,
    pacing_rule: str,
    types_dist: dict[str, int],
) -> list[str]:
    notes: list[str] = []
    if pacing_rule:
        notes.append(f"genre pacingRule: {pacing_rule}")
    if pressure == "high":
        notes.append(
            f"satisfaction gap = {gap} chapters → high pressure. "
            f"Plan a payoff / 爽点 in the upcoming chapter."
        )
    elif pressure == "medium":
        notes.append(
            f"satisfaction gap = {gap} chapters → medium pressure. "
            f"Plan a payoff within the next 1-2 chapters."
        )
    if types_dist:
        dominant = max(types_dist.items(), key=lambda kv: kv[1])
        if dominant[1] >= 3:
            notes.append(
                f"recent chapterType dominant: '{dominant[0]}' "
                f"({dominant[1]}x) — vary the next chapter type to avoid monotony."
            )
    transitional_count = sum(
        v for k, v in types_dist.items()
        if "过渡" in k or "transition" in k.lower() or "日常" in k
    )
    if transitional_count >= 5:
        notes.append(
            f"{transitional_count} of last {sum(types_dist.values())} chapters "
            "were transitional / daily — risk of stalling, prefer an action / payoff beat."
        )
    return notes


def diagnose(
    book_dir: Path,
    skill_root: Path,
    current_chapter: int,
    lookback: int,
) -> dict[str, Any]:
    profile = parse_genre_profile(book_dir, skill_root)
    summaries_obj = load_json(
        book_dir / "story" / "state" / "chapter_summaries.json",
        {"summaries": []},
    )
    summaries = summaries_obj.get("summaries", []) if isinstance(summaries_obj, dict) else []

    volume_map_path = book_dir / "story" / "outline" / "volume_map.md"
    volume_raw = read_text(volume_map_path)
    if not volume_raw.strip():
        volume_raw = read_text(book_dir / "story" / "volume_outline.md")
    volumes = parse_volume_map(volume_raw)
    vol_idx, current_vol = find_current_volume(volumes, current_chapter)

    rows = lookback_summaries(summaries, current_chapter, lookback)

    sat_types = profile.get("satisfactionTypes") or []
    chap_types = profile.get("chapterTypes") or []
    pacing_rule = profile.get("pacingRule") or ""

    gap, last_sat = chapters_since_satisfaction(rows, current_chapter, sat_types)
    pressure = classify_pressure(gap)

    # If there are no rows at all, satisfaction concept does not apply yet.
    if not rows:
        gap = 0
        pressure = "low"
        last_sat = None

    types_dist = recent_types_distribution(rows)
    recs = recommend_chapter_types(chap_types, sat_types, types_dist, pressure)

    notes = build_pacing_notes(pressure, gap, pacing_rule, types_dist)
    vol_status = volume_beat_status(current_vol, current_chapter)

    return {
        "currentChapter": current_chapter,
        "currentVolume": (
            {
                "index": vol_idx,
                "name": current_vol.get("name", ""),
                "startCh": current_vol.get("startCh"),
                "endCh": current_vol.get("endCh"),
            }
            if current_vol
            else None
        ),
        "chaptersSinceSatisfaction": gap,
        "satisfactionPressure": pressure,
        "lastSatisfactionChapter": last_sat,
        "recommendedChapterTypes": recs,
        "pacingNotes": notes,
        "volumeBeatStatus": vol_status,
        "satisfactionTypes": sat_types,
        "chapterTypes": chap_types,
        "pacingRule": pacing_rule,
        "lookbackChapters": [int(s.get("chapter", 0) or 0) for s in rows],
        "_genreProfileSource": profile.get("_source", ""),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Probe pacing / cadence pressure for the upcoming chapter.",
    )
    p.add_argument("--book", required=True, help="book directory (containing story/)")
    p.add_argument(
        "--current-chapter", required=True, type=int,
        help="the chapter number being planned (NOT lastAppliedChapter)",
    )
    p.add_argument(
        "--lookback", type=int, default=LOOKBACK_DEFAULT,
        help=f"how many recent summaries to scan (default {LOOKBACK_DEFAULT})",
    )
    p.add_argument("--json", action="store_true", help="emit pure JSON (no human summary)")
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
    if args.lookback < 1:
        print(json.dumps({"error": "--lookback must be >= 1"}, ensure_ascii=False),
              file=sys.stderr)
        return 1

    skill_root = Path(__file__).resolve().parent.parent
    diag = diagnose(book, skill_root, args.current_chapter, args.lookback)

    if args.json:
        print(json.dumps(diag, ensure_ascii=False, indent=2))
    else:
        print(f"Chapter {diag['currentChapter']}  | volume: "
              f"{diag['currentVolume']['name'] if diag['currentVolume'] else '(none)'}")
        print(f"Satisfaction pressure: {diag['satisfactionPressure']} "
              f"(gap={diag['chaptersSinceSatisfaction']} "
              f"last={diag['lastSatisfactionChapter']})")
        print(f"Volume beat status: {diag['volumeBeatStatus']}")
        if diag["recommendedChapterTypes"]:
            print("Recommended chapterTypes (top 3): "
                  + ", ".join(diag["recommendedChapterTypes"]))
        if diag["pacingNotes"]:
            print("Pacing notes:")
            for n in diag["pacingNotes"]:
                print(f"  - {n}")
        print()
        print(json.dumps(diag, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
