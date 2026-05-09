"""Feilu (飞卢小说网) — static HTML rankings.

Feilu's ranking pages are server-rendered with predictable book-link
markup: `<a href="//b.faloo.com/<id>.html" target="_blank">Title</a>` plus
an author link nearby. Best-effort regex extraction; warnings surface
template drift.

URL pattern (best-effort, may need tuning if Feilu re-templates):
- 月票榜: https://b.faloo.com/y_0_0_0_0_0_5_1.html
- 分类榜: https://b.faloo.com/l/<cate>/<sub>/0/0/2/0/1.html

Genre mapping intentionally minimal; expand in references/radar-sources.md.
"""
from __future__ import annotations

import re
from typing import Optional

from . import _http
from ._base import PlatformRankings, RankingEntry

# SKILL genre id → faloo cate id (best guesses; refine via radar-sources.md).
GENRE_TO_CATE = {
    "xuanhuan": 1,
    "xianxia": 2,
    "urban": 3,
    "sci-fi": 5,
    "isekai": 6,
}

OVERALL_URL = "https://b.faloo.com/y_0_0_0_0_0_5_1.html"


def _genre_url(genre: Optional[str]) -> tuple[str, str]:
    if genre and genre in GENRE_TO_CATE:
        cate = GENRE_TO_CATE[genre]
        return (f"https://b.faloo.com/l/{cate}/0/0/0/2/0/1.html", f"飞卢{genre}榜")
    return OVERALL_URL, "飞卢综合月票榜"


# Match book links inside ranking lists. Faloo book URLs end in <id>.html under b.faloo.com.
BOOK_LINK_RE = re.compile(
    r'<a[^>]*href="(?:https?:)?//b\.faloo\.com/(\d+)\.html"[^>]*>([^<]{2,40})</a>',
    re.IGNORECASE,
)


def fetch(genre: Optional[str], top: int) -> PlatformRankings:
    url, label = _genre_url(genre)
    out = PlatformRankings(
        site="feilu",
        platform="飞卢小说网",
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
        out.warnings.append("feilu regex matched 0 book links")
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
            url=f"https://b.faloo.com/{book_id}.html",
            extra=f"[{label}]",
        ))
        if len(out) >= top:
            break
    return out
