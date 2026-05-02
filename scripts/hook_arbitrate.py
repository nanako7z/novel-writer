#!/usr/bin/env python3
"""Hook Arbiter — Python port of `.inkos-src/utils/hook-arbiter.ts`.

Stands between Settler's raw delta and `apply_delta.py`'s last-write-wins
upsert. For every `newHookCandidate` (and any `hookOps.upsert` whose hookId
is not yet in `hooks.json`), decide one of four verdicts:

    created    candidate is admitted as a brand-new hook with a freshly
               minted canonical hookId.
    mapped     candidate looks semantically identical to an existing hook
               family but adds novel detail — merge candidate into that
               existing hookId via mergeCandidateIntoExistingHook().
    mentioned  candidate restates an existing hook with no novelty — drop
               the upsert and just touch lastAdvancedChapter via
               hookOps.mention.
    rejected   admission failed (missing type / missing payoff signal /
               duplicate_family without match).

Calls into the same admission rules as `evaluate_hook_admission()` (ported
inline below) and the same payoff-timing inference as
`hook-lifecycle.ts#resolveHookPayoffTiming`.

CLI:
    python hook_arbitrate.py --hooks hooks.json --delta delta.json [--max-active 12] [--json]

Reads hooks.json (the existing ledger {"hooks": [...]}) and delta.json (a
RuntimeStateDelta — same shape produced by Settler).  Outputs:

    {
      "ok": true,
      "decisions": [{"action": "...", "reason": "...", "hookId": "...", "candidate": {...}}],
      "resolvedDelta": { ... patched delta ... },
      "summary": "n_created=2 n_mapped=1 n_mentioned=0 n_rejected=1"
    }

stdlib only — no external deps; safe to call from `apply_delta.py` in a
subprocess or to import directly.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ─────────────────────────── constants ───────────────────────────

VALID_PAYOFF_TIMING = ("immediate", "near-term", "mid-arc", "slow-burn", "endgame")

# `STOP_WORDS` mirrors the union of the two stop-word sets in inkos
# (`hook-arbiter.ts` adds chapter/about/already/question on top of the
# core list shared with `hook-governance.ts`).
STOP_WORDS = {
    "that", "this", "with", "from", "into", "still", "just", "have", "will",
    "reveal", "about", "already", "question", "chapter",
}

# payoffTiming inference patterns — port of hook-lifecycle.ts SIGNAL_PATTERNS.
SIGNAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("endgame", re.compile(r"(终局|终章|大结局|最终揭晓|最终摊牌|climax|finale|endgame|final reveal|last act)", re.I)),
    ("immediate", re.compile(r"(当章|本章|下一章|马上|立刻|即刻|immediate|next chapter|right away|at once)", re.I)),
    ("near-term", re.compile(r"(近期|近几章|很快|短线|soon|near-term|short run|current sequence)", re.I)),
    ("mid-arc", re.compile(r"(中期|卷中|本卷中段|mid-book|mid arc|middle of the arc)", re.I)),
    ("slow-burn", re.compile(r"(长线|慢烧|后续发酵|慢慢揭开|later|slow burn|long arc|long tail)", re.I)),
]

TIMING_ALIASES: list[tuple[str, re.Pattern]] = [
    ("immediate", re.compile(r"^(?:立即|马上|当章|本章|下一章|immediate|instant|next(?:\s+chapter|\s+beat)?|right\s+away)$", re.I)),
    ("near-term", re.compile(r"^(?:近期|近几章|短线|soon|short(?:\s+run)?|near(?:\s*-\s*|\s+)term|current\s+sequence)$", re.I)),
    ("mid-arc", re.compile(r"^(?:中程|中期|卷中|mid(?:\s*-\s*|\s+)arc|mid(?:\s*-\s*|\s+)book|middle)$", re.I)),
    ("slow-burn", re.compile(r"^(?:慢烧|长线|后续|later|late(?:r)?|long(?:\s*-\s*|\s+)arc|slow(?:\s*-\s*|\s+)burn)$", re.I)),
    ("endgame", re.compile(r"^(?:终局|终章|大结局|最终|climax|finale|endgame|late\s+book)$", re.I)),
]


# ─────────────────────── text normalize helpers ───────────────────────

_NORMALIZE_RE = re.compile(r"[^a-z0-9一-鿿]+")
_WHITESPACE_RE = re.compile(r"\s+")
_CHINESE_RUN_RE = re.compile(r"[一-鿿]+")
_CHINESE_TERM_RE = re.compile(r"[一-鿿]{2,6}")


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    s = value.strip().lower()
    s = _NORMALIZE_RE.sub(" ", s)
    s = _WHITESPACE_RE.sub(" ", s)
    return s.strip()


def _extract_terms(value: str) -> set[str]:
    english = {
        term.strip() for term in value.split(" ")
        if len(term.strip()) >= 4 and term.strip() not in STOP_WORDS
    }
    chinese = set(_CHINESE_TERM_RE.findall(value))
    return english | chinese


def _extract_chinese_bigrams(value: str) -> set[str]:
    bigrams: set[str] = set()
    for segment in _CHINESE_RUN_RE.findall(value):
        if len(segment) < 2:
            continue
        for i in range(len(segment) - 1):
            bigrams.add(segment[i:i + 2])
    return bigrams


def _prefer_richer(primary: str, fallback: str) -> str:
    left = (primary or "").strip()
    right = (fallback or "").strip()
    if not left:
        return right
    if not right:
        return left
    if left == right:
        return left
    return right if len(right) > len(left) else left


# ────────────────────── payoffTiming inference ───────────────────────


def normalize_payoff_timing(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    norm = value.strip()
    if not norm:
        return None
    for timing, pattern in TIMING_ALIASES:
        if pattern.match(norm):
            return timing
    if norm in VALID_PAYOFF_TIMING:
        return norm
    return None


def infer_payoff_timing(expected_payoff: str | None, notes: str | None) -> str:
    combined = " ".join(
        s.strip() for s in (expected_payoff, notes) if isinstance(s, str) and s.strip()
    ).strip()
    if not combined:
        return "mid-arc"
    for timing, pattern in SIGNAL_PATTERNS:
        if pattern.search(combined):
            return timing
    return "mid-arc"


def resolve_payoff_timing(payoff_timing: Any, expected_payoff: Any, notes: Any) -> str:
    norm = normalize_payoff_timing(payoff_timing)
    if norm:
        return norm
    return infer_payoff_timing(
        expected_payoff if isinstance(expected_payoff, str) else None,
        notes if isinstance(notes, str) else None,
    )


# ─────────────────────── admission evaluation ────────────────────────


def evaluate_hook_admission(candidate: dict, active_hooks: list[dict],
                             max_active: int = 12) -> dict:
    """Port of `hook-governance.ts#evaluateHookAdmission`.

    Returns: {"admit": bool, "reason": str, "matchedHookId"?: str}
    Reasons: "admit" / "missing_type" / "missing_payoff_signal" /
             "duplicate_family" / "ledger_full"
    """
    candidate_type = _normalize_text(candidate.get("type"))
    if not candidate_type:
        return {"admit": False, "reason": "missing_type"}

    payoff_signal = " ".join(
        s.strip() for s in (candidate.get("expectedPayoff"), candidate.get("notes"))
        if isinstance(s, str) and s.strip()
    ).strip()
    if not payoff_signal:
        return {"admit": False, "reason": "missing_payoff_signal"}

    candidate_norm = _normalize_text(" ".join(str(candidate.get(k) or "")
        for k in ("type", "expectedPayoff", "payoffTiming", "notes")))
    candidate_terms = _extract_terms(candidate_norm)
    candidate_bigrams = _extract_chinese_bigrams(candidate_norm)

    for hook in active_hooks:
        active_norm = _normalize_text(" ".join(str(hook.get(k) or "")
            for k in ("type", "expectedPayoff", "payoffTiming", "notes")))

        if candidate_norm == active_norm:
            return {
                "admit": False, "reason": "duplicate_family",
                "matchedHookId": hook.get("hookId"),
            }

        if candidate_type != _normalize_text(hook.get("type")):
            continue

        active_terms = _extract_terms(active_norm)
        active_bigrams = _extract_chinese_bigrams(active_norm)
        overlap = candidate_terms & active_terms
        chinese_overlap = candidate_bigrams & active_bigrams
        if len(overlap) >= 2 or len(chinese_overlap) >= 3:
            return {
                "admit": False, "reason": "duplicate_family",
                "matchedHookId": hook.get("hookId"),
            }

    # Budget gate (inkos doesn't enforce inside evaluateHookAdmission, but
    # we honour `--max-active` here so callers can cap the ledger).
    if max_active is not None and max_active >= 0:
        live = sum(1 for h in active_hooks
                   if (h.get("status") or "").lower() not in ("resolved",))
        if live >= max_active:
            return {"admit": False, "reason": "ledger_full"}

    return {"admit": True, "reason": "admit"}


# ───────────────────────── arbitration core ──────────────────────────


def _is_pure_restatement(candidate: dict, existing: dict) -> bool:
    candidate_text = _normalize_text(" ".join(str(candidate.get(k) or "")
        for k in ("type", "expectedPayoff", "notes")))
    existing_text = _normalize_text(" ".join(str(existing.get(k) or "")
        for k in ("type", "expectedPayoff", "notes")))
    if not candidate_text:
        return True
    if candidate_text == existing_text:
        return True
    cand_terms = _extract_terms(candidate_text)
    ex_terms = _extract_terms(existing_text)
    novel_terms = cand_terms - ex_terms
    cand_bigrams = _extract_chinese_bigrams(candidate_text)
    ex_bigrams = _extract_chinese_bigrams(existing_text)
    novel_bigrams = cand_bigrams - ex_bigrams
    return len(novel_terms) == 0 and len(novel_bigrams) < 2


def _slugify_hook_stem(value: str) -> str:
    norm = _normalize_text(value)
    english = [t for t in re.findall(r"[a-z0-9]{3,}", norm) if t not in STOP_WORDS][:5]
    chinese = _CHINESE_TERM_RE.findall(norm)[:3]
    stem = "-".join(english + chinese)[:64].rstrip("-")
    return stem or "hook"


def _build_canonical_hook_id(candidate: dict, existing_ids: set[str]) -> str:
    preferred = (candidate.get("preferredHookId") or "").strip() if isinstance(
        candidate.get("preferredHookId"), str) else ""
    if preferred and preferred not in existing_ids:
        return preferred
    base = _slugify_hook_stem(" ".join(str(candidate.get(k) or "")
        for k in ("type", "expectedPayoff", "notes")))
    nxt = base
    suffix = 2
    while nxt in existing_ids:
        nxt = f"{base}-{suffix}"
        suffix += 1
    return nxt


def _create_canonical_hook(candidate: dict, chapter: int,
                            existing_ids: set[str]) -> dict:
    return {
        "hookId": _build_canonical_hook_id(candidate, existing_ids),
        "startChapter": chapter,
        "type": (candidate.get("type") or "").strip(),
        "status": "open",
        "lastAdvancedChapter": chapter,
        "expectedPayoff": (candidate.get("expectedPayoff") or "").strip(),
        "payoffTiming": resolve_payoff_timing(
            candidate.get("payoffTiming"),
            candidate.get("expectedPayoff"),
            candidate.get("notes"),
        ),
        "notes": (candidate.get("notes") or "").strip(),
    }


def _merge_candidate_into_existing(existing: dict, candidate: dict,
                                   chapter: int) -> dict:
    merged = dict(existing)
    merged["type"] = _prefer_richer(existing.get("type", ""), candidate.get("type", ""))
    merged["status"] = "resolved" if (existing.get("status") == "resolved") else "progressing"
    merged["lastAdvancedChapter"] = max(
        int(existing.get("lastAdvancedChapter") or 0), int(chapter or 0))
    merged["expectedPayoff"] = _prefer_richer(
        existing.get("expectedPayoff", ""), candidate.get("expectedPayoff", ""))
    merged["notes"] = _prefer_richer(
        existing.get("notes", ""), candidate.get("notes", ""))
    merged["payoffTiming"] = resolve_payoff_timing(
        candidate.get("payoffTiming") or existing.get("payoffTiming"),
        merged["expectedPayoff"],
        merged["notes"],
    )
    return merged


def _replace_or_append(working: list[dict], hook: dict) -> None:
    for i, h in enumerate(working):
        if h.get("hookId") == hook.get("hookId"):
            working[i] = hook
            return
    working.append(hook)


def _unique_strings(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    seen: list[str] = []
    seen_set: set[str] = set()
    for v in values:
        if not isinstance(v, str):
            continue
        s = v.strip()
        if s and s not in seen_set:
            seen.append(s)
            seen_set.add(s)
    return seen


def _sort_hooks(hooks: list[dict]) -> list[dict]:
    return sorted(hooks, key=lambda h: (
        int(h.get("startChapter") or 0),
        int(h.get("lastAdvancedChapter") or 0),
        h.get("hookId") or "",
    ))


def arbitrate(hooks: list[dict], delta: dict, max_active: int = 12) -> dict:
    """Run arbitration and return {resolvedDelta, decisions}."""
    delta = dict(delta or {})
    chapter = int(delta.get("chapter") or 0)
    hook_ops = dict(delta.get("hookOps") or {})
    upsert = list(hook_ops.get("upsert") or [])
    mention = set(hook_ops.get("mention") or [])
    resolves = _unique_strings(hook_ops.get("resolve"))
    defers = _unique_strings(hook_ops.get("defer"))
    new_candidates = list(delta.get("newHookCandidates") or [])

    working: list[dict] = [dict(h) for h in (hooks or []) if isinstance(h, dict)]
    known_ids: set[str] = {h.get("hookId") for h in working if h.get("hookId")}
    upserts_by_id: dict[str, dict] = {}
    fallback_candidates: list[dict] = []
    decisions: list[dict] = []

    # Pass 1: known-id upserts go through; unknown-id upserts become candidates.
    for hook in upsert:
        if not isinstance(hook, dict):
            continue
        hid = hook.get("hookId")
        if hid and hid in known_ids:
            normalized = dict(hook)
            upserts_by_id[hid] = normalized
            _replace_or_append(working, normalized)
            continue
        # Unknown hookId — treat as a candidate with preferredHookId hint.
        fallback_candidates.append({
            "type": hook.get("type", ""),
            "expectedPayoff": hook.get("expectedPayoff", ""),
            "payoffTiming": hook.get("payoffTiming"),
            "notes": hook.get("notes", ""),
            "preferredHookId": hid,
        })

    # Pass 2: arbitrate fallback candidates + newHookCandidates.
    for candidate in [*fallback_candidates, *new_candidates]:
        if not isinstance(candidate, dict):
            continue
        active_hooks = [h for h in working if (h.get("status") or "").lower() != "resolved"]
        admission = evaluate_hook_admission(candidate, active_hooks, max_active=max_active)

        if not admission["admit"]:
            if admission["reason"] == "duplicate_family" and admission.get("matchedHookId"):
                matched = next((h for h in working
                                if h.get("hookId") == admission["matchedHookId"]), None)
                if matched is None:
                    decisions.append({
                        "action": "rejected",
                        "reason": "duplicate_family_without_match",
                        "candidate": candidate,
                    })
                    continue

                if _is_pure_restatement(candidate, matched):
                    mid = matched["hookId"]
                    if (mid not in upserts_by_id
                            and mid not in resolves
                            and mid not in defers):
                        mention.add(mid)
                    decisions.append({
                        "action": "mentioned",
                        "reason": "restated_existing_family",
                        "hookId": mid,
                        "candidate": candidate,
                    })
                    continue

                base = upserts_by_id.get(matched["hookId"], matched)
                mapped = _merge_candidate_into_existing(base, candidate, chapter)
                upserts_by_id[mapped["hookId"]] = mapped
                mention.discard(mapped["hookId"])
                _replace_or_append(working, mapped)
                decisions.append({
                    "action": "mapped",
                    "reason": "duplicate_family_with_novelty",
                    "hookId": mapped["hookId"],
                    "candidate": candidate,
                })
                continue

            decisions.append({
                "action": "rejected",
                "reason": admission["reason"],
                "candidate": candidate,
            })
            continue

        # admit → create canonical hook
        existing_ids = {h.get("hookId") for h in working if h.get("hookId")}
        existing_ids |= set(upserts_by_id.keys())
        created = _create_canonical_hook(candidate, chapter, existing_ids)
        upserts_by_id[created["hookId"]] = created
        working.append(created)
        decisions.append({
            "action": "created",
            "reason": "admit",
            "hookId": created["hookId"],
            "candidate": candidate,
        })

    # Stitch resolvedDelta.
    final_mention = sorted({mid for mid in mention
                             if mid not in upserts_by_id
                             and mid not in resolves
                             and mid not in defers})
    resolved_delta = dict(delta)
    resolved_delta["hookOps"] = {
        "upsert": _sort_hooks(list(upserts_by_id.values())),
        "mention": final_mention,
        "resolve": resolves,
        "defer": defers,
    }
    resolved_delta["newHookCandidates"] = []

    return {"resolvedDelta": resolved_delta, "decisions": decisions}


# ───────────────────────────── CLI ──────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Arbitrate Settler hookOps + newHookCandidates against an "
                    "existing hooks ledger (port of inkos hook-arbiter.ts)."
    )
    p.add_argument("--hooks", required=True,
                   help="path to hooks.json ({\"hooks\":[...]})")
    p.add_argument("--delta", required=True,
                   help="path to delta.json (RuntimeStateDelta)")
    p.add_argument("--max-active", type=int, default=12,
                   help="hard cap on live hooks (default: 12; -1 disables)")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="print result as JSON (default behavior)")
    return p.parse_args()


def _load_hooks(path: Path) -> list[dict]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        hooks = raw.get("hooks") or []
    elif isinstance(raw, list):
        hooks = raw
    else:
        hooks = []
    return [h for h in hooks if isinstance(h, dict)]


def main() -> int:
    args = parse_args()
    hooks_path = Path(args.hooks)
    delta_path = Path(args.delta)
    if not delta_path.exists():
        print(json.dumps({"ok": False, "error": f"delta not found: {delta_path}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    try:
        hooks = _load_hooks(hooks_path)
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": f"failed to read hooks: {e!r}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    try:
        delta = json.loads(delta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": f"failed to read delta: {e!r}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    result = arbitrate(hooks, delta, max_active=args.max_active)
    decisions = result["decisions"]
    counts = {"created": 0, "mapped": 0, "mentioned": 0, "rejected": 0}
    for d in decisions:
        counts[d["action"]] = counts.get(d["action"], 0) + 1

    out = {
        "ok": True,
        "decisions": decisions,
        "resolvedDelta": result["resolvedDelta"],
        "summary": (
            f"n_created={counts['created']} n_mapped={counts['mapped']} "
            f"n_mentioned={counts['mentioned']} n_rejected={counts['rejected']}"
        ),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
