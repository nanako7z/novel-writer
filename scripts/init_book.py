#!/usr/bin/env python3
"""Initialize a new book project under <workdir>/books/<bookId>/.

Copies templates from <skill_root>/templates/, substitutes placeholders,
creates the inkos.json project config if absent, and prints a JSON summary.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import shutil
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = SKILL_ROOT / "templates"

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def kebab_ok(s: str) -> bool:
    return bool(re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", s))


def substitute(text: str, mapping: dict) -> str:
    def repl(m):
        key = m.group(1)
        v = mapping.get(key, m.group(0))
        return str(v)
    return PLACEHOLDER_RE.sub(repl, text)


def copy_templates(book_dir: Path, mapping: dict) -> list[str]:
    created: list[str] = []
    if not TEMPLATES_DIR.exists():
        raise SystemExit(f"templates dir not found: {TEMPLATES_DIR}")
    for src in TEMPLATES_DIR.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(TEMPLATES_DIR)
        # inkos.json belongs at workdir level, not book dir
        if rel.name == "inkos.json":
            continue
        dest = book_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8")
        dest.write_text(substitute(text, mapping), encoding="utf-8")
        created.append(str(dest))
    return created


def ensure_inkos_json(workdir: Path, project_name: str, lang: str) -> str | None:
    p = workdir / "inkos.json"
    if p.exists():
        return None
    src = TEMPLATES_DIR / "inkos.json"
    text = src.read_text(encoding="utf-8")
    text = substitute(text, {"projectName": project_name, "lang": lang})
    p.write_text(text, encoding="utf-8")
    return str(p)


def make_dirs(book_dir: Path) -> None:
    for sub in [
        "story/state",
        "chapters",
        "story/runtime",
        "story/outline",
        "story/roles/主要角色",
        "story/roles/次要角色",
    ]:
        (book_dir / sub).mkdir(parents=True, exist_ok=True)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Initialize a new book project")
    p.add_argument("--workdir", default=".", help="root where books/ lives (default: cwd)")
    p.add_argument("--id", required=True, help="bookId (kebab-case)")
    p.add_argument("--title", required=True)
    p.add_argument("--genre", required=True)
    p.add_argument("--platform", required=True, choices=["tomato", "feilu", "qidian", "other"])
    p.add_argument("--target-chapters", type=int, default=100)
    p.add_argument("--chapter-words", type=int, default=3000)
    p.add_argument("--lang", default="zh", choices=["zh", "en"])
    p.add_argument("--fanfic-mode", choices=["canon", "au", "ooc", "cp"], default=None)
    p.add_argument("--parent-book-id", default=None)
    p.add_argument("--brief", default=None,
                   help="Path to a creative brief markdown file. If given, "
                        "its content seeds story/author_intent.md and the "
                        "next-step is set to 'architect' so Claude runs "
                        "Architect immediately after init.")
    p.add_argument("--current-focus", default=None,
                   help="Inline string OR @path/to/file. If given, populates "
                        "story/current_focus.md instead of the placeholder.")
    return p.parse_args()


_FRONT_MATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def _load_brief(path: str) -> str:
    """Read a brief from disk. If it contains its own YAML frontmatter or
    starts with `# ` heading, treat as ready-to-write content. Otherwise
    wrap in the author_intent template."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise SystemExit(f"--brief file not found: {p}")
    body = p.read_text(encoding="utf-8")
    return body


