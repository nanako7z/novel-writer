# Phase 02: Planner（章节规划 / chapter_memo 生成）

## 何时进入

主循环在「写下一章」流程中、Composer 之前调到这里。每一章都要跑一次 Planner，产物是 `story/runtime/chapter_memo.md`，由下游 Composer 读入装配上下文，再由 Writer 按 memo 扩写正文。Planner 不写正文。

## Inputs

Claude 在这一阶段需要读：

- `book.json` / `inkos.json` ——拿到 chapterNumber、language、targetChapters、chapterWordCount
- `story/author_intent.md` 或 `brief.md` ——用户原始 brief（最高优先级）
- `story/current_focus.md` ——当前 arc 推什么
- `story/outline/story_frame.md` + `story/outline/volume_map.md` ——卷纲（架构师产物）
- `story/character_matrix.md` ——主角 / 对手 / 协作者行
- `story/pending_hooks.md` ——伏笔池（用于 threadRefs / hook 账）
- `story/subplot_board.md` ——支线进度
- `story/chapter_summaries.md` ——最近 3 章摘要 + 上一章最后一屏
- `story/state/hooks.json` ——筛 stale hooks（pressured / near_payoff 且 ≥ 5 章未推进）
- 如果 chapterNumber ≤ 3，需要触发"黄金三章"指引段
- 上一章正文文件（取最后一屏作为 `previous_chapter_ending_excerpt`）
- **上章 Analyzer 反馈**：`story/runtime/chapter-{N-1}.analysis.json`（如存在）——由 [phase 13 Chapter Analyzer](13-analyzer.md) 在上章落盘后产出的定性回顾，包含 `warningsForNextChapter`、`fatigueSignals` 等下章 Planner 必须消费的硬信号

## 上章 Analyzer 的反馈（载入与消费）

[Phase 13 Chapter Analyzer](13-analyzer.md) 在每章落盘后会产出 `story/runtime/chapter-{N-1}.analysis.json`，本阶段 Planner **必须**先尝试读这份文件（如果 `currentChapter > 1`）。

**消费规则：**

1. **读取**：尝试读 `story/runtime/chapter-{N-1}.analysis.json`。
   - 文件不存在 → 视为无定性输入，按常规走（不抛错）。
   - 文件存在但 `warning === "analyzer-failed"` → 同上，stub 文件代表 Analyzer 跑挂了，不阻断。
2. **`warningsForNextChapter` → 必须正面回应**：每条 warning 在生成 chapter_memo 时按以下规则映射：
   - 形如 `"H001 已连续 3 章未推进，下章必须推一下"` → 在 `## 本章 hook 账` 的 `advance` 或 `resolve` 段落显式列出 `H001`，**不能 defer**。
   - 形如 `"chapter_memo 承诺的『七号门实证』只兑现一半，下章需补完"` → 在 `## 该兑现的 / 暂不掀的` 段补一条「续兑现：七号门实证」。
   - 形如 `"AI 味集中在 X 段，下章注意"` → 在 `## 不要做` 段加一条具体的避坑点。
3. **`fatigueSignals` → 灌进 `## 不要做`**：每条 `"'冷笑' 出现 4 次"` 直接转成 `## 不要做` 里的一条「本章避免使用 '冷笑' 等上章疲劳词」。
4. **`reusableMotifs`、`pacingBeats`、`satisfactionHits`** 是软性参考，可以参考但不强制——主要用于让 Planner 判断本章是该重复成功配方还是需要换节奏（避免连续 3 章打同一个爽点）。
5. **冲突仲裁**：如果多条 `warningsForNextChapter` 是 `priority: "high"` 且互相挤压（例如同时要求兑现 3 个 hook + 推主线 + 补 audit critical），且本章 `chapterWordCount` 装不下 → **不要硬塞**，把冲突摘要给用户：
   - "上章 Analyzer 给本章压了 3 条 high-priority 信号：A / B / C，估计装不下，按优先级建议留 A+B 推到下章。要不要这样？"
   - 用户决策后再生成 memo；不要 Planner 自己悄悄丢掉信号。
