# Writer Sub-Reference — 输出格式契约（OUTPUT FORMAT）

## 功能说明

本文件覆盖 Writer 系统 prompt 中**唯一一段直接决定 Writer-Parser 能否消费的 sentinel 输出契约**：

- **§14 输出格式契约**——Writer 必须严格按区块输出，便于 Writer-Parser、Observer、Settler 顺序消费。

`mode == "full"`（默认）走 14.A 全量版输出 11 个 sentinel 区块；`mode == "creative"` 走 14.B 精简版仅输出 3 个区块（PRE_WRITE_CHECK + CHAPTER_TITLE + CHAPTER_CONTENT），其余结算交给 Settler 统一处理。

**这是 SKILL 中 Writer 输出的唯一消费契约**——主循环 step 5b 调 `scripts/writer_parse.py` 把 Writer 输出按 sentinel 拆出 `{title, body, wordCount, summary, postWriteErrors, ...}`。任何 ad-hoc 自解析（让 LLM 自己读自己的输出）都会失败，会让 Auditor 把 sentinel 当作正文打分。

启用条件：恒启。`mode == "creative"` 用 14.B；其余用 14.A 全量版。

---

### 14. 输出格式契约（OUTPUT FORMAT）

**作用**：Writer 必须严格按以下区块输出，便于 **Writer-Parser**、Observer、Settler 顺序消费。每个 `=== BLOCK ===` 头**必须独占一行、必须左顶格、必须使用 verbatim 大写**，不得改名、不得加序号、不得加引号、不得替换为 markdown 标题。

> **下游解析点**：主循环在 step 5b 调 `scripts/writer_parse.py` 把这份输出按 sentinel 拆出 `{title, body, wordCount, summary, postWriteErrors, ...}`。该解析器是 SKILL 中 Writer 输出的**唯一**消费者；任何 ad-hoc 自解析（让 LLM 自己读自己的输出）会失败、会让 Auditor 把 sentinel 当作正文打分。
>
> Writer-Parser 接受的 sentinel verbatim 列表（其中 CHAPTER_TITLE / CHAPTER_CONTENT 必填）：
>
> ```
> === PRE_WRITE_CHECK ===
> === CHAPTER_TITLE ===          (required)
> === CHAPTER_CONTENT ===        (required)
> === POST_SETTLEMENT ===
> === UPDATED_STATE ===
> === UPDATED_LEDGER ===         (only when gp.numericalSystem)
> === UPDATED_HOOKS ===
> === CHAPTER_SUMMARY ===
> === UPDATED_SUBPLOTS ===
> === UPDATED_EMOTIONAL_ARCS ===
> === UPDATED_CHARACTER_MATRIX ===
> === POST_WRITE_ERRORS ===      (optional — Writer 自报本章违规)
> === HOOK_PAYOFF_AUDIT ===      (optional — Writer 自检 advance/resolve 兑现锚)
> ```
>
> 如果 Writer 自检中发现任何已经写入正文但来不及修的违规（如疲劳词超限、节奏漂移），可以把每条违规按一行一条写在末尾的 `=== POST_WRITE_ERRORS ===` 块里，下游 reviser 会读这份清单优先修。
>
> `=== HOOK_PAYOFF_AUDIT ===` 是 commit `ab39bd6` 落地的可选自检块——Writer 写完初稿后，把本章 `advance / resolve` 的每个 `hook_id` 列下来，对照正文找出"对应那段 ≥ 60 字、含可观察动作 / 对话 / 物件" 的 prose 锚段，把 anchor 文本的前 ≤ 80 字粘进去（不是再写一遍正文，是粘正文里某一段的开头）。Settler 拿到这个块直接对照，落空即 `dirty=true` 触发 Reviser；没有这个块时由 [`scripts/commitment_ledger.py`](../../scripts/commitment_ledger.py) 在正文里 fallback 搜锚。
>
> 一切对 sentinel 的"美化"——加 markdown `##`、加序号、改大小写、合并区块、把短的合并成 inline 行——都会让 `writer_parse.py --strict` 报错并触发回写循环。Writer 不要自作聪明。

