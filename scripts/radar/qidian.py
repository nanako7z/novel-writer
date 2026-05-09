"""Qidian (起点中文网) — web HTML regex.

Ported from `.inkos-src/agents/radar-source.ts:88-123` (QidianRadarSource).
Inkos pulls qidian.com/rank/ as a single page and regex-extracts book titles
from `<a href="//book.qidian.com/info/<id>">title</a>`. We do the same.

Limitations (inherited from inkos):
- author / category fields are empty (page doesn't expose them inline)
- regex breaks if Qidian re-templates the page; warning surfaces it

Genre routing:
- if `genre` matches a known SKILL → qidian site mapping, fetch the
  per-genre rank URL (rank.qidian.com/yuepiao?style=N&chn=K) instead of
  the homepage all-types one. The page structure is similar so the same
  regex works.
"""
from __future__ import annotations

import re
from typing import Optional

from . import _http
from ._base import PlatformRankings, RankingEntry

HOMEPAGE_URL = "https://www.qidian.com/rank/"

# SKILL genre id → qidian rank.qidian.com chn id (subset; expand as needed).
# Reference table also lives in references/radar-sources.md.
GENRE_TO_CHN = {
    "xuanhuan": 21,
    "xianxia": 1,
    "urban": 4,
    "sci-fi": 9,
    "litrpg": 8,
    "isekai": 22,
    "horror": 7,
}

BOOK_LINK_RE = re.compile(
    r'<a[^>]*href="(?://book\.qidian\.com/info/(\d+))"[^>]*>([^<]+)</a>'
)


def _build_url(genre: Optional[str]) -> tuple[str, str]:
    if genre and genre in GENRE_TO_CHN:
        chn = GENRE_TO_CHN[genre]
        return (
            f"https://www.qidian.com/rank/yuepiao/chn{chn}/",
            f"{genre}月票榜",
        )
    return HOMEPAGE_URL, "起点综合榜"


def fetch(genre: Optional[str], top: int) -> PlatformRankings:
    url, label = _build_url(genre)
    out = PlatformRankings(
        site="qidian",
        platform="起点中文网",
        ranking_type=label,
        source_url=url,
    )
    try:
        status, html, _ = _http.get(url, timeout=8)
    except Exception as e:  # noqa: BLE001
        out.failures.append({"stage": "http", "reason": f"{type(e).__name__}: {e}", "url": url})
        return out
    if status >= 400:
        out.failures.append({"stage": "http", "reason": f"HTTP {status}", "url": url})
        return out

    out.entries.extend(parse_html(html, label, top))
    if not out.entries:
        out.warnings.append(
            "qidian regex matched 0 book links (page template may have changed)"
        )
    return out


SOURCE = fetch


def parse_html(html: str, label: str, top: int) -> list[RankingEntry]:
    """Pure-function parser; used by adapter and self-test fixtures."""
    seen: set[str] = set()
    out: list[RankingEntry] = []
    for m in BOOK_LINK_RE.finditer(html):
        book_id, title = m.group(1), m.group(2).strip()
        if not title or title in seen or len(title) <= 1 or len(title) >= 30:
            continue
        seen.add(title)
        out.append(RankingEntry(
            rank=len(out) + 1,
            title=title,
            url=f"https://book.qidian.com/info/{book_id}",
            extra=f"[{label}]",
        ))
        if len(out) >= top:
            break
    return out
