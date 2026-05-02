#!/usr/bin/env python3
"""Audit Drift Guidance — Python port of inkos `persistAuditDriftGuidance`.

After chapter N's audit-revise loop closes, write the surviving
critical / warning issues to `books/<id>/story/audit_drift.md` so the
next chapter's Planner can read them and inject into the chapter_memo's
"避坑 / 不要做" section.  This file is short-lived: Planner consumes it
and clears it.

Source-of-truth file: `.inkos-src/pipeline/runner.ts#persistAuditDriftGuidance`.
The exact format inkos emits (and that we must reproduce verbatim, since
Composer / Planner pattern-match it):

    # 审计纠偏

    ## 审计纠偏（自动生成，下一章写作前参照）

    > 第{N}章审计发现以下问题，下一章写作时必须避免：
    > - [{severity}] {category}: {description}
    > - [{severity}] {category}: {description}
    ...

English variant uses `# Audit Drift` / `## Audit Drift Correction` /
`> Chapter {N} audit found the following issues to avoid in the next
chapter:`.

Empty issues list → delete the drift file (don't leave stale content).

Side effect (mirrors inkos): `current_state.md` is sanitized — any
inline "## 审计纠偏（自动生成，...)" or "## Audit Drift Correction"
block (or `# 审计纠偏` / `# Audit Drift`) is stripped.  Older versions
of the pipeline embedded the drift block into current_state, so the
strip is kept for backward compat.

CLI:
    python audit_drift.py --book <bookDir> write \\
        --chapter N --issues <issues.json> [--lang zh|en] [--json]
    python audit_drift.py --book <bookDir> clear [--json]
    python audit_drift.py --book <bookDir> read [--json]
    python audit_drift.py --book <bookDir> sanitize-current-state [--json]

issues.json shape:
    [{"severity": "critical|warning|info",
      "category": "...",
      "description": "..."}, ...]

write keeps only severity ∈ {critical, warning}; if the filtered list
is empty the drift file is removed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

DRIFT_HEADERS_TO_STRIP = (
    "## 审计纠偏（自动生成，下一章写作前参照）",
    "## Audit Drift Correction",
    "# 审计纠偏",
    "# Audit Drift",
)

KEEP_SEVERITIES = ("critical", "warning")


# ---------- IO helpers ---------------------------------------------------

def _story_dir(book_dir: Path) -> Path:
    return book_dir / "story"


def _drift_path(book_dir: Path) -> Path:
    return _story_dir(book_dir) / "audit_drift.md"


def _state_path(book_dir: Path) -> Path:
    return _story_dir(book_dir) / "current_state.md"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------- sanitize current_state ---------------------------------------

def strip_audit_drift_correction_block(text: str) -> str:
    """Mirror inkos `stripAuditDriftCorrectionBlock`.

    Find earliest occurrence among the four headers; if found, slice up
    to that index and rtrim trailing whitespace.  If no header → return
    text unchanged.
    """
    cut = -1
    for header in DRIFT_HEADERS_TO_STRIP:
        idx = text.find(header)
        if idx >= 0 and (cut < 0 or idx < cut):
            cut = idx
    if cut < 0:
        return text
    return text[:cut].rstrip()


def sanitize_current_state(book_dir: Path) -> dict[str, Any]:
    state_path = _state_path(book_dir)
    if not state_path.is_file():
        return {
            "ok": True,
            "changed": False,
            "reason": "current_state.md not present",
            "path": str(state_path),
        }
    original = state_path.read_text(encoding="utf-8")
    sanitized = strip_audit_drift_correction_block(original).rstrip()
    if sanitized == original.rstrip():
        return {
            "ok": True,
            "changed": False,
            "path": str(state_path),
        }
    _atomic_write(state_path, sanitized)
    return {
        "ok": True,
        "changed": True,
        "path": str(state_path),
        "originalLength": len(original),
        "newLength": len(sanitized),
    }


# ---------- write / clear / read -----------------------------------------

def _localize(lang: str, zh: str, en: str) -> str:
    return en if lang == "en" else zh


def _validate_issues(raw: Any) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    if not isinstance(raw, list):
        return [], ["issues must be a JSON array"]
    out: list[dict] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f"issues[{i}] is not an object")
            continue
        sev = item.get("severity")
        cat = item.get("category", "")
        desc = item.get("description", "")
        if sev not in ("critical", "warning", "info"):
            errors.append(
                f"issues[{i}].severity must be critical/warning/info, got {sev!r}"
            )
            continue
        out.append({
            "severity": sev,
            "category": str(cat or ""),
            "description": str(desc or ""),
        })
    return out, errors


def write_drift(
    book_dir: Path,
    chapter: int,
    issues: list[dict],
    lang: str,
) -> dict[str, Any]:
    # Always sanitize current_state.md first (mirror inkos: strip even
    # when issues are empty — older versions embedded the block inline).
    sanitize_result = sanitize_current_state(book_dir)

    drift_path = _drift_path(book_dir)
    drift_issues = [i for i in issues if i["severity"] in KEEP_SEVERITIES]

    if not drift_issues:
        deleted = False
        if drift_path.is_file():
            try:
                drift_path.unlink()
                deleted = True
            except OSError as e:
                return {
                    "ok": False,
                    "error": f"failed to delete drift file: {e}",
                    "path": str(drift_path),
                }
        return {
            "ok": True,
            "action": "cleared",
            "deleted": deleted,
            "path": str(drift_path),
            "keptIssues": 0,
            "totalIssues": len(issues),
            "currentStateSanitized": sanitize_result.get("changed", False),
        }

    title_line = _localize(lang, "# 审计纠偏", "# Audit Drift")
    section_line = _localize(
        lang,
        "## 审计纠偏（自动生成，下一章写作前参照）",
        "## Audit Drift Correction",
    )
    intro = _localize(
        lang,
        f"> 第{chapter}章审计发现以下问题，下一章写作时必须避免：",
        f"> Chapter {chapter} audit found the following issues to avoid in the next chapter:",
    )
    body_lines = [
        title_line,
        "",
        section_line,
        "",
        intro,
    ]
    for it in drift_issues:
        body_lines.append(
            f"> - [{it['severity']}] {it['category']}: {it['description']}"
        )
    body_lines.append("")
    content = "\n".join(body_lines)

    _atomic_write(drift_path, content)
    return {
        "ok": True,
        "action": "written",
        "path": str(drift_path),
        "chapter": chapter,
        "lang": lang,
        "keptIssues": len(drift_issues),
        "totalIssues": len(issues),
        "currentStateSanitized": sanitize_result.get("changed", False),
        "bytes": len(content.encode("utf-8")),
    }


def clear_drift(book_dir: Path) -> dict[str, Any]:
    drift_path = _drift_path(book_dir)
    if not drift_path.is_file():
        return {
            "ok": True,
            "action": "noop",
            "path": str(drift_path),
            "reason": "drift file not present",
        }
    try:
        drift_path.unlink()
    except OSError as e:
        return {
            "ok": False,
            "error": f"failed to delete drift file: {e}",
            "path": str(drift_path),
        }
    return {"ok": True, "action": "deleted", "path": str(drift_path)}


_ISSUE_LINE_RE = re.compile(r"^>\s*-\s*\[(?P<sev>[^\]]+)\]\s*(?P<rest>.*)$")
_INTRO_RE = re.compile(r"^>\s*第(\d+)章")
_INTRO_EN_RE = re.compile(r"^>\s*Chapter\s+(\d+)\b", re.IGNORECASE)


def read_drift(book_dir: Path) -> dict[str, Any]:
    """Parse audit_drift.md back into a structured issues array.

    Returns {"ok": True, "exists": bool, "chapter": int|None,
             "lang": "zh"|"en"|None, "issues": [...]}.
    """
    drift_path = _drift_path(book_dir)
    if not drift_path.is_file():
        return {
            "ok": True,
            "exists": False,
            "path": str(drift_path),
            "issues": [],
            "chapter": None,
            "lang": None,
        }
    text = drift_path.read_text(encoding="utf-8")
    chapter: int | None = None
    lang = "zh" if "审计纠偏" in text else ("en" if "Audit Drift" in text else None)

    issues: list[dict] = []
    for line in text.splitlines():
        if chapter is None:
            m = _INTRO_RE.match(line)
            if m:
                try:
                    chapter = int(m.group(1))
                except ValueError:
                    pass
            else:
                m = _INTRO_EN_RE.match(line)
                if m:
                    try:
                        chapter = int(m.group(1))
                    except ValueError:
                        pass
        m = _ISSUE_LINE_RE.match(line)
        if not m:
            continue
        sev = m.group("sev").strip()
        rest = m.group("rest").strip()
        # rest is "category: description"; tolerate missing colon
        if ":" in rest:
            cat, _, desc = rest.partition(":")
            issues.append({
                "severity": sev,
                "category": cat.strip(),
                "description": desc.strip(),
            })
        else:
            issues.append({
                "severity": sev,
                "category": "",
                "description": rest,
            })

    return {
        "ok": True,
        "exists": True,
        "path": str(drift_path),
        "chapter": chapter,
        "lang": lang,
        "issues": issues,
        "totalIssues": len(issues),
    }


# ---------- CLI glue -----------------------------------------------------

def cmd_write(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        return {"ok": False, "error": f"book directory not found: {book_dir}"}

    issues_path = Path(args.issues)
    if not issues_path.is_file():
        return {"ok": False, "error": f"issues file not found: {issues_path}"}
    try:
        raw = json.loads(issues_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"invalid JSON in {issues_path}: {e}"}

    issues, parse_errors = _validate_issues(raw)
    if parse_errors:
        return {"ok": False, "errors": parse_errors}

    return write_drift(book_dir, args.chapter, issues, args.lang or "zh")


def cmd_clear(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        return {"ok": False, "error": f"book directory not found: {book_dir}"}
    return clear_drift(book_dir)


def cmd_read(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        return {"ok": False, "error": f"book directory not found: {book_dir}"}
    return read_drift(book_dir)


def cmd_sanitize(args: argparse.Namespace) -> dict:
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        return {"ok": False, "error": f"book directory not found: {book_dir}"}
    return sanitize_current_state(book_dir)


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="audit_drift.py",
        description=(
            "Manage books/<id>/story/audit_drift.md (post-audit drift "
            "guidance for the next chapter's Planner)."
        ),
    )
    ap.add_argument("--book", required=True, help="path to book directory")
    ap.add_argument("--json", action="store_true", help="JSON output (default for write/read/clear)")
    sub = ap.add_subparsers(dest="command", required=True)

    p_w = sub.add_parser("write", help="write drift guidance from audit issues")
    p_w.add_argument("--chapter", type=int, required=True)
    p_w.add_argument("--issues", required=True,
                     help="path to issues.json (array of {severity,category,description})")
    p_w.add_argument("--lang", choices=("zh", "en"), default="zh")

    sub.add_parser("clear", help="delete the drift file (Planner calls after consuming)")
    sub.add_parser("read", help="parse drift file back to issues array")
    sub.add_parser(
        "sanitize-current-state",
        help="strip embedded drift headers from current_state.md (no audit input)",
    )

    args = ap.parse_args()

    handlers = {
        "write": cmd_write,
        "clear": cmd_clear,
        "read": cmd_read,
        "sanitize-current-state": cmd_sanitize,
    }
    result = handlers[args.command](args)

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result.get("ok"):
        sys.exit(2)


if __name__ == "__main__":
    main()
