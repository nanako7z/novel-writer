# Phase 09 — Auditor（章节审计）

> 端口自 `inkos` 的 `ContinuityAuditor`（`packages/core/src/agents/continuity.ts`）。本阶段是 audit-revise 闭环的判官：跑 37 维度审查，输出结构化 JSON，决定是否进入 reviser，或直接落盘。

## 何时进入

- write（05）→ observe（06）→ settle（07）→ normalize（08）完成之后；
- 在 reviser（10）之前；
- 失败后从 reviser 出来再回到本阶段，最多 3 轮（见下文 audit-revise 闭环）。

## Inputs

Auditor 进入时必须能读到下列文件 / 上下文：

- 章节正文：normalize 阶段产出的当前 draft（已落到 `chapters/<NNNN>.md` 或 working buffer 中）
- 真理文件（按 inkos 原 reader 顺序）：
  - `story/state/current_state.json`（fallback：`story/current_state.md`）
  - `story/particle_ledger.md`（仅当 GenreProfile.numericalSystem=true 时进 prompt）
  - `story/pending_hooks.md`
  - `story/style_guide.md`（缺失时退回 `book_rules.md` 的 body）
  - `story/subplot_board.md`
  - `story/emotional_arcs.md`
  - `story/character_matrix.md`
  - `story/chapter_summaries.md`
  - `story/parent_canon.md`（spinoff 模式）
  - `story/fanfic_canon.md`（fanfic 模式）
  - `story/volume_map.md`
