#!/usr/bin/env python3
"""Settler 2-stage parser (standalone CLI + reusable module).

Mirrors inkos `agents/settler-parser.ts` + `agents/settler-delta-parser.ts`,
extended with a soft-fix layer that auto-corrects common Settler format
deviations *before* schema validation. This is what makes Settler retries
much cheaper: small format issues (snake_case keys, stringified ints, wrong
key casing) are silently normalized; only genuine semantic errors trigger
a Settler re-run.

Pipeline
--------
    Settler raw chat output
        │  (sentinels: === POST_SETTLEMENT === / === RUNTIME_STATE_DELTA ===)
        ▼
    Stage 1 — lenient extract     → returns the JSON block + post_settlement text
        │
        ▼
    Stage 2 — soft-fix normalize  → renames known aliases, coerces obvious types,
        │                            logs every fix into `softFixes`
        ▼
    Stage 3 — strict validate     → returns structured `errors` for Settler retry

Stage 1 + Stage 2 are non-destructive: even on hard failure we return whatever
we managed to extract, so the caller can decide whether to retry, log, or
surface a parserFeedback block to Settler.

CLI
---
    python settler_parse.py --input <raw-or-json-file> [--mode raw|json] \\
        [--out <delta.json>] [--strict]

When `--out` is given and parsing succeeded (Stage 3 ok), writes the cleaned
delta JSON to `<out>` so apply_delta.py can consume it as plain JSON.
When `--strict`, exits non-zero on any soft-fix as well as hard errors.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _constants import HOOK_OPS, HOOK_STATUS, PAYOFF_TIMING  # noqa: E402

# Re-exported under legacy names (apply_delta + others import these).
VALID_HOOK_OPS = HOOK_OPS
VALID_HOOK_STATUS = HOOK_STATUS
VALID_PAYOFF_TIMING = PAYOFF_TIMING


# ───────────────────────────── stage 1: extract ─────────────────────────────


def sanitize_json(s: str) -> str:
    """Mirror inkos `sanitizeJSON`: strip control chars + trailing commas."""
    s = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", s)
    s = re.sub(r",\s*([}\]])", r"\1", s)
    return s


def strip_code_fence(value: str) -> str:
    trimmed = value.strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", trimmed, re.IGNORECASE)
    return m.group(1).strip() if m else trimmed


def lenient_extract(raw: str) -> tuple[str, str | None, str]:
    """Stage 1 — locate the RUNTIME_STATE_DELTA JSON payload.

    Returns (json_text, post_settlement_text_or_None, source_label).
    Tolerant of:
      - leading/trailing prose around the sentinels
      - indented `=== TAG ===` lines
      - missing trailing `=== END ===`
      - markdown ```json fences inside the block

    Raises ValueError if no JSON-shaped payload could be located.
    """
    if raw is None:
        raise ValueError("input is empty")
    text = raw.strip()
    if not text:
        raise ValueError("input is empty")

    # Allow indented / leading-whitespace sentinels by matching at line start with optional ws.
    # Use re.MULTILINE flag rather than inline (?m) to keep the pattern composable.
    def sentinel(name: str) -> str:
        return r"^[ \t]*===\s*" + name + r"\s*===[ \t]*$"

    post: str | None = None
    m_post = re.search(
        sentinel("POST_SETTLEMENT") + r"\s*([\s\S]*?)(?=" + sentinel(r"[A-Z_]+") + r"|\Z)",
        text,
        re.MULTILINE,
    )
    if m_post:
        post_body = m_post.group(1).strip()
        post = post_body or None

    # Variant A: explicit `=== RUNTIME_STATE_DELTA === ... === END ===` sentinels.
    m = re.search(
        sentinel("RUNTIME_STATE_DELTA")
        + r"\s*([\s\S]*?)\s*"
        + sentinel("END"),
        text,
        re.MULTILINE,
    )
    if m:
        return strip_code_fence(m.group(1)), post, "sentinel-end"

    # Variant B: `=== RUNTIME_STATE_DELTA ===` without END — read until next sentinel or EOF.
    m = re.search(
        sentinel("RUNTIME_STATE_DELTA")
        + r"\s*([\s\S]*?)(?="
        + sentinel(r"[A-Z_]+")
        + r"|\Z)",
        text,
        re.MULTILINE,
    )
    if m:
        return strip_code_fence(m.group(1)), post, "sentinel-open"

    # Variant C: ```json ... ``` fenced block anywhere in the text.
    m = re.search(r"```json\s*([\s\S]*?)```", text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), post, "code-fence"

    # Variant D: bare JSON.
    if text.lstrip().startswith("{"):
        return text, post, "bare"

    # Variant E: any embedded {...} block — last-resort scan (greedy on outermost braces).
    m = re.search(r"(\{[\s\S]*\})", text)
    if m:
        return m.group(1), post, "embedded-braces"

    raise ValueError("no RUNTIME_STATE_DELTA JSON block found in input")


# ───────────────────────────── stage 2: soft-fix ────────────────────────────


# Top-level key aliases → canonical name. All match strategies: exact + case-insensitive +
# snake_case. We only rename when the canonical key is NOT already present, to avoid
# clobbering a Settler that emitted both.
TOP_KEY_ALIASES: dict[str, str] = {
    "chapterNumber": "chapter",
    "chapter_number": "chapter",
    "chapterNo": "chapter",
    "state_patch": "currentStatePatch",
    "statePatch": "currentStatePatch",
    "current_state_patch": "currentStatePatch",
    "currentstatepatch": "currentStatePatch",
    "hook_ops": "hookOps",
    "hookops": "hookOps",
    "new_hook_candidates": "newHookCandidates",
    "newhookcandidates": "newHookCandidates",
    "chapter_summary": "chapterSummary",
    "chaptersummary": "chapterSummary",
    "subplot_ops": "subplotOps",
    "subplotops": "subplotOps",
    "emotional_arc_ops": "emotionalArcOps",
    "emotionalarcops": "emotionalArcOps",
    "character_matrix_ops": "characterMatrixOps",
    "charactermatrixops": "characterMatrixOps",
}

HOOK_RECORD_ALIASES: dict[str, str] = {
    "hook_id": "hookId",
    "hookid": "hookId",
    "start_chapter": "startChapter",
    "startchapter": "startChapter",
    "last_advanced_chapter": "lastAdvancedChapter",
    "lastadvancedchapter": "lastAdvancedChapter",
    "expected_payoff": "expectedPayoff",
    "expectedpayoff": "expectedPayoff",
    "payoff_timing": "payoffTiming",
    "payofftiming": "payoffTiming",
    "depends_on": "dependsOn",
    "dependson": "dependsOn",
    "pays_off_in_arc": "paysOffInArc",
    "paysoffinarc": "paysOffInArc",
    "core_hook": "coreHook",
    "corehook": "coreHook",
    "half_life_chapters": "halfLifeChapters",
    "halflifechapters": "halfLifeChapters",
    "advanced_count": "advancedCount",
    "advancedcount": "advancedCount",
}

CHAPTER_SUMMARY_ALIASES: dict[str, str] = {
    "chapter_type": "chapterType",
    "chaptertype": "chapterType",
    "hook_activity": "hookActivity",
    "hookactivity": "hookActivity",
    "state_changes": "stateChanges",
    "statechanges": "stateChanges",
}

CURRENT_STATE_ALIASES: dict[str, str] = {
    "current_location": "currentLocation",
    "currentlocation": "currentLocation",
    "protagonist_state": "protagonistState",
    "protagoniststate": "protagonistState",
    "current_goal": "currentGoal",
    "currentgoal": "currentGoal",
    "current_constraint": "currentConstraint",
    "currentconstraint": "currentConstraint",
    "current_alliances": "currentAlliances",
    "currentalliances": "currentAlliances",
    "current_conflict": "currentConflict",
    "currentconflict": "currentConflict",
}

NEW_HOOK_CAND_ALIASES: dict[str, str] = {
    "expected_payoff": "expectedPayoff",
    "expectedpayoff": "expectedPayoff",
    "payoff_timing": "payoffTiming",
    "payofftiming": "payoffTiming",
}


def _rename_keys(
    obj: dict, alias_map: dict[str, str], path: str, fixes: list[dict]
) -> dict:
    """Rename aliased keys in-place; record each rename in fixes. Idempotent."""
    if not isinstance(obj, dict):
        return obj
    # Build a canonical-target set so we don't overwrite existing canonical keys.
    targets = set(alias_map.values())
    # Two-pass: first detect renames, then apply (so dict iteration stays stable).
    renames: list[tuple[str, str]] = []
    for k in list(obj.keys()):
        # match: exact > lowercase
        target: str | None = None
        if k in alias_map:
            target = alias_map[k]
        elif isinstance(k, str) and k.lower() in alias_map:
            target = alias_map[k.lower()]
        # Already canonical? skip.
        if target is None or k == target:
            continue
        # Don't clobber.
        if target in obj:
            fixes.append({
                "path": f"{path}.{k}",
                "fix": "drop_duplicate_alias",
                "note": f"both '{k}' and canonical '{target}' present; dropping alias",
            })
            del obj[k]
            continue
        renames.append((k, target))
    for src, dst in renames:
        obj[dst] = obj.pop(src)
        fixes.append({
            "path": f"{path}.{src}",
            "fix": "rename_key",
            "to": dst,
        })
    # Drop any leftover non-canonical keys whose canonical exists? No — keep unknown
    # keys around; validate_delta only flags known-bad keys (subplotOps etc are open).
    _ = targets  # keep targets lookup explicit; not used after dedupe
    return obj


def _coerce_int(v: Any) -> tuple[Any, bool]:
    """Try to coerce v to int. Returns (value, did_coerce)."""
    if isinstance(v, bool):
        return v, False
    if isinstance(v, int):
        return v, False
    if isinstance(v, float) and v.is_integer():
        return int(v), True
    if isinstance(v, str):
        s = v.strip()
        if re.fullmatch(r"-?\d+", s):
            try:
                return int(s), True
            except ValueError:
                return v, False
        # "第12章" / "chapter 12" / "12回"-style — last-resort digits extraction.
        m = re.search(r"-?\d+", s)
        if m:
            try:
                return int(m.group(0)), True
            except ValueError:
                return v, False
    return v, False


def _soft_fix_hook_record(h: Any, path: str, fixes: list[dict]) -> Any:
    if not isinstance(h, dict):
        return h
    _rename_keys(h, HOOK_RECORD_ALIASES, path, fixes)
    for k in ("startChapter", "lastAdvancedChapter", "halfLifeChapters", "advancedCount"):
        if k in h:
            new, did = _coerce_int(h[k])
            if did:
                fixes.append({"path": f"{path}.{k}", "fix": "coerce_int", "from": h[k], "to": new})
                h[k] = new
    # Some Settlers emit `status: "Resolved"` capital.
    if "status" in h and isinstance(h["status"], str):
        low = h["status"].strip().lower()
        if low != h["status"] and low in VALID_HOOK_STATUS:
            fixes.append({"path": f"{path}.status", "fix": "lowercase_enum", "from": h["status"], "to": low})
            h["status"] = low
    if "payoffTiming" in h and isinstance(h["payoffTiming"], str):
        low = h["payoffTiming"].strip().lower()
        if low != h["payoffTiming"] and low in VALID_PAYOFF_TIMING:
            fixes.append({"path": f"{path}.payoffTiming", "fix": "lowercase_enum", "from": h["payoffTiming"], "to": low})
            h["payoffTiming"] = low
    return h


def soft_fix(delta: Any, fixes: list[dict] | None = None) -> tuple[Any, list[dict]]:
    """Stage 2 — normalize common Settler format deviations.

    Mutates `delta` in place AND returns it. `fixes` collects a structured log
    of every mutation, so callers can echo it back to Settler ("we silently
    accepted X soft fixes; please prefer canonical names next time").
    """
    if fixes is None:
        fixes = []
    if not isinstance(delta, dict):
        return delta, fixes

    _rename_keys(delta, TOP_KEY_ALIASES, "$", fixes)

    # chapter: coerce string "12" → 12
    if "chapter" in delta:
        new, did = _coerce_int(delta["chapter"])
        if did:
            fixes.append({"path": "$.chapter", "fix": "coerce_int", "from": delta["chapter"], "to": new})
            delta["chapter"] = new

    # currentStatePatch keys
    if isinstance(delta.get("currentStatePatch"), dict):
        _rename_keys(delta["currentStatePatch"], CURRENT_STATE_ALIASES, "$.currentStatePatch", fixes)

    # hookOps
    if isinstance(delta.get("hookOps"), dict):
        ho = delta["hookOps"]
        # Some Settlers emit single-record forms; coerce to lists.
        for arr_key in ("upsert", "mention", "resolve", "defer"):
            if arr_key in ho and not isinstance(ho[arr_key], list):
                if ho[arr_key] in (None, ""):
                    fixes.append({"path": f"$.hookOps.{arr_key}", "fix": "drop_null_array"})
                    del ho[arr_key]
                else:
                    fixes.append({"path": f"$.hookOps.{arr_key}", "fix": "wrap_in_array", "from": type(ho[arr_key]).__name__})
                    ho[arr_key] = [ho[arr_key]]
        if isinstance(ho.get("upsert"), list):
            for i, h in enumerate(ho["upsert"]):
                _soft_fix_hook_record(h, f"$.hookOps.upsert[{i}]", fixes)

    # newHookCandidates
    if "newHookCandidates" in delta:
        nhc = delta["newHookCandidates"]
        if not isinstance(nhc, list):
            if nhc in (None, ""):
                fixes.append({"path": "$.newHookCandidates", "fix": "drop_null_array"})
                del delta["newHookCandidates"]
            elif isinstance(nhc, dict):
                fixes.append({"path": "$.newHookCandidates", "fix": "wrap_in_array", "from": "object"})
                delta["newHookCandidates"] = [nhc]
                nhc = delta["newHookCandidates"]
        if isinstance(nhc, list):
            for i, c in enumerate(nhc):
                if isinstance(c, dict):
                    _rename_keys(c, NEW_HOOK_CAND_ALIASES, f"$.newHookCandidates[{i}]", fixes)
                    if "payoffTiming" in c and isinstance(c["payoffTiming"], str):
                        low = c["payoffTiming"].strip().lower()
                        if low != c["payoffTiming"] and low in VALID_PAYOFF_TIMING:
                            fixes.append({
                                "path": f"$.newHookCandidates[{i}].payoffTiming",
                                "fix": "lowercase_enum",
                                "from": c["payoffTiming"],
                                "to": low,
                            })
                            c["payoffTiming"] = low

    # chapterSummary
    if isinstance(delta.get("chapterSummary"), dict):
        cs = delta["chapterSummary"]
        _rename_keys(cs, CHAPTER_SUMMARY_ALIASES, "$.chapterSummary", fixes)
        if "chapter" in cs:
            new, did = _coerce_int(cs["chapter"])
            if did:
                fixes.append({"path": "$.chapterSummary.chapter", "fix": "coerce_int", "from": cs["chapter"], "to": new})
                cs["chapter"] = new

    # notes: string → list[string]
    if "notes" in delta and isinstance(delta["notes"], str):
        s = delta["notes"]
        fixes.append({"path": "$.notes", "fix": "wrap_in_array", "from": "string"})
        delta["notes"] = [s] if s else []

    # *_ops arrays: drop nulls / wrap singletons
    for arr_key in ("subplotOps", "emotionalArcOps", "characterMatrixOps"):
        if arr_key in delta:
            v = delta[arr_key]
            if v is None:
                fixes.append({"path": f"$.{arr_key}", "fix": "drop_null_array"})
                del delta[arr_key]
            elif isinstance(v, dict):
                fixes.append({"path": f"$.{arr_key}", "fix": "wrap_in_array", "from": "object"})
                delta[arr_key] = [v]

    return delta, fixes


# ───────────────────────────── stage 3: validate ────────────────────────────


def _ferr(path: str, got, expected: str) -> dict:
    return {"path": path, "got": got, "expected": expected}


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validate_hook_record(path: str, h) -> list[dict]:
    out: list[dict] = []
    if not isinstance(h, dict):
        return [_ferr(path, type(h).__name__, "object")]
    if not isinstance(h.get("hookId"), str) or not h.get("hookId"):
        out.append(_ferr(f"{path}.hookId", h.get("hookId", "missing"), "non-empty string"))
    if not isinstance(h.get("type"), str) or not h.get("type"):
        out.append(_ferr(f"{path}.type", h.get("type", "missing"), "non-empty string"))
    if "startChapter" not in h:
        out.append(_ferr(f"{path}.startChapter", "missing", "int>=0"))
    elif not _is_int(h["startChapter"]) or h["startChapter"] < 0:
        out.append(_ferr(f"{path}.startChapter", h["startChapter"], "int>=0"))
    if "lastAdvancedChapter" not in h:
        out.append(_ferr(f"{path}.lastAdvancedChapter", "missing", "int>=0"))
    elif not _is_int(h["lastAdvancedChapter"]) or h["lastAdvancedChapter"] < 0:
        out.append(_ferr(f"{path}.lastAdvancedChapter", h["lastAdvancedChapter"], "int>=0"))
    if "status" not in h:
        out.append(_ferr(f"{path}.status", "missing", "|".join(sorted(VALID_HOOK_STATUS))))
    elif h["status"] not in VALID_HOOK_STATUS:
        out.append(_ferr(f"{path}.status", h["status"], "|".join(sorted(VALID_HOOK_STATUS))))
    if "payoffTiming" in h and h["payoffTiming"] not in VALID_PAYOFF_TIMING:
        out.append(_ferr(f"{path}.payoffTiming", h["payoffTiming"], "|".join(VALID_PAYOFF_TIMING)))
    return out


def validate_delta(d) -> list[dict]:
    """Stage 3 — strict schema validation. Returns list of structured errors."""
    errs: list[dict] = []
    if not isinstance(d, dict):
        return [_ferr("$", type(d).__name__, "object")]

    if "chapter" not in d:
        errs.append(_ferr("chapter", "missing", "int>=1"))
    elif not _is_int(d["chapter"]) or d["chapter"] < 1:
        errs.append(_ferr("chapter", d["chapter"], "int>=1"))

    if "currentStatePatch" in d:
        cp = d["currentStatePatch"]
        if not isinstance(cp, dict):
            errs.append(_ferr("currentStatePatch", type(cp).__name__, "object"))
        else:
            for k in (
                "currentLocation", "protagonistState", "currentGoal",
                "currentConstraint", "currentAlliances", "currentConflict",
            ):
                if k in cp and not isinstance(cp[k], str):
                    errs.append(_ferr(f"currentStatePatch.{k}", type(cp[k]).__name__, "string"))

    if "hookOps" in d:
        ho = d["hookOps"]
        if not isinstance(ho, dict):
            errs.append(_ferr("hookOps", type(ho).__name__, "object"))
        else:
            for k in ho:
                if k not in VALID_HOOK_OPS:
                    errs.append(_ferr(f"hookOps.{k}", k, f"one of {sorted(VALID_HOOK_OPS)}"))
            for k in ("mention", "resolve", "defer"):
                if k in ho:
                    if not isinstance(ho[k], list):
                        errs.append(_ferr(f"hookOps.{k}", type(ho[k]).__name__, "array<string>"))
                    else:
                        for i, hid in enumerate(ho[k]):
                            if not isinstance(hid, str) or not hid:
                                errs.append(_ferr(f"hookOps.{k}[{i}]", hid, "non-empty string"))
            if "upsert" in ho:
                if not isinstance(ho["upsert"], list):
                    errs.append(_ferr("hookOps.upsert", type(ho["upsert"]).__name__, "array<HookRecord>"))
                else:
                    for i, h in enumerate(ho["upsert"]):
                        errs.extend(_validate_hook_record(f"hookOps.upsert[{i}]", h))

    if "newHookCandidates" in d:
        nhc = d["newHookCandidates"]
        if not isinstance(nhc, list):
            errs.append(_ferr("newHookCandidates", type(nhc).__name__, "array"))
        else:
            for i, c in enumerate(nhc):
                if not isinstance(c, dict):
                    errs.append(_ferr(f"newHookCandidates[{i}]", type(c).__name__, "object"))
                    continue
                if "type" not in c or not isinstance(c.get("type"), str) or not c["type"]:
                    errs.append(_ferr(f"newHookCandidates[{i}].type", c.get("type", "missing"), "non-empty string"))
                if "payoffTiming" in c and c["payoffTiming"] not in VALID_PAYOFF_TIMING:
                    errs.append(_ferr(
                        f"newHookCandidates[{i}].payoffTiming",
                        c["payoffTiming"],
                        "|".join(VALID_PAYOFF_TIMING),
                    ))

    if "chapterSummary" in d:
        cs = d["chapterSummary"]
        if not isinstance(cs, dict):
            errs.append(_ferr("chapterSummary", type(cs).__name__, "object"))
        else:
            if "chapter" not in cs:
                errs.append(_ferr("chapterSummary.chapter", "missing", "int>=1"))
            elif not _is_int(cs["chapter"]) or cs["chapter"] < 1:
                errs.append(_ferr("chapterSummary.chapter", cs["chapter"], "int>=1"))
            elif _is_int(d.get("chapter")) and cs["chapter"] != d["chapter"]:
                errs.append(_ferr(
                    "chapterSummary.chapter",
                    cs["chapter"],
                    f"equal to top-level chapter ({d['chapter']})",
                ))
            if "title" in cs and (not isinstance(cs["title"], str) or not cs["title"]):
                errs.append(_ferr("chapterSummary.title", cs.get("title"), "non-empty string"))

    for arr_key in ("subplotOps", "emotionalArcOps", "characterMatrixOps"):
        if arr_key in d and not isinstance(d[arr_key], list):
            errs.append(_ferr(arr_key, type(d[arr_key]).__name__, "array"))

    if "notes" in d and not isinstance(d["notes"], (str, list)):
        errs.append(_ferr("notes", type(d["notes"]).__name__, "string|array<string>"))

    return errs


# ───────────────────────────── parser-feedback rendering ────────────────────


def render_parser_feedback(stage: str, errors: list[dict] | str) -> str:
    """Build a human-readable feedback block to inject back into Settler.

    `stage`: "extract" | "schema". On `extract` we got nothing JSON-shaped —
    hand Settler a sentinel reminder. On `schema` we got specific field errors.
    """
    lines = ["=== SETTLER_FEEDBACK ==="]
    if stage == "extract":
        lines.append("上一次输出无法定位 RUNTIME_STATE_DELTA JSON 块。")
        if isinstance(errors, str):
            lines.append(f"原因：{errors}")
        lines.append("请严格使用以下输出格式：")
        lines.append("=== POST_SETTLEMENT ===")
        lines.append("（人读摘要）")
        lines.append("=== RUNTIME_STATE_DELTA ===")
        lines.append("```json")
        lines.append("{ ...delta JSON 见 schema... }")
        lines.append("```")
    else:
        errs = errors if isinstance(errors, list) else []
        lines.append(f"上一次输出的 RUNTIME_STATE_DELTA 有 {len(errs)} 处问题需要修正：")
        for e in errs:
            path = e.get("path", "?")
            got = e.get("got", "?")
            expected = e.get("expected", "?")
            if got == "missing":
                lines.append(f"- {path}: 必填字段缺失，期望 {expected}")
            else:
                got_s = json.dumps(got, ensure_ascii=False) if not isinstance(got, str) else f'"{got}"'
                lines.append(f"- {path}: 你写了 {got_s}，但允许值是 {expected}")
        lines.append("请仅修正这几处，其余字段保持原样重新输出 RUNTIME_STATE_DELTA。")
    lines.append("=== END ===")
    return "\n".join(lines)


