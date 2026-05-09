#!/usr/bin/env python3
"""Validate a RuntimeStateDelta JSON and apply it to the book's truth files.

Three-stage parser (mirrors inkos `settler-parser.ts` + `settler-delta-parser.ts`,
extended with a soft-fix layer):

  Stage 1 — lenient extract (raw mode only):
    Find the RUNTIME_STATE_DELTA JSON block within arbitrary input. Accept:
      - bare JSON file
      - JSON wrapped in `=== RUNTIME_STATE_DELTA === ... === END ===` sentinels
      - JSON inside a ```json ... ``` markdown code fence
      - JSON preceded by `=== POST_SETTLEMENT === ...` summary text
      - indented sentinels, trailing prose, missing END

  Stage 2 — soft-fix normalize:
    Auto-correct common Settler format deviations (snake_case keys, "12" → 12,
    capitalized enum values, single-record→list, key aliases). Every fix is
    logged in `softFixes` so the caller can echo it back to Settler. No retry
    is needed for soft issues.

  Stage 3 — strict validate:
    Run schema checks. On failure produce structured per-field errors AND a
    Chinese feedback block (`parserFeedback`) that Settler can ingest in its
    next attempt.

Input modes:
  --input-mode json (default): treat --delta as bare JSON (legacy behavior).
                                Stage 1 is skipped; Stage 2 + 3 still run.
  --input-mode raw           : treat --delta as Settler's raw chat output
                                (with sentinels). All three stages run.

Writes are atomic via .tmp + rename. Hook governance is invoked after writes.

Output JSON adds:
  softFixes:    list of soft-fix records (key renames, type coercions, ...)
  parseStage:   "extracted" | "softfix" | "schema" | "applied"
  parserFeedback: Chinese feedback block for Settler retry (empty on success)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Reuse the shared 3-stage pipeline from settler_parse.py — single source of truth
# for sentinels, soft-fix rules, and schema validation.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from settler_parse import (  # noqa: E402
    VALID_HOOK_OPS,
    VALID_HOOK_STATUS,
    VALID_PAYOFF_TIMING,
    lenient_extract,
    parse_settler_output,
    render_parser_feedback,
    sanitize_json,
    soft_fix,
    strip_code_fence,
    validate_delta,
)
from _schema import SCHEMA_VERSION  # noqa: E402  — single source of truth
from _constants import BOOK_STATUS_INITIAL  # noqa: E402  — single source of truth

# Hook arbiter (port of inkos hook-arbiter.ts) — reshapes hookOps + new
# candidates against the existing ledger BEFORE schema re-validation and
# governance gate. Imported in-process to avoid a subprocess hop.
from hook_arbitrate import arbitrate as arbitrate_hooks  # noqa: E402
from role_arbitrate import arbitrate as arbitrate_roles  # noqa: E402
from _summary import emit_summary  # noqa: E402
import doc_ops  # noqa: E402  — docOps applier (sister module)

HOOK_GOVERNANCE_SCRIPT = Path(__file__).resolve().parent / "hook_governance.py"
COMMITMENT_LEDGER_SCRIPT = Path(__file__).resolve().parent / "commitment_ledger.py"
BOOK_LOCK_SCRIPT = Path(__file__).resolve().parent / "book_lock.py"


# ───────────────────────── parser shim ───────────────────────────
# Stage 1+2+3 implementations live in `settler_parse` (single source of truth).
# The functions imported above (lenient_extract, soft_fix, validate_delta,
# sanitize_json, strip_code_fence, render_parser_feedback) plus the constants
# VALID_HOOK_OPS / VALID_HOOK_STATUS / VALID_PAYOFF_TIMING are re-exported here
# so external callers that previously imported from apply_delta still work.


def render_settler_feedback(errors: list[dict]) -> str:
    """Backward-compat alias — old callers expected schema-only feedback."""
    return render_parser_feedback("schema", errors)


# ───────────────────────────── apply helpers ─────────────────────────────


def err(msg: str, code: int = 1) -> None:
    print(json.dumps({"error": msg}, ensure_ascii=False), file=sys.stderr)
    sys.exit(code)


def atomic_write_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def load_json(p: Path, default):
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        err(f"corrupt json at {p}: {e}")


def write_json(p: Path, data) -> None:
    atomic_write_text(p, json.dumps(data, ensure_ascii=False, indent=2))


def append_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    existing = p.read_text(encoding="utf-8") if p.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    atomic_write_text(p, existing + text + ("\n" if not text.endswith("\n") else ""))


def ensure_manifest_schema_version(obj: dict, warnings: list[str]) -> dict:
    """Stamp / sanity-check `schemaVersion` on **manifest only** (inkos parity).

    Per inkos's StateManifestSchema, schemaVersion is a number (`z.literal(2)`).
    Older novel-writer books may have it as the string ``"1.0"`` — we
    self-heal those silently, write back the number, and log a one-time
    migration warning.

    * Missing → fill with current SCHEMA_VERSION.
    * Present as string ``"1.0"`` (legacy SKILL format) → migrate to ``2``,
      warn once.
    * Present as another string / number → keep existing untouched, warn so
      caller knows there's a version mismatch.
    """
    if not isinstance(obj, dict):
        return obj
    cur = obj.get("schemaVersion")
    if cur is None:
        obj["schemaVersion"] = SCHEMA_VERSION
    elif cur == "1.0":
        obj["schemaVersion"] = SCHEMA_VERSION
        warnings.append(
            "manifest schemaVersion migrated '1.0' → 2 (inkos parity); "
            "see references/schemas/migration-log.md"
        )
    elif cur != SCHEMA_VERSION:
        warnings.append(
            f"manifest schemaVersion mismatch: file={cur!r} "
            f"current={SCHEMA_VERSION!r} — see references/schemas/migration-log.md"
        )
    return obj


def strip_legacy_schema_version(obj: dict, file_label: str, warnings: list[str]) -> dict:
    """Remove a stray ``schemaVersion`` field from non-manifest state files.

    inkos's HooksStateSchema / CurrentStateStateSchema / ChapterSummariesStateSchema
    don't carry schemaVersion — only the manifest does. Older novel-writer
    books had it on every dict-shaped state file; on next write we strip
    it for cleanliness so inkos's strict zod accepts the file.
    """
    if isinstance(obj, dict) and "schemaVersion" in obj:
        old = obj.pop("schemaVersion")
        warnings.append(
            f"stripped legacy schemaVersion={old!r} from {file_label} "
            "(inkos schema doesn't carry it on this file)"
        )
    return obj


def merge_dict(dst: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge_dict(dst[k], v)
        else:
            dst[k] = v
    return dst


# Reserved hookId values that LLMs occasionally fabricate when mis-parsing
# the pending_hooks.md table (using header column names as data) or when
# confused by Settler's "newHookCandidates → hookOps.upsert" path. Rejecting
# them at apply time prevents stub rows like {"hookId": "hookId", ...} from
# polluting hooks.json. cf. plan A7.
RESERVED_HOOK_IDS = frozenset({
    "hookId", "hookid", "HookId", "HOOK_ID",
    "status", "type", "open", "progressing", "deferred", "resolved",
    "mentioned", "near-term", "mid-arc", "slow-burn", "endgame",
    "newHook", "candidate", "TBD", "todo", "TODO", "null", "None", "",
})


def apply_hook_ops(hooks_obj: dict, ops: dict, warnings: list[str]) -> dict:
    hooks = hooks_obj.get("hooks", [])
    # Drop pre-existing stub rows whose hookId is a reserved word — these are
    # historical pollution from before A7 was added; cleaning them on every
    # apply keeps the hooks.json pure without a separate one-shot migration.
    cleaned_hooks: list[dict] = []
    for h in hooks:
        if not isinstance(h, dict):
            continue
        hid = h.get("hookId")
        if isinstance(hid, str) and hid.strip() in RESERVED_HOOK_IDS:
            warnings.append(f"dropped stub hook row with reserved hookId={hid!r}")
            continue
        cleaned_hooks.append(h)
    by_id = {h.get("hookId"): h for h in cleaned_hooks}
    for h in ops.get("upsert", []) or []:
        hid = h.get("hookId")
        if not hid:
            warnings.append("upsert hook missing hookId; skipped")
            continue
        if isinstance(hid, str) and hid.strip() in RESERVED_HOOK_IDS:
            warnings.append(
                f"rejected upsert with reserved hookId={hid!r}; "
                "use a real hook id or move it to newHookCandidates"
            )
            continue
        if hid in by_id:
            by_id[hid].update(h)
        else:
            by_id[hid] = h
    for hid in ops.get("mention", []) or []:
        if hid in by_id:
            by_id[hid]["status"] = "mentioned"
        else:
            warnings.append(f"mention: unknown hookId {hid}")
    for hid in ops.get("resolve", []) or []:
        if hid in by_id:
            by_id[hid]["status"] = "resolved"
        else:
            warnings.append(f"resolve: unknown hookId {hid}")
    for hid in ops.get("defer", []) or []:
        if hid in by_id:
            by_id[hid]["status"] = "deferred"
        else:
            warnings.append(f"defer: unknown hookId {hid}")
    return {"hooks": list(by_id.values())}


def _bump_book_metadata(book_dir: Path, chapter_no: int | None) -> dict:
    """Mirror inkos `markBookActiveIfNeeded` + updatedAt bump.

    Bumps `book.json#updatedAt` to current ISO8601, and on first chapter
    persistence flips `status: incubating|outlining → active`. Atomic write
    via the existing helper. Failures are non-fatal — we log the reason and
    let the caller continue (apply_delta's main job has already succeeded).

    Returns a structured report:
        {"ran": true, "updatedAt": "...", "statusChanged": "incubating→active"|null}
    or
        {"ran": false, "reason": "..."}
    """
    bj = book_dir / "book.json"
    if not bj.is_file():
        return {"ran": False, "reason": f"book.json not found at {bj}"}
    try:
        book = json.loads(bj.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"ran": False, "reason": f"book.json unreadable: {e!r}"}
    if not isinstance(book, dict):
        return {"ran": False, "reason": "book.json is not a JSON object"}

    from datetime import datetime as _dt, timezone as _tz
    now_iso = _dt.now(_tz.utc).isoformat()

    status_before = book.get("status")
    status_after = status_before
    # First-chapter status flip — chapter 1 is the canonical "we started" point.
    # Honor incubating + outlining (inkos uses outlining; we default to incubating).
    if chapter_no == 1 and status_before in BOOK_STATUS_INITIAL:
        status_after = "active"

    if book.get("updatedAt") == now_iso and status_after == status_before:
        # No-op (clock didn't tick this microsecond AND no flip needed)
        return {"ran": True, "updatedAt": now_iso, "statusChanged": None}

    book["updatedAt"] = now_iso
    if status_after != status_before:
        book["status"] = status_after
    try:
        write_json(bj, book)
    except OSError as e:
        return {"ran": False, "reason": f"write failed: {e!r}"}

    return {
        "ran": True,
        "updatedAt": now_iso,
        "statusChanged": (
            f"{status_before}→{status_after}" if status_after != status_before else None
        ),
    }


def _advance_manifest_chapter(
    book_dir: Path, delta_chapter: int | None, warnings: list[str]
) -> dict:
    """Mirror inkos `state-reducer.ts:64-67` — pin
    ``manifest.lastAppliedChapter = delta.chapter`` after a successful apply.

    Refuses to go backwards (inkos throws on
    ``delta.chapter <= lastAppliedChapter``; apply_delta is more permissive
    and warns + skips instead). Re-applying the same chapter is a no-op.
    """
    if not isinstance(delta_chapter, int):
        return {"ran": False, "reason": "delta has no top-level chapter field"}
    mp = book_dir / "story" / "state" / "manifest.json"
    if not mp.is_file():
        return {"ran": False, "reason": f"manifest.json not found at {mp}"}
    try:
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"ran": False, "reason": f"manifest.json unreadable: {e!r}"}
    if not isinstance(manifest, dict):
        return {"ran": False, "reason": "manifest.json is not a JSON object"}

    before = int(manifest.get("lastAppliedChapter", 0) or 0)
    if delta_chapter < before:
        warnings.append(
            f"manifest advance skipped: delta.chapter={delta_chapter} < "
            f"lastAppliedChapter={before} (inkos refuses going backwards)"
        )
        return {"ran": False, "before": before, "reason": "delta goes backwards"}
    if delta_chapter == before:
        return {
            "ran": False,
            "before": before,
            "reason": f"already at lastAppliedChapter={before} (no-op re-apply)",
        }

    ensure_manifest_schema_version(manifest, warnings)
    manifest["lastAppliedChapter"] = delta_chapter
    try:
        write_json(mp, manifest)
    except OSError as e:
        return {"ran": False, "reason": f"write failed: {e!r}"}
    return {"ran": True, "before": before, "after": delta_chapter}


def md_row(values: list) -> str:
    return "| " + " | ".join(str(v) if v is not None else "" for v in values) + " |"


# ───────────────── render JSON state → markdown views ────────────────
# inkos's promise (see references/schemas/runtime-state-delta.md §"writeTargets"):
# every state-bearing JSON has a paired markdown "view" kept in sync. We render
# from the JSON authoritatively after each apply_delta write — the markdown
# files are pure outputs, never authoritative. Anyone hand-editing the md will
# lose their changes on the next apply_delta run; the doctored header at the
# top of each rendered file warns about this.

_VIEW_BANNER = (
    "> 自动渲染：来自 `state/{json_file}`，每次 `apply_delta` 后由脚本重写。\n"
    "> 直接编辑此文件不会持久化；要改请改 json 或通过 settler delta。\n"
)

_CS_FIELDS = [
    ("currentLocation",   "当前位置"),
    ("protagonistState",  "主角状态"),
    ("currentGoal",       "当前目标"),
    ("currentConstraint", "当前约束"),
    ("currentAlliances",  "当前盟友"),
    ("currentConflict",   "当前冲突"),
]

_HOOKS_COLS = [
    "hookId", "type", "status", "startChapter", "lastAdvancedChapter",
    "mentionCount", "expectedPayoff", "payoffTiming", "priority",
    "tags", "involvedCharacters", "notes", "createdAt",
]

_SUMMARY_COLS = [
    "chapter", "title", "characters", "events", "stateChanges",
    "hookActivity", "mood", "chapterType",
]


def _render_table(cols: list[str], rows: list[dict]) -> str:
    """Markdown table with cols as header; each row dict's values look up by col."""
    out = ["| " + " | ".join(cols) + " |",
           "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows:
        if not isinstance(r, dict):
            continue
        cells = []
        for c in cols:
            v = r.get(c, "")
            if isinstance(v, list):
                v = ",".join(str(x) for x in v)
            cells.append(str(v) if v is not None else "")
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def render_current_state_md(cs_json: dict) -> str:
    chapter = cs_json.get("chapter", 0)
    facts = cs_json.get("facts") or []
    lines = [
        "# 当前状态（Current State）",
        "",
        _VIEW_BANNER.format(json_file="current_state.json"),
        "- 当前章次：" + str(chapter),
    ]
    for k, label in _CS_FIELDS:
        v = cs_json.get(k, "")
        lines.append(f"- {label}：{v if v else ''}")
    lines += ["", "## 已建立的事实", ""]
    if facts:
        for f in facts:
            if isinstance(f, dict):
                ch = f.get("chapter", "")
                txt = f.get("fact") or f.get("text") or ""
                lines.append(f"- (ch{ch}) {txt}" if ch != "" else f"- {txt}")
            else:
                lines.append(f"- {f}")
    else:
        lines.append("（暂无）")
    return "\n".join(lines) + "\n"


def render_pending_hooks_md(hooks_json: dict) -> str:
    rows = hooks_json.get("hooks") or []
    head = [
        "# Pending Hooks（钩子追踪）",
        "",
        _VIEW_BANNER.format(json_file="hooks.json"),
        "",
    ]
    return "\n".join(head) + _render_table(_HOOKS_COLS, rows)


def render_chapter_summaries_md(cs_json: dict) -> str:
    rows = cs_json.get("rows") or cs_json.get("summaries") or []
    head = [
        "# 章节摘要（Chapter Summaries）",
        "",
        _VIEW_BANNER.format(json_file="chapter_summaries.json"),
        "",
    ]
    return "\n".join(head) + _render_table(_SUMMARY_COLS, rows)


def _render_views_for(book: Path, modified: list[str], warnings: list[str]) -> None:
    """After json writes, re-render the three paired md views from json."""
    state_dir = book / "story" / "state"
    pairs = [
        (state_dir / "current_state.json",      book / "story" / "current_state.md",      render_current_state_md,    {"chapter": 0, "facts": []}),
        (state_dir / "hooks.json",              book / "story" / "pending_hooks.md",      render_pending_hooks_md,    {"hooks": []}),
        (state_dir / "chapter_summaries.json",  book / "story" / "chapter_summaries.md",  render_chapter_summaries_md,{"rows": []}),
    ]
    for js, md, render, default in pairs:
        try:
            data = load_json(js, default)
            if not isinstance(data, dict):
                data = default
            atomic_write_text(md, render(data))
            modified.append(str(md))
        except Exception as e:  # noqa: BLE001 — view render must never abort apply
            warnings.append(f"view render failed for {md.name}: {e!r}")


# ───────────────────────── *Ops → docOps translator ─────────────────
# Schema-level *Ops (subplotOps / emotionalArcOps / characterMatrixOps) and
# docOps both target the same three table truth files. Historically they had
# separate ad-hoc append codepaths here, while doc_ops.py used header-aware
# upsert — meaning the two paths could disagree on row identity and produce
# duplicate rows. We now translate *Ops into equivalent docOps batch entries
# before doc_ops.apply() runs, collapsing to a single write path. Both API
# surfaces continue to be accepted by the schema; only the implementation
# unifies.
_TABLE_OPS_TRANSLATION = {
    # delta_key:        (docOps target,    key field names,        value field names → docOps `fields` keys)
    "subplotOps":        ("subplotBoard",   ("subplotId",),         ("name", "status", "lastAdvancedChapter", "characters", "notes")),
    "emotionalArcOps":   ("emotionalArcs",  ("character", "chapter"), ("emotionalState", "triggerEvent", "intensity", "arcDirection")),
    "characterMatrixOps":("characterMatrix",("charA", "charB"),     ("relationship", "intimacy", "lastInteraction", "notes")),
}


def _translate_table_ops_to_docops(delta: dict) -> int:
    """Drain delta[<*Ops>] into delta['docOps'][<target>] as upsert_row entries.

    Returns the number of *Ops items translated. Caller-supplied docOps entries
    (if any) are preserved by appending after them. *Ops keys are removed from
    the delta after translation so the legacy ad-hoc writer doesn't run.
    """
    docops = delta.setdefault("docOps", {})
    if not isinstance(docops, dict):
        delta["docOps"] = {}
        docops = delta["docOps"]
    chapter = delta.get("chapter") if isinstance(delta.get("chapter"), int) else None
    n = 0
    for src_key, (target, key_fields, value_fields) in _TABLE_OPS_TRANSLATION.items():
        ops = delta.pop(src_key, None)
        if not isinstance(ops, list) or not ops:
            continue
        bucket = docops.setdefault(target, [])
        if not isinstance(bucket, list):
            # Caller put a non-list under this target; we cannot safely merge.
            # Skip translation and leave the original *Ops in place to surface
            # the inconsistency through doc_ops.apply's normal error path.
            delta[src_key] = ops
            continue
        for op in ops:
            if not isinstance(op, dict):
                continue
            key = [op.get(k) for k in key_fields]
            fields = {k: op.get(k) for k in value_fields if k in op}
            bucket.append({
                "op": "upsert_row",
                "key": key,
                "fields": fields,
                "sourcePhase": "settler",
                "sourceChapter": chapter,
                "reason": f"settler {src_key} translation",
            })
            n += 1
    return n


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Apply RuntimeStateDelta to truth files (3-stage parser)")
    # Subcommand-style: 'revert-doc-op' is a positional verb that bypasses the
    # normal apply pipeline. Detected before argparse parsing in main().
    p.add_argument("--book", required=True, help="book directory path")
    g = p.add_mutually_exclusive_group(required=False)
    g.add_argument("--delta", help="path to delta file (raw JSON or settler-wrapped output)")
    g.add_argument("--delta-stdin", action="store_true", help="read delta payload from stdin")
    p.add_argument(
        "--op-id",
        help="(only with revert-doc-op verb) opId hash to revert from doc_changes.log",
    )
    p.add_argument(
        "--input-mode",
        choices=["json", "raw"],
        default="json",
        help="json (default): treat input as bare RuntimeStateDelta JSON (legacy). "
        "raw: treat input as Settler's raw chat output with === POST_SETTLEMENT === "
        "/ === RUNTIME_STATE_DELTA === sentinels and surrounding prose. The raw mode "
        "runs the full 3-stage parser (lenient extract → soft-fix → strict validate).",
    )
    p.add_argument(
        "--skip-hook-governance",
        action="store_true",
        help="skip the post-write hook_governance validate+stale-scan pass",
    )
    p.add_argument(
        "--skip-commitment-ledger",
        action="store_true",
        help="skip the post-write commitment_ledger pass (reveal-bury floor + "
        "hook payoff window check, ports inkos commits b1cc3a7 + ab39bd6). "
        "Use only when the memo or chapter draft is unavailable on disk.",
    )
    p.add_argument(
        "--skip-arbitration",
        action="store_true",
        help="skip the pre-write hook arbiter (newHookCandidates / unknown-id "
        "upserts will go straight to last-write-wins). Use only for legacy "
        "deltas already shaped by an external arbiter.",
    )
    p.add_argument(
        "--max-active-hooks",
        type=int,
        default=12,
        help="hard cap on live hooks honored by the arbiter (default: 12; -1 disables)",
    )
    p.add_argument(
        "--max-roster",
        type=int,
        default=30,
        help="advisory cap on total roles in story/roles/ (default: 30; -1 disables). "
        "Above the cap, role arbiter rejects new admissions.",
    )
    p.add_argument(
        "--skip-role-arbitration",
        action="store_true",
        help="skip the pre-write role arbiter (newRoleCandidates won't be "
        "materialized into create_role docOps). Use only when you've shaped "
        "delta.docOps.roles externally.",
    )
    p.add_argument(
        "--feedback-format",
        choices=["json", "settler"],
        default="json",
        help="on validation failure: 'json' (structured errors) or 'settler' "
        "(human-readable feedback block ready to inject back into Settler)",
    )
    p.add_argument(
        "--max-parse-attempts",
        type=int,
        default=1,
        help="when reading from stdin, retry parsing up to N times. Each retry "
        "reads a fresh stdin chunk delimited by an EOF or `=== ATTEMPT_END ===` "
        "line. Useful when the caller pipes corrected output back.",
    )
    p.add_argument(
        "--skip-lock",
        action="store_true",
        help="skip the book_lock acquire/release pass. The lock is advisory; "
        "use this when you've already acquired it externally or are running "
        "in a single-process pipeline that doesn't need the safety net.",
    )
    p.add_argument(
        "--skip-book-metadata",
        action="store_true",
        help="skip the post-apply book.json metadata maintenance pass "
        "(updatedAt bump + status flip incubating/outlining → active on first "
        "chapter). Use for batch / dry-run scenarios.",
    )
    return p.parse_args()


def _import_book_lock():
    """Import book_lock in-process so acquire/release share our pid."""
    try:
        import book_lock  # type: ignore
        return book_lock
    except Exception:  # noqa: BLE001
        return None


def acquire_book_lock(book: Path, operation: str) -> tuple[bool, dict]:
    """Acquire the advisory book write lock (in-process).

    Returns (ok, payload). On refusal (lock held), ok is False and payload
    contains the structured refusal report. We import ``book_lock`` so the
    pid + host stamped on the lock match this Python process — a release
    later in this same run will be recognized as ours.
    """
    bl = _import_book_lock()
    if bl is None:
        return True, {"skipped": True, "reason": "book_lock module not importable"}
    p = bl._lock_path(book)  # noqa: SLF001 — internal helper, intentional
    existing = bl._read_lock(p)  # noqa: SLF001
    if existing is not None and not bl._is_expired(existing):  # noqa: SLF001
        return False, {
            "ok": False,
            "result": "refused",
            "reason": "lock held by another owner",
            "lockPath": str(p),
            "currentLock": existing,
        }
    from datetime import datetime, timedelta, timezone
    payload = {
        "pid": os.getpid(),
        "operation": operation,
        "acquiredAt": bl._now_iso(),  # noqa: SLF001
        "expiresAt": (datetime.now(timezone.utc) + timedelta(
            seconds=bl.DEFAULT_TTL_SEC,
        )).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "host": __import__("socket").gethostname(),
    }
    bl._atomic_write_lock(p, payload)  # noqa: SLF001
    return True, {
        "ok": True, "result": "acquired", "lockPath": str(p), "lock": payload,
    }


def release_book_lock(book: Path) -> dict:
    bl = _import_book_lock()
    if bl is None:
        return {"skipped": True}
    p = bl._lock_path(book)  # noqa: SLF001
    existing = bl._read_lock(p)  # noqa: SLF001
    if existing is None:
        return {"ok": True, "result": "no-lock"}
    if not bl._ours(existing):  # noqa: SLF001
        return {"ok": False, "result": "not-ours", "lock": existing}
    try:
        os.remove(p)
        return {"ok": True, "result": "released"}
    except OSError as e:
        return {"ok": False, "result": "io-error", "error": str(e)}


def run_commitment_ledger(book: Path, chapter: int | None) -> dict:
    """Run scripts/commitment_ledger.py against the just-written chapter.

    Resolves memo path = book/story/runtime/chapter_memo.md and draft path via
    `_chapter_files.find_chapter_file`. Returns the ledger validate JSON or
    `{"ok": False, "error": ...}` if either input is missing.

    Critical violations under categories REVEAL_BURY_FLOOR or
    HOOK_PAYOFF_UNLOCATED (commits b1cc3a7 + ab39bd6) trigger the same
    governance_blocked behavior as hook_governance critical issues — truth
    files persist but the caller must not promote the draft.
    """
    if not COMMITMENT_LEDGER_SCRIPT.exists():
        return {"ok": False, "error": "commitment_ledger.py missing"}
    memo = book / "story" / "runtime" / "chapter_memo.md"
    if not memo.exists():
        return {"ok": False, "error": "chapter_memo.md missing — skipped"}
    draft = None
    if isinstance(chapter, int):
        try:
            from _chapter_files import find_chapter_file
            draft = find_chapter_file(book, chapter)
        except Exception:  # noqa: BLE001
            draft = None
    if draft is None or not draft.exists():
        return {"ok": False, "error": f"chapter draft for ch={chapter} missing — skipped"}
    try:
        proc = subprocess.run(
            [sys.executable, str(COMMITMENT_LEDGER_SCRIPT),
             "--memo", str(memo), "--draft", str(draft), "--json"],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return {"ok": False, "error": f"commitment_ledger subprocess failed: {e}"}
    # Exit codes: 0 = clean, 2 = critical violations, 3 = bad input.
    if proc.returncode not in (0, 2):
        return {"ok": False,
                "error": f"commitment_ledger exit={proc.returncode}",
                "stderr": (proc.stderr or "").strip()[:400]}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"commitment_ledger bad JSON: {e}"}


def run_hook_governance(book: Path, command: str, current_chapter=None) -> dict:
    if not HOOK_GOVERNANCE_SCRIPT.exists():
        return {"ok": False, "error": "hook_governance.py missing"}
    cmd = [sys.executable, str(HOOK_GOVERNANCE_SCRIPT),
           "--book", str(book), "--command", command]
    if current_chapter is not None:
        cmd += ["--current-chapter", str(current_chapter)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as e:
        return {"ok": False, "error": f"hook_governance subprocess failed: {e}"}
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": f"hook_governance {command} exit={proc.returncode}",
            "stderr": proc.stderr.strip(),
        }
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"hook_governance bad JSON: {e}"}


def emit_failure(parse_result: dict, *, feedback_format: str) -> int:
    """Emit a structured (or settler-format) failure report and return exit 1.

    `parse_result` comes from `settler_parse.parse_settler_output(...)` and has
    keys: parseStage, issues, softFixes, parserFeedback.
    """
    stage = parse_result.get("parseStage", "extracted")
    issues = parse_result.get("issues")
    fixes = parse_result.get("softFixes", [])

    if feedback_format == "settler" and stage == "schema":
        # Print the human-readable feedback block to stdout so the caller can
        # forward it directly into Settler's next prompt.
        print(parse_result.get("parserFeedback") or render_parser_feedback("schema", issues or []))
    else:
        if stage == "extracted":
            payload = {
                "ok": False,
                "parseStage": "extracted",
                "stage": "extract",  # back-compat alias for older callers
                "error": issues,
                "softFixes": fixes,
                "parserFeedback": parse_result.get("parserFeedback", ""),
            }
        else:
            payload = {
                "ok": False,
                "parseStage": stage,
                "stage": "validate",  # back-compat alias for older callers
                "errors": issues,
                "softFixes": fixes,
                "parserFeedback": parse_result.get("parserFeedback", ""),
            }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    n_issues = len(issues) if isinstance(issues, list) else (1 if issues else 0)
    emit_summary(
        f"FAILED: applied=False parseStage={stage} issues={n_issues} softFixes={len(fixes)}",
        prefix="error",
    )
    return 1


def read_stdin_attempt() -> str:
    """Read one stdin attempt, delimited by EOF or `=== ATTEMPT_END ===`.

    For chained retries from a calling agent. If the caller never writes the
    sentinel, we just consume to EOF.
    """
    chunks: list[str] = []
    for line in sys.stdin:
        if line.strip() == "=== ATTEMPT_END ===":
            break
        chunks.append(line)
    return "".join(chunks)


def parse_input(raw: str, *, input_mode: str) -> dict:
    """Run the settler_parse pipeline.

    Returns the dict from parse_settler_output (keys: ok, parseStage, delta,
    softFixes, issues, parserFeedback, ...). In `json` mode Stage 1 (lenient
    extract from sentinels) is skipped — the raw text is JSON-parsed directly.
    Stage 2 (soft-fix) and Stage 3 (schema validate) run in both modes, so
    legacy callers still benefit from the soft-fix layer.
    """
    return parse_settler_output(raw, mode=input_mode)


def main() -> int:
    # Subcommand-style verb: `apply_delta.py --book BK revert-doc-op --op-id SHA8`
    # Detect before argparse to avoid making `--delta` mandatory for revert.
    if "revert-doc-op" in sys.argv[1:]:
        argv = [a for a in sys.argv[1:] if a != "revert-doc-op"]
        # minimal parser for revert
        rp = argparse.ArgumentParser(description="Revert a previously applied docOp")
        rp.add_argument("--book", required=True)
        rp.add_argument("--op-id", required=True)
        rargs = rp.parse_args(argv)
        rbook = Path(rargs.book).resolve()
        if not rbook.is_dir():
            err(f"book dir not found: {rbook}")
        result = doc_ops.revert(rbook, rargs.op_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    # Subcommand-style verb: `apply_delta.py --book BK log-direct-edit
    #   --file FILE --reason R [--chapter N]`
    # For author-constitution files edited directly under user directive.
    if "log-direct-edit" in sys.argv[1:]:
        argv = [a for a in sys.argv[1:] if a != "log-direct-edit"]
        lp = argparse.ArgumentParser(
            description="Append an audit-trail entry to doc_changes.log for a "
            "direct Edit on an author-constitution file (author_intent / "
            "fanfic_canon / parent_canon / book.json).",
        )
        lp.add_argument("--book", required=True)
        lp.add_argument(
            "--file", required=True,
            help="book-relative path of the file you just edited "
            "(e.g. 'story/author_intent.md' or 'book.json')",
        )
        lp.add_argument("--reason", required=True,
                        help="why the change was made (≤ 200 chars)")
        lp.add_argument(
            "--chapter", type=int, default=None,
            help="optional chapter number to stamp; defaults to "
            "manifest.lastAppliedChapter when available",
        )
        largs = lp.parse_args(argv)
        lbook = Path(largs.book).resolve()
        if not lbook.is_dir():
            err(f"book dir not found: {lbook}")
        chapter = largs.chapter
        if chapter is None:
            mp = lbook / "story" / "state" / "manifest.json"
            if mp.exists():
                try:
                    mfst = json.loads(mp.read_text(encoding="utf-8"))
                    chapter = mfst.get("lastAppliedChapter")
                except (OSError, json.JSONDecodeError):
                    chapter = None
        result = doc_ops.log_direct_edit(lbook, largs.file, largs.reason, chapter)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1

    args = parse_args()
    if not args.delta and not args.delta_stdin:
        err("must specify --delta <path> or --delta-stdin (or use revert-doc-op verb)")
    book = Path(args.book).resolve()
    if not book.is_dir():
        err(f"book dir not found: {book}")

    # ── Book write lock (advisory) ─────────────────────────────────────
    lock_acquired = False
    if not args.skip_lock:
        ok, lock_payload = acquire_book_lock(book, operation="apply-delta")
        if not ok:
            print(json.dumps({
                "ok": False,
                "stage": "lock",
                "error": "could not acquire book write lock",
                "lockReport": lock_payload,
                "hint": (
                    f"Run `python {BOOK_LOCK_SCRIPT.name} --book {book} status` "
                    "to inspect; if no other process is writing, "
                    f"`python {BOOK_LOCK_SCRIPT.name} --book {book} release --force` "
                    "clears it. Pass --skip-lock if you've already acquired it externally."
                ),
            }, ensure_ascii=False, indent=2), file=sys.stderr)
            emit_summary(
                "FAILED: could not acquire book write lock (held by another owner)",
                prefix="error",
            )
            return 3
        lock_acquired = bool(lock_payload.get("ok") and not lock_payload.get("skipped"))

    try:
        return _main_apply(args, book, lock_acquired)
    finally:
        if lock_acquired:
            release_book_lock(book)


def _main_apply(args: argparse.Namespace, book: Path, lock_acquired: bool) -> int:
    # ── Stage 1 + Stage 2 + Stage 3 (with optional chained retries from stdin) ─
    parse_result: dict = {"ok": False, "parseStage": "extracted", "issues": "no input read"}

    if args.delta_stdin:
        attempts_left = max(1, args.max_parse_attempts)
        while attempts_left > 0:
            raw = read_stdin_attempt()
            if not raw.strip():
                parse_result = {
                    "ok": False, "parseStage": "extracted",
                    "issues": "empty stdin attempt", "softFixes": [],
                    "parserFeedback": render_parser_feedback("extract", "empty stdin attempt"),
                }
                break
            parse_result = parse_input(raw, input_mode=args.input_mode)
            if parse_result.get("ok"):
                break
            attempts_left -= 1
            if attempts_left > 0:
                # Emit feedback so the caller can react before piping the next attempt.
                if args.feedback_format == "settler" and parse_result.get("parseStage") == "schema":
                    print(parse_result.get("parserFeedback", ""), file=sys.stderr)
                else:
                    print(json.dumps({
                        "ok": False,
                        "parseStage": parse_result.get("parseStage"),
                        "errors": parse_result.get("issues"),
                        "softFixes": parse_result.get("softFixes", []),
                    }, ensure_ascii=False), file=sys.stderr)
    else:
        raw = Path(args.delta).read_text(encoding="utf-8")
        parse_result = parse_input(raw, input_mode=args.input_mode)

    if not parse_result.get("ok"):
        return emit_failure(parse_result, feedback_format=args.feedback_format)

    delta = parse_result["delta"]
    soft_fixes = parse_result.get("softFixes", [])

    # ── Apply deltas to truth files (unchanged semantics) ───────────────
    modified: list[str] = []
    warnings: list[str] = []

    state_dir = book / "story" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    # ── Hook arbitration (pre-write) ────────────────────────────────────
    # Reshape `hookOps.upsert` (unknown ids) and `newHookCandidates` against
    # the live ledger so apply_hook_ops sees a clean delta. Runs BEFORE the
    # governance validate gate — arbitration shapes the delta; validate
    # confirms it's clean.
    arbitration_report: dict = {"ran": False, "reason": "no hookOps and no newHookCandidates"}
    if not args.skip_arbitration and (delta.get("hookOps") or delta.get("newHookCandidates")):
        hp = state_dir / "hooks.json"
        cur_hooks_obj = load_json(hp, {"hooks": []})
        cur_hooks = cur_hooks_obj.get("hooks", []) if isinstance(cur_hooks_obj, dict) else []
        try:
            arb = arbitrate_hooks(cur_hooks, delta, max_active=args.max_active_hooks)
            delta = arb["resolvedDelta"]
            decisions = arb["decisions"]
            counts = {"created": 0, "mapped": 0, "mentioned": 0, "rejected": 0}
            for d in decisions:
                counts[d["action"]] = counts.get(d["action"], 0) + 1
            arbitration_report = {
                "ran": True,
                "decisions": decisions,
                "summary": (
                    f"n_created={counts['created']} n_mapped={counts['mapped']} "
                    f"n_mentioned={counts['mentioned']} n_rejected={counts['rejected']}"
                ),
                "counts": counts,
            }
            if counts["rejected"] > 0:
                warnings.append(
                    f"hook arbiter rejected {counts['rejected']} candidate(s); "
                    "see arbitration.decisions for reasons."
                )
        except Exception as e:  # noqa: BLE001 — arbitration must not crash apply
            arbitration_report = {"ran": False, "error": f"{e!r}"}
            warnings.append(f"hook arbitration failed: {e!r}; continuing with raw delta.")
    elif args.skip_arbitration:
        arbitration_report = {"ran": False, "reason": "skipped via --skip-arbitration"}

    # ── Role arbitration (pre-write, after hook arbitration) ─────────────
    # Drains delta["newRoleCandidates"] into delta["docOps"]["roles"] as
    # create_role ops; mapped/rejected candidates produce decisions only.
    role_arbitration_report: dict = {
        "ran": False,
        "reason": "no newRoleCandidates",
    }
    if not args.skip_role_arbitration and delta.get("newRoleCandidates"):
        try:
            rarb = arbitrate_roles(book, delta, max_roster=args.max_roster)
            delta = rarb["resolvedDelta"]
            r_decisions = rarb["decisions"]
            r_counts = {"created": 0, "mapped": 0, "rejected": 0}
            for d in r_decisions:
                r_counts[d["action"]] = r_counts.get(d["action"], 0) + 1
            role_arbitration_report = {
                "ran": True,
                "decisions": r_decisions,
                "summary": (
                    f"n_created={r_counts['created']} n_mapped={r_counts['mapped']} "
                    f"n_rejected={r_counts['rejected']}"
                ),
                "counts": r_counts,
            }
            if r_counts["rejected"] > 0:
                warnings.append(
                    f"role arbiter rejected {r_counts['rejected']} candidate(s); "
                    "see roleArbitration.decisions for reasons."
                )
        except Exception as e:  # noqa: BLE001 — must not crash apply
            role_arbitration_report = {"ran": False, "error": f"{e!r}"}
            warnings.append(f"role arbitration failed: {e!r}; continuing.")
    elif args.skip_role_arbitration:
        role_arbitration_report = {
            "ran": False, "reason": "skipped via --skip-role-arbitration",
        }

    if "currentStatePatch" in delta:
        cs_p = state_dir / "current_state.json"
        cur = load_json(cs_p, {"chapter": 0, "facts": []})
        if not isinstance(cur, dict):
            cur = {"chapter": 0, "facts": []}
        strip_legacy_schema_version(cur, "current_state.json", warnings)
        merge_dict(cur, delta["currentStatePatch"])
        # Inkos's CurrentStateStateSchema requires top-level `chapter` field.
        ch = delta.get("chapter")
        if isinstance(ch, int):
            cur["chapter"] = ch
        cur.setdefault("chapter", 0)
        cur.setdefault("facts", [])
        write_json(cs_p, cur)
        modified.append(str(cs_p))

    if "hookOps" in delta:
        hp = state_dir / "hooks.json"
        cur = load_json(hp, {"hooks": []})
        if not isinstance(cur, dict):
            cur = {"hooks": []}
        strip_legacy_schema_version(cur, "hooks.json", warnings)
        new_obj = apply_hook_ops(cur, delta["hookOps"], warnings)
        # apply_hook_ops returns {"hooks": [...]}; nothing else to preserve.
        write_json(hp, new_obj)
        modified.append(str(hp))

    if "chapterSummary" in delta:
        sp = state_dir / "chapter_summaries.json"
        cur = load_json(sp, {"rows": []})
        if not isinstance(cur, dict):
            cur = {"rows": []}
        strip_legacy_schema_version(cur, "chapter_summaries.json", warnings)
        # Backward-read: legacy SKILL books had `summaries:` wrapper; migrate
        # to inkos's `rows:` on first write.
        if "summaries" in cur and "rows" not in cur:
            cur["rows"] = cur.pop("summaries")
            warnings.append(
                "chapter_summaries.json: migrated wrapper key 'summaries' → 'rows' (inkos parity)"
            )
        # Plan A1: upsert by chapter number (was: blind append).  Re-applying a
        # delta for chapter N must replace the existing row, not duplicate it.
        # This makes apply_delta idempotent w.r.t. the chapter_summary part —
        # the prior append-only behavior caused doubled rows when an earlier
        # apply attempt failed late (e.g. docOps schema fail) but had already
        # updated chapter_summaries.json before the failure path returned.
        new_summary = delta["chapterSummary"]
        rows = cur.setdefault("rows", [])
        ch_no = new_summary.get("chapter") if isinstance(new_summary, dict) else None
        replaced = False
        if isinstance(ch_no, int):
            for i, existing in enumerate(rows):
                if isinstance(existing, dict) and existing.get("chapter") == ch_no:
                    rows[i] = new_summary
                    replaced = True
                    warnings.append(
                        f"chapter_summaries.json: upserted row for chapter {ch_no} "
                        "(replaced existing entry; idempotent re-apply)"
                    )
                    break
        if not replaced:
            rows.append(new_summary)
        write_json(sp, cur)
        modified.append(str(sp))

    # Translate legacy table *Ops into docOps batch entries — single write
    # path through doc_ops.apply() with header-aware key upsert.
    translated_count = _translate_table_ops_to_docops(delta)
    if translated_count:
        warnings.append(
            f"translated {translated_count} table-row op(s) "
            "(subplotOps/emotionalArcOps/characterMatrixOps) → docOps batch"
        )

    if "notes" in delta:
        note_p = book / "story" / "runtime" / "settler-notes.log"
        notes = delta["notes"]
        if isinstance(notes, str):
            notes = [notes]
        for n in notes:
            append_text(note_p, str(n))
        modified.append(str(note_p))

    # ── cliffhangerEntry → story/state/cliffhanger_history.json ─────────
    # See references/schemas/runtime-state-delta.md §7c. Append-only ledger
    # of how each chapter ended; Planner reads the last 6 rows to spot
    # over-used cliffhanger types and steer the next chapter elsewhere.
    if "cliffhangerEntry" in delta:
        ce = delta.get("cliffhangerEntry")
        ch_no = delta.get("chapter") if isinstance(delta.get("chapter"), int) else None
        if isinstance(ce, dict) and ch_no is not None:
            ch_p = state_dir / "cliffhanger_history.json"
            cur = load_json(ch_p, {"rows": []})
            if not isinstance(cur, dict):
                cur = {"rows": []}
            cur.setdefault("rows", [])
            from datetime import datetime, timezone
            row = {
                "chapter": ch_no,
                "type": str(ce.get("type", "")).strip(),
                "intensity": ce.get("intensity"),
                "brief": str(ce.get("brief", "")).strip(),
                "recordedAt": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.%f"
                )[:-3] + "Z",
            }
            cur["rows"].append(row)
            write_json(ch_p, cur)
            modified.append(str(ch_p))
        else:
            warnings.append(
                "cliffhangerEntry present but malformed (or chapter missing) — skipped"
            )
    elif isinstance(delta.get("chapter"), int):
        # Settler didn't emit cliffhangerEntry — log warning but do not block.
        warnings.append(
            "cliffhangerEntry missing — Settler should emit one per chapter "
            "(use type='none' for pure-buildup chapters); see runtime-state-delta.md §7c"
        )

    # ── docOps (LLM-driven guidance md edits) ───────────────────────────
    # Defense in depth: blacklist enforcement here is independent of the
    # schema check in settler_parse.validate_delta. Schema-fail upstream
    # would normally have refused the batch already, but a forged delta
    # JSON shouldn't be able to reach the truth files either.
    doc_chapter = delta.get("chapter") if isinstance(delta.get("chapter"), int) else None
    doc_ops_applied: list[dict] = doc_ops.apply(
        book,
        delta.get("docOps"),
        warnings,
        modified,
        chapter=doc_chapter,
    )

    # ── render JSON-paired markdown views ───────────────────────────────
    # current_state / pending_hooks / chapter_summaries md are pure outputs:
    # rebuilt deterministically from state/*.json after every apply_delta so
    # downstream consumers (audit_drift, hook_governance, pov_filter fallback)
    # see fresh data instead of the empty template that used to ship.
    _render_views_for(book, modified, warnings)

    # ── hook governance gate ────────────────────────────────────────────
    governance_report: dict = {}
    governance_blocked = False
    if not args.skip_hook_governance:
        chapter_for_scan = None
        if "chapterSummary" in delta and isinstance(delta["chapterSummary"], dict):
            ch = delta["chapterSummary"].get("chapter")
            if isinstance(ch, int):
                chapter_for_scan = ch

        validate_out = run_hook_governance(book, "validate", chapter_for_scan)
        stale_out = run_hook_governance(book, "stale-scan", chapter_for_scan)
        governance_report = {
            "validate": validate_out,
            "staleScan": stale_out,
        }
        if isinstance(validate_out, dict) and validate_out.get("ok"):
            crit = (validate_out.get("counts") or {}).get("critical", 0)
            if crit > 0:
                governance_blocked = True
                warnings.append(
                    f"hook_governance validate flagged {crit} critical issue(s); "
                    "delta written to disk but caller should NOT promote draft."
                )
        elif isinstance(validate_out, dict) and not validate_out.get("ok"):
            warnings.append(f"hook_governance validate failed: {validate_out.get('error')}")

    # ── commitment_ledger gate (reveal-bury floor + payoff window) ──────
    # Ports inkos commits b1cc3a7 (hook-ledger-validator hard floor) +
    # ab39bd6 (hook payoff must be concretely locatable in prose). Runs only
    # when the memo + chapter draft are on disk; missing inputs degrade to a
    # warning, not a block, so legacy runners keep working.
    commitment_report: dict = {}
    if not args.skip_commitment_ledger and not governance_blocked:
        commitment_report = run_commitment_ledger(book, chapter_for_scan)
        if isinstance(commitment_report, dict):
            # Two distinct "ok=False" modes need disambiguation:
            #   (1) wrapper-level: subprocess error / missing memo or draft
            #       → has "error" key, no "violations" key
            #   (2) ledger-level: commitment_ledger ran but found critical
            #       violations → has "violations" array, no "error" key
            if "error" in commitment_report and "violations" not in commitment_report:
                warnings.append(
                    f"commitment_ledger skipped: {commitment_report.get('error')}"
                )
            else:
                crit_violations = [
                    v for v in (commitment_report.get("violations") or [])
                    if v.get("severity") == "critical"
                ]
                if crit_violations:
                    governance_blocked = True
                    cats = sorted({v.get("category", "") for v in crit_violations})
                    warnings.append(
                        f"commitment_ledger flagged {len(crit_violations)} "
                        f"critical issue(s) [{', '.join(c for c in cats if c)}]; "
                        "delta written but caller should NOT promote draft."
                    )

    # ── book.json metadata maintenance (mirrors inkos markBookActiveIfNeeded) ──
    chapter_no = delta.get("chapter") if isinstance(delta, dict) else None
    chapter_no = chapter_no if isinstance(chapter_no, int) else None
    if args.skip_book_metadata or governance_blocked:
        # Skip when explicitly disabled, or when governance refused to commit
        # (status flip / updatedAt bump should only fire on a real successful write).
        book_metadata = {
            "ran": False,
            "reason": "skipped via --skip-book-metadata"
            if args.skip_book_metadata
            else "skipped due to hookGovernanceBlocked",
        }
    else:
        try:
            book_metadata = _bump_book_metadata(book, chapter_no)
        except Exception as e:  # noqa: BLE001
            book_metadata = {"ran": False, "reason": f"unexpected: {e!r}"}
            warnings.append(f"book metadata bump failed: {e!r}")

    # ── manifest.lastAppliedChapter advance (mirrors inkos state-reducer.ts:64-67) ──
    if governance_blocked:
        manifest_advance = {"ran": False, "reason": "skipped due to hookGovernanceBlocked"}
    else:
        try:
            manifest_advance = _advance_manifest_chapter(book, chapter_no, warnings)
        except Exception as e:  # noqa: BLE001
            manifest_advance = {"ran": False, "reason": f"unexpected: {e!r}"}
            warnings.append(f"manifest advance failed: {e!r}")
        if manifest_advance.get("ran"):
            modified.append(str(book / "story" / "state" / "manifest.json"))

    print(json.dumps({
        "ok": True,
        "applied": True,
        "parseStage": "applied",
        "softFixes": soft_fixes,
        "parserFeedback": "",
        "filesModified": sorted(set(modified)),
        "warnings": warnings,
        "arbitration": arbitration_report,
        "roleArbitration": role_arbitration_report,
        "hookGovernance": governance_report,
        "commitmentLedger": commitment_report,
        "hookGovernanceBlocked": governance_blocked,
        "bookMetadata": book_metadata,
        "manifestAdvance": manifest_advance,
        "docOpsApplied": doc_ops_applied,
    }, ensure_ascii=False, indent=2))
    chapter_label = "?"
    if isinstance(delta, dict):
        ch = delta.get("chapter")
        if not isinstance(ch, int):
            cs = delta.get("chapterSummary")
            if isinstance(cs, dict) and isinstance(cs.get("chapter"), int):
                ch = cs["chapter"]
        if isinstance(ch, int):
            chapter_label = str(ch)
    files_n = len(set(modified))
    soft_n = len(soft_fixes) if isinstance(soft_fixes, list) else 0
    warn_n = len(warnings)
    if governance_blocked:
        emit_summary(
            f"BLOCKED: hook governance critical (ch={chapter_label} files={files_n} "
            f"softFixes={soft_n} warnings={warn_n})",
            prefix="error",
        )
    else:
        emit_summary(
            f"applied=True ch={chapter_label} files={files_n} softFixes={soft_n} "
            f"warnings={warn_n} parseStage=applied"
        )
    return 1 if governance_blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
