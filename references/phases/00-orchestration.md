# Phase 00: 主循环编排

整个 SKILL 的"调度器"——告诉 Claude 在用户发出"写下一章"之类指令时，按什么顺序触发哪些 phase 文件、什么条件回环、什么时候退出。

不变量统一来源：[invariants.md](../invariants.md)（编号 #1-#11）。本文件只在 step 边界 inline 引用编号，不复述规则。

## 顶层流程图

```
用户指令 → SKILL.md 决策路由
  ├─ 没有书目录 ──→ 走 init 流（不在主循环）
  ├─ 有书但首章未架构 ──→ Phase 04 architect → 主循环
  ├─ 主流（写下一章） ──→ 进入主循环
  ├─ 单点指令（审/改/抽风格/抽 canon） ──→ 直接跳 09/10/style/fanfic
  └─ 退场
```

## 主循环 preflight（动手前逐项打钩）

每次进入 `writeNextChapter` 前，先在心里跑这张清单——任何一项答"不"，**回去补，不许进 step 2**：

- [ ] 已读 `story/state/manifest.json#lastAppliedChapter` 算出 `chapterNo`？
- [ ] 已确认 `book.json` / `inkos.json` 存在、未在 `book_lock.py` 锁定状态？
- [ ] 上章的 `chapter-{NNNN-1}.delta.json` 已 apply 成功（`manifest.lastAppliedChapter == chapterNo - 1`）？
- [ ] 上章的 `audit_drift.md` / `docops_drift.json` 已生成（如有）——本章 Planner / Settler 要消费的喂料？
- [ ] 我清楚这一章是常规章 / 首章 / 卷尾 / 卷切——不同分支会触发 Architect / volume-payoff，不能漏判？
- [ ] 我准备好从 step 2 走到 step 11.05（含所有子步骤），不会"写完就交"提前退出？

进入主循环后，每个 step 进入前先跑 `loop_state.py require --step <id>`，完成后 `loop_state.py mark --step <id>`（invariant #11）。跳步会被 exit 3 阻止。

## 主循环（write-next）伪代码

