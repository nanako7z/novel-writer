"""Cross-script enum constants — single source of truth.

All enums are mirrored from inkos's zod schemas (see ``.inkos-src/models/``).
When inkos bumps a schema, mirror the change here in **one place** and the
downstream scripts that re-import these constants pick it up without
modification.

Usage:

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _constants import HOOK_STATUS, PAYOFF_TIMING  # ...

Each enum is exposed as a ``frozenset`` (immutable, hashable, supports the
``in`` membership test). For argparse ``choices=`` arguments, materialize
into a sorted list: ``choices=sorted(PLATFORM)``.

Sources:

  * HOOK_STATUS, PAYOFF_TIMING, HOOK_OPS  ← models/runtime-state.ts
  * CHAPTER_STATUS                          ← models/chapter.ts
  * BOOK_STATUS, PLATFORM, FANFIC_MODE      ← models/book.ts
"""
from __future__ import annotations

# ---- hook lifecycle (models/runtime-state.ts HookStatusSchema) ------------
# z.enum(["open", "progressing", "deferred", "resolved"])
HOOK_STATUS: frozenset[str] = frozenset({
    "open", "progressing", "deferred", "resolved",
})

# ---- hook payoff timing (models/runtime-state.ts HookPayoffTimingSchema) --
# z.enum(["immediate", "near-term", "mid-arc", "slow-burn", "endgame"])
PAYOFF_TIMING: frozenset[str] = frozenset({
    "immediate", "near-term", "mid-arc", "slow-burn", "endgame",
})

# ---- hook ops (models/runtime-state.ts HookOpsSchema keys) ----------------
# subkeys of HookOpsSchema: upsert / mention / resolve / defer
HOOK_OPS: frozenset[str] = frozenset({
    "upsert", "mention", "resolve", "defer",
})

# ---- per-chapter lifecycle (models/chapter.ts ChapterStatusSchema) --------
# 14 values, from card-generated through published/imported.
CHAPTER_STATUS: frozenset[str] = frozenset({
    "card-generated",
    "drafting",
    "drafted",
    "auditing",
    "audit-passed",
    "audit-failed",
    "state-degraded",
    "revising",
    "ready-for-review",
    "approved",
    "rejected",
    "published",
    "imported",
})

# ---- per-book lifecycle (models/book.ts BookStatusSchema) -----------------
# z.enum(["incubating", "outlining", "active", "paused", "completed", "dropped"])
# NOTE: inkos uses "dropped" (not "archived"). Pre-consolidation, book.py
# accepted "archived" — that was schema drift; we now align with inkos.
BOOK_STATUS: frozenset[str] = frozenset({
    "incubating", "outlining", "active", "paused", "completed", "dropped",
})

# Initial book statuses: when chapter 1 successfully persists, these
# transition automatically to "active" (mirrors inkos markBookActiveIfNeeded).
BOOK_STATUS_INITIAL: frozenset[str] = frozenset({"incubating", "outlining"})

# ---- platform (models/book.ts PlatformSchema) -----------------------------
# z.enum(["tomato", "feilu", "qidian", "other"])
PLATFORM: frozenset[str] = frozenset({
    "tomato", "feilu", "qidian", "other",
})

# ---- fanfic mode (models/book.ts FanficModeSchema) ------------------------
# z.enum(["canon", "au", "ooc", "cp"])
FANFIC_MODE: frozenset[str] = frozenset({
    "canon", "au", "ooc", "cp",
})

# ---- commitment ledger violation codes (commit b1cc3a7 + ab39bd6) ---------
# Severity = critical for both. Mirrored into commitment_ledger.py output and
# read by apply_delta.py as part of the chapter-truth-validation gate.
LEDGER_VIOLATION_REVEAL_BURY_FLOOR = "REVEAL_BURY_FLOOR"
LEDGER_VIOLATION_HOOK_PAYOFF_UNLOCATED = "HOOK_PAYOFF_UNLOCATED"

# Minimum prose window (in CJK chars) that a hook payoff scene must occupy
# in the chapter draft to satisfy commit ab39bd6's "concretely locatable
# payoff scene" rule. Pure inner-recall is not enough.
HOOK_PAYOFF_MIN_CHARS = 60
