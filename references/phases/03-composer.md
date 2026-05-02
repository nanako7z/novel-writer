# Phase 03: Composer（确定性上下文装配）

## 何时进入

主循环在 Planner 之后、Architect/Writer 之前调到这里。Composer 不调 LLM、不做语义判断；它只是把多份真理文件按章节焦点筛一遍，拼成三份 runtime 工件（contextPackage / ruleStack / chapterTrace）写到 `story/runtime/`，作为 Writer 的"输入清单"。

## Inputs

Claude 在这一阶段需要读：

- `story/runtime/chapter_memo.md` ——Planner 的产物（goal / outlineNode / threadRefs / 章节意图）
- `story/current_focus.md` ——当前任务焦点
- `story/audit_drift.md` ——上一章 audit drift 注释（如果有）
- `story/current_state.md` ——硬事实
- `story/outline/story_frame.md` + `story/outline/volume_map.md` ——卷纲（如果不存在，回退 `story_bible.md` / `volume_outline.md`）
- `story/parent_canon.md` ——续作 / 父书 canon（如果存在）
- `story/fanfic_canon.md` ——同人原作设定（如果存在）
- `chapters/{近 3 章}.md` ——最近 3 章正文末句（结尾 trail，避免结构重复）
- `scripts/memory_retrieve.py` 输出 ——滑窗记忆（recent + relevant summaries / active hooks / character roster / current_state snapshot）。**这个替代了原先"通读全部 chapter_summaries"的做法**；详见 [references/memory-retrieval.md](../memory-retrieval.md)
- `story/state/hooks.json` ——根据 memo.threadRefs 抽出对应 hook 的债务简报（只取 memory_retrieve 输出之外、threadRefs 显式点名的那几个 hookId）

## Process

Composer 是确定性的"读 + 拼"流程，Claude 不需要自由发挥；它扮演一个"图书管理员"，按以下步骤把上下文打包。

### 工作步骤

#### 0. 跑 memory_retrieve（**先于一切其他读**）

Composer 不再"把所有 chapter_summaries 全读一遍"——那在 30 章后就开始挤 token。换成调脚本拿"滑窗记忆"：

```bash
python {SKILL_ROOT}/scripts/memory_retrieve.py \
  --book <bookDir> \
  --current-chapter N \
  [--window-recent 6] \
  [--window-relevant 8] \
  [--include-resolved-hooks] \
  --format json
```

