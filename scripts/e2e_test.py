#!/usr/bin/env python3
"""End-to-end deterministic-glue test harness for the novel-writer SKILL.

Synthesizes a fake Writer output + audit + settler delta and walks the
deterministic chain (parse → validate → length → ai-tells → sensitive →
audit-round-log → apply-delta → chapter-index → snapshot → audit-drift →
commitment-ledger → analytics → status → consolidate-check → cadence-check
→ memory-retrieve), asserting each step's exit code and the shape of its
output JSON. No LLM is invoked.

Run:
    python3 scripts/e2e_test.py            # human + machine summary, exit 0/1
    python3 scripts/e2e_test.py --json     # structured JSON only
    python3 scripts/e2e_test.py --keep-tmp # keep tempdir for inspection
    python3 scripts/e2e_test.py --verbose  # echo per-step stdout/stderr
    python3 scripts/e2e_test.py --step writer_parse   # only run one step

Exit codes:
    0  all steps passed
    1  one or more steps failed (check stdout for diagnosis)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent

# ───────────────────────── synthesized fixtures ──────────────────────────

# A clean ~2500-char Chinese body. NO half-width quotes around CJK; NO
# "不是…而是…"; NO "——"; healthy paragraph shape (>= 4 paragraphs, dialogue
# uses curly quotes "…"). Hook H001 keyword "断剑" is echoed in the body so
# the commitment_ledger check passes.
SYNTH_BODY = """\
山雨忽至，他立在檐下。雨脚把青石板打湿，反出冷青的光。少年握紧手里那柄断剑，剑身只剩半截，缺口边缘还留着旧血的暗痕。他低头看，看了很久。

“你真的要去？”身后有人问。声音被雨水切成几节，落进他耳里时已经发凉。

少年没有回头。屋檐下的雨帘像一层薄薄的纱，把人和外面的世界隔开。他知道一旦走出去，这层纱就会被撕碎。撕碎之后，他要面对的，是早就料到却始终没敢直视的那个真相。

“嗯。”他只回了一个字。

那人沉默了一会儿，叹了口气，把一件旧蓑衣递到他肩上。蓑衣有些重，夹杂着久年不散的草木气和淡淡的霉味。少年下意识地缩了一下肩，但还是接住了。

“路上小心。”那人说。

“我知道。”少年说。

雨势更大了。他向前迈出一步，脚下的青石板发出一声轻响，像是替他答了一句"知道"。檐外的雨直接砸在他蓑衣上，密密的，急急的，让他几乎要弯下腰去。

他没有弯。他想起师父临终前说的那句话："剑断了，人不能断。"那时他还小，听不懂这句话的份量。如今他懂了。懂了之后，他反而更不愿意把断剑放下。

他沿着青石板路一直往南走。雨越下越大，把整个山坳都罩进了一层灰白的水雾里。山道上没有别人，只有他一个人和那柄断剑。剑在他手里轻轻颤着，像是在催他快一点。

走出三里地，他停下来，回头看了一眼。来时的小村已经被雨雾吞没，看不清屋脊。他怔了片刻，把蓑衣的帽兜拉得更低一些，然后继续向前。

到了岔路口，他犹豫了。左边的路通向城里，右边的路通向更深的山。城里有他要找的人；山里有他要躲的人。他握着断剑站了一会儿，最后选择了右边。

"如果我先把山里的事了结，"他在心里对自己说，"再去城里也不迟。"

雨把他的视线拍得模糊。他抬手抹了一把脸，掌心却没有干。山道开始陡峭，他不得不一手扶着崖壁前行。崖壁上长满了湿滑的青苔，每走一步都要小心。他没有抱怨，也没有停下，只是机械地一步一步向前。

走了不知多久，他听见前方传来低低的水声。那是一条山涧。山涧的水被雨催得涨了，浑浊地冲下崖底，发出闷闷的轰响。他顺着声音找过去，在涧边一块大石上看见了一个人。

那人背对着他坐着，身上披着一件墨色斗篷。少年的脚步声不大，但那人还是听见了。他没有回头，只是缓缓抬起手，做了一个让少年靠近的手势。

