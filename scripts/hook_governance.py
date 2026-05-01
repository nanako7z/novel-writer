#!/usr/bin/env python3
"""Hook governance subsystem (Python port of inkos hook-* utils).

Commands:
  promote-pass   apply 4 promotion rules to seeds and write back to hooks.json
  stale-scan     mark hooks stale based on per-type halfLifeChapters
  validate       cross-file consistency: hooks.json vs pending_hooks.md vs
                 chapter_summaries.json + depends_on cycles
  health-report  per-hook freshness + ledger pressure metrics

All commands print structured JSON to stdout.  Non-zero exit ONLY on hard
errors (parser failure / IO / bad args).  Validation findings go in the
"issues" array — caller decides whether they are gates.

Defaults (mirrored from .inkos-src/utils/hook-policy.ts +
hook-promotion.ts):

  HOOK_HEALTH_DEFAULTS:
    maxActiveHooks       = 12
    staleAfterChapters   = 10
    noAdvanceWindow      = 5
    newHookBurstThreshold = 2

  default half-life by payoffTiming:
    immediate / near-term  -> 10
    mid-arc (and unknown)  -> 30
    slow-burn / endgame    -> 80

  promotion: any of (cross_volume / advancedCount>=2 / depends_on non-empty
  / coreHook==True) flips promoted=True.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ----------------------------- defaults ------------------------------------

HOOK_HEALTH_DEFAULTS = {
    "maxActiveHooks": 12,
    "staleAfterChapters": 10,
    "noAdvanceWindow": 5,
    "newHookBurstThreshold": 2,
}

# ledger soft cap: when total hooks > LEDGER_PRESSURE_LIMIT we report
# "ledger pressure" in the health report so the orchestrator can prune.
LEDGER_PRESSURE_LIMIT = 30

CHINESE_NUMERALS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}

VOLUME_PATTERNS = [
    re.compile(r"第\s*([一二三四五六七八九十百千\d]+)\s*卷"),
    re.compile(r"volume\s+(\d+)", re.IGNORECASE),
    re.compile(r"vol\.?\s*(\d+)", re.IGNORECASE),
]

RESOLVED_STATUSES = {"resolved", "closed", "done", "已回收", "已解决"}


# ------------------------- io helpers --------------------------------------

def hard_err(msg: str, code: int = 2) -> "None":
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False),
          file=sys.stderr)
    sys.exit(code)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        hard_err(f"corrupt json at {path}: {e}")


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    import os as _os
    _os.replace(tmp, path)


# --------------------------- shared logic ----------------------------------

def default_half_life(payoff_timing: str | None) -> int:
    if payoff_timing in ("immediate", "near-term"):
        return 10
    if payoff_timing in ("slow-burn", "endgame"):
        return 80
    # mid-arc + unknown
    return 30


def resolve_half_life(hook: dict) -> int:
    hl = hook.get("halfLifeChapters")
    if isinstance(hl, int) and hl > 0:
        return hl
    return default_half_life(hook.get("payoffTiming"))


def is_resolved(hook: dict) -> bool:
    status = (hook.get("status") or "").strip().lower()
    return status in RESOLVED_STATUSES or status in ("resolved",)


def parse_volume_token(token: str) -> int | None:
    if re.fullmatch(r"\d+", token):
        return int(token)
    if len(token) == 1 and token in CHINESE_NUMERALS:
        return CHINESE_NUMERALS[token]
    if token == "十":
        return 10
    return None


def extract_volume_index_from_arc(arc: str) -> int | None:
    arc = (arc or "").strip()
    if not arc:
        return None
    for pat in VOLUME_PATTERNS:
        m = pat.search(arc)
        if not m:
            continue
        n = parse_volume_token(m.group(1))
        if n is not None:
            return n - 1  # 1-indexed in prose, 0 here
    return None


def find_volume_index(boundaries: list[dict], chapter: int) -> int:
    for i, vol in enumerate(boundaries):
        if vol["startCh"] <= chapter <= vol["endCh"]:
            return i
    if chapter <= 0 and boundaries:
        return 0
    return -1


def is_cross_volume(hook: dict, boundaries: list[dict],
                    seed_starts: dict[str, int]) -> bool:
    if len(boundaries) < 2:
        return False
    seed_idx = find_volume_index(boundaries, hook.get("startChapter", 0))
    if seed_idx < 0:
        return False
    # Case A: upstream declared in later volume
    for upstream in hook.get("dependsOn", []) or []:
        up_start = seed_starts.get(upstream)
        if up_start is None:
            continue
        up_idx = find_volume_index(boundaries, up_start)
        if up_idx > seed_idx:
            return True
    # Case B: paysOffInArc names a different volume
    arc_idx = extract_volume_index_from_arc(hook.get("paysOffInArc") or "")
    if arc_idx is not None and arc_idx != seed_idx:
        return True
    # Case C: endgame/slow-burn planted in non-final volume
    timing = hook.get("payoffTiming")
    if timing in ("endgame", "slow-burn") and seed_idx < len(boundaries) - 1:
        return True
    return False


def should_promote(hook: dict, boundaries: list[dict],
                   seed_starts: dict[str, int]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if hook.get("coreHook") is True:
        reasons.append("core_hook")
    if (hook.get("dependsOn") or []) and len(hook["dependsOn"]) > 0:
        reasons.append("depends_on")
    advanced = hook.get("advancedCount") or 0
    if isinstance(advanced, int) and advanced >= 2:
        reasons.append("advanced_count")
    if is_cross_volume(hook, boundaries, seed_starts):
        reasons.append("cross_volume")
    return (len(reasons) > 0, reasons)


# ------------------------- volume-map parsing ------------------------------

def parse_volume_boundaries(book_dir: Path) -> list[dict]:
    """Read `story/outline/volume_map.md` if present and parse boundaries.

    Tolerant to layout — we look for lines like "第 N 卷 ... ch 1-30" or
    "volume 1: 1-30".  Returns ordered list of {name, startCh, endCh}.
    """
    p = book_dir / "story" / "outline" / "volume_map.md"
    if not p.exists():
        return []
    boundaries: list[dict] = []
    text = p.read_text(encoding="utf-8")
    # match "第N卷 ... 1-30" or "volume N: a-b"
    range_re = re.compile(r"(\d+)\s*[-–~]\s*(\d+)")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # find a chapter-range token on the line
        m = range_re.search(line)
        if not m:
            continue
        start = int(m.group(1))
        end = int(m.group(2))
        if end < start:
            continue
        # name = strip range portion off the line
        name = line.replace(m.group(0), "").strip(" :|·-—–")[:60] or f"vol-{len(boundaries)+1}"
        boundaries.append({"name": name, "startCh": start, "endCh": end})
    boundaries.sort(key=lambda v: v["startCh"])
    return boundaries


# ----------------------------- commands ------------------------------------

def cmd_promote_pass(book_dir: Path, _: int | None) -> dict:
    state_dir = book_dir / "story" / "state"
    runtime_dir = book_dir / "story" / "runtime"
    hooks_path = state_dir / "hooks.json"
    seeds_path = runtime_dir / "hook-seeds.json"

    hooks_obj = load_json(hooks_path, {"hooks": []})
    seeds_obj = load_json(seeds_path, {"seeds": []})
    if not isinstance(hooks_obj, dict):
        hooks_obj = {"hooks": []}
    hooks: list[dict] = list(hooks_obj.get("hooks", []) or [])
    seeds: list[dict] = list((seeds_obj or {}).get("seeds", []) or [])

    boundaries = parse_volume_boundaries(book_dir)

    # Index every seed/hook startChapter for cross-volume Case A lookups.
    seed_starts: dict[str, int] = {}
    for h in hooks + seeds:
        if isinstance(h, dict) and h.get("hookId"):
            seed_starts[h["hookId"]] = int(h.get("startChapter", 0) or 0)

    flipped: list[dict] = []
    promoted_from_seeds: list[dict] = []

    by_id = {h.get("hookId"): h for h in hooks if isinstance(h, dict)}

    # 1. existing hooks: flip promoted flag
    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        if hook.get("promoted") is True:
            continue
        promote, reasons = should_promote(hook, boundaries, seed_starts)
        if promote:
            hook["promoted"] = True
            flipped.append({"hookId": hook.get("hookId"), "reasons": reasons})

    # 2. seeds: any seed that crosses promotion threshold migrates into the
    # main hooks ledger (it stops being just-a-seed).  Seeds remain in the
    # seeds file regardless — promotion is a one-way upgrade.
    for seed in seeds:
        if not isinstance(seed, dict) or not seed.get("hookId"):
            continue
        sid = seed["hookId"]
        if sid in by_id:
            continue  # already a hook
        promote, reasons = should_promote(seed, boundaries, seed_starts)
        if promote:
            new_hook = dict(seed)
            new_hook["promoted"] = True
            hooks.append(new_hook)
            by_id[sid] = new_hook
            promoted_from_seeds.append({"hookId": sid, "reasons": reasons})

    hooks_obj["hooks"] = hooks
    atomic_write_json(hooks_path, hooks_obj)

    return {
        "ok": True,
        "command": "promote-pass",
        "flipped": flipped,
        "promotedFromSeeds": promoted_from_seeds,
        "totalHooks": len(hooks),
        "totalSeeds": len(seeds),
        "volumeCount": len(boundaries),
    }


def cmd_stale_scan(book_dir: Path, current_chapter: int | None) -> dict:
    state_dir = book_dir / "story" / "state"
    hooks_path = state_dir / "hooks.json"
    manifest_path = state_dir / "manifest.json"

    hooks_obj = load_json(hooks_path, {"hooks": []})
    if not isinstance(hooks_obj, dict):
        hooks_obj = {"hooks": []}
    hooks: list[dict] = list(hooks_obj.get("hooks", []) or [])

    if current_chapter is None:
        manifest = load_json(manifest_path, {})
        if isinstance(manifest, dict):
            current_chapter = int(manifest.get("lastAppliedChapter", 0) or 0)
        else:
            current_chapter = 0

    # Also need cross-references for the blocked check.
    by_id = {h.get("hookId"): h for h in hooks if isinstance(h, dict)}

    marked_stale: list[dict] = []
    marked_blocked: list[dict] = []

    for hook in hooks:
        if not isinstance(hook, dict):
            continue
        half_life = resolve_half_life(hook)
        planted = max(0, int(hook.get("startChapter", 0) or 0))
        distance = max(0, current_chapter - planted)

        stale = (not is_resolved(hook)) and planted > 0 and distance > half_life

        # blocked: depends_on references unplanted/unresolved upstream
        missing_upstream: list[str] = []
        for up in hook.get("dependsOn", []) or []:
            up_hook = by_id.get(up)
            if up_hook is None:
                missing_upstream.append(up)
                continue
            up_planted = (int(up_hook.get("startChapter", 0) or 0) > 0
                          and int(up_hook.get("startChapter", 0) or 0) <= current_chapter)
            if (not up_planted) or (not is_resolved(up_hook)):
                missing_upstream.append(up)
        blocked = bool(missing_upstream) and not is_resolved(hook)

        prev_stale = bool(hook.get("stale"))
        prev_blocked = bool(hook.get("blocked"))
        hook["stale"] = stale
        hook["blocked"] = blocked
        hook["staleDistance"] = distance
        hook["staleHalfLife"] = half_life
        if missing_upstream:
            hook["missingUpstream"] = missing_upstream
        elif "missingUpstream" in hook:
            del hook["missingUpstream"]

        if stale and not prev_stale:
            marked_stale.append({
                "hookId": hook.get("hookId"),
                "distance": distance,
                "halfLife": half_life,
            })
        if blocked and not prev_blocked:
            marked_blocked.append({
                "hookId": hook.get("hookId"),
                "missingUpstream": missing_upstream,
            })

    hooks_obj["hooks"] = hooks
    atomic_write_json(hooks_path, hooks_obj)

    total_stale = sum(1 for h in hooks if isinstance(h, dict) and h.get("stale"))
    total_blocked = sum(1 for h in hooks if isinstance(h, dict) and h.get("blocked"))

    return {
        "ok": True,
        "command": "stale-scan",
        "currentChapter": current_chapter,
        "newlyStale": marked_stale,
        "newlyBlocked": marked_blocked,
        "totalStale": total_stale,
        "totalBlocked": total_blocked,
        "totalHooks": len(hooks),
    }


def detect_dep_cycles(hooks: list[dict]) -> list[list[str]]:
    """Return a list of cycles (each cycle is a list of hookIds)."""
    graph: dict[str, list[str]] = {}
    for h in hooks:
        if isinstance(h, dict) and h.get("hookId"):
            graph[h["hookId"]] = list(h.get("dependsOn") or [])

    cycles: list[list[str]] = []
    visited: set[str] = set()
    on_stack: dict[str, int] = {}
    stack: list[str] = []

    def dfs(node: str) -> None:
        if node in on_stack:
            idx = on_stack[node]
            cycles.append(stack[idx:] + [node])
            return
        if node in visited:
            return
        visited.add(node)
        on_stack[node] = len(stack)
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt in graph:
                dfs(nxt)
        stack.pop()
        del on_stack[node]

    for n in graph:
        if n not in visited:
            dfs(n)
    # dedupe by frozenset of nodes (cycle directionless for reporting)
    seen: set[frozenset] = set()
    unique: list[list[str]] = []
    for c in cycles:
        key = frozenset(c)
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique


def parse_pending_hooks_md(path: Path) -> list[str]:
    """Extract hookIds referenced in pending_hooks.md table cells."""
    if not path.exists():
        return []
    ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        if "---" in line:
            continue
        # take first non-empty cell
        cells = [c.strip() for c in line.split("|")]
        for c in cells:
            if not c:
                continue
            # skip header
            if c.lower() in ("hookid", "hook id", "id", "伏笔", "伏笔id"):
                break
            # plausible id: alnum / underscore / dash / CJK
            if re.fullmatch(r"[A-Za-z0-9_\-一-鿿]{1,40}", c):
                ids.append(c)
            break
    return ids


def cmd_validate(book_dir: Path, current_chapter: int | None) -> dict:
    state_dir = book_dir / "story" / "state"
    story_dir = book_dir / "story"

    hooks_obj = load_json(state_dir / "hooks.json", {"hooks": []})
    if not isinstance(hooks_obj, dict):
        hooks_obj = {"hooks": []}
    hooks: list[dict] = list(hooks_obj.get("hooks", []) or [])

    summaries_obj = load_json(state_dir / "chapter_summaries.json",
                              {"summaries": []})
    summaries = (summaries_obj or {}).get("summaries", []) if isinstance(summaries_obj, dict) else []

    issues: list[dict] = []

    by_id = {h.get("hookId"): h for h in hooks if isinstance(h, dict)}

    # 1. depends_on: every reference must resolve (warning if not — could be
    #    a forward reference that hasn't been seeded yet, but worth flagging).
    for h in hooks:
        if not isinstance(h, dict):
            continue
        for up in h.get("dependsOn", []) or []:
            if up not in by_id:
                issues.append({
                    "severity": "warning",
                    "category": "dangling_dep",
                    "hookId": h.get("hookId"),
                    "message": f"depends_on references unknown hookId '{up}'",
                })

    # 2. cycles in depends_on  -> CRITICAL
    cycles = detect_dep_cycles(hooks)
    for c in cycles:
        issues.append({
            "severity": "critical",
            "category": "dep_cycle",
            "cycle": c,
            "message": f"depends_on cycle: {' -> '.join(c)}",
        })

    # 3. pending_hooks.md cross-reference: every id mentioned in the .md
    #    must exist in hooks.json (warning for stale ledger rows).
    pending_ids = parse_pending_hooks_md(story_dir / "pending_hooks.md")
    for pid in pending_ids:
        if pid not in by_id:
            issues.append({
                "severity": "warning",
                "category": "stale_ledger_row",
                "hookId": pid,
                "message": f"pending_hooks.md references hookId '{pid}' not present in hooks.json",
            })

    # 4. chapter_summaries.json hookActivity references must resolve.
    if isinstance(summaries, list):
        for row in summaries:
            if not isinstance(row, dict):
                continue
            ha = (row.get("hookActivity") or "")
            ch = row.get("chapter")
            if not isinstance(ha, str):
                continue
            for token in re.findall(r"[A-Za-z0-9_\-一-鿿]{2,40}", ha):
                if token in {"advanced", "resolved", "deferred", "mentioned",
                             "open", "and"}:
                    continue
                if token in by_id:
                    continue
                # only warn for tokens that look like ids (have a hyphen or
                # mixed case or starts with capital)
                if "-" in token or "_" in token or re.match(r"^[A-Z]", token):
                    issues.append({
                        "severity": "info",
                        "category": "summary_unknown_token",
                        "chapter": ch,
                        "token": token,
                        "message": f"chapter_summaries[{ch}].hookActivity references '{token}' (not in hooks.json)",
                    })

    # 5. promoted hooks should have at least one promotion-worthy signal.
    boundaries = parse_volume_boundaries(book_dir)
    seed_starts = {h.get("hookId"): int(h.get("startChapter", 0) or 0)
                   for h in hooks if isinstance(h, dict) and h.get("hookId")}
    for h in hooks:
        if not isinstance(h, dict):
            continue
        if h.get("promoted") is True:
            ok, reasons = should_promote(h, boundaries, seed_starts)
            if not ok:
                issues.append({
                    "severity": "warning",
                    "category": "unjustified_promotion",
                    "hookId": h.get("hookId"),
                    "message": "promoted=true but no promotion condition currently holds",
                })

    counts = {
        "critical": sum(1 for i in issues if i["severity"] == "critical"),
        "warning": sum(1 for i in issues if i["severity"] == "warning"),
        "info": sum(1 for i in issues if i["severity"] == "info"),
    }

    return {
        "ok": True,
        "command": "validate",
        "issues": issues,
        "counts": counts,
        "totalHooks": len(hooks),
    }


def cmd_health_report(book_dir: Path, current_chapter: int | None) -> dict:
    state_dir = book_dir / "story" / "state"
    hooks_obj = load_json(state_dir / "hooks.json", {"hooks": []})
    if not isinstance(hooks_obj, dict):
        hooks_obj = {"hooks": []}
    hooks: list[dict] = list(hooks_obj.get("hooks", []) or [])

    if current_chapter is None:
        manifest = load_json(state_dir / "manifest.json", {})
        if isinstance(manifest, dict):
            current_chapter = int(manifest.get("lastAppliedChapter", 0) or 0)
        else:
            current_chapter = 0

    per_hook: list[dict] = []
    active = 0
    stale_count = 0
    blocked_count = 0
    resolved_count = 0
    deferred_count = 0
    latest_advance = 0

    for h in hooks:
        if not isinstance(h, dict):
            continue
        status = (h.get("status") or "").lower()
        last_adv = int(h.get("lastAdvancedChapter", 0) or 0)
        planted = int(h.get("startChapter", 0) or 0)
        half_life = resolve_half_life(h)
        distance = max(0, current_chapter - planted)
        # freshness 1.0 when last advanced this chapter, 0.0 when last advance
        # was >= half_life chapters ago.  Linear ramp.
        chapters_since_advance = max(0, current_chapter - last_adv)
        freshness = max(0.0, min(1.0, 1.0 - (chapters_since_advance / max(1, half_life))))

        if is_resolved(h):
            resolved_count += 1
        elif status == "deferred":
            deferred_count += 1
        else:
            active += 1
            if h.get("stale") or (planted > 0 and distance > half_life):
                stale_count += 1
            if h.get("blocked"):
                blocked_count += 1
            latest_advance = max(latest_advance, last_adv)

        per_hook.append({
            "hookId": h.get("hookId"),
            "status": status,
            "type": h.get("type"),
            "payoffTiming": h.get("payoffTiming"),
            "promoted": bool(h.get("promoted")),
            "coreHook": bool(h.get("coreHook")),
            "startChapter": planted,
            "lastAdvancedChapter": last_adv,
            "distance": distance,
            "halfLife": half_life,
            "freshness": round(freshness, 3),
            "stale": bool(h.get("stale") or (status not in RESOLVED_STATUSES
                                             and planted > 0
                                             and distance > half_life)),
            "blocked": bool(h.get("blocked")),
        })

    chapters_since_any_advance = max(0, current_chapter - latest_advance) if active else 0

    ledger_pressure = "ok"
    if active > HOOK_HEALTH_DEFAULTS["maxActiveHooks"]:
        ledger_pressure = "high"
    elif active > HOOK_HEALTH_DEFAULTS["maxActiveHooks"] - 3:
        ledger_pressure = "warn"
    if len(hooks) > LEDGER_PRESSURE_LIMIT:
        ledger_pressure = "high"

    return {
        "ok": True,
        "command": "health-report",
        "currentChapter": current_chapter,
        "totalHooks": len(hooks),
        "activeCount": active,
        "staleCount": stale_count,
        "blockedCount": blocked_count,
        "resolvedCount": resolved_count,
        "deferredCount": deferred_count,
        "chaptersSinceAnyAdvance": chapters_since_any_advance,
        "noAdvanceWindow": HOOK_HEALTH_DEFAULTS["noAdvanceWindow"],
        "maxActiveHooks": HOOK_HEALTH_DEFAULTS["maxActiveHooks"],
        "ledgerPressure": ledger_pressure,
        "perHook": per_hook,
    }


# ------------------------------- main --------------------------------------

COMMANDS = {
    "promote-pass": cmd_promote_pass,
    "stale-scan": cmd_stale_scan,
    "validate": cmd_validate,
    "health-report": cmd_health_report,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Hook governance: promote-pass / stale-scan / validate / "
                    "health-report.",
    )
    p.add_argument("--book", required=True, help="book directory")
    p.add_argument("--command", required=True, choices=sorted(COMMANDS.keys()))
    p.add_argument("--current-chapter", type=int, default=None,
                   help="override manifest.json#lastAppliedChapter")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    book = Path(args.book).resolve()
    if not book.is_dir():
        hard_err(f"book directory not found: {book}")

    fn = COMMANDS[args.command]
    try:
        out = fn(book, args.current_chapter)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        hard_err(f"{args.command} failed: {e!r}")
        return 2  # unreachable

    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
