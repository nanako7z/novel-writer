#!/usr/bin/env python3
"""Pure-text style fingerprint extractor.

Computes sentence/paragraph length stats, vocabulary diversity (TTR),
top sentence-opening patterns, and rhetorical features. Writes a
StyleProfile JSON. No LLM is called.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

SENT_SPLIT = re.compile(r"[。！？\n]+")
PARA_SPLIT = re.compile(r"\n\s*\n")
PUNCT_AND_DIGIT_RE = re.compile(r"[\s\n\r，。！？、：；“”‘’（）【】《》\d]+")

METAPHOR_RE = re.compile(r"[像如仿佛](?!.{0,3}的人)")
RHETORIC_QUESTION_RE = re.compile(r"(难道|岂|何|莫非)[^\n。！？]{0,20}[?？]")
EXAGGERATE_RE = re.compile(r"简直|何止|岂止|宛如|犹如")
PERSONIFY_RE = re.compile(r"[风云雨月花][^\n。！？]{0,4}[笑哭怒跳跑]")


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]


def split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in PARA_SPLIT.split(text) if p.strip()]


def avg_std(nums: list[int]) -> tuple[float, float]:
    if not nums:
        return 0.0, 0.0
    avg = sum(nums) / len(nums)
    var = sum((n - avg) ** 2 for n in nums) / len(nums)
    return avg, math.sqrt(var)


def vocab_ttr(text: str) -> float:
    cleaned = PUNCT_AND_DIGIT_RE.sub("", text)
    if not cleaned:
        return 0.0
    return len(set(cleaned)) / len(cleaned)


def top_openings(sentences: list[str], top_n: int = 5, min_count: int = 3) -> list[str]:
    openers = [s[:2] for s in sentences if len(s) >= 2]
    counts = Counter(openers)
    items = [(p, c) for p, c in counts.most_common() if c >= min_count]
    items = items[:top_n]
    return [f"{p}({c}次)" for p, c in items]


def detect_paralelism(sentences: list[str]) -> int:
    """Detect runs of >=3 consecutive sentences sharing the first 2 chars."""
    if len(sentences) < 3:
        return 0
    runs = 0
    i = 0
    while i < len(sentences):
        head = sentences[i][:2] if len(sentences[i]) >= 2 else ""
        j = i
        while j < len(sentences) and sentences[j][:2] == head and head:
            j += 1
        if j - i >= 3:
            runs += 1
        i = max(j, i + 1)
    return runs


def short_sentence_count(sentences: list[str], threshold: int = 8) -> int:
    return sum(1 for s in sentences if len(s) < threshold)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pure-text style fingerprint extractor")
    p.add_argument("--file", required=True, help="source text file")
    p.add_argument("--name", default=None, help="sourceName label")
    p.add_argument("--out", default="story/style_profile.json", help="output path")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    text = Path(args.file).read_text(encoding="utf-8")

    sentences = split_sentences(text)
    paragraphs = split_paragraphs(text)

    sent_lens = [len(s) for s in sentences]
    para_lens = [len(p) for p in paragraphs]

    s_avg, s_std = avg_std(sent_lens)
    p_avg, p_std = avg_std(para_lens)

    profile = {
        "sourceName": args.name or Path(args.file).name,
        "generatedAt": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "sentenceLength": {
            "avg": round(s_avg, 2),
            "stddev": round(s_std, 2),
            "count": len(sentences),
        },
        "paragraphLength": {
            "avg": round(p_avg, 2),
            "min": min(para_lens) if para_lens else 0,
            "max": max(para_lens) if para_lens else 0,
            "stddev": round(p_std, 2),
            "count": len(paragraphs),
        },
        "vocabularyDiversity": round(vocab_ttr(text), 4),
        "topOpeningPatterns": top_openings(sentences),
        "rhetoricalFeatures": {
            "比喻": len(METAPHOR_RE.findall(text)),
            "排比": detect_paralelism(sentences),
            "反问": len(RHETORIC_QUESTION_RE.findall(text)),
            "夸张": len(EXAGGERATE_RE.findall(text)),
            "拟人": len(PERSONIFY_RE.findall(text)),
            "短句节奏": short_sentence_count(sentences),
        },
    }

    out_p = Path(args.out)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(out_p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