少年握紧断剑，慢慢走过去。每走一步，他都能听见自己心跳的声音。雨水顺着他的发梢往下滴，滴在断剑的剑身上，发出细微的、几乎听不见的"叮"。

到了那人身后三步远，他停下，开口："你等我很久了？"

那人这才回过头来，露出半张脸。脸上有一道极旧的疤，从眉骨划到颧骨，疤痕已经发白，看起来已经很多年了。他笑了一下，那笑很淡，像雨里的一点光。

"等得不算久，"他说，"比你师父等的短一些。"

少年的手指在断剑的柄上紧了一紧。他听懂了这句话的意思。听懂之后，他没有动，也没有说话。雨打在他蓑衣上，沙沙地响。

那人把目光移到断剑上，停了一会儿，又移回少年脸上。"剑断了，人不能断。"他说，"你师父也是这么教你的吧？"

少年点了点头。他的喉咙里有一股酸涩的气往上涌，但他咽了回去。咽下去的那一刻，他做出了决定。他把断剑往前递了半寸，然后，把它收回了原来的位置。

"我来归还断剑，"他说，"也来兑现师父的承诺。"

那人沉默了片刻，从石上站起来。他的斗篷被雨水浸得发沉，但他的腰背依旧挺直。他向少年微微颔首，伸出手，接过那柄断剑。山涧的水声在两人之间流过，像把这一刻冲洗得格外清晰。
"""

# Memo with a hook ledger that "advance"s H001 — body must echo "断剑" / "归还"
# (it does). Used by commitment_ledger.py.
SYNTH_MEMO = """\
---
chapter: 1
isGoldenOpening: false
volumeFinale: false
---

# 第 1 章 chapter memo

## 本章 hook 账

advance:
- H001 "断剑之约" → 主角归还断剑，兑现承诺

resolve:

defer:

