#!/usr/bin/env python3
"""Commitment Ledger validator (Python port of inkos hook-ledger-validator.ts).

The Planner attaches a `## 本章 hook 账` (or `## Hook ledger for this chapter`)
block to chapter_memo with four subsections:

  open:     newly seeded hooks (no id yet)
  advance:  H001 "name" → state-change description
  resolve:  H007 "name" → resolving action
  defer:    H012 "name" → reason for deferring

For every entry under `advance` and `resolve` we extract the descriptor
(everything after the hook id) and verify the chapter draft contains at least
one keyword pulled from it. If the descriptor mentions "断剑之约 → 主角归还
断剑" but the draft doesn't echo any of {断剑, 之约, 归还} — the planner
made a promise and the writer didn't keep it. That is a critical violation.

We do **not** validate `defer` (deliberately untouched) or `open` (new hooks
have no pre-existing descriptor to echo). Placeholder rows like "- 无" /
"- none" / "- tbd" under empty subsections are tolerated.

If a hook has `committedToChapter` (or legacy `committedPayoffChapter`) on
its hooks.json record AND that chapter equals the current chapter being
validated, we ALSO require evidence — even if the planner didn't list it in
the ledger that round. This is the stricter form of forward-looking
commitment described in references/hook-governance.md §8d.

CLI:
  python commitment_ledger.py --memo <chapter_memo.md> --draft <draft.md>
                              [--hooks <hooks.json>] [--chapter N] [--json]

Exit codes:
  0  no violations
  2  one or more critical violations
  3  bad input (missing files / empty ledger AND --strict-empty)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ----------------------------- patterns ------------------------------------

LEDGER_HEADING_PATTERNS = [
    re.compile(r"^#{2,3}\s*本章\s*hook\s*账\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^#{2,3}\s*Hook\s+ledger\s+for\s+this\s+chapter\s*$",
               re.IGNORECASE | re.MULTILINE),
]

SUBSECTION_KEYS = ("open", "advance", "resolve", "defer")
# Accept either plain `advance:` form (inkos default) or `### advance:` /
# `#### advance:` (markdown h3/h4 form some planners produce).
SUBHEADING_RE = re.compile(
    r"^#{0,4}\s*(open|advance|resolve|defer)\s*[:：]?\s*$",
    re.IGNORECASE,
)

# Tokens meaning "no entry in this slot" — tolerated, do not parse as hook id.
PLACEHOLDER_RE = re.compile(
    r"^(无|空|none|nil|null|暂无|n\/a|na|n-a|tbd|todo|待定)$",
    re.IGNORECASE,
)

# Words that look like hook ids but are subsection labels.
SUBSECTION_WORD_RE = re.compile(r"^(open|advance|resolve|defer|new)$",
                                re.IGNORECASE)

# Strip "[new]" prefix used in `open:` rows.
NEW_TAG_RE = re.compile(r"^\[new\]\s*", re.IGNORECASE)

# ID candidate: leading ASCII or CJK, plus internal alnum/underscore/dash/CJK.
ID_RE = re.compile(r"^([A-Za-z一-鿿][A-Za-z0-9_\-一-鿿]{0,19})")

# Quoted name in descriptor: "..." or "..." (curly Chinese quotes).
QUOTED_RE = re.compile(r"[“”\"]([^“”\"\n]+)[“”\"]")

CJK_RUN_RE = re.compile(r"[一-鿿]{2,}")
ASCII_WORD_RE = re.compile(r"[A-Za-z]{3,}")

ASCII_STOPWORDS = {
    "and", "the", "for", "with", "from", "that", "into", "then",
    "open", "close", "advance", "resolve", "defer", "new",
    "planted", "pressured", "near", "payoff", "ready", "stale",
}

ARROW_SPLIT_RE = re.compile(r"[→]|->")


# ----------------------------- io helpers ----------------------------------

def hard_err(msg: str, code: int = 3) -> "None":
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False),
          file=sys.stderr)
    sys.exit(code)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        hard_err(f"file not found: {path}")
    except OSError as e:
        hard_err(f"read failed for {path}: {e}")
    return ""  # unreachable


# ----------------------------- ledger parser -------------------------------

def extract_ledger_section(memo_body: str) -> str | None:
    """Return the contents of the `## 本章 hook 账` section, or None.

    The section ends at the *next h2* heading (`## …`), NOT at the next
    h3 — `### advance:` style subsections are legitimate ledger interior.
    If the planner uses h3 for a sibling section (rare; planner spec is
    h2-only), it gets included; that is preferred to dropping the entire
    advance/resolve list.
    """
    for pat in LEDGER_HEADING_PATTERNS:
        m = pat.search(memo_body)
        if not m:
            continue
        start = m.end()
        rest = memo_body[start:]
        # End at the next h2 heading specifically (## but not ###).
        nxt = re.search(r"\n##(?!#)\s", rest)
        end = nxt.start() if nxt else len(rest)
        return rest[:end]
    return None


def extract_keywords(descriptor: str) -> list[str]:
    """Pick search tokens out of a ledger descriptor.

    Priority 1: quoted hook name — most informative, what writer should echo.
    Priority 2: descriptor text up to the first state-transition arrow
                (→ or ->). Anything after arrow describes the new state, not
                the hook itself, so we ignore it (avoids picking up character
                names that appear elsewhere).
    """
    if not descriptor:
        return []
    qm = QUOTED_RE.search(descriptor)
    if qm:
        source = qm.group(1)
    else:
        # split on the first arrow; fall back to whole descriptor
        parts = ARROW_SPLIT_RE.split(descriptor, maxsplit=1)
        source = parts[0] if parts else descriptor

    tokens: list[str] = []
    for run in CJK_RUN_RE.findall(source):
        tokens.append(run)
        if len(run) >= 4:
            tokens.append(run[:2])
            tokens.append(run[-2:])
    for word in ASCII_WORD_RE.findall(source):
        w = word.lower()
        if w not in ASCII_STOPWORDS:
            tokens.append(w)

    seen: list[str] = []
    seen_set: set[str] = set()
    for t in tokens:
        if t in seen_set:
            continue
        seen_set.add(t)
        seen.append(t)
    return seen


def extract_ledger_entry(line: str) -> dict | None:
    cleaned = re.sub(r"^-+\s*", "", line).strip()
    if not cleaned:
        return None
    cleaned = NEW_TAG_RE.sub("", cleaned)
    if not cleaned:
        return None
    first_word = cleaned.split()[0] if cleaned.split() else ""
    if PLACEHOLDER_RE.match(first_word):
        return None
    m = ID_RE.match(cleaned)
    if not m:
        return None
    candidate = m.group(1)
    if SUBSECTION_WORD_RE.match(candidate):
        return None
    if PLACEHOLDER_RE.match(candidate):
        return None
    descriptor = cleaned[len(candidate):].strip()
    return {
        "hookId": candidate,
        "descriptor": descriptor,
        "keywords": extract_keywords(descriptor),
    }


def parse_hook_ledger(memo_body: str) -> dict:
    section = extract_ledger_section(memo_body)
    out = {k: [] for k in SUBSECTION_KEYS}
    if not section:
        return out
    current: str | None = None
    for raw in section.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        sm = SUBHEADING_RE.match(stripped)
        if sm:
            current = sm.group(1).lower()
            continue
        if not current:
            continue
        if not stripped.startswith("-"):
            continue
        entry = extract_ledger_entry(stripped)
        if entry:
            out[current].append(entry)
    return out


# ----------------------------- evidence check ------------------------------

def draft_echoes_entry(draft: str, entry: dict) -> bool:
    keywords = entry.get("keywords") or []
    if keywords:
        draft_lower = draft.lower()
        for kw in keywords:
            if not kw:
                continue
            # ASCII keywords were lowercased at extract time; CJK tokens are
            # case-insensitive trivially.
            if re.match(r"^[a-z]", kw):
                if kw in draft_lower:
                    return True
            else:
                if kw in draft:
                    return True
        return False
    # No descriptor at all — fall back to literal id match.
    hid = entry.get("hookId", "")
    if not hid:
        return False
    if re.fullmatch(r"[A-Za-z0-9_\-]+", hid):
        return re.search(rf"\b{re.escape(hid)}\b", draft) is not None
    return hid in draft


def dedupe_by_id(entries: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for e in entries:
        hid = e.get("hookId")
        if not hid or hid in seen:
            continue
        seen.add(hid)
        out.append(e)
    return out


# ------------------------- hook-record commitment --------------------------

def load_committed_for_chapter(hooks_path: Path | None,
                               chapter: int | None) -> list[dict]:
    """Return hook records committed (committedToChapter / legacy
    committedPayoffChapter) to the given chapter, sourced from hooks.json.

    Empty list if no hooks file or no chapter context.
    """
    if hooks_path is None or chapter is None:
        return []
    if not hooks_path.exists():
        return []
    try:
        obj = json.loads(hooks_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    hooks = []
    if isinstance(obj, dict):
        hooks = obj.get("hooks") or []
    if not isinstance(hooks, list):
        return []
    out: list[dict] = []
    for h in hooks:
        if not isinstance(h, dict):
            continue
        cm = h.get("committedToChapter")
        if not isinstance(cm, int):
            cm = h.get("committedPayoffChapter")
        if not isinstance(cm, int):
            continue
        if cm != chapter:
            continue
        status = (h.get("status") or "").strip().lower()
        if status in ("resolved", "closed", "done", "已回收", "已解决"):
            continue
        out.append(h)
    return out


def hook_to_entry(hook: dict) -> dict:
    """Synthesize a ledger-entry-like object from a hook record so we can
    reuse extract_keywords + draft_echoes_entry for committedToChapter
    enforcement.
    """
    parts = []
    for key in ("expectedPayoff", "notes", "type"):
        v = hook.get(key)
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    descriptor = " / ".join(parts)
    return {
        "hookId": hook.get("hookId") or "",
        "descriptor": descriptor,
        "keywords": extract_keywords(descriptor),
    }


# --------------------------------- main ------------------------------------

def validate(memo_text: str, draft_text: str,
             committed_hooks: list[dict] | None = None) -> dict:
    ledger = parse_hook_ledger(memo_text)
    committed = dedupe_by_id(list(ledger["advance"]) + list(ledger["resolve"]))

    violations: list[dict] = []
    for entry in committed:
        if not draft_echoes_entry(draft_text, entry):
            violations.append({
                "severity": "critical",
                "category": "hook 账未兑现",
                "hookId": entry["hookId"],
                "description": (
                    f"memo 在 advance/resolve 里声明要处理 "
                    f"{entry['hookId']}，但正文没有对应的落地动作"
                ),
                "suggestion": (
                    f"在正文中加入对 {entry['hookId']} 的具体情节推进"
                    f"（动作、对话、环境变化），或把它从 hook 账里"
                    f"移到 defer 并给出理由"
                ),
                "keywords": entry.get("keywords", []),
            })

    # committedToChapter enforcement: hook records that named *this* chapter
    # as their payoff also need observable evidence in the draft, even if the
    # planner forgot to list them in the ledger.
    if committed_hooks:
        ledger_ids = {e["hookId"] for e in committed}
        for hook in committed_hooks:
            entry = hook_to_entry(hook)
            if not entry["hookId"]:
                continue
            if entry["hookId"] in ledger_ids:
                continue  # already covered by ledger check
            if not draft_echoes_entry(draft_text, entry):
                violations.append({
                    "severity": "critical",
                    "category": "committedToChapter 未兑现",
                    "hookId": entry["hookId"],
                    "description": (
                        f"hook {entry['hookId']} 的 committedToChapter "
                        f"指向本章，但 memo 未在 hook 账里登记，"
                        f"正文也没有对应落地"
                    ),
                    "suggestion": (
                        f"要么在 memo 的 advance/resolve 里登记并写出"
                        f"具体动作，要么把 committedToChapter 改到后续"
                        f"章节"
                    ),
                    "keywords": entry.get("keywords", []),
                })

    summary = (
        f"advance={len(ledger['advance'])} "
        f"resolve={len(ledger['resolve'])} "
        f"defer={len(ledger['defer'])} "
        f"open={len(ledger['open'])} "
        f"violations={len(violations)}"
    )
    return {
        "ok": len(violations) == 0,
        "ledger": ledger,
        "violations": violations,
        "summary": summary,
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Commitment Ledger validator — verify that the chapter draft "
            "actually delivers on every hook the planner committed under "
            "## 本章 hook 账 (advance / resolve)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit 0 = no violations; 2 = critical violations; 3 = bad input.\n"
            "Examples:\n"
            "  commitment_ledger.py --memo memo.md --draft draft.md --json\n"
            "  commitment_ledger.py --memo memo.md --draft draft.md "
            "--hooks book/story/state/hooks.json --chapter 12\n"
        ),
    )
    p.add_argument("--memo", required=True,
                   help="path to chapter_memo.md (or chapter-NNNN.intent.md)")
    p.add_argument("--draft", required=True,
                   help="path to chapter draft (post-Writer / post-Normalizer)")
    p.add_argument("--hooks", default=None,
                   help="optional hooks.json path; enables committedToChapter "
                        "enforcement when paired with --chapter")
    p.add_argument("--chapter", type=int, default=None,
                   help="current chapter number (required to enforce "
                        "committedToChapter)")
    p.add_argument("--json", action="store_true",
                   help="output JSON (default; flag kept for symmetry)")
    p.add_argument("--strict-empty", action="store_true",
                   help="treat 'no ledger section in memo' as exit 3")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    memo_path = Path(args.memo)
    draft_path = Path(args.draft)
    hooks_path = Path(args.hooks) if args.hooks else None

    memo_text = read_text(memo_path)
    draft_text = read_text(draft_path)

    committed = load_committed_for_chapter(hooks_path, args.chapter)
    result: dict[str, Any] = validate(memo_text, draft_text, committed)

    if args.strict_empty:
        ledger = result["ledger"]
        if (not ledger["advance"] and not ledger["resolve"]
                and not ledger["defer"] and not ledger["open"]):
            hard_err(
                "memo has no `## 本章 hook 账` section (or all four "
                "subsections are empty); --strict-empty refuses to proceed",
                code=3,
            )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if not result["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
