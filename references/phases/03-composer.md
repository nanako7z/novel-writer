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
- `story/chapter_summaries.md` ——最近 3-5 章 title / mood / chapterType
- `chapters/{近 3 章}.md` ——最近 3 章正文末句（结尾 trail，避免结构重复）
- `story/state/hooks.json` ——根据 memo.threadRefs 抽出对应 hook 的债务简报
- `story/state/current_state.json` ——硬事实（数值、关系等）

## Process

Composer 是确定性的"读 + 拼"流程，Claude 不需要自由发挥；它扮演一个"图书管理员"，按以下步骤把上下文打包。

### 工作步骤

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
| 9    | `story/chapter_summaries.md#recent_titles` | Avoid repetitive chapter naming.                                          | 最近 5 章 `chapter: title` 用 ` \| ` 拼接                                 |
| 10   | `story/chapter_summaries.md#recent_mood_type_trail` | Mood / chapterType cadence visibility.                            | 最近 5 章 `chapter: mood / chapterType` 拼接                              |
| 11   | `story/chapters#recent_endings`     | Show how recent chapters ended (avoid 3 连续 collapse endings).                  | 最近 3 章末句各取最后一句（>60 字截断为 57 字 + "..."）                          |
| 12   | `runtime/hook_debt#<hookId>`        | Narrative debt brief with original seed text.                                   | 对每个 `memo.threadRefs` 中存在于 hooks.json 的 hookId，渲染下方"hook debt 简报" |
| 13   | `story/current_state.md#<predicate>` | Relevant current-state fact retrieved.                                         | memorySelection.facts（与 hint 匹配的硬事实行）                              |
| 14   | `story/chapter_summaries.md#<chapter>` | Relevant episodic memory.                                                    | memorySelection.summaries（与 hint 匹配的旧章摘要）                          |
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

## 注意事项

- **不调 LLM**：本阶段产物完全可由确定性逻辑生成，Claude 自己读文件、按表格拼即可。
- **selectedContext 体积要克制**：每条 excerpt 控制在 200-500 字符；hook debt 单条不超过 300 字。整个 context_package.json 应能被 Writer 一次性吃下不爆 token。
- **不存在的文件不要拼空条目**：跳过即可。Writer 只看到的就是"实际有的"，避免它在虚位条目上脑补。
- **outline 回退**：`outline/story_frame.md` 不存在时回退 `story_bible.md`；`outline/volume_map.md` 不存在时回退 `volume_outline.md`。这是给老书的兼容路径。
- **memo 里要求"## 当前任务对应某 hook → 必须 resolve"** 的硬规则在 Planner 阶段就该压平；Composer 只是搬，不修。如果发现 memo 违反此规则，记到 `chapter_trace.composerWarnings` 让 Writer / Auditor 注意，但不阻塞流程。
- **threadRefs 里出现的 hookId 在 hooks.json 里找不到** → 跳过该 hook debt 条目，记 `composerWarnings`，让 Auditor 第 32 维度（hook 账失衡）去抓。
- **Recent endings trail 至少要有 2 章**才输出，避免单章不成 trend。
- **English book**：所有 reason 字段切换为英文版本（参 inkos composer.ts L101-113、L347-363 的双语分支）；excerpt 内容随原文。
