#!/usr/bin/env python3
"""Context budget enforcement for the Composer phase (gap item #11).

Stdlib-only. Reads a `context_pkg.json` (Composer's pre-Writer assembly),
enforces per-category character budgets, drops / truncates content by a
priority order, and emits a budgeted package + an audit report.

Inkos's `buildGovernedContextPackage` enforces this in TypeScript at write
time; in SKILL form Composer assembles the pkg in markdown / json and we
run this script as the last step of phase 03 right before the Writer
prompt is built. After 30+ chapters `recentSummaries` and `relevantSummaries`
tend to dominate token budget and squeeze out current state / hooks; this
script applies a structural pass to keep load-bearing categories.

CLI:
    python context_budget.py --input <context_pkg.json> \
        [--profile default|strict|loose] \
        [--budget-total 80000] \
        [--out <budgeted.json>] \
        [--json]

Exit codes:
    0 — budget enforced (ok / adjusted / hard-overflow are all "data");
        callers inspect `budgetStatus` rather than exit code.
    1 — usage / IO error.

Output schema:
    {
      "ok": true,
      "budgetStatus": "ok" | "adjusted" | "hard-overflow",
      "totalCharsBefore": int,
      "totalCharsAfter":  int,
      "budgetTotal":      int,
      "profile":          "default",
      "perCategory": [
        {"name": "recentSummaries",
         "before": 18234, "after": 11800,
         "action": "truncated-summaries", "kept": 6, "dropped": 4}
      ],
      "warnings": ["..."],
      "budgetedContext": { ... budgeted context_pkg ... }
    }
"""
from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

# ────────────────────────── Profiles ──────────────────────────
#
# Each entry: (default_budget_chars, drop_priority_1to5)
#   drop_priority 1 = drop / shrink first
#   drop_priority 5 = load-bearing, never drop entirely
#
# Tunables tracked in references/context-budget.md. Adjust in lockstep.

PROFILE_DEFAULT: dict[str, tuple[int, int]] = {
    "chapterMemo":         (4000, 5),  # never drop
    "currentState":        (3000, 4),  # load-bearing; can compress
    "recentSummaries":     (12000, 3), # truncate to fewer chapters first
    "relevantSummaries":   (8000, 2),  # truncate first
    "activeHooks":         (5000, 4),  # compress entries; never drop entirely
    "characterMatrix":     (4000, 3),
    "subplotBoard":        (3000, 2),
    "emotionalArcs":       (2000, 2),
    "styleGuide":          (4000, 4),
    "genreProfile":        (5000, 5),
    "bookRules":           (3000, 4),
    "fanficCanon":         (6000, 4),  # only present in fanfic mode
    "auditDriftGuidance":  (1500, 5),  # load-bearing for next-chapter avoidance
}

# strict: tighter caps overall (~75% of default), aggressive drop priorities.
PROFILE_STRICT: dict[str, tuple[int, int]] = {
    k: (int(v[0] * 0.75), v[1]) for k, v in PROFILE_DEFAULT.items()
}

# loose: more headroom (~135%) when budget allows.
PROFILE_LOOSE: dict[str, tuple[int, int]] = {
    k: (int(v[0] * 1.35), v[1]) for k, v in PROFILE_DEFAULT.items()
}

PROFILES = {
    "default": PROFILE_DEFAULT,
    "strict":  PROFILE_STRICT,
    "loose":   PROFILE_LOOSE,
}

# Hard floor: no category may shrink below 30% of its quota.
HARD_FLOOR_FACTOR = 0.30

DEFAULT_BUDGET_TOTAL = 80000


# ───────────────── Size accounting ─────────────────


def category_size(value: Any) -> int:
    """Character size of a category value (CJK 1 char = 1 unit, ASCII same).

    For dicts / lists we serialize compactly; this is a *budget proxy*, not a
    token count — close enough for heuristic shedding.
    """
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (dict, list)):
        try:
            return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        except (TypeError, ValueError):
            return len(str(value))
    return len(str(value))


def total_size(pkg: dict[str, Any], schema: dict[str, tuple[int, int]]) -> int:
    return sum(category_size(pkg.get(name)) for name in schema if name in pkg)


# ───────────────── Truncation strategies ─────────────────


def truncate_string(s: str, target: int) -> str:
    if not isinstance(s, str) or len(s) <= target:
        return s
    if target <= 0:
        return ""
    # Keep the head; tail-truncate with an ellipsis marker.
    keep = max(0, target - 3)
    return s[:keep] + "..."


