#!/usr/bin/env python3
"""Doctor: SKILL environment + (optional) book health checklist.

Inkos's doctor focuses on Node / SQLite / API connectivity.  In SKILL form
none of that applies — Claude Code is the runtime.  We instead verify:

  1. Python >= 3.9 (we use `int | None` typing in our scripts)
  2. SKILL root layout (SKILL.md, references/, scripts/, templates/)
  3. Templates integrity (16 base files + 15 genre files)
  4. Each script accepts `--help` and exits 0 (smoke test)
  5. (with --book) book-level layout + manifest / hooks / chapter-name sanity

Each check is ok / warning / fail.  Exit 0 iff no `fail`.

Output: text by default, `{checks: [...], summary: {ok, warnings, fails}}` on
`--json`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parent.parent

# Single source of truth for the on-disk schema version.
sys.path.insert(0, str(SKILL_ROOT / "scripts"))
from _schema import SCHEMA_VERSION, STATE_FILES_WITH_VERSION  # noqa: E402
from _constants import CHAPTER_STATUS  # noqa: E402

# ---- expectation tables (kept in sync with templates/ + scripts/) ---------

EXPECTED_TEMPLATES = [
    "book.json",
    "inkos.json",
    "story/author_intent.md",
    "story/chapter_summaries.md",
    "story/character_matrix.md",
    "story/current_focus.md",
    "story/current_state.md",
    "story/emotional_arcs.md",
    "story/particle_ledger.md",
    "story/pending_hooks.md",
    "story/style_guide.md",
    "story/subplot_board.md",
    "story/state/chapter_summaries.json",
    "story/state/current_state.json",
    "story/state/hooks.json",
    "story/state/manifest.json",
]

EXPECTED_GENRES = [
    "cozy.md", "cultivation.md", "dungeon-core.md", "horror.md", "isekai.md",
    "litrpg.md", "other.md", "progression.md", "romantasy.md", "sci-fi.md",
    "system-apocalypse.md", "tower-climber.md", "urban.md", "xianxia.md",
    "xuanhuan.md",
]

EXPECTED_SCRIPTS = [
    "init_book.py",
    "book.py",
    "apply_delta.py",
    "hook_governance.py",
    "memory_retrieve.py",
    "consolidate_check.py",
    "settler_parse.py",
    "writer_parse.py",
    "post_write_validate.py",
    "word_count.py",
    "style_analyze.py",
    "ai_tell_scan.py",
    "sensitive_scan.py",
    "status.py",
    "doctor.py",
    "analytics.py",
    "fatigue_scan.py",
    "pov_filter.py",
    "split_chapter.py",
    "export_book.py",
    "recover_chapter.py",
    "cadence_check.py",
    "state_project.py",
    "hook_arbitrate.py",
    "context_filter.py",
    "narrative_control.py",
    "writing_methodology.py",
    "spot_fix_patches.py",
    "chapter_index.py",
    "snapshot_state.py",
    "audit_drift.py",
    "audit_round_log.py",
    "commitment_ledger.py",
    "genre.py",
    "context_budget.py",
    "book_lock.py",
    "e2e_test.py",
]

CHAPTER_NAME_OK = re.compile(r"^\d{4}(_[^/]*)?\.md$")
# Runtime-form file accidentally placed in chapters/. Final landed chapter
# bodies are NEVER named like this; this prefix only belongs in story/runtime/.
RUNTIME_FORM_IN_CHAPTERS = re.compile(r"^chapter-\d{4}(\.[^/]+)?\.md$")


# ---------- check primitive ------------------------------------------------

def make(name: str, status: str, detail: str) -> dict:
    assert status in ("ok", "warning", "fail")
    return {"name": name, "status": status, "detail": detail}


# ---------- environment checks ---------------------------------------------

def check_python() -> dict:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) < (3, 9):
        return make("Python >= 3.9", "fail",
                    f"got {detail}; SKILL scripts use 3.9+ typing")
    return make("Python >= 3.9", "ok", detail)


def check_skill_layout() -> dict:
    missing = []
    for rel in ("SKILL.md", "references", "scripts", "templates"):
        if not (SKILL_ROOT / rel).exists():
            missing.append(rel)
    if missing:
        return make("SKILL root layout", "fail",
                    f"missing: {', '.join(missing)} (root={SKILL_ROOT})")
    return make("SKILL root layout", "ok", str(SKILL_ROOT))


def check_templates() -> dict:
    tdir = SKILL_ROOT / "templates"
    missing_base = [r for r in EXPECTED_TEMPLATES
                    if not (tdir / r).is_file()]
    missing_genre = [g for g in EXPECTED_GENRES
                     if not (tdir / "genres" / g).is_file()]

    if missing_base or missing_genre:
        bits = []
        if missing_base:
            bits.append(f"templates: {', '.join(missing_base)}")
        if missing_genre:
            bits.append(f"genres: {', '.join(missing_genre)}")
        return make("Templates integrity", "fail", "; ".join(bits))
    return make("Templates integrity", "ok",
                f"{len(EXPECTED_TEMPLATES)} base + {len(EXPECTED_GENRES)} genres")


def check_scripts_help() -> list[dict]:
    out: list[dict] = []
    sdir = SKILL_ROOT / "scripts"
    for name in EXPECTED_SCRIPTS:
        path = sdir / name
        if not path.is_file():
            out.append(make(f"script {name}", "fail", f"missing: {path}"))
            continue
        try:
            res = subprocess.run(
                [sys.executable, str(path), "--help"],
                capture_output=True, text=True, timeout=8,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except Exception as e:  # noqa: BLE001
            out.append(make(f"script {name}", "fail", f"launch failed: {e!r}"))
            continue
        if res.returncode != 0:
            tail = (res.stderr or res.stdout or "").strip().splitlines()[-1:]
            tail_str = tail[0] if tail else ""
            out.append(make(f"script {name}", "fail",
                            f"--help exit={res.returncode}: {tail_str}"))
            continue
        out.append(make(f"script {name}", "ok", "--help OK"))
    return out


# ---------- book-level checks (only with --book) ---------------------------

def check_book(book_dir: Path) -> list[dict]:
    out: list[dict] = []
    if not book_dir.is_dir():
        out.append(make("book dir exists", "fail", f"not a dir: {book_dir}"))
        return out
    out.append(make("book dir exists", "ok", str(book_dir)))

    # subdirs / files
    expected_sub = ["story", "story/state", "chapters",
                    "story/runtime", "story/outline", "story/roles"]
    missing = [s for s in expected_sub if not (book_dir / s).is_dir()]
    if missing:
        out.append(make("book subdirs", "warning",
                        f"missing: {', '.join(missing)}"))
    else:
        out.append(make("book subdirs", "ok", "all present"))

    # book.json parses
    book = None
    bj = book_dir / "book.json"
    if not bj.is_file():
        out.append(make("book.json", "fail", "missing"))
    else:
        try:
            book = json.loads(bj.read_text(encoding="utf-8"))
            out.append(make("book.json", "ok", f"id={book.get('id')}"))
        except json.JSONDecodeError as e:
            out.append(make("book.json", "fail", f"invalid: {e}"))

    # inkos.json parses (look up nearest)
    cur = book_dir.resolve()
    inkos_path = None
    for p in [cur, *cur.parents]:
        cand = p / "inkos.json"
        if cand.is_file():
            inkos_path = cand
            break
    if inkos_path is None:
        out.append(make("inkos.json", "warning",
                        "not found in book or ancestors"))
    else:
        try:
            json.loads(inkos_path.read_text(encoding="utf-8"))
            out.append(make("inkos.json", "ok", str(inkos_path)))
        except json.JSONDecodeError as e:
            out.append(make("inkos.json", "fail", f"invalid: {e}"))

    # manifest #lastAppliedChapter <= count(chapters/*.md)
    state_dir = book_dir / "story" / "state"
    manifest_p = state_dir / "manifest.json"
    last_applied = 0
    if manifest_p.is_file():
        try:
            man = json.loads(manifest_p.read_text(encoding="utf-8"))
            last_applied = int(man.get("lastAppliedChapter", 0) or 0)
        except (json.JSONDecodeError, ValueError) as e:
            out.append(make("manifest.json", "fail", f"invalid: {e}"))
    else:
        out.append(make("manifest.json", "warning", "missing"))

    chap_dir = book_dir / "chapters"
    chap_files: list[Path] = []
    if chap_dir.is_dir():
        chap_files = [f for f in chap_dir.iterdir()
                      if f.is_file() and f.suffix == ".md"]

    bad_named = [f.name for f in chap_files if not CHAPTER_NAME_OK.match(f.name)]
    runtime_misplaced = [f.name for f in chap_files
                         if RUNTIME_FORM_IN_CHAPTERS.match(f.name)]
    if bad_named:
        out.append(make("chapter naming", "fail",
                        f"non-NNNN[_<title>].md: {', '.join(bad_named[:5])}"))
    else:
        out.append(make("chapter naming", "ok",
                        f"{len(chap_files)} files match NNNN[_<title>].md"))
    if runtime_misplaced:
        out.append(make("chapters/ no runtime sidecars", "fail",
                        "runtime-form file(s) in chapters/: "
                        f"{', '.join(runtime_misplaced[:5])} — "
                        "rename to NNNN[_<title>].md (chapter-NNNN.* belongs in story/runtime/)"))
    else:
        out.append(make("chapters/ no runtime sidecars", "ok",
                        "no chapter-NNNN.* files in chapters/"))

    # chapters/index.json schema sanity (operational index per
    # references/schemas/chapter-index.md). chapter_index.py validate is the
    # authoritative checker; doctor surfaces the most common drifts (legacy
    # field names from hand-written entries, off-enum status) so they get
    # caught even when the user never runs validate.
    index_p = chap_dir / "index.json"
    if not index_p.is_file():
        out.append(make("chapters/index.json", "warning",
                        "missing (will be created on first chapter_index.py add)"))
    else:
        try:
            idx_obj = json.loads(index_p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            out.append(make("chapters/index.json", "fail", f"invalid JSON: {e}"))
            idx_obj = None
        if idx_obj is not None:
            if not isinstance(idx_obj, list):
                out.append(make("chapters/index.json", "fail",
                                "top-level must be a JSON array of ChapterMeta"))
            else:
                legacy_field_hits: list[str] = []
                bad_status_hits: list[str] = []
                missing_required: list[str] = []
                for i, e in enumerate(idx_obj):
                    if not isinstance(e, dict):
                        missing_required.append(f"#{i}: not an object")
                        continue
                    if "chapter" in e and "number" not in e:
                        legacy_field_hits.append(f"#{i}: 'chapter' (use 'number')")
                    if "file" in e:
                        legacy_field_hits.append(f"#{i}: 'file' (not in schema)")
                    s = e.get("status")
                    if s is not None and s not in CHAPTER_STATUS:
                        bad_status_hits.append(f"#{i}: status={s!r}")
                    for req in ("number", "title", "status", "createdAt", "updatedAt"):
                        if req not in e:
                            missing_required.append(f"#{i}: missing {req}")
                            break
                problems: list[str] = []
                if legacy_field_hits:
                    problems.append("legacy fields → " + "; ".join(legacy_field_hits[:3]))
                if bad_status_hits:
                    problems.append("off-enum status → " + "; ".join(bad_status_hits[:3]))
                if missing_required:
                    problems.append("missing required → " + "; ".join(missing_required[:3]))
                if problems:
                    out.append(make("chapters/index.json", "fail",
                                    " | ".join(problems)
                                    + " — run chapter_index.py validate for full report"))
                else:
                    out.append(make("chapters/index.json", "ok",
                                    f"{len(idx_obj)} entries match ChapterMeta"))

    if last_applied > len(chap_files):
        out.append(make("manifest vs chapters", "fail",
                        f"lastAppliedChapter={last_applied} > files on disk={len(chap_files)}"))
    else:
        out.append(make("manifest vs chapters", "ok",
                        f"lastAppliedChapter={last_applied}, files={len(chap_files)}"))

    # hooks: no hook references chapter > lastApplied + 1
    hooks_p = state_dir / "hooks.json"
    if hooks_p.is_file():
        try:
            hooks_obj = json.loads(hooks_p.read_text(encoding="utf-8"))
            hooks = hooks_obj.get("hooks", []) if isinstance(hooks_obj, dict) else []
            cap = last_applied + 1
            offenders: list[str] = []
            for h in hooks:
                if not isinstance(h, dict):
                    continue
                start = int(h.get("startChapter", 0) or 0)
                if start > cap:
                    offenders.append(f"{h.get('hookId')}@{start}")
            if offenders:
                out.append(make("hooks chapter sanity", "warning",
                                f"hooks reference future chapters > {cap}: {', '.join(offenders[:5])}"))
            else:
                out.append(make("hooks chapter sanity", "ok",
                                f"{len(hooks)} hook(s), all within current+1"))
        except json.JSONDecodeError as e:
            out.append(make("hooks.json", "fail", f"invalid: {e}"))
    else:
        out.append(make("hooks.json", "warning", "missing"))

    # manifest schema version: only the manifest carries `schemaVersion` per
    # inkos's StateManifestSchema. Other state files are versioned implicitly
    # by the manifest in the same book.
    manifest_p = state_dir / "manifest.json"
    if manifest_p.is_file():
        try:
            md = json.loads(manifest_p.read_text(encoding="utf-8"))
            if not isinstance(md, dict):
                out.append(make("manifest schema version", "fail",
                                "manifest.json is not a JSON object"))
            else:
                cur = md.get("schemaVersion")
                if cur is None:
                    out.append(make("manifest schema version", "warning",
                                    f"missing (expected {SCHEMA_VERSION!r}; "
                                    "next apply_delta will auto-fill)"))
                elif cur == "1.0":
                    out.append(make("manifest schema version", "warning",
                                    f"legacy '1.0' (string); next write migrates to {SCHEMA_VERSION!r}; "
                                    "see references/schemas/migration-log.md"))
                elif cur != SCHEMA_VERSION:
                    out.append(make("manifest schema version", "warning",
                                    f"mismatch: file={cur!r} current={SCHEMA_VERSION!r}; "
                                    "see references/schemas/migration-log.md"))
                else:
                    out.append(make("manifest schema version", "ok",
                                    f"{cur} (matches inkos)"))
        except json.JSONDecodeError as e:
            out.append(make("manifest schema version", "fail", f"unreadable: {e}"))

    # Cross-validate non-manifest state files don't carry stray schemaVersion
    # (older SKILL books did; on next write they'll be stripped).
    stray = []
    for rel in ("story/state/hooks.json",
                "story/state/current_state.json",
                "story/state/chapter_summaries.json"):
        sp = book_dir / rel
        if sp.is_file():
            try:
                sd = json.loads(sp.read_text(encoding="utf-8"))
                if isinstance(sd, dict) and "schemaVersion" in sd:
                    stray.append(rel)
            except json.JSONDecodeError:
                pass  # other checks will report it
    if stray:
        out.append(make("state files inkos-clean", "warning",
                        f"stray schemaVersion in: {', '.join(stray)} "
                        "(legacy SKILL format; next apply_delta strips it)"))
    else:
        out.append(make("state files inkos-clean", "ok",
                        "no stray schemaVersion in non-manifest state files"))

    # genre profile resolves
    if book is not None:
        genre = book.get("genre")
        gp = SKILL_ROOT / "templates" / "genres" / f"{genre}.md"
        fb = SKILL_ROOT / "templates" / "genres" / "other.md"
        if gp.is_file():
            out.append(make("genre profile", "ok", f"{genre}.md"))
        elif fb.is_file():
            out.append(make("genre profile", "warning",
                            f"{genre}.md missing; will fall back to other.md"))
        else:
            out.append(make("genre profile", "fail",
                            f"{genre}.md and other.md both missing"))

    return out


# ---------- main -----------------------------------------------------------

def check_e2e_chain(timeout_sec: int = 30) -> dict:
    """Content-level smoke: invoke e2e_test.py to chain all 16 deterministic
    glue scripts against synthesized fixtures. Catches contract drift that
    --help can't see (e.g., field renamed in one writer but not its readers).

    Runs in a tempdir owned by e2e_test; fails iff any of the 16 steps fails.
    """
    e2e = SKILL_ROOT / "scripts" / "e2e_test.py"
    if not e2e.is_file():
        return make("e2e chain", "warning", "scripts/e2e_test.py missing")
    try:
        res = subprocess.run(
            [sys.executable, str(e2e), "--json"],
            capture_output=True, text=True, timeout=timeout_sec,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except subprocess.TimeoutExpired:
        return make("e2e chain", "fail",
                    f"timeout after {timeout_sec}s (chain hung)")
    except Exception as e:  # noqa: BLE001
        return make("e2e chain", "fail", f"launch failed: {e!r}")

    try:
        report = json.loads(res.stdout) if res.stdout.strip() else {}
    except json.JSONDecodeError as e:
        return make("e2e chain", "fail",
                    f"invalid JSON output: {e!r} (stderr: {res.stderr.strip()[:200]})")

    total = report.get("totalSteps", 0)
    passed = report.get("passed", 0)
    failed = report.get("failed", total - passed)
    elapsed = report.get("elapsedSeconds", "?")

    if total == 0:
        return make("e2e chain", "fail", "no steps reported by harness")
    if failed == 0:
        return make("e2e chain", "ok",
                    f"{passed}/{total} steps in {elapsed}s")

    # Failed: collect first-2 failure summaries so doctor user sees the cause.
    failed_summaries = [
        f"{s.get('name')}: {s.get('summary', '')[:60]}"
        for s in report.get("steps", [])
        if not s.get("ok")
    ][:2]
    detail = (f"{failed}/{total} failed in {elapsed}s"
              + (" — " + " | ".join(failed_summaries) if failed_summaries else ""))
    return make("e2e chain", "fail", detail)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SKILL environment + (optional) book health checklist.",
    )
    p.add_argument("--book", default=None,
                   help="optional book dir for book-level checks")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--skip-script-help", action="store_true",
                   help="skip the --help smoke test (faster)")
    p.add_argument("--skip-e2e", action="store_true",
                   help="skip the end-to-end chain check (faster; --help only)")
    p.add_argument("--e2e-timeout", type=int, default=30,
                   help="seconds to wait for e2e chain (default: 30)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    checks: list[dict] = []
    checks.append(check_python())
    checks.append(check_skill_layout())
    checks.append(check_templates())
    if not args.skip_script_help:
        checks.extend(check_scripts_help())
    if not args.skip_e2e:
        checks.append(check_e2e_chain(timeout_sec=args.e2e_timeout))

    if args.book:
        checks.extend(check_book(Path(args.book).resolve()))

    summary = {
        "ok": sum(1 for c in checks if c["status"] == "ok"),
        "warnings": sum(1 for c in checks if c["status"] == "warning"),
        "fails": sum(1 for c in checks if c["status"] == "fail"),
    }
    payload: dict[str, Any] = {"checks": checks, "summary": summary}

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        icon = {"ok": "[ok]  ", "warning": "[warn]", "fail": "[FAIL]"}
        for c in checks:
            print(f"{icon[c['status']]} {c['name']}: {c['detail']}")
        print()
        print(f"Summary: {summary['ok']} ok / "
              f"{summary['warnings']} warning / {summary['fails']} fail")
    return 0 if summary["fails"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