**何时启用**：恒启（`mode == "creative"` 用精简版，省去 5 个 UPDATED_* 区块；其余用全量版）。

#### 14.A 全量输出（默认 `mode == "full"`）

```
## 输出格式（严格遵守）

=== PRE_WRITE_CHECK ===
（必须输出Markdown表格，全部检查项对齐 chapter_memo 七段，而不是卷纲）
| 检查项 | 本章记录 | 备注 |
|--------|----------|------|
| 当前任务 | 复述 chapter_memo 的「当前任务」并写出本章执行动作 | 必须具体，不能抽象 |
| 读者在等什么 | 本章如何处理「读者此刻在等什么」—制造/延迟/兑现 | 与 memo 一致 |
| 该兑现的 / 暂不掀的 | 本章确认要兑现的伏笔 + 必须压住不掀的底牌 | 引用 memo 原文 |
| 日常/过渡承担任务 | 若有日常/过渡段落，说明各自承担的功能 | 对齐 memo 映射表 |
| 章尾必须发生的改变 | 列出 memo「章尾必须发生的改变」中 1-3 条具体改变 | 必须落地 |
| 不要做 | 复述 memo「不要做」清单 | 正文不得触碰 |
| 上下文范围 | 第X章至第Y章 / 状态卡 / 设定文件 | |
| 当前锚点 | 地点 / 对手 / 收益目标 | 锚点必须具体 |
| 当前资源总量 | X | 与账本一致 |（仅 numericalSystem）
| 本章预计增量 | +X（来源） | 无增量写+0 |（仅 numericalSystem）
| 待回收伏笔 | 用真实 hook_id 填写（无则写 none） | 与伏笔池一致 |
| 本章冲突 | 一句话概括 | |
| 章节类型 | <gp.chapterTypes 拼接 "/"> | |
| 风险扫描 | OOC/信息越界/设定冲突<powerScaling? "/战力崩坏" : "">/节奏/词汇疲劳 | |

=== CHAPTER_TITLE ===
(章节标题，不含"第X章"。标题必须与已有章节标题不同，不要重复使用相同或相似的标题；若提供了 recent title history 或高频标题词，必须主动避开重复词根和高频意象)

=== CHAPTER_CONTENT ===
(正文内容，目标<lengthSpec.target>字，允许区间<lengthSpec.softMin>-<lengthSpec.softMax>字)

=== POST_SETTLEMENT ===
（如有数值变动 / 伏笔变动，必须输出Markdown表格）
| 结算项 | 本章记录 | 备注 |
|--------|----------|------|
| 资源账本 | 期初X / 增量+Y / 期末Z | 无增量写+0 |（仅 numericalSystem）
| 重要资源 | 资源名 -> 贡献+Y（依据） | 无写"无" |（仅 numericalSystem）
| 伏笔变动 | 新增/回收/延后 Hook | 同步更新伏笔池 |

=== UPDATED_STATE ===
(更新后的完整状态卡，Markdown表格格式)

=== UPDATED_LEDGER ===   ← 仅 numericalSystem
(更新后的完整资源账本，Markdown表格格式)

=== UPDATED_HOOKS ===
(更新后的完整伏笔池，Markdown表格格式)

=== CHAPTER_SUMMARY ===
(本章摘要，Markdown表格格式)
| 章节 | 标题 | 出场人物 | 关键事件 | 状态变化 | 伏笔动态 | 情绪基调 | 章节类型 |
|------|------|----------|----------|----------|----------|----------|----------|
| N | 本章标题 | 角色1,角色2 | 一句话概括 | 关键变化 | H01埋设/H02推进 | 情绪走向 | <gp.chapterTypes 拼接 "/" 或 "过渡/冲突/高潮/收束"> |

=== UPDATED_SUBPLOTS ===
(更新后的完整支线进度板)
| 支线ID | 支线名 | 相关角色 | 起始章 | 最近活跃章 | 距今章数 | 状态 | 进度概述 | 回收ETA |
|--------|--------|----------|--------|------------|----------|------|----------|---------|

=== UPDATED_EMOTIONAL_ARCS ===
(更新后的完整情感弧线)
| 角色 | 章节 | 情绪状态 | 触发事件 | 强度(1-10) | 弧线方向 |
|------|------|----------|----------|------------|----------|

=== UPDATED_CHARACTER_MATRIX ===
(更新后的角色矩阵，每个角色一个 ## 块)

## 角色名
- **定位**: 主角 / 反派 / 盟友 / 配角 / 提及
- **标签**: 核心身份标签
- **反差**: 打破刻板印象的独特细节
- **说话**: 说话风格概述
- **性格**: 性格底色
- **动机**: 根本驱动力
- **当前**: 本章即时目标
- **关系**: 某角色(关系性质/Ch#) | ...
- **已知**: 该角色已知的信息（仅限亲历或被告知）
- **未知**: 该角色不知道的信息

=== HOOK_PAYOFF_AUDIT ===   ← 可选；强烈建议 Writer 在 advance / resolve 非空时输出
（写完初稿后自检，把本章 advance / resolve 的每个 hook_id 列下来，对照正文找出 ≥ 60 字、含可观察动作 / 对话 / 物件 的 prose 锚段，粘锚段前 ≤ 80 字。仅纯内心提及不算兑现，必须能指到一段动作 / 对话）

| hook_id | 标签 | 锚段前 80 字（粘正文片段，不要再写一遍） |
|---------|------|------------------------------------------|
| H001 | advance | 主角伸手翻开父亲留下的旧信，手指在泛黄的纸页上停了停... |
| H002 | resolve | 他握住玉牌，玉牌上的纹路在月光下泛冷，他对身边的师弟说... |
```