def truncate_recent_summaries(value: Any, target: int) -> tuple[Any, dict[str, Any]]:
    """Drop oldest summaries first; if still over, tail-truncate per-entry."""
    if not isinstance(value, list):
        if isinstance(value, str):
            new = truncate_string(value, target)
            return new, {"action": "tail-truncate", "kept": None, "dropped": None}
        return value, {"action": "noop"}
    if not value:
        return value, {"action": "noop"}

    items = list(value)
    # Sort by chapter ascending if available, drop oldest first.
    def chap_key(it: Any) -> int:
        if isinstance(it, dict):
            try:
                return int(it.get("chapter", 0) or 0)
            except (TypeError, ValueError):
                return 0
        return 0
    items.sort(key=chap_key)

    original_count = len(items)
    while items and category_size(items) > target:
        items.pop(0)  # drop oldest
    dropped = original_count - len(items)

    if items and category_size(items) > target:
        # Per-entry compression: keep only chapter / title / events fields.
        compressed: list[Any] = []
        for it in items:
            if isinstance(it, dict):
                compressed.append({
                    "chapter": it.get("chapter"),
                    "title": it.get("title", ""),
                    "events": truncate_string(str(it.get("events", "")), 200),
                })
            else:
                compressed.append(it)
        items = compressed
        action = "truncated-summaries+compressed-entries"
    else:
        action = "truncated-summaries"

    return items, {
        "action": action,
        "kept": len(items),
        "dropped": dropped,
    }


def truncate_relevant_summaries(value: Any, target: int) -> tuple[Any, dict[str, Any]]:
    """Same shape as recent; drop oldest first (these are deeper history)."""
    return truncate_recent_summaries(value, target)


def truncate_active_hooks(value: Any, target: int) -> tuple[Any, dict[str, Any]]:
    """Compress hook entries: keep id + status + expectedPayoff only.

    Never drop entries entirely (priority 4) — Writer needs to know the
    full active-hook set to honor the commitment ledger.
    """
    if not isinstance(value, list) or not value:
        return value, {"action": "noop"}

    # Pass 1: full entries — fits?
    if category_size(value) <= target:
        return value, {"action": "noop"}

    # Pass 2: compressed entries.
    compressed = []
    for h in value:
        if isinstance(h, dict):
            compressed.append({
                "hookId": h.get("hookId"),
                "type": h.get("type", ""),
                "status": h.get("status", ""),
                "lastAdvancedChapter": h.get("lastAdvancedChapter"),
                "expectedPayoff": truncate_string(
                    str(h.get("expectedPayoff", "")), 120
                ),
            })
        else:
            compressed.append(h)

    return compressed, {
        "action": "compressed-hook-entries",
        "kept": len(compressed),
        "dropped": 0,
    }


def truncate_subplot_board(value: Any, target: int) -> tuple[Any, dict[str, Any]]:
    """Drop closed / resolved subplots first, then tail-truncate."""
    if isinstance(value, list):
        items = list(value)
        before_count = len(items)
        # Drop closed/resolved entries first.
        kept = [
            it for it in items
            if not (
                isinstance(it, dict)
                and str(it.get("status", "")).lower() in {"closed", "resolved", "done"}
            )
        ]
        if category_size(kept) <= target:
            return kept, {
                "action": "dropped-closed-subplots",
                "kept": len(kept),
                "dropped": before_count - len(kept),
            }
        # Still over: tail-truncate the list.
        while kept and category_size(kept) > target:
            kept.pop()
        return kept, {
            "action": "dropped-closed+truncated",
            "kept": len(kept),
            "dropped": before_count - len(kept),
        }
    if isinstance(value, str):
        return truncate_string(value, target), {"action": "tail-truncate"}
    return value, {"action": "noop"}


def truncate_emotional_arcs(value: Any, target: int) -> tuple[Any, dict[str, Any]]:
    """Drop oldest entries first (priority 2 — fine to lose history)."""
    if isinstance(value, list):
        items = list(value)
        before = len(items)
        while items and category_size(items) > target:
            items.pop(0)
        return items, {
            "action": "dropped-old-arcs",
            "kept": len(items),
            "dropped": before - len(items),
        }
    if isinstance(value, str):
        return truncate_string(value, target), {"action": "tail-truncate"}
    return value, {"action": "noop"}


def truncate_character_matrix(value: Any, target: int) -> tuple[Any, dict[str, Any]]:
    """Keep relationships involving protagonist / recent characters first."""
    if isinstance(value, list):
        items = list(value)
        before = len(items)
        # Deterministic: drop tail (less-recent / less-central entries).
        while items and category_size(items) > target:
            items.pop()
        return items, {
            "action": "trimmed-matrix-rows",
            "kept": len(items),
            "dropped": before - len(items),
        }
    if isinstance(value, str):
        return truncate_string(value, target), {"action": "tail-truncate"}
    return value, {"action": "noop"}