- 上一章全文（用于衔接检查）：`chapters/<NNNN-1>.md`
- 规则与设定：`GenreProfile`（含 fatigueWords、satisfactionTypes、auditDimensions、eraResearch、numericalSystem、language）+ `book_rules.md` 的 frontmatter（含 protagonist、eraConstraints、additionalAuditDimensions、fanficMode、allowedDeviations）
- Planner 产物：`ChapterMemo`（goal + body）、`ChapterIntent`（含 mustKeep/mustAvoid/styleEmphasis）
- Composer 产物：`ContextPackage`（selectedContext）、`RuleStack`（layers/sections/activeOverrides）
- 确定性闸门结果（在 LLM audit 之前已经跑过）：
  - `scripts/ai_tell_scan.py` → 单章 AI 味 issue 列表
  - `scripts/fatigue_scan.py` → 跨章长跨度疲劳 issue 列表（本章前 N 章窗口扫描，详见 [references/long-span-fatigue.md](../long-span-fatigue.md)）
  - `scripts/sensitive_scan.py` → 三级敏感词命中
  - `scripts/commitment_ledger.py` → planner 在 `## 本章 hook 账` 段声明的 advance/resolve 是否在 draft 中真的兑现。critical violation（类别 `hook 账未兑现` / `committedToChapter 未兑现`）作为 **load-bearing** 输入合并进 audit issues——不是 advisory，reviser 必须在下一轮把缺失的落地动作补回正文。详见 [references/hook-governance.md §8c](../hook-governance.md#8c-章节-hook-账commitment-ledger)。标准调用：

    ```bash
    python {SKILL_ROOT}/scripts/commitment_ledger.py \
      --memo story/runtime/chapter_memo.md \
      --draft story/runtime/chapter-{NNNN}.draft.md \
      --hooks <bookDir>/story/state/hooks.json \
      --chapter <chapterNumber>
    ```

    在 deterministic gate 链中位置：`sensitive_scan` 之后、LLM auditor 之前。命中 critical 不阻断本章主循环（reviser 会补救），但若 audit-revise 三轮后仍命中 critical → 章节标 `audit-failed-best-effort` 并把对应 issue 完整写进 chapters/index.json。
  - `scripts/word_count.py` → 章节字数 vs `LengthSpec`（target / softMin / softMax / hardMin / hardMax）

跑 `fatigue_scan.py` 的标准调用：

```bash
python {SKILL_ROOT}/scripts/fatigue_scan.py \
  --book <bookDir> --current-chapter N \
  --window 5 --genre-fatigue-words \
  [--draft <path-to-current-draft-if-not-yet-on-disk>]
```

把它返回的 `issues` 列表合并进 audit 的 `issues`：
- `severity=critical`（连续 4+ 章模式重复 / 同形态冲突堆叠）→ 作为 dim 25（节奏失控）/ dim 26（章节字数 / 标题疲劳）的硬证据，触发 Reviser 的 `polish` 或 `rework` 模式。
- `severity=warning` → 进 dim 25/26 的旁证，不强制 fail。
- `severity=info` → 仅记录到 audit summary，不触发任何动作。

跨章疲劳与单章 AI 味是互补关系：`ai_tell_scan` 抓"同一章里 hedge 多 / 转折词重复"，`fatigue_scan` 抓"5 章里同一句式反复 / 同一题材疲劳词反复 / 同一开篇模式连用"。两者的 issue 都进 reviser 的输入，但两者都**不替代**Auditor 对 dim 25/26 的语义判断（节奏感由 LLM 综合）。

## Process

### 1. 装配 prompt

按 inkos 原结构组装：system prompt（题材标签 + 主角锁 + 联网搜索许可 + 维度清单 + 输出格式 + 评分校准）+ user prompt（当前状态卡 / 资源账本 / 伏笔池 / 支线进度板 / 情感弧线 / 角色矩阵 / 章节摘要 / canon 参照 / 章节备忘 / 控制输入 / 文风指南 / 上一章全文 / 待审章节）。

`book_rules.protagonist` 存在时拼出"主角人设锁定：…，行为约束：…"段；`GenreProfile.eraResearch=true` 时追加联网搜索段。

### 2. Auditor system prompt（中文版，逐字搬自 `continuity.ts` L494-530）

> 你是一位严格的{gp.name}网络小说结构审稿编辑。你只审完成度 + 结构，不审文笔。{protagonistBlock}{searchNote}
>
> ## 审稿边界（硬约束）
>
> 你不审文笔、不审排版、不审句式——这些归 Polisher。你发现的文笔问题只能以 severity="info" 标注供 Polisher 参考，不计入 reviewer 的 passed/overall_score，也绝不可标为 critical。
>
> 你审 12 条结构类雷点：开篇拖沓/平淡、世界观模糊脱现实、人设矛盾、视角杂乱、主线偏离/停滞、冲突乏力爽点缺失、节奏失控过渡生硬、人设前后矛盾、人物单薄无反差、情感表达生硬/关系突兀、金手指失衡、设定无落地。同时保留工程维度（OOC、timeline 一致、信息越界、hook-debt、跨章重复、词汇疲劳、章节字数、标题疲劳、段落形状）。
>
> 稀疏 memo 是合法状态。喘息章 / 后效章 / 过渡章的 memo 可以只有 goal + 骨架 body——此类 memo 不判 incomplete，也不能因为 memo 没写的段落就扣成稿的分。只按 memo 实际写出来的内容判偏离。
>
> 审查维度：
> {dimList}
>
> 输出格式必须为 JSON：
> {
>   "passed": true/false,
>   "overall_score": 0-100,
>   "issues": [
>     {
>       "severity": "critical|warning|info",
>       "category": "审查维度名称",
>       "description": "具体问题描述",
>       "suggestion": "修改建议"
>     }
>   ],
>   "summary": "一句话总结审查结论"
> }
>
> 只有当存在 critical 级别问题时，passed 才为 false。
>
> overall_score 评分校准：
> - 95-100：可直接发布，无明显问题
> - 85-94：有小瑕疵但整体流畅可读，读者不会出戏
> - 75-84：有明显问题但故事主干完整，需要修但不紧急
> - 65-74：多处影响阅读体验的问题，节奏或连续性有断裂
> - < 65：结构性问题，需要大幅重写
> 综合评分，不要因为单一小问题大幅拉低分数。

英文 prompt（逐字版本见 `continuity.ts` L457-493）保持等价。Claude 在执行本阶段时按 `book.json#language` 选择中/英 system prompt。

### 2.5 题材维度过滤（Genre Profile）

Auditor 进入时按 `references/phases/05-writer.md §11.5` 同样的 loader 读 `templates/genres/<book.genre>.md`（项目级 override 优先），把 `gp.auditDimensions` 与 `gp.numericalSystem` / `gp.powerScaling` / `gp.eraResearch` 三个 toggle 全部装进 prompt 装配：

1. **维度白名单**：把全局 37 维清单与 `gp.auditDimensions` 数组**取交集**——只有 id 同时在两边的维度才进 system prompt 的"审查维度"列表。例如 `xianxia.md` 的 `auditDimensions: [1,2,3,4,5,6,7,8,9,10,11,13,14,15,16,17,18,19,24,25,26]` 会让 dim 12（年代考据）、dim 20-23（段落 / 套话 / 转折 / 列表）、dim 27（敏感词 LLM 审）都不进 prompt（敏感词仍由确定性脚本兜底）。
2. **三个 toggle 的强制激活**（独立于白名单，最终是"白名单 ∪ 强制激活 ∩ mode 触发"）：
   - `gp.numericalSystem == true` → 强制激活 dim 5（数值检查）；同时让 user prompt 中追加 `particle_ledger.md` 区块。
   - `gp.powerScaling == true` → 强制激活 dim 4（战力崩坏）；user prompt 把 `current_state.json#power level` 单独抽出来。
   - `gp.eraResearch == true` → 强制激活 dim 12（年代考据）；同时在 system prompt 注入"联网搜索许可"段（允许使用 search_web / fetch_url，要求 ≥ 2 来源交叉）。
3. **mode 后置过滤**：上面两步取出的活跃集再按 mode 过滤（参考 `references/audit-dimensions.md` 的 Dimension activation matrix）：
   - 非 spinoff 关闭 28-31；非 fanfic 关闭 34-37；fanfic 模式自动关 28-31 同时开 34-37。
   - dim 32-33 永远 always-active（universal），不受白名单约束。
4. **`gp.satisfactionTypes` / `gp.fatigueWords`**：不进维度白名单，但作为对应维度的"判定参照"塞进 `buildDimensionNote`：
   - dim 10（词汇疲劳）的 note 拼出 `gp.fatigueWords`（叠加全局 AI 标记词），单章 >1 次即 warning。
   - dim 15（爽点虚化）的 note 拼出 `gp.satisfactionTypes`，要求 Auditor 检查本章爽点是否在清单内，并且兑现度是否超过 70% 期待。
5. **加载失败回退**：`gp` 解析失败 → 退到 `other.md`，但要在 audit summary 末尾标注 `genre-fallback=other`，提醒用户 `book.json#genre` 配错。

> 完整字段语义、自定义题材方法、catalog 列表见 `references/genre-profile.md`；37 维各条触发条件见 `references/audit-dimensions.md`（其"适用条件"列必须**与 `gp.auditDimensions` 取交集**后再用）。

### 3. 37-dimension 评估

Auditor 不是固定跑 37 条，而是按 mode 动态构建活跃维度集。完整维度名 / 真理文件 / 检查要点 / 模式触发表见：

→ `references/audit-dimensions.md`

要点：

- 维度 1-27：通用核心（按 GenreProfile.auditDimensions 子集启用）；
- 维度 12「年代考据」：`gp.eraResearch=true` 或 `bookRules.eraConstraints.enabled=true` 才触发；
- 维度 28-31：仅 spinoff（`story/parent_canon.md` 存在 **且** 不在 fanfic 模式）；
- 维度 32-33：始终激活（读者期待管理 / 章节备忘偏离），inkos 原文是 universal always-active；
- 维度 34-37：仅 fanfic（`story/fanfic_canon.md` 存在 + `bookRules.fanficMode` ∈ {canon, au, ooc, cp}），同时把 28-31 关闭。

每条维度在 prompt 里渲染为 `<id>. <中文名>（<note>）`，note 由 `buildDimensionNote` 按 mode 动态生成，注入题材疲劳词、爽点类型、年代约束、fanfic 严重度等上下文。

### 4. 通过判定（Pass criteria）

inkos 在 `chapter-review-cycle.ts` L33-35 与 L202-203 中硬编码：

- `MAX_REVIEW_ITERATIONS = 3`
- `PASS_SCORE_THRESHOLD = 85`
- `NET_IMPROVEMENT_EPSILON = 3`

`isPassed` 同时要求 4 个条件全部成立才算过：

1. `auditResult.passed === true`（即 LLM 没报 critical，且无 post-write critical，且无 block 级敏感词）；
2. `overall_score >= 85`；
3. `lengthInRange`（章节字数在 `LengthSpec` 的 softMin..softMax 内，由 `word_count.py` 判定）；
4. 无 block 级敏感词（`sensitive_scan.py` severity=block 命中数 == 0）—— 这条会强制把 `passed` 拉成 false。

注：AI 味 issue 与 post-write check（章节引用、段落形状等）也合并进 `auditResult.issues`，作为 reviser 的输入；但只有 `severity=critical` 的 post-write issue 才会强制 fail。

### 4.1 Per-round artifact（i > 0 时必读）

audit-revise 闭环每一轮（含 round 0 初评）都会被 orchestration 落到
`story/runtime/chapter-{NNNN}.audit-r{i}.json`（schema 见
[references/schemas/audit-result.md §10](../schemas/audit-result.md#10-audit-r-单轮-artifact)，写盘工具
`scripts/audit_round_log.py`）。

**进入 round i (i > 0) 的 Auditor 必须**：

1. 读 `chapter-{NNNN}.audit-r{i-1}.json`，拿到上一轮报过的 `audit.issues`
   清单（特别是 `severity=critical` 的那些）；
2. 在评估当前 draft 时，对每条上一轮的 critical issue 单独判断"还在不在"——
   - **仍在**：把它原样合并进本轮 `issues` 数组，并保留为 `severity=critical`，
     额外在 `description` 末尾追加 `（上一轮已报，未修复）` 标记；
   - **已消失**：不再报，但本轮 `summary` 里要点名"上一轮 N 条 critical
     已 K 条修掉"；
   - **变形（同语义不同表述）**：合并到现有条目并追加 `（同原 critical 重述）`，
     不要拆成新条。

这条契约保证：当 reviser 在 round i 改完后没真正解决某个 critical，本轮 Auditor
不会"忘了" round i-1 的判定，从而给出错误的高分。orchestration 的 stagnation
detection（连续 2+ 轮同一 critical）由此命中，下一轮 reviser 会被升级模式
（polish → rewrite → rework）。

`audit-r{i-1}.json` 缺失（首轮、或被 `--clear` 过）→ 跳过本节，按普通初评做。

### 5. Audit-revise 闭环

```
draft ──► normalize（最多 2 次）─► assess #0
                                      │
                                      ├─ pass? ──► 落盘 ✓
                                      │
                                      └─ fail ──► revise → assess #1
                                                          │
                                                          ├─ pass? ──► 落盘
                                                          │
                                                          └─ score - prevScore < 3? ──► 提前退出（取最高分 snapshot）
                                                                                       │
                                                                                       └─ 否，继续 → revise → assess #2
                                                                                                                │
                                                                                                                └─ 第 3 轮强制结束，取最高分 snapshot
```

详细：

- `MAX_REVIEW_ITERATIONS = 3`：最多 3 轮 audit-revise（assess #0 是初评，不计入 revise 计数）；
- `NET_IMPROVEMENT_EPSILON = 3`：本轮 score − 上一轮 score < 3 即认为收益不显著，提前退出循环；
- 每轮的 (content, wordCount, auditResult, score) 都进 `snapshots[]`，最终落盘选 score 最高的那一份；
- 长度问题不进 reviser issue 列表，由 normalize 单独处理；
- 闭环退出后再做一次最终长度归一（如必要）。

## Output contract

Auditor 直接输出符合 inkos `AuditResult` 的 JSON：

```json
{
  "passed": false,
  "overall_score": 78,
  "issues": [
    { "severity": "critical", "category": "章节备忘偏离",
      "description": "章末未交付 memo 中 'goal: 揭示老李身份' 的兑现",
      "suggestion": "在最后一段补一句让老李说出真实身份" }
  ],
  "summary": "主线推进到位但 memo goal 未兑现，需 polish/spot-fix"
}
```

字段形状与解析容错（4 种 JSON 提取策略 + 字段级 regex fallback）见：

→ `references/schemas/audit-result.md`

落地路径：

- 工作 buffer：本轮的 audit JSON 留在内存供 reviser 读用，不落盘；
- 闭环结束后：把最终 snapshot 的 audit JSON 写入 `story/state/audit_log/<NNNN>.json`（可选，用于回溯 / 评测）。

## Failure handling

- **JSON 解析失败**：按 inkos 4 级降级（balanced JSON → 整段 JSON → ```json fence → 字段级 regex）；全部失败则返回 `passed=false` + 一条 `severity=critical, category=系统错误` 的 issue，并把章节标记为 needs-rerun。
- **LLM 拒答 / 空响应**：等同解析失败，重试 1 次（temperature 提到 0.5），仍失败则人工介入。
- **真理文件缺失**：用 `(文件不存在)` 占位，不阻塞 audit；但若 `current_state.md` 缺失，必须先跑 architect seed 派生（参 `readCurrentStateWithFallback`）。
- **闭环 3 轮仍未通过**：取所有 snapshot 中 score 最高者落盘，并在 `summary` 中标注 "exited at iter=N, best_score=X"，由用户决定是否再次手动改章。

## 注意事项

- Auditor 不审文笔。任何文笔/排版问题在 prompt 里被强制压到 `severity=info`，不计入 score、不进 critical。这是 inkos 的硬约束（参见 prompt 中"审稿边界（硬约束）"段）。
- Auditor 不修改任何真理文件。它只读不写；修改由 reviser（10）或 settler（07）发起。
- 联网搜索（`search_web` / `fetch_url`）仅在 `gp.eraResearch=true` 时启用，用于年代考据维度（dim 12）核实事实，要求 ≥ 2 个来源交叉验证。
- 稀疏 memo（喘息章 / 过渡章只有 goal + 骨架 body）是合法的；不要用 memo 没写的段落去扣成稿的分。
- fanfic 模式下，`book.json#fanficMode` 决定 dim 34-37 与 dim 1 的 severity 重映射，且 dim 28-31 自动关闭。详见 `references/audit-dimensions.md` 的「Dimension activation matrix」与「Fanfic-mode severity table」。
