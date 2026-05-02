#!/usr/bin/env python3
"""Chapter splitter — find natural seams to cut an over-long draft into two.

When Writer drafts a chapter that's significantly over hardMax (e.g.,
target 3000, draft 6000), Normalizer's compression mode would have to
slash too much — risking loss of necessary scenes. This script does the
*deterministic preparation*: locate candidate seams (scene breaks, time
skips, POV changes, long pauses) and rank them.

This script is pure Python, stdlib only. The actual two-chapter rewrite
(new chapter_memo for chapter B + cliff polish for chapter A) requires
LLM and is left to Claude — see references/phases/08-normalizer.md
for the workflow.

(Optional LLM fallback for ambiguous drafts is documented but NOT
implemented here.)

CLI:
  python split_chapter.py --file <draft.md> --target <words> \\
      [--threshold-multiplier 1.5] [--min-paragraph-distance 5] \\
      [--mode zh|en] [--json]

Output: JSON with shouldSplit flag and ranked candidates.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ----- word counting (mirrors scripts/word_count.py) -----

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
    return len(re.findall(r"[A-Za-z0-9'\-]+", text))


def count_words(text: str, mode: str) -> int:
    text = strip_markdown(text)
    return count_zh(text) if mode == "zh" else count_en(text)


# ----- seam detection -----

# Time / place lead-ins commonly seen at scene breaks in 中文网文.
TIME_LEADINS = [
    r"次日", r"翌日", r"第二天", r"第三日", r"三日后", r"七日后", r"半月后",
    r"一月后", r"数日后", r"几日后", r"片刻后", r"良久", r"许久之后",
    r"清晨", r"黎明", r"破晓", r"天亮", r"日暮", r"傍晚", r"入夜", r"深夜",
    r"夜半", r"子时", r"卯时", r"晨时", r"午后", r"黄昏",
    r"与此同时", r"另一边", r"此时", r"此刻",
    r"回到", r"再说", r"且说", r"再看", r"话分两头",
]
TIME_LEAD_RE = re.compile(r"^(?:" + "|".join(TIME_LEADINS) + r")")

# Explicit dividers
DIVIDER_RE = re.compile(r"^\s*(?:[-]{3,}|[*]{3,}|[=]{3,}|＊{3,})\s*$")

# Long pause / sleep / silence / faint cues
PAUSE_KEYWORDS = [
    r"陷入沉默", r"沉沉睡去", r"昏睡过去", r"昏迷过去", r"昏厥",
    r"失去意识", r"昏了过去", r"沉入梦乡", r"进入梦境", r"闭目养神",
    r"打坐入定", r"运功疗伤", r"静修", r"闭关",
    r"夜色渐深", r"天色将晚", r"夜幕降临",
]
PAUSE_RE = re.compile("|".join(PAUSE_KEYWORDS))

# POV / character-name leading. We approximate by finding paragraphs that
# begin with a Chinese-name-shaped 2-4 char token followed by typical
# subject markers (verbs / particles).
NAME_LEAD_RE = re.compile(
    r"^([一-鿿]{2,4})(?=[，。、 \t]|[一-龥])"
)


@dataclass
class Paragraph:
    index: int             # 0-based paragraph index
    line: int              # 1-based line number where paragraph starts
    text: str              # raw paragraph text (no leading/trailing blank)
    words: int             # word count of paragraph (mode-aware)


def split_paragraphs(text: str, mode: str) -> list[Paragraph]:
    """Split body into paragraphs separated by one or more blank lines.

    Tracks line numbers so callers can point a human / LLM at the seam.
    """
    paragraphs: list[Paragraph] = []
    lines = text.splitlines()
    buf: list[str] = []
    para_start_line = 1
    cur_line = 1
    idx = 0

    def flush(start_line: int) -> None:
        nonlocal idx
        if not buf:
            return
        para_text = "\n".join(buf).strip()
        if para_text:
            paragraphs.append(Paragraph(
                index=idx,
                line=start_line,
                text=para_text,
                words=count_words(para_text, mode),
            ))
            idx += 1
        buf.clear()

    for ln in lines:
        if ln.strip() == "":
            flush(para_start_line)
            cur_line += 1
            para_start_line = cur_line
            continue
        if not buf:
            para_start_line = cur_line
        buf.append(ln)
        cur_line += 1
    flush(para_start_line)

    return paragraphs


def detect_seam_type(prev_para: Paragraph | None,
                     cur_para: Paragraph,
                     prev_text_blob: str) -> tuple[str, float] | None:
    """Return (seamType, baseScore) if cur_para starts a natural seam.

    baseScore is a 0..1 quality estimate; higher = clearer seam.
    """
    text = cur_para.text.lstrip()

    # Explicit divider line
    if DIVIDER_RE.match(text):
        return ("explicit-divider", 1.0)

    # Time / place lead-in at start of paragraph
    if TIME_LEAD_RE.match(text):
        return ("time-skip", 0.85)

    # Pause / faint keywords either ended the previous paragraph or open
    # this paragraph — interpret as natural breath point.
    if prev_para is not None and PAUSE_RE.search(prev_para.text[-30:]):
        return ("pause", 0.6)
    if PAUSE_RE.search(text[:30]):
        return ("pause", 0.55)

    # POV / character change: paragraph starts with a name token that did
    # *not* dominate the previous chunk.
    m = NAME_LEAD_RE.match(text)
    if m:
        name = m.group(1)
        # Heuristic: only treat as POV change if name appears scarcely in
        # the recent prev blob but plausibly in this paragraph.
        prev_count = prev_text_blob.count(name)
        if prev_count <= 1 and len(name) >= 2:
            return ("pov-change", 0.5)

    # Plain blank-line scene break (already implicit since paragraph
    # boundary). Only count it as a candidate if previous paragraph
    # ended with terminal punctuation, suggesting a real beat close.
    if prev_para is not None:
        tail = prev_para.text.rstrip()[-1:]
        if tail in "。！？.…":
            return ("scene-break", 0.35)

    return None


@dataclass
class Candidate:
    para_index: int
    line: int
    seam_type: str
    base_score: float
    left_words: int
    right_words: int
    distance_to_mid: float
    composite: float
    left_tail: str
    right_head: str


def score_and_rank(paragraphs: list[Paragraph],
                   total_words: int,
                   target: int,
                   min_para_distance: int) -> list[Candidate]:
    """Walk paragraph boundaries, emit candidates with composite score.

    Composite score = base_score - 0.5 * distance_to_mid_normalized, where
    distance_to_mid_normalized is how far (0..1) the seam is from the
    chapter mid-point in word terms.
    """
    candidates: list[Candidate] = []
    if len(paragraphs) < 2:
        return candidates

    cum_words: list[int] = []
    running = 0
    for p in paragraphs:
        running += p.words
        cum_words.append(running)

    half_target_floor = max(1, int(target * 0.5))

    # Build a rolling text blob of the prior portion for POV detection.
    for i in range(1, len(paragraphs)):
        if i < min_para_distance or (len(paragraphs) - i) < min_para_distance:
            # Too close to start/end — skip this seam.
            continue
        cur = paragraphs[i]
        prev = paragraphs[i - 1]
        left_words = cum_words[i - 1]
        right_words = total_words - left_words

        # Hard rule: both halves must clear half_target_floor.
        if left_words < half_target_floor or right_words < half_target_floor:
            continue

        # Build a small backward blob (~last 4 paragraphs) for POV check.
        back_start = max(0, i - 4)
        prev_blob = "\n".join(p.text for p in paragraphs[back_start:i])

        seam = detect_seam_type(prev, cur, prev_blob)
        if seam is None:
            continue
        seam_type, base = seam

        # Distance from mid (in word space, normalized 0..1).
        mid = total_words / 2
        dist = abs(left_words - mid) / max(mid, 1)
        composite = base - 0.5 * dist

        candidates.append(Candidate(
            para_index=i,
            line=cur.line,
            seam_type=seam_type,
            base_score=round(base, 3),
            left_words=left_words,
            right_words=right_words,
            distance_to_mid=round(dist, 3),
            composite=round(composite, 3),
            left_tail=_tail_preview(prev.text, 60),
            right_head=_head_preview(cur.text, 60),
        ))

    # Rank by composite descending; ties broken by closer to mid.
    candidates.sort(key=lambda c: (-c.composite, c.distance_to_mid))
    return candidates


def _tail_preview(s: str, n: int) -> str:
    s = s.replace("\n", " ").strip()
    return s[-n:] if len(s) > n else s


def _head_preview(s: str, n: int) -> str:
    s = s.replace("\n", " ").strip()
    return s[:n]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Find natural seams in an over-long chapter draft.",
    )
    p.add_argument("--file", required=True, help="path to draft markdown file")
    p.add_argument("--target", type=int, required=True,
                   help="target chapter word count (e.g. 3000)")
    p.add_argument("--threshold-multiplier", type=float, default=1.5,
                   help="trigger split only if currentWords >= target * X")
    p.add_argument("--min-paragraph-distance", type=int, default=5,
                   help="minimum paragraphs from start/end before a seam "
                        "is considered (default 5)")
    p.add_argument("--mode", default="zh", choices=["zh", "en"],
                   help="word-count mode (default zh)")
    p.add_argument("--top", type=int, default=3,
                   help="number of top candidates to return (default 3)")
    p.add_argument("--json", action="store_true",
                   help="emit JSON (default true; flag kept for symmetry)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    fp = Path(args.file)
    if not fp.is_file():
        print(json.dumps({"error": f"file not found: {fp}"},
                         ensure_ascii=False))
        return 1

    raw = fp.read_text(encoding="utf-8")
    total_words = count_words(raw, args.mode)
    threshold = args.target * args.threshold_multiplier

    if total_words < threshold:
        print(json.dumps({
            "shouldSplit": False,
            "currentWords": total_words,
            "target": args.target,
            "thresholdMultiplier": args.threshold_multiplier,
            "rationale": (
                f"current {total_words} < target * {args.threshold_multiplier} "
                f"= {int(threshold)}; Normalizer compression mode is "
                "appropriate, do not split."
            ),
        }, ensure_ascii=False, indent=2))
        return 0

    paragraphs = split_paragraphs(raw, args.mode)
    candidates = score_and_rank(
        paragraphs, total_words, args.target, args.min_paragraph_distance,
    )

    top = candidates[: args.top]

    if not top:
        print(json.dumps({
            "shouldSplit": True,
            "currentWords": total_words,
            "target": args.target,
            "candidates": [],
            "rationale": (
                "draft exceeds threshold but no clean paragraph-boundary "
                "seam was found; consider an LLM-assisted split or fall "
                "back to compress mode."
            ),
        }, ensure_ascii=False, indent=2))
        return 0

    out_candidates = []
    for rank, c in enumerate(top, start=1):
        out_candidates.append({
            "rank": rank,
            "line": c.line,
            "paragraphIndex": c.para_index,
            "seamType": c.seam_type,
            "baseScore": c.base_score,
            "compositeScore": c.composite,
            "distanceToMid": c.distance_to_mid,
            "leftWords": c.left_words,
            "rightWords": c.right_words,
            "preview": {
                "left_tail": c.left_tail,
                "right_head": c.right_head,
            },
        })

    print(json.dumps({
        "shouldSplit": True,
        "currentWords": total_words,
        "target": args.target,
        "thresholdMultiplier": args.threshold_multiplier,
        "candidates": out_candidates,
        "rationale": (
            f"current {total_words} >= target * {args.threshold_multiplier} "
            f"= {int(threshold)}; surfaced top {len(out_candidates)} "
            "natural seams. Hand off to Claude for cliff polish on chapter A "
            "and a fresh chapter_memo for chapter B."
        ),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