# ───────────────────────────── pipeline entry ───────────────────────────────


def parse_settler_output(raw: str, *, mode: str = "raw") -> dict:
    """Run the full 3-stage pipeline.

    Returns a dict with shape:
        {
          "ok": bool,
          "parseStage": "extracted" | "softfix" | "schema",
          "delta": <cleaned dict> | None,
          "postSettlement": <str> | None,
          "extractSource": <str> | None,   # which stage-1 variant matched
          "softFixes": [ {path, fix, ...}, ... ],
          "issues": [ {path, got, expected}, ... ] | "<extract error msg>",
          "parserFeedback": <str>,         # ready to paste into Settler retry
        }

    `mode`:
      - "raw":  apply Stage 1 + 2 + 3 (full Settler chat output).
      - "json": skip Stage 1 — parse `raw` as JSON directly, then Stage 2 + 3.
                (For backwards-compat callers that already have clean delta JSON.)
    """
    out: dict = {
        "ok": False,
        "parseStage": "extracted",
        "delta": None,
        "postSettlement": None,
        "extractSource": None,
        "softFixes": [],
        "issues": None,
        "parserFeedback": "",
    }

    # Stage 1 — extract
    if mode == "json":
        json_text = raw
        out["extractSource"] = "json-mode"
    else:
        try:
            json_text, post, src = lenient_extract(raw)
            out["postSettlement"] = post
            out["extractSource"] = src
        except ValueError as e:
            out["parseStage"] = "extracted"
            out["issues"] = str(e)
            out["parserFeedback"] = render_parser_feedback("extract", str(e))
            return out

    # Stage 1b — JSON parse with sanitize
    try:
        delta = json.loads(sanitize_json(json_text))
    except json.JSONDecodeError as e:
        msg = f"delta not valid JSON after stage-1 extract: {e}"
        out["parseStage"] = "extracted"
        out["issues"] = msg
        out["parserFeedback"] = render_parser_feedback("extract", msg)
        return out

    # Stage 2 — soft-fix
    delta, fixes = soft_fix(delta)
    out["delta"] = delta
    out["softFixes"] = fixes
    out["parseStage"] = "softfix"

    # Stage 3 — strict validate
    errors = validate_delta(delta)
    if errors:
        out["parseStage"] = "schema"
        out["issues"] = errors
        out["parserFeedback"] = render_parser_feedback("schema", errors)
        return out

    out["ok"] = True
    out["parseStage"] = "schema"  # passed schema; apply_delta moves it to "applied".
    return out


# ───────────────────────────── CLI ──────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Settler 2-stage parser (lenient extract + soft-fix + strict validate)",
    )
    p.add_argument("--input", required=True, help="path to Settler raw output OR delta JSON")
    p.add_argument(
        "--mode",
        choices=["raw", "json"],
        default="raw",
        help="raw=full Settler chat output with sentinels; json=already-clean delta JSON (skips stage 1)",
    )
    p.add_argument("--out", help="if given and parsing succeeds, write cleaned delta JSON here")
    p.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if any softFixes were applied (treat soft fixes as failures)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    src = Path(args.input)
    if not src.is_file():
        print(json.dumps({"ok": False, "error": f"input not found: {src}"}, ensure_ascii=False))
        return 2
    raw = src.read_text(encoding="utf-8")

    result = parse_settler_output(raw, mode=args.mode)

    if result["ok"] and args.out:
        out_p = Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(
            json.dumps(result["delta"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["wroteCleanDelta"] = str(out_p)

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result["ok"]:
        return 1
    if args.strict and result["softFixes"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