6. **不要发明 hook_id**：warning 里引用的 hook_id 必须在当前 `pending_hooks.md` 里能查到才能进 advance/resolve；查不到（已被 resolve 或被 settler 删除）就把这条 warning 转成 `## 不要做` 里的一条说明（"不要再提及 H001，已收"）。

**写入位置 cheat sheet**：
- 兑现型 warning → `## 该兑现的 / 暂不掀的`
- hook 推进型 warning → `## 本章 hook 账`（advance / resolve）
- 避坑型 warning + fatigueSignals → `## 不要做`

## Process

Claude 在心中扮演"创作总编"，按下面的系统 prompt 执行。

### 系统 prompt（搬自 inkos `planner-prompts.ts` L9-102，请 Claude 在心中扮演这个角色）

```
你是这本小说的创作总编，职责是为下一章产生一份 chapter_memo。你不写正文——你只规划这章要完成什么、兑现什么、不要做什么。下游写手（writer）会按你的 memo 扩写正文。

你的工作原则（内化，不要在 memo 里引用条目号）：

1. 3-5 章一个小目标周期：每 3-5 章必须有一个小目标达成或悬念升级，主线持续推进
2. 主动塑造读者期待：作者刻意制造"还没兑现但快要兑现"的缺口，兑现时必须超过读者预期 70%
3. 万物皆饵：日常/过渡章节的每一笔都要是未来剧情的伏笔或钩子
4. 人设防崩：角色行为由"过往经历 + 当前利益 + 性格底色"共同驱动。禁止反派突然降智、主角突然圣母
5. 1 主线 + 1 支线：支线必须为主线服务，不同时推 3 条以上支线
6. 爽点密集化：每 3-5 章一个小爽点（小冲突→快解决→强反馈），全员智商在线
7. 高潮前铺垫：大高潮前 3-5 章必须有线索埋设
8. 高潮后影响：爆发章之后 1-2 章必须写出改变（主线推进、人设成长、关系变化）
9. 人物立体化：核心标签 + 反差细节 = 活人
10. 五感具体化：场景描写必须有具体可视化感官细节
11. 钩子承接：每章章尾留钩
12. 钩子账本必须结账：每章对活跃 hook 做明确动作（open/advance/resolve/defer），不允许"新开一堆不回收"

## 输出格式（严格遵守）

输出 YAML frontmatter + markdown body，不要用 JSON 对象包 markdown 字符串，不要加代码块标记。

结构如下：

---
chapter: 12
goal: 把七号门被动过手脚从猜测钉成现场实证
isGoldenOpening: false
threadRefs:
  - H03
  - S004
---

## 当前任务
<一句话：本章主角要完成的具体动作，不要抽象描述>

## 读者此刻在等什么
<两行：
1) 读者现在期待什么（基于前几章的埋伏）
2) 本章对这个期待做什么——制造更强缺口 / 部分兑现 / 完全兑现 / 暂不兑现但给暗示>

## 该兑现的 / 暂不掀的
- 该兑现：X → 兑现到什么程度
- 暂不掀：Y → 先压住，留到第 N 章

## 日常/过渡承担什么任务
<如果本章是非高压章节，每段非冲突段落说明功能。格式：[段落位置] → [承担功能]
如果本章是高压/冲突章节，写"不适用 - 本章无日常过渡">

## 关键抉择过三连问
- 主角本章最关键的一次选择：
  - 为什么这么做？
  - 符合当前利益吗？
  - 符合他的人设吗？
- 对手/配角本章最关键的一次选择：
  - 为什么这么做？
  - 符合当前利益吗？
  - 符合他的人设吗？

## 章尾必须发生的改变
<1-3 条，从以下维度选：信息改变 / 关系改变 / 物理改变 / 权力改变>

## 本章 hook 账
**这是本章对活跃伏笔的账本，写手必须按这份账动作。格式如下（每个分类下用 - 列表）：**

open:
- [new] 新钩子描述（<=30字）|| 理由：为什么是现在开，不在本章点破（要求：本章新开钩子 ≤ 2 个）

advance:
- H007 "胖虎借条" → 林秋第一次想撕，被阻止（planted → pressured）
- H012 "雷架焦痕" → 师兄偷看留下印子（pressured → near_payoff）

resolve:
- H003 "杂役腰牌" → 林秋主动摘下（clear）

defer:
- H009 "守拙诀来历" → 本章不动，理由：时机不到，等到第 N 章

**硬规则**：
- 输入的 pending_hooks 里如果有任何 hook 状态已是 "pressured" 或 "near_payoff" 且距上次推进 ≥ 5 章，**必须**放到 advance 或 resolve，不允许 defer
- advance/resolve 里写的 hook_id 必须真实存在于 pending_hooks 输入中（不要编造 ID）
- 如果这章是纯高压/战斗章节没有伏笔处理空间，至少也要有 1 条 advance 或 defer 声明
- 本章"## 当前任务"如果天然对应某个 hook 的兑现动作，必须在 resolve 里显式声明对应 hook_id

## 不要做
<2-4 条硬约束>

## 输出要求

- goal 字段不超过 50 字
- threadRefs 是 YAML 数组，内容是从输入的 pending_hooks/subplot_board 中挑出的 id
- 每个二级标题（##）必须出现，内容不能为空
- 不要在 memo 里提方法论术语（"情绪缺口"、"cyclePhase"、"蓄压"等）——直接用这本书的人物、地点、事件说事
- 不要产生正文片段或对话片段
- 如果卷纲和上章摘要冲突，信上章摘要（剧情已实际发生）
```

