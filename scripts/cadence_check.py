#!/usr/bin/env python3
"""Cadence policy probe (Phase 02 — Planner pre-input).

Stdlib-only. Reads the genre profile + recent chapter_summaries + volume_map
and reports cadence pressure: how long since the last "satisfaction" beat,
which chapter types the upcoming chapter SHOULD be, and where we sit inside
the current volume's beat structure.

Planner is meant to consume this *before* writing the chapter_memo so that
it can rebalance pacing / hook payoff / volume mid-point pressure proactively.

Usage:
    python cadence_check.py --book <bookDir> --current-chapter N \
        [--memo <chapter_memo.md>] [--json]

`--memo` (gap item #3): read YAML frontmatter flags. `isGoldenOpening:
true` suppresses satisfaction-pressure warnings (golden three chapters
don't follow normal cadence). `volumeFinale: true` triggers an extra
`volumeFinaleReady` report (whether expected hook payoffs are scheduled
for this chapter).

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
    cadence_block = parse_cadence_block(raw)
    return {
        "_source": used,
        "_genreId": genre_id,
        "chapterTypes": fm.get("chapterTypes") or [],
        "satisfactionTypes": fm.get("satisfactionTypes") or [],
        "pacingRule": fm.get("pacingRule") or "",
        "cadence": cadence_block,
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


def parse_cadence_block(raw: str) -> dict[str, Any]:
    """Extract the structured ``cadence:`` sub-object from frontmatter.

    Returns ``{}`` if the block is absent — caller falls back to inferred
    defaults. The micro-parser supports the limited shapes documented in
    ``references/schemas/cadence-policy.md``: scalars, lists of inline
    ``{...}`` mappings, and 2-deep mappings (e.g. ``volumeBeatDistribution``).
    """
    if not raw.startswith("---"):
        return {}
    body = raw[3:]
    end = body.find("\n---")
    if end < 0:
        return {}
    block_lines = body[:end].splitlines()

    # Find the line "cadence:" at column 0.
    start_idx = -1
    for i, line in enumerate(block_lines):
        if re.match(r"^cadence\s*:\s*$", line):
            start_idx = i
            break
    if start_idx < 0:
        return {}

    # Collect indented (>= 1 space) lines under it.
    cad_lines: list[str] = []
    for line in block_lines[start_idx + 1:]:
        if not line.strip():
            cad_lines.append(line)
            continue
        # any non-indented line ends the block
        if not re.match(r"^\s", line):
            break
        cad_lines.append(line)

    return _parse_cadence_indented(cad_lines)


def _split_top_commas(s: str) -> list[str]:
    """Split ``s`` on commas not inside ``[]`` / ``{}`` / quoted strings."""
    parts: list[str] = []
    buf: list[str] = []
    depth_sq = 0
    depth_cu = 0
    quote: str | None = None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ('"', "'"):
            quote = ch
            buf.append(ch)
            continue
        if ch == "[":
            depth_sq += 1
            buf.append(ch)
            continue
        if ch == "]":
            depth_sq = max(0, depth_sq - 1)
            buf.append(ch)
            continue
        if ch == "{":
            depth_cu += 1
            buf.append(ch)
            continue
        if ch == "}":
            depth_cu = max(0, depth_cu - 1)
            buf.append(ch)
            continue
        if ch == "," and depth_sq == 0 and depth_cu == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _parse_inline_mapping(s: str) -> dict[str, Any]:
    """Parse ``{key: val, key: val}`` (vals may be ``"..."`` / list / unquoted)."""
    s = s.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return {}
    inner = s[1:-1]
    out: dict[str, Any] = {}
    parts = _split_top_commas(inner)
    for p in parts:
        if ":" not in p:
            continue
        k, v = p.split(":", 1)
        k = k.strip().strip('"').strip("'")
        v = v.strip()
        # list?
        if v.startswith("[") and v.endswith("]"):
            items = [
                t.strip().strip('"').strip("'")
                for t in _split_top_commas(v[1:-1])
            ]
            out[k] = items
            continue
        v = v.strip('"').strip("'")
        # int?
        if re.match(r"^-?\d+$", v):
            out[k] = int(v)
        else:
            out[k] = v
    return out


def _parse_cadence_indented(lines: list[str]) -> dict[str, Any]:
    """Parse the indented body of the ``cadence:`` block."""
    out: dict[str, Any] = {}
    i = 0
    n = len(lines)

    def line_indent(s: str) -> int:
        return len(s) - len(s.lstrip(" "))

    while i < n:
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        ind = line_indent(line)
        stripped = line.strip()
        # scalar: "key: value"
        m_scalar = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.+)$", stripped)
        m_block = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*$", stripped)

        if ind == 2 and m_scalar and not stripped.endswith(":"):
            key, val = m_scalar.group(1), m_scalar.group(2).strip()
            if re.match(r"^-?\d+$", val):
                out[key] = int(val)
            else:
                out[key] = val.strip('"').strip("'")
            i += 1
            continue

        if ind == 2 and m_block:
            key = m_block.group(1)
            # collect children (indent > 2)
            children: list[str] = []
            j = i + 1
            while j < n:
                cl = lines[j]
                if not cl.strip():
                    j += 1
                    continue
                ci = line_indent(cl)
                if ci <= 2:
                    break
                children.append(cl)
                j += 1

            # Decide: list (children start with "- ") or mapping?
            non_empty = [c for c in children if c.strip()]
            if non_empty and non_empty[0].lstrip().startswith("- "):
                items: list[Any] = []
                for c in non_empty:
                    cs = c.lstrip()
                    if cs.startswith("- "):
                        rest = cs[2:].strip()
                        if rest.startswith("{"):
                            items.append(_parse_inline_mapping(rest))
                        else:
                            items.append(rest.strip('"').strip("'"))
                out[key] = items
            else:
                # mapping of "subkey: {...}" or "subkey: scalar"
                sub: dict[str, Any] = {}
                for c in non_empty:
                    cs = c.strip()
                    m2 = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", cs)
                    if not m2:
                        continue
                    sk, sv = m2.group(1), m2.group(2).strip()
                    if not sv:
                        sub[sk] = {}
                    elif sv.startswith("{"):
                        sub[sk] = _parse_inline_mapping(sv)
                    elif re.match(r"^-?\d+$", sv):
                        sub[sk] = int(sv)
                    else:
                        sub[sk] = sv.strip('"').strip("'")
                out[key] = sub
            i = j
            continue

        i += 1
    return out


# ── Default cadence policy (used when `cadence:` is absent from the genre) ──
# Mapping of genre id → satisfactionWindow inferred from the table in
# `references/cadence-policy.md`. Keep in sync with that doc.
GENRE_DEFAULT_WINDOW = {
    "xianxia": 5, "xuanhuan": 4, "urban": 4, "horror": 6,
    "cultivation": 5, "litrpg": 3, "progression": 5,
    "tower-climber": 6, "system-apocalypse": 4, "dungeon-core": 6,
    "isekai": 6, "cozy": 8, "romantasy": 4, "sci-fi": 7, "other": 5,
}


def infer_cadence(profile: dict[str, Any]) -> dict[str, Any]:
    """Build a full cadence policy from profile (used when no `cadence:`)."""
    genre_id = profile.get("_genreId", "other")
    window = GENRE_DEFAULT_WINDOW.get(genre_id, 5)
    sat_types = profile.get("satisfactionTypes") or []
    chap_types = profile.get("chapterTypes") or []

    sequence = [{"type": t, "weight": 1} for t in sat_types]
    early = chap_types[:2] if chap_types else []
    middle = chap_types[1:3] if len(chap_types) >= 2 else chap_types
    late = chap_types[-2:] if chap_types else []

    return {
        "satisfactionWindow": window,
        "satisfactionSequence": sequence,
        "volumeBeatDistribution": {
            "early":  {"chapterTypes": early,  "satisfactionPerN": window * 2},
            "middle": {"chapterTypes": middle, "satisfactionPerN": window},
            "late":   {"chapterTypes": late,   "satisfactionPerN": max(2, window - 2)},
        },
        "fatigueGuards": [
            {"pattern": "连续 3 章同 chapterType",
             "action": "force-switch-type"},
            {"pattern": f"连续 {window} 章无爽点",
             "action": "satisfactionEmergency"},
        ],
    }


def resolve_cadence(profile: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Return (resolvedCadence, source) where source ∈ {'embedded','inferred'}."""
    cad = profile.get("cadence") or {}
    if not cad:
        return infer_cadence(profile), "inferred"
    # Fill any missing top-level keys from inferred defaults.
    inferred = infer_cadence(profile)
    merged = dict(inferred)
    for k, v in cad.items():
        merged[k] = v
    return merged, "embedded"


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


