#!/usr/bin/env python3
"""Long-span (multi-chapter) fatigue scan.

Per-chapter ai_tell_scan catches repetition *within* a chapter; this script
catches repetition *across* the last `--window` chapters: same n-grams,
opening patterns, conflict shapes, character-pair overheating, and reuse of
the genre profile's `fatigueWords` list.

Advisory only — exits 0 always. Severity is in payload.

Usage:
    python fatigue_scan.py --book <bookDir> --current-chapter N \\
        [--window 5] [--min-repeat 2] [--genre-fatigue-words] \\
        [--draft <path>] [--json]

JSON output schema:
    {
      "currentChapter": N,
      "windowChapters": [N-5, ..., N-1],
      "issues": [
        {"severity": "critical|warning|info",
         "category": "fatigue-word|ngram|opening-pattern|conflict-trope|pair-overheat",
         "description": "...",
         "evidence": [{"chapter": M, "text": "..."}]}
      ],
      "summary": "..."
    }

See references/long-span-fatigue.md for the full rules table.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

# ─────────────────── constants ────────────────────────────────

# Particles / fillers that should not anchor n-gram detection.
ZH_STOP_CHARS = set("的了在是和与或及但而又却也不就都还很更最之以为对从向到把被让使有没")
ZH_PUNCT_RE = re.compile(r"[，。！？；：、""''（）《》【】\s\-—…·\.,!?;:\"'()\[\]<>]")

# Genre profile frontmatter pattern
GENRE_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
FATIGUE_WORDS_RE = re.compile(r"fatigueWords:\s*\[([^\]]+)\]")

# Conflict / dialogue heuristics
DIALOGUE_OPEN_RE = re.compile(r"[""].{1,80}?[""]")
ACTION_VERBS = ["冲", "扑", "斩", "砍", "打", "踢", "撞", "扔", "抓", "推", "挡", "跳", "逃", "追", "跑", "拔", "拉", "举", "摔"]
REVEAL_MARKERS = ["原来", "竟是", "其实", "真相", "终于明白", "才知道", "这才", "原是"]

# Opening sentence pattern markers
WEATHER_PATTERN = ["雨", "雪", "风", "云", "雷", "雾", "霾", "霜", "晴", "阴"]
TIME_PATTERN = ["晨", "晚", "夜", "黄昏", "正午", "清晨", "傍晚", "午后", "深夜", "黎明", "破晓"]
SOUND_PATTERN = ["声", "响", "鸣", "啸", "啼", "哗", "嗒", "吱"]

# Character-pair interaction patterns
INTERACTION_PATTERNS = {
    "bicker": ["吵", "怼", "顶嘴", "冷哼", "白了一眼", "翻白眼", "斥", "骂", "嘲讽"],
    "flirt": ["脸红", "心跳", "凑近", "贴近", "暧昧", "调笑", "轻笑", "眨眼", "靠近"],
    "threaten": ["杀", "威胁", "你信不信", "敢", "试试", "找死", "活不过", "抹脖子"],
}

PAIR_OVERHEAT_THRESHOLD = 3   # ≥ N consecutive chapters with same A→B pattern


# ─────────────────── IO helpers ───────────────────────────────


def load_chapter(book_dir: Path, chapter: int) -> str | None:
    p = book_dir / "chapters" / f"{chapter:04d}.md"
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return None


def load_genre_fatigue_words(book_dir: Path) -> list[str]:
    """Read book.json#genre then load templates/genres/<genre>.md frontmatter.

    Returns the `fatigueWords` array, or [] if missing / unparseable.
    """
    book_json = book_dir / "book.json"
    if not book_json.exists():
        return []
    try:
        bj = json.loads(book_json.read_text(encoding="utf-8"))
        genre = str(bj.get("genre", "")).strip()
    except Exception:
        return []
    if not genre:
        return []

    # Walk up to find SKILL_ROOT/templates/genres
    candidates: list[Path] = []
    here = Path(__file__).resolve().parent.parent
    candidates.append(here / "templates" / "genres" / f"{genre}.md")
    candidates.append(here / "templates" / "genres" / "other.md")
    # also accept project-level override
    candidates.insert(0, book_dir / "templates" / "genres" / f"{genre}.md")

    for path in candidates:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        m = GENRE_FRONTMATTER_RE.match(text)
        if not m:
            continue
        fm = m.group(1)
        fw = FATIGUE_WORDS_RE.search(fm)
        if not fw:
            continue
        raw = fw.group(1)
        words = [w.strip().strip('"').strip("'") for w in raw.split(",")]
        return [w for w in words if w]
    return []


# ─────────────────── helpers ──────────────────────────────────


def strip_punct(s: str) -> str:
    return ZH_PUNCT_RE.sub("", s)


def first_sentence(text: str) -> str:
    """First substantive sentence (skip headings)."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # split at first sentence terminator
        m = re.split(r"[。！？!?\.]", line, maxsplit=1)
        s = m[0].strip()
        if s:
            return s
    return ""