open:
"""


def synth_writer_output() -> str:
    """Return a fake Writer output with all required sentinels."""
    return (
        "=== CHAPTER_TITLE ===\n"
        "第一章 山雨欲来\n\n"
        "=== CHAPTER_CONTENT ===\n"
        f"{SYNTH_BODY}\n"
        "=== CHAPTER_SUMMARY ===\n"
        "主角踏入山雨，归还断剑，兑现师父之约。\n\n"
        "=== POST_WRITE_ERRORS ===\n"
    )


def synth_audit_round(chapter: int, score: int = 88) -> dict:
    return {
        "chapter": chapter,
        "round": 0,
        "audit": {
            "overall_score": score,
            "passed": True,
            "issues": [
                {
                    "dim": 4,
                    "severity": "warning",
                    "category": "rhythm",
                    "description": "段落节奏略偏稳，可在中段加一个停顿",
                    "evidence": "",
                }
            ],
        },
        "deterministic_gates": {
            "ai_tells": {"critical": 0, "warning": 0},
            "sensitive": {"blocked": False},
            "post_write": {"critical": 0, "warning": 0},
            "fatigue": {"critical": 0, "warning": 0},
            "commitment_ledger": {"violations": 0},
        },
        "reviser_action": {
            "mode": None,
            "target_issues": [],
            "outcome": "skipped",
        },
    }


def synth_settler_raw(chapter: int) -> str:
    """A minimal but schema-valid settler raw output. The hookOps.upsert hook
    has expectedPayoff (admission requires it) plus a payoffTiming."""
    delta = {
        "chapter": chapter,
        "currentStatePatch": {
            "currentLocation": "南山涧边",
            "protagonistState": "归还断剑后心境澄明",
            "currentGoal": "下山入城",
            "currentConstraint": "暴雨封路",
        },
        "hookOps": {
            "upsert": [
                {
                    "hookId": "H001",
                    "startChapter": 1,
                    "type": "约定",
                    "status": "progressing",
                    "lastAdvancedChapter": 1,
                    "expectedPayoff": "断剑归还后揭示师父真正死因",
                    "payoffTiming": "near-term",
                    "notes": "断剑之约——核心牵引",
                }
            ],
        },
        "chapterSummary": {
            "chapter": chapter,
            "title": "山雨欲来",
            "events": "主角在雨中归还断剑，与神秘人会面。",
            "mood": "压抑后转澄明",
            "hooksAdvanced": ["H001"],
        },
        "notes": "首章基线设定；建立断剑核心 hook。",
    }
    return (
        "=== POST_SETTLEMENT ===\n"
        "本章主线：主角归还断剑，兑现承诺。\n"
        "新增设定：神秘人持有线索。\n\n"
        "=== RUNTIME_STATE_DELTA ===\n"
        f"{json.dumps(delta, ensure_ascii=False, indent=2)}\n"
        "=== END ===\n"
    )


def synth_audit_drift_issues() -> list[dict]:
    return [
        {
            "severity": "warning",
            "category": "rhythm",
            "description": "段落节奏略偏稳，可在中段加一个停顿",
        }
    ]


# ───────────────────────── step runner ──────────────────────────────────


class StepResult:
    def __init__(self, name: str, ok: bool, duration_ms: int,
                 summary: str, exit_code: int = 0,
                 stdout: str = "", stderr: str = "",
                 detail: dict | None = None) -> None:
        self.name = name
        self.ok = ok
        self.duration_ms = duration_ms
        self.summary = summary
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.detail = detail or {}

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "exit_code": self.exit_code,
        }
        if self.detail:
            d["detail"] = self.detail
        if not self.ok:
            d["stdout_tail"] = self.stdout[-500:]
            d["stderr_tail"] = self.stderr[-500:]
        return d


def run_subprocess(
    args: list[str],
    *,
    stdin_data: str | None = None,
    timeout: int = 60,
) -> tuple[int, str, str]:
    """Run a subprocess; return (exit_code, stdout, stderr)."""
    proc = subprocess.run(
        args,
        input=stdin_data,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def script(name: str) -> list[str]:
    return [sys.executable, str(SCRIPT_DIR / name)]


def parse_json_or_empty(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # script may have emitted extra "[summary]" lines or non-JSON tail —
        # try to find the first JSON block.
        for start_idx, ch in enumerate(text):
            if ch in "{[":
                for end_idx in range(len(text), start_idx, -1):
                    try:
                        return json.loads(text[start_idx:end_idx])
                    except json.JSONDecodeError:
                        continue
                break
        return {}


# ───────────────────────── individual steps ─────────────────────────────


def step_init_book(book_id: str, workdir: Path) -> tuple[Path, StepResult]:
    """Bootstrap the test book. Not counted as one of the 16 named steps;
    runs unconditionally before them. If init fails, the harness has nothing
    to test against, so we surface the error and abort."""
    t0 = time.monotonic()
    args = script("init_book.py") + [
        "--workdir", str(workdir),
        "--id", book_id,
        "--title", "测试集成书",
        "--genre", "xianxia",
        "--platform", "tomato",
        "--target-chapters", "30",
        "--chapter-words", "2500",
        "--lang", "zh",
    ]
    code, out, err = run_subprocess(args, timeout=30)
    duration = int((time.monotonic() - t0) * 1000)
    ok = code == 0
    book_dir = workdir / "books" / book_id
    if ok:
        # init_book prints JSON; check book dir exists
        ok = book_dir.is_dir() and (book_dir / "book.json").is_file()
    summary = (
        f"book at {book_dir}" if ok
        else f"init failed exit={code}"
    )
    return book_dir, StepResult(
        "init_book", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
    )


def step_writer_parse(book_dir: Path, runtime: Path) -> tuple[StepResult, dict]:
    raw_writer = synth_writer_output()
    raw_path = runtime / "chapter-0001.raw_writer.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw_writer, encoding="utf-8")

    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("writer_parse.py") + ["--file", str(raw_path), "--strict"],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    body = parsed.get("body", "")

    ok = (
        code == 0
        and parsed.get("ok") is True
        and isinstance(parsed.get("title"), str)
        and parsed.get("title")
        and isinstance(body, str)
        and len(body) > 200
        and isinstance(parsed.get("wordCount"), int)
        and isinstance(parsed.get("raw_sentinels_found"), list)
        and "CHAPTER_TITLE" in parsed.get("raw_sentinels_found", [])
        and "CHAPTER_CONTENT" in parsed.get("raw_sentinels_found", [])
    )
    n_sentinels = len(parsed.get("raw_sentinels_found", []))
    summary = (
        f"sentinels={n_sentinels} body={len(body)} chars "
        f"wordCount={parsed.get('wordCount')}"
    )
    if not ok and code == 0:
        summary += " (shape mismatch)"

    # Persist parsed body so downstream steps can use it.
    draft_path = runtime / "chapter-0001.draft.md"
    if body:
        draft_path.write_text(body, encoding="utf-8")

    detail = {
        "wordCount": parsed.get("wordCount"),
        "sentinelsFound": parsed.get("raw_sentinels_found"),
        "draftPath": str(draft_path),
    }
    return StepResult(
        "writer_parse", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err, detail=detail,
    ), {"draft": draft_path, "body": body, "title": parsed.get("title")}


def step_post_write_validate(book_dir: Path, draft: Path) -> StepResult:
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("post_write_validate.py") + [
            "--file", str(draft),
            "--chapter", "1",
            "--book", str(book_dir),
        ],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    issues = parsed.get("issues") if isinstance(parsed.get("issues"), list) else []
    n_crit = sum(1 for i in issues if i.get("severity") == "critical")
    n_warn = sum(1 for i in issues if i.get("severity") == "warning")

    # Acceptable: exit 0 (no critical) AND issues list is well-formed.
    # exit 2 is allowed only if we genuinely have critical (we don't, by design).
    ok = (
        code == 0
        and isinstance(parsed.get("ok"), bool)
        and isinstance(parsed.get("summary"), str)
        and isinstance(issues, list)
    )
    summary = f"critical={n_crit} warning={n_warn} (exit {code})"
    if n_crit > 0:
        summary += " — UNEXPECTED critical in synth body"
        ok = False
    return StepResult(
        "post_write_validate", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
        detail={"critical": n_crit, "warning": n_warn},
    )


def step_word_count(draft: Path) -> StepResult:
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("word_count.py") + [
            "--file", str(draft),
            "--mode", "zh",
            "--target", "2500",
            "--soft-min", "1800",
            "--soft-max", "3500",
        ],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    count = parsed.get("count")
    status = parsed.get("status")
    valid_status = {"in-soft", "in-hard", "under-soft", "over-soft", "under-hard", "over-hard"}
    ok = (
        code == 0
        and isinstance(count, int)
        and count > 0
        and status in valid_status
    )
    summary = f"count={count} status={status}"
    return StepResult(
        "word_count", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
        detail={"count": count, "status": status},
    )


def step_ai_tell_scan(draft: Path) -> StepResult:
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("ai_tell_scan.py") + ["--file", str(draft)],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    issues = parsed.get("issues") if isinstance(parsed.get("issues"), list) else None
    summary_str = parsed.get("summary") or ""
    n_crit = (
        sum(1 for i in issues if i.get("severity") == "critical")
        if issues else 0
    )
    ok = (
        code == 0
        and issues is not None
        and isinstance(summary_str, str)
        and "chars=" in summary_str
        and n_crit == 0  # synth body has no analysis-term-leak
    )
    summary = f"issues={len(issues or [])} critical={n_crit} ({summary_str[:80]})"
    return StepResult(
        "ai_tell_scan", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
        detail={"issueCount": len(issues or []), "critical": n_crit},
    )


def step_sensitive_scan(draft: Path) -> StepResult:
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("sensitive_scan.py") + ["--file", str(draft)],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    blocked = parsed.get("blocked")
    hits = parsed.get("hits")
    ok = (
        code == 0
        and isinstance(blocked, bool)
        and blocked is False
        and isinstance(hits, list)
    )
    summary = f"blocked={blocked} hits={len(hits or [])}"
    return StepResult(
        "sensitive_scan", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
        detail={"blocked": blocked, "hits": len(hits or [])},
    )


def step_audit_round_log(book_dir: Path, runtime: Path) -> StepResult:
    audit_path = runtime / "round-0.json"
    audit_path.write_text(
        json.dumps(synth_audit_round(1, 88), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("audit_round_log.py") + [
            "--book", str(book_dir),
            "--chapter", "1",
            "--round", "0",
            "--write", str(audit_path),
        ],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    ok = (
        code == 0
        and parsed.get("ok") is True
        and parsed.get("chapter") == 1
        and parsed.get("round") == 0
        and isinstance(parsed.get("delta"), dict)
    )
    summary = (
        f"round=0 score=88 delta_score_change="
        f"{parsed.get('delta', {}).get('score_change')}"
    )
    return StepResult(
        "audit_round_log", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
    )


def step_apply_delta(book_dir: Path, runtime: Path) -> StepResult:
    raw = synth_settler_raw(1)
    raw_path = runtime / "chapter-0001.settler.raw.md"
    raw_path.write_text(raw, encoding="utf-8")
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("apply_delta.py") + [
            "--book", str(book_dir),
            "--delta", str(raw_path),
            "--input-mode", "raw",
            "--skip-lock",
        ],
        timeout=60,
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    arbitration = parsed.get("arbitration") or {}
    arb_summary = arbitration.get("summary", "")
    hg = parsed.get("hookGovernance") or {}
    validate_ok = (hg.get("validate") or {}).get("ok") is True
    book_meta = parsed.get("bookMetadata") or {}
    ok = (
        code == 0
        and parsed.get("applied") is True
        and parsed.get("parseStage") == "applied"
        and "n_created=1" in arb_summary
        and validate_ok
        and book_meta.get("statusChanged") == "incubating→active"
    )
    summary = (
        f"applied parseStage={parsed.get('parseStage')} "
        f"arb='{arb_summary}' bookStatusChanged={book_meta.get('statusChanged')}"
    )
    return StepResult(
        "apply_delta", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
        detail={
            "arbitrationSummary": arb_summary,
            "hookGovValidateOk": validate_ok,
            "statusChanged": book_meta.get("statusChanged"),
        },
    )


def step_chapter_index_add(book_dir: Path, draft: Path,
                            title: str | None) -> StepResult:
    """add chapter 1 to chapters/index.json. Also persists chapters/0001.md
    so analytics / status / chapter-index validate see the file."""
    chapter_md = book_dir / "chapters" / "0001.md"
    chapter_md.parent.mkdir(parents=True, exist_ok=True)
    body = draft.read_text(encoding="utf-8")
    chapter_md.write_text(
        f"# {title or '山雨欲来'}\n\n{body}\n", encoding="utf-8"
    )
    word_count = len(body)
    audit_issues = json.dumps(["[warning] rhythm: 段落节奏略偏稳"],
                              ensure_ascii=False)
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("chapter_index.py") + [
            "--book", str(book_dir),
            "--skip-lock",
            "add",
            "--chapter", "1",
            "--status", "ready-for-review",
            "--title", title or "山雨欲来",
            "--word-count", str(max(1, word_count // 2)),
            "--audit-issues", audit_issues,
        ],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    ok = (
        code == 0
        and parsed.get("ok") is True
        and parsed.get("action") in ("added", "replaced")
    )
    summary = f"action={parsed.get('action')} totalEntries={parsed.get('totalEntries')}"
    return StepResult(
        "chapter_index_add", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
    )


def step_snapshot_state(book_dir: Path) -> StepResult:
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("snapshot_state.py") + [
            "--book", str(book_dir),
            "create",
            "--chapter", "1",
            "--json",
        ],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    ok = (
        code == 0
        and parsed.get("chapter") == 1
        and isinstance(parsed.get("snapshotDir"), str)
        and (parsed.get("fileCount") or 0) > 0
    )
    summary = (
        f"files={parsed.get('fileCount')} bytes={parsed.get('totalBytes')}"
    )
    return StepResult(
        "snapshot_state", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
    )


def step_audit_drift(book_dir: Path, runtime: Path) -> StepResult:
    issues_path = runtime / "chapter-0001.audit-final-issues.json"
    issues_path.write_text(
        json.dumps(synth_audit_drift_issues(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("audit_drift.py") + [
            "--book", str(book_dir),
            "write",
            "--chapter", "1",
            "--issues", str(issues_path),
        ],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    ok = (
        code == 0
        and parsed.get("ok") is True
        and parsed.get("action") == "written"
        and parsed.get("keptIssues") == 1
    )
    summary = (
        f"action={parsed.get('action')} keptIssues={parsed.get('keptIssues')}"
    )
    return StepResult(
        "audit_drift", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
    )


def step_commitment_ledger(book_dir: Path, runtime: Path,
                            draft: Path) -> StepResult:
    memo_path = runtime / "chapter_memo.md"
    memo_path.write_text(SYNTH_MEMO, encoding="utf-8")
    hooks_path = book_dir / "story" / "state" / "hooks.json"
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("commitment_ledger.py") + [
            "--memo", str(memo_path),
            "--draft", str(draft),
            "--hooks", str(hooks_path),
            "--chapter", "1",
        ],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    violations = parsed.get("violations") or []
    ok = (
        code == 0
        and parsed.get("ok") is True
        and len(violations) == 0
    )
    summary = (
        f"violations={len(violations)} "
        f"summary='{parsed.get('summary', '')[:60]}'"
    )
    return StepResult(
        "commitment_ledger", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
    )


def step_analytics(book_dir: Path) -> StepResult:
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("analytics.py") + ["--book", str(book_dir), "--json"],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    # analytics returns {aggregate: {...}, ...} or top-level keys; check both.
    agg = parsed.get("aggregate") or parsed
    total_chapters = agg.get("totalChapters")
    total_words = agg.get("totalWords")
    ok = (
        code == 0
        and isinstance(total_chapters, int)
        and total_chapters >= 1
        and isinstance(total_words, int)
        and total_words > 0
    )
    summary = f"totalChapters={total_chapters} totalWords={total_words}"
    return StepResult(
        "analytics", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
    )


def step_status(book_dir: Path) -> StepResult:
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("status.py") + ["--book", str(book_dir), "--json"],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    books = parsed.get("books") or []
    ok = (
        code == 0
        and isinstance(books, list)
        and len(books) == 1
        and isinstance(books[0].get("totalChapters"), int)
    )
    book_stats = books[0] if books else {}
    summary = (
        f"books={len(books)} "
        f"chapters={book_stats.get('totalChapters')} "
        f"words={book_stats.get('totalWords')}"
    )
    return StepResult(
        "status", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
    )


def step_consolidate_check(book_dir: Path) -> StepResult:
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("consolidate_check.py") + [
            "--book", str(book_dir), "--json",
        ],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    should = parsed.get("shouldConsolidate")
    ok = (
        code == 0
        and isinstance(should, bool)
        and should is False  # one chapter shouldn't trigger
        and isinstance(parsed.get("totalChapters"), int)
    )
    summary = (
        f"shouldConsolidate={should} "
        f"totalChapters={parsed.get('totalChapters')} "
        f"reason='{(parsed.get('reason') or '')[:50]}'"
    )
    return StepResult(
        "consolidate_check", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
    )


def step_cadence_check(book_dir: Path) -> StepResult:
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("cadence_check.py") + [
            "--book", str(book_dir),
            "--current-chapter", "2",
            "--json",
        ],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    ok = (
        code == 0
        and parsed.get("currentChapter") == 2
        and "recommendedNext" in parsed
        and isinstance(parsed.get("satisfactionPressure"), str)
    )
    rn = parsed.get("recommendedNext") or {}
    summary = (
        f"pressure={parsed.get('satisfactionPressure')} "
        f"recommendedNext.chapterType="
        f"'{rn.get('chapterType', '')[:20]}'"
    )
    return StepResult(
        "cadence_check", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
    )


def step_memory_retrieve(book_dir: Path) -> StepResult:
    t0 = time.monotonic()
    code, out, err = run_subprocess(
        script("memory_retrieve.py") + [
            "--book", str(book_dir),
            "--current-chapter", "2",
            "--format", "json",
        ],
    )
    duration = int((time.monotonic() - t0) * 1000)
    parsed = parse_json_or_empty(out)
    recent = parsed.get("recentSummaries")
    active = parsed.get("activeHooks")
    ok = (
        code == 0
        and isinstance(recent, list)
        and isinstance(active, list)
        and len(recent) >= 1
        and len(active) >= 1
    )
    summary = (
        f"recent={len(recent or [])} active={len(active or [])}"
    )
    return StepResult(
        "memory_retrieve", ok, duration, summary,
        exit_code=code, stdout=out, stderr=err,
    )


# ─────────────────────────── orchestration ──────────────────────────────


STEP_NAMES = [
    "writer_parse",
    "post_write_validate",
    "word_count",
    "ai_tell_scan",
    "sensitive_scan",
    "audit_round_log",
    "apply_delta",
    "chapter_index_add",
    "snapshot_state",
    "audit_drift",
    "commitment_ledger",
    "analytics",
    "status",
    "consolidate_check",
    "cadence_check",
    "memory_retrieve",
]


def run_all_steps(
    book_dir: Path,
    runtime: Path,
    only: str | None,
    verbose: bool,
) -> list[StepResult]:
    results: list[StepResult] = []
    ctx: dict[str, Any] = {}

    def maybe(name: str, fn) -> None:
        if only and name != only:
            return
        try:
            r = fn()
        except Exception as exc:  # noqa: BLE001 — we want failures captured
            r = StepResult(
                name, False, 0, f"harness exception: {exc!r}",
                exit_code=-1, stdout="", stderr=repr(exc),
            )
        results.append(r)
        if verbose:
            _print_step_verbose(r)

    # 1. writer_parse — produces draft used by 2/3/4/5/11
    if not only or only == "writer_parse":
        try:
            r, parsed_ctx = step_writer_parse(book_dir, runtime)
            ctx.update(parsed_ctx)
        except Exception as exc:  # noqa: BLE001
            r = StepResult(
                "writer_parse", False, 0, f"harness exception: {exc!r}",
                exit_code=-1, stderr=repr(exc),
            )
            ctx["draft"] = runtime / "chapter-0001.draft.md"
            ctx["title"] = None
        results.append(r)
        if verbose:
            _print_step_verbose(r)
    else:
        # We need a draft for downstream when running --step only on a later
        # step. Synthesize the draft from the body directly.
        draft_path = runtime / "chapter-0001.draft.md"
        if not draft_path.is_file():
            draft_path.write_text(SYNTH_BODY, encoding="utf-8")
        ctx["draft"] = draft_path
        ctx["title"] = "山雨欲来"

    draft = ctx.get("draft") or (runtime / "chapter-0001.draft.md")
    if not draft.is_file():
        # Hard fallback so subsequent --step targets don't all crash.
        draft.write_text(SYNTH_BODY, encoding="utf-8")

    title = ctx.get("title") or "山雨欲来"

    maybe("post_write_validate", lambda: step_post_write_validate(book_dir, draft))
    maybe("word_count", lambda: step_word_count(draft))
    maybe("ai_tell_scan", lambda: step_ai_tell_scan(draft))
    maybe("sensitive_scan", lambda: step_sensitive_scan(draft))
    maybe("audit_round_log", lambda: step_audit_round_log(book_dir, runtime))
    maybe("apply_delta", lambda: step_apply_delta(book_dir, runtime))
    maybe("chapter_index_add",
          lambda: step_chapter_index_add(book_dir, draft, title))
    maybe("snapshot_state", lambda: step_snapshot_state(book_dir))
    maybe("audit_drift", lambda: step_audit_drift(book_dir, runtime))
    maybe("commitment_ledger",
          lambda: step_commitment_ledger(book_dir, runtime, draft))
    maybe("analytics", lambda: step_analytics(book_dir))
    maybe("status", lambda: step_status(book_dir))
    maybe("consolidate_check", lambda: step_consolidate_check(book_dir))
    maybe("cadence_check", lambda: step_cadence_check(book_dir))
    maybe("memory_retrieve", lambda: step_memory_retrieve(book_dir))

    return results


def _print_step_verbose(r: StepResult) -> None:
    mark = "OK" if r.ok else "FAIL"
    print(f"--- [{mark}] {r.name} (exit {r.exit_code}, {r.duration_ms} ms) ---",
          file=sys.stderr)
    if r.stdout:
        print(f"stdout: {r.stdout[:1500]}", file=sys.stderr)
    if r.stderr:
        print(f"stderr: {r.stderr[:1500]}", file=sys.stderr)


def render_text(results: list[StepResult], total: int, elapsed_s: float,
                tmp_label: str) -> str:
    lines: list[str] = []
    n_total = len(results)
    for i, r in enumerate(results, start=1):
        mark = "OK  " if r.ok else "FAIL"
        lines.append(
            f"[e2e] step {i}/{n_total}: {r.name:<22} {mark} "
            f"({r.duration_ms} ms) {r.summary}"
        )
        if not r.ok:
            tail_err = r.stderr.strip().splitlines()[-3:] if r.stderr else []
            for ln in tail_err:
                lines.append(f"        stderr: {ln[:180]}")
            tail_out = r.stdout.strip().splitlines()[-3:] if r.stdout else []
            for ln in tail_out:
                lines.append(f"        stdout: {ln[:180]}")

    n_pass = sum(1 for r in results if r.ok)
    n_fail = n_total - n_pass
    if n_fail == 0:
        lines.append("")
        lines.append(f"PASS: {n_pass}/{n_total} steps passed in {elapsed_s:.1f}s")
    else:
        lines.append("")
        lines.append(
            f"FAIL: {n_fail}/{n_total} steps failed in {elapsed_s:.1f}s "
            f"(passed: {n_pass})"
        )
    lines.append(f"tempBookDir: {tmp_label}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="e2e_test.py",
        description=(
            "End-to-end deterministic-glue test harness. Synthesizes Writer "
            "+ audit + settler outputs and walks the chain through 16 "
            "scripts, asserting exit codes and JSON shapes. No LLM calls."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Steps:\n  " + "\n  ".join(STEP_NAMES) + "\n\n"
            "Exit 0 if all steps pass; 1 otherwise."
        ),
    )
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable JSON only (no human report)")
    p.add_argument("--keep-tmp", action="store_true",
                   help="don't clean up the tempdir; print path for inspection")
    p.add_argument("--verbose", action="store_true",
                   help="echo each step's stdout/stderr to stderr")
    p.add_argument("--step", default=None, choices=STEP_NAMES,
                   help="run only one named step (still does init + setup)")
    p.add_argument("--book-id", default="e2e-test",
                   help="book id to use inside the tempdir (default: e2e-test)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    overall_t0 = time.monotonic()

    # Allocate tempdir manually so --keep-tmp can suppress cleanup.
    tmp = tempfile.mkdtemp(prefix="novel-writer-e2e-")
    workdir = Path(tmp)
    cleaned = False
    try:
        # ── init book (gating step; not counted) ─────────────────────────
        book_dir, init_result = step_init_book(args.book_id, workdir)
        if not init_result.ok:
            elapsed = time.monotonic() - overall_t0
            tmp_label = (
                str(workdir) if args.keep_tmp else f"{workdir} (will clean up)"
            )
            init_payload = {
                "ok": False,
                "totalSteps": 0,
                "passed": 0,
                "failed": 1,
                "steps": [init_result.to_dict()],
                "tempBookDir": tmp_label,
                "abortedAt": "init_book",
            }
            if args.json:
                print(json.dumps(init_payload, ensure_ascii=False, indent=2))
            else:
                print(
                    f"[e2e] init_book FAILED (exit {init_result.exit_code}, "
                    f"{init_result.duration_ms} ms) — aborting harness."
                )
                if init_result.stderr:
                    print(f"  stderr: {init_result.stderr.strip()[:600]}")
                if init_result.stdout:
                    print(f"  stdout: {init_result.stdout.strip()[:600]}")
                print(f"  tempBookDir: {tmp_label}")
            return 1

        runtime = book_dir / "story" / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)

        results = run_all_steps(book_dir, runtime, args.step, args.verbose)

        elapsed = time.monotonic() - overall_t0
        n_total = len(results)
        n_pass = sum(1 for r in results if r.ok)
        n_fail = n_total - n_pass

        tmp_label = (
            str(workdir) if args.keep_tmp
            else f"{workdir} (cleaned up)"
        )

        if args.json:
            payload = {
                "ok": n_fail == 0,
                "totalSteps": n_total,
                "passed": n_pass,
                "failed": n_fail,
                "elapsedSeconds": round(elapsed, 3),
                "steps": [r.to_dict() for r in results],
                "tempBookDir": tmp_label,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(render_text(results, n_total, elapsed, tmp_label))

        return 0 if n_fail == 0 else 1
    finally:
        if not args.keep_tmp:
            try:
                import shutil
                shutil.rmtree(workdir, ignore_errors=True)
                cleaned = True
            except OSError:
                pass
        if args.keep_tmp:
            print(f"[e2e] kept tempdir at: {workdir}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