def classify_pressure(gap: int, window: int | None = None) -> str:
    """Classify gap → low / medium / high.

    When ``window`` is given, derive thresholds from it so that genres with
    looser pacing (cozy: window=8) don't get false-positive "high" alerts:
        high   when gap >= window
        medium when gap >= ceil(window * 0.6)
    Otherwise fall back to the legacy global constants.
    """
    if window and window > 0:
        high_t = window
        med_t = max(1, (window * 6 + 9) // 10)  # ceil(window*0.6)
        if gap >= high_t:
            return "high"
        if gap >= med_t:
            return "medium"
        return "low"
    if gap >= PRESSURE_HIGH:
        return "high"
    if gap >= PRESSURE_MED:
        return "medium"
    return "low"


def volume_band(volume: dict[str, Any] | None, chapter: int) -> str:
    """Return ``early`` / ``middle`` / ``late`` for cadence band lookup."""
    if not volume:
        return "middle"
    span = max(1, volume["endCh"] - volume["startCh"])
    progress = (chapter - volume["startCh"]) / span
    if progress < 0.33:
        return "early"
    if progress < 0.66:
        return "middle"
    return "late"


def recent_satisfaction_types_seen(
    rows: list[dict[str, Any]],
    satisfaction_types: list[str],
) -> list[str]:
    """Return chronological list of satisfactionTypes that hit in lookback."""
    hits: list[str] = []
    for s in sorted(rows, key=lambda r: int(r.get("chapter", 0) or 0)):
        if not matches_satisfaction(s, satisfaction_types):
            continue
        blob = " ".join(
            str(s.get(k, "")) for k in ("chapterType", "mood", "events", "title")
        ).lower()
        # find which satisfactionType matched
        for st in satisfaction_types:
            if st and st.strip().lower() in blob:
                hits.append(st)
                break
    return hits


def pick_next_satisfaction_type(
    sequence: list[dict[str, Any]],
    recent_hits: list[str],
) -> str:
    """Pick by sequence weight, demoting types just-used."""
    if not sequence:
        return ""
    last = recent_hits[-1] if recent_hits else ""
    last2 = set(recent_hits[-2:])
    scored: list[tuple[float, str]] = []
    for entry in sequence:
        t = str(entry.get("type", "") or "")
        w = float(entry.get("weight", 1) or 1)
        if not t:
            continue
        if t == last:
            w *= 0.3
        elif t in last2:
            w *= 0.6
        scored.append((w, t))
    if not scored:
        return ""
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][1]