def opening_pattern(sentence: str) -> str:
    """Coarse classification of an opening sentence's pattern."""
    if not sentence:
        return "empty"
    head = sentence[:20]
    if any(w in head for w in WEATHER_PATTERN):
        return "weather"
    if any(w in head for w in TIME_PATTERN):
        return "time"
    if any(w in head for w in SOUND_PATTERN):
        return "sound"
    # quoted dialogue lead
    if head and head[0] in "\"'\"":
        return "dialogue"
    # action lead
    if any(v in head for v in ACTION_VERBS):
        return "action"
    return "other"


def ngrams(text: str, n: int) -> Iterable[str]:
    s = strip_punct(text)
    if len(s) < n:
        return
    for i in range(len(s) - n + 1):
        chunk = s[i:i + n]
        # reject if any char is a stop-char and chunk leads/trails with one
        if chunk[0] in ZH_STOP_CHARS or chunk[-1] in ZH_STOP_CHARS:
            continue
        # reject mostly-stop ngrams
        stop_ratio = sum(1 for c in chunk if c in ZH_STOP_CHARS) / n
        if stop_ratio > 0.4:
            continue
        # reject pure ASCII (English / numbers leak)
        if re.match(r"^[\x00-\x7f]+$", chunk):
            continue
        yield chunk


def dominant_dialogue_density(text: str) -> float:
    if not text.strip():
        return 0.0
    quoted = sum(len(m.group()) for m in DIALOGUE_OPEN_RE.finditer(text))
    return quoted / max(len(text), 1)


def conflict_shape(text: str) -> str:
    """Best-effort heuristic: classify dominant conflict shape."""
    has_action = any(v in text for v in ACTION_VERBS)
    has_reveal = any(m in text for m in REVEAL_MARKERS)
    dlg_density = dominant_dialogue_density(text)
    if has_action and has_reveal:
        return "fight-reveal"
    if has_action and dlg_density < 0.05:
        return "pure-fight"
    if has_reveal and dlg_density > 0.12:
        return "dialogue-reveal"
    if dlg_density > 0.18:
        return "dialogue-heavy"
    if has_action:
        return "action-light"
    return "narration"


def detect_pair_pattern(text: str) -> str | None:
    """Most prominent A→B interaction class in this chapter."""
    counts = {k: sum(text.count(t) for t in v) for k, v in INTERACTION_PATTERNS.items()}
    best = max(counts.items(), key=lambda kv: kv[1])
    return best[0] if best[1] >= 3 else None


# ─────────────────── detectors ────────────────────────────────


def detect_genre_fatigue_word_reuse(
    chapters: dict[int, str],
    genre_words: list[str],
    threshold: int,
) -> list[dict]:
    issues: list[dict] = []
    if not genre_words:
        return issues
    for word in genre_words:
        evidence: list[dict] = []
        total = 0
        for ch_num in sorted(chapters.keys()):
            text = chapters[ch_num]
            c = text.count(word)
            if c > 0:
                total += c
                evidence.append({"chapter": ch_num, "text": f"出现{c}次"})
        if total >= threshold and len(evidence) >= 2:
            severity = "warning" if total >= threshold + 1 else "info"
            issues.append({
                "severity": severity,
                "category": "fatigue-word",
                "description": f"题材疲劳词「{word}」在窗口内累计 {total} 次（横跨 {len(evidence)} 章），下章避让",
                "evidence": evidence[:5],
            })
    return issues