def truncate_generic(value: Any, target: int) -> tuple[Any, dict[str, Any]]:
    """Default: tail-truncate strings; compact-serialize-then-truncate dicts/lists."""
    if isinstance(value, str):
        return truncate_string(value, target), {"action": "tail-truncate"}
    if isinstance(value, (dict, list)):
        s = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(s) <= target:
            return value, {"action": "noop"}
        s = truncate_string(s, target)
        # We deliberately return the truncated *string* here — the caller
        # surfaces this as "lossy" via the action label. Better than dropping.
        return s, {"action": "tail-truncate-serialized"}
    return value, {"action": "noop"}


TRUNCATORS: dict[str, Callable[[Any, int], tuple[Any, dict[str, Any]]]] = {
    "recentSummaries":   truncate_recent_summaries,
    "relevantSummaries": truncate_relevant_summaries,
    "activeHooks":       truncate_active_hooks,
    "subplotBoard":      truncate_subplot_board,
    "emotionalArcs":     truncate_emotional_arcs,
    "characterMatrix":   truncate_character_matrix,
}


def truncate_category(name: str, value: Any, target: int) -> tuple[Any, dict[str, Any]]:
    fn = TRUNCATORS.get(name, truncate_generic)
    return fn(value, target)


# ───────────────── Budget enforcement loop ─────────────────


def enforce_budget(
    pkg: dict[str, Any],
    schema: dict[str, tuple[int, int]],
    budget_total: int,
) -> dict[str, Any]:
    """Apply the four-pass priority shedding algorithm.

    1. Pass 1 (priority=1): try to fit within per-category quotas.
    2. If still over, walk priority levels 2 → 5 ascending; for each level,
       shrink any category at that drop-priority that exceeds 0.6× its quota.
    3. Hard floor: never go below quota × 0.30.
    4. If still over after all passes at floors → status hard-overflow.
    """
    out = deepcopy(pkg)
    perf: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    sizes_before = {
        name: category_size(out.get(name)) for name in schema if name in out
    }
    total_before = sum(sizes_before.values())

    # Pass 1: trim any category that exceeds its own quota.
    for name, (quota, _prio) in schema.items():
        if name not in out:
            continue
        cur = category_size(out.get(name))
        if cur > quota:
            new_val, info = truncate_category(name, out[name], quota)
            out[name] = new_val
            perf[name] = {
                "before": cur,
                "after": category_size(new_val),
                **info,
            }

    # Subsequent passes by priority.
    def total() -> int:
        return sum(category_size(out.get(n)) for n in schema if n in out)

    if total() <= budget_total:
        status = "adjusted" if perf else "ok"
        return _build_report(out, sizes_before, total_before, schema,
                             budget_total, status, perf, warnings)

    for level in (1, 2, 3, 4):
        if total() <= budget_total:
            break
        # Cycle: priority 1 first → most aggressive.
        eligible = [n for n, (_q, p) in schema.items() if p == level and n in out]
        # Sort by current size desc — biggest first.
        eligible.sort(key=lambda n: -category_size(out.get(n)))
        for name in eligible:
            if total() <= budget_total:
                break
            quota, _prio = schema[name]
            floor = max(1, int(quota * HARD_FLOOR_FACTOR))
            cur = category_size(out.get(name))
            if cur <= floor:
                continue
            # Compute headroom: how much we'd like to shave from this one.
            overage = total() - budget_total
            target = max(floor, cur - overage)
            target = min(target, quota)  # never push *up*
            new_val, info = truncate_category(name, out[name], target)
            out[name] = new_val
            prev = perf.get(name, {"before": cur})
            perf[name] = {
                "before": prev.get("before", cur),
                "after": category_size(new_val),
                **info,
                "priorityLevel": level,
            }

    # Pass 5: load-bearing categories at floor (priority 5) — only if still over.
    if total() > budget_total:
        eligible = [n for n, (_q, p) in schema.items() if p == 5 and n in out]
        eligible.sort(key=lambda n: -category_size(out.get(n)))
        for name in eligible:
            if total() <= budget_total:
                break
            quota, _prio = schema[name]
            floor = max(1, int(quota * HARD_FLOOR_FACTOR))
            cur = category_size(out.get(name))
            if cur <= floor:
                continue
            new_val, info = truncate_category(name, out[name], floor)
            out[name] = new_val
            prev = perf.get(name, {"before": cur})
            perf[name] = {
                "before": prev.get("before", cur),
                "after": category_size(new_val),
                **info,
                "priorityLevel": 5,
                "atFloor": True,
            }
            warnings.append(
                f"category '{name}' shrunk to hard floor ({floor} chars); "
                f"load-bearing content may be partially lost"
            )

    if total() > budget_total:
        warnings.append(
            f"hard-overflow: total={total()} > budget={budget_total} after "
            "all priority passes; caller must reduce inputs (consolidate / "
            "state_project) or raise --budget-total"
        )
        status = "hard-overflow"
    else:
        status = "adjusted" if perf else "ok"

    return _build_report(out, sizes_before, total_before, schema,
                         budget_total, status, perf, warnings)


