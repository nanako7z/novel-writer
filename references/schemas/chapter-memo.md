# ChapterMemo（Planner 输出 schema）

Planner 阶段（02-planner）的输出契约。来源：`agents/planner-prompts.ts` L9-102（中文）/ L111-200（英文）。

输出格式：**YAML frontmatter + Markdown body**（不要用 JSON 包 markdown，不要加代码块标记）。落盘路径：`books/<bookId>/chapters/.memo/<chapter>.md`（或由 SKILL 实施时决定的路径，关键是与正文一一对应）。

---

## 1. YAML Frontmatter

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `chapter` | int ≥ 1 | 是 | 本 memo 对应的章节号 |
| `goal` | string（≤ 50 字） | 是 | 本章主目标，一句话动词驱动 |
| `isGoldenOpening` | bool | 是 | 是否黄金三章范围内（chapter ∈ {1,2,3}） OR 新弧线开篇 |
| `threadRefs` | array<string> | 是 | 从 `pending_hooks` / `subplot_board` 中挑出的 id 列表 |
| `cliffResolution` | bool | 否（默认 false） | 本章正面回收上一章 / 近章悬念，下游需注入"已 resolve hooks"作 continuity |
| `arcTransition` | bool | 否（默认 false） | 弧线 / 卷间过渡章，需放宽 relevant memory 窗口、可能触发 Architect 回顾 |
| `volumeFinale` | bool | 否（默认 false） | 卷尾终章，触发 cross-volume payoff 验证 + 收紧记忆窗口为本卷视角 |
| `isReshootChapter` | bool | 否（默认 false） | 重写已落盘的旧章；与新章流程不同（不写新 manifest 行、apply_delta 走 reshoot 模式） |

### 1.1 编程式 flags 的语义与下游消费方

这五个布尔 flag 不是装饰性元数据——下游脚本会显式读取它们调整行为。
Planner 必须在生成 memo 时按下述规则**程式化**地置位（不要依赖 Writer
临时判断）。

| flag | 何时置 true | 谁消费 | 消费行为 |
|---|---|---|---|
| `isGoldenOpening` | `chapter ≤ 3`，或 memo `## 当前任务` 涉及新弧线开篇 | `memory_retrieve.py` | `--window-recent 2 --window-relevant 0`（首章不需要长尾记忆）|
| | | `cadence_check.py` | 抑制 satisfaction-pressure 警告（黄金三章不走常规 cadence）|
| | | `03-composer.md` step 2 row 1 | 在 chapter_memo 摘要里追加 `\| golden-opening=true` |
| `cliffResolution` | 上一章末尾留了硬钩子，本章正面回收 | `memory_retrieve.py` | 自动加 `--include-resolved-hooks`；窗口 6/12 |
| | | `03-composer.md` §4.6 | 触发 `state_project.py --view subplot-threads` |
| `arcTransition` | 卷间 / 弧线边界（按 volume_map.md 边界判断） | `memory_retrieve.py` | `--window-relevant 12`（拉宽相关记忆）|
| | | `03-composer.md` §4.6 | 触发 `state_project.py --view emotional-trajectories` |
| | | `00-orchestration.md` step 4 | Architect 回顾触发条件之一（与 volume_map 边界 OR 关系）|
| `volumeFinale` | 章号 == 当前卷末章号（按 volume_map.md） | `memory_retrieve.py` | `--window-relevant 0`（只看本卷）|
| | | `cadence_check.py` | 输出 `volumeFinaleReady: bool`（按 expected payoff schedule 是否完备）|
| | | `hook_governance.py` | volume-payoff 验证（committedToChapter ≤ 卷末必兑现）|
| `isReshootChapter` | 重写第 N 章（N ≤ lastAppliedChapter） | `apply_delta.py` | 走 reshoot 模式（覆盖 chapter file，不动 manifest 计数）|
| | | `00-orchestration.md` | 跳过 manifest.lastAppliedChapter +1 步 |

**优先级**：CLI 显式参数 > memo flag > 默认值。例如 Composer 调
`memory_retrieve.py --window-recent 4` 时会覆盖 `isGoldenOpening: true` 默认
带来的窗口缩小。

---

## 2. Markdown Body 结构（7 段，全部必填）

每个二级标题（`##`）必须出现，内容不能为空。段落顺序固定：

1. `## 当前任务`
2. `## 读者此刻在等什么`
3. `## 该兑现的 / 暂不掀的`
4. `## 日常/过渡承担什么任务`
5. `## 关键抉择过三连问`
6. `## 章尾必须发生的改变`
7. `## 本章 hook 账`
8. `## 不要做`

> 以上 8 段在原 prompt 中明确列举（含 hook 账与不要做）。"7 段"是俗称，实际为 8 个 `##` 段。

---

## 3. 完整示例

