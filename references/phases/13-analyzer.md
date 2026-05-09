# Phase 13: Chapter Analyzer（章节定性回顾 / 下章 Planner 喂料）

> ⛔ **硬约束 / 不跳步**：
> 1. **前置**：本章已 `chapters/{NNNN}.md` 写盘 + `audit-r{i}.json` / `observations.md` 已落；step 11.05 必跑——**不允许**直接不调用
> 2. **本阶段必跑**：单向只读——**禁止**改 `chapters/*` / `story/state/*` / `pending_hooks.md`（与 Settler 的事实增量分工）；产出仅作为下章 Planner 的"读起来怎么样"喂料
> 3. **退出条件**：`story/runtime/chapter-{NNNN}.analysis.json` 落盘（解析失败也要写 stub，不阻断主循环）
> 4. **重试规则**：解析失败 ≤ 2 次重输；仍失败写 stub，不算 fatal

## 何时进入

主循环 step 11.05——章节正文已落盘到 `chapters/{NNNN}.md`、真理文件已 `apply_delta.py` 写入、Polisher 跑完之后；下一章 [Planner](02-planner.md) 还没启动之前（见 [00-orchestration.md](00-orchestration.md)）。本章一切定局，正文 / 真理文件 / polish 都不会再动。

Chapter Analyzer 的角色是**单向只读的复盘者**：它读已经定稿的章节正文与本章的 `chapter_memo.md`、audit 结果、题材 profile，做一次定性回顾，把"本章击中了哪些满足类型 / 触发了哪些节奏拍 / 留下了什么可复用桥段 / 下章 Planner 必须正面回应的信号"产出为结构化分析文件。**绝不修改章节正文、`story/state/*.json`、`pending_hooks.md` 或任何真理文件**。

> 注：本阶段是 novel-writer SKILL 的扩展点（inkos 源 `chapter-analyzer.ts` 是连续性事实抽取角色，本阶段在此基础上重新定位为下章 Planner 的定性输入），与 Settler（事实增量）、Observer（事实穷举）职责正交：Settler/Observer 关心"发生了什么"，Analyzer 关心"读起来怎么样、对下一章有什么交代"。

## Inputs

Claude 在这一阶段需要读：

- `chapters/{NNNN}.md` ——本章**最终发布版**正文（Polisher 之后；不是 runtime 草稿）
- `story/runtime/chapter_memo.md` ——本章的 chapter_memo（goal / threadRefs / 该兑现的 / hook 账 / 不要做）。Planner 覆盖式写入，下游脚本硬编码读此名
- `story/runtime/chapter-{NNNN}.audit.json` 或最后一轮 audit 结果 ——37 维评分与 issue 列表（可选）
- `book.json` / `inkos.json` ——chapterNumber、language、genre、chapterWordCount
- `templates/genres/<book.genre>.md` 或 `references/genre-profile.md` ——拿到本题材的 `satisfactionTypes` 目录、`fatigueWords` 词表、`pacingRule` 拍点定义、`chapterTypes` 词汇
- `story/runtime/observations.md`（可选）——Observer 抽出的 9 类事实，作为定性归类的事实底座
- `story/state/hooks.json` ——本章前的 hook 池快照（用于判断哪些 hook 已连续 N 章未推进）

不读上一章的 analysis 文件——Analyzer 是单章定性，不做跨章聚合（聚合由 `scripts/analyzer_index.py` 单独负责）。

## Process

Claude 在心中扮演"小说连续性分析师"，按下面的系统 prompt 执行。**只读不写真理文件**。

### 系统 prompt（搬自 inkos `chapter-analyzer.ts` `buildSystemPrompt` 中文分支 L333-L433，请 Claude 在心中扮演这个角色）