def _wrap_brief_as_author_intent(brief: str, mapping: dict) -> str:
    """If the brief already looks like a fleshed-out author_intent (has a
    `# 作者意图` heading or YAML frontmatter), keep it verbatim. Otherwise,
    inject it as the body under the standard headings."""
    stripped = brief.lstrip()
    has_frontmatter = stripped.startswith("---")
    has_intent_heading = stripped.startswith("# 作者意图") or stripped.startswith("# Author Intent")
    if has_frontmatter or has_intent_heading:
        return brief
    # Treat brief as raw description; fold it into the template's "核心命题" slot
    # but preserve the structural headings so Architect can read the same shape.
    template = (
        "# 作者意图（Author Intent）\n\n"
        "> 长程愿景。本文件由作者/Claude 维护，描述全书核心命题、主题、爽点曲线、情绪基调与目标读者。\n"
        "> 写作过程中由 Planner 阶段读取，**不应被 runtime 自动改写**。\n\n"
        "## 用户原始 Brief（init 时写入）\n\n"
        f"{brief.rstrip()}\n\n"
        "## 核心命题\n\n"
        "（Architect 阶段会从上面的 brief 中提炼并填回这里。）\n\n"
        "## 主题与情绪基调\n\n"
        "- 主题关键词：\n"
        "- 情绪基调：\n"
        "- 全书爽点曲线（前期/中期/后期）：\n\n"
        "## 目标读者画像\n\n"
        f"- 平台：{mapping.get('platform', '')}\n"
        f"- 题材：{mapping.get('genre', '')}\n"
        "- 读者偏好：\n\n"
        "## 不可违背的设定红线\n\n"
        "- （Architect 阶段会从 brief 中识别硬约束并补全这里。）\n"
    )
    return template


def _resolve_current_focus(arg: str) -> str:
    """`--current-focus` accepts either an inline string or `@path` syntax."""
    if arg.startswith("@"):
        p = Path(arg[1:]).expanduser()
        if not p.is_file():
            raise SystemExit(f"--current-focus file not found: {p}")
        return p.read_text(encoding="utf-8")
    return arg


def main() -> int:
    args = parse_args()
    if not kebab_ok(args.id):
        print(json.dumps({"error": "bookId must be kebab-case"}), file=sys.stderr)
        return 1
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    book_dir = workdir / "books" / args.id
    if book_dir.exists() and any(book_dir.iterdir()):
        print(json.dumps({"error": f"book dir not empty: {book_dir}"}), file=sys.stderr)
        return 1
    book_dir.mkdir(parents=True, exist_ok=True)
    make_dirs(book_dir)

    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    mapping = {
        "title": args.title,
        "bookId": args.id,
        "genre": args.genre,
        "platform": args.platform,
        "lang": args.lang,
        "targetChapters": args.target_chapters,
        "chapterWords": args.chapter_words,
        "createdAt": now,
        "projectName": workdir.name,
    }

    files_created: list[str] = []
    inkos_p = ensure_inkos_json(workdir, workdir.name, args.lang)
    if inkos_p:
        files_created.append(inkos_p)
    files_created.extend(copy_templates(book_dir, mapping))

    # Patch book.json with optional fields
    book_json_p = book_dir / "book.json"
    if book_json_p.exists():
        try:
            data = json.loads(book_json_p.read_text(encoding="utf-8"))
            if args.fanfic_mode:
                data["fanficMode"] = args.fanfic_mode
            if args.parent_book_id:
                data["parentBookId"] = args.parent_book_id
            book_json_p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"book.json invalid: {e}"}), file=sys.stderr)
            return 1

    # Optional: seed author_intent.md from a creative brief
    next_step = "author_intent"
    if args.brief:
        brief_text = _load_brief(args.brief)
        intent_p = book_dir / "story" / "author_intent.md"
        intent_p.parent.mkdir(parents=True, exist_ok=True)
        intent_p.write_text(_wrap_brief_as_author_intent(brief_text, mapping),
                            encoding="utf-8")
        next_step = "architect"

    # Optional: seed current_focus.md
    if args.current_focus:
        focus_text = _resolve_current_focus(args.current_focus)
        focus_p = book_dir / "story" / "current_focus.md"
        focus_p.parent.mkdir(parents=True, exist_ok=True)
        focus_p.write_text(focus_text, encoding="utf-8")

    print(json.dumps({
        "bookId": args.id,
        "path": str(book_dir),
        "filesCreated": files_created,
        "nextStep": next_step,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
