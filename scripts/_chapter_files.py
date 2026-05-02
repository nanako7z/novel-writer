"""Chapter file lookup — accepts both `{NNNN}.md` and `{NNNN}_<title>.md`.

inkos writes chapter bodies as ``chapters/{NNNN}_<title>.md`` (4-digit
zero-padded number, underscore, title slug). novel-writer's own
orchestration writes the bare ``chapters/{NNNN}.md``. Both forms must be
readable by every consumer (validators, fatigue scan, recovery) so the
two tools share books cleanly.

Usage:

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _chapter_files import find_chapter_file, CHAPTER_NAME_RE

If both a bare and a titled file exist for the same chapter number,
the bare one wins (it is the form novel-writer writes itself, and
keeping it stable under that name avoids a rename-race during
in-flight writes). Callers that want to detect the duplicate should
use ``list_chapter_files``.
"""
from __future__ import annotations

import re
from pathlib import Path

CHAPTER_NAME_RE = re.compile(r"^(\d{4})(_[^/]*)?\.md$")


def find_chapter_file(book_dir: Path, n: int) -> Path | None:
    """Return chapters/{NNNN}.md or chapters/{NNNN}_*.md if exactly one exists.

    Bare-NNNN file wins if both forms exist (see module docstring).
    Returns None if no matching file is found.
    """
    chap_dir = book_dir / "chapters"
    if not chap_dir.is_dir():
        return None
    bare = chap_dir / f"{n:04d}.md"
    if bare.is_file():
        return bare
    titled = sorted(chap_dir.glob(f"{n:04d}_*.md"))
    return titled[0] if titled else None


def list_chapter_files(book_dir: Path, n: int) -> list[Path]:
    """Return every chapter file that matches chapter number ``n``.

    Use this to detect the (rare) case where both a bare and a titled
    file are present, which usually indicates a half-completed rename
    or two tools writing under different conventions.
    """
    chap_dir = book_dir / "chapters"
    if not chap_dir.is_dir():
        return []
    out: list[Path] = []
    bare = chap_dir / f"{n:04d}.md"
    if bare.is_file():
        out.append(bare)
    out.extend(sorted(chap_dir.glob(f"{n:04d}_*.md")))
    return out


def all_chapter_files(book_dir: Path) -> list[Path]:
    """Return every chapter body file under chapters/ (sorted by number)."""
    chap_dir = book_dir / "chapters"
    if not chap_dir.is_dir():
        return []
    matches: list[tuple[int, Path]] = []
    for p in chap_dir.iterdir():
        if not p.is_file():
            continue
        m = CHAPTER_NAME_RE.match(p.name)
        if m:
            matches.append((int(m.group(1)), p))
    matches.sort(key=lambda t: t[0])
    return [p for _, p in matches]