```
你是小说连续性分析师。你的任务是分析一章已完成的小说正文，从中提取所有状态变化并更新追踪文件。

## 工作模式

你不是在写作，而是在分析已有正文。你需要：
1. 仔细阅读正文，提取所有关键信息
2. 基于"当前追踪文件"做增量更新
3. 输出格式与写作模块完全一致

## 分析维度

从正文中提取以下信息：
- 角色出场、退场、状态变化（受伤/突破/死亡等）
- 位置移动、场景转换
- 物品/资源的获得与消耗
- 伏笔的埋设、推进、回收
- 情感弧线变化
- 支线进展
- 角色间关系变化、新的信息边界

## 书籍信息

- 标题：${book.title}
- 题材：${genreProfile.name}（${book.genre}）
- 平台：${book.platform}
${numericalBlock}

## 题材特征

${genreBody}

${bookRulesBody ? `## 本书规则\n\n${bookRulesBody}` : ""}

## 输出格式（必须严格遵循）

使用 === TAG === 分隔各部分，与写作模块完全一致：

=== CHAPTER_TITLE ===
（从正文标题行提取或推断章节标题，只输出标题文字）

=== CHAPTER_CONTENT ===
（原样输出正文内容，不做任何修改）

=== PRE_WRITE_CHECK ===
（留空，分析模式不需要写作自检）

=== POST_SETTLEMENT ===
（留空，分析模式不需要写后结算）

=== UPDATED_STATE ===
更新后的状态卡（Markdown表格），反映本章结束时的最新状态：
| 字段 | 值 |
|------|-----|
| 当前章节 | {章节号} |
| 当前位置 | ... |
| 主角状态 | ... |
| 当前目标 | ... |
| 当前限制 | ... |
| 当前敌我 | ... |
| 当前冲突 | ... |

=== UPDATED_LEDGER ===
（如有数值系统：更新后的完整资源账本表格；无则留空）

=== UPDATED_HOOKS ===
更新后的伏笔池（Markdown表格），包含所有已知伏笔的最新状态：
| hook_id | 起始章节 | 类型 | 状态 | 最近推进 | 预期回收 | 回收节奏 | 备注 |

=== CHAPTER_SUMMARY ===
本章摘要（Markdown表格行）：
| 章节 | 标题 | 出场人物 | 关键事件 | 状态变化 | 伏笔动态 | 情绪基调 | 章节类型 |

=== UPDATED_SUBPLOTS ===
更新后的支线进度板（Markdown表格）

=== UPDATED_EMOTIONAL_ARCS ===
更新后的情感弧线（Markdown表格）

=== UPDATED_CHARACTER_MATRIX ===
更新后的角色矩阵（每个角色一个 ## 块，字段用 bullet list）：

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

（每个角色重复以上格式。新角色追加新 ## 块，已有角色做增量更新。）

## 关键规则

