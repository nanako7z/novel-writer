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
    # 3a. 先取滑窗记忆（避免 30+ 章后全量 chapter_summaries 爆 context）
    memory = scripts/memory_retrieve.py --book <bookDir> \
                --current-chapter chapterNo \
                [tunables based on chapter_memo flags：
                 isGoldenOpening → window-recent=2 window-relevant=0
                 cliffResolution → --include-resolved-hooks
                 arcTransition  → window-relevant=12]
    # 3b. 装配 context_pkg + rule_stack（吃 memory + 题材 profile）
    genreProfile = templates/genres/{book.genre}.md   # 若 id 不在 catalog 回退 other.md
    contextPkg, ruleStack, trace = runComposer(chapterMemo, memory, genreProfile, truth files)
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
        # runArchitect 内部不是单次 LLM 调用——它现在是一个
        # Architect ↔ Foundation Reviewer 的回环（整体 ≤ 2 轮）：
        #   1) Architect 出 5 SECTION（story_frame / volume_map /
        #      roles / book_rules / pending_hooks）
        #   2) Foundation Reviewer 在内存里审稿 → verdict
        #      ∈ {pass, revise, reject}
        #      （详见 references/foundation-reviewer.md）
        #   3) pass   → 切分 SECTION 落盘，本步骤完成
        #      revise → 把 issues + overallFeedback 注回
        #              Architect 跑第 2 轮，再审一次
        #              第 2 轮还非 pass → best-effort 落盘
        #              + architectStatus="review-failed"
        #      reject → 不落盘，把 issues 抛给用户决策，
        #              不自动重试，主循环在此中止
        # Architect 调用次数 ≤ 2；Reviewer 解析失败 / LLM 抽风
        # 走 Reviewer 内部降级，不消耗 Architect 重做预算。
        runArchitect(book, fanficCanon if fanfic mode)

    # ── 5. Write ───────────────────────────────────────────
    # references/phases/05-writer.md
    rawWriter = runWriter(
        chapterMemo, contextPkg, ruleStack,
        previous chapter excerpt (≤ 800 字),
        style_guide.md, style_profile.json,
        fanfic_canon.md (if applicable)
    )
    # 落盘：story/raw_writer/chapter-{NNNN}.md（保留所有 === BLOCK === sentinel）

    # ── 5b. Parse Writer output（确定性 sentinel 解析）────────
    # references/post-write-validation.md（脚本：scripts/writer_parse.py）
    parsed = scripts/writer_parse.py --file story/raw_writer/chapter-{NNNN}.md --strict
    if parsed.exitCode != 0:
        # 缺 sentinel → 让 Writer 仅重输出缺失区块（≤ 2 次）
        # 仍失败 → 改用默认 lenient 模式做 best-effort 解析，
        #         并在 manifest 标 status=parser-failed-best-effort
        retry up to 2; then fall back to lenient (drop --strict)
    # 取 parsed.body 当作 draft；落盘：story/runtime/chapter-{NNNN}.draft.md
    draft = parsed.body

    # ── 5c. Post-write validate（确定性机械层闸门）───────────
    # references/post-write-validation.md（脚本：scripts/post_write_validate.py）
    pwv = scripts/post_write_validate.py --file story/runtime/chapter-{NNNN}.draft.md \
              --chapter chapterNo [--book <bookDir>]
    if pwv.exitCode == 2:    # 出现 critical（章节号自指 / 破折号 / 不是…而是… / 段落塌陷 / sentinel 残留 / 长度异常）
        # 让 Writer 拿 pwv.issues 当反馈重写一次（最多 1 次）
        rawWriter = runWriter(retry=true, postWriteFeedback=pwv.issues)
        重跑 5b / 5c
        if 仍 critical:
            # 不再硬扛——报错给用户，不进 Normalizer / Auditor
            return { status: "post-write-validate-failed", issues: pwv.issues }
    # warning（exit 0 + warning 列表）合并到后续 Auditor 的 issues 池，由 Reviser polish 模式统一处理

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

    # ── 10. 校验并应用 delta（确定性 + hook 治理闸门）──────────
    # apply_delta 内部会自动调 hook_governance.py 的 validate + stale-scan
    result = scripts/apply_delta.py --book <bookDir> --delta story/runtime/chapter-{NNNN}.delta.json
    if result.exitCode != 0:
        # 两类失败：
        #  · delta 不合规 schema → 退回 settler 重写一次
        #  · result.hookGovernanceBlocked == True → 治理 critical（如 depends_on 环、
        #    pending_hooks.md 引用对不上）→ 让 Settler 改 delta，不能强落盘
        delta = runSettlerWithRetry(governanceFeedback=result.hookGovernance.validate.issues)
        retry once

    # 10.1 Hook seed → ledger 推升（4 条 OR 条件）
    # 让本章 Settler 产出的新 hookCandidates 在下章 Composer 看到之前就经过门槛
    scripts/hook_governance.py --book <bookDir> --command promote-pass --current-chapter chapterNo

    # ── 10.5. （可选）Polisher 文字层打磨 ───────────────────────
    # references/phases/11-polisher.md
    # 仅在 audit 真正过线（非借线）时入场，磨表面不改结构。
    POLISH_THRESHOLD = 88           # 借线（85-87）跳过，不冒不必要的风险
    if passed and audit.overall_score >= POLISH_THRESHOLD:
        polishResult = runPolisher(
            chapterContent = draft,         # audit 通过的最终态
            chapterMemo,
            genreProfile.fatigueWords,
            language = book.language
        )
        if polishResult.changed:
            # 备份 audit 通过的原版本，准备覆盖
            write story/runtime/chapter-{NNNN}.pre-polish.md = draft
            postScan = ai_tell_scan + sensitive_scan on polishResult.polishedContent
            if postScan introduced new critical/block:
                # Polisher 引入了新问题——回退
                draft 保持原状（pre-polish 备份保留作证据）
                log status = "polish-reverted-introduced-issues"
            else:
                draft = polishResult.polishedContent  # 剥掉 [polisher-note] 行
                # polisher-note 进 polish.json 的 polisherNotes，下一章 Planner 读
        # 元数据：story/runtime/chapter-{NNNN}.polish.json
    elif passed:
        log polish: skipped (borderline score=<n>)
    # audit 未过则根本不到这一步——Reviser 已经在 step 7 处理过了

    # ── 11. 最终落盘章节正文 + 章节运营索引 ──────────────────
    write chapters/{NNNN}.md = draft
    update story/state/manifest.json#lastAppliedChapter = chapterNo

    # 11.0 写章节运营索引（chapters/index.json）—— inkos `inkos review list` /
    # `analytics` / `book delete --json` 等都从这里读章节状态。
    # schema: references/schemas/chapter-index.md
    chapterStatus = passed ? "ready-for-review" : "audit-failed"
    auditIssuesFmt = [f"[{i.severity}] {i.description}" for i in allIssues]
    scripts/chapter_index.py --book <bookDir> add \
        --chapter chapterNo \
        --status chapterStatus \
        --title <chapter title from chapter_memo or writer> \
        --word-count finalWordCount \
        --audit-issues <JSON-encoded auditIssuesFmt> \
        [--length-warnings <JSON if any>] \
        [--token-usage <JSON if tracked>] \
        [--review-note "polish-reverted-introduced-issues" if applicable]
    # 退出码非 0 即视为索引写入失败——记 warning 但不 abort（章节正文已落盘）。

    # ── 11.05 Chapter Analyzer (post-persist 定性回顾) ─────────
    # references/phases/13-analyzer.md
    # 章节正文 + 真理文件全部定稿后，跑一次单向只读的定性回顾。
    # 产物 chapter-{NNNN}.analysis.json 是给下一章 Planner 的喂料——
    # 关心的是"读起来怎么样、对下一章有什么交代"，不是事实增量
    # （事实增量是 Settler 在 step 9 干的）。
    analysis = runChapterAnalyzer(
        chapterFile        = chapters/{NNNN}.md,           # Polisher 后的最终版
        chapterMemo        = story/runtime/chapter_memo.md
                          OR story/runtime/chapter-{NNNN}.intent.md,
        auditResult        = story/runtime/chapter-{NNNN}.audit.json (可选),
        observations       = story/runtime/observations.md (可选),
        hooksSnapshot      = story/state/hooks.json,
        genreProfile       = templates/genres/{book.genre}.md,
        bookConfig         = book.json
    )
    # 落盘：story/runtime/chapter-{NNNN}.analysis.json
    # 失败处理：解析失败重试 ≤ 2，仍失败写 stub（warning="analyzer-failed"），
    #           不阻断主循环——Analyzer 是信息性的，不是 load-bearing。
    # 关键不变量：Analyzer 单向只读，绝不修改 chapters/* 或 story/state/*。

    # ── 11.1 (可选) Consolidate 自动建议 —— 不擅自跑 ──────────
    # references/phases/12-consolidator.md
    # 落盘 + manifest 更新后，跑一次只读检测脚本：
    #   python scripts/consolidate_check.py --book <bookDir> --threshold 60 --json
    # 若返回 shouldConsolidate=true（章节摘要 ≥ 60 行 且 至少有 1 卷已完结
    # 且 该卷尚未归档），向用户**提示一句**：
    #   "前面 N 卷已完结，章节摘要 K 条，要不要做一次 consolidate？"
    # 用户点头才进 phase 12；不点头就放着，下次写完再问。
    # 严禁自动跑 consolidate——它会重写 chapter_summaries.json，是有损操作。

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
| "整章 polish / 文字打磨一遍" | 直接跑 [phase 11 polisher](11-polisher.md)（绕过 audit 借线规则） |
| "学一下这段文字的风格" | `references/branches/style.md` 的 analyze + import |
| "更新一下设定 / 重做架构" | phase 04 architect 单跑 |
| "压缩前面卷 / consolidate / 摘要太多了 / 把前面的卷归档 / 历史压缩一下" | [phase 12 consolidator](12-consolidator.md)（侧流，**不进**主循环；先跑 `scripts/consolidate_check.py` 看是否值得跑） |
| "看一下当前进度" | 不进 phase；读 `story/state/manifest.json` + `chapter_summaries.json` 直接答 |