```pseudocode
function writeNextChapter(book):
    # ── 1. 准备阶段 ────────────────────────────────────────
    loop_state.py begin --book <bd> --chapter chapterNo
    loop_state.py require --step 1
    book        = read book.json
    chapterNo   = read story/state/manifest.json#lastAppliedChapter + 1
    lengthSpec  = buildLengthSpec(book.chapterWordCount)
    loop_state.py mark --step 1

    # ── 2. Plan ────────────────────────────────────────────
    # references/phases/02-planner.md
    loop_state.py require --step 2
    chapterMemo = runPlanner(...)
    # 失败重试 ≤ 3 次（invariant #4 / #5）
    # 落盘：story/runtime/chapter_memo.md（YAML+md，下游脚本硬编码读这个名字）
    loop_state.py mark --step 2 --artifact story/runtime/chapter_memo.md

    # ── 3. Compose ─────────────────────────────────────────
    # references/phases/03-composer.md（无 LLM）
    loop_state.py require --step 3
    # 3a. 取滑窗记忆（避免 30+ 章后全量 chapter_summaries 爆 context）
    # 直接传 --memo，让脚本根据 frontmatter flag 自适应窗口
    memory = scripts/memory_retrieve.py --book <bd> \
                --current-chapter chapterNo \
                --memo story/runtime/chapter_memo.md
    # memo flag → 行为：
    #  isGoldenOpening → window-recent=2 window-relevant=0
    #  cliffResolution → --include-resolved-hooks (auto)
    #  arcTransition  → window-relevant=12 + --scan-volume-summaries (auto)
    #  volumeFinale   → window-relevant=0 (本卷视角)
    # 3b. 装配 context_pkg + rule_stack
    genreProfile = templates/genres/{book.genre}.md   # id 不在 catalog 回退 other.md
    contextPkg, ruleStack, trace = runComposer(chapterMemo, memory, genreProfile, truth files)
    # 落盘：story/runtime/chapter-{NNNN}.{context,rule-stack,trace}.json
    loop_state.py mark --step 3 --artifact story/runtime/chapter-{NNNN}.context.json

    # ── 4. (可选) Architect 回顾 ────────────────────────────
    # 触发：首章 + story_frame 占位 / 卷尾切换（memo.arcTransition）/ 用户"重做架构"
    # references/phases/04-architect.md；Foundation Reviewer 回环 ≤ 2 轮
    if needsFoundation:
        loop_state.py require --step 4
        runArchitect(book, fanficCanon if fanfic mode)
        # 4a. Architect cascade docOps（必跑；同步 current_focus / roles 等下游 md）
        if exists("story/runtime/architect-cascade.delta.json"):
            python scripts/apply_delta.py --book <bd> \
                --delta story/runtime/architect-cascade.delta.json \
                --skip-hook-governance --skip-commitment-ledger --skip-book-metadata
            mv .../architect-cascade.delta.json .../architect-cascade.applied-{NNNN}.delta.json
        # cascade 失败 → warning 不 abort；step 11.0c drift 兜底
        loop_state.py mark --step 4

    # ── 5. Write ───────────────────────────────────────────
    # references/phases/05-writer.md
    loop_state.py require --step 5
    rawWriter = runWriter(...)
    # 落盘：story/raw_writer/chapter-{NNNN}.md（保留所有 === BLOCK === sentinel）
    loop_state.py mark --step 5 --artifact story/raw_writer/chapter-{NNNN}.md

    # ── 5b. Parse Writer output（确定性 sentinel 解析）────────
    loop_state.py require --step 5b
    parsed = scripts/writer_parse.py --file story/raw_writer/chapter-{NNNN}.md --strict
    if parsed.exitCode != 0:
        # 缺 sentinel → 让 Writer 仅重输出缺失区块（≤ 2 次）
        # 仍失败 → fall back to lenient (drop --strict)，标 status=parser-failed-best-effort
        retry up to 2; then fall back to lenient
    draft = parsed.body
    # 落盘：story/runtime/chapter-{NNNN}.draft.md
    loop_state.py mark --step 5b --artifact story/runtime/chapter-{NNNN}.draft.md

    # ── 5c. Post-write validate（确定性机械层闸门）───────────
    # invariant #6
    loop_state.py require --step 5c
    pwv = scripts/post_write_validate.py --file story/runtime/chapter-{NNNN}.draft.md \
              --chapter chapterNo [--book <bd>]
    if pwv.exitCode == 2:    # critical：章节号自指 / 破折号 / 不是…而是… / 段落塌陷 / sentinel 残留 / 长度异常
        # 让 Writer 拿 pwv.issues 当反馈重写一次（最多 1 次，注入 postWriteFeedback —— invariant #5）
        rawWriter = runWriter(retry=true, postWriteFeedback=pwv.issues)
        重跑 5b / 5c
        if 仍 critical:
            return { status: "post-write-validate-failed", issues: pwv.issues }
    # warning 合并到后续 Auditor 的 issues 池
    loop_state.py mark --step 5c

    # ── 6. 长度治理（pre-audit normalize）─────────────────────
    # references/phases/08-normalizer.md
    loop_state.py require --step 6
    count = scripts/word_count.py --file draft --target ... --soft-min ... --soft-max ...
    if count.status not in {"in-soft"}:
        draft = runNormalizer(draft, lengthSpec)  # 单次修正，max 2 passes
    loop_state.py mark --step 6 --artifact story/runtime/chapter-{NNNN}.normalized.md

    # ── 7. Audit-Revise 回环（max 3 轮 + per-round artifact 持久化）───
    # references/phases/09-auditor.md + 10-reviser.md
    # 每轮的 audit + 闸门 + reviser 都落到 story/runtime/chapter-{NNNN}.audit-r{i}.json
    loop_state.py require --step 7
    iter = 0; lastScore = -1
    EPSILON = 3; PASS = 85; MAX_ITER = 3
    # 每轮入口必确认（防止漏跑确定性闸门）：
    #   · ai_tell_scan / sensitive_scan / commitment_ledger 三个脚本本轮**都会跑**（不是只第一轮）
    #   · i > 0 时 audit-r{i-1}.json 已落盘可读
    #   · i > 0 时 audit_round_log.py --analyze 已跑过取 stagnation/recurringIssues
    #   · 准备好把 detGates 整体拼回 allIssues
    while iter < MAX_ITER:
        # 7.0 跨轮分析
        stagnation = false; recurringIssues = []
        if iter > 0:
            ana = scripts/audit_round_log.py --book <bd> --chapter chapterNo --analyze
            stagnation = ana.stagnationDetected
            recurringIssues = ana.recurringIssues

        # 7a. 确定性闸门
        aiTells   = scripts/ai_tell_scan.py --file draft
        sensitive = scripts/sensitive_scan.py --file draft
        commitLedger = scripts/commitment_ledger.py \
                          --memo story/runtime/chapter_memo.md --draft draft \
                          --hooks <bd>/story/state/hooks.json --chapter chapterNo
        detGates = {
            ai_tells: ..., sensitive: ..., post_write: ...,
            fatigue: ..., commitment_ledger: commitLedger.violations,
        }

        # 7b. 语义审计——i > 0 时 Auditor 必须读 audit-r{i-1}.json
        audit = runAuditor(draft, contextPkg, fanficMode if any,
                           previousRound = read audit-r{iter-1}.json if iter > 0)
        allIssues = audit.issues + aiTells.issues + sensitive.issues_warn_or_block \
                  + commitLedger.violations
        passed = (audit.overall_score >= PASS
                  and count.status == "in-soft"
                  and sensitive.blocked == False
                  and no critical in allIssues)

        # 7c. 修订（仅 fail 时进）
        reviserAction = {mode: null, target_issues: [], outcome: "skipped"}
        if not passed:
            mode = chooseReviseMode(allIssues)
            # 7c.1 stagnation escalation：同一 critical issue 连续 2 轮没修掉
            #      → polish→rewrite，rewrite→rework；rework 仍 stagnation → 硬终止回环
            if stagnation:
                escalation = {"polish": "rewrite", "rewrite": "rework"}
                if mode in escalation:
                    mode = escalation[mode]
                elif mode == "rework":
                    log "audit-r{iter}: stagnation under rework, abort loop"
                    reviserAction = {mode: "rework", target_issues: [],
                                     outcome: "skipped-stagnation-abort"}
                    passed = false
                    break
            # 7c.2 Reviser 必须看过 previousRounds + recurringIssues
            previousRounds = scripts/audit_round_log.py --book <bd> \
                                --chapter chapterNo --list --json
            draft = runReviser(draft, allIssues, mode,
                               previousRounds = previousRounds.rounds,
                               recurringIssues = recurringIssues)
            reviserAction = {mode, target_issues, outcome: "applied"}
            # 7d. 修订后若长度漂移，再单次 normalize
            if length 漂出 soft range:
                draft = runNormalizer(draft, lengthSpec)

        # 7e. 持久化本轮 artifact
        roundJson = { chapter, round: iter, timestamp,
                      audit: {overall_score, passed, issues},
                      deterministic_gates: detGates,
                      reviser_action: reviserAction }
        scripts/audit_round_log.py --book <bd> --chapter chapterNo \
            --round iter --write /tmp/round-{iter}.json

        if passed: break
        # 7f. 增益检查
        improvement = audit.overall_score - lastScore
        if iter > 0 and improvement < EPSILON: break
        lastScore = audit.overall_score
        iter += 1
    loop_state.py mark --step 7

    # ── 7.5. audit-revise 退出后写入 chapters/index.json ──────
    loop_state.py require --step 7.5
    analysisJson = scripts/audit_round_log.py --book <bd> \
                       --chapter chapterNo --analyze --json
    scripts/chapter_index.py --book <bd> update \
        --chapter chapterNo \
        --audit-round-analysis "$analysisJson"
    # 失败 advisory，不阻断主循环
    loop_state.py mark --step 7.5

    # ── 8. Observe ─────────────────────────────────────────
    # references/phases/06-observer.md
    loop_state.py require --step 8
    observations = runObserver(draft, current truth files)
    # 落盘：story/runtime/observations.md（覆盖式）
    loop_state.py mark --step 8 --artifact story/runtime/observations.md

    # ── 9. Settle (产出 RuntimeStateDelta) ───────────────────
    # references/phases/07-settler.md；invariant #7（主动 5 项铁律）
    loop_state.py require --step 9
    # 9.1 消费上章 drift（如存在）→ 本章必须显式处置（改 / 不改写明理由）
    # 9.2 主动 5 项：current_focus / character_matrix / emotional_arcs / subplot_board / roles
    # 9.3 docOps 字段必填（哪怕 `{}`）
    delta = runSettler(draft, observations, current truth files,
                       priorDrift=story/runtime/docops_drift.json)
    # 落盘：story/runtime/chapter-{NNNN}.delta.json
    loop_state.py mark --step 9 --artifact story/runtime/chapter-{NNNN}.delta.json

    # ── 10. 校验并应用 delta（确定性 + hook 治理闸门）──────────
    # invariant #1
    loop_state.py require --step 10
    result = scripts/apply_delta.py --book <bd> --delta story/runtime/chapter-{NNNN}.delta.json
    if result.exitCode != 0:
        # delta 不合规 schema → 退回 settler 重写一次
        # result.hookGovernanceBlocked == True → 治理 critical → Settler 改 delta
        # 原子保证：apply_delta 任一阶段失败 → 真理文件回到应用前状态
        # Settler 重跑必须注入 result.parserFeedback / governanceFeedback（invariant #5）
        delta = runSettlerWithRetry(governanceFeedback=result.hookGovernance.validate.issues)
        retry once
    loop_state.py mark --step 10

    # 10.1 Hook seed → ledger 推升（4 条 OR 条件）
    loop_state.py require --step 10.1
    scripts/hook_governance.py --book <bd> --command promote-pass --current-chapter chapterNo
    loop_state.py mark --step 10.1

    # ── 10.5. （可选）Polisher 文字层打磨 ───────────────────────
    # references/phases/11-polisher.md；invariant #10
    POLISH_THRESHOLD = 88
    if passed and audit.overall_score >= POLISH_THRESHOLD:
        loop_state.py require --step 10.5
        polishResult = runPolisher(draft, chapterMemo, genreProfile.fatigueWords, book.language)
        if polishResult.changed:
            write story/runtime/chapter-{NNNN}.pre-polish.md = draft   # 备份原版
            postScan = ai_tell_scan + sensitive_scan on polishResult.polishedContent
            if postScan introduced new critical/block:
                draft 保持原状; log "polish-reverted-introduced-issues"
            else:
                draft = polishResult.polishedContent
        loop_state.py mark --step 10.5

    # ── 11. 最终落盘章节正文 + 章节运营索引 ──────────────────
    # 命名硬约束：最终章节文件**只能**是 `chapters/{NNNN}.md` 或 `chapters/{NNNN}_<title>.md`。
    # **禁止**把 runtime 阶段的 `chapter-{NNNN}.<phase>.md` 命名搬进 chapters/。
    loop_state.py require --step 11
    write chapters/{NNNN}.md = draft
    update story/state/manifest.json#lastAppliedChapter = chapterNo

    # 11.0 写章节运营索引（chapters/index.json）
    chapterStatus = passed ? "ready-for-review" : "audit-failed"
    auditIssuesFmt = [f"[{i.severity}] {i.description}" for i in allIssues]
    scripts/chapter_index.py --book <bd> add \
        --chapter chapterNo --status chapterStatus --title <title> \
        --word-count finalWordCount --audit-issues <JSON-encoded auditIssuesFmt> \
        [--length-warnings <JSON if any>] [--token-usage <JSON if tracked>] \
        [--review-note "polish-reverted-introduced-issues" if applicable]
    # 退出码非 0 → warning 不 abort（章节正文已落盘）
    loop_state.py mark --step 11 --artifact chapters/{NNNN}.md

    # ── 11.0a 状态快照（rollback 兜底）──────
    # references/state-snapshots.md
    loop_state.py require --step 11.0a
    python scripts/snapshot_state.py --book <bd> create --chapter chapterNo
    # 失败非 fatal
    loop_state.py mark --step 11.0a

    # ── 11.0b 审计纠偏喂料（audit drift → 下章 Planner）────────
    # references/audit-drift.md
    loop_state.py require --step 11.0b
    finalAuditIssues = audit.issues
    write story/runtime/chapter-{NNNN}.audit-final-issues.json = finalAuditIssues
    python scripts/audit_drift.py --book <bd> write \
        --chapter chapterNo \
        --issues story/runtime/chapter-{NNNN}.audit-final-issues.json \
        [--lang en if book.language == "en"]
    # 失败非 fatal
    loop_state.py mark --step 11.0b

    # ── 11.0c docOps 漂移扫描（指导 md 是否陈旧）─────────────
    # 扫近 6 章 summaries，flag "应改未改"的指导 md，写 advisory 到 story/runtime/docops_drift.json
    loop_state.py require --step 11.0c
    python scripts/docops_drift.py --book <bd> --window 6 --write
    # 消费规则：下章 Settler 必须**显式处置**每条候选——见 07-settler.md
    loop_state.py mark --step 11.0c

    # ── 11.05 Chapter Analyzer（post-persist 单向只读回顾）─────
    # references/phases/13-analyzer.md；invariant #9
    loop_state.py require --step 11.05
    analysis = runChapterAnalyzer(...)
    # 解析失败重试 ≤ 2，仍失败写 stub（invariant #4）
    loop_state.py mark --step 11.05 --artifact story/runtime/chapter-{NNNN}.analysis.json

    # ── 11.2 卷尾 cross-volume payoff 验证（仅卷末章触发）────────
    isVolumeFinale = (volume_map.endCh == chapterNo)
    if isVolumeFinale:
        loop_state.py require --step 11.2
        vp = scripts/hook_governance.py --command volume-payoff --volume volumeNumber
        if vp.issues 含 critical:
            chapter_index.py set-status --chapter chapterNo \
              --review-note "volume-payoff: <vp.summary>"
        # 没有 volume_map → 脚本 graceful 跳过
        loop_state.py mark --step 11.2

    # ── 11.1 (可选) Consolidate 建议（不擅自跑）──────────
    # references/phases/12-consolidator.md
    loop_state.py require --step 11.1
    cc = scripts/consolidate_check.py --book <bd>
    if cc.shouldConsolidate:
        # 向用户提示，**点头才进 phase 12**（严禁自动跑）
        notify user
    loop_state.py mark --step 11.1

    # 章结束
    loop_state.py end --book <bd> --chapter chapterNo
    return {
        chapterNumber: chapterNo,
        title, wordCount, auditResult: audit,
        revised: iter > 0,
        status: passed ? "ready-for-review" : "audit-failed-best-effort"
    }
```

## 路由

路由表见 [SKILL.md §决策路由](../../SKILL.md) 与 [§单点指令速查](../../SKILL.md)。本文件不复述。

## 何时**跳过**主循环

- 用户只是问"现在写到哪了"——直接读 manifest 答；
- 用户只是要看某个真理文件——直接 cat；
- 用户给的是**单点修改请求**（"把第 3 章那句改成 X"）——用 reviser 的 `spot-fix` 模式；
- 用户在调风格、建项目、抽 canon——这些是侧流，不是主循环。

## 运行时遗留物清理

每章完成（step 11 落盘 + `loop_state.py end` 后），可保留 `story/runtime/chapter-{NNNN}.*` 作为审计证据；7 天后或下一卷开始时可手动归档。SKILL 不主动清理。
