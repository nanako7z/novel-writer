"""Radar source contract — Python port of inkos `RadarSource` interface.

Mirrors `.inkos-src/agents/radar-source.ts`:
- RankingEntry: one book on a leaderboard
- PlatformRankings: one site's rankings (may include multiple ranking types
  collapsed into a single entries list, distinguished by entry.extra)

Each adapter module exposes `SOURCE: RadarSource`. Adapters never raise on
network or parse failure — they return a PlatformRankings with empty entries
and a non-empty `failures` list, so the orchestrator can route to fallback
without try/except scattered across callers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class RankingEntry:
    rank: int
    title: str
    author: str = ""
    category: str = ""
    url: str = ""
    extra: str = ""
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "title": self.title,
            "author": self.author,
            "category": self.category,
            "url": self.url,
            "extra": self.extra,
            "stats": dict(self.stats),
        }


@dataclass
class PlatformRankings:
    site: str                      # adapter id, e.g. "fanqie"
    platform: str                  # display name, e.g. "番茄小说"
    entries: list[RankingEntry] = field(default_factory=list)
    ranking_type: str = ""         # e.g. "热门榜" / "玄幻周榜"
    source_url: str = ""
    fetched_via: str = "http"      # http | cache | webfetch | user-paste
    warnings: list[str] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)  # [{stage, reason, url?}]

    def to_dict(self) -> dict:
        return {
            "site": self.site,
            "platform": self.platform,
            "rankingType": self.ranking_type,
            "sourceUrl": self.source_url,
            "fetchedVia": self.fetched_via,
            "entries": [e.to_dict() for e in self.entries],
            "warnings": list(self.warnings),
            "failures": list(self.failures),
        }

    def is_empty(self) -> bool:
        return len(self.entries) == 0


# RadarSource = a callable adapter.
# Signature: fetch(genre, top) -> PlatformRankings
# - genre: SKILL-internal id (xianxia/xuanhuan/urban/...) or None for "all"
# - top: max entries to return per ranking type
RadarSource = Callable[[Optional[str], int], PlatformRankings]