### 工作步骤

1. **采集材料**。按 Inputs 顺序读文件；不存在的文件一律视作空（不要伪造内容）。
1a. **载入上章 Analyzer 反馈**（仅当 `chapterNumber > 1`）：尝试读 `story/runtime/chapter-{N-1}.analysis.json`，按上节"上章 Analyzer 的反馈"规则分流到 hook 账 / 该兑现的 / 不要做 三栏的输入材料中。文件缺失或 `warning === "analyzer-failed"` 即跳过，不阻断。
1b. **跑 cadence_check**（每章必跑，提早一步看节奏压力）：

  ```bash
  python {SKILL_ROOT}/scripts/cadence_check.py --book <bookDir> \
    --current-chapter <chapterNumber> --json
  ```

  把输出按下表分流进 memo 各段（详见 `references/cadence-policy.md`）：

  | cadence_check 字段 | 进 memo 哪一段 / 怎么处理 |
  |---|---|
  | `satisfactionPressure: "high"` | `## 当前任务` 必须直接对应一个 satisfactionType；不允许写"日常 / 整顿 / 走访"这类绵软任务 |
  | `satisfactionPressure: "medium"` | `## 读者此刻在等什么` 第 2 行强调"本章给一次部分兑现，下章必须完整兑现" |
  | `volumeBeatStatus` 含 "approaching mid-point" | `## 章尾必须发生的改变` 至少含一条**方向级**改变（信息 / 关系 / 权力，不只是位置/物品）|
  | `volumeBeatStatus` 含 "climax window" | 同上；优先 `权力改变` 类项 |
  | `recommendedChapterTypes[0]` | memo frontmatter 隐含建议（让 Writer §14 PRE_WRITE_CHECK 做 default chapterType）|
  | `pacingNotes` 含 "transitional dominant" | `## 不要做` 加一条："本章不要再写过渡 / 日常段，必须有冲突或决策" |

  这些是**软建议**——`current_focus.md` 或 hook 账上的硬约束可以否决。脚本失败（IO 错误）→ 跳过本步，不阻断。
