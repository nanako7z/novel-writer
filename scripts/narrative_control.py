#!/usr/bin/env python3
"""
narrative_control.py — text-hygiene utilities for the Composer/Writer hand-off.

Two subcommands:

  sanitize                 (default behavior; preserves legacy flat CLI)
      Sanitize "out-of-narrative" entities + soften AI-leaning phrasing in
      text the Composer is about to embed in a Writer prompt.
      Ported from inkos `utils/narrative-control.ts`. Two independent passes:

        --strip-entities  (default on)
          Replace hook IDs (H001, h12), chapter refs (第 12 章 / chapter 12),
          and hook slugs (kebab-case identifiers) with a neutral phrase
          ("这条线索" / "this thread", "此前" / "an earlier scene").

        --soften          (default on)
          Apply small zh/en regex replacements that soften AI-leaning
          phrasing (仿佛→像, 似乎→像是, previous chapters→earlier scenes...).

      Both passes default ON. Disable with --no-strip-entities / --no-soften.

  build-pre-write-check    (workflow inconsistency #3)
      Build the canonical PRE_WRITE_CHECK markdown table that Writer must
      emit FIRST inside its sentinel output, per inkos `agents/writer-prompts.ts`
      buildOutputFormat(). Pre-fills cells from chapter_memo.md + book.json +
      genre profile. Cells the Writer must reason about are left as
      `(待 Writer 填写)` placeholders or memo excerpts.

Backward compatibility:
  `python narrative_control.py --file <text.md> ...` (flat, no subcommand)
  is still accepted and routed to `sanitize`.

CLI examples:
    python narrative_control.py sanitize --file <text.md> [--lang zh|en] \\
        [--no-strip-entities] [--no-soften] [--json] [--out <path>]

    python narrative_control.py build-pre-write-check \\
        --chapter-memo <chapter_memo.md> \\
        --book-config <book.json> \\
        --genre-profile <templates/genres/<id>.md> \\
        [--hooks <hooks.json>] [--lang zh|en] [--out <path>] [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Subcommand 1 — sanitize (existing behavior; ported from narrative-control.ts)
# ---------------------------------------------------------------------------

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


def cmd_sanitize(args: argparse.Namespace) -> int:
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


# ---------------------------------------------------------------------------
# Subcommand 2 — build-pre-write-check
# ---------------------------------------------------------------------------

PLACEHOLDER_ZH = "(待 Writer 填写)"
PLACEHOLDER_EN = "(to be filled by Writer)"


def _parse_frontmatter(text: str) -> Tuple[Dict[str, object], str]:
    """Minimal YAML frontmatter parser (handles scalars + simple lists).
    Returns (frontmatter_dict, body)."""
    if not text.startswith("---"):
        return {}, text
    # find closing ---
    rest = text[3:]
    # accept optional newline immediately after opening ---
    if rest.startswith("\n"):
        rest = rest[1:]
    end = rest.find("\n---")
    if end < 0:
        return {}, text
    fm_block = rest[:end]
    body = rest[end + len("\n---"):]
    if body.startswith("\n"):
        body = body[1:]

    fm: Dict[str, object] = {}
    current_key: Optional[str] = None
    current_list: Optional[List[str]] = None
    for raw in fm_block.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        # list item belonging to current key
        m_item = re.match(r"^\s+-\s*(.+)$", line)
        if m_item and current_list is not None:
            val = m_item.group(1).strip().strip('"').strip("'")
            current_list.append(val)
            continue
        m_kv = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if m_kv:
            key = m_kv.group(1)
            val = m_kv.group(2).strip()
            if val == "":
                # start a list / nested block
                current_key = key
                current_list = []
                fm[key] = current_list
            else:
                # inline scalar or inline list
                if val.startswith("[") and val.endswith("]"):
                    inner = val[1:-1].strip()
                    items = []
                    if inner:
                        for it in re.split(r",\s*", inner):
                            items.append(it.strip().strip('"').strip("'"))
                    fm[key] = items
                else:
                    v = val.strip('"').strip("'")
                    if v.lower() == "true":
                        fm[key] = True
                    elif v.lower() == "false":
                        fm[key] = False
                    elif re.fullmatch(r"-?\d+", v):
                        fm[key] = int(v)
                    else:
                        fm[key] = v
                current_key = key
                current_list = None
        # else: unrecognized; skip silently
    return fm, body


def _split_memo_sections(body: str) -> Dict[str, str]:
    """Split memo body by `## Heading` blocks. Returns dict heading→content."""
    sections: Dict[str, str] = {}
    current: Optional[str] = None
    buf: List[str] = []
    for line in body.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf).strip()
            current = m.group(1).strip()
            buf = []
        else:
            if current is not None:
                buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf).strip()
    return sections


def _find_section(sections: Dict[str, str], *needles: str) -> str:
    """Return content of first section whose heading contains any needle."""
    for key, val in sections.items():
        for n in needles:
            if n in key:
                return val
    return ""


def _extract_hook_ids(text: str) -> List[str]:
    """Pick out H\\d+ / S\\d+ identifiers from text."""
    if not text:
        return []
    ids = re.findall(r"\b[HS]\d{2,4}\b", text)
    # de-dup, preserve order
    seen: List[str] = []
    for x in ids:
        if x not in seen:
            seen.append(x)
    return seen


def _trim_excerpt(s: str, max_len: int = 80) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _first_nonempty_line(s: str) -> str:
    for line in s.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _bullet_lines(s: str, limit: int = 5) -> List[str]:
    out: List[str] = []
    for line in s.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^[-*]\s+(.+)$", line)
        if m:
            out.append(m.group(1).strip())
        else:
            out.append(line)
        if len(out) >= limit:
            break
    return out


def build_pre_write_check(
    *,
    memo_fm: Dict[str, object],
    memo_sections: Dict[str, str],
    book_config: Dict[str, object],
    genre_fm: Dict[str, object],
    hooks_data: Optional[object] = None,
    language: str = "zh",
) -> Tuple[str, Dict[str, str], List[str]]:
    """Build the PRE_WRITE_CHECK markdown table. Returns (table, filled, placeholders)."""
    placeholder = PLACEHOLDER_EN if language == "en" else PLACEHOLDER_ZH

    numerical = bool(genre_fm.get("numericalSystem", False))
    power_scaling = bool(genre_fm.get("powerScaling", False))
    chapter_types_raw = genre_fm.get("chapterTypes") or []
    if isinstance(chapter_types_raw, str):
        chapter_types: List[str] = [chapter_types_raw]
    else:
        chapter_types = [str(x) for x in chapter_types_raw]
    chapter_types_str = "/".join(chapter_types) if chapter_types else "过渡/冲突/高潮/收束"

    # Pull memo sections (tolerant of slight heading variants)
    s_task = _find_section(memo_sections, "当前任务")
    s_waiting = _find_section(memo_sections, "读者此刻在等什么", "读者在等什么")
    s_payoff = _find_section(memo_sections, "该兑现的", "暂不掀")
    s_daily = _find_section(memo_sections, "日常/过渡", "日常", "过渡承担")
    s_endchg = _find_section(memo_sections, "章尾必须发生的改变", "章尾")
    s_dont = _find_section(memo_sections, "不要做")
    s_hookacc = _find_section(memo_sections, "本章 hook 账", "hook 账")

    chapter_num = memo_fm.get("chapter")
    thread_refs = memo_fm.get("threadRefs") or []
    if isinstance(thread_refs, str):
        thread_refs = [thread_refs]

    # ---- compute filled values ----
    filled: Dict[str, str] = {}
    placeholders: List[str] = []

    def _set(key: str, value: str) -> None:
        if not value or value == placeholder:
            placeholders.append(key)
            filled[key] = placeholder
        else:
            filled[key] = value

    # 当前任务: take memo's first non-empty line; ask Writer to add the action
    task_excerpt = _first_nonempty_line(s_task)
    if task_excerpt:
        filled["当前任务"] = f"memo: {_trim_excerpt(task_excerpt, 60)} → {placeholder}"
        placeholders.append("当前任务(执行动作)")
    else:
        _set("当前任务", "")

    # 读者在等什么: structural (制造/延迟/兑现) — Writer must reason
    reader_excerpt = _trim_excerpt(s_waiting, 60) if s_waiting else ""
    if reader_excerpt:
        filled["读者在等什么"] = f"memo: {reader_excerpt} → {placeholder}"
        placeholders.append("读者在等什么(本章处理)")
    else:
        _set("读者在等什么", "")

    # 该兑现的 / 暂不掀的
    payoff_excerpt = _trim_excerpt(s_payoff, 100) if s_payoff else ""
    if payoff_excerpt:
        filled["该兑现的 / 暂不掀的"] = payoff_excerpt
    else:
        _set("该兑现的 / 暂不掀的", "")

    # 日常/过渡承担任务
    daily_excerpt = _trim_excerpt(s_daily, 100) if s_daily else ""
    if daily_excerpt and "不适用" not in daily_excerpt:
        filled["日常/过渡承担任务"] = daily_excerpt
    elif daily_excerpt:
        filled["日常/过渡承担任务"] = "不适用 - 本章无日常过渡"
    else:
        _set("日常/过渡承担任务", "")

    # 章尾必须发生的改变 — pull bullets directly
    end_changes = _bullet_lines(s_endchg, limit=3) if s_endchg else []
    if end_changes:
        filled["章尾必须发生的改变"] = " / ".join(end_changes)
    else:
        _set("章尾必须发生的改变", "")

    # 不要做 — pull bullets verbatim
    donts = _bullet_lines(s_dont, limit=4) if s_dont else []
    if donts:
        filled["不要做"] = " / ".join(donts)
    else:
        _set("不要做", "")

    # 上下文范围 — Writer must reason
    _set("上下文范围", "")

    # 当前锚点 — Writer must reason
    _set("当前锚点", "")

    # 资源行 (only if numericalSystem)
    if numerical:
        _set("当前资源总量", "")
        _set("本章预计增量", "")

    # 待回收伏笔 — extract from memo (resolve list in hook 账, threadRefs frontmatter)
    hook_ids_due = _extract_hook_ids(s_hookacc)
    # also fold threadRefs in
    for tid in thread_refs:
        if isinstance(tid, str) and tid not in hook_ids_due:
            hook_ids_due.append(tid)
    if hook_ids_due:
        filled["待回收伏笔"] = ", ".join(hook_ids_due)
    else:
        filled["待回收伏笔"] = "none"

    # 本章冲突 — Writer must reason (one-line summary)
    _set("本章冲突", "")

    # 章节类型 — Writer picks; we list the menu
    filled["章节类型"] = f"从 [{chapter_types_str}] 中选一个 → {placeholder}"
    placeholders.append("章节类型(选定)")

    # 风险扫描 — fixed wording assembled from genre toggles
    risk_items = ["OOC", "信息越界", "设定冲突"]
    if power_scaling:
        risk_items.append("战力崩坏")
    risk_items.extend(["节奏", "词汇疲劳"])
    filled["风险扫描"] = "/".join(risk_items)

    # ---- assemble markdown table ----
    note_default = ""
    rows: List[Tuple[str, str, str]] = [
        ("当前任务", filled["当前任务"], "必须具体，不能抽象"),
        ("读者在等什么", filled["读者在等什么"], "与 memo 一致"),
        ("该兑现的 / 暂不掀的", filled["该兑现的 / 暂不掀的"], "引用 memo 原文"),
        ("日常/过渡承担任务", filled["日常/过渡承担任务"], "对齐 memo 映射表"),
        ("章尾必须发生的改变", filled["章尾必须发生的改变"], "必须落地"),
        ("不要做", filled["不要做"], "正文不得触碰"),
        ("上下文范围", filled["上下文范围"], note_default),
        ("当前锚点", filled["当前锚点"], "锚点必须具体"),
    ]
    if numerical:
        rows.append(("当前资源总量", filled["当前资源总量"], "与账本一致"))
        rows.append(("本章预计增量", filled["本章预计增量"], "无增量写+0"))
    rows.extend([
        ("待回收伏笔", filled["待回收伏笔"], "与伏笔池一致"),
        ("本章冲突", filled["本章冲突"], "一句话概括"),
        ("章节类型", filled["章节类型"], note_default),
        ("风险扫描", filled["风险扫描"], note_default),
    ])

    header_note = "（必须输出Markdown表格，全部检查项对齐 chapter_memo 七段，而不是卷纲）"
    lines: List[str] = [
        "=== PRE_WRITE_CHECK ===",
        header_note,
        "| 检查项 | 本章记录 | 备注 |",
        "|--------|----------|------|",
    ]
    for name, val, note in rows:
        # escape pipes in cell content
        v = (val or "").replace("|", "\\|")
        n = (note or "").replace("|", "\\|")
        lines.append(f"| {name} | {v} | {n} |")

    table = "\n".join(lines)
    return table, filled, placeholders


def cmd_build_pre_write_check(args: argparse.Namespace) -> int:
    memo_path = Path(args.chapter_memo)
    book_path = Path(args.book_config)
    genre_path = Path(args.genre_profile)

    for label, p in (("chapter-memo", memo_path),
                     ("book-config", book_path),
                     ("genre-profile", genre_path)):
        if not p.is_file():
            print(f"build-pre-write-check: --{label} not found: {p}", file=sys.stderr)
            return 2

    memo_text = memo_path.read_text(encoding="utf-8")
    memo_fm, memo_body = _parse_frontmatter(memo_text)
    memo_sections = _split_memo_sections(memo_body)

    try:
        book_config = json.loads(book_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"build-pre-write-check: book.json invalid: {e}", file=sys.stderr)
        return 2

    genre_text = genre_path.read_text(encoding="utf-8")
    genre_fm, _ = _parse_frontmatter(genre_text)

    hooks_data = None
    if args.hooks:
        hp = Path(args.hooks)
        if hp.is_file():
            try:
                hooks_data = json.loads(hp.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                hooks_data = None

    language = args.lang or str(book_config.get("language") or "zh")

    table, filled, placeholders = build_pre_write_check(
        memo_fm=memo_fm,
        memo_sections=memo_sections,
        book_config=book_config,
        genre_fm=genre_fm,
        hooks_data=hooks_data,
        language=language,
    )

    if args.json:
        payload = {
            "table": table,
            "filled": filled,
            "placeholders": placeholders,
            "language": language,
            "numericalSystem": bool(genre_fm.get("numericalSystem", False)),
            "powerScaling": bool(genre_fm.get("powerScaling", False)),
            "chapterTypes": genre_fm.get("chapterTypes") or [],
            "chapter": memo_fm.get("chapter"),
        }
        out_text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        out_text = table

    if args.out:
        Path(args.out).write_text(
            out_text + ("\n" if not out_text.endswith("\n") else ""),
            encoding="utf-8",
        )
    else:
        sys.stdout.write(out_text)
        if not out_text.endswith("\n"):
            sys.stdout.write("\n")

    return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="narrative_control.py",
        description=(
            "Text-hygiene utilities for the Composer/Writer hand-off. "
            "Subcommands: sanitize, build-pre-write-check."
        ),
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # --- sanitize ---
    p_san = sub.add_parser(
        "sanitize",
        help="Strip out-of-narrative entities and soften AI phrasing in a text file.",
    )
    p_san.add_argument("--file", required=True, help="Path to input text/md file.")
    p_san.add_argument("--lang", choices=["zh", "en"], default="zh")
    p_san.add_argument(
        "--strip-entities",
        dest="strip_entities",
        action="store_true",
        default=True,
        help="(default on) Strip hook IDs / chapter refs / slugs.",
    )
    p_san.add_argument(
        "--no-strip-entities",
        dest="strip_entities",
        action="store_false",
    )
    p_san.add_argument(
        "--soften",
        dest="soften",
        action="store_true",
        default=True,
        help="(default on) Apply zh/en softening regex.",
    )
    p_san.add_argument(
        "--no-soften",
        dest="soften",
        action="store_false",
    )
    p_san.add_argument("--json", action="store_true", help="Emit JSON summary.")
    p_san.add_argument("--out", help="Optional output path; default stdout.")
    p_san.set_defaults(func=cmd_sanitize)

    # --- build-pre-write-check ---
    p_pwc = sub.add_parser(
        "build-pre-write-check",
        help=(
            "Generate the PRE_WRITE_CHECK markdown table Writer must emit first "
            "(per inkos writer-prompts.ts). Pre-fills cells from chapter_memo."
        ),
    )
    p_pwc.add_argument("--chapter-memo", required=True,
                       help="Path to chapter_memo.md (YAML frontmatter + 7-section body).")
    p_pwc.add_argument("--book-config", required=True,
                       help="Path to book.json.")
    p_pwc.add_argument("--genre-profile", required=True,
                       help="Path to templates/genres/<id>.md (provides chapterTypes / numericalSystem / powerScaling).")
    p_pwc.add_argument("--hooks", default=None,
                       help="Optional path to hooks.json (for cross-checking due hooks).")
    p_pwc.add_argument("--lang", choices=["zh", "en"], default=None,
                       help="Output language for placeholders; default = book.language or zh.")
    p_pwc.add_argument("--out", default=None,
                       help="Output path; default stdout.")
    p_pwc.add_argument("--json", action="store_true",
                       help="Emit JSON {table, filled, placeholders, ...} instead of bare table.")
    p_pwc.set_defaults(func=cmd_build_pre_write_check)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # Backward compatibility: legacy flat invocation `narrative_control.py --file X ...`
    # had no subcommand. Detect and inject `sanitize` as the implicit subcommand.
    if argv and not argv[0].startswith("-"):
        # has a subcommand already
        pass
    elif argv and ("--file" in argv) and not any(
        a in argv for a in ("-h", "--help")
    ):
        argv = ["sanitize", *argv]

    parser = _build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return 2

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