def detect_ngram_repetition(
    chapters: dict[int, str],
    min_repeat: int,
) -> list[dict]:
    """N-gram (3-5 char) appearing in ≥ min_repeat chapters."""
    issues: list[dict] = []
    # chapter-set per ngram for each n
    for n in (5, 4, 3):
        ngram_chapters: dict[str, set[int]] = defaultdict(set)
        # only count an ngram once per chapter
        for ch_num, text in chapters.items():
            seen: set[str] = set()
            for ng in ngrams(text, n):
                if ng in seen:
                    continue
                seen.add(ng)
                ngram_chapters[ng].add(ch_num)
        # filter: appears in ≥ min_repeat chapters
        suspects = sorted(
            ((ng, chs) for ng, chs in ngram_chapters.items() if len(chs) >= min_repeat),
            key=lambda kv: (-len(kv[1]), -len(kv[0])),
        )
        # only keep n-gram if not subsumed by a longer one already reported
        already_reported_substrings: list[str] = [i["evidence"][0]["text"] for i in issues if i["category"] == "ngram"]

        for ng, chs in suspects[:8]:  # cap per-n
            if any(ng in big for big in already_reported_substrings):
                continue
            evidence = [{"chapter": c, "text": ng} for c in sorted(chs)]
            severity = "warning" if len(chs) >= min_repeat + 1 else "info"
            issues.append({
                "severity": severity,
                "category": "ngram",
                "description": f"{n}-gram「{ng}」在 {len(chs)} 章中重复出现，建议下章替换",
                "evidence": evidence,
            })
    # cap total ngram issues to avoid noise
    out: list[dict] = []
    n_seen = 0
    for i in issues:
        if i["category"] == "ngram":
            if n_seen >= 6:
                continue
            n_seen += 1
        out.append(i)
    return out


def detect_opening_pattern_reuse(chapters: dict[int, str]) -> list[dict]:
    """Same opening pattern in 4+ consecutive chapters."""
    issues: list[dict] = []
    sorted_nums = sorted(chapters.keys())
    if len(sorted_nums) < 4:
        return issues
    pattern_seq = [(n, opening_pattern(first_sentence(chapters[n]))) for n in sorted_nums]
    # find longest run
    best_run: list[tuple[int, str]] = []
    cur_run: list[tuple[int, str]] = [pattern_seq[0]]
    for entry in pattern_seq[1:]:
        if entry[1] == cur_run[-1][1] and entry[1] not in ("empty", "other"):
            cur_run.append(entry)
        else:
            if len(cur_run) > len(best_run):
                best_run = cur_run[:]
            cur_run = [entry]
    if len(cur_run) > len(best_run):
        best_run = cur_run[:]
    if len(best_run) >= 4:
        evidence = [{"chapter": n, "text": first_sentence(chapters[n])[:40]} for n, _ in best_run]
        issues.append({
            "severity": "critical",
            "category": "opening-pattern",
            "description": f"连续 {len(best_run)} 章以「{best_run[0][1]}」型描写开篇，下章必须换入口",
            "evidence": evidence,
        })
    elif len(best_run) == 3:
        evidence = [{"chapter": n, "text": first_sentence(chapters[n])[:40]} for n, _ in best_run]
        issues.append({
            "severity": "warning",
            "category": "opening-pattern",
            "description": f"最近 3 章开篇都用「{best_run[0][1]}」型描写，注意下章换入口",
            "evidence": evidence,
        })
    return issues


def detect_conflict_trope_reuse(chapters: dict[int, str]) -> list[dict]:
    """Same conflict shape repeated 3+ times in the window."""
    issues: list[dict] = []
    if len(chapters) < 3:
        return issues
    shapes = [(n, conflict_shape(chapters[n])) for n in sorted(chapters.keys())]
    counts = Counter(s for _, s in shapes)
    most_common, freq = counts.most_common(1)[0]
    if freq >= 3 and most_common != "narration":
        evidence = [{"chapter": n, "text": s} for n, s in shapes if s == most_common]
        severity = "warning" if freq == 3 else "critical"
        issues.append({
            "severity": severity,
            "category": "conflict-trope",
            "description": f"窗口内 {freq} 章主结构都是「{most_common}」（动作/对话/揭示组合），下章换冲突形态",
            "evidence": evidence,
        })
    return issues


