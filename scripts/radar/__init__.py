"""Radar adapter registry.

`SOURCES` maps site id → adapter callable. `radar_fetch.py scan` iterates
this registry; new sites only need to be added here + a module file.

Adapter contract: see scripts/radar/_base.py — every adapter exports
`fetch(genre: str | None, top: int) -> PlatformRankings`.
"""
from __future__ import annotations

from . import fanqie, feilu, jjwxc, qidian, sfacg, zongheng
from ._base import PlatformRankings, RankingEntry, RadarSource

SOURCES: dict[str, RadarSource] = {
    "fanqie": fanqie.SOURCE,
    "qidian": qidian.SOURCE,
    "feilu": feilu.SOURCE,
    "jjwxc": jjwxc.SOURCE,
    "zongheng": zongheng.SOURCE,
    "sfacg": sfacg.SOURCE,
}

__all__ = ["SOURCES", "PlatformRankings", "RankingEntry", "RadarSource"]
