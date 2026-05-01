# Phase 00: 主循环编排

本文件不是某一阶段的 prompt，而是**整个 SKILL 的"调度器"**——告诉 Claude 在用户发出"写下一章"之类指令时，按什么顺序触发哪些 phase 文件、什么条件回环、什么时候退出。

## 顶层流程图

```
┌──────────────────────────────────────────────────────────────────┐
│  用户指令 → SKILL.md 决策路由                                       │
│                                                                  │
│  ┌─ 没有书目录 ──→ 走 init 流（不在主循环）                          │
│  ├─ 有书但首章未架构 ──→ Phase 04 architect → 主循环                │
│  ├─ 主流（写下一章） ──→ 进入主循环                                  │
│  ├─ 单点指令（审/改/抽风格/抽 canon） ──→ 直接跳 09/10/style/fanfic │
│  └─ 退场                                                          │
└──────────────────────────────────────────────────────────────────┘
```

## 主循环（write-next）

下面的伪代码用于"写下一章"。请严格按顺序执行；除非显式说明，前一阶段产物落盘后才能进入下一阶段。

```pseudocode
function writeNextChapter(book):
    # ── 1. 准备阶段 ────────────────────────────────────────
    book        = read book.json
    chapterNo   = read story/state/manifest.json#lastAppliedChapter + 1
    lengthSpec  = buildLengthSpec(book.chapterWordCount)

    # ── 2. Plan ────────────────────────────────────────────
    # references/phases/02-planner.md
    chapterMemo = runPlanner(
        author_intent.md, current_focus.md,
        story_frame.md, volume_map.md, book_rules.md,
        story/state/current_state.json,
        story/state/hooks.json,
        recent chapter_summaries (≤ 6 条)
    )
    # 失败重试 ≤ 3 次
    # 落盘：story/runtime/chapter-{NNNN}.intent.md（YAML+md）

    # ── 3. Compose ─────────────────────────────────────────
    # references/phases/03-composer.md（无 LLM）
    contextPkg, ruleStack, trace = runComposer(chapterMemo, all truth files)
    # 落盘：story/runtime/chapter-{NNNN}.context.json
    #       story/runtime/chapter-{NNNN}.rule-stack.json
    #       story/runtime/chapter-{NNNN}.trace.json

    # ── 4. (可选) Architect 回顾 ────────────────────────────
    # 仅在以下条件之一时触发：
    #  · 首章（chapterNo == 1）且 story_frame.md 缺失或为占位
    #  · 卷尾切换（volume_map 上标记的边界）
    #  · 用户显式要求 "重做架构"
    # references/phases/04-architect.md
    if needsFoundation:
        runArchitect(book, fanficCanon if fanfic mode)
        # 失败重试 ≤ 2 次（FoundationReviewer 卡阀）

    # ── 5. Write ───────────────────────────────────────────
    # references/phases/05-writer.md
    draft = runWriter(
        chapterMemo, contextPkg, ruleStack,
        previous chapter excerpt (≤ 800 字),
        style_guide.md, style_profile.json,
        fanfic_canon.md (if applicable)
    )
    # 落盘：story/runtime/chapter-{NNNN}.draft.md

    # ── 6. 长度治理（pre-audit normalize）─────────────────────
    # references/phases/08-normalizer.md
    count = scripts/word_count.py --file draft --target ... --soft-min ... --soft-max ...
    if count.status not in {"in-soft"}:
        draft = runNormalizer(draft, lengthSpec)  # 单次修正，max 2 passes
    # 落盘：story/runtime/chapter-{NNNN}.normalized.md

    # ── 7. Audit-Revise 回环（max 3 轮）─────────────────────────
    # references/phases/09-auditor.md + 10-reviser.md
    iter = 0
    lastScore = 0
    EPSILON = 3              # 增益 < 3 即退出
    PASS = 85                # 分数线
    MAX_ITER = 3
    while iter < MAX_ITER:
        # 7a. 确定性闸门
        aiTells   = scripts/ai_tell_scan.py --file draft
        sensitive = scripts/sensitive_scan.py --file draft

        # 7b. 语义审计
        audit = runAuditor(draft, contextPkg, fanficMode if any)

        allIssues = audit.issues + aiTells.issues + sensitive.issues_warn_or_block

        passed = (audit.overall_score >= PASS
                  and count.status == "in-soft"
                  and sensitive.blocked == False
                  and no critical in allIssues)

        if passed:
            break

        # 7c. 修订
        mode = chooseReviseMode(allIssues)   # 见 references/phases/10-reviser.md
        draft = runReviser(draft, allIssues, mode)

        # 7d. 修订后若长度漂移，再单次 normalize
        if length 漂出 soft range:
            draft = runNormalizer(draft, lengthSpec)

        # 7e. 增益检查
        improvement = audit.overall_score - lastScore
        if improvement < EPSILON:
            break          # 收益递减即退出，不再死磕
        lastScore = audit.overall_score
        iter += 1

    # ── 8. Observe ─────────────────────────────────────────
    # references/phases/06-observer.md
    observations = runObserver(draft, current truth files)
    # 落盘：临时变量；进 Settler 不单独落盘

    # ── 9. Settle (产出 RuntimeStateDelta) ───────────────────
    # references/phases/07-settler.md
    delta = runSettler(draft, observations, current truth files)
    # 落盘：story/runtime/chapter-{NNNN}.delta.json

    # ── 10. 校验并应用 delta（确定性）──────────────────────────
    result = scripts/apply_delta.py --book <bookDir> --delta story/runtime/chapter-{NNNN}.delta.json
    if result.exitCode != 0:
        # delta 不合规：退回到 settler 重写一次；仍不行则人工
        delta = runSettlerWithRetry(...)
        retry once

    # ── 11. 最终落盘章节正文 ─────────────────────────────────
    write chapters/{NNNN}.md = draft
    update story/state/manifest.json#lastAppliedChapter = chapterNo

    return {
        chapterNumber: chapterNo,
        title, wordCount, auditResult: audit,
        revised: iter > 0,
        status: passed ? "ready-for-review" : "audit-failed-best-effort"
    }
```

