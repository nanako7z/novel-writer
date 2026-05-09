"""Zongheng (纵横中文网) — static HTML rankings.

URL: https://www.zongheng.com/rank/details.html?rt=1&d=1&i=<i>
- rt: ranking type (1=点击, 2=月票, ...)
- d:  duration (1=日, 2=周, 3=月, 4=总)
- i:  category (varies)

Layout uses `<a href="//book.zongheng.com/book/<id>.html">title</a>`
inside rank rows. Author is in a sibling `<span>` we extract best-effort.
"""
from __future__ import annotations

import re
from typing import Optional

from . import _http
from ._base import PlatformRankings, RankingEntry

GENRE_TO_I = {
    "xuanhuan": 2,
    "xianxia": 4,
    "urban": 5,
    "sci-fi": 7,
    "isekai": 3,
}


def _build_url(genre: Optional[str]) -> tuple[str, str]:
    i = GENRE_TO_I.get(genre or "", 0)
    return (
        f"https://www.zongheng.com/rank/details.html?rt=1&d=2&i={i}",
        f"纵横周点击榜·i={i}",
    )


BOOK_LINK_RE = re.compile(
    r'<a[^>]*href="(?:https?:)?//book\.zongheng\.com/book/(\d+)\.html"[^>]*>([^<]{2,40})</a>',
    re.IGNORECASE,
)


def fetch(genre: Optional[str], top: int) -> PlatformRankings:
    url, label = _build_url(genre)
    out = PlatformRankings(
        site="zongheng",
        platform="纵横中文网",
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
        out.warnings.append("zongheng regex matched 0 book links")
    return out


SOURCE = fetch


def parse_html(html: str, label: str, top: int) -> list[RankingEntry]:
    seen: set[str] = set()
    out: list[RankingEntry] = []
    for m in BOOK_LINK_RE.finditer(html):
        book_id, title = m.group(1), m.group(2).strip()
        if not title or title in seen:
            continue
        seen.add(title)
        out.append(RankingEntry(
            rank=len(out) + 1,
            title=title,
            url=f"https://book.zongheng.com/book/{book_id}.html",
            extra=f"[{label}]",
        ))
        if len(out) >= top:
            break
    return out
