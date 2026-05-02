#!/usr/bin/env python3
"""Compute analytics for a single book.

Read-only.  No LLM.  Aggregates over `chapters/NNNN.md`,
`story/state/chapter_summaries.json`, `story/state/hooks.json`, and any
`runtime/chapter-NNNN.{audit,tokens}.json` sidecars that may be present.

Usage:
    python analytics.py --book <bookDir> [--chapters] [--detection] [--json]

Aggregate fields are always emitted.  `--chapters` adds a per-chapter table.
`--detection` summarises `story/detection_history.json` if present (the
detection module is not yet ported, so an empty stub is returned when the
file is absent).

Length counting honours `book.lengthCountingMode` (`zh-cjk`, `cjk-strict`,
`latin-words`).  Falls back to `book.language` (`zh` -> zh-cjk,
`en` -> latin-words).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from statistics import median
from typing import Any


# --------------------------- length counting -------------------------------

# CJK Unified Ideographs, Ext A, Ext B-G via surrogate-aware ranges.
# Python re handles supplementary planes with \U escapes.
_CJK_RANGES = (
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # Ext A
    (0x20000, 0x2A6DF), # Ext B
    (0x2A700, 0x2B73F), # Ext C
    (0x2B740, 0x2B81F), # Ext D
    (0x2B820, 0x2CEAF), # Ext E
    (0x2CEB0, 0x2EBEF), # Ext F
    (0x30000, 0x3134F), # Ext G
)

# Chinese + Latin punctuation we exclude from unique-char counts.
_PUNCT_CHARS = set(
    "，。！？、；：“”‘’「」『』（）【】《》〈〉—…·~"
    ".,!?;:\"'()[]{}<>-—_/\\|`~@#$%^&*+=…"
)

_FRONT_MATTER = re.compile(r"^---\n.*?\n---\n", re.DOTALL)
_HEADING = re.compile(r"^#{1,6}\s+.*$", re.MULTILINE)
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`]*`")


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    for lo, hi in _CJK_RANGES:
        if lo <= o <= hi:
            return True
    return False


def _strip_metadata(text: str) -> str:
    text = text.replace("\r\n", "\n").lstrip("﻿")
    text = _FRONT_MATTER.sub("", text, count=1)
    text = _FENCE.sub("", text)
    text = _INLINE_CODE.sub("", text)
    text = _HTML_COMMENT.sub("", text)
    text = _HEADING.sub("", text)
    return text


def resolve_counting_mode(book: dict) -> str:
    raw = book.get("lengthCountingMode")
    if raw in ("zh-cjk", "cjk-strict", "latin-words"):
        return raw
    # Backward-compat aliases used by other scripts.
    if raw in ("zh_chars",):
        return "zh-cjk"
    if raw in ("en_words",):
        return "latin-words"
    lang = (book.get("language") or "zh").lower()
    return "latin-words" if lang.startswith("en") else "zh-cjk"


def count_length(text: str, mode: str) -> int:
    body = _strip_metadata(text)
    if mode == "latin-words":
        return len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", body))
    if mode == "cjk-strict":
        return sum(1 for c in body if _is_cjk(c))
    # zh-cjk default: count CJK code points (punctuation already not CJK).
    return sum(1 for c in body if _is_cjk(c))


def collect_unique_chars(text: str) -> set[str]:
    body = _strip_metadata(text)
    out: set[str] = set()
    for ch in body:
        if ch.isspace():
            continue
        if ch in _PUNCT_CHARS:
            continue
        # Drop other punctuation/symbols for a fair "content char" set.
        cat = _char_cat(ch)
        if cat in {"P", "S"}:
            continue
        out.add(ch)
    return out


def _char_cat(ch: str) -> str:
    """Crude unicode category classifier (P=punct, S=symbol, L=letter, ...).

    stdlib `unicodedata.category` returns 2-letter codes; we only need the
    first letter to reject punct/symbol.  Use lazy import to keep imports
    tidy at the top.
    """
    import unicodedata
    return unicodedata.category(ch)[0]


# --------------------------- file helpers ----------------------------------

CHAPTER_NAME_RE = re.compile(r"^(\d{4})\.md$")


def _safe_load_json(p: Path, default: Any) -> Any:
    if not p.is_file():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def list_chapter_files(book_dir: Path) -> list[Path]:
    chap_dir = book_dir / "chapters"
    if not chap_dir.is_dir():
        return []
    files: list[Path] = []
    for f in chap_dir.iterdir():
        if f.is_file() and CHAPTER_NAME_RE.match(f.name):
            files.append(f)
    files.sort(key=lambda p: p.name)
    return files


def _load_audit_sidecar(book_dir: Path, ch_num: int) -> dict | None:
    """runtime/chapter-NNNN.audit.json (optional)."""
    p = book_dir / "runtime" / f"chapter-{ch_num:04d}.audit.json"
    data = _safe_load_json(p, None)
    return data if isinstance(data, dict) else None


def _load_tokens_sidecar(book_dir: Path, ch_num: int) -> dict | None:
    """runtime/chapter-NNNN.tokens.json (optional)."""
    p = book_dir / "runtime" / f"chapter-{ch_num:04d}.tokens.json"
    data = _safe_load_json(p, None)
    return data if isinstance(data, dict) else None


# --------------------------- aggregate -------------------------------------

def _passed_statuses() -> set[str]:
    return {"ready-for-review", "approved", "published"}


def compute_book_analytics(book_dir: Path, *, with_chapters: bool,
                           with_detection: bool) -> dict:
    book = _safe_load_json(book_dir / "book.json", {}) or {}
    state_dir = book_dir / "story" / "state"

    counting_mode = resolve_counting_mode(book)
    target_per_chapter = int(book.get("chapterWordCount", 0) or 0)
    soft_min = int(round(target_per_chapter * 0.85)) if target_per_chapter else 0
    soft_max = int(round(target_per_chapter * 1.15)) if target_per_chapter else 0

    # Index summaries by chapter number.
    # inkos uses `rows` wrapper; legacy SKILL books used `summaries`. Read both.
    summaries_obj = _safe_load_json(state_dir / "chapter_summaries.json",
                                    {"rows": []}) or {}
    summaries = (summaries_obj.get("rows", summaries_obj.get("summaries", []))
                 if isinstance(summaries_obj, dict) else [])
    summary_by_ch: dict[int, dict] = {}
    if isinstance(summaries, list):
        for row in summaries:
            if isinstance(row, dict) and isinstance(row.get("chapter"), int):
                summary_by_ch[row["chapter"]] = row

    chapter_files = list_chapter_files(book_dir)
    total_chapters = len(chapter_files)

    chapter_rows: list[dict] = []
    word_counts: list[int] = []
    unique_chars: set[str] = set()
    audit_scores: list[int] = []
    audit_rounds: list[int] = []
    in_soft_range = 0
    soft_range_eligible = 0
    word_trend: list[dict] = []

    # Token sums (only over chapters with sidecar tokens.json).
    token_total = 0
    token_prompt = 0
    token_completion = 0
    token_chapter_count = 0

    for f in chapter_files:
        m = CHAPTER_NAME_RE.match(f.name)
        if not m:
            continue
        ch_num = int(m.group(1))
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            text = ""
        wc = count_length(text, counting_mode)
        word_counts.append(wc)
        unique_chars.update(collect_unique_chars(text))
        word_trend.append({"chapter": ch_num, "words": wc})

        if target_per_chapter:
            soft_range_eligible += 1
            if soft_min <= wc <= soft_max:
                in_soft_range += 1

        summary = summary_by_ch.get(ch_num) or {}

        # Audit score: prefer summary row, else sidecar.
        score = summary.get("auditScore")
        sidecar_audit = _load_audit_sidecar(book_dir, ch_num)
        if score is None and sidecar_audit is not None:
            score = (sidecar_audit.get("auditScore")
                     or sidecar_audit.get("score"))
        if isinstance(score, (int, float)):
            audit_scores.append(int(score))

        # Revision rounds: summary row, else sidecar.
        rounds = summary.get("auditRounds")
        if rounds is None and sidecar_audit is not None:
            rounds = (sidecar_audit.get("auditRounds")
                      or sidecar_audit.get("rounds")
                      or sidecar_audit.get("revisionRounds"))
        if isinstance(rounds, (int, float)):
            audit_rounds.append(int(rounds))

        # Tokens sidecar.
        tokens = _load_tokens_sidecar(book_dir, ch_num)
        if isinstance(tokens, dict):
            tt = tokens.get("totalTokens") or tokens.get("total")
            tp = tokens.get("promptTokens") or tokens.get("prompt")
            tc = tokens.get("completionTokens") or tokens.get("completion")
            if any(isinstance(v, (int, float)) for v in (tt, tp, tc)):
                token_chapter_count += 1
                if isinstance(tt, (int, float)):
                    token_total += int(tt)
                if isinstance(tp, (int, float)):
                    token_prompt += int(tp)
                if isinstance(tc, (int, float)):
                    token_completion += int(tc)

        if with_chapters:
            chapter_rows.append({
                "chapter": ch_num,
                "title": summary.get("title") or "",
                "wordCount": wc,
                "auditScore": int(score) if isinstance(score, (int, float)) else None,
                "auditRounds": int(rounds) if isinstance(rounds, (int, float)) else None,
                "status": summary.get("status"),
                "mood": summary.get("mood"),
                "chapterType": summary.get("chapterType"),
            })

    total_words = sum(word_counts)
    avg_words = round(total_words / total_chapters) if total_chapters else 0
    med_words = int(median(word_counts)) if word_counts else 0

    # Audit pass rate: % of audit-scored chapters with score >= 85.
    if audit_scores:
        passed = sum(1 for s in audit_scores if s >= 85)
        audit_pass_rate = passed / len(audit_scores) * 100.0
    else:
        # Fallback: status-based (mirrors inkos `computeAnalytics`).
        audited = [r for r in summary_by_ch.values()
                   if r.get("status") not in {None, "drafted", "drafting", "card-generated"}]
        if audited:
            passed_set = _passed_statuses()
            passed = sum(1 for r in audited if r.get("status") in passed_set)
            audit_pass_rate = passed / len(audited) * 100.0
        else:
            audit_pass_rate = None  # nothing to score

    revision_avg = (sum(audit_rounds) / len(audit_rounds)) if audit_rounds else None
    length_compliance = (in_soft_range / soft_range_eligible * 100.0
                         if soft_range_eligible else None)

    # Hooks summary.
    hooks_obj = _safe_load_json(state_dir / "hooks.json", {"hooks": []}) or {}
    hooks = hooks_obj.get("hooks", []) if isinstance(hooks_obj, dict) else []
    hook_total = 0
    hook_active = 0
    hook_resolved = 0
    hook_stale = 0
    if isinstance(hooks, list):
        for h in hooks:
            if not isinstance(h, dict):
                continue
            hook_total += 1
            status = (h.get("status") or "").strip().lower()
            if status in {"resolved", "closed", "done", "已回收", "已解决"}:
                hook_resolved += 1
                continue
            hook_active += 1
            if h.get("stale"):
                hook_stale += 1

    token_usage: dict | None = None
    if token_chapter_count > 0:
        token_usage = {
            "totalTokens": token_total,
            "totalPromptTokens": token_prompt,
            "totalCompletionTokens": token_completion,
            "avgTokensPerChapter": round(token_total / token_chapter_count),
            "chaptersWithTokens": token_chapter_count,
        }

    aggregate = {
        "countingMode": counting_mode,
        "totalChapters": total_chapters,
        "totalWords": total_words,
        "avgWordsPerChapter": avg_words,
        "medianWordsPerChapter": med_words,
        "totalUniqueChars": len(unique_chars),
        "auditPassRate": (round(audit_pass_rate, 1)
                          if audit_pass_rate is not None else None),
        "revisionAvg": (round(revision_avg, 2)
                        if revision_avg is not None else None),
        "lengthCompliance": (round(length_compliance, 1)
                             if length_compliance is not None else None),
        "chapterWordCountTarget": target_per_chapter or None,
        "hookActivity": {
            "totalEverPlanted": hook_total,
            "active": hook_active,
            "resolved": hook_resolved,
            "stale": hook_stale,
        },
        "wordCountTrend": word_trend,
        "tokenUsage": token_usage,
    }

    payload = {
        "bookId": book.get("id") or book_dir.name,
        "title": book.get("title") or book_dir.name,
        "computedAt": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "aggregate": aggregate,
    }
    if with_chapters:
        payload["chapters"] = chapter_rows
    if with_detection:
        payload["detection"] = compute_detection_summary(book_dir)
    return payload


def compute_detection_summary(book_dir: Path) -> dict:
    """Summarise `story/detection_history.json` if present.

    The detection module is not yet ported to the SKILL form; if the file is
    absent we return an empty stub.  When present, we expect a list of
    entries each carrying at least `score` and optionally `passed` /
    `reduction`.
    """
    p = book_dir / "story" / "detection_history.json"
    if not p.is_file():
        return {"present": False}
    raw = _safe_load_json(p, None)
    entries: list[dict] = []
    if isinstance(raw, list):
        entries = [e for e in raw if isinstance(e, dict)]
    elif isinstance(raw, dict):
        maybe = raw.get("history") or raw.get("entries") or []
        if isinstance(maybe, list):
            entries = [e for e in maybe if isinstance(e, dict)]
    if not entries:
        return {"present": True, "count": 0}

    scores = [float(e["score"]) for e in entries
              if isinstance(e.get("score"), (int, float))]
    passed = [bool(e.get("passed")) for e in entries if "passed" in e]
    reductions = [float(e["reduction"]) for e in entries
                  if isinstance(e.get("reduction"), (int, float))]

    out: dict[str, Any] = {"present": True, "count": len(entries)}
    if scores:
        out["avgScore"] = round(sum(scores) / len(scores), 2)
        out["minScore"] = round(min(scores), 2)
        out["maxScore"] = round(max(scores), 2)
    if reductions:
        out["scoreReductionTrend"] = round(sum(reductions) / len(reductions), 2)
    if passed:
        out["passRate"] = round(sum(1 for v in passed if v) / len(passed) * 100.0, 1)
    return out


# --------------------------- text rendering --------------------------------

def _fmt_int(n: int | None) -> str:
    return f"{n:,}" if isinstance(n, int) else "-"


def _fmt_pct(p: float | None) -> str:
    return f"{p:.1f}%" if isinstance(p, (int, float)) else "-"


def render_text(payload: dict, *, with_chapters: bool,
                with_detection: bool) -> str:
    agg = payload["aggregate"]
    lines: list[str] = []
    lines.append(f"Analytics for {payload['title']} ({payload['bookId']})")
    lines.append(f"  Computed at: {payload['computedAt']}")
    lines.append("")

    rows: list[tuple[str, str]] = [
        ("Counting mode",            agg["countingMode"]),
        ("Total chapters",           _fmt_int(agg["totalChapters"])),
        ("Total words",              _fmt_int(agg["totalWords"])),
        ("Avg words / chapter",      _fmt_int(agg["avgWordsPerChapter"])),
        ("Median words / chapter",   _fmt_int(agg["medianWordsPerChapter"])),
        ("Unique CJK / letters",     _fmt_int(agg["totalUniqueChars"])),
        ("Per-chapter target",       _fmt_int(agg.get("chapterWordCountTarget"))),
        ("Audit pass rate (>=85)",   _fmt_pct(agg["auditPassRate"])),
        ("Avg revision rounds",      (f"{agg['revisionAvg']:.2f}"
                                      if agg["revisionAvg"] is not None else "-")),
        ("Length compliance",        _fmt_pct(agg["lengthCompliance"])),
    ]
    width = max(len(k) for k, _ in rows)
    for k, v in rows:
        lines.append(f"  {k.ljust(width)} : {v}")

    h = agg["hookActivity"]
    lines.append("")
    lines.append("  Hook activity:")
    lines.append(f"    total ever planted : {_fmt_int(h['totalEverPlanted'])}")
    lines.append(f"    active             : {_fmt_int(h['active'])}")
    lines.append(f"    resolved           : {_fmt_int(h['resolved'])}")
    lines.append(f"    stale              : {_fmt_int(h['stale'])}")

    tu = agg["tokenUsage"]
    if tu:
        lines.append("")
        lines.append("  Token usage:")
        lines.append(f"    total tokens       : {_fmt_int(tu['totalTokens'])}")
        lines.append(f"    prompt tokens      : {_fmt_int(tu['totalPromptTokens'])}")
        lines.append(f"    completion tokens  : {_fmt_int(tu['totalCompletionTokens'])}")
        lines.append(f"    avg / chapter      : {_fmt_int(tu['avgTokensPerChapter'])}")
        lines.append(f"    chapters w/ tokens : {_fmt_int(tu['chaptersWithTokens'])}")

    trend = agg["wordCountTrend"]
    if trend:
        lines.append("")
        lines.append("  Word-count trend (last 10):")
        lines.append(f"    {'#':>4}  {'words':>8}")
        for row in trend[-10:]:
            lines.append(f"    {row['chapter']:>4}  {row['words']:>8,}")

    if with_chapters and payload.get("chapters"):
        lines.append("")
        lines.append("  Chapters:")
        lines.append(f"    {'#':>4}  {'words':>7}  {'score':>5}  {'rnds':>4}  {'status':<18}  {'type':<14}  {'mood':<10}  title")
        for row in payload["chapters"]:
            lines.append(
                f"    {row['chapter']:>4}  "
                f"{(row['wordCount'] or 0):>7,}  "
                f"{(row['auditScore'] if row['auditScore'] is not None else '-'):>5}  "
                f"{(row['auditRounds'] if row['auditRounds'] is not None else '-'):>4}  "
                f"{(row.get('status') or '-'):<18}  "
                f"{(row.get('chapterType') or '-'):<14}  "
                f"{(row.get('mood') or '-'):<10}  "
                f"{row.get('title') or ''}"
            )

    if with_detection and payload.get("detection") is not None:
        d = payload["detection"]
        lines.append("")
        lines.append("  Detection:")
        if not d.get("present"):
            lines.append("    (no detection_history.json — module not yet ported)")
        elif d.get("count", 0) == 0:
            lines.append("    (history file present but empty)")
        else:
            lines.append(f"    runs        : {_fmt_int(d['count'])}")
            if "avgScore" in d:
                lines.append(f"    avg score   : {d['avgScore']}")
                lines.append(f"    min / max   : {d.get('minScore', '-')} / {d.get('maxScore', '-')}")
            if "scoreReductionTrend" in d:
                lines.append(f"    avg reduction: {d['scoreReductionTrend']}")
            if "passRate" in d:
                lines.append(f"    pass rate   : {_fmt_pct(d['passRate'])}")

    return "\n".join(lines)


# --------------------------- main ------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Compute analytics for one book (read-only, stdlib only).",
    )
    p.add_argument("--book", required=True,
                   help="Path to a book dir (the one containing book.json)")
    p.add_argument("--chapters", action="store_true",
                   help="Include per-chapter table")
    p.add_argument("--detection", action="store_true",
                   help="Include detection_history.json summary if present")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON instead of text")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir() or not (book_dir / "book.json").is_file():
        msg = {"error": f"not a book dir (missing book.json): {book_dir}"}
        print(json.dumps(msg, ensure_ascii=False), file=sys.stderr)
        return 1

    payload = compute_book_analytics(
        book_dir,
        with_chapters=args.chapters,
        with_detection=args.detection,
    )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_text(payload,
                          with_chapters=args.chapters,
                          with_detection=args.detection))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
