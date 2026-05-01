#!/usr/bin/env python3
"""Three-tier sensitive-word scan (informational; SKILL decides whether to block).

POLITICAL hits → severity=block; SEXUAL/VIOLENCE → severity=warn.
Always exits 0; the `blocked` flag in output is set when any block-tier hit exists.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

POLITICAL = [
    "习近平", "毛泽东", "邓小平", "江泽民", "胡锦涛", "李克强", "温家宝",
    "共产党", "中国共产党", "国务院", "中南海", "政治局",
    "六四", "天安门事件", "八九民运",
    "法轮功", "李洪志",
    "台独", "藏独", "疆独", "港独",
    "维吾尔", "新疆集中营", "再教育营",
    "西藏独立", "达赖喇嘛",
    "文化大革命", "文革", "红卫兵",
    "翻墙", "VPN翻墙", "GFW",
    "反共", "反华", "颠覆国家政权",
]

SEXUAL = [
    "性交", "做爱", "口交", "肛交", "手淫", "自慰",
    "阴茎", "阴道", "睾丸", "阴蒂",
    "乳头", "乳房", "胸器",
    "强奸", "轮奸", "性侵", "猥亵",
    "卖淫", "嫖娼", "妓女",
]

VIOLENCE = [
    "肢解", "碎尸", "分尸", "凌迟",
    "活埋", "活剥", "剥皮",
    "斩首", "绞刑", "电刑",
    "酷刑", "拷打", "虐杀",
    "自杀方法", "自残教程",
]


def find_positions(text: str, term: str) -> list[int]:
    out: list[int] = []
    if not term:
        return out
    start = 0
    while True:
        idx = text.find(term, start)
        if idx < 0:
            break
        out.append(idx)
        start = idx + len(term)
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Three-tier sensitive content scan")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="path to text file")
    g.add_argument("--stdin", action="store_true")
    p.add_argument("--lang", default="zh", choices=["zh", "en"])
    return p.parse_args()


def scan_list(text: str, words: list[str], category: str, severity: str) -> list[dict]:
    hits: list[dict] = []
    for w in words:
        positions = find_positions(text, w)
        if positions:
            hits.append({
                "word": w,
                "severity": severity,
                "category": category,
                "positions": positions,
            })
    return hits


def main() -> int:
    args = parse_args()
    text = sys.stdin.read() if args.stdin else Path(args.file).read_text(encoding="utf-8")

    hits: list[dict] = []
    hits.extend(scan_list(text, POLITICAL, "political", "block"))
    hits.extend(scan_list(text, SEXUAL, "sexual", "warn"))
    hits.extend(scan_list(text, VIOLENCE, "violence", "warn"))

    blocked = any(h["severity"] == "block" for h in hits)
    cat_counts = {"political": 0, "sexual": 0, "violence": 0}
    for h in hits:
        cat_counts[h["category"]] = cat_counts.get(h["category"], 0) + 1
    summary = (f"hits={len(hits)} blocked={blocked} "
               f"political={cat_counts['political']} "
               f"sexual={cat_counts['sexual']} "
               f"violence={cat_counts['violence']}")

    print(json.dumps({
        "hits": hits,
        "blocked": blocked,
        "summary": summary,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