## 关键不变量（每一阶段都要守的）

1. **真理文件只能经 `scripts/apply_delta.py` 修改**——直接编辑 `story/state/*.json` 视为脏写，会被 manifest 校验发现。
2. **章节正文落盘前必须经过 step 7（audit-revise 回环）**——即便 audit 失败也要标 `status: "audit-failed-best-effort"`，不要悄悄写盘。
3. **Polisher（step 10.5，见 [11-polisher.md](11-polisher.md)）只在 audit 真正过线时入场**——借线（score 85-87）跳过；audit 未过则压根不进 Polisher，由 Reviser 兜底。Polisher 单 pass，不开回环，引入新问题即回退。
4. **每个 phase 的产物都先写到 `story/runtime/`，最终章节 + delta 才落到 `chapters/` 与 `story/state/`**——保证可回溯、可重跑。
5. **任何阶段的 LLM 输出若解析失败，重试 ≤ 该阶段上限（Planner 3、Architect 2、audit-revise 整轮 3）**，不要无限重试。
6. **Reflector 不是单独阶段**——README 提到了，但源码里它的职责并入了 audit-revise loop（"反思+修改"是同一阶段的两面）；本 SKILL 也合并不单列。
7. **Chapter Analyzer (step 11.05, [13-analyzer.md](13-analyzer.md)) 是单向只读**——它只读已定稿的本章正文与配套 runtime/state，产物只有 `story/runtime/chapter-{NNNN}.analysis.json` 一份，**不**修改 `chapters/*`、`story/state/*` 或 `pending_hooks.md`。失败也不阻断主循环（写 stub），区别于 Auditor / Settler 这两个 load-bearing 阶段。

## 何时**跳过**主循环

- 用户只是问"现在写到哪了"——直接读 manifest 答；
- 用户只是要看某个真理文件——直接 cat；
- 用户给的是**单点修改请求**（"把第 3 章那句改成 X"）——用 reviser 的 `spot-fix` 模式，跳过 plan/compose/write/audit；
- 用户在调风格、建项目、抽 canon——这些是侧流，不是主循环。

## 运行时遗留物清理

每章完成（step 11 落盘后），可保留 `story/runtime/chapter-{NNNN}.*` 作为审计证据；7 天后或下一卷开始时可手动归档。SKILL 不主动清理。
