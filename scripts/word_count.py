#!/usr/bin/env python3
"""Word/character counting with LengthSpec status reporting.

zh mode: count CJK characters.
en mode: split on whitespace, count tokens.
Markdown formatting (headings, fenced code, list bullets) is stripped first.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*`")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
LIST_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
HTML_RE = re.compile(r"<[^>]+>")


def strip_markdown(text: str) -> str:
    text = FENCE_RE.sub("", text)
    text = INLINE_CODE_RE.sub("", text)
    text = HEADING_RE.sub("", text)
    text = LIST_RE.sub("", text)
    text = HTML_RE.sub("", text)
    return text


def count_zh(text: str) -> int:
    return len(CJK_RE.findall(text))


def count_en(text: str) -> int:
    tokens = re.findall(r"[A-Za-z0-9'\-]+", text)
    return len(tokens)


def status_for(count: int, target, soft_min, soft_max, hard_min, hard_max) -> str:
    if hard_min is not None and count < hard_min:
        return "under-hard"
    if hard_max is not None and count > hard_max:
        return "over-hard"
    if soft_min is not None and count < soft_min:
        return "under-soft"
    if soft_max is not None and count > soft_max:
        return "over-soft"
    if soft_min is not None or soft_max is not None:
        return "in-soft"
    if hard_min is not None or hard_max is not None:
        return "in-hard"
    return "in-soft"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Word/char count with LengthSpec status")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="path to text file")
    g.add_argument("--stdin", action="store_true")
    p.add_argument("--mode", default="zh", choices=["zh", "en"])
    p.add_argument("--target", type=int, default=None)
    p.add_argument("--soft-min", type=int, default=None)
    p.add_argument("--soft-max", type=int, default=None)
    p.add_argument("--hard-min", type=int, default=None)
    p.add_argument("--hard-max", type=int, default=None)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.stdin:
        text = sys.stdin.read()
    else:
        text = Path(args.file).read_text(encoding="utf-8")
    text = strip_markdown(text)
    count = count_zh(text) if args.mode == "zh" else count_en(text)
    status = status_for(count, args.target, args.soft_min, args.soft_max,
                        args.hard_min, args.hard_max)
    print(json.dumps({
        "count": count,
        "mode": args.mode,
        "status": status,
        "target": args.target,
        "softMin": args.soft_min,
        "softMax": args.soft_max,
        "hardMin": args.hard_min,
        "hardMax": args.hard_max,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