## 路由表（用户指令 → 入口）

| 用户大致这么说 | 入口 |
|---|---|
| "建项目 / 初始化一本 / 我想写一本网文" | 不进入 phase；走 SKILL.md 的"项目初始化"小节，调 `scripts/init_book.py` |
| "我要写《XX》的同人 / fanfic" | 同上，但加 `--fanfic-mode` 参数；之后走 `references/branches/fanfic.md` 里的 canon 抽取 |
| "写第 N 章 / 写下一章" | 上面整个 writeNextChapter 主循环 |
| "审一下第 N 章" | 单独跑 phase 09，不进 reviser |
| "改一下这一章 / 用 polish 模式改" | 单独跑 phase 09 + 10，按用户给的模式或 auto |
| "学一下这段文字的风格" | `references/branches/style.md` 的 analyze + import |
| "更新一下设定 / 重做架构" | phase 04 architect 单跑 |
| "看一下当前进度" | 不进 phase；读 `story/state/manifest.json` + `chapter_summaries.json` 直接答 |

## 关键不变量（每一阶段都要守的）

1. **真理文件只能经 `scripts/apply_delta.py` 修改**——直接编辑 `story/state/*.json` 视为脏写，会被 manifest 校验发现。
2. **章节正文落盘前必须经过 step 7（audit-revise 回环）**——即便 audit 失败也要标 `status: "audit-failed-best-effort"`，不要悄悄写盘。
3. **每个 phase 的产物都先写到 `story/runtime/`，最终章节 + delta 才落到 `chapters/` 与 `story/state/`**——保证可回溯、可重跑。
4. **任何阶段的 LLM 输出若解析失败，重试 ≤ 该阶段上限（Planner 3、Architect 2、audit-revise 整轮 3）**，不要无限重试。
5. **Reflector 不是单独阶段**——README 提到了，但源码里它的职责并入了 audit-revise loop（"反思+修改"是同一阶段的两面）；本 SKILL 也合并不单列。

## 何时**跳过**主循环

- 用户只是问"现在写到哪了"——直接读 manifest 答；
- 用户只是要看某个真理文件——直接 cat；
- 用户给的是**单点修改请求**（"把第 3 章那句改成 X"）——用 reviser 的 `spot-fix` 模式，跳过 plan/compose/write/audit；
- 用户在调风格、建项目、抽 canon——这些是侧流，不是主循环。

## 运行时遗留物清理

每章完成（step 11 落盘后），可保留 `story/runtime/chapter-{NNNN}.*` 作为审计证据；7 天后或下一卷开始时可手动归档。SKILL 不主动清理。