def evaluate_fatigue_guards(
    guards: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    gap: int,
    window: int,
) -> list[dict[str, Any]]:
    """Run each guard's heuristic; return triggered alerts."""
    alerts: list[dict[str, Any]] = []
    sorted_rows = sorted(rows, key=lambda r: int(r.get("chapter", 0) or 0))

    # Helpers
    def streak_same_type(rs: list[dict[str, Any]]) -> tuple[int, str]:
        if not rs:
            return 0, ""
        last_t = str(rs[-1].get("chapterType", "") or "").strip()
        if not last_t:
            return 0, ""
        streak = 0
        for r in reversed(rs):
            if str(r.get("chapterType", "") or "").strip() == last_t:
                streak += 1
            else:
                break
        return streak, last_t

    streak, last_type = streak_same_type(sorted_rows)

    for g in guards:
        action = str(g.get("action", "") or "").strip()
        pattern = str(g.get("pattern", "") or "").strip()
        if not action:
            continue
        if action == "force-switch-type":
            # Trigger if streak >= 3 same chapterType.
            if streak >= 3:
                alerts.append({
                    "pattern": pattern or f"streak={streak} same chapterType='{last_type}'",
                    "action": action,
                    "evidence": {"streak": streak, "chapterType": last_type},
                })
        elif action == "satisfactionEmergency":
            if gap >= window:
                alerts.append({
                    "pattern": pattern or f"gap={gap} >= window={window}",
                    "action": action,
                    "evidence": {"gap": gap, "window": window},
                })
        elif action == "lower-tension":
            # Trigger when last 4 rows all have a high-tension mood marker.
            tension_keywords = (
                "紧张", "压抑", "凝重", "压迫", "窒息", "杀意", "对峙",
                "tense", "oppressive", "grim", "ominous", "bleak",
            )
            tail = sorted_rows[-4:]
            if len(tail) == 4 and all(
                any(k in str(r.get("mood", "")).lower() for k in tension_keywords)
                for r in tail
            ):
                alerts.append({
                    "pattern": pattern or "4 consecutive high-tension moods",
                    "action": action,
                    "evidence": {"streak": 4},
                })
        elif action == "vary-title-token":
            # Reserved for title-cadence integration. No-op now.
            continue
    return alerts


