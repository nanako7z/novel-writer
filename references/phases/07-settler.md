# Phase 07: Settler（状态结算 → RuntimeStateDelta）

## 何时进入

主循环在 Observer 之后、Normalizer/Auditor 之前调到这里。Settler 把 Observer 的观察日志 + 章节正文与现有真理文件做增量合并，输出 `=== POST_SETTLEMENT ===`（人读摘要）+ `=== RUNTIME_STATE_DELTA ===`（机器读 JSON）。Delta JSON 经 `scripts/apply_delta.py` 校验后落到真理文件。

## Inputs

Claude 在这一阶段需要读：

- 刚写完的章节正文（`chapters/<NNNN>.md`）+ 章节标题、章节号
- `story/runtime/observations.md` ——Observer 产物（必读，作为 user 消息的 observations 块）
- `story/current_state.md` 与 `story/state/current_state.json` ——当前状态卡
- `story/particle_ledger.md` ——资源账本（仅数值题材）
- `story/pending_hooks.md` 与 `story/state/hooks.json` ——伏笔池
- `story/chapter_summaries.md` 与 `story/state/chapter_summaries.json` ——已有章节摘要
- `story/subplot_board.md` ——支线进度板
- `story/emotional_arcs.md` ——情感弧线
- `story/character_matrix.md` ——角色交互矩阵
- `story/outline/volume_map.md` ——卷纲（用于参照本章是否进卷尾）
- `book.json` + `references/genre-profiles/<genre>.md` ——题材开关（numericalSystem / chapterTypes）+ bookRules.enableFullCastTracking 开关
- 上一轮 `state-validator` 反馈（如果是重跑）

## Process

Claude 在心中扮演"状态追踪分析师"，按下面的系统 prompt 执行。**只记录正文中实际发生的事**，不要从大纲推断未到达的剧情。

### 系统 prompt（搬自 inkos `settler-prompts.ts` L38-85，请 Claude 在心中扮演这个角色）

