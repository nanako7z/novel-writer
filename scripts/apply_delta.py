#!/usr/bin/env python3
"""Validate a RuntimeStateDelta JSON and apply it to the book's truth files.

Validation is manual (no jsonschema dep). Writes are atomic via .tmp + rename.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

VALID_HOOK_OPS = {"upsert", "mention", "resolve", "defer"}

HOOK_GOVERNANCE_SCRIPT = Path(__file__).resolve().parent / "hook_governance.py"


def err(msg: str, code: int = 1) -> "None":
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


def validate_delta(d) -> list[str]:
    errs: list[str] = []
    if not isinstance(d, dict):
        return ["delta must be an object"]
    # All fields optional, but if present must have correct type
    if "currentStatePatch" in d and not isinstance(d["currentStatePatch"], dict):
        errs.append("currentStatePatch must be object")
    if "hookOps" in d:
        ho = d["hookOps"]
        if not isinstance(ho, dict):
            errs.append("hookOps must be object")
        else:
            for k, v in ho.items():
                if k not in VALID_HOOK_OPS:
                    errs.append(f"hookOps.{k} not in {sorted(VALID_HOOK_OPS)}")
                elif not isinstance(v, list):
                    errs.append(f"hookOps.{k} must be array")
    if "chapterSummary" in d:
        cs = d["chapterSummary"]
        if not isinstance(cs, dict):
            errs.append("chapterSummary must be object")
        else:
            if "chapter" not in cs or not isinstance(cs["chapter"], int):
                errs.append("chapterSummary.chapter required int")
    for arr_key in ("subplotOps", "emotionalArcOps", "characterMatrixOps"):
        if arr_key in d and not isinstance(d[arr_key], list):
            errs.append(f"{arr_key} must be array")
    if "notes" in d and not isinstance(d["notes"], (str, list)):
        errs.append("notes must be string or array")
    return errs


def merge_dict(dst: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge_dict(dst[k], v)
        else:
            dst[k] = v
    return dst


def apply_hook_ops(hooks_obj: dict, ops: dict, warnings: list[str]) -> dict:
    hooks = hooks_obj.get("hooks", [])
    by_id = {h.get("hookId"): h for h in hooks if isinstance(h, dict)}
    for h in ops.get("upsert", []) or []:
        hid = h.get("hookId")
        if not hid:
            warnings.append("upsert hook missing hookId; skipped")
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


def md_row(values: list) -> str:
    return "| " + " | ".join(str(v) if v is not None else "" for v in values) + " |"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Apply RuntimeStateDelta to truth files")
    p.add_argument("--book", required=True, help="book directory path")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--delta", help="path to delta JSON file")
    g.add_argument("--delta-stdin", action="store_true")
    p.add_argument(
        "--skip-hook-governance",
        action="store_true",
        help="skip the post-write hook_governance validate+stale-scan pass",
    )
    return p.parse_args()


def run_hook_governance(book: Path, command: str, current_chapter: int | None = None) -> dict:
    """Invoke scripts/hook_governance.py as a subprocess, return parsed JSON.

    On hard errors (non-zero exit) return a sentinel dict with `ok: False` so
    apply_delta can decide how to react without crashing.
    """
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


def main() -> int:
    args = parse_args()
    book = Path(args.book).resolve()
    if not book.is_dir():
        err(f"book dir not found: {book}")

    if args.delta_stdin:
        raw = sys.stdin.read()
    else:
        raw = Path(args.delta).read_text(encoding="utf-8")
    try:
        delta = json.loads(raw)
    except json.JSONDecodeError as e:
        err(f"delta not valid JSON: {e}")
        return 1

    verrs = validate_delta(delta)
    if verrs:
        print(json.dumps({"valid": False, "errors": verrs}, ensure_ascii=False), file=sys.stderr)
        return 1

    modified: list[str] = []
    warnings: list[str] = []

    state_dir = book / "story" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    if "currentStatePatch" in delta:
        cs_p = state_dir / "current_state.json"
        cur = load_json(cs_p, {"facts": []})
        if not isinstance(cur, dict):
            cur = {"facts": []}
        merge_dict(cur, delta["currentStatePatch"])
        write_json(cs_p, cur)
        modified.append(str(cs_p))

    if "hookOps" in delta:
        hp = state_dir / "hooks.json"
        cur = load_json(hp, {"hooks": []})
        if not isinstance(cur, dict):
            cur = {"hooks": []}
        new_obj = apply_hook_ops(cur, delta["hookOps"], warnings)
        write_json(hp, new_obj)
        modified.append(str(hp))

    if "chapterSummary" in delta:
        sp = state_dir / "chapter_summaries.json"
        cur = load_json(sp, {"summaries": []})
        if not isinstance(cur, dict):
            cur = {"summaries": []}
        cur.setdefault("summaries", []).append(delta["chapterSummary"])
        write_json(sp, cur)
        modified.append(str(sp))

    if "subplotOps" in delta:
        sb = book / "story" / "subplot_board.md"
        for op in delta["subplotOps"]:
            row = md_row([
                op.get("subplotId"), op.get("name"), op.get("status"),
                op.get("lastAdvancedChapter"), op.get("characters"), op.get("notes"),
            ])
            append_text(sb, row)
        modified.append(str(sb))

    if "emotionalArcOps" in delta:
        ea = book / "story" / "emotional_arcs.md"
        for op in delta["emotionalArcOps"]:
            row = md_row([
                op.get("character"), op.get("chapter"), op.get("emotionalState"),
                op.get("triggerEvent"), op.get("intensity"), op.get("arcDirection"),
            ])
            append_text(ea, row)
        modified.append(str(ea))

    if "characterMatrixOps" in delta:
        cm = book / "story" / "character_matrix.md"
        for op in delta["characterMatrixOps"]:
            row = md_row([
                op.get("charA"), op.get("charB"), op.get("relationship"),
                op.get("intimacy"), op.get("lastInteraction"), op.get("notes"),
            ])
            append_text(cm, row)
        modified.append(str(cm))

    if "notes" in delta:
        note_p = book / "story" / "runtime" / "settler-notes.log"
        notes = delta["notes"]
        if isinstance(notes, str):
            notes = [notes]
        for n in notes:
            append_text(note_p, str(n))
        modified.append(str(note_p))

    # ── hook governance gate ────────────────────────────────────────────
    # After writing all delta-derived state, run validate + stale-scan.
    # validate's critical findings block the commit (we already wrote files,
    # but caller is expected to inspect exit code before promoting the draft
    # to chapters/{NNNN}.md).  Warnings/info are surfaced but non-blocking.
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
            # Hard governance error: surface as warning but do not crash apply_delta.
            warnings.append(f"hook_governance validate failed: {validate_out.get('error')}")

    print(json.dumps({
        "applied": True,
        "filesModified": sorted(set(modified)),
        "warnings": warnings,
        "hookGovernance": governance_report,
        "hookGovernanceBlocked": governance_blocked,
    }, ensure_ascii=False, indent=2))
    return 1 if governance_blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