2. **筛 stale hooks**：扫 `story/state/hooks.json`，挑出 `status ∈ {pressured, near_payoff}` 且 `chapterNumber - lastAdvancedChapter >= 5` 的 hook，作为「必须本章处理」清单注入用户消息。
3. **判定 isGoldenOpening**：`chapterNumber <= 3` → true，并附加黄金三章指引段（见下文）。
4. **拼装用户消息**。按 inkos `PLANNER_MEMO_USER_TEMPLATE` 的 7 段结构填模板：brief_block / 上一章最后一屏 / 最近 3 章摘要 / 当前 arc / 主角行 / 对手行 / 协作者行 / 相关 thread / 必须回收的陈旧 hook / 卷外约束。
5. **生成 memo**：在心中扮演系统 prompt 的角色，输出 YAML frontmatter + markdown body。**不要包代码块标记**，不要把 markdown 字符串塞进 JSON。
6. **解析校验**（参 inkos `parseMemo`）：必须能解析出 `chapter / goal / isGoldenOpening / threadRefs` 四个 frontmatter 字段，且 7 个 ## 二级标题全部出现且非空。

#### 黄金三章指引（chapter ≤ 3 时附加）

> 第 1 章：把主角直接抛进核心冲突（追杀 / 死局 / 被夺权 / 穿越即危机），禁止背景铺垫开场。
> 第 2 章：金手指通过一次具体事件落地（不是"觉醒了 XX"，而是"用了 XX，发生了 YY"）。
> 第 3 章：钉一个 3-10 章内可达成的具体短期目标（攒第一桶金 / 干翻小反派 / 救某人）。
> 全程：场景 ≤ 3、人物 ≤ 3、信息分层（基础信息伴随主角行动揭示，世界规则伴随剧情节点揭示，禁止整段 exposition）。

## Output contract

- 写入 `story/runtime/chapter_memo.md`（覆盖式写入；同一章节多次跑 Planner 取最后一次）
- 文件 schema：见 `references/schemas/chapter-memo.md`
- frontmatter 字段：`chapter: int`, `goal: ≤50 chars`, `isGoldenOpening: bool`, `threadRefs: string[]`
- body 必须包含 7 个二级标题：`## 当前任务` / `## 读者此刻在等什么` / `## 该兑现的 / 暂不掀的` / `## 日常/过渡承担什么任务` / `## 关键抉择过三连问` / `## 章尾必须发生的改变` / `## 本章 hook 账` / `## 不要做`

## Failure handling

参 inkos `planner.ts` L55 / L240：

- `MEMO_RETRY_LIMIT = 3`：解析失败最多重试 3 次。
- 每次失败把具体错误（"frontmatter 缺 goal"、"## 章尾必须发生的改变 段为空"等）作为 feedback 块附到用户消息末尾，请 Claude 在心中重新生成。
- 第 3 次仍失败 → 抛 `PlannerParseError`，主循环必须停止本章流程并把错误报告给用户；**不要静默截断或重命名字段**。

## 注意事项

- **brief 优先级最高**：用户原始 brief 里写明的核心设定（主角设定、世界前提、开场机制）必须在前几章落地，不要推迟。
- **不要发明 hook_id**：advance / resolve 里的 hook_id 必须来自输入的 pending_hooks，不存在就用 newHookCandidates 报给 Settler。
- **threadRefs 是 YAML 数组**，不是逗号分隔字符串；空就写 `threadRefs: []`。
- **不要写正文片段**：哪怕是"举例对话"也不行——Composer 会把整段 memo 喂给 Writer，Writer 看到样例对话就会照搬。
- **卷纲冲突时信上章摘要**：剧情已经实际发生过，volume_map 是预设、可被推翻。
- **方法论术语禁用**：memo 里不要出现"情绪缺口"、"cyclePhase"、"蓄压"——直接讲本书的人物、地点、事件。
- **English variant**：如果 `book.language === "en"`，使用 `PLANNER_MEMO_SYSTEM_PROMPT_EN` 与 `PLANNER_MEMO_USER_TEMPLATE_EN`（结构 1:1 对应，所有占位符与硬规则保持等价）。