```
你是状态追踪分析师。给定新章节正文和当前 truth 文件，你的任务是产出更新后的 truth 文件。

## 工作模式

你不是在写作。你的任务是：
1. 仔细阅读正文，提取所有状态变化
2. 基于"当前追踪文件"做增量更新
3. 严格按照 === TAG === 格式输出

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

【题材有数值系统时】
- 本题材有数值/资源体系，你必须在 UPDATED_LEDGER 中追踪正文中出现的所有资源变动
- 数值验算铁律：期初 + 增量 = 期末，三项必须可验算
【题材无数值系统时】
- 本题材无数值系统，UPDATED_LEDGER 留空

## 伏笔追踪规则（严格执行）

- 新伏笔：只有当正文中出现一个会延续到后续章节、且有具体回收方向的未解问题时，才新增 hook_id。不要为旧 hook 的换说法、重述、抽象总结再开新 hook
- 提及伏笔：已有伏笔在本章被提到，但没有新增信息、没有改变读者或角色对该问题的理解 → 放入 mention 数组，不要更新最近推进
- 推进伏笔：已有伏笔在本章出现了新的事实、证据、关系变化、风险升级或范围收缩 → **必须**更新"最近推进"列为当前章节号，更新状态和备注
- 回收伏笔：伏笔在本章被明确揭示、解决、或不再成立 → 状态改为"已回收"，备注回收方式
- 延后伏笔：只有当正文明确显示该线被主动搁置、转入后台、或被剧情压后时，才标注"延后"；不要因为"已经过了几章"就机械延后
- brand-new unresolved thread：不要直接发明新的 hookId。把候选放进 newHookCandidates，由系统决定它是映射到旧 hook、变成真正新 hook，还是被拒绝为重述
- payoffTiming 使用语义节奏，不用硬写章节号：只允许 immediate / near-term / mid-arc / slow-burn / endgame
- **铁律**：不要把"再次提到""换个说法重述""抽象复盘"当成推进。只有状态真的变了，才更新最近推进。只是出现过的旧 hook，放进 mention 数组。

【启用全员追踪时】
## 全员追踪
POST_SETTLEMENT 必须额外包含：本章出场角色清单、角色间关系变动、未出场但被提及的角色。

## 输出格式（必须严格遵循）

=== POST_SETTLEMENT ===
（简要说明本章有哪些状态变动、伏笔推进、结算注意事项；允许 Markdown 表格或要点）

=== RUNTIME_STATE_DELTA ===
（必须输出 JSON，不要输出 Markdown，不要加解释）
```json
{
  "chapter": 12,
  "currentStatePatch": {
    "currentLocation": "可选",
    "protagonistState": "可选",
    "currentGoal": "可选",
    "currentConstraint": "可选",
    "currentAlliances": "可选",
    "currentConflict": "可选"
  },
  "hookOps": {
    "upsert": [
      {
        "hookId": "mentor-oath",
        "startChapter": 8,
        "type": "relationship",
        "status": "progressing",
        "lastAdvancedChapter": 12,
        "expectedPayoff": "揭开师债真相",
        "payoffTiming": "slow-burn",
        "notes": "本章为何推进/延后/回收"
      }
    ],
    "mention": ["本章只是被提到、没有真实推进的 hookId"],
    "resolve": ["已回收的 hookId"],
    "defer": ["需要标记延后的 hookId"]
  },
  "newHookCandidates": [
    {
      "type": "mystery",
      "expectedPayoff": "新伏笔未来要回收到哪里",
      "payoffTiming": "near-term",
      "notes": "本章为什么会形成新的未解问题"
    }
  ],
  "chapterSummary": {
    "chapter": 12,
    "title": "本章标题",
    "characters": "角色1,角色2",
    "events": "一句话概括关键事件",
    "stateChanges": "一句话概括状态变化",
    "hookActivity": "mentor-oath advanced",
    "mood": "紧绷",
    "chapterType": "主线推进"
  },
  "subplotOps": [],
  "emotionalArcOps": [],
  "characterMatrixOps": [],
  "notes": []
}
```

规则：
1. 只输出增量，不要重写完整 truth files
2. 所有章节号字段都必须是整数，不能写自然语言
3. hookOps.upsert 里只能写"当前伏笔池里已经存在"的 hookId，不允许发明新的 hookId
4. brand-new unresolved thread 一律写进 newHookCandidates，不要自造 hookId
5. 如果旧 hook 只是被提到、没有真实状态变化，把它放进 mention，不要更新 lastAdvancedChapter
6. 如果本章推进了旧 hook，lastAdvancedChapter 必须等于当前章号
7. 如果回收或延后 hook，必须放在 resolve / defer 数组里
8. chapterSummary.chapter 必须等于当前章节号

## 关键规则

1. 状态卡和伏笔池必须基于"当前追踪文件"做增量更新，不是从零开始
2. 正文中的每一个事实性变化都必须反映在对应的追踪文件中
3. 不要遗漏细节：数值变化、位置变化、关系变化、信息变化都要记录
4. 角色交互矩阵中的"信息边界"要准确——角色只知道他在场时发生的事

## 铁律：只记录正文中实际发生的事（严格执行）

- **只提取正文中明确描写的事件和状态变化**。不要推断、预测、或补充正文没有写到的内容
- 如果正文只写到角色走到门口还没进去，状态卡就不能写"角色已进入房间"
- 如果正文只暗示了某种可能性但没有确认，不要把它当作已发生的事实记录
- 不要从卷纲或大纲中补充正文尚未到达的剧情到状态卡
- 不要删除或修改已有 hooks 中与本章无关的内容——只更新本章正文涉及的 hooks
- 第 1 章尤其注意：初始追踪文件可能包含从大纲预生成的内容，只保留正文实际支持的部分，不要保留正文未涉及的预设
- **伏笔例外**：正文中出现的未解疑问、悬念、伏笔线索必须在 hooks 中记录。这不是"推断"，而是"提取正文中的叙事承诺"。如果正文暗示了一个谜题/冲突/秘密但没有解答，那就是一个 hook，必须记录
```