def _build_report(
    out: dict[str, Any],
    sizes_before: dict[str, int],
    total_before: int,
    schema: dict[str, tuple[int, int]],
    budget_total: int,
    status: str,
    perf: dict[str, dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    sizes_after = {
        name: category_size(out.get(name)) for name in schema if name in out
    }
    total_after = sum(sizes_after.values())

    per_category: list[dict[str, Any]] = []
    # Stable, schema-declared order.
    for name in schema:
        if name not in out:
            continue
        before = sizes_before.get(name, 0)
        after = sizes_after.get(name, 0)
        if name in perf:
            entry = {"name": name, **perf[name]}
            entry.setdefault("before", before)
            entry.setdefault("after", after)
        else:
            entry = {"name": name, "before": before, "after": after,
                     "action": "noop"}
        per_category.append(entry)

    return {
        "ok": True,
        "budgetStatus": status,
        "totalCharsBefore": total_before,
        "totalCharsAfter": total_after,
        "budgetTotal": budget_total,
        "perCategory": per_category,
        "warnings": warnings,
        "budgetedContext": out,
    }


# ───────────────── CLI ─────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Enforce per-category character budgets on a context_pkg.json.",
    )
    p.add_argument("--input", required=True, type=Path,
                   help="path to context_pkg.json (Composer pre-Writer assembly)")
    p.add_argument("--profile", default="default",
                   choices=sorted(PROFILES.keys()),
                   help="budget profile (default tuned for 80k char total)")
    p.add_argument("--budget-total", type=int, default=DEFAULT_BUDGET_TOTAL,
                   help=f"total char budget (default {DEFAULT_BUDGET_TOTAL})")
    p.add_argument("--out", type=Path, default=None,
                   help="optional path to write the budgeted context_pkg JSON")
    p.add_argument("--json", action="store_true",
                   help="emit pure JSON to stdout (default if --out unset)")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if not args.input.is_file():
        print(json.dumps(
            {"ok": False, "error": f"input not found: {args.input}"},
            ensure_ascii=False,
        ), file=sys.stderr)
        return 1

    try:
        pkg = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps(
            {"ok": False, "error": f"failed to read input: {e!r}"},
            ensure_ascii=False,
        ), file=sys.stderr)
        return 1

    if not isinstance(pkg, dict):
        print(json.dumps(
            {"ok": False, "error": "input must be a JSON object"},
            ensure_ascii=False,
        ), file=sys.stderr)
        return 1

    if args.budget_total < 1:
        print(json.dumps(
            {"ok": False, "error": "--budget-total must be >= 1"},
            ensure_ascii=False,
        ), file=sys.stderr)
        return 1

    schema = PROFILES[args.profile]
    report = enforce_budget(pkg, schema, args.budget_total)
    report["profile"] = args.profile

    out_text = json.dumps(report, ensure_ascii=False, indent=2)

    if args.out is not None:
        try:
            tmp = args.out.with_suffix(args.out.suffix + ".tmp")
            tmp.write_text(
                json.dumps(report["budgetedContext"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(args.out)
        except OSError as e:
            print(json.dumps(
                {"ok": False, "error": f"failed to write --out: {e!r}"},
                ensure_ascii=False,
            ), file=sys.stderr)
            return 1

    if args.json or args.out is None:
        sys.stdout.write(out_text)
        if not out_text.endswith("\n"):
            sys.stdout.write("\n")
    else:
        # Brief human summary
        print(f"profile={args.profile} status={report['budgetStatus']}")
        print(f"chars: {report['totalCharsBefore']} -> {report['totalCharsAfter']} "
              f"(budget={report['budgetTotal']})")
        for entry in report["perCategory"]:
            if entry.get("action") not in {None, "noop"}:
                print(f"  {entry['name']}: {entry['before']} -> {entry['after']} "
                      f"[{entry['action']}]")
        if report["warnings"]:
            print("warnings:")
            for w in report["warnings"]:
                print(f"  - {w}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