def build_recommended_next(
    cadence: dict[str, Any],
    band: str,
    pressure: str,
    gap: int,
    chap_recs: list[str],
    sat_types_seen: list[str],
) -> dict[str, Any]:
    """Build the {chapterType, satisfactionType, reasoning} hint."""
    band_cfg = (cadence.get("volumeBeatDistribution") or {}).get(band, {}) or {}
    band_chap_types = band_cfg.get("chapterTypes") or []
    sat_per_n = band_cfg.get("satisfactionPerN")

    # Prefer band's chapter types if any; otherwise fall back to cadence_check's
    # legacy recommendation list.
    chosen_chap = ""
    last_recent_chap = sat_types_seen[-1] if sat_types_seen else ""
    for t in band_chap_types:
        if t and t != last_recent_chap:
            chosen_chap = t
            break
    if not chosen_chap and chap_recs:
        chosen_chap = chap_recs[0]
    if not chosen_chap and band_chap_types:
        chosen_chap = band_chap_types[0]

    sequence = cadence.get("satisfactionSequence") or []
    chosen_sat = pick_next_satisfaction_type(sequence, sat_types_seen)

    parts = [f"band={band}"]
    if sat_per_n:
        parts.append(f"target {sat_per_n}-ch satisfaction cadence")
    parts.append(f"gap={gap}")
    if pressure != "low":
        parts.append(f"pressure={pressure} → satisfactionType prioritized")
    if last_recent_chap:
        parts.append(f"avoid repeating last chapterType='{last_recent_chap}'")
    return {
        "chapterType": chosen_chap,
        "satisfactionType": chosen_sat,
        "reasoning": "; ".join(parts),
    }


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


def parse_memo_flags(memo_path: Path | None) -> dict[str, bool]:
    """Read 5 boolean flags from chapter_memo YAML frontmatter.

    Returns all-False if path is None / file missing / no frontmatter.
    Tolerates "true" / "True" / "yes" / "1" as truthy.
    """
    flags = {
        "isGoldenOpening": False,
        "cliffResolution": False,
        "arcTransition": False,
        "volumeFinale": False,
        "isReshootChapter": False,
    }
    if memo_path is None or not memo_path.is_file():
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