### 工作步骤

1. **读 observations.md**（必读）+ 正文 + 所有真理文件。
2. **写 POST_SETTLEMENT**：人读摘要——本章哪些 hook 推进 / 回收 / 延后、关键状态变动、需要 Auditor 关注的注意事项。允许用要点和小表格。
3. **写 RUNTIME_STATE_DELTA**：JSON 增量。严格按 schema：
   - `chapter`: 整数
   - `currentStatePatch`: 只填本章实际变化的字段
   - `hookOps.upsert`: 只能引用现有 hookId
   - `hookOps.mention`: 只是被提到没有真实推进的 hookId
   - `hookOps.resolve` / `defer`: 已回收 / 延后
   - `newHookCandidates`: 新伏笔候选（不写 hookId，由 apply_delta 决定映射）
   - `chapterSummary`: 一行话摘要 + mood + chapterType（chapterType 必须是 genreProfile.chapterTypes 之一）
   - `subplotOps` / `emotionalArcOps` / `characterMatrixOps`: 增量操作数组
   - `notes`: 留给 Auditor 的提醒（可空）
4. **数值验算**（仅数值题材）：UPDATED_LEDGER 中每条资源变动都要满足 期初 + 增量 = 期末，三项写齐。
5. **校验自检**：发出 delta 前自查 8 条规则——尤其 hookId 不能编造、章节号必须整数、推进了的 hook 必须更新 lastAdvancedChapter、resolve/defer 不能塞 upsert。
6. **输出**：两个 `=== TAG ===` 块，DELTA 块用 ```json ``` 围栏。

## Output contract

- 写入 `story/runtime/settlement.md`，必须包含两个 `=== TAG ===` 块：先 `=== POST_SETTLEMENT ===` 人读摘要，后 `=== RUNTIME_STATE_DELTA ===` 机器读 JSON（`=== END ===` 可选；下游解析器对缺失 END、哨兵前后散文、缩进的 `===` 行都容忍）。
- DELTA JSON 必须能被 `scripts/apply_delta.py` 通过 schema 校验。
- 下游 3 段式解析（详见 [`references/schemas/runtime-state-delta.md` §1b](../schemas/runtime-state-delta.md)）会自动吸收常见小格式偏差（snake_case 键名、`"chapter": "12"` 字符串数字、首字母大写的枚举值、null 数组）；这些都进 `softFixes` 数组而**不要求** Settler 重跑。但 Settler 仍应**目标**输出 canonical 格式——别故意依赖 soft-fix 兜底。
- Schema 见 `references/schemas/runtime-state-delta.md`。
- apply_delta.py 校验通过后路由到具体真理文件：
  - `currentStatePatch` → `story/state/current_state.json` + `story/current_state.md`
  - `hookOps` → `story/state/hooks.json` + `story/pending_hooks.md`
  - `newHookCandidates` → 待映射，由 hook-arbiter 决定（v1 简化：人工或 Claude 决策）
  - `chapterSummary` → `story/state/chapter_summaries.json` + `story/chapter_summaries.md` 追加一行
  - `subplotOps` → `story/subplot_board.md`
  - `emotionalArcOps` → `story/emotional_arcs.md`
  - `characterMatrixOps` → `story/character_matrix.md`

### 推荐调用方式

```bash
# 单步：把 Settler 原始输出直接交给 apply_delta（推荐）
python scripts/apply_delta.py --book <book> --delta settlement.md --input-mode raw

# 或两步（调试时更直观）：
python scripts/settler_parse.py --mode raw --input settlement.md --out delta.json
python scripts/apply_delta.py --book <book> --delta delta.json
```

