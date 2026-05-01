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
    return p.parse_args()


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

    print(json.dumps({
        "bookId": args.id,
        "path": str(book_dir),
        "filesCreated": files_created,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