```markdown
---
chapter: 12
goal: 把七号门被动过手脚从猜测钉成现场实证
isGoldenOpening: false
threadRefs:
  - H03
  - S004
---

## 当前任务
林秋在第二节查岗时强行打开七号门检修井，找到雷脉残片刻有"庚辰"二字。

## 读者此刻在等什么
1) 读者期待七号门那道灼痕的来历被点破，并把矛头对准赵执事。
2) 本章对这个期待——制造更强缺口：把猜测钉成实证，但揭露身份延后。

## 该兑现的 / 暂不掀的
- 该兑现：H03 七号门灼痕 → 兑现到"实证已抓到、谁动的手锁定为'前代弟子'"
- 暂不掀：S004 赵执事身份 → 先压住，留到第 16 章

## 日常/过渡承担什么任务
[第 1 节晨练] → 让二师姐自然出场，落"灵海受创"伏笔
[第 4 节饭桌] → 用赵执事一句反话强化敌意

## 关键抉择过三连问
- 主角本章最关键的一次选择：强行打开七号门检修井
  - 为什么这么做？只剩半天就要被换岗，错过这次再没机会
  - 符合当前利益吗？是——抓不到证据下章会被反咬偷东西
  - 符合他的人设吗？是——他在底色上就是"宁可破规也要落锤"
- 对手/配角本章最关键的一次选择：赵执事提前换岗
  - 为什么这么做？嗅到林秋在查
  - 符合当前利益吗？是
  - 符合他的人设吗？是

## 章尾必须发生的改变
- 信息改变：林秋从猜测升级为持有物证
- 关系改变：与二师姐结成临时盟友
- 权力改变：赵执事知道自己被盯上了

## 本章 hook 账

open:
- [new] 雷脉残片"庚辰"二字 || 理由：本章自然带出新谜，伏笔做下章主推

advance:
- H03 "七号门灼痕" → 林秋拿到刻字残片（pressured → near_payoff）
- S004 "赵执事身份" → 提前换岗暴露警觉（planted → pressured）

resolve:
- H07 "杂役腰牌" → 林秋摘下交还（clear）

defer:
- H09 "守拙诀来历" → 本章不动，理由：时机不到，等到第 18 章

## 不要做
- 不要在本章解释赵执事身份
- 不要写林秋强行突破修为
- 不要让二师姐怀疑林秋的动机
```

---

## 4. 验证准则（apply 前 SKILL 自检）

### 4.1 Frontmatter 硬校验

- `chapter` 是正整数，且等于"上一已写章号 + 1"（`isReshootChapter: true` 例外，等于被重写的旧章号）
- `goal` 字符长度 ≤ 50（中文按字符计）
- `isGoldenOpening`：当 `chapter <= 3` 时**必须** `true`；否则可由 Planner 因"新弧线开篇"主动置 true
- `threadRefs` 中的每个 id 必须真实存在于输入的 `pending_hooks` 或 `subplot_board`（不允许编造）
- `cliffResolution` / `arcTransition` / `volumeFinale` / `isReshootChapter` 默认 false；Planner 按上节"何时置 true"程式化设置，不要靠下游运行时再猜

### 4.2 Body 段完整性

- 8 个 `##` 段全部出现，且每段非空（不能只有标题）
- 标题使用原文文案，不要重命名

### 4.3 hook 账硬规则（来自 prompt）

- 输入 `pending_hooks` 中任何 hook 状态已是 `pressured` 或 `near_payoff` 且距上次推进 ≥ 5 章 → 必须落到 `advance` 或 `resolve`，不允许 `defer`
- `advance` / `resolve` 中的 hook_id 必须真实存在于输入的 `pending_hooks`（禁止编造）
- 若本章是纯高压/战斗章节没有伏笔处理空间 → 至少要有 1 条 `advance` 或 `defer` 声明
- 本章"## 当前任务"如果天然对应某个 hook 的兑现动作 → 必须在 `resolve` 里显式声明对应 hook_id
- `open` 中新开钩子数 ≤ 2 个

### 4.4 内容硬规则

- 不要在 memo 里提方法论术语（"情绪缺口"、"cyclePhase"、"蓄压"等）——直接用这本书的人物、地点、事件说事
- 不要产生正文片段或对话片段
- 如果卷纲和上章摘要冲突，信上章摘要（剧情已实际发生）

### 4.5 稀疏 memo 例外

喘息章 / 后效章 / 过渡章 memo 可以只写 `goal` + 各段骨架；下游 Auditor 只对 memo 实际写出的段落做 drift 检查，**不会**因为 memo 稀疏判 incomplete（参见 continuity.ts L260）。但即使稀疏，8 个 `##` 段的标题必须出现。

---

## 5. 失败处理

Planner 调用最多重试 3 次：

1. 输出非 YAML frontmatter（缺 `---` 包裹）
2. frontmatter 字段缺失或类型错误
3. 8 段 `##` 缺失任意一段
4. `threadRefs` 出现编造 id

任一发生即视为解析失败，把错误详情塞回 prompt 重跑（max 3 次）。3 次仍失败 → SKILL 中断流程并向用户报错。