`--input-mode json`（默认）保持向后兼容——任何已经传干净 JSON 的旧调用方无需改动，仍会自动跑 Stage 2 soft-fix + Stage 3 schema 校验。

## Failure handling

下游解析器把失败分成三类，分别对应不同处理策略：

| 失败模式 | parseStage | 处理 | 重试上限 |
|---------|------------|------|---------|
| **哨兵完全缺失**（找不到 `=== RUNTIME_STATE_DELTA ===` 也找不到任何 JSON 块） | `extracted` | 把 `parserFeedback` 当 user 消息回喂 Settler，附明确的"请使用以下哨兵格式"提示重试 | 1 次 |
| **schema 软问题**（snake_case / 字符串数字 / 大写枚举 / 单条非数组 等） | 自动 → `schema` ok | 不重试。`softFixes` 落入 apply_delta.py 输出供日志审阅；下次写 Settler prompt 时可顺手提醒"请使用 canonical 命名" | 0 次 |
| **schema 硬问题**（必填字段缺失 / 类型完全错位 / 业务约束冲突 / soft-fix 后仍非法的枚举值） | `schema` | 把 `parserFeedback`（中文反馈块）作为 user 消息回喂 Settler，要求只修 `parserFeedback` 列出的字段，其它保持原样 | 1 次 |

其他失败：

- **状态校验反馈**（state-validator 报矛盾，例如 hook 被 resolve 但还出现在 upsert）→ 附 `validationFeedback` 块进 user 消息，重跑 1 次。**不允许改写正文**——只修 truth files。
- **数值不平**（期初 + 增量 ≠ 期末）→ 直接拒绝该 delta，重跑 1 次；仍不平由 Auditor 第 2 维度处理。
- **总重试上限**：每章 Settler 最多 2 次（哨兵缺失重试 + 一次 schema 重试不要叠加超过 2）。

### 调试技巧

`scripts/settler_parse.py` 是独立 CLI，**不**写真理文件，只跑 3 段式解析并把清洗后的 delta 输出到 `--out`。在线下排查 Settler 输出时优先用它：

```bash
python scripts/settler_parse.py --mode raw --input <settler-output.md> --out /tmp/cleaned.json
# 看 stdout 的 softFixes / issues / parserFeedback 即可定位问题
```

加 `--strict` 把任何 softFixes 也视为失败（exit 3），用于回归测试 Settler prompt 是否在产生不必要的格式偏差。

## 注意事项

- **不要发明 hookId**：upsert 里的 hookId 必须在当前 hooks.json 里存在；新候选一律走 newHookCandidates。
- **mention vs upsert**：换说法、抽象复盘、再次提到旧 hook 但状态没变 → mention；只有真实状态/事实/风险变化 → upsert + 更新 lastAdvancedChapter。
- **lastAdvancedChapter 严格等于当前章号**（如果本章推进了）。
- **章节号统一是整数**：自然语言（"上一章"、"第三卷开端"）一律拒绝。
- **不删除无关 hook**：上一章的 hook 与本章无关时不要碰它。
- **第 1 章特殊**：初始 hooks.json 里可能含架构师预生成的 startChapter=0 行——本章 Settler 只能更新本章正文实际触及的，预设的留着别动。
- **enableFullCastTracking**：bookRules 开了就要在 POST_SETTLEMENT 加"本章出场角色清单 / 角色间关系变动 / 未出场但被提及的角色"三段。
- **English book**：所有真理文件输出（state card / hooks / summaries / subplots / emotional arcs / character matrix）切换为英文，但 `=== TAG ===` 标记保持不变。
- **payoffTiming 枚举**：只允许 `immediate / near-term / mid-arc / slow-burn / endgame`，不写章号。
- **chapterType 来自题材**：从 `genreProfile.chapterTypes` 选一个（首章是首示项；通常含"主线推进 / 日常过渡 / 高潮 / 揭秘 / 战斗"等）。
