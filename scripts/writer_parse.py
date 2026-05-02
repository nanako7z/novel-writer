#!/usr/bin/env python3
"""
writer_parse.py — Writer output parser (sentinel + lenient fallback).

Ports inkos `agents/writer-parser.ts` to a stdlib-only CLI. Parses Writer's
structured response into the canonical fields downstream phases consume.

Sentinels recognized (verbatim from Writer's §14 OUTPUT FORMAT contract):

    === PRE_WRITE_CHECK ===
    === CHAPTER_TITLE ===
    === CHAPTER_CONTENT ===
    === POST_SETTLEMENT ===
    === UPDATED_STATE ===
    === UPDATED_LEDGER ===          (only when numericalSystem)
    === UPDATED_HOOKS ===
    === CHAPTER_SUMMARY ===
    === UPDATED_SUBPLOTS ===
    === UPDATED_EMOTIONAL_ARCS ===
    === UPDATED_CHARACTER_MATRIX ===
    === POST_WRITE_ERRORS ===       (optional — Writer self-reported violations)

Short aliases (TITLE / BODY / SUMMARY / POSTWRITE_ERRORS) are also accepted
for hand-crafted test fixtures.

CLI:
    python writer_parse.py --file <writer-output.md> [--strict] [--json]

Modes:
    default (lenient):
        missing CHAPTER_TITLE → infer from first H1 / first line
        missing CHAPTER_CONTENT → fall back to longest prose block
        missing CHAPTER_SUMMARY → null
    --strict:
        any missing required sentinel (CHAPTER_TITLE, CHAPTER_CONTENT) → exit 2

Exit codes:
    0  parse OK (lenient may have used fallback; check `lenient_fallback_used`)
    2  --strict + missing required sentinel, or unrecoverable (empty input)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

ALIAS = {
    "TITLE": "CHAPTER_TITLE",
    "BODY": "CHAPTER_CONTENT",
    "CONTENT": "CHAPTER_CONTENT",
    "SUMMARY": "CHAPTER_SUMMARY",
    "POSTWRITE_ERRORS": "POST_WRITE_ERRORS",
}

KNOWN_SENTINELS = (
    "PRE_WRITE_CHECK",
    "CHAPTER_TITLE",
    "CHAPTER_CONTENT",
    "POST_SETTLEMENT",
    "UPDATED_STATE",
    "UPDATED_LEDGER",
    "UPDATED_HOOKS",
    "CHAPTER_SUMMARY",
    "UPDATED_SUBPLOTS",
    "UPDATED_EMOTIONAL_ARCS",
    "UPDATED_CHARACTER_MATRIX",
    "POST_WRITE_ERRORS",
)
REQUIRED_STRICT = ("CHAPTER_TITLE", "CHAPTER_CONTENT")

SENTINEL_RE = re.compile(r"^===\s*([A-Z_]+)\s*===\s*$", re.MULTILINE)


# ---- helpers ----


def _normalize(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("﻿"):
        text = text[1:]
    return text


def _split_sentinels(text: str) -> tuple[dict[str, str], list[str]]:
    """Return (canonical_tag → body, raw_tags_found_in_order)."""
    matches = list(SENTINEL_RE.finditer(text))
    if not matches:
        return {}, []

    raw_found: list[str] = []
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        tag = m.group(1)
        canonical = ALIAS.get(tag, tag)
        raw_found.append(tag)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip("\n").strip()
        if canonical not in sections or not sections[canonical]:
            sections[canonical] = body
    return sections, raw_found


def _parse_postwrite_errors(block: str) -> list[str]:
    if not block.strip():
        return []
    out: list[str] = []
    for line in block.splitlines():
        s = line.strip()
        if not s:
            continue
        s = re.sub(r"^[-*+•]\s+", "", s)
        s = re.sub(r"^\d+[.)]\s+", "", s)
        if s:
            out.append(s)
    return out


def count_zh_chars(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


def count_en_words(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9'\-]+", text))


def word_count(text: str) -> int:
    zh = count_zh_chars(text)
    return zh if zh > 0 else count_en_words(text)


# ---- fallbacks (lenient mode) ----


def fallback_title(raw: str) -> Optional[str]:
    # Markdown heading: # 第N章 Title
    m = re.search(r"^#\s*第\s*\d+\s*章\s*[:：\s]?\s*(.+?)\s*$", raw, re.MULTILINE)
    if m:
        return m.group(1).strip()
    # # Chapter N: Title
    m = re.search(
        r"^#\s*Chapter\s+\d+\s*[:\-]?\s*(.+?)\s*$",
        raw, re.IGNORECASE | re.MULTILINE,
    )
    if m:
        return m.group(1).strip()
    # 章节标题：…
    m = re.search(r"(?:章节标题|CHAPTER_TITLE)[：:]\s*(.+)", raw)
    if m:
        return m.group(1).strip()
    # Generic H1
    m = re.search(r"^#\s+(.+?)\s*$", raw, re.MULTILINE)
    if m:
        return m.group(1).strip()
    # First non-empty line
    for line in raw.splitlines():
        s = line.strip().lstrip("#").strip()
        if s and not SENTINEL_RE.match(s):
            return s[:60]
    return None


def fallback_content(raw: str) -> str:
    # # 第N章 ... then prose
    m = re.search(r"^#\s*第\s*\d+\s*章[^\n]*\n+([\s\S]+)", raw, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(
        r"^#\s*Chapter\s+\d+(?::|\s+)([^\n]*)\n+([\s\S]+)",
        raw, re.MULTILINE | re.IGNORECASE,
    )
    if m:
        return m.group(2).strip()
    # 正文: / 内容: / 章节内容:
    m = re.search(r"(?:正文|内容|章节内容)[：:]\s*\n+([\s\S]+)", raw)
    if m:
        return m.group(1).strip()
    # Strip sentinels and tag-prefix lines
    kept = []
    for line in raw.splitlines():
        t = line.strip()
        if SENTINEL_RE.match(t):
            continue
        if re.match(r"^(PRE_WRITE_CHECK|CHAPTER_TITLE|章节标题|写作自检)[：:]", t):
            continue
        kept.append(line)
    result = "\n".join(kept).strip()
    return result if len(result) > 100 else ""


# ---- main parse ----


def parse(raw: str, strict: bool) -> tuple[dict, int]:
    text = _normalize(raw)
    if not text.strip():
        return ({"ok": False, "error": "empty input",
                 "raw_sentinels_found": []}, 2)

    sections, raw_found = _split_sentinels(text)

    title = sections.get("CHAPTER_TITLE", "").strip()
    body = sections.get("CHAPTER_CONTENT", "").strip()
    summary = sections.get("CHAPTER_SUMMARY", "").strip()
    pre_check = sections.get("PRE_WRITE_CHECK", "").strip()
    errors_raw = sections.get("POST_WRITE_ERRORS", "")
    post_write_errors = _parse_postwrite_errors(errors_raw) if errors_raw else []

    missing = [s for s in REQUIRED_STRICT if not sections.get(s)]

    if missing and strict:
        return ({
            "ok": False,
            "error": f"missing required sentinel(s): {', '.join(missing)}",
            "raw_sentinels_found": raw_found,
            "missing_required": missing,
        }, 2)

    fallback_used = False
    if not title:
        inferred = fallback_title(text)
        if inferred:
            title = inferred
            fallback_used = True
    if not body:
        inferred_body = fallback_content(text)
        if inferred_body:
            body = inferred_body
            fallback_used = True

    if not body:
        return ({
            "ok": False,
            "error": "could not extract body (no CHAPTER_CONTENT sentinel and lenient fallback failed)",
            "raw_sentinels_found": raw_found,
        }, 2)

    extras = {
        k: sections[k]
        for k in (
            "POST_SETTLEMENT", "UPDATED_STATE", "UPDATED_LEDGER",
            "UPDATED_HOOKS", "UPDATED_SUBPLOTS", "UPDATED_EMOTIONAL_ARCS",
            "UPDATED_CHARACTER_MATRIX",
        )
        if k in sections and sections[k].strip()
    }

    result = {
        "ok": True,
        "title": title,
        "body": body,
        "wordCount": word_count(body),
        "summary": summary if summary else None,
        "preWriteCheck": pre_check if pre_check else None,
        "postWriteErrors": post_write_errors,
        "extras": extras,
        "raw_sentinels_found": raw_found,
        "missing_required": missing,
        "lenient_fallback_used": fallback_used,
    }
    return (result, 0)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="writer_parse.py",
        description=(
            "Parse Writer LLM output (sentinel-delimited) into a JSON shape "
            "{title, body, wordCount, summary, postWriteErrors, ...}. "
            "Default mode is lenient; --strict requires CHAPTER_TITLE + CHAPTER_CONTENT."
        ),
    )
    parser.add_argument("--file", required=True,
                        help="Path to Writer raw output (markdown).")
    parser.add_argument("--strict", action="store_true",
                        help="Missing required sentinel → exit 2.")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON to stdout (default behavior; flag is for clarity).")
    args = parser.parse_args(argv)

    path = Path(args.file)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(json.dumps({"ok": False,
                          "error": f"file not found: {path}",
                          "raw_sentinels_found": []},
                         ensure_ascii=False))
        return 2
    except OSError as exc:
        print(json.dumps({"ok": False,
                          "error": f"read error: {exc}",
                          "raw_sentinels_found": []},
                         ensure_ascii=False))
        return 2

    try:
        result, code = parse(raw, strict=args.strict)
    except Exception as exc:
        print(json.dumps({"ok": False,
                          "error": f"parse exception: {exc}",
                          "raw_sentinels_found": []},
                         ensure_ascii=False))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
