"""Jinjiang (晋江文学城) — static HTML rankings, GBK encoded, gzip transit.

URL: https://www.jjwxc.net/topten.php?orderstr=<X>&t=<T>
- orderstr: ranking type (4=日点击, 7=周收藏, 9=月红包, ...)
- t: category (0=综合, 1=纯爱, 2=言情, 4=百合, 6=幻想, 7=武侠, 11=同人)

Body is GB18030-encoded and arrives gzip-compressed even when we send
`Accept-Encoding: identity` (server ignores the hint). `_http.py` handles
both. Real markup uses `<a title="书名" data-recommendInfo='{"relationNovelid":"...", ...}'>`,
not `href=onebook.php?...` like the older inkos-era template — title and
novel id are extracted from those two attrs.
"""
from __future__ import annotations

import re
from typing import Optional

from . import _http
from ._base import PlatformRankings, RankingEntry

GENRE_TO_T = {
    # SKILL is male-channel-leaning; Jinjiang is female-channel — mappings
    # are best-effort. Most useful: 6=幻想, 7=武侠.
    "xianxia": 6,
    "xuanhuan": 6,
    "urban": 2,    # 言情
    "romantasy": 2,
    "isekai": 6,
    "horror": 6,
    "cozy": 2,
}

DEFAULT_ORDER = 7  # 周收藏


def _build_url(genre: Optional[str]) -> tuple[str, str]:
    t = GENRE_TO_T.get(genre or "", 0)
    label = f"晋江周收藏榜·t={t}"
    return (
        f"https://www.jjwxc.net/topten.php?orderstr={DEFAULT_ORDER}&t={t}",
        label,
    )


# Match: <a title="书名" ... data-recommendInfo='{"relationNovelid":"NNN", ...}'
# (multi-line, so DOTALL).
BOOK_LINK_RE = re.compile(
    r'<a\s+title="([^"]{2,40})"[^>]{0,800}?relationNovelid["\']?\s*:\s*["\']?(\d+)',
    re.IGNORECASE | re.DOTALL,
)


def fetch(genre: Optional[str], top: int) -> PlatformRankings:
    url, label = _build_url(genre)
    out = PlatformRankings(
        site="jjwxc",
        platform="晋江文学城",
        ranking_type=label,
        source_url=url,
    )
    try:
        status, html, _ = _http.get(url, timeout=8, fallback_encoding="gb18030")
    except Exception as e:  # noqa: BLE001
        out.failures.append({"stage": "http", "reason": f"{type(e).__name__}: {e}", "url": url})
        return out
    if status >= 400:
        out.failures.append({"stage": "http", "reason": f"HTTP {status}", "url": url})
        return out

    out.entries.extend(parse_html(html, label, top))
    if not out.entries:
        out.warnings.append("jjwxc regex matched 0 book links (encoding or template drift)")
    return out


SOURCE = fetch


def parse_html(html: str, label: str, top: int) -> list[RankingEntry]:
    seen_ids: set[str] = set()
    out: list[RankingEntry] = []
    for m in BOOK_LINK_RE.finditer(html):
        title, novel_id = m.group(1).strip(), m.group(2)
        if not title or novel_id in seen_ids:
            continue
        seen_ids.add(novel_id)
        out.append(RankingEntry(
            rank=len(out) + 1,
            title=title,
            url=f"https://www.jjwxc.net/onebook.php?novelid={novel_id}",
            extra=f"[{label}]",
        ))
        if len(out) >= top:
            break
    return out
