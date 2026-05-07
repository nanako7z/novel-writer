#!/usr/bin/env python3
"""docOps Drift Scanner — flag stale guidance md against recent chapter activity.

Sister of `audit_drift.py`. After a chapter lands (orchestration step 11.0c),
scan the white-list guidance md against the last N chapter summaries; emit
advisory candidates for changes that *should* have been made via docOps but
weren't. The output mirrors `09-auditor.docDriftCandidates`: pure suggestion,
non-blocking, consumed by next chapter's Settler.

Heuristics (intentionally conservative — false negatives > false positives):

  1. current_focus.md
     "## Active Focus" mentions chapter numbers (e.g. "第 N 章：兑现 X").
     If the largest such N is ≥ 3 chapters behind lastAppliedChapter, flag.

  2. character_matrix.md / roles/**
     Names that recur in ≥ 3 of the last N chapter summaries' `characters`
     field but appear in NEITHER character_matrix rows NOR roles/* filenames
     → role-arbiter missed them (or Settler didn't propose them); flag.

  3. subplot_board.md
     `events` text from the last N chapter_summaries that mention strings
     looking like subplot ids (uppercase + dash, e.g. "SUB-MOTHER-LINE")
     but not present in subplot_board → flag.

  4. emotional_arcs.md
     `mood` in chapter_summary changes 3 chapters in a row for the same
     protagonist with no row in emotional_arcs covering that span → flag.

  5. outline/volume_map.md / story_frame.md
     Not auto-scanned — these change rarely and are Architect-owned. Skip.

Each candidate has:
  - target:       docOps top-level key the suggested fix would touch
  - severity:     "warning" | "info"
  - reason:       human-readable description
  - evidence:     list of chapter numbers / strings supporting the candidate
  - suggestedOp:  "replace_section" | "upsert_row" | "create_role" | ...

Exit code 0 always (advisory, like audit_drift). Empty candidates → no drift.

CLI:
    python docops_drift.py --book <bookDir> [--window 6] [--json] [--write]

`--write` persists output to `story/runtime/docops_drift.json` so next
chapter's Settler prompt assembly can pick it up.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _summary import emit_summary  # noqa: E402

# ──────────────────────────── helpers ───────────────────────────────


_CHAP_RE = re.compile(r"第\s*(\d+)\s*章")
_NAME_RE = re.compile(r"[一-鿿]{2,4}")
_SUBPLOT_ID_RE = re.compile(r"\b[A-Z]{3,}-[A-Z][A-Z0-9-]+\b")
_TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")


def _read_json(p: Path, default: Any) -> Any:
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _read_text(p: Path) -> str:
    if not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _last_n_summaries(book: Path, n: int) -> list[dict]:
    cs_path = book / "story" / "state" / "chapter_summaries.json"
    obj = _read_json(cs_path, {"rows": []})
    if isinstance(obj, dict):
        rows = obj.get("rows") or []
    elif isinstance(obj, list):
        rows = obj
    else:
        rows = []
    rows = [r for r in rows if isinstance(r, dict)]
    rows.sort(key=lambda r: r.get("chapter") or 0)
    return rows[-n:] if n > 0 else rows


def _last_applied_chapter(book: Path) -> int:
    mfst = _read_json(book / "story" / "state" / "manifest.json", {})
    v = mfst.get("lastAppliedChapter") if isinstance(mfst, dict) else None
    return int(v) if isinstance(v, int) else 0


def _parse_table_first_col(text: str) -> set[str]:
    """Return the set of values in the first column of the first table found."""
    out: set[str] = set()
    lines = text.splitlines()
    in_table = False
    seen_header = False
    for line in lines:
        if _TABLE_LINE_RE.match(line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not in_table:
                in_table = True
                seen_header = True
                continue  # skip header
            if seen_header and re.match(r"^[\s:|-]+$", "".join(cells)):
                seen_header = False  # separator row
                continue
            if cells and cells[0]:
                out.add(cells[0])
        else:
            if in_table:
                break
    return out


def _list_role_slugs(book: Path) -> set[str]:
    root = book / "story" / "roles"
    if not root.is_dir():
        return set()
    out: set[str] = set()
    for p in root.rglob("*.md"):
        if p.is_file() and not p.name.startswith("_"):
            out.add(p.stem)
    return out


# ──────────────────────────── checks ────────────────────────────────


def _check_current_focus(book: Path, last_ch: int) -> list[dict]:
    txt = _read_text(book / "story" / "current_focus.md")
    if not txt or last_ch == 0:
        return []
    m_section = re.search(r"##\s+Active Focus\b([\s\S]*?)(?=^##\s|\Z)", txt, re.M)
    body = m_section.group(1) if m_section else txt
    chap_refs = [int(m.group(1)) for m in _CHAP_RE.finditer(body)]
    if not chap_refs:
        return []
    max_ref = max(chap_refs)
    gap = last_ch - max_ref
    if gap >= 3:
        return [{
            "target": "currentFocus",
            "severity": "warning",
            "reason": (
                f"current_focus.md 最远引用 第{max_ref}章，但当前 lastAppliedChapter="
                f"{last_ch}（gap={gap} 章）；焦点条可能已过时"
            ),
            "evidence": [f"ch{c}" for c in sorted(set(chap_refs))],
            "suggestedOp": "replace_section",
        }]
    return []


def _check_unknown_recurring_names(book: Path, summaries: list[dict],
                                   matrix_keys: set[str],
                                   role_slugs: set[str]) -> list[dict]:
    if len(summaries) < 3:
        return []
    name_count: dict[str, list[int]] = {}
    for row in summaries:
        chars_field = row.get("characters") or ""
        ch = row.get("chapter")
        if not isinstance(chars_field, str) or not isinstance(ch, int):
            continue
        # split by comma (full / half-width) then trim
        for raw in re.split(r"[,，、]", chars_field):
            name = raw.strip()
            if not name or len(name) < 2:
                continue
            # ignore generic groups
            if name in {"众人", "他", "她", "它", "众", "群众"}:
                continue
            name_count.setdefault(name, []).append(ch)

    candidates: list[dict] = []
    for name, chapters in name_count.items():
        if len(chapters) < 3:
            continue
        # filter matrix membership: matrix_keys are first-column entries
        # (often charA names); also check role_slugs which is roles/<name>.md.
        if name in matrix_keys or name in role_slugs:
            continue
        # also tolerate partial match (e.g., name = "林秋" matches a role
        # slug "林秋之"; rare but harmless to suppress)
        if any(name in s or s in name for s in role_slugs if len(s) >= 2):
            continue
        candidates.append({
            "target": "roles",
            "severity": "warning",
            "reason": (
                f"角色 {name!r} 在最近 {len(chapters)} 章持续出场（"
                f"{','.join(f'ch{c}' for c in chapters)}），"
                "但 character_matrix 与 roles/ 都没有对应条目"
            ),
            "evidence": [f"ch{c}" for c in chapters],
            "suggestedOp": "create_role (via newRoleCandidates)",
            "name": name,
        })
    return candidates


def _check_subplot_drift(book: Path, summaries: list[dict]) -> list[dict]:
    sb_text = _read_text(book / "story" / "subplot_board.md")
    sb_keys = _parse_table_first_col(sb_text)
    seen_ids: dict[str, list[int]] = {}
    for row in summaries:
        ev = row.get("events") or ""
        ch = row.get("chapter")
        if not isinstance(ev, str) or not isinstance(ch, int):
            continue
        for m in _SUBPLOT_ID_RE.finditer(ev):
            sid = m.group(0)
            seen_ids.setdefault(sid, []).append(ch)
    out: list[dict] = []
    for sid, chs in seen_ids.items():
        if sid in sb_keys:
            continue
        out.append({
            "target": "subplotBoard",
            "severity": "info",
            "reason": (
                f"事件文本提到疑似支线 ID {sid!r}（出现于 "
                f"{','.join(f'ch{c}' for c in chs)}），但 subplot_board 无对应行"
            ),
            "evidence": [f"ch{c}" for c in chs],
            "suggestedOp": "upsert_row",
            "subplotId": sid,
        })
    return out


def _check_emotional_arc_stagnation(book: Path, summaries: list[dict]) -> list[dict]:
    if len(summaries) < 3:
        return []
    last3 = summaries[-3:]
    moods = [str(r.get("mood") or "").strip() for r in last3]
    if not all(moods):
        return []
    # if all 3 chapters share the same non-empty mood, the protagonist's
    # emotional arc is stagnant; emotional_arcs.md should record this run
    if len(set(moods)) == 1:
        ea_text = _read_text(book / "story" / "emotional_arcs.md")
        last_chapter = last3[-1].get("chapter")
        # crude check: did the last 3 chapter numbers appear in emotional_arcs?
        anchored = all(f"ch{r.get('chapter')}" in ea_text or
                       str(r.get("chapter")) in ea_text for r in last3)
        if not anchored:
            return [{
                "target": "emotionalArcs",
                "severity": "info",
                "reason": (
                    f"最近 3 章 mood 持续 {moods[0]!r}，但 emotional_arcs.md "
                    "未为这一段持续情绪开行"
                ),
                "evidence": [f"ch{r.get('chapter')}" for r in last3],
                "suggestedOp": "upsert_row",
            }]
    return []


# ────────────────────────────── main ────────────────────────────────


def scan(book: Path, window: int = 6) -> dict:
    last_ch = _last_applied_chapter(book)
    summaries = _last_n_summaries(book, window)

    cm_keys = _parse_table_first_col(_read_text(book / "story" / "character_matrix.md"))
    role_slugs = _list_role_slugs(book)

    candidates: list[dict] = []
    candidates.extend(_check_current_focus(book, last_ch))
    candidates.extend(_check_unknown_recurring_names(book, summaries, cm_keys, role_slugs))
    candidates.extend(_check_subplot_drift(book, summaries))
    candidates.extend(_check_emotional_arc_stagnation(book, summaries))

    return {
        "ok": True,
        "lastAppliedChapter": last_ch,
        "window": window,
        "summariesScanned": len(summaries),
        "candidates": candidates,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Scan guidance md for drift against recent chapter activity. "
                    "Sister of audit_drift.py; advisory, non-blocking.",
    )
    p.add_argument("--book", required=True, help="book directory path")
    p.add_argument("--window", type=int, default=6,
                   help="how many recent chapter_summaries to scan (default: 6)")
    p.add_argument("--write", action="store_true",
                   help="persist result to story/runtime/docops_drift.json "
                   "for the next chapter's Settler to read")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="print result as JSON (default behavior)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    book = Path(args.book).resolve()
    if not book.is_dir():
        print(json.dumps({"ok": False, "error": f"book dir not found: {book}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    result = scan(book, window=args.window)

    if args.write:
        out_path = book / "story" / "runtime" / "docops_drift.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # If no candidates, remove the file (don't leave stale advice)
        if not result["candidates"]:
            if out_path.exists():
                out_path.unlink()
            result["written"] = None
        else:
            out_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            result["written"] = str(out_path.relative_to(book))

    print(json.dumps(result, ensure_ascii=False, indent=2))
    n = len(result["candidates"])
    emit_summary(
        f"docops drift scan: lastCh={result['lastAppliedChapter']} "
        f"window={args.window} candidates={n}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
