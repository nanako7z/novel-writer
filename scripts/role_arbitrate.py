#!/usr/bin/env python3
"""Role Arbiter — sister of `hook_arbitrate.py` for `newRoleCandidates`.

Stands between Settler's `newRoleCandidates` and `doc_ops.apply()`. For each
candidate, decide one of three verdicts:

    created     candidate is admitted as a brand-new role; emit a docOps
                `create_role` op into the resolved delta so doc_ops will
                materialize `story/roles/<tier>/<slug>.md` from the template.
    mapped      candidate name fuzzy-matches an existing role file (alias /
                nickname / character relabel); drop the candidate, no new
                file. Suggested follow-up: `patch_role_section` on the
                existing file (the arbiter only flags; doesn't auto-patch).
    rejected   admission failed: name missing / justification too thin /
                roster full (advisory cap, default 30 roles).

Why this layer exists: lets Settler observe new characters in chapter prose
without prematurely opening a file for every walk-on cameo. Settler proposes;
arbiter decides. Mirrors the pattern that worked for hooks.

CLI:
    python role_arbitrate.py --book BK --delta delta.json [--max-roster 30] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ─────────────────────────── constants ───────────────────────────

# Names too short or pure-stop-word are unlikely to be real characters.
_STOP_NAMES = {"他", "她", "它", "你", "我", "某", "一人", "他们", "她们", "众人", "this", "that"}

# Chinese name token extractor: 2-4 char run.
_CHINESE_NAME_RE = re.compile(r"[一-鿿]{2,4}")
_CHINESE_RUN_RE = re.compile(r"[一-鿿]+")

# Bigram extractor for fuzzy matching.
_BIGRAM_RE = re.compile(r"[一-鿿]{2}")

DEFAULT_MAX_ROSTER = 30
MIN_JUSTIFICATION_LEN = 6  # chars; below this we treat as walk-on


# ───────────────── name normalization + similarity ─────────────────


def _normalize_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    s = value.strip()
    # collapse whitespace, lowercase ASCII, keep Chinese as-is
    s = re.sub(r"\s+", "", s)
    s = s.lower()
    # strip honorifics and modifiers that often vary between mentions
    for suffix in ("先生", "小姐", "大叔", "大婶", "师姐", "师兄", "师弟", "师妹",
                   "公子", "姑娘", "前辈", "长老", "宗主", "门主", "陛下", "殿下"):
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)]
    for prefix in ("老", "小", "阿"):
        if s.startswith(prefix) and len(s) > len(prefix) + 1:
            s = s[len(prefix):]
    return s


def _bigrams(value: str) -> set[str]:
    out: set[str] = set()
    for run in _CHINESE_RUN_RE.findall(value):
        if len(run) >= 2:
            for i in range(len(run) - 1):
                out.add(run[i:i + 2])
    return out


def _similarity(a: str, b: str) -> float:
    """Jaccard on bigrams + whole-string equality bonus.

    Returns 1.0 for exact match, 0.0 for nothing in common.
    Used to detect "李大叔" ≈ "李大爷" or "二师姐" ≈ "二师姐姐".
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.85
    ba = _bigrams(a)
    bb = _bigrams(b)
    if not ba and not bb:
        # ASCII fallback — trigram-ish on lowercase
        sa = {a[i:i+3] for i in range(max(0, len(a) - 2))}
        sb = {b[i:i+3] for i in range(max(0, len(b) - 2))}
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / max(1, len(sa | sb))
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / max(1, len(ba | bb))


# Threshold tuned for Chinese names: 0.5 catches "二师姐"/"二师姐姐" but not
# unrelated 2-char names. Conservative — we'd rather reject (Settler can
# re-propose next chapter) than collapse two real characters into one.
_SIMILARITY_THRESHOLD = 0.5


# ─────────────────────── existing roster discovery ───────────────────


def _scan_roles_dir(book: Path) -> list[dict]:
    """Return [{slug, tier, path}] for every role file under story/roles/**."""
    root = book / "story" / "roles"
    if not root.is_dir():
        return []
    out: list[dict] = []
    for p in root.rglob("*.md"):
        if not p.is_file() or p.name.startswith("_"):
            continue
        try:
            tier = p.parent.name if p.parent != root else ""
        except ValueError:
            tier = ""
        out.append({
            "slug": p.stem,
            "tier": tier,
            "path": str(p.relative_to(book)),
            "normalized": _normalize_name(p.stem),
        })
    return out


# ─────────────────────────── arbitration ───────────────────────────


def _evaluate(candidate: dict, roster: list[dict], max_roster: int) -> dict:
    """Decide one of: admit / map_to_<slug> / reject_walkon / reject_full / reject_thin."""
    name = candidate.get("name")
    norm = _normalize_name(name)
    if not norm or norm in _STOP_NAMES:
        return {"action": "reject", "reason": "missing_or_stop_name"}

    # justification thinness — walk-ons get rejected
    just = candidate.get("justification") or ""
    if not isinstance(just, str) or len(just.strip()) < MIN_JUSTIFICATION_LEN:
        return {"action": "reject", "reason": "thin_justification"}

    # fuzzy match against existing roster
    for entry in roster:
        sim = _similarity(norm, entry["normalized"])
        if sim >= _SIMILARITY_THRESHOLD:
            return {
                "action": "map",
                "matchedSlug": entry["slug"],
                "matchedPath": entry["path"],
                "similarity": round(sim, 3),
                "reason": "fuzzy_match_existing",
            }

    if max_roster is not None and max_roster >= 0 and len(roster) >= max_roster:
        return {"action": "reject", "reason": "roster_full"}

    return {"action": "admit", "reason": "admit"}


