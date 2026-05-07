#!/usr/bin/env python3
"""Repair guidance md files dirtied by historical docOps bugs.

Two specific dirty patterns this script fixes:

  1. Duplicate-anchor accumulation
     A `replace_section` whose `newContent` started with the anchor line
     (e.g. `"## Active Focus\\n..."`) used to leave the anchor doubled on
     disk; subsequent runs split into independent sections that never
     dedup-merged. Result: the same H2 heading appears 2..N times in a
     single file, each with its own body.

  2. Cross-anchor leak
     newContent containing other H2/H3 lines (e.g. `## 暂缓项` snuck into
     the body of `## Active Focus`) splits sections oddly.

What this script does (per file):
  - Parse into (heading, body) pairs (same logic as doc_ops._split_into_sections)
  - For any anchor that appears > 1 time, KEEP THE LAST occurrence's body
    (most recent docOps applied) and drop the earlier ones. The H2
    heading order is otherwise preserved.
  - Strip a leading anchor line embedded in any body (legacy artifact).
  - Atomic write back with a `.bak.<ts>` next to the file.
  - Emit a JSON report of what changed.

Scope: white-list guidance md files only. Author-constitution files are
NOT touched.

CLI:
    python repair_doc_md.py --book <bookDir> [--dry-run] [--json]
        [--target current_focus|character_matrix|...]   (defaults to all)

Exit code: 0 (advisory). `--strict` makes exit 1 when repairs were needed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _summary import emit_summary  # noqa: E402

# Whitelist (mirrors doc_ops.DOC_OPS_WHITELIST minus the *roles* glob,
# which we walk per-file).
SECTION_TARGETS: dict[str, str] = {
    "currentFocus":   "story/current_focus.md",
    "styleGuide":     "story/style_guide.md",
    "storyFrame":     "story/outline/story_frame.md",
    "volumeMap":      "story/outline/volume_map.md",
}

ROLES_DIR = "story/roles"

_HEADING_RE = re.compile(r"(?m)^(#{2,3}) (.+)$")


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Same shape as doc_ops._split_into_sections."""
    parts: list[tuple[str, str]] = []
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [("", text)]
    if matches[0].start() > 0:
        parts.append(("", text[: matches[0].start()]))
    for i, m in enumerate(matches):
        heading = m.group(0)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts.append((heading, text[body_start:body_end]))
    return parts


def _join_sections(parts: Iterable[tuple[str, str]]) -> str:
    out: list[str] = []
    for heading, body in parts:
        if heading:
            if not body.startswith("\n"):
                out.append(heading + "\n" + body)
            else:
                out.append(heading + body)
        else:
            out.append(body)
    return "".join(out)


def _strip_leading_anchor(body: str, anchor: str) -> tuple[str, bool]:
    """Strip leading lines that repeat the anchor itself."""
    text = body.lstrip("\n")
    stripped = False
    while True:
        head, sep, rest = text.partition("\n")
        if head.strip() == anchor.strip():
            text = rest.lstrip("\n")
            stripped = True
            continue
        break
    if stripped:
        # restore single leading newline so _join_sections format is consistent
        return "\n" + text if not text.startswith("\n") else text, True
    return body, False


def _repair_file(path: Path, *, dry_run: bool) -> dict:
    if not path.is_file():
        return {"file": str(path), "exists": False, "changes": []}
    original = path.read_text(encoding="utf-8")
    parts = _split_sections(original)

    changes: list[dict] = []

    # 1) Strip leading-anchor leak inside each section body.
    cleaned: list[tuple[str, str]] = []
    for heading, body in parts:
        if heading:
            new_body, did = _strip_leading_anchor(body, heading)
            if did:
                changes.append({
                    "kind": "stripped_leading_anchor",
                    "anchor": heading,
                })
                cleaned.append((heading, new_body))
                continue
        cleaned.append((heading, body))
    parts = cleaned

    # 2) Dedup duplicate anchors (keep last occurrence's body — most recent
    #    docOps wins).
    seen_index: dict[str, int] = {}
    indices_to_drop: set[int] = set()
    for i, (heading, _) in enumerate(parts):
        if not heading:
            continue
        anchor = heading.strip()
        if anchor in seen_index:
            # mark the previous occurrence as dropped (not the current one)
            indices_to_drop.add(seen_index[anchor])
        seen_index[anchor] = i

    if indices_to_drop:
        # build a count for the report
        from collections import Counter
        anchors_counted = Counter(
            heading.strip() for heading, _ in parts if heading
        )
        for anchor, count in anchors_counted.items():
            if count > 1:
                changes.append({
                    "kind": "deduped_duplicate_anchors",
                    "anchor": anchor,
                    "occurrences": count,
                    "kept": "last",
                })
        parts = [p for i, p in enumerate(parts) if i not in indices_to_drop]

    new_text = _join_sections(parts)

    if new_text == original:
        return {"file": str(path), "exists": True, "changes": []}

    result: dict = {
        "file": str(path),
        "exists": True,
        "changes": changes,
        "diffBytes": len(new_text) - len(original),
    }

    if not dry_run:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        bak = path.with_suffix(path.suffix + f".bak.{ts}")
        bak.write_text(original, encoding="utf-8")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)
        result["backup"] = str(bak)

    return result


def _list_role_files(book: Path) -> list[Path]:
    root = book / ROLES_DIR
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in root.rglob("*.md"):
        if p.is_file() and not p.name.startswith("_"):
            out.append(p)
    return out


def repair(book: Path, target_filter: set[str] | None, dry_run: bool) -> dict:
    files: list[Path] = []
    for key, rel in SECTION_TARGETS.items():
        if target_filter and key not in target_filter:
            continue
        files.append(book / rel)
    if not target_filter or "roles" in target_filter:
        files.extend(_list_role_files(book))

    reports: list[dict] = []
    total_changes = 0
    for f in files:
        rep = _repair_file(f, dry_run=dry_run)
        reports.append(rep)
        total_changes += len(rep.get("changes") or [])

    return {
        "ok": True,
        "dryRun": dry_run,
        "filesScanned": len(reports),
        "filesChanged": sum(1 for r in reports if r.get("changes")),
        "totalChanges": total_changes,
        "reports": reports,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Repair guidance md files dirtied by duplicate-anchor / "
                    "anchor-leak bugs. Whitelist-only; constitution files "
                    "are never touched.",
    )
    p.add_argument("--book", required=True)
    p.add_argument("--dry-run", action="store_true", help="report without writing")
    p.add_argument(
        "--target", action="append", default=None,
        help="limit to one target key (currentFocus / styleGuide / "
        "storyFrame / volumeMap / roles); repeat to scan several. "
        "Default = all.",
    )
    p.add_argument("--strict", action="store_true",
                   help="exit 1 if any repair was needed (useful for CI)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    book = Path(args.book).resolve()
    if not book.is_dir():
        print(json.dumps({"ok": False, "error": f"book dir not found: {book}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    target_filter = set(args.target) if args.target else None
    result = repair(book, target_filter, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    emit_summary(
        f"repair_doc_md: scanned={result['filesScanned']} "
        f"changed={result['filesChanged']} "
        f"changes={result['totalChanges']} "
        f"dryRun={result['dryRun']}"
    )
    if args.strict and result["totalChanges"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