def assess_volume_finale_ready(
    hooks: list[dict[str, Any]],
    current_chapter: int,
    current_vol: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assess whether expected hook payoffs are scheduled for a volumeFinale chapter.

    Heuristic: hooks with `committedToChapter` ∈ [current_volume_range] AND
    not yet resolved should all have `committedToChapter <= current_chapter`.
    Returns a small dict consumed by the report.
    """
    out = {
        "ready": True,
        "owedHooks": [],   # hook ids whose committedToChapter <= currentCh but not resolved
        "lateHooks": [],   # hook ids committed to *this* chapter
        "deferredOverdue": [],  # hooks committed to a past chapter but unresolved
    }
    if not isinstance(hooks, list):
        return out
    vol_start = (current_vol or {}).get("startCh", 1)
    vol_end = (current_vol or {}).get("endCh", current_chapter)
    for h in hooks:
        if not isinstance(h, dict):
            continue
        committed = h.get("committedToChapter")
        if committed is None:
            continue
        try:
            committed = int(committed)
        except (TypeError, ValueError):
            continue
        if committed < vol_start or committed > vol_end:
            continue
        status = str(h.get("status", "")).strip().lower()
        if status == "resolved":
            continue
        hid = h.get("hookId", "")
        if committed == current_chapter:
            out["lateHooks"].append(hid)
            out["owedHooks"].append(hid)
        elif committed < current_chapter:
            out["deferredOverdue"].append(hid)
            out["owedHooks"].append(hid)
    out["ready"] = not out["owedHooks"]
    return out


def diagnose(
    book_dir: Path,
    skill_root: Path,
    current_chapter: int,
    lookback: int,
    memo_flags: dict[str, bool] | None = None,
) -> dict[str, Any]:
    if memo_flags is None:
        memo_flags = {
            "isGoldenOpening": False, "cliffResolution": False,
            "arcTransition": False, "volumeFinale": False, "isReshootChapter": False,
        }
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

    cadence, cadence_source = resolve_cadence(profile)
    window = int(cadence.get("satisfactionWindow") or 0) or None

    gap, last_sat = chapters_since_satisfaction(rows, current_chapter, sat_types)
    pressure = classify_pressure(gap, window)

    # If there are no rows at all, satisfaction concept does not apply yet.
    if not rows:
        gap = 0
        pressure = "low"
        last_sat = None

    # Golden opening overrides cadence: those chapters don't follow normal pressure rules.
    pressure_suppressed = False
    if memo_flags.get("isGoldenOpening"):
        pressure = "low"
        pressure_suppressed = True

    types_dist = recent_types_distribution(rows)
    recs = recommend_chapter_types(chap_types, sat_types, types_dist, pressure)

    notes = build_pacing_notes(pressure, gap, pacing_rule, types_dist)
    if pressure_suppressed:
        notes.append(
            "isGoldenOpening=true → satisfaction-pressure warnings suppressed "
            "(golden opening doesn't follow normal cadence)"
        )
    vol_status = volume_beat_status(current_vol, current_chapter)

    band = volume_band(current_vol, current_chapter)
    sat_hits = recent_satisfaction_types_seen(rows, sat_types)
    recommended_next = build_recommended_next(
        cadence, band, pressure, gap, recs, sat_hits,
    )
    fatigue_alerts = evaluate_fatigue_guards(
        cadence.get("fatigueGuards") or [], rows, gap, window or 5,
    )

    # volumeFinaleReady: only emitted when memo flag set
    volume_finale_ready: dict[str, Any] | None = None
    if memo_flags.get("volumeFinale"):
        hooks_obj = load_json(
            book_dir / "story" / "state" / "hooks.json", {"hooks": []},
        )
        hooks = hooks_obj.get("hooks", []) if isinstance(hooks_obj, dict) else []
        volume_finale_ready = assess_volume_finale_ready(
            hooks, current_chapter, current_vol,
        )

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
        "recommendedNext": recommended_next,
        "fatigueAlerts": fatigue_alerts,
        "pacingNotes": notes,
        "volumeBeatStatus": vol_status,
        "volumeBand": band,
        "satisfactionTypes": sat_types,
        "chapterTypes": chap_types,
        "pacingRule": pacing_rule,
        "lookbackChapters": [int(s.get("chapter", 0) or 0) for s in rows],
        "cadencePolicy": {
            "satisfactionWindow": cadence.get("satisfactionWindow"),
            "source": cadence_source,
        },
        "memoFlags": memo_flags,
        "volumeFinaleReady": volume_finale_ready,  # null unless memo.volumeFinale=true
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
    p.add_argument(
        "--memo", type=Path, default=None,
        help=("optional chapter_memo.md path; YAML frontmatter flags "
              "(isGoldenOpening / volumeFinale) adjust output. "
              "isGoldenOpening=true suppresses satisfaction-pressure warnings; "
              "volumeFinale=true emits volumeFinaleReady report."),
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
    memo_flags = parse_memo_flags(args.memo)
    diag = diagnose(book, skill_root, args.current_chapter, args.lookback, memo_flags)

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
        rn = diag.get("recommendedNext") or {}
        if rn.get("chapterType") or rn.get("satisfactionType"):
            print(f"Recommended next: chapterType='{rn.get('chapterType','')}' "
                  f"satisfactionType='{rn.get('satisfactionType','')}'")
            if rn.get("reasoning"):
                print(f"  reasoning: {rn['reasoning']}")
        fa = diag.get("fatigueAlerts") or []
        if fa:
            print("Fatigue alerts:")
            for a in fa:
                print(f"  - [{a.get('action')}] {a.get('pattern')}")
        cp = diag.get("cadencePolicy") or {}
        if cp:
            print(f"Cadence policy: window={cp.get('satisfactionWindow')} "
                  f"source={cp.get('source')}")
        if diag["pacingNotes"]:
            print("Pacing notes:")
            for n in diag["pacingNotes"]:
                print(f"  - {n}")
        print()
        print(json.dumps(diag, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