def _make_create_op(candidate: dict, chapter: int) -> dict:
    name = (candidate.get("name") or "").strip()
    tier = candidate.get("tier")
    return {
        "op": "create_role",
        "slug": name,                  # filename stem = display name
        "displayName": name,
        "tier": tier if tier in ("主要角色", "次要角色") else "次要角色",
        "reason": (candidate.get("justification") or f"ch{chapter} 首次有名出场")[:200],
        "sourcePhase": "settler",
        "sourceChapter": chapter,
    }


def arbitrate(book: Path, delta: dict, max_roster: int = DEFAULT_MAX_ROSTER) -> dict:
    """Run role arbitration; return {resolvedDelta, decisions}.

    Mutates a copy of the delta:
      - drains delta["newRoleCandidates"] (becomes [])
      - appends `create_role` ops for each `created` decision into
        delta["docOps"]["roles"] (creates the array if absent).

    Existing docOps.roles entries are preserved — arbiter only adds.
    """
    delta = dict(delta or {})
    chapter = int(delta.get("chapter") or 0)
    candidates = list(delta.get("newRoleCandidates") or [])
    roster = _scan_roles_dir(book)

    decisions: list[dict] = []
    create_ops: list[dict] = []

    # Track names admitted in this batch to avoid two candidates collapsing into
    # the same new file (Settler may double-count).
    admitted_norm: set[str] = set()

    for cand in candidates:
        if not isinstance(cand, dict):
            decisions.append({"action": "rejected", "reason": "non_object",
                              "candidate": cand})
            continue
        verdict = _evaluate(cand, roster, max_roster)
        if verdict["action"] == "admit":
            norm = _normalize_name(cand.get("name"))
            if norm in admitted_norm:
                decisions.append({
                    "action": "rejected", "reason": "duplicate_in_batch",
                    "candidate": cand,
                })
                continue
            admitted_norm.add(norm)
            create_op = _make_create_op(cand, chapter)
            create_ops.append(create_op)
            # add to roster so subsequent candidates in same batch can map to it
            roster.append({
                "slug": create_op["slug"], "tier": create_op["tier"],
                "path": f"story/roles/{create_op['tier']}/{create_op['slug']}.md",
                "normalized": norm,
            })
            decisions.append({
                "action": "created",
                "slug": create_op["slug"],
                "tier": create_op["tier"],
                "reason": "admit",
                "candidate": cand,
            })
        elif verdict["action"] == "map":
            decisions.append({
                "action": "mapped",
                "matchedSlug": verdict["matchedSlug"],
                "matchedPath": verdict["matchedPath"],
                "similarity": verdict["similarity"],
                "reason": verdict["reason"],
                "candidate": cand,
            })
        else:
            decisions.append({
                "action": "rejected",
                "reason": verdict["reason"],
                "candidate": cand,
            })

    # Stitch resolved delta
    resolved = dict(delta)
    if create_ops:
        doc_ops = dict(resolved.get("docOps") or {})
        existing_roles = list(doc_ops.get("roles") or [])
        doc_ops["roles"] = existing_roles + create_ops
        resolved["docOps"] = doc_ops
    resolved["newRoleCandidates"] = []

    return {"resolvedDelta": resolved, "decisions": decisions}


# ───────────────────────────── CLI ──────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Arbitrate Settler newRoleCandidates against existing "
                    "story/roles/ files (sister of hook_arbitrate.py).",
    )
    p.add_argument("--book", required=True, help="book directory path")
    p.add_argument("--delta", required=True, help="path to delta.json")
    p.add_argument("--max-roster", type=int, default=DEFAULT_MAX_ROSTER,
                   help=f"advisory cap on total roles (default: {DEFAULT_MAX_ROSTER}; -1 disables)")
    p.add_argument("--json", dest="as_json", action="store_true",
                   help="print result as JSON (default behavior)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    book = Path(args.book).resolve()
    if not book.is_dir():
        print(json.dumps({"ok": False, "error": f"book dir not found: {book}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    delta_path = Path(args.delta)
    try:
        delta = json.loads(delta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(json.dumps({"ok": False, "error": f"failed to read delta: {e!r}"},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    result = arbitrate(book, delta, max_roster=args.max_roster)
    decisions = result["decisions"]
    counts = {"created": 0, "mapped": 0, "rejected": 0}
    for d in decisions:
        counts[d["action"]] = counts.get(d["action"], 0) + 1

    out = {
        "ok": True,
        "decisions": decisions,
        "resolvedDelta": result["resolvedDelta"],
        "summary": (
            f"n_created={counts['created']} n_mapped={counts['mapped']} "
            f"n_rejected={counts['rejected']}"
        ),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
