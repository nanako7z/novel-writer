#!/usr/bin/env python3
"""
post_write_validate.py — deterministic per-chapter validator.

Ports key checks from inkos `agents/post-write-validator.ts`:

  - chapter ref / number consistency (filename + frontmatter vs body refs)
  - paragraph shape (orphan short paragraphs, walls of text, monolithic blocks)
  - dialogue punctuation (paired Chinese quotes; said/spoke spacing)
  - 章节号 references inside body ("第N章", "Chapter N")
  - hard prohibitions ("不是…而是…", "——")
  - meta-narration / report-term / sermon / collective-shock markers
  - chapter title format (single H1, single line)
  - annotation / template leakage ([作者按], <TODO>, leftover sentinels)
  - length sanity (zero / extremely short / extremely long)
  - character name spelling consistency vs story/character_matrix.md (when --book)

CLI:
    python post_write_validate.py --file <chapter.md> [--chapter N] [--book <dir>] [--strict] [--json]

Exit codes:
    0  no critical issues (warnings allowed unless --strict)
    2  one or more critical issues (or --strict + any warning)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

# ---- markers / patterns ported from post-write-validator.ts ----

SURPRISE_MARKERS = ["仿佛", "忽然", "竟然", "猛地", "猛然", "不禁", "宛如"]

META_NARRATION_PATTERNS = [
    r"到这里[，,]?算是",
    r"接下来[，,]?(?:就是|将会|即将)",
    r"(?:后面|之后)[，,]?(?:会|将|还会)",
    r"(?:故事|剧情)(?:发展)?到了",
    r"读者[，,]?(?:可能|应该|也许)",
    r"我们[，,]?(?:可以|不妨|来看)",
]

REPORT_TERMS = [
    "核心动机", "信息边界", "信息落差", "核心风险", "利益最大化",
    "当前处境", "行为约束", "性格过滤", "情绪外化", "锚定效应",
    "沉没成本", "认知共鸣",
]

SERMON_WORDS = ["显然", "毋庸置疑", "不言而喻", "众所周知", "不难看出"]

COLLECTIVE_SHOCK_PATTERNS = [
    r"(?:全场|众人|所有人|在场的人)[，,]?(?:都|全|齐齐|纷纷)?(?:震惊|惊呆|倒吸凉气|目瞪口呆|哗然|惊呼)",
    r"(?:全场|一片)[，,]?(?:寂静|哗然|沸腾|震动)",
]

# Annotation / template leakage patterns
ANNOTATION_PATTERNS = [
    r"\[作者按[^\]]*\]",
    r"\[作者注[^\]]*\]",
    r"\[TODO[^\]]*\]",
    r"<TODO[^>]*>",
    r"<!--[\s\S]*?-->",
    r"【作者[^】]*】",
]
SENTINEL_LEAK_RE = re.compile(r"^=== [A-Z_]+ ===$", re.MULTILINE)

CHAPTER_REF_RE = re.compile(r"(?:第\s*\d+\s*章|[Cc]hapter\s+\d+)")

# Paragraph shape thresholds (Chinese).
SHORT_THRESHOLD = 40  # was 35; tightened by inkos commit 6e47112 (40 chars ≈ 2 mobile lines)
LONG_PARAGRAPH = 300
SHORT_CAP_PER_CHAPTER = 5  # inkos commit 6e47112 — at most 5 short-paragraph "punches" per chapter
CONSECUTIVE_SHORT_LIMIT = 2  # inkos commit b1c7089 / ab39bd6 — 3+ consecutive shorts → critical
WALL_OF_TEXT = 600          # critical: clearly too long for mobile reading
MONOLITHIC_THRESHOLD = 1200  # critical: a single paragraph this big is a render bug

# Length sanity bands (chars, post-stripped).
ABS_MIN_CHARS = 200
ABS_MAX_CHARS = 20000

# ---- helpers ----


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, body


def _line_of_offset(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _line_of_match(text: str, pattern: str, flags: int = 0) -> Optional[int]:
    m = re.search(pattern, text, flags)
    return _line_of_offset(text, m.start()) if m else None


def _line_of_substring(text: str, needle: str) -> Optional[int]:
    idx = text.find(needle)
    return _line_of_offset(text, idx) if idx >= 0 else None


def _evidence(text: str, offset: int, span: int = 40) -> str:
    start = max(0, offset - 8)
    end = min(len(text), offset + span)
    return text[start:end].replace("\n", " ").strip()


def _extract_paragraphs(body: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    line = 1
    buf: list[str] = []
    buf_start = 1
    for raw in body.splitlines(keepends=False):
        stripped = raw.strip()
        if stripped == "":
            if buf:
                joined = "\n".join(buf).strip()
                if joined and joined != "---" and not joined.startswith("#"):
                    out.append((joined, buf_start))
                buf = []
            line += 1
            continue
        if not buf:
            buf_start = line
        buf.append(raw)
        line += 1
    if buf:
        joined = "\n".join(buf).strip()
        if joined and joined != "---" and not joined.startswith("#"):
            out.append((joined, buf_start))
    return out


def _is_dialogue(p: str) -> bool:
    s = p.lstrip()
    if not s:
        return False
    return s[0] in "“”\"「『'《" or s.startswith("——")


def _load_character_names(book_dir: Path) -> list[str]:
    matrix = book_dir / "story" / "character_matrix.md"
    text = _read(matrix)
    if not text:
        return []
    names: list[str] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(\S[^\n]*?)\s*$", line)
        if m:
            name = m.group(1).strip().rstrip("：:")
            if name and len(name) <= 12:
                names.append(name)
    return names


# ---- individual checks ----


def check_chapter_title_format(body: str) -> list[dict]:
    issues: list[dict] = []
    h1s = [(m.group(0), _line_of_offset(body, m.start()))
           for m in re.finditer(r"^#\s+[^\n]+", body, re.MULTILINE)]
    if not h1s:
        # Not always required — only warn, not critical
        issues.append({
            "severity": "info",
            "category": "title-format",
            "description": "未找到 H1 章节标题（应以 `# 标题` 单行起首）",
            "line": 1,
            "evidence": "",
        })
    elif len(h1s) > 1:
        issues.append({
            "severity": "warning",
            "category": "title-format",
            "description": f"出现多个 H1 标题（共 {len(h1s)} 个），章节内只允许 1 个",
            "line": h1s[1][1],
            "evidence": h1s[1][0][:60],
        })
    return issues


def check_chapter_ref_consistency(
    body: str, chapter_number: Optional[int], frontmatter: dict, file_stem: str,
) -> list[dict]:
    issues: list[dict] = []

    if chapter_number is not None:
        # Frontmatter chapter number vs CLI
        fm_ch = frontmatter.get("chapter") or frontmatter.get("chapter_number")
        if fm_ch:
            try:
                fm_n = int(re.sub(r"\D", "", fm_ch) or "-1")
                if fm_n != chapter_number:
                    issues.append({
                        "severity": "critical",
                        "category": "chapter-ref",
                        "description": f"frontmatter 章节号 {fm_n} 与 --chapter {chapter_number} 不一致",
                        "line": 1,
                        "evidence": f"chapter: {fm_ch}",
                    })
            except ValueError:
                pass

        # Filename consistency
        digits = re.sub(r"\D", "", file_stem)
        if digits:
            try:
                file_n = int(digits)
                if file_n != chapter_number:
                    issues.append({
                        "severity": "critical",
                        "category": "chapter-ref",
                        "description": f"文件名 {file_stem!r} 数字 {file_n} 与 --chapter {chapter_number} 不一致",
                        "line": 1,
                        "evidence": file_stem,
                    })
            except ValueError:
                pass

    # In-body chapter references (角色不知道自己在第几章)
    refs = CHAPTER_REF_RE.findall(body)
    if refs:
        unique = sorted(set(refs))
        m = CHAPTER_REF_RE.search(body)
        line = _line_of_offset(body, m.start()) if m else 1
        desc = f"正文中出现章节号指称：{ '、'.join(unique) }。角色不知道自己在第几章。"
        if chapter_number is not None:
            for ref in unique:
                num_match = re.search(r"\d+", ref)
                if num_match and int(num_match.group(0)) != chapter_number:
                    desc += f"（且 {ref} 与本章 N={chapter_number} 不一致）"
                    break
        issues.append({
            "severity": "critical",
            "category": "chapter-ref",
            "description": desc,
            "line": line,
            "evidence": _evidence(body, m.start()) if m else "",
        })

    return issues


def check_paragraph_shape(body: str) -> list[dict]:
    issues: list[dict] = []
    paragraphs = _extract_paragraphs(body)

    # Single-paragraph chapter — almost always a render bug.
    if len(paragraphs) <= 1 and len(body.strip()) > 600:
        issues.append({
            "severity": "critical",
            "category": "paragraph-shape",
            "description": "全章只有 1 段——段落分隔丢失（疑似一整章一个 block）",
            "line": 1,
            "evidence": "",
        })
        return issues

    if len(paragraphs) < 4:
        # Still check monolithic
        for p, ln in paragraphs:
            if len(p) >= MONOLITHIC_THRESHOLD:
                issues.append({
                    "severity": "critical",
                    "category": "paragraph-shape",
                    "description": f"段落长 {len(p)} 字，超过整段阈值 {MONOLITHIC_THRESHOLD}——疑似断段错误",
                    "line": ln,
                    "evidence": p[:60],
                })
        return issues

    narrative = [(p, ln) for p, ln in paragraphs if not _is_dialogue(p)]
    short = [(p, ln) for p, ln in narrative if len(p) < SHORT_THRESHOLD]
    long_paragraphs = [(p, ln) for p, ln in paragraphs if len(p) > LONG_PARAGRAPH]
    walls = [(p, ln) for p, ln in paragraphs if len(p) > WALL_OF_TEXT]
    monoliths = [(p, ln) for p, ln in paragraphs if len(p) >= MONOLITHIC_THRESHOLD]

    short_ratio = (len(short) / len(narrative)) if narrative else 0
    # Inkos commit 6e47112: 60%+ narrative paragraphs under 40 chars → critical
    # (was previously a softer warning at 35-char threshold).
    if short_ratio >= 0.6 and len(short) >= 4:
        issues.append({
            "severity": "critical",
            "category": "paragraph-shape",
            "description": (
                f"{len(paragraphs)} 段中 {len(short)} 段短于 {SHORT_THRESHOLD} 字，"
                f"段落被切得过碎（短段比 {short_ratio:.0%}，硬阈值 60%）"
            ),
            "line": short[0][1],
            "evidence": short[0][0][:40],
        })

    # Inkos commit 6e47112: short-paragraph cap of 5 per chapter — three legit
    # use cases (opening 300-char reversal / chapter-end hook / impact moments)
    # combine to ≤ 5 total. Above that = telegraphic stacking.
    if len(short) > SHORT_CAP_PER_CHAPTER:
        issues.append({
            "severity": "warning",
            "category": "paragraph-shape",
            "description": (
                f"短段（< {SHORT_THRESHOLD} 字）共 {len(short)} 段，超过单章上限 "
                f"{SHORT_CAP_PER_CHAPTER}——开篇反转 / 章末钩子 / ≤3 次冲击瞬间合计"
                f"才允许 5 段，多了就是电报体堆砌"
            ),
            "line": short[0][1],
            "evidence": short[0][0][:40],
        })

    # consecutive-short — inkos commit b1c7089/ab39bd6: 3+ in a row → critical
    # (CONSECUTIVE_SHORT_LIMIT = 2 means "two in a row is the ceiling"; the 3rd
    # paragraph must be ≥ 60 chars per the commit's hard rule).
    max_streak = 0
    streak = 0
    streak_start_line = 1
    streak_start_for_max = 1
    for p, ln in narrative:
        if len(p) < SHORT_THRESHOLD:
            if streak == 0:
                streak_start_line = ln
            streak += 1
            if streak > max_streak:
                max_streak = streak
                streak_start_for_max = streak_start_line
        else:
            streak = 0
    if max_streak > CONSECUTIVE_SHORT_LIMIT:
        issues.append({
            "severity": "critical",
            "category": "paragraph-shape",
            "description": (
                f"连续出现 {max_streak} 个短段（< {SHORT_THRESHOLD} 字），违反"
                f"“连续短段最多 {CONSECUTIVE_SHORT_LIMIT}”硬规则——第 3 段必须"
                f"60 字以上带动作 / 情绪 / 描写"
            ),
            "line": streak_start_for_max,
            "evidence": "",
        })

    if len(long_paragraphs) >= 2:
        issues.append({
            "severity": "warning",
            "category": "paragraph-shape",
            "description": f"{len(long_paragraphs)} 个段落超过 {LONG_PARAGRAPH} 字，不适合手机阅读",
            "line": long_paragraphs[0][1],
            "evidence": long_paragraphs[0][0][:40],
        })

    if walls:
        issues.append({
            "severity": "critical",
            "category": "paragraph-shape",
            "description": f"{len(walls)} 个段落超过 {WALL_OF_TEXT} 字，必须拆分",
            "line": walls[0][1],
            "evidence": walls[0][0][:40],
        })

    if monoliths:
        issues.append({
            "severity": "critical",
            "category": "paragraph-shape",
            "description": (
                f"{len(monoliths)} 个段落超过整段阈值 {MONOLITHIC_THRESHOLD} 字"
                "——疑似断段丢失或一段塞满整章"
            ),
            "line": monoliths[0][1],
            "evidence": monoliths[0][0][:40],
        })

    return issues


def check_dialogue_punctuation(body: str) -> list[dict]:
    issues: list[dict] = []
    open_curly = body.count("“")
    close_curly = body.count("”")
    if open_curly != close_curly:
        line = _line_of_substring(body, "“") or _line_of_substring(body, "”") or 1
        issues.append({
            "severity": "critical",
            "category": "dialogue",
            "description": f"中文引号不成对：开 {open_curly} / 闭 {close_curly}",
            "line": line,
            "evidence": "",
        })

    open_square = body.count("「")
    close_square = body.count("」")
    if open_square != close_square:
        line = _line_of_substring(body, "「") or _line_of_substring(body, "」") or 1
        issues.append({
            "severity": "warning",
            "category": "dialogue",
            "description": f"日式直角引号不成对：开 {open_square} / 闭 {close_square}",
            "line": line,
            "evidence": "",
        })

    # Half-width quote \" sandwiched between CJK chars — looks like a bad
    # paste from a model that can't emit curly quotes.
    straight_count = len(re.findall(r'(?<=[一-鿿])"|"(?=[一-鿿])', body))
    if straight_count >= 2:
        m = re.search(r'(?<=[一-鿿])"|"(?=[一-鿿])', body)
        issues.append({
            "severity": "warning",
            "category": "dialogue",
            "description": f"检测到 {straight_count} 处半角引号 \" 紧贴中文，应改为中文引号 “…”",
            "line": _line_of_offset(body, m.start()) if m else 1,
            "evidence": _evidence(body, m.start()) if m else "",
        })

    if re.search(r"[”」]\s+(说|道|问|答|喊|叫|笑)", body):
        line = _line_of_match(body, r"[”」]\s+(说|道|问|答|喊|叫|笑)") or 1
        issues.append({
            "severity": "info",
            "category": "dialogue",
            "description": "对话引号后出现空格再接「说/道/问/答/喊/叫/笑」，建议直接相连",
            "line": line,
            "evidence": "",
        })

    return issues


_VALID_SEVERITIES = ("critical", "warning", "info", "off")


def _read_em_dash_severity(book_dir: Optional[Path]) -> str:
    """Read book.json#postWriteRules.emDashSeverity. Default: 'warning'.

    Em-dash 「——」 is a legal CJK punctuation mark; the original hard ban (critical)
    was empirically too strict — multiple already-published chapters of horror /
    suspense books use it freely.  Default is now 'warning'; per-book override
    via book.json#postWriteRules.emDashSeverity = 'critical' | 'warning' | 'info' | 'off'.
    cf. plan A12.
    """
    default = "warning"
    if book_dir is None:
        return default
    book_json = book_dir / "book.json"
    if not book_json.is_file():
        return default
    try:
        data = json.loads(book_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default
    rules = data.get("postWriteRules", {})
    sev = rules.get("emDashSeverity", default)
    if sev not in _VALID_SEVERITIES:
        return default
    return sev


def check_hard_prohibitions(body: str, book_dir: Optional[Path] = None) -> list[dict]:
    issues: list[dict] = []
    m = re.search(r"不是[^，。！？\n]{0,30}[，,]?\s*而是", body)
    if m:
        issues.append({
            "severity": "critical",
            "category": "forbidden-pattern",
            "description": "出现「不是……而是……」句式（硬性禁令）",
            "line": _line_of_offset(body, m.start()),
            "evidence": _evidence(body, m.start()),
        })
    em_dash_sev = _read_em_dash_severity(book_dir)
    if em_dash_sev != "off" and "——" in body:
        line = _line_of_substring(body, "——") or 1
        count = body.count("——")
        issues.append({
            "severity": em_dash_sev,
            "category": "forbidden-pattern",
            "description": (
                f"出现破折号「——」（{count} 处；默认 warning，可经 "
                f"book.json#postWriteRules.emDashSeverity 调整为 critical/info/off）"
            ),
            "line": line,
            "evidence": _evidence(body, body.find("——")),
        })
    return issues


def check_markers(body: str) -> list[dict]:
    issues: list[dict] = []

    # Surprise-marker density
    total = 0
    breakdown: dict[str, int] = {}
    for w in SURPRISE_MARKERS:
        c = len(re.findall(re.escape(w), body))
        if c:
            breakdown[w] = c
            total += c
    limit = max(1, len(body) // 3000)
    if total > limit:
        detail = "、".join(f'"{w}"×{c}' for w, c in breakdown.items())
        issues.append({
            "severity": "warning",
            "category": "ai-tell",
            "description": f"转折/惊讶标记词共 {total} 次（上限 {limit} 次/{len(body)} 字）：{detail}",
            "line": 1,
            "evidence": "",
        })

    found_terms = [t for t in REPORT_TERMS if t in body]
    if found_terms:
        line = _line_of_substring(body, found_terms[0]) or 1
        issues.append({
            "severity": "critical",
            "category": "report-terms",
            "description": f"正文中出现分析报告术语：{ '、'.join(repr(t) for t in found_terms) }",
            "line": line,
            "evidence": _evidence(body, body.find(found_terms[0])),
        })

    for pat in META_NARRATION_PATTERNS:
        m = re.search(pat, body)
        if m:
            issues.append({
                "severity": "warning",
                "category": "meta-narration",
                "description": f"出现编剧旁白式表述：「{m.group(0)}」",
                "line": _line_of_offset(body, m.start()),
                "evidence": _evidence(body, m.start()),
            })
            break

    found_sermons = [w for w in SERMON_WORDS if w in body]
    if found_sermons:
        line = _line_of_substring(body, found_sermons[0]) or 1
        issues.append({
            "severity": "warning",
            "category": "sermon",
            "description": f"出现说教词：{ '、'.join(repr(w) for w in found_sermons) }",
            "line": line,
            "evidence": "",
        })

    for pat in COLLECTIVE_SHOCK_PATTERNS:
        m = re.search(pat, body)
        if m:
            issues.append({
                "severity": "warning",
                "category": "collective-shock",
                "description": f"出现集体反应套话：「{m.group(0)}」",
                "line": _line_of_offset(body, m.start()),
                "evidence": _evidence(body, m.start()),
            })
            break

    # consecutive 了
    sentences = [s.strip() for s in re.split(r"[。！？]", body) if len(s.strip()) > 2]
    cur = 0
    mx = 0
    for s in sentences:
        if "了" in s:
            cur += 1
            mx = max(mx, cur)
        else:
            cur = 0
    if mx >= 6:
        issues.append({
            "severity": "warning",
            "category": "rhythm",
            "description": f'连续 {mx} 句包含"了"字，节奏拖沓',
            "line": 1,
            "evidence": "",
        })

    return issues


def check_annotation_leak(body: str) -> list[dict]:
    issues: list[dict] = []
    for pat in ANNOTATION_PATTERNS:
        for m in re.finditer(pat, body):
            issues.append({
                "severity": "critical",
                "category": "annotation-leak",
                "description": f'正文中残留批注/模板：「{m.group(0)[:40]}」',
                "line": _line_of_offset(body, m.start()),
                "evidence": _evidence(body, m.start()),
            })
    for m in SENTINEL_LEAK_RE.finditer(body):
        issues.append({
            "severity": "critical",
            "category": "annotation-leak",
            "description": f'正文中残留 Writer 输出 sentinel：「{m.group(0)}」',
            "line": _line_of_offset(body, m.start()),
            "evidence": m.group(0),
        })
    return issues


def check_length(body: str) -> tuple[list[dict], int]:
    issues: list[dict] = []
    cjk = len(re.findall(r"[一-鿿]", body))
    char_len = len(body.strip())
    use_len = cjk if cjk > 0 else char_len
    if use_len < ABS_MIN_CHARS:
        issues.append({
            "severity": "critical",
            "category": "length",
            "description": f"全文仅 {use_len} 字，低于绝对下限 {ABS_MIN_CHARS}——疑似空稿或被截断",
            "line": 1,
            "evidence": "",
        })
    elif use_len > ABS_MAX_CHARS:
        issues.append({
            "severity": "warning",
            "category": "length",
            "description": f"全文 {use_len} 字，超过单章绝对上限 {ABS_MAX_CHARS}",
            "line": 1,
            "evidence": "",
        })
    return issues, use_len


def check_character_consistency(body: str, book_dir: Path) -> list[dict]:
    issues: list[dict] = []
    names = _load_character_names(book_dir)
    if not names:
        return issues
    name_set = set(names)
    seen_variants: dict[str, set[str]] = {}
    for canonical in names:
        if len(canonical) < 2:
            continue
        for suffix in ("儿", "哥", "姐", "爷", "君", "公子"):
            variant = canonical + suffix
            if variant in body and variant not in name_set:
                seen_variants.setdefault(canonical, set()).add(variant)
        if len(canonical) >= 3:
            stem = canonical[:-1]
            if stem in body and stem not in name_set and canonical in body:
                seen_variants.setdefault(canonical, set()).add(stem)
    for canonical, variants in seen_variants.items():
        if not variants:
            continue
        first = next(iter(variants))
        line = _line_of_substring(body, first) or 1
        issues.append({
            "severity": "warning",
            "category": "character-consistency",
            "description": (
                f"角色「{canonical}」出现疑似名称变体：{ '、'.join(sorted(variants)) }；"
                "请确认是否同一角色"
            ),
            "line": line,
            "evidence": "",
        })
    return issues


# ---- driver ----


def validate(
    raw: str,
    chapter_number: Optional[int],
    file_stem: str,
    book_dir: Optional[Path],
) -> dict:
    frontmatter, body = _split_frontmatter(raw)
    if not body.strip():
        return {
            "ok": False,
            "issues": [{
                "severity": "critical",
                "category": "length",
                "description": "章节正文为空",
                "line": 1,
                "evidence": "",
            }],
            "summary": "chars=0 paragraphs=0 critical=1 warning=0",
        }

    issues: list[dict] = []
    length_issues, char_count = check_length(body)
    issues += length_issues
    issues += check_chapter_title_format(body)
    issues += check_chapter_ref_consistency(body, chapter_number, frontmatter, file_stem)
    issues += check_paragraph_shape(body)
    issues += check_dialogue_punctuation(body)
    issues += check_hard_prohibitions(body, book_dir)
    issues += check_markers(body)
    issues += check_annotation_leak(body)
    if book_dir is not None:
        issues += check_character_consistency(body, book_dir)

    paragraphs = _extract_paragraphs(body)
    n_crit = sum(1 for i in issues if i["severity"] == "critical")
    n_warn = sum(1 for i in issues if i["severity"] == "warning")
    summary = (
        f"chars={char_count} paragraphs={len(paragraphs)} "
        f"critical={n_crit} warning={n_warn}"
    )
    return {"ok": n_crit == 0, "issues": issues, "summary": summary}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="post_write_validate.py",
        description=(
            "Run deterministic post-write checks on a chapter draft. "
            "Exit 0 if no critical issues, exit 2 otherwise."
        ),
    )
    parser.add_argument("--file", required=True,
                        help="Path to chapter .md (parsed body, with optional frontmatter).")
    parser.add_argument("--chapter", type=int, default=None,
                        help="Chapter number (for chapter-ref consistency).")
    parser.add_argument("--book", default=None,
                        help="Optional book dir; enables character-matrix consistency check.")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings as critical too (gating exit code).")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON to stdout (default behavior; flag is for clarity).")
    args = parser.parse_args(argv)

    path = Path(args.file)
    raw = _read(path)
    if raw is None:
        print(json.dumps(
            {"ok": False,
             "issues": [{"severity": "critical", "category": "length",
                         "description": f"file not found or unreadable: {path}",
                         "line": 0, "evidence": ""}],
             "summary": "io error"},
            ensure_ascii=False, indent=2,
        ))
        return 2

    book_dir = Path(args.book) if args.book else None
    if book_dir is not None and not book_dir.exists():
        print(json.dumps(
            {"ok": False,
             "issues": [{"severity": "warning", "category": "length",
                         "description": f"book dir not found: {book_dir} (skipping character checks)",
                         "line": 0, "evidence": ""}],
             "summary": "book dir missing"},
            ensure_ascii=False, indent=2,
        ))
        return 0  # not a draft-level critical

    try:
        result = validate(raw, args.chapter, path.stem, book_dir)
    except Exception as exc:
        print(json.dumps(
            {"ok": False,
             "issues": [{"severity": "critical", "category": "length",
                         "description": f"validator exception: {exc}",
                         "line": 0, "evidence": ""}],
             "summary": "validator crashed"},
            ensure_ascii=False, indent=2,
        ))
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))

    has_critical = any(i["severity"] == "critical" for i in result["issues"])
    if has_critical:
        return 2
    if args.strict and any(i["severity"] == "warning" for i in result["issues"]):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
