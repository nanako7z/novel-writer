#!/usr/bin/env python3
"""
spot_fix_patches.py — apply structured spot-fix patches to a chapter draft.

Backend for phase 10 reviser `spot-fix` mode: when the LLM returns a list of
small structured patches (instead of a full rewrite), this script applies
them deterministically with line-anchored exact match (with a whitespace-
normalized fuzzy fallback ported from inkos `utils/spot-fix-patches.ts`).

Patches JSON shape:

    {
      "patches": [
        {"line": 42, "find": "原句精确匹配", "replace": "替换句", "reason": "..."},
        {"line": 87, "find": "another exact span", "replace": "rewritten", "reason": "..."}
      ]
    }

Per patch:
  - "line"   : 1-based line number where `find` is expected (anchor; window
               of ±2 lines accepted to tolerate minor drift).
  - "find"   : exact substring to locate. If unique on the anchor line/window
               we replace it; else (whitespace-only diff) we fall back to a
               whitespace-normalized fuzzy match within the window.
  - "replace": replacement string (may be multi-line; line count may change).
  - "reason" : optional human-readable rationale (logged, not used for matching).

Atomic write (`.tmp` + os.replace) when committing.

CLI:
    python spot_fix_patches.py --file <chapter.md> --patches <patches.json> \\
        [--out <patched.md>] [--dry-run] [--json] [--anchor-window N]

Exit codes:
    0  all patches applied (or --dry-run with all matchable)
    1  partial (some patches errored); patched output still written if not --dry-run
    2  fatal (file/JSON missing or invalid)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# ---- whitespace-normalized fuzzy matching (ported from inkos) ----

_WS_RE = re.compile(r"\s+")


def _normalize_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _map_normalized_to_original(original: str, normalized_pos: int) -> int:
    """Map a position in the whitespace-normalized text back to the original."""
    ni = 0
    in_ws = False

    # skip leading whitespace (matches .strip())
    oi = 0
    while oi < len(original) and original[oi].isspace():
        oi += 1

    while oi <= len(original) and ni < normalized_pos:
        if oi == len(original):
            break
        ch = original[oi]
        if ch.isspace():
            if not in_ws:
                ni += 1
                in_ws = True
        else:
            ni += 1
            in_ws = False
        oi += 1

    return oi if oi <= len(original) else -1


def _fuzzy_match_unique(content: str, target: str) -> Optional[Tuple[int, int]]:
    """Return (start, end) if a unique whitespace-normalized match exists."""
    norm_target = _normalize_ws(target)
    if len(norm_target) < 10:
        return None
    norm_content = _normalize_ws(content)
    start = norm_content.find(norm_target)
    if start == -1:
        return None
    if norm_content.find(norm_target, start + len(norm_target)) != -1:
        return None
    o_start = _map_normalized_to_original(content, start)
    o_end = _map_normalized_to_original(content, start + len(norm_target))
    if o_start == -1 or o_end == -1:
        return None
    return (o_start, o_end)


def _exact_match_unique(content: str, target: str) -> Optional[int]:
    start = content.find(target)
    if start == -1:
        return None
    if content.find(target, start + len(target)) != -1:
        return None
    return start


# ---- patch application ----


def _line_window_text(lines: List[str], line_no: int, window: int) -> Tuple[str, int, int]:
    """Return (joined_text, start_idx_inclusive, end_idx_exclusive) for the
    window around 1-based `line_no`. Indices are line indices (0-based)."""
    start = max(0, line_no - 1 - window)
    end = min(len(lines), line_no - 1 + window + 1)
    return ("\n".join(lines[start:end]), start, end)


def apply_patches(
    body: str,
    patches: List[dict],
    *,
    anchor_window: int = 2,
) -> Tuple[str, List[dict], List[dict]]:
    """Apply a list of patches to body. Returns (new_body, applied, errors).

    `applied` and `errors` are dicts mirroring the patch with extra fields
    {status, mode}. We process patches sequentially against the running body
    so later patches see earlier replacements.
    """
    applied: List[dict] = []
    errors: List[dict] = []
    current = body

    for idx, patch in enumerate(patches):
        if not isinstance(patch, dict):
            errors.append({
                "index": idx,
                "error": "patch is not an object",
                "patch": patch,
            })
            continue

        find = patch.get("find")
        replace = patch.get("replace")
        line_no = patch.get("line")

        if not isinstance(find, str) or not find:
            errors.append({
                "index": idx,
                "patch": patch,
                "error": "missing or empty 'find'",
            })
            continue
        if not isinstance(replace, str):
            errors.append({
                "index": idx,
                "patch": patch,
                "error": "missing 'replace' (must be string)",
            })
            continue

        # Strategy:
        # 1) If line provided: search inside ±anchor_window line window first.
        # 2) Else: search whole body.
        # In either case: exact unique match first, then fuzzy unique fallback
        # restricted to the same scope.

        if isinstance(line_no, int) and line_no >= 1:
            lines = current.split("\n")
            window_text, start_line_idx, end_line_idx = _line_window_text(
                lines, line_no, anchor_window
            )
            window_offset = sum(len(l) + 1 for l in lines[:start_line_idx])

            scope_text = window_text
            scope_offset = window_offset
        else:
            scope_text = current
            scope_offset = 0

        # 1. exact match in scope
        rel_start = _exact_match_unique(scope_text, find)
        match_mode = None
        match_span: Optional[Tuple[int, int]] = None
        if rel_start is not None:
            match_mode = "exact"
            match_span = (
                scope_offset + rel_start,
                scope_offset + rel_start + len(find),
            )
        else:
            fuzzy = _fuzzy_match_unique(scope_text, find)
            if fuzzy is not None:
                match_mode = "fuzzy"
                match_span = (
                    scope_offset + fuzzy[0],
                    scope_offset + fuzzy[1],
                )
            elif isinstance(line_no, int) and line_no >= 1:
                # 2. fallback: whole-body exact match (anchor was wrong but
                # text is still uniquely identifiable)
                whole = _exact_match_unique(current, find)
                if whole is not None:
                    match_mode = "exact-global"
                    match_span = (whole, whole + len(find))

        if match_span is None:
            errors.append({
                "index": idx,
                "patch": patch,
                "error": (
                    "could not locate `find` text"
                    + (
                        f" near line {line_no} (window=±{anchor_window})"
                        if isinstance(line_no, int)
                        else ""
                    )
                    + " — exact and fuzzy match both failed or non-unique"
                ),
            })
            continue

        s, e = match_span
        current = current[:s] + replace + current[e:]
        applied.append({
            "index": idx,
            "line": line_no,
            "mode": match_mode,
            "findLength": len(find),
            "replaceLength": len(replace),
            "reason": patch.get("reason"),
        })

    return current, applied, errors


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply structured spot-fix patches to a chapter draft."
    )
    parser.add_argument("--file", required=True, help="Chapter markdown file.")
    parser.add_argument("--patches", required=True, help="Patches JSON file.")
    parser.add_argument(
        "--out",
        help="Output path for patched body. Default: overwrite --file.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't write; just report what would change.",
    )
    parser.add_argument(
        "--anchor-window",
        type=int,
        default=2,
        help="Allowed line drift around anchor 'line' (default 2).",
    )
    parser.add_argument("--json", action="store_true", help="JSON summary on stdout.")
    args = parser.parse_args()

    src = Path(args.file)
    if not src.is_file():
        print(f"spot_fix_patches: --file not found: {src}", file=sys.stderr)
        return 2

    pjson = Path(args.patches)
    if not pjson.is_file():
        print(f"spot_fix_patches: --patches not found: {pjson}", file=sys.stderr)
        return 2

    try:
        patches_doc = json.loads(pjson.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"spot_fix_patches: invalid JSON in {pjson}: {exc}", file=sys.stderr)
        return 2

    if isinstance(patches_doc, list):
        patches = patches_doc
    elif isinstance(patches_doc, dict) and isinstance(patches_doc.get("patches"), list):
        patches = patches_doc["patches"]
    else:
        print(
            "spot_fix_patches: patches JSON must be a list or "
            "an object with 'patches' array.",
            file=sys.stderr,
        )
        return 2

    body = src.read_text(encoding="utf-8")
    new_body, applied, errors = apply_patches(
        body, patches, anchor_window=args.anchor_window
    )

    summary = {
        "file": str(src),
        "totalPatches": len(patches),
        "applied": len(applied),
        "skipped": len(errors),
        "dryRun": args.dry_run,
        "appliedDetails": applied,
        "errors": errors,
        "originalLength": len(body),
        "newLength": len(new_body),
    }

    out_path = Path(args.out) if args.out else src
    if not args.dry_run and len(applied) > 0:
        _atomic_write(out_path, new_body)
        summary["wrote"] = str(out_path)
    else:
        summary["wrote"] = None

    if args.json:
        sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    else:
        # human-readable
        sys.stdout.write(
            f"spot_fix_patches: {summary['applied']}/{summary['totalPatches']} applied "
            f"({summary['skipped']} skipped) "
            f"{'[dry-run]' if args.dry_run else ('-> ' + str(out_path))}\n"
        )
        for a in applied:
            sys.stdout.write(
                f"  [ok ] #{a['index']} line={a['line']} mode={a['mode']} "
                f"-{a['findLength']} +{a['replaceLength']}\n"
            )
        for e in errors:
            sys.stdout.write(f"  [err] #{e['index']}: {e['error']}\n")

    if errors and applied:
        return 1
    if errors and not applied:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
