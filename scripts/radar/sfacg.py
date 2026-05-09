"""SF轻小说 (book.sfacg.com) — static HTML rankings.

URL: https://book.sfacg.com/Rank/?d=<d>&t=<t>
- d: duration (1=日, 7=周, 30=月)
- t: novel type (varies; SF轻小说 is light-novel-leaning)

Book links: <a href="/Novel/<id>/" title="...">title</a>.
"""
from __future__ import annotations

import re
from typing import Optional

from . import _http
from ._base import PlatformRankings, RankingEntry

GENRE_TO_T = {
    # SF轻小说 is mostly light-novel; we map a handful of SKILL ids → its types.
    "isekai": 11,
    "sci-fi": 16,
    "litrpg": 22,
    "xuanhuan": 13,
    "horror": 19,
}


def _build_url(genre: Optional[str]) -> tuple[str, str]:
    t = GENRE_TO_T.get(genre or "", 0)
    return (
        f"https://book.sfacg.com/Rank/?d=7&t={t}",
        f"SF轻小说周榜·t={t}",
    )


# Two-pass: capture the whole anchor-tag opening (incl. attributes), then
# extract title from either `title="..."` attr or the inner text up to </a>.
# href may be relative (/Novel/N/) or absolute (http://book.sfacg.com/Novel/N/).
ANCHOR_RE = re.compile(
    r'<a([^>]*?href="(?:https?://book\.sfacg\.com)?/Novel/(\d+)/?"[^>]*)>([^<]*)(?:</a>|<)',
    re.IGNORECASE,
)
TITLE_ATTR_RE = re.compile(r'title="([^"]+)"', re.IGNORECASE)


def fetch(genre: Optional[str], top: int) -> PlatformRankings:
    url, label = _build_url(genre)
    out = PlatformRankings(
        site="sfacg",
        platform="SF轻小说",
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
        out.warnings.append("sfacg regex matched 0 book links")
    return out


SOURCE = fetch


def parse_html(html: str, label: str, top: int) -> list[RankingEntry]:
    seen: set[str] = set()
    out: list[RankingEntry] = []
    for m in ANCHOR_RE.finditer(html):
        attrs = m.group(1)
        novel_id = m.group(2)
        inner = (m.group(3) or "").strip()
        title_m = TITLE_ATTR_RE.search(attrs)
        title = (title_m.group(1) if title_m else inner).strip()
        if not title or len(title) < 2 or len(title) > 40 or title in seen:
            continue
        seen.add(title)
        out.append(RankingEntry(
            rank=len(out) + 1,
            title=title,
            url=f"https://book.sfacg.com/Novel/{novel_id}/",
            extra=f"[{label}]",
        ))
        if len(out) >= top:
            break
    return out
