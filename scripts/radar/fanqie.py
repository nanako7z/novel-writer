"""Fanqie (番茄小说) — SDK private JSON API.

Ported 1:1 from `.inkos-src/agents/radar-source.ts:53-86` (FanqieRadarSource).
Most reliable adapter in the radar set: Fanqie's homepage rank API returns
JSON directly, no HTML parsing, no anti-bot CAPTCHA. ByteDance can change
or revoke this endpoint at any time — adapter degrades gracefully.

The API has no per-genre endpoint we know of, so `genre` is unused here:
mixed-category leaderboards come back; the LLM downstream slices.
"""
from __future__ import annotations

from typing import Optional

from . import _http
from ._base import PlatformRankings, RankingEntry

# inkos uses sideType 10 (热门榜) + 13 (黑马榜).
RANK_TYPES = [
    {"side_type": 10, "label": "热门榜"},
    {"side_type": 13, "label": "黑马榜"},
]

API_BASE = (
    "https://api-lf.fanqiesdk.com/api/novel/channel/homepage/rank/rank_list/v2/"
    "?aid=13&offset=0"
)


def fetch(genre: Optional[str], top: int) -> PlatformRankings:
    out = PlatformRankings(
        site="fanqie",
        platform="番茄小说",
        ranking_type="+".join(rt["label"] for rt in RANK_TYPES),
        source_url=API_BASE,
    )
    rank = 0
    for rt in RANK_TYPES:
        url = f"{API_BASE}&limit={top}&side_type={rt['side_type']}"
        try:
            data = _http.get_json(
                url,
                ua="Mozilla/5.0 (compatible; novel-writer/0.1)",
                timeout=6,
            )
        except Exception as e:  # noqa: BLE001
            out.failures.append({
                "stage": "http",
                "reason": f"{type(e).__name__}: {e}",
                "url": url,
                "rankingType": rt["label"],
            })
            continue

        items = ((data.get("data") or {}).get("result")) or []
        if not isinstance(items, list):
            out.failures.append({
                "stage": "parse",
                "reason": f"data.result is not a list (got {type(items).__name__})",
                "url": url,
                "rankingType": rt["label"],
            })
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            rank += 1
            out.entries.append(RankingEntry(
                rank=rank,
                title=str(item.get("book_name", "") or ""),
                author=str(item.get("author", "") or ""),
                category=str(item.get("category", "") or ""),
                url=(f"https://fanqienovel.com/page/{item['book_id']}"
                     if item.get("book_id") else ""),
                extra=f"[{rt['label']}]",
                stats={
                    k: item[k]
                    for k in ("read_count", "score", "word_number", "creation_status")
                    if k in item
                },
            ))

    if out.is_empty() and not out.failures:
        out.warnings.append("fanqie API returned 0 entries (schema may have shifted)")
    return out


SOURCE = fetch


def parse_api_response(payload: dict, label: str = "热门榜", start_rank: int = 0) -> list[RankingEntry]:
    """Pure-function parser for self-test fixtures."""
    items = ((payload.get("data") or {}).get("result")) or []
    out: list[RankingEntry] = []
    rank = start_rank
    for item in items:
        if not isinstance(item, dict):
            continue
        rank += 1
        out.append(RankingEntry(
            rank=rank,
            title=str(item.get("book_name", "") or ""),
            author=str(item.get("author", "") or ""),
            category=str(item.get("category", "") or ""),
            url=(f"https://fanqienovel.com/page/{item['book_id']}"
                 if item.get("book_id") else ""),
            extra=f"[{label}]",
        ))
    return out