def detect_pair_overheat(chapters: dict[int, str]) -> list[dict]:
    """Same A→B interaction pattern in 3+ consecutive chapters."""
    issues: list[dict] = []
    sorted_nums = sorted(chapters.keys())
    pair_seq = [(n, detect_pair_pattern(chapters[n])) for n in sorted_nums]
    best_run: list[tuple[int, str]] = []
    cur_run: list[tuple[int, str]] = []
    for entry in pair_seq:
        if entry[1] is None:
            if len(cur_run) > len(best_run):
                best_run = cur_run[:]
            cur_run = []
            continue
        if not cur_run or cur_run[-1][1] == entry[1]:
            cur_run.append(entry)  # type: ignore[arg-type]
        else:
            if len(cur_run) > len(best_run):
                best_run = cur_run[:]
            cur_run = [entry]  # type: ignore[list-item]
    if len(cur_run) > len(best_run):
        best_run = cur_run[:]
    if len(best_run) >= PAIR_OVERHEAT_THRESHOLD:
        kind = best_run[0][1]
        evidence = [{"chapter": n, "text": kind} for n, _ in best_run]
        issues.append({
            "severity": "warning",
            "category": "pair-overheat",
            "description": f"连续 {len(best_run)} 章都是「{kind}」类互动主导，关系/情绪基调单调",
            "evidence": evidence,
        })
    return issues


# ─────────────────── main ─────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Long-span (multi-chapter) fatigue scan.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--book", required=True, help="book directory (contains chapters/)")
    p.add_argument("--current-chapter", type=int, required=True,
                   help="current chapter number; window covers N-window..N-1")
    p.add_argument("--window", type=int, default=5,
                   help="how many chapters back to scan (default 5)")
    p.add_argument("--min-repeat", type=int, default=2,
                   help="minimum chapters an n-gram must appear in to flag (default 2)")
    p.add_argument("--genre-fatigue-words", action="store_true",
                   help="also load and scan the genre profile's fatigueWords list")
    p.add_argument("--draft", help="optional path to current draft (counted as chapter N)")
    p.add_argument("--json", action="store_true", help="output JSON (default true)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        print(json.dumps({
            "error": f"book dir not found: {book_dir}",
            "issues": [],
            "summary": "error",
        }, ensure_ascii=False, indent=2))
        return 0

    cur = args.current_chapter
    win = max(1, args.window)
    window_nums = list(range(max(1, cur - win), cur))

    chapters: dict[int, str] = {}
    for n in window_nums:
        body = load_chapter(book_dir, n)
        if body:
            chapters[n] = body

    if args.draft:
        try:
            draft_text = Path(args.draft).read_text(encoding="utf-8")
            chapters[cur] = draft_text
        except Exception:
            pass

    issues: list[dict] = []

    if args.genre_fatigue_words:
        genre_words = load_genre_fatigue_words(book_dir)
        issues.extend(detect_genre_fatigue_word_reuse(chapters, genre_words, threshold=3))

    if len(chapters) >= 2:
        issues.extend(detect_ngram_repetition(chapters, args.min_repeat))
        issues.extend(detect_opening_pattern_reuse(chapters))
        issues.extend(detect_conflict_trope_reuse(chapters))
        issues.extend(detect_pair_overheat(chapters))

    crit = sum(1 for i in issues if i["severity"] == "critical")
    warn = sum(1 for i in issues if i["severity"] == "warning")
    info = sum(1 for i in issues if i["severity"] == "info")

    summary = (
        f"window={sorted(chapters.keys())}, "
        f"issues={len(issues)} (critical={crit}, warning={warn}, info={info})"
    )

    out = {
        "currentChapter": cur,
        "windowChapters": sorted(chapters.keys()),
        "issues": issues,
        "summary": summary,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