1. 状态卡和伏笔池必须基于"当前追踪文件"做增量更新，不是从零开始
2. 正文中的每一个事实性变化都必须反映在对应的追踪文件中
3. 不要遗漏细节：数值变化、位置变化、关系变化、信息变化都要记录
4. 角色矩阵中的"已知/未知"要准确——角色只知道他在场时发生的事
```

> 英文书：用 inkos 源同函数的 English 分支（L225-L326），结构 1:1 对应。

### 在 SKILL 内的角色复用（重要）

inkos 源里 ChapterAnalyzer 的 === TAG === 输出主要驱动 Settler-like 的真理文件回写。在 novel-writer SKILL 里：

- 真理文件回写已由 phase 06 Observer + phase 07 Settler + `scripts/apply_delta.py` 完成（在 step 8/9）。
- 本阶段（phase 13）**不再回写真理文件**，而是把"已完成连续性分析"的同一份理解，**蒸馏成一份定性 JSON**，作为下一章 Planner 的喂料。
- Claude 内部仍可按上述 === TAG === 系统 prompt 跑一遍连续性扫描（保证理解深度），但**只输出**下面定义的 JSON 结构到 `story/runtime/chapter-{NNNN}.analysis.json`，并把 === TAG === 块丢弃。

### 工作步骤

1. **采集材料**。按 Inputs 顺序读文件；不存在视作空，不要伪造。
2. **跑一遍系统 prompt**。在心中扮演连续性分析师，对本章做一次完整的事实扫描——这一步是为了"读懂"本章。
3. **对照 chapter_memo 复盘**：
   - `chapter_memo.goal` 是否兑现？
   - `chapter_memo.该兑现的` 列表逐条核对：兑现 / 部分兑现 / 跳票
   - `chapter_memo.hook 账（advance/resolve/defer）` 逐条核对：是否落地？落地程度？
   - `chapter_memo.不要做` 是否被踩雷？
4. **匹配题材 satisfactionTypes**。读题材 profile 的 `satisfactionTypes`（如「悟道突破」「身份揭示」「装逼打脸」「破阵」「升级反馈」等），对照本章实际发生的事，列出**击中**的类型。不"硬套"——没击中就空数组。
5. **抽 pacingBeats**。按题材 `pacingRule`（如"开篇悬念—中段反转—结尾钩子"），定位本章实际的拍点：每个拍点写 `{position, type, note}`，position 取「开篇 / 前段 / 中段 / 后段 / 结尾」之一。
6. **找 reusableMotifs**。识别本章用得好的桥段、意象、对话节奏，能在后续章节复用的（如"用一个具体物件承载情绪转折"、"反派以礼貌句式说狠话"）。
7. **抽 characterArcDeltas**。每个出场主要角色一行，描述这章心境/立场/关系的位移（"从迷茫到决心"、"对师兄从信任到怀疑"）。
8. **生成 warningsForNextChapter**。这是给下章 Planner 的硬警报，触发条件示例：
   - 某 hook 已连续 ≥ 3 章未推进 → "H001 已连续 3 章未推进，下章必须推一下"
   - chapter_memo 该兑现的有跳票 → "上章承诺的 X 未兑现，下章必须补"
   - audit 中 critical issue 未被修订完 → "AI 味集中在 X 段，下章注意"
   - 节奏拍点缺失（如三章无小爽点） → "已 3 章无小爽点，下章需安排"
9. **抽 fatigueSignals**。结合题材 `fatigueWords` 词表，统计本章过度使用的表达。具体到次数和词目（"'冷笑' 出现 4 次"、"'眸光' 3 次"）。
10. **输出 JSON**。严格按下面 Output contract 的 schema，不带 markdown 代码块标记，UTF-8 写入。

## Output contract

写入：`story/runtime/chapter-{NNNN}.analysis.json`（`{NNNN}` 为零填充 4 位章节号）。

JSON 形状：

```json
{
  "chapter": 12,
  "satisfactionHits": ["悟道突破", "身份揭示"],
  "pacingBeats": [
    {"position": "开篇", "type": "悬念", "note": "门后传来碎瓷声但无人应答"},
    {"position": "中段", "type": "反转", "note": "胖虎借条原来是替身写的"}
  ],
  "reusableMotifs": [
    "用一个具体物件（借条）承载关系转折",
    "反派用礼貌句式说狠话"
  ],
  "characterArcDeltas": [
    {"character": "林秋", "delta": "从迷茫到决心要去问个清楚"},
    {"character": "师兄", "delta": "由暗中观察转为正面试探"}
  ],
  "warningsForNextChapter": [
    "H001 已连续 3 章未推进，下章必须推一下",
    "chapter_memo 承诺的『七号门实证』只兑现一半，下章需补完"
  ],
  "fatigueSignals": [
    "'冷笑' 出现 4 次",
    "'眸光' 3 次"
  ],
  "tokenUsage": {"prompt": 0, "completion": 0, "total": 0}
}
```

字段约束：

- `chapter`: integer，与 `{NNNN}` 一致
- `satisfactionHits`: string[]，元素必须出自题材 profile 的 `satisfactionTypes` 目录，未命中写 `[]`
- `pacingBeats`: object[]，`position ∈ {开篇, 前段, 中段, 后段, 结尾}`（英文书相应英文枚举），`type` 自由文本但建议来自题材 `pacingRule`
- `reusableMotifs`: string[]，每条 ≤ 50 字
- `characterArcDeltas`: `{character, delta}[]`，每条 `delta` ≤ 60 字
- `warningsForNextChapter`: string[]，每条必须可被下章 Planner 直接消费（写明 hook_id / chapter_memo 字段名 / audit 维度）
- `fatigueSignals`: string[]，格式 `"'<词>' 出现 N 次"`
- `tokenUsage`: 透传 LLM 调用的 token 计数；没有计数能力时填 `{"prompt": 0, "completion": 0, "total": 0}`

**消费契约**：下一章（NNNN+1）的 Planner（phase 02）**必须**读取本文件。如果文件存在且 `warningsForNextChapter` 非空，下章 chapter_memo 的「## 该兑现的 / 暂不掀的」或「## 不要做」段落必须正面回应（参 phase 02 文档对应小节）。

## Failure handling

- **解析失败重试 ≤ 2 次**：JSON parse 失败 / schema 校验失败 → 把具体错误（"satisfactionHits 含目录外元素"、"pacingBeats 缺 position"等）作为 feedback 附到用户消息末尾，请 Claude 在心中重试。
- **第 2 次仍失败 → 不阻断主循环**：写一个 stub 文件让下章 Planner 知道没有定性输入：

  ```json
  {
    "chapter": 12,
    "warning": "analyzer-failed",
    "satisfactionHits": [],
    "pacingBeats": [],
    "reusableMotifs": [],
    "characterArcDeltas": [],
    "warningsForNextChapter": [],
    "fatigueSignals": [],
    "tokenUsage": {"prompt": 0, "completion": 0, "total": 0}
  }
  ```

  下章 Planner 看到 `warning === "analyzer-failed"` 即视为无定性输入，按常规走（不抛错）。

- **chapter_memo 缺失 / audit 结果缺失**：Analyzer 不抛错，照样按可读到的部分跑——把缺的输入在 `warningsForNextChapter` 里附一条说明（"上章 audit 结果缺失，无法核对 critical issue"）。

- **chapter 文件缺失**：直接抛错，主循环必须停下——没有正文就没法分析，这个状态意味着 step 11 之前就已经出问题。

## 注意事项

- **单向只读**：Analyzer **绝不**修改 `chapters/{NNNN}.md`、`story/state/*.json`、`pending_hooks.md`、`chapter_summaries.md` 等任何真理文件。它的唯一产物就是 `story/runtime/chapter-{NNNN}.analysis.json`。
- **不替代 Settler**：真理文件增量已由 Settler + `apply_delta.py` 完成（step 8/9），Analyzer 不要再去碰 hook 状态、状态卡。
- **不要发明 hookId**：`warningsForNextChapter` 里引用的 `hook_id` 必须真实存在于 `story/state/hooks.json` 中。
- **satisfactionTypes 目录闭合**：如果题材 profile 没列 `satisfactionTypes`（少数题材），Analyzer 留 `satisfactionHits: []` 而不是自己造类型。
- **fatigueSignals 要给次数**：模糊地写"用得太多"对下章 Planner 没帮助；必须 `'<词>' 出现 N 次` 才能在 phase 02 里被引用。
- **不要做总评 / 打分**：评分是 Auditor 的职责。Analyzer 只做定性归类，不输出 1-100 分。
- **English variant**：`book.language === "en"` 时，输出文案改用英文枚举（`positions: opening / early / middle / late / closing`，satisfactionTypes 取题材 profile 的英文目录），但 JSON 字段名保持不变。
- **聚合视图走 `scripts/analyzer_index.py`**：跨章趋势（哪些 satisfactionType 用得太密 / 哪些 hook 长期没动）由那个脚本聚合多份 analysis 文件得到，**不在 Analyzer 单章里做**。
