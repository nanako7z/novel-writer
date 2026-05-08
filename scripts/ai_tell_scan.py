#!/usr/bin/env python3
"""Scan text for AI-tell signals: hedge density, transition repetition,
surprise-marker overuse, analysis-term leakage, paragraph-length uniformity,
and list-like consecutive openings.

Outputs a JSON list of issues. Exit 0 always; severity in payload.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

HEDGE_WORDS_ZH = ["似乎", "可能", "或许", "大概", "某种程度上", "一定程度上", "在某种意义上"]
TRANSITION_WORDS_ZH = ["然而", "不过", "与此同时", "另一方面", "尽管如此", "话虽如此", "但值得注意的是"]
SURPRISE_MARKERS = ["仿佛", "忽然", "竟", "竟然", "猛地", "猛然", "不禁", "宛如"]

# Analysis-framework / methodology terms that LLMs leak from reasoning
# scratchpads into chapter prose. Three groups:
# (1) "report-style" reasoning labels (mirrors post_write_validate REPORT_TERMS;
#     duplicated here so both scanners flag at their respective stages).
# (2) Planner methodology terms — Planner is already forbidden from putting
#     these in chapter_memo (see references/phases/02-planner.md L181), but
#     LLMs still leak them into the prose body (Writer phase).
# (3) Narrative-craft framework terms that are educator vocabulary, not
#     storyteller vocabulary — flag any appearance in the body.
ANALYSIS_TERMS = [
    # group 1 — reasoning-report labels (sync with post_write_validate.REPORT_TERMS)
    "核心动机", "信息边界", "信息落差", "核心风险", "利益最大化",
    "当前处境", "行为约束", "性格过滤", "情绪外化", "锚定效应",
    "沉没成本", "认知共鸣", "推理框架",
    # group 2 — planner methodology (from references/cadence-policy.md / planner)
    "情绪缺口", "蓄压", "释放阶段", "后效阶段", "cyclePhase",
    "satisfactionPressure", "satisfactionType", "期待管理",
    # group 3 — narrative-craft educator terms
    "叙事张力", "叙事节奏", "叙事弧线", "人物弧光", "角色弧光",
    "三幕结构", "起承转合", "情节驱动", "戏剧反讽", "主题升华",
]

SENT_SPLIT = re.compile(r"[。！？\n]+")
PARA_SPLIT = re.compile(r"\n\s*\n")


def count_substr(text: str, term: str) -> int:
    if not term:
        return 0
    return text.count(term)


def positions_of(text: str, term: str) -> list[int]:
    out: list[int] = []
    start = 0
    while True:
        idx = text.find(term, start)
        if idx < 0:
            break
        out.append(idx)
        start = idx + len(term)
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Detect AI-style tells in text")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="path to text file")
    g.add_argument("--stdin", action="store_true")
    p.add_argument("--threshold-density", type=float, default=3.0,
                   help="hedge density threshold per 1000 chars (default 3.0)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    text = sys.stdin.read() if args.stdin else Path(args.file).read_text(encoding="utf-8")
    char_count = len(text)
    issues: list[dict] = []

    # Hedge density
    hedge_total = sum(count_substr(text, w) for w in HEDGE_WORDS_ZH)
    density = (hedge_total / char_count * 1000) if char_count else 0.0
    if density > args.threshold_density:
        issues.append({
            "severity": "warning",
            "category": "hedge-density",
            "description": f"模糊语密度 {density:.2f}/千字 超过阈值 {args.threshold_density}",
            "evidence": f"hedgeCount={hedge_total}, chars={char_count}",
        })

    # Transition repetition
    for w in TRANSITION_WORDS_ZH:
        c = count_substr(text, w)
        if c >= 3:
            issues.append({
                "severity": "warning",
                "category": "transition-repetition",
                "description": f"转折词「{w}」出现 {c} 次，建议替换或删除部分",
                "evidence": f"count={c}",
            })

    # Surprise markers — 铁律：每3000字最多1次
    surprise_total = sum(count_substr(text, w) for w in SURPRISE_MARKERS)
    allowed = max(1, char_count // 3000) if char_count else 1
    if surprise_total > allowed:
        issues.append({
            "severity": "warning",
            "category": "surprise-marker-overuse",
            "description": f"突兀词总计 {surprise_total} 次，超过每3000字允许的 {allowed} 次",
            "evidence": ", ".join(f"{w}:{count_substr(text, w)}" for w in SURPRISE_MARKERS
                                  if count_substr(text, w) > 0),
        })

    # Analysis terms in body — critical
    for term in ANALYSIS_TERMS:
        if term in text:
            issues.append({
                "severity": "critical",
                "category": "analysis-term-leak",
                "description": f"正文出现分析报告语「{term}」，应仅用于内部记录",
                "evidence": f"positions={positions_of(text, term)[:5]}",
            })

    # Paragraph length CV
    paras = [p.strip() for p in PARA_SPLIT.split(text) if p.strip()]
    if len(paras) >= 5:
        lens = [len(p) for p in paras]
        mean = sum(lens) / len(lens)
        if mean > 0:
            var = sum((l - mean) ** 2 for l in lens) / len(lens)
            cv = math.sqrt(var) / mean
            if cv < 0.15:
                issues.append({
                    "severity": "info",
                    "category": "paragraph-uniformity",
                    "description": f"段落长度 CV={cv:.3f} < 0.15，节奏过均匀",
                    "evidence": f"paragraphs={len(paras)}, mean={mean:.1f}",
                })

    # List-like consecutive sentence openers
    sentences = [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]
    run = 1
    max_run = 1
    run_head = ""
    max_head = ""
    for i in range(1, len(sentences)):
        a = sentences[i - 1][:2]
        b = sentences[i][:2]
        if a == b and a:
            run += 1
            run_head = a
        else:
            if run > max_run:
                max_run = run
                max_head = run_head
            run = 1
    if run > max_run:
        max_run = run
        max_head = run_head
    if max_run >= 3:
        issues.append({
            "severity": "info",
            "category": "list-like-openings",
            "description": f"检测到 {max_run} 个连续句首相同的句子（首字「{max_head}」），疑似列表化",
            "evidence": f"runLength={max_run}",
        })

    summary = f"chars={char_count}, issues={len(issues)}, " \
              f"critical={sum(1 for i in issues if i['severity'] == 'critical')}, " \
              f"warning={sum(1 for i in issues if i['severity'] == 'warning')}, " \
              f"info={sum(1 for i in issues if i['severity'] == 'info')}"

    print(json.dumps({"issues": issues, "summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