输出 JSON 形状见 [references/memory-retrieval.md#输出-schema](../memory-retrieval.md#输出-schema)。Composer 把这份 JSON 当作"记忆维度"的主输入；step 2 的 selectedContext 表里 row 9–11、13–15 就直接从这份 JSON 里取（不再各自重读 `chapter_summaries.md` 全文）。

##### Memory window — 怎么挑窗口大小

读 chapter_memo 的几个标志位决定调参：

| memo 标志 | 调用方式 |
|---|---|
| `isGoldenOpening: true`（首章 / 卷首） | `--window-recent 3 --window-relevant 4` |
| `cliffResolution: true`（即将回收 core hook） | `--window-recent 6 --window-relevant 12 --include-resolved-hooks` |
| 上一章刚 resolve 了 hook（"余响"章） | `--window-recent 6 --window-relevant 6 --include-resolved-hooks` |
| `arcTransition: true`（新卷 / 新弧线开篇） | `--window-recent 8 --window-relevant 12` |
| 节奏 / 关系日常章（`chapterType` ∈ {日常, 节奏调整}） | `--window-recent 4 --window-relevant 4` |
| 默认 | 不传窗口参数，用脚本默认值 6 / 8 |

判定优先级：cliffResolution > isGoldenOpening > arcTransition > 余响 > 日常 > 默认。多个同时为真极少见，按优先级取最高那一档。

调参依据写到 `chapter_trace.composerInputs`，方便后期 audit / replay。

#### 1. 推导 retrievalHints

从 `chapter_memo` 抽出 retrieval 关键词集合：

```
retrievalHints = [memo.goal, memo.outlineNode, ...memo.threadRefs].filter(truthy)
```

后续 `current_state` / `story_frame` 等长文件用 hints 做"段落优选"——优先返回包含 hint 的段。

#### 2. 装 selectedContext（一组 `{source, reason, excerpt}` 条目）

按以下顺序拼，**不存在的文件直接跳过**（不要写"文件未创建"占位条目）：

| 顺序 | source                              | reason                                                                          | excerpt 取法                                                            |
|------|-------------------------------------|---------------------------------------------------------------------------------|------------------------------------------------------------------------|
| 1    | `runtime/chapter_memo`              | Carry the planner's chapter memo into governed writing.                         | `goal=… \| golden-opening=true? \| <memo body>`                        |
| 2    | `story/current_focus.md`            | Current task focus for this chapter.                                            | 首段非空非标题行                                                          |
| 3    | `story/audit_drift.md`              | Carry forward audit drift guidance from the previous chapter.                   | 首段非空非标题行                                                          |
| 4    | `story/current_state.md`            | Preserve hard state facts referenced by the active brief / hard constraints.    | 优先含 hint 的段，否则首段                                                   |
| 5    | `story/outline/story_frame.md`      | Preserve canon constraints.                                                     | 优先含 hint 的段                                                         |
| 6    | `story/outline/volume_map.md`       | Anchor the default planning node for this chapter.                              | 优先含 `memo.outlineNode` 的段                                            |
| 7    | `story/parent_canon.md`             | Preserve parent canon constraints (续作 / spinoff).                            | 首段                                                                    |
| 8    | `story/fanfic_canon.md`             | Preserve extracted fanfic canon constraints.                                    | 首段                                                                    |
| 9    | `memory_retrieve#recent_titles`     | Avoid repetitive chapter naming.                                                | `recentSummaries` 末 5 条的 `chapter: title` 用 ` \| ` 拼接                |
| 10   | `memory_retrieve#recent_mood_type_trail` | Mood / chapterType cadence visibility.                                     | `recentSummaries` 末 5 条的 `chapter: mood / chapterType` 拼接             |
| 11   | `story/chapters#recent_endings`     | Show how recent chapters ended (avoid 3 连续 collapse endings).                  | 最近 3 章末句各取最后一句（>60 字截断为 57 字 + "..."）                          |
| 12   | `runtime/hook_debt#<hookId>`        | Narrative debt brief with original seed text.                                   | 对每个 `memo.threadRefs` 中存在于 `activeHooks` 的 hookId，渲染下方"hook debt 简报" |
| 13   | `memory_retrieve#facts`             | Relevant current-state fact retrieved.                                          | `currentState.facts` 中与 hint 匹配的硬事实行                                 |
| 14   | `memory_retrieve#relevant_summaries` | Relevant episodic memory（events-only）.                                       | `relevantSummaries`（脚本已按 character/hook 重叠筛选并截到 events）             |
| 15   | `story/volume_summaries.md#<anchor>` | Long-span arc memory compressed from earlier volumes.                          | 卷级摘要（如果有）                                                          |
| 16   | `story/pending_hooks.md#<hookId>`   | Carry forward unresolved hooks that match the chapter focus.                    | type \| status \| expectedPayoff \| payoffTiming \| notes 拼接          |

**hook debt 简报格式**（中文版）：

```
<hookId>（<type>，备忘引用旧债，已开<age>章） | 读者承诺：<expectedPayoff> | 种于第<startChapter>章：<seed beat> | 推进于第<lastAdvancedChapter>章：<latest beat>
```

其中 `seed beat` / `latest beat` 从 `chapter_summaries` 里挑首次提到该 hookId 的章和最近一次推进章渲染为 `chN <title> - <events|hookActivity|stateChanges>`。

#### 3. 构建 ruleStack（四级覆盖契约）

按 `references/rule-stack.md` 定义的 L1→L4 层级，从对应的源文件抽硬约束：

- **L1 题材规则**：`genre-profile`（题材底色、敏感词、数值系统开关、平台禁忌）——全书唯一
- **L2 全书规则**：`book_rules` YAML（protagonist personalityLock / behavioralConstraints / forbidden 风格 / prohibitions / chapterTypesOverride）——架构师产物
- **L3 章节规则**：从 `chapter_memo` 抽（goal、threadRefs、isGoldenOpening、"## 不要做"段、"## 章尾必须发生的改变"段）
- **L4 runtime 规则**：上一章 audit_drift 提示 + 资源账本硬上限 + stale hook 强制处理清单

下层覆盖上层。冲突时以 L4 → L3 → L2 → L1 顺序裁决；同级冲突保留两条让 Writer 自己解（罕见，应在 Planner 阶段就压平）。

#### 4. 构建 chapterTrace（审计追踪）

记录本次 Composer 的输入指纹：

```yaml
chapter: 12
plannerMemoPath: story/runtime/chapter_memo.md
contextSources:
  - story/current_state.md
  - story/outline/story_frame.md
  - ...
ruleStackLayers: [L1, L2, L3, L4]
composerInputs:
  - story/runtime/chapter_memo.md
generatedAt: <ISO timestamp>
```

#### 4.5 POV 过滤（可选）

如果 `chapter_memo` 声明了**单 POV**（`pov: <character>` 或 `视角: <character>`），在写 runtime 工件之前，跑一次 POV 过滤把 POV 不可能知道的真相剔出 selectedContext：

```bash
python {SKILL_ROOT}/scripts/pov_filter.py \
  --book <bookDir> \
  --pov <chapter_memo 中的 POV 角色名> \
  --current-chapter N \
  --input <draft 出来但还没写盘的 context_package.json> \
  [--strict]
```

处理规则：

- 输出的 `filtered_context` 直接覆盖原 `selectedContext`（不要保留两份）。
- 输出的 `pov_blindspots` 追加为新增 selectedContext 条目，`source = "runtime/pov_blindspots"`，`reason = "POV cannot witness; do not reveal in this chapter"`，`excerpt` 拼成"以下事项 POV 不知，本章不要主动揭示：<id>，<id>…"。
- 把 `povChapters` / `relationships` 摘要写进 `chapter_trace.composerInputs.pov`（作为审计追踪）。

`--strict` 何时打开（按 `book.json#fanficMode` 决定，按下表）：

| 条件 | --strict |
|---|---|
| 非 fanfic（普通单 POV 章） | 看 chapter_memo 是否含 `strictPOV: true`，是则开 |
| fanfic = canon | 开 |
| fanfic = au | 关（世界观已变，inferred 应放行） |
| fanfic = ooc | 关（OOC 改的是反应不是认知） |
| fanfic = cp | 开（CP 视角盲区是爽点） |

**fanfic mode 调参**：在 chapter_memo 的 frontmatter 里添加 `povStrict: true|false` 可强制覆盖以上默认。

如果 chapter_memo 没声明 POV（多视角章 / 全知章），跳过本步——直接走原 selectedContext 即可。

完整算法、输出 schema、blindspot 处理见 [references/pov-filter.md](../pov-filter.md)。

#### 4.6 （可选）用 state projections 替换胖大的真理文件

当上下文实在装不下原始真理文件时，可以让 Composer 用 `state_project.py` 拿压缩视图代替原始真理文件读取。详见 [references/state-projections.md](../state-projections.md)。

触发条件（任一即可）：

| 触发 | 行为 |
|---|---|
| `chapter_summaries.json` 行数 > 100 | 用 `--view characters-in-scene` 替原表 row 9-11（`recent_titles` / `recent_mood_type_trail` / `recent_endings`）的"近窗摘要全量"形态——projection 已经聚合好"谁在场 / 主导情绪"|
| `pending_hooks.md` 行数 > 30 | 用 `--view hooks-grouped` 替原表 row 16（`pending_hooks#<hookId>`）的钩子整段——projection 把钩子分主线 / 支线 / 孤立三栏 |
| chapter_memo `cliffResolution: true` | 加跑 `--view subplot-threads` 看核心 hook 的上下游（**追加**而非替换，原 hook debt 保留）|
| chapter_memo `arcTransition: true` 或 audit drift 抱怨"角色情绪平/单一" | 加跑 `--view emotional-trajectories` |

```bash
python {SKILL_ROOT}/scripts/state_project.py \
  --book <bookDir> --current-chapter N \
  --view <one of: characters-in-scene|hooks-grouped|emotional-trajectories|subplot-threads> \
  [--window 10] [--markdown]
```

输出（JSON 或 markdown）以单条 selectedContext 的形式塞进 `context_package.selectedContext`，`source = "runtime/state_projection/<view>"`，`reason = "Compressed projection in lieu of raw truth file (window=N)"`，excerpt 用 markdown 渲染（`--markdown`）保持人类可读。

**绝不写**回真理文件——projection 是单向只读派生。一行 view 通常 < 800 字符，比原始文件压缩 5-10 倍。

不触发条件就走原表 §2 装 raw 文件——projection 是预算紧时的备用通道，不是默认路径。

#### 5. 写入 runtime 工件

把三份产物写到：

- `story/runtime/context_package.json`
- `story/runtime/rule_stack.json`
- `story/runtime/chapter_trace.json`

写入采用「先写 .tmp 再 mv」原子方式（参考 `scripts/apply_delta.py` 的写法）。

## Output contract

```
story/runtime/context_package.json   # ContextPackage schema (chapter + selectedContext[])
story/runtime/rule_stack.json        # RuleStack schema (L1/L2/L3/L4 layers)
story/runtime/chapter_trace.json     # ChapterTrace schema (audit trail)
```

ContextPackage 形状（搬自 inkos `models/input-governance.ts`）：

```json
{
  "chapter": 12,
  "selectedContext": [
    { "source": "runtime/chapter_memo", "reason": "...", "excerpt": "..." },
    { "source": "story/current_state.md", "reason": "...", "excerpt": "..." }
  ]
}
```

RuleStack 形状：

```json
{
  "L1_genre": { "genre": "玄幻", "platform": "番茄", "forbidden": ["…"] },
  "L2_book":  { "protagonist": {…}, "prohibitions": ["…"] },
  "L3_chapter": { "goal": "…", "threadRefs": ["H03"], "isGoldenOpening": false, "doNot": ["…"], "endChange": ["…"] },
  "L4_runtime": { "auditDrift": "…", "staleHooksMustHandle": ["H012"], "resourceCaps": {…} }
}
```

## Failure handling

Composer 是确定性流程，**不应失败**。可能的异常：

- 必读文件缺失（`chapter_memo.md` / `current_state.md`）→ 立刻停掉本章流程，要求用户先跑 init / 上一章流程。
- JSON 写入失败（磁盘满 / 权限错）→ 把错误原样报给用户，不重试。
- 同一章节多次跑 Composer：覆盖式写入，最后一次为准。

不需要重试机制（无 LLM 调用）。

## 辅助：轻量过滤 vs. 派生视图

Composer 在装 selectedContext 前可选两条降噪路径，按需要二选一或都用：

| 工具 | 何时用 | 输出形态 |
|---|---|---|
| `scripts/context_filter.py` | **drop noise**——只想去掉 `pending_hooks` 已回收行 / `chapter_summaries` 远窗口外的章 / `subplot_board` 已结支线 / `emotional_arcs` 太老的孤立行 | 原文件相同形状的 markdown，只是行少了。`(文件尚未创建)` 占位 / 全部行都被过滤的 fallback 都直接还原原文。 |
| `scripts/state_project.py` | **derived views**——需要"按角色聚合的 hook 债务"、"主线进展时间线"、"卷视图"等**重新结构化**的派生视图 | 全新 schema 的 JSON / md（不是输入文件的子集），用于 selectedContext 单条 excerpt。 |

简单原则：

- 只是想砍掉无关行 → `context_filter.py`（更便宜，不改结构）。
- 想要 cross-cut 的"切片视图" → `state_project.py`。

`context_filter.py` 的调用形式（详见脚本 `--help`）：

```bash
python {SKILL_ROOT}/scripts/context_filter.py \
  --book <bookDir> --current-chapter N \
  --filter hooks|summaries|subplots|emotional-arcs|all \
  [--keep-recent 6] [--json]
```

- 默认 `--keep-recent` 与 `chapter-cadence` 默认窗口一致（6）。
- `--filter all` 一次跑四个，输出 `{ok, results: [{filter, source, originalLines, keptLines, content}]}`。
- 单 filter 不带 `--json` 时直接把过滤后的纯文本写到 stdout，方便管道。
- **fallback 守恒**：任何 filter 把数据行清空时回退原文，避免 Writer 看到空表错觉。

## 注意事项

- **不调 LLM**：本阶段产物完全可由确定性逻辑生成，Claude 自己读文件、按表格拼即可。
- **selectedContext 体积要克制**：每条 excerpt 控制在 200-500 字符；hook debt 单条不超过 300 字。整个 context_package.json 应能被 Writer 一次性吃下不爆 token。
- **不存在的文件不要拼空条目**：跳过即可。Writer 只看到的就是"实际有的"，避免它在虚位条目上脑补。
- **outline 回退**：`outline/story_frame.md` 不存在时回退 `story_bible.md`；`outline/volume_map.md` 不存在时回退 `volume_outline.md`。这是给老书的兼容路径。
- **memo 里要求"## 当前任务对应某 hook → 必须 resolve"** 的硬规则在 Planner 阶段就该压平；Composer 只是搬，不修。如果发现 memo 违反此规则，记到 `chapter_trace.composerWarnings` 让 Writer / Auditor 注意，但不阻塞流程。
- **threadRefs 里出现的 hookId 在 hooks.json 里找不到** → 跳过该 hook debt 条目，记 `composerWarnings`，让 Auditor 第 32 维度（hook 账失衡）去抓。
- **Recent endings trail 至少要有 2 章**才输出，避免单章不成 trend。
- **English book**：所有 reason 字段切换为英文版本（参 inkos composer.ts L101-113、L347-363 的双语分支）；excerpt 内容随原文。
