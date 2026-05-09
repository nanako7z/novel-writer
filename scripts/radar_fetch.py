#!/usr/bin/env python3
"""Radar — auto-fetch market rankings from novel sites.

Python port of inkos's `RadarAgent.scan()` (.inkos-src/agents/radar.ts) in
two layers:

  1. Adapter layer (`scripts/radar/<site>.py`) — one module per platform.
     Each adapter is a callable `fetch(genre, top) -> PlatformRankings`
     that NEVER raises (network/parse failures land in `failures[]`).
  2. Orchestrator (this file) — iterates the registry, applies cache
     dedup, emits a uniform JSON payload + a list of sites needing
     Claude WebFetch fallback or user-paste fallback.

Three-stage progressive fallback (orchestrated by phase 01-radar.md, not
this script):

  A. std-lib HTTP (this script)
  B. Claude WebFetch — phase 01 picks up `pendingWebFetch[]` and calls
     WebFetch in-session, then `radar_fetch.py merge` re-injects results.
  C. User paste — sites still empty land in `pendingUserPaste[]`.

Empty-data policy diverges from inkos: inkos prompt-stuffs "no data, use
your knowledge" when all sources fail. SKILL keeps phase-01's hard
constraint by default ("don't fabricate"). Pass `--allow-knowledge-fallback`
to opt in to inkos-style behavior.

CLI:
  python radar_fetch.py scan [--sites a,b|all] [--genre xianxia|all] [--top 15]
                              [--max-age-hours 6] [--cache-dir <path>]
                              [--workdir <path>] [--no-cache]
                              [--out <path>] [--format json|markdown]
                              [--allow-knowledge-fallback]
  python radar_fetch.py merge --site <id> --paste @<path|inline>
                              [--cache-dir <path>] [--workdir <path>]
  python radar_fetch.py self-test [--site <id>]   # offline; doctor.py uses this
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCRIPT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_ROOT))

from radar import SOURCES  # noqa: E402
from radar._base import PlatformRankings, RankingEntry  # noqa: E402

KNOWN_SITES = list(SOURCES.keys())
DEFAULT_TOP = 15
DEFAULT_MAX_AGE_HOURS = 6


# ───────────────────────── workdir / cache ──────────────────────────


def find_project_root(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / "inkos.json").is_file():
            return p
    return None


def resolve_workdir(arg: Optional[str]) -> Path:
    """Mirror book.py: explicit > project root from cwd > cwd."""
    if arg:
        return Path(arg).resolve()
    root = find_project_root(Path.cwd())
    if root is not None:
        return root
    return Path.cwd().resolve()


def cache_dir_for(workdir: Path, override: Optional[str]) -> Path:
    if override:
        return Path(override).resolve()
    return workdir / ".radar-cache"


def cache_key(site: str, genre: Optional[str], ranking_type: str) -> str:
    g = genre or "all"
    rt = (ranking_type or "default").replace("/", "_").replace(" ", "_")
    return f"{site}__{g}__{rt}"


def cache_latest_path(cache_dir: Path, site: str, genre: Optional[str], ranking_type: str) -> Path:
    return cache_dir / f"{cache_key(site, genre, ranking_type)}__latest.json"


def cache_timestamped_path(cache_dir: Path, site: str, genre: Optional[str], ranking_type: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    return cache_dir / f"{cache_key(site, genre, ranking_type)}__{ts}.json"


def load_cache(cache_dir: Path, site: str, genre: Optional[str], max_age_hours: float) -> Optional[dict]:
    """Best cache hit across any ranking_type for this (site, genre).

    Returns the parsed JSON payload (a single rankings entry) or None.
    """
    if not cache_dir.is_dir():
        return None
    cutoff = time.time() - max_age_hours * 3600
    candidates = sorted(
        cache_dir.glob(f"{site}__{genre or 'all'}__*__latest.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for p in candidates:
        if p.stat().st_mtime < cutoff:
            continue
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def save_cache(cache_dir: Path, payload: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    site = payload.get("site", "unknown")
    genre = payload.get("genre")
    rt = payload.get("rankingType", "default")
    latest = cache_latest_path(cache_dir, site, genre, rt)
    snapshot = cache_timestamped_path(cache_dir, site, genre, rt)
    blob = json.dumps(payload, ensure_ascii=False, indent=2)
    latest.write_text(blob, encoding="utf-8")
    snapshot.write_text(blob, encoding="utf-8")


# ───────────────────────── scan ──────────────────────────


def serialize_rankings(pr: PlatformRankings, genre: Optional[str], fetched_via: str) -> dict:
    d = pr.to_dict()
    d["genre"] = genre or "all"
    d["fetchedVia"] = fetched_via
    d["fetchedAt"] = datetime.now(timezone.utc).isoformat()
    return d


def deserialize_rankings(d: dict) -> PlatformRankings:
    pr = PlatformRankings(
        site=d.get("site", ""),
        platform=d.get("platform", ""),
        ranking_type=d.get("rankingType", ""),
        source_url=d.get("sourceUrl", ""),
        fetched_via=d.get("fetchedVia", "cache"),
        warnings=list(d.get("warnings") or []),
        failures=list(d.get("failures") or []),
    )
    for e in d.get("entries", []) or []:
        pr.entries.append(RankingEntry(
            rank=int(e.get("rank", 0)),
            title=str(e.get("title", "")),
            author=str(e.get("author", "")),
            category=str(e.get("category", "")),
            url=str(e.get("url", "")),
            extra=str(e.get("extra", "")),
            stats=dict(e.get("stats") or {}),
        ))
    return pr


def scan(
    sites: list[str],
    genre: Optional[str],
    top: int,
    cache_dir: Path,
    use_cache: bool,
    max_age_hours: float,
) -> dict:
    rankings: list[dict] = []
    pending_webfetch: list[dict] = []
    pending_user_paste: list[dict] = []
    failures: list[dict] = []

    for site in sites:
        if site not in SOURCES:
            failures.append({"site": site, "stage": "config", "reason": "unknown site id"})
            continue

        payload: Optional[dict] = None
        if use_cache:
            payload = load_cache(cache_dir, site, genre, max_age_hours)
            if payload is not None:
                payload["fetchedVia"] = "cache"

        if payload is None:
            try:
                pr = SOURCES[site](genre, top)
            except Exception as e:  # adapters shouldn't raise; backstop only.
                failures.append({
                    "site": site, "stage": "adapter-crash",
                    "reason": f"{type(e).__name__}: {e}",
                })
                pending_webfetch.append({
                    "site": site,
                    "url": "",
                    "reason": "adapter raised (bug); WebFetch as backup",
                })
                continue
            payload = serialize_rankings(pr, genre, fetched_via="http")
            if pr.failures:
                failures.extend(
                    {"site": site, **f} for f in pr.failures
                )
            if pr.is_empty():
                pending_webfetch.append({
                    "site": site,
                    "url": pr.source_url,
                    "reason": (pr.failures[0]["reason"] if pr.failures
                               else (pr.warnings[0] if pr.warnings else "empty result")),
                })
            else:
                save_cache(cache_dir, payload)

        rankings.append(payload)

    return {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "request": {"sites": sites, "genre": genre or "all", "top": top},
        "rankings": rankings,
        "failures": failures,
        "pendingWebFetch": pending_webfetch,
        "pendingUserPaste": pending_user_paste,
    }


# ───────────────────────── markdown render ──────────────────────────


def render_markdown(payload: dict) -> str:
    lines: list[str] = []
    lines.append(f"# Radar 报告（{payload['fetchedAt']}）")
    req = payload["request"]
    lines.append(f"\n请求：站点 = {','.join(req['sites'])}，题材 = {req['genre']}，top = {req['top']}\n")
    for r in payload["rankings"]:
        lines.append(f"## {r['platform']}（{r.get('rankingType','')}）via {r['fetchedVia']}")
        if r.get("warnings"):
            lines.append("> warnings: " + "; ".join(r["warnings"]))
        if not r["entries"]:
            lines.append("（空）")
            continue
        for e in r["entries"]:
            seg = f"{e['rank']}. {e['title']}"
            if e.get("author"):
                seg += f" / {e['author']}"
            if e.get("category"):
                seg += f" [{e['category']}]"
            if e.get("extra"):
                seg += f" {e['extra']}"
            lines.append(seg)
        lines.append("")
    if payload.get("pendingWebFetch"):
        lines.append("## 待 WebFetch 兜底")
        for p in payload["pendingWebFetch"]:
            lines.append(f"- {p['site']} → {p['url']}（{p['reason']}）")
    if payload.get("failures"):
        lines.append("\n## 失败明细")
        for f in payload["failures"]:
            lines.append(f"- {f['site']} [{f['stage']}] {f['reason']}")
    return "\n".join(lines)


# ───────────────────────── merge (WebFetch / paste re-inject) ──────────────────────────


def parse_paste_input(raw: str) -> dict:
    """Accept either a full PlatformRankings JSON, or a minimal entries list.

    Minimal form (preferred for user-paste): one line per book,
        1. 书名 / 作者 [类别]
    or
        书名|作者|类别
    """
    raw = raw.strip()
    if raw.startswith("{") or raw.startswith("["):
        obj = json.loads(raw)
        if isinstance(obj, list):
            return {"entries": obj}
        return obj
    entries: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # try "1. title / author [category]"
        m = line
        rank = len(entries) + 1
        for prefix in (f"{rank}.", f"{rank}、", f"{rank})", f"{rank}）"):
            if m.startswith(prefix):
                m = m[len(prefix):].strip()
                break
        title = m
        author = ""
        category = ""
        if "[" in m and m.endswith("]"):
            i = m.rfind("[")
            category = m[i+1:-1].strip()
            m = m[:i].strip()
            title = m
        if "/" in m:
            title, _, author = m.partition("/")
            title = title.strip()
            author = author.strip()
        elif "|" in m:
            parts = [p.strip() for p in m.split("|")]
            title = parts[0]
            author = parts[1] if len(parts) > 1 else ""
            category = parts[2] if len(parts) > 2 else category
        if title:
            entries.append({"rank": rank, "title": title, "author": author, "category": category})
    return {"entries": entries}


def cmd_merge(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    cache_dir = cache_dir_for(workdir, args.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    raw = args.paste
    if raw.startswith("@"):
        raw = Path(raw[1:]).read_text(encoding="utf-8")
    parsed = parse_paste_input(raw)

    site = args.site
    if site not in SOURCES:
        print(json.dumps({"error": f"unknown site: {site}"}, ensure_ascii=False), file=sys.stderr)
        return 1

    payload = {
        "site": site,
        "platform": parsed.get("platform") or site,
        "rankingType": parsed.get("rankingType") or "user-paste",
        "sourceUrl": parsed.get("sourceUrl") or "",
        "fetchedVia": args.via,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "genre": args.genre or "all",
        "entries": parsed.get("entries", []),
        "warnings": parsed.get("warnings") or [],
        "failures": [],
    }
    save_cache(cache_dir, payload)
    print(json.dumps({
        "ok": True,
        "site": site,
        "savedTo": str(cache_latest_path(cache_dir, site, args.genre or "all",
                                          payload["rankingType"])),
        "entryCount": len(payload["entries"]),
    }, ensure_ascii=False, indent=2))
    return 0


# ───────────────────────── self-test (offline) ──────────────────────────


SELF_TEST_FIXTURES: dict[str, dict[str, Any]] = {
    "fanqie": {
        "kind": "json",
        "payload": {
            "data": {
                "result": [
                    {"book_name": "测试一号", "author": "甲", "category": "玄幻",
                     "book_id": "111"},
                    {"book_name": "测试二号", "author": "乙", "category": "都市",
                     "book_id": "222"},
                ]
            }
        },
        "expect_min": 2,
    },
    "qidian": {
        "kind": "html",
        "html": (
            '<a href="//book.qidian.com/info/111" class="bk">第一书名</a>'
            '<a href="//book.qidian.com/info/222" class="bk">第二书名</a>'
            '<a href="//book.qidian.com/info/111" class="bk">第一书名</a>'  # dup, skip
        ),
        "expect_min": 2,
    },
    "feilu": {
        "kind": "html",
        "html": (
            '<a href="//b.faloo.com/123456.html" target="_blank">飞卢测试一</a>'
            '<a href="https://b.faloo.com/789012.html">飞卢测试二</a>'
        ),
        "expect_min": 2,
    },
    "jjwxc": {
        "kind": "html",
        "html": (
            '<a title="晋江测试一" target="_blank" '
            'data-recommendInfo=\'{"relationNovelid":"1001","source":"pc"}\' '
            'href="//my.jjwxc.net/onebook.php?novelid=1001">'
            '<a title="晋江测试二" '
            'data-recommendInfo=\'{"relationNovelid":"1002"}\'>'
        ),
        "expect_min": 2,
    },
    "zongheng": {
        "kind": "html",
        "html": (
            '<a href="//book.zongheng.com/book/9001.html">纵横一号</a>'
            '<a href="https://book.zongheng.com/book/9002.html">纵横二号</a>'
        ),
        "expect_min": 2,
    },
    "sfacg": {
        "kind": "html",
        "html": (
            '<a href="/Novel/501/" title="SF一号">SF一号</a>'
            '<a href="/Novel/502/" title="SF二号"><img alt=""></a>'
        ),
        "expect_min": 2,
    },
}


def run_self_test(only_site: Optional[str] = None) -> dict:
    from radar import fanqie, feilu, jjwxc, qidian, sfacg, zongheng
    results: list[dict] = []
    sites = [only_site] if only_site else list(SELF_TEST_FIXTURES.keys())
    for site in sites:
        fx = SELF_TEST_FIXTURES.get(site)
        if not fx:
            results.append({"site": site, "ok": False, "reason": "no fixture"})
            continue
        try:
            if site == "fanqie":
                entries = fanqie.parse_api_response(fx["payload"], "热门榜")
            elif site == "qidian":
                entries = qidian.parse_html(fx["html"], "测试榜", 50)
            elif site == "feilu":
                entries = feilu.parse_html(fx["html"], "测试榜", 50)
            elif site == "jjwxc":
                entries = jjwxc.parse_html(fx["html"], "测试榜", 50)
            elif site == "zongheng":
                entries = zongheng.parse_html(fx["html"], "测试榜", 50)
            elif site == "sfacg":
                entries = sfacg.parse_html(fx["html"], "测试榜", 50)
            else:
                results.append({"site": site, "ok": False, "reason": "no parser"})
                continue
        except Exception as e:  # noqa: BLE001
            results.append({"site": site, "ok": False, "reason": f"{type(e).__name__}: {e}"})
            continue
        ok = len(entries) >= fx["expect_min"]
        results.append({
            "site": site,
            "ok": ok,
            "got": len(entries),
            "expectMin": fx["expect_min"],
            "sample": [e.title for e in entries[:3]],
        })
    summary = {
        "total": len(results),
        "passed": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
    }
    return {"results": results, "summary": summary}


def cmd_self_test(args: argparse.Namespace) -> int:
    report = run_self_test(args.site)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["failed"] == 0 else 2


# ───────────────────────── main / scan command ──────────────────────────


def cmd_scan(args: argparse.Namespace) -> int:
    workdir = resolve_workdir(args.workdir)
    cache_dir = cache_dir_for(workdir, args.cache_dir)

    if args.sites == "all":
        sites = list(KNOWN_SITES)
    else:
        sites = [s.strip() for s in args.sites.split(",") if s.strip()]

    genre = None if args.genre in (None, "", "all") else args.genre
    top = max(1, args.top)
    use_cache = not args.no_cache

    payload = scan(
        sites=sites,
        genre=genre,
        top=top,
        cache_dir=cache_dir,
        use_cache=use_cache,
        max_age_hours=args.max_age_hours,
    )

    payload["allowKnowledgeFallback"] = bool(args.allow_knowledge_fallback)

    if args.format == "markdown":
        text = render_markdown(payload)
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text)

    # exit code: 0 if any ranking has entries OR pendingWebFetch is non-empty
    # (Claude can salvage); 2 if everything is dead and no fallback queued.
    has_entries = any(len(r.get("entries") or []) > 0 for r in payload["rankings"])
    if has_entries or payload["pendingWebFetch"]:
        return 0
    return 2


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Auto-fetch market rankings from novel sites (radar).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scan", help="Fetch rankings (default cmd)")
    sp.add_argument("--sites", default="all",
                    help=f"comma-separated list or 'all' (known: {','.join(KNOWN_SITES)})")
    sp.add_argument("--genre", default=None,
                    help="SKILL genre id (xianxia/xuanhuan/urban/...) or 'all'")
    sp.add_argument("--top", type=int, default=DEFAULT_TOP)
    sp.add_argument("--max-age-hours", type=float, default=DEFAULT_MAX_AGE_HOURS)
    sp.add_argument("--cache-dir", default=None)
    sp.add_argument("--workdir", default=None)
    sp.add_argument("--no-cache", action="store_true")
    sp.add_argument("--out", default=None, help="write to file instead of stdout")
    sp.add_argument("--format", choices=("json", "markdown"), default="json")
    sp.add_argument("--allow-knowledge-fallback", action="store_true",
                    help="signal phase 01 to let LLM use prior knowledge when "
                         "all fetches fail (off by default to honor SKILL hard "
                         "constraint against fabrication)")
    sp.set_defaults(func=cmd_scan)

    mp = sub.add_parser("merge", help="Inject WebFetch/user-paste result into cache")
    mp.add_argument("--site", required=True, help=f"one of: {','.join(KNOWN_SITES)}")
    mp.add_argument("--paste", required=True,
                    help="raw text or @<path>; either entries-list JSON or '1. title / author' lines")
    mp.add_argument("--via", choices=("webfetch", "user-paste"), default="user-paste")
    mp.add_argument("--genre", default=None)
    mp.add_argument("--cache-dir", default=None)
    mp.add_argument("--workdir", default=None)
    mp.set_defaults(func=cmd_merge)

    tp = sub.add_parser("self-test", help="Offline parser test against fixtures")
    tp.add_argument("--site", default=None, choices=KNOWN_SITES)
    tp.set_defaults(func=cmd_self_test)

    return p.parse_args()


def main() -> int:
    args = parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