#### 14.B Creative-only 输出（`mode == "creative"`）

仅输出三个区块（PRE_WRITE_CHECK + CHAPTER_TITLE + CHAPTER_CONTENT），其余结算交给 Settler 统一处理。Verbatim 末尾提示：

```
【重要】本次只需输出以上三个区块（PRE_WRITE_CHECK、CHAPTER_TITLE、CHAPTER_CONTENT）。
状态卡、伏笔池、摘要等追踪文件将由后续结算阶段处理，请勿输出。
```

---

## 与上层 Writer 阶段的关系

在 Writer system prompt 拼装顺序中：

- **§14 是最后一段**——所有规则、风格、题材、主角、fanfic、style 都注入完毕之后，最后用 §14 把"输出形态"定死。这个顺序的原因：sentinel 契约必须在 prompt 末尾，确保 Writer 在生成时把"输出格式"当成最近的指令；如果 §14 提前注入，Writer 容易在中间段被风格脊梁段（[craft-and-anti-ai](./craft-and-anti-ai.md) §5 "never list"）"误导"成不输出列表式 sentinel。

**关键不变量**：
- sentinel 头必须 verbatim 大写、独占一行、左顶格、不加 markdown `##`；
- `CHAPTER_TITLE` + `CHAPTER_CONTENT` 是必填，缺任一即触发 Writer-Parser 错误；
- `UPDATED_LEDGER` 仅在 `gp.numericalSystem == true` 时输出（与 [genre-injection §11.5](./genre-injection.md) 联动）；
- 全员追踪开启时（[book-rules §13](./book-rules.md)），POST_SETTLEMENT 多三段角色清单。

下游消费链：
1. `scripts/writer_parse.py --strict` 拆 sentinel；
2. Observer (06) 读 UPDATED_STATE / UPDATED_HOOKS / CHAPTER_SUMMARY 抽事实；
3. Settler (07) 把 Observer 的事实落到真理文件；
4. 正文 CHAPTER_CONTENT 经 Auditor (09) 审稿、Reviser (10) 修订、Polisher (11) 打磨之后落到 `chapters/<NNNN>.md`。

回主文件参见 [phases/05-writer.md](../phases/05-writer.md)。
