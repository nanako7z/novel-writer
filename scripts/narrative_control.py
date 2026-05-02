#!/usr/bin/env python3
"""
narrative_control.py — sanitize "out-of-narrative" entities + soften AI-leaning
phrasing in text that Composer/Writer is about to embed in prompts.

Ported from inkos `utils/narrative-control.ts`. Two independent passes:

  1. --strip-entities  (default on)
       Replace hook IDs (H001, h12), chapter refs (第 12 章 / chapter 12),
       and hook slugs (kebab-case identifiers) with a neutral phrase
       ("这条线索" / "this thread", "此前" / "an earlier scene").
       Goal: prevent meta-text like hook IDs or chapter numbers from
       leaking into Writer-facing context.

  2. --soften         (default on)
       Apply small zh/en regex replacements that soften AI-leaning
       phrasing (仿佛→像, 似乎→像是, previous chapters→earlier scenes...).
       Ported verbatim from inkos source.

Both passes default ON. Disable with --no-strip-entities / --no-soften.

CLI:
    python narrative_control.py --file <text.md> [--lang zh|en] \\
        [--strip-entities|--no-strip-entities] \\
        [--soften|--no-soften] \\
        [--json] [--out <path>]

Output:
  default: print sanitized text to stdout
  --json:  print {originalLength, sanitizedLength, replacements: [...], sanitized}
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Tuple

# ---- patterns (verbatim from narrative-control.ts) ----

HOOK_ID_PATTERN = re.compile(r"\bH\d+\b", re.IGNORECASE)
HOOK_SLUG_PATTERN = re.compile(r"\b[a-z]+(?:-[a-z]+){1,3}\b")
CHAPTER_REF_PATTERNS: List[re.Pattern] = [
    re.compile(r"\bch(?:apter)?\s*\d+\b", re.IGNORECASE),
    re.compile(r"第\s*\d+\s*章"),
]

ZH_REPLACEMENTS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"前几章"), "此前"),
    (re.compile(r"本章要做的是"), "眼下要处理的是"),
    (re.compile(r"本章要做的"), "眼下要处理的"),
    (re.compile(r"仿佛"), "像"),
    (re.compile(r"似乎"), "像是"),
]

EN_REPLACEMENTS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bprevious chapters\b", re.IGNORECASE), "earlier scenes"),
    (re.compile(r"\bthis chapter needs to\b", re.IGNORECASE), "the current move is to"),
]


def sanitize(
    text: str,
    *,
    language: str = "zh",
    strip_entities: bool = True,
    soften: bool = True,
) -> Tuple[str, List[dict]]:
    """Return (sanitized, replacements_log)."""
    result = text
    log: List[dict] = []

    def _apply(pattern: re.Pattern, repl: str, label: str) -> None:
        nonlocal result
        count = len(pattern.findall(result))
        if count > 0:
            result = pattern.sub(repl, result)
            log.append({"pattern": label, "replacement": repl, "count": count})

    if strip_entities:
        thread_repl = "this thread" if language == "en" else "这条线索"
        chap_repl = "an earlier scene" if language == "en" else "此前"
        _apply(HOOK_ID_PATTERN, thread_repl, "hook_id (H\\d+)")
        _apply(HOOK_SLUG_PATTERN, thread_repl, "hook_slug (kebab)")
        for i, pat in enumerate(CHAPTER_REF_PATTERNS):
            _apply(pat, chap_repl, f"chapter_ref_{i}")

    if soften:
        replacements = (
            ZH_REPLACEMENTS + EN_REPLACEMENTS
            if language == "zh"
            else EN_REPLACEMENTS + ZH_REPLACEMENTS
        )
        for pat, repl in replacements:
            _apply(pat, repl, pat.pattern)

    return result, log


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sanitize out-of-narrative entities and soften AI phrasing."
    )
    parser.add_argument("--file", required=True, help="Path to input text/md file.")
    parser.add_argument("--lang", choices=["zh", "en"], default="zh")
    parser.add_argument(
        "--strip-entities",
        dest="strip_entities",
        action="store_true",
        default=True,
        help="(default on) Strip hook IDs / chapter refs / slugs.",
    )
    parser.add_argument(
        "--no-strip-entities",
        dest="strip_entities",
        action="store_false",
    )
    parser.add_argument(
        "--soften",
        dest="soften",
        action="store_true",
        default=True,
        help="(default on) Apply zh/en softening regex.",
    )
    parser.add_argument(
        "--no-soften",
        dest="soften",
        action="store_false",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON summary.")
    parser.add_argument("--out", help="Optional output path; default stdout.")
    args = parser.parse_args()

    src = Path(args.file)
    if not src.is_file():
        print(f"narrative_control: file not found: {src}", file=sys.stderr)
        return 2

    original = src.read_text(encoding="utf-8")
    sanitized, log = sanitize(
        original,
        language=args.lang,
        strip_entities=args.strip_entities,
        soften=args.soften,
    )

    if args.json:
        payload = {
            "originalLength": len(original),
            "sanitizedLength": len(sanitized),
            "language": args.lang,
            "stripEntities": args.strip_entities,
            "soften": args.soften,
            "replacements": log,
            "sanitized": sanitized,
        }
        out_text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        out_text = sanitized

    if args.out:
        Path(args.out).write_text(out_text, encoding="utf-8")
    else:
        sys.stdout.write(out_text)
        if not out_text.endswith("\n"):
            sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
