#!/usr/bin/env python3
"""Chapter state recovery (resume after partial / crashed write-next-chapter run).

Stdlib-only. Detects partial runtime artifacts left in `story/runtime/` from an
interrupted write-next-chapter pass (Writer crash, network error, user
interrupt) and tells the caller which phase to resume from.

The pipeline phase order this script knows about:
    intent -> context -> draft -> normalized -> audit -> delta -> polish ->
    final -> analysis

`final` here means the chapter body has been promoted to `chapters/{NNNN}.md`
AND `manifest.json#lastAppliedChapter` was advanced. If the runtime artifacts
exist for chapter N+1 but `lastAppliedChapter` is still N, we are clearly
mid-pipeline.

Usage:
    python recover_chapter.py --book <bookDir> [--clean] [--json]

Output (always JSON when --json, else text + same JSON tail):
    {
      "nextChapter": 7,
      "latestPhase": "delta",
      "presentArtifacts": ["intent.md", ...],
      "missingArtifacts": ["polish.json", "analysis.json"],
      "recommendedAction": "resume from polish",
      "stalenessWarnings": ["draft.md is older than 7 days"]
    }

Exit codes:
    0 — diagnosis successful (regardless of what was found)
    1 — usage / IO error
    2 — `--clean` aborted (user declined confirmation)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _chapter_files import find_chapter_file  # noqa: E402

# Phase order, from earliest to latest. Each entry is (phase, artifact_filename,
# whether-the-artifact-being-present-implies-this-phase-completed). All entries
# default to True; we keep the tuple shape for symmetry.
PHASE_ARTIFACTS: list[tuple[str, str]] = [
    ("intent", "intent.md"),
    ("context", "context.json"),
    ("draft", "draft.md"),
    ("normalized", "normalized.md"),
    ("audit", "audit.json"),
    ("delta", "delta.json"),
    ("polish", "polish.json"),
    ("analysis", "analysis.json"),
]

# Optional / supplementary artifacts (presence does not move the latestPhase
# pointer but we still report them). rule-stack and trace are produced by
# Composer alongside context.json; we list them as "context-aux".
AUX_ARTIFACTS: list[str] = [
    "rule-stack.json",
    "trace.json",
]

STALENESS_DAYS = 7


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def chapter_runtime_files(runtime_dir: Path, chapter_no: int) -> dict[str, Path]:
    """Return {artifact_filename: Path} for every chapter-NNNN.* file present."""
    prefix = f"chapter-{chapter_no:04d}."
    out: dict[str, Path] = {}
    if not runtime_dir.is_dir():
        return out
    for entry in runtime_dir.iterdir():
        if not entry.is_file():
            continue
        if not entry.name.startswith(prefix):
            continue
        suffix = entry.name[len(prefix):]
        out[suffix] = entry
    return out


def detect_latest_phase(present: dict[str, Path]) -> str:
    """Return the latest fully-completed phase based on which files exist."""
    latest = "none"
    for phase, fname in PHASE_ARTIFACTS:
        if fname in present:
            latest = phase
    return latest


def chapter_finalized(book_dir: Path, chapter_no: int) -> bool:
    """Has chapters/{NNNN}[_<title>].md landed AND manifest been advanced past N-1?"""
    if find_chapter_file(book_dir, chapter_no) is None:
        return False
    manifest = load_json(book_dir / "story" / "state" / "manifest.json", {})
    last_applied = int(manifest.get("lastAppliedChapter", 0) or 0)
    return last_applied >= chapter_no


def recommended_action(latest_phase: str, finalized: bool) -> str:
    if finalized:
        # Manifest already shows this chapter; nothing to resume.
        return "no resume needed (chapter already finalized; manifest advanced)"
    mapping = {
        "none": "start from Plan (no runtime artifacts present)",
        "intent": "resume from Compose",
        "context": "resume from Write",
        "draft": "resume from Normalize",
        "normalized": "resume from Audit-Revise",
        "audit": "resume from Settle (Observe + delta)",
        "delta": "resume from Polish (or final-write if audit borderline)",
        "polish": "resume from final-write (chapters/NNNN.md + manifest update)",
        "analysis": "resume from final-write (analysis came after persist; verify manifest)",
    }
    return mapping.get(latest_phase, f"resume from {latest_phase}")


def staleness_warnings(present: dict[str, Path]) -> list[str]:
    warnings: list[str] = []
    now = time.time()
    cutoff = STALENESS_DAYS * 86400
    for fname, path in sorted(present.items()):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        age_days = (now - mtime) / 86400
        if (now - mtime) > cutoff:
            warnings.append(
                f"{fname} is {age_days:.1f} days old (> {STALENESS_DAYS}d) — likely stale"
            )
    return warnings


def diagnose(book_dir: Path) -> dict[str, Any]:
    manifest = load_json(book_dir / "story" / "state" / "manifest.json", {})
    last_applied = int(manifest.get("lastAppliedChapter", 0) or 0)
    next_chapter = last_applied + 1

    runtime_dir = book_dir / "story" / "runtime"
    present = chapter_runtime_files(runtime_dir, next_chapter)

    expected: list[str] = [fname for _, fname in PHASE_ARTIFACTS]
    present_names = sorted(present.keys())
    missing: list[str] = [name for name in expected if name not in present]

    latest = detect_latest_phase(present)
    finalized = chapter_finalized(book_dir, next_chapter)

    aux_present = [name for name in AUX_ARTIFACTS if name in present]

    return {
        "nextChapter": next_chapter,
        "latestPhase": latest,
        "chapterFinalized": finalized,
        "presentArtifacts": present_names,
        "auxiliaryArtifacts": aux_present,
        "missingArtifacts": missing,
        "recommendedAction": recommended_action(latest, finalized),
        "stalenessWarnings": staleness_warnings(present),
    }


def clean_runtime(book_dir: Path, chapter_no: int, present: dict[str, Path]) -> list[str]:
    deleted: list[str] = []
    for fname, path in present.items():
        try:
            path.unlink()
            deleted.append(fname)
        except OSError as exc:
            print(f"warn: failed to delete {path}: {exc}", file=sys.stderr)
    return deleted


def confirm_clean(next_chapter: int, present_names: list[str]) -> bool:
    if not present_names:
        print(f"No runtime artifacts to clean for chapter {next_chapter}.")
        return False
    print(
        f"About to delete {len(present_names)} runtime file(s) for chapter "
        f"{next_chapter}: {', '.join(present_names)}"
    )
    try:
        ans = input("Proceed? [y/N]: ").strip().lower()
    except EOFError:
        ans = ""
    return ans in {"y", "yes"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Detect partial chapter runtime state and recommend a resume point.",
    )
    p.add_argument("--book", required=True, help="book directory (containing story/)")
    p.add_argument(
        "--clean",
        action="store_true",
        help="delete all runtime artifacts for the next chapter (asks confirmation unless --json)",
    )
    p.add_argument("--json", action="store_true", help="emit pure JSON (no prose, no confirmation)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    book_dir = Path(args.book).resolve()
    if not book_dir.is_dir():
        msg = {"error": f"book dir not found: {book_dir}"}
        print(json.dumps(msg, ensure_ascii=False), file=sys.stderr)
        return 1

    diag = diagnose(book_dir)

    if args.clean:
        next_ch = diag["nextChapter"]
        present_names = diag["presentArtifacts"] + diag["auxiliaryArtifacts"]
        runtime_dir = book_dir / "story" / "runtime"
        present_map = chapter_runtime_files(runtime_dir, next_ch)
        if not args.json:
            if not confirm_clean(next_ch, present_names):
                print(json.dumps({**diag, "cleanedArtifacts": [], "cleanAborted": True},
                                 ensure_ascii=False, indent=2))
                return 2
        deleted = clean_runtime(book_dir, next_ch, present_map)
        diag["cleanedArtifacts"] = deleted
        # Re-diagnose after deletion so output reflects the cleaned state.
        post = diagnose(book_dir)
        post["cleanedArtifacts"] = deleted
        diag = post

    if args.json:
        print(json.dumps(diag, ensure_ascii=False, indent=2))
    else:
        print(f"Next chapter to write: {diag['nextChapter']}")
        print(f"Latest completed phase: {diag['latestPhase']}")
        if diag["chapterFinalized"]:
            print("Chapter is already finalized (manifest advanced + chapters/*.md exists).")
        else:
            print(f"Recommended action: {diag['recommendedAction']}")
        if diag["presentArtifacts"]:
            print(f"Present runtime artifacts: {', '.join(diag['presentArtifacts'])}")
        if diag["auxiliaryArtifacts"]:
            print(f"Auxiliary (Composer) artifacts: {', '.join(diag['auxiliaryArtifacts'])}")
        if diag["missingArtifacts"]:
            print(f"Missing pipeline artifacts: {', '.join(diag['missingArtifacts'])}")
        if diag["stalenessWarnings"]:
            print("Staleness warnings:")
            for w in diag["stalenessWarnings"]:
                print(f"  - {w}")
        if "cleanedArtifacts" in diag:
            print(f"Cleaned: {', '.join(diag['cleanedArtifacts']) or '(nothing)'}")
        # Always tail with JSON so callers (Claude) can parse easily.
        print()
        print(json.dumps(diag, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
