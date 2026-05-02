#!/usr/bin/env python3
"""Export a book's chapters to txt / md / epub.

Pure stdlib: uses only `zipfile`, `xml.sax.saxutils`, etc.

CLI:
  python export_book.py --book <bookDir> --format txt|md|epub \\
      [--out <path>] [--include-summary] \\
      [--from-chapter N] [--to-chapter M]

Reads:
  <bookDir>/book.json                       (metadata)
  <bookDir>/chapters/NNNN.md                (chapter bodies)
  <bookDir>/story/state/chapter_summaries.json  (optional, only when
                                                 --include-summary)

Writes:
  <out>  (or default <bookId>-<title>.<format> next to bookDir)

Word counts in chapters are trusted as-is; this script does not recount.
Empty chapter files are skipped.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

CHAPTER_NAME_RE = re.compile(r"^(\d{4})\.md$")


# ----------------------------- IO helpers ---------------------------------

def load_book_json(book_dir: Path) -> dict:
    p = book_dir / "book.json"
    if not p.is_file():
        raise SystemExit(f"book.json not found at {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def list_chapters(book_dir: Path,
                  from_ch: int | None,
                  to_ch: int | None) -> list[tuple[int, Path]]:
    chap_dir = book_dir / "chapters"
    if not chap_dir.is_dir():
        return []
    items: list[tuple[int, Path]] = []
    for f in sorted(chap_dir.iterdir(), key=lambda p: p.name):
        m = CHAPTER_NAME_RE.match(f.name)
        if not m:
            continue
        n = int(m.group(1))
        if from_ch is not None and n < from_ch:
            continue
        if to_ch is not None and n > to_ch:
            continue
        if f.stat().st_size == 0:
            continue
        items.append((n, f))
    return items


def load_summaries(book_dir: Path) -> dict[int, str]:
    """Map chapter number → summary text. Empty if file absent."""
    p = book_dir / "story" / "state" / "chapter_summaries.json"
    if not p.is_file():
        return {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[int, str] = {}
    # inkos `rows` / legacy SKILL `summaries` — read both.
    rows = obj.get("rows", obj.get("summaries")) if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ch = row.get("chapter")
        if not isinstance(ch, int):
            continue
        # Prefer 'summary' but fall back to 'events' / 'oneLine'.
        for k in ("summary", "oneLine", "events", "description"):
            v = row.get(k)
            if isinstance(v, str) and v.strip():
                out[ch] = v.strip()
                break
            if isinstance(v, list):
                joined = " ".join(str(x) for x in v if x)
                if joined.strip():
                    out[ch] = joined.strip()
                    break
    return out


# --------------------------- markdown helpers -----------------------------

H1_RE = re.compile(r"^\s{0,3}#\s+(.*?)\s*$", re.MULTILINE)
H2_RE = re.compile(r"^\s{0,3}##\s+(.*?)\s*$", re.MULTILINE)
H3_RE = re.compile(r"^\s{0,3}###\s+(.*?)\s*$", re.MULTILINE)
ANY_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*$", re.MULTILINE)
FENCE_RE = re.compile(r"^```.*?$", re.MULTILINE)
LIST_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)


def extract_chapter_title(body: str, chapter_num: int) -> tuple[str, str]:
    """Pull the first heading as the chapter title. Returns (title, body_without_title).

    If none, fabricate '第 N 章'.
    """
    lines = body.splitlines()
    # Skip leading blank lines
    title = ""
    consumed = 0
    for i, ln in enumerate(lines):
        if ln.strip() == "":
            continue
        m = ANY_HEADING_RE.match(ln)
        if m:
            title = m.group(1).strip()
            consumed = i + 1
            break
        # First non-blank line isn't a heading: don't consume it.
        break

    if not title:
        title = f"第 {chapter_num} 章"

    remainder = "\n".join(lines[consumed:]).lstrip("\n")
    return title, remainder


def md_to_plain(body: str) -> str:
    """Strip markdown formatting for the txt export.

    Keeps paragraph breaks. Removes headings, list bullets, code fences.
    Inline emphasis (*x*, **x**, `x`) is unwrapped.
    """
    text = body
    # Drop fenced code-block delimiters (keep inner text)
    text = FENCE_RE.sub("", text)
    # Drop heading markers but keep heading text on its own line
    text = ANY_HEADING_RE.sub(lambda m: m.group(1), text)
    # Strip leading list bullets
    text = LIST_BULLET_RE.sub("", text)
    # Strip inline code backticks
    text = re.sub(r"`([^`]*)`", r"\1", text)
    # Strip bold/italic markers
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    # Strip image / link syntax: keep alt / link text
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    # Collapse 3+ blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def md_to_xhtml_paragraphs(body: str) -> str:
    """Minimal markdown → XHTML for EPUB chapter bodies.

    Recognises: headings (# / ## / ###), paragraphs (blank-line separated),
    bold/italic/inline-code. No tables, no images, no nested lists — kept
    deliberately small (stdlib only)."""
    blocks = re.split(r"\n\s*\n", body.strip())
    out: list[str] = []
    for block in blocks:
        block = block.rstrip()
        if not block:
            continue
        # Heading?
        m = ANY_HEADING_RE.match(block)
        if m:
            level = len(block) - len(block.lstrip("#"))
            level = max(1, min(level, 6))
            inner = _inline_md(m.group(1).strip())
            tag = "h2" if level <= 2 else "h3"
            out.append(f"<{tag}>{inner}</{tag}>")
            continue
        # Otherwise treat the block as a paragraph; convert internal
        # newlines to <br/> so writers' line breaks survive.
        lines = [_inline_md(ln.strip()) for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        joined = "<br/>".join(lines)
        out.append(f"<p>{joined}</p>")
    return "\n".join(out)


def _inline_md(s: str) -> str:
    s = xml_escape(s)
    # Inline code first so its contents aren't re-processed
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", s)
    s = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", s)
    s = re.sub(r"_([^_]+)_", r"<em>\1</em>", s)
    return s


# ----------------------------- exporters ----------------------------------

def export_txt(book: dict, chapters: list[tuple[int, Path]],
               summaries: dict[int, str] | None,
               out_path: Path) -> int:
    title = book.get("title", "Untitled")
    author = book.get("author", "Unknown")
    lang = book.get("language", "zh")
    now = _dt.datetime.now().isoformat(timespec="seconds")

    parts: list[str] = []
    parts.append(title)
    parts.append("=" * max(8, len(title)))
    parts.append(f"作者: {author}")
    parts.append(f"语言: {lang}")
    parts.append(f"导出时间: {now}")
    parts.append("")
    parts.append("")

    for n, fp in chapters:
        body = fp.read_text(encoding="utf-8")
        chap_title, remainder = extract_chapter_title(body, n)
        plain = md_to_plain(remainder)
        parts.append("")
        parts.append(f"第 {n} 章 {chap_title}".rstrip())
        parts.append("")
        parts.append(plain)
        parts.append("")

    if summaries:
        parts.append("")
        parts.append("=" * 16)
        parts.append("附录: 章节摘要")
        parts.append("=" * 16)
        parts.append("")
        for n, _ in chapters:
            s = summaries.get(n)
            if not s:
                continue
            parts.append(f"第 {n} 章: {s}")
            parts.append("")

    out_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return len(chapters)


def export_md(book: dict, chapters: list[tuple[int, Path]],
              summaries: dict[int, str] | None,
              out_path: Path) -> int:
    title = book.get("title", "Untitled")
    author = book.get("author", "Unknown")
    lang = book.get("language", "zh")
    now = _dt.datetime.now().isoformat(timespec="seconds")

    parts: list[str] = []
    parts.append(f"# {title}")
    parts.append("")
    parts.append(f"- 作者: {author}")
    parts.append(f"- 语言: {lang}")
    parts.append(f"- 导出时间: {now}")
    parts.append("")
    # TOC
    parts.append("## 目录")
    parts.append("")
    for n, fp in chapters:
        body = fp.read_text(encoding="utf-8")
        chap_title, _ = extract_chapter_title(body, n)
        parts.append(f"- 第 {n} 章 {chap_title}")
    parts.append("")

    for n, fp in chapters:
        body = fp.read_text(encoding="utf-8")
        chap_title, remainder = extract_chapter_title(body, n)
        parts.append("")
        parts.append(f"## 第 {n} 章 {chap_title}")
        parts.append("")
        parts.append(remainder.strip())
        parts.append("")

    if summaries:
        parts.append("")
        parts.append("## 附录: 章节摘要")
        parts.append("")
        for n, _ in chapters:
            s = summaries.get(n)
            if not s:
                continue
            parts.append(f"- **第 {n} 章**: {s}")
        parts.append("")

    out_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return len(chapters)


# ------------------------------- EPUB -------------------------------------

EPUB_CSS = """\
body { font-family: serif; line-height: 1.6; margin: 1em; }
h1, h2, h3 { line-height: 1.3; }
p { text-indent: 2em; margin: 0.4em 0; }
.cover { text-align: center; padding-top: 4em; }
.cover h1 { font-size: 2em; }
.appendix p { text-indent: 0; }
"""


def _xhtml_doc(title: str, body: str, lang: str = "zh") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{xml_escape(lang)}">\n'
        '<head>\n'
        '  <meta charset="utf-8"/>\n'
        f'  <title>{xml_escape(title)}</title>\n'
        '  <link rel="stylesheet" type="text/css" href="styles.css"/>\n'
        '</head>\n'
        '<body>\n'
        f'{body}\n'
        '</body>\n'
        '</html>\n'
    )


def export_epub(book: dict, chapters: list[tuple[int, Path]],
                summaries: dict[int, str] | None,
                out_path: Path) -> int:
    title = book.get("title", "Untitled")
    author = book.get("author", "Unknown")
    lang = book.get("language", "zh")
    book_id = book.get("id", "book")
    now = _dt.datetime.now().isoformat(timespec="seconds")
    uid = f"urn:novel-writer:{book_id}:{int(_dt.datetime.now().timestamp())}"

    # Pre-render chapters
    chapter_records: list[dict] = []
    for n, fp in chapters:
        body = fp.read_text(encoding="utf-8")
        chap_title, remainder = extract_chapter_title(body, n)
        xhtml_body = (
            f"<h2>第 {n} 章 {xml_escape(chap_title)}</h2>\n"
            + md_to_xhtml_paragraphs(remainder)
        )
        chapter_records.append({
            "num": n,
            "title": chap_title,
            "filename": f"chapter-{n:04d}.xhtml",
            "id": f"chap{n:04d}",
            "xhtml": _xhtml_doc(f"第 {n} 章 {chap_title}", xhtml_body, lang),
        })

    # Cover
    cover_xhtml = _xhtml_doc(title, (
        f'<div class="cover">\n'
        f'  <h1>{xml_escape(title)}</h1>\n'
        f'  <p>作者: {xml_escape(author)}</p>\n'
        f'  <p>语言: {xml_escape(lang)}</p>\n'
        f'  <p>导出时间: {xml_escape(now)}</p>\n'
        f'</div>'
    ), lang)

    # Optional appendix
    appendix_xhtml = None
    if summaries:
        rows = []
        for n, _ in chapters:
            s = summaries.get(n)
            if not s:
                continue
            rows.append(
                f"<p><strong>第 {n} 章</strong>: {xml_escape(s)}</p>"
            )
        if rows:
            appendix_body = (
                '<div class="appendix">\n'
                '<h2>附录: 章节摘要</h2>\n'
                + "\n".join(rows)
                + "\n</div>"
            )
            appendix_xhtml = _xhtml_doc("附录", appendix_body, lang)

    # nav.xhtml
    nav_items = ['<li><a href="cover.xhtml">封面</a></li>']
    for c in chapter_records:
        nav_items.append(
            f'<li><a href="{c["filename"]}">第 {c["num"]} 章 '
            f'{xml_escape(c["title"])}</a></li>'
        )
    if appendix_xhtml is not None:
        nav_items.append('<li><a href="appendix.xhtml">附录: 章节摘要</a></li>')
    nav_body = (
        '<nav epub:type="toc" id="toc" xmlns:epub="http://www.idpf.org/2007/ops">\n'
        '  <h1>目录</h1>\n'
        '  <ol>\n    '
        + "\n    ".join(nav_items)
        + '\n  </ol>\n'
        '</nav>'
    )
    nav_xhtml = _xhtml_doc("目录", nav_body, lang)

    # content.opf
    manifest_items: list[str] = [
        '<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>',
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="css" href="styles.css" media-type="text/css"/>',
    ]
    spine_items: list[str] = [
        '<itemref idref="cover"/>',
        '<itemref idref="nav"/>',
    ]
    for c in chapter_records:
        manifest_items.append(
            f'<item id="{c["id"]}" href="{c["filename"]}" '
            'media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'<itemref idref="{c["id"]}"/>')
    if appendix_xhtml is not None:
        manifest_items.append(
            '<item id="appendix" href="appendix.xhtml" '
            'media-type="application/xhtml+xml"/>'
        )
        spine_items.append('<itemref idref="appendix"/>')

    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid" xml:lang="' + xml_escape(lang) + '">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="bookid">{xml_escape(uid)}</dc:identifier>\n'
        f'    <dc:title>{xml_escape(title)}</dc:title>\n'
        f'    <dc:creator>{xml_escape(author)}</dc:creator>\n'
        f'    <dc:language>{xml_escape(lang)}</dc:language>\n'
        f'    <meta property="dcterms:modified">'
        f'{_dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}</meta>\n'
        '  </metadata>\n'
        '  <manifest>\n    '
        + "\n    ".join(manifest_items)
        + '\n  </manifest>\n'
        '  <spine>\n    '
        + "\n    ".join(spine_items)
        + '\n  </spine>\n'
        '</package>\n'
    )

    container_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles>\n'
        '    <rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/>\n'
        '  </rootfiles>\n'
        '</container>\n'
    )

    # Write zip — mimetype must be first and STORED (not deflated).
    with zipfile.ZipFile(out_path, "w") as zf:
        # mimetype: stored, no extra fields
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        zf.writestr(zi, "application/epub+zip")

        zf.writestr("META-INF/container.xml", container_xml,
                    compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf,
                    compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml,
                    compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/styles.css", EPUB_CSS,
                    compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/cover.xhtml", cover_xhtml,
                    compress_type=zipfile.ZIP_DEFLATED)
        for c in chapter_records:
            zf.writestr(f"OEBPS/{c['filename']}", c["xhtml"],
                        compress_type=zipfile.ZIP_DEFLATED)
        if appendix_xhtml is not None:
            zf.writestr("OEBPS/appendix.xhtml", appendix_xhtml,
                        compress_type=zipfile.ZIP_DEFLATED)
    return len(chapters)


# ------------------------------ entry -------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Export a book's chapters to txt / md / epub.",
    )
    p.add_argument("--book", required=True,
                   help="path to <bookDir> (the directory containing book.json)")
    p.add_argument("--format", required=True,
                   choices=["txt", "md", "epub"])
    p.add_argument("--out", default=None,
                   help="output path; defaults to <bookId>-<title>.<format> "
                        "next to <bookDir>")
    p.add_argument("--include-summary", action="store_true",
                   help="append per-chapter summary appendix from "
                        "story/state/chapter_summaries.json")
    p.add_argument("--from-chapter", type=int, default=None)
    p.add_argument("--to-chapter", type=int, default=None)
    p.add_argument("--json", action="store_true",
                   help="emit JSON status to stdout")
    return p.parse_args()


def _safe_filename(s: str) -> str:
    return re.sub(r"[^\w一-鿿.-]+", "_", s).strip("_") or "book"


def main() -> int:
    args = parse_args()
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        print(json.dumps({"error": f"book dir not found: {book_dir}"},
                         ensure_ascii=False))
        return 1

    book = load_book_json(book_dir)
    chapters = list_chapters(book_dir, args.from_chapter, args.to_chapter)
    if not chapters:
        print(json.dumps({
            "error": "no chapter files matched",
            "book": str(book_dir),
            "fromChapter": args.from_chapter,
            "toChapter": args.to_chapter,
        }, ensure_ascii=False))
        return 1

    summaries = load_summaries(book_dir) if args.include_summary else None

    book_id = book.get("id", "book")
    title = book.get("title", "untitled")
    default_name = f"{_safe_filename(book_id)}-{_safe_filename(title)}.{args.format}"
    out_path = Path(args.out).resolve() if args.out \
        else (book_dir.parent / default_name).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "txt":
        n = export_txt(book, chapters, summaries, out_path)
    elif args.format == "md":
        n = export_md(book, chapters, summaries, out_path)
    elif args.format == "epub":
        n = export_epub(book, chapters, summaries, out_path)
    else:
        print(json.dumps({"error": f"unknown format: {args.format}"}))
        return 1

    payload = {
        "ok": True,
        "book": book.get("id"),
        "title": book.get("title"),
        "format": args.format,
        "chaptersExported": n,
        "outputPath": str(out_path),
        "includedSummary": bool(summaries),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
