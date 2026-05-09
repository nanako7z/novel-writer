# Phase 07: Settler（状态结算 → RuntimeStateDelta）

> ⛔ **硬约束 / 不跳步**：
> 1. **前置**：`observations.md` 已落盘；上章 `docops_drift.json`（如存在）**必须读**——它是 Settler 的检查范围，**禁止**甩给下章 drift
> 2. **本阶段必跑**：**主动性铁律 5 项**逐项审视——`current_focus` / `character_matrix` / `emotional_arcs` / `subplot_board` / `story/roles/<slug>.md`，每一项都要给出"是否需更新 + 不更新的理由"；`docOps` 字段必填（哪怕 `{}`，也是"我已显式查过"的承诺）
> 3. **退出条件**：`story/runtime/chapter-{NNNN}.delta.json` 落盘 + `apply_delta.py` exit 0；**禁止**直接 Edit `story/state/*.json`（脏写）
> 4. **重试规则**：每章总重试上限 **2 次**——parser 失败注入 `parserFeedback`、治理 critical 注入 `governanceFeedback`、状态矛盾 / 数值不平注入 `validationFeedback`；**禁止**裸重发原 prompt

## 何时进入

主循环 step 9，Observer 之后、Normalizer/Auditor 之前。Settler 把 Observer 观察日志 + 章节正文与现有真理文件做增量合并，输出 `=== POST_SETTLEMENT ===`（人读摘要）+ `=== RUNTIME_STATE_DELTA ===`（机器读 JSON）。Delta JSON 经 `scripts/apply_delta.py` 校验后落到真理文件。

## Inputs

Claude 在这一阶段需要读：

- 刚写完的章节正文（`chapters/<NNNN>.md`）+ 章节标题、章节号
- `story/runtime/observations.md` ——Observer 产物（必读，作为 user 消息的 observations 块）
- `story/current_state.md` 与 `story/state/current_state.json` ——当前状态卡
- `story/particle_ledger.md` ——资源账本（仅数值题材；不在 init 模板中，由 Architect 按需建出；缺失则跳过这条输入）
- `story/pending_hooks.md` 与 `story/state/hooks.json` ——伏笔池
- `story/chapter_summaries.md` 与 `story/state/chapter_summaries.json` ——已有章节摘要
- `story/subplot_board.md` ——支线进度板
- `story/emotional_arcs.md` ——情感弧线
- `story/character_matrix.md` ——角色交互矩阵
- `story/outline/volume_map.md` ——卷纲（用于参照本章是否进卷尾）
- `book.json` + `references/genre-profiles/<genre>.md` ——题材开关（numericalSystem / chapterTypes）+ bookRules.enableFullCastTracking 开关
- 上一轮 `state-validator` 反馈（如果是重跑）
- `story/runtime/docops_drift.json`（如果存在）——上章 step 11.0c 跑 `docops_drift.py` 产的指导 md 漂移候选；Settler 在产 docOps 时**应优先**消化这些候选（current_focus 过时 / 角色档案缺失 / 支线未登记 / 情感弧线停滞）

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
- **指导文件需要同步更新的地方**（见下文 §"指导文件维护"）

## 指导文件维护（docOps）

### 上章 docops_drift 候选的消费规则

如果存在 `story/runtime/docops_drift.json`（上章 step 11.0c 跑 `docops_drift.py --write` 产的）——这是**上章遗留的"应改未改"建议清单**，给本章 Settler 兜底用。处理规则：

1. **必须显式处置每一条候选**：要么在本章 docOps 里改、要么在 POST_SETTLEMENT "本章修改的指导文件"段下方新开一段"忽略的 drift 候选"，**逐条写明为什么不改**（"该 hook 本章已 resolve，drift 已过期" / "该角色本章未出场，留到下章再补" 等）。**不允许**沉默忽略——drift 没消费完会越积越多。
2. **drift 不是免检通行证**：本章正文实际推动了的指导 md 修改仍然由 §"主动性铁律"5 项自检触发，drift 候选只是补充提醒。本章正文未触发的项目即使在 drift 里也**不要**盲改（否则会替作者补未发生的设定）。
3. **drift 的权威性 < 本章正文**：如果 drift 候选与本章正文实际推动方向冲突（drift 说"焦点 X 应该删"，本章正文却兑现了 X 的下半），按本章正文产 docOps，drift 那条放进"忽略"段写明理由。
4. **drift 文件本身不需要 Settler 改**——它由下章 step 11.0c 重新生成；本章只读不写。

### 本章正文驱动的 docOps

本章正文如果**真实推动了**下列指导 md 的内容，把对应变更打包成 `docOps` 块，与 hookOps 同一 delta 落盘（见 [runtime-state-delta.md §7b](../schemas/runtime-state-delta.md#7b-docops-子-schema指导-md-维护)）。

可写白名单（这些是作者域，但允许 Settler 通过 docOps 维护）：

| 文件 | 何时改 |
|------|--------|
| `story/current_focus.md` | 本章兑现了"接下来 1-3 章"的某条焦点 → 推进、删除或新增下条 |
| `story/character_matrix.md` | 角色关系数值/边界变了（吵架、和解、得知秘密、信任度位移） |
| `story/emotional_arcs.md` | 本章是某角色情感弧线的关键节点（转折、低谷、爆发） |
| `story/subplot_board.md` | 支线进度推进 / 状态切换 |
| `story/style_guide.md` | 仅当用户在主对话明示"调整文风" → 此时由 user-directive 路径走，Settler 通常不动 |
| `story/outline/story_frame.md` / `story/outline/volume_map.md` | Settler **不动**——这两个由 Architect 大改时 cascade |
| `story/roles/<slug>.md` | `patch_role_section` / `rename_role` 改已有角色；新角色一律走 `newRoleCandidates` 候选池（不要直接 `create_role`，让 `role_arbitrate.py` 仲裁） |

**绝对禁止**（黑名单，自动通道永久只读，schema 阶段就 fail）：
- `story/author_intent.md`、`story/fanfic_canon.md`、`story/parent_canon.md`、`book.json#bookRules` —— 这些是作者宪法。如果 LLM 在 docOps 里写了这些键，整批被拒。

**铁律（正确性）**：
- docOps 只改本章正文**实际触发**的指导 md，不要替作者补设定。"这角色应该再加一段背景"不是触发，是发挥；不准。
- `reason` 必填，≤ 200 字符；说清"本章哪一段哪句话让你想改"。
- 单 batch 总条数 ≤ 20。超限直接 schema-fail。

**铁律（主动性）**：每章 Settler 在产 delta 前必须**显式枚举并逐项检查**下面这份固定清单是否应更新。不能因为"本章好像没什么大事"就空 docOps 了事，也不能把检测责任甩给下一章 [docops_drift](./00-orchestration.md) 扫描——那是事后兜底，不是免检通行证。

| # | 文件 | 自检问题 |
|---|------|----------|
| 1 | `story/current_focus.md` | 当前章目标 / 阻力 / 下 1-3 章焦点是否变了？ |
| 2 | `story/character_matrix.md` | 角色关系强度 / 站位 / 信任度是否位移？ |
| 3 | `story/emotional_arcs.md` | 本章是否触发任一角色的情绪弧关键节点（转折/低谷/爆发）？ |
| 4 | `story/subplot_board.md` | 本章是否推进、开启或切换某条副线状态？ |
| 5 | `story/roles/<slug>.md` | 本章是否暴露角色新设定 / 新关系 / 新别名 / 改名？ |

如果上一章 `story/runtime/docops_drift.json` 存在，本章 Settler 必须先把里面的候选项纳入自检范围（详见 [00-orchestration.md step 9](./00-orchestration.md)）。

如果**全部 5 项**自检后确实无改动，仍要输出 `docOps: {}` —— 这是承诺"我已经显式查过"，不是"我懒得查"。POST_SETTLEMENT "本章修改的指导文件"段写"无"也必须建立在这次显式自检之上。

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

## 章末勾子记录（cliffhangerEntry）

每章 Settler **必须**输出 `cliffhangerEntry`，描述本章最后一段是用什么形式收尾的——`apply_delta.py` 把它落到 `story/state/cliffhanger_history.json`，下章 Planner 读最近 6 条来抑制重复套路。

12 类 type（详见 [runtime-state-delta.md §7c](../schemas/runtime-state-delta.md#7c-cliffhangerentry-子-schema章末勾子记录)）：
- `ambush` 突遇袭击 / 伏击
- `revelation` 真相揭露（信息层）
- `betrayal` 背叛
- `ultimatum` 被迫选择 / 最后通牒
- `encounter` 强敌或重要人物登场
- `transformation` 突破 / 质变
- `loss` 失去重要的人或物
- `discovery` 重大发现
- `decision` 主角主动决断
- `secret-exposed` 身份 / 秘密被识破
- `stakes-raised` 风险陡然升级
- `none` 纯铺垫章无收尾勾子（intensity = 1，brief 写"纯铺垫无收尾"）

`intensity` ∈ [1, 5]：1 = 日常温和，3 = 中等张力，5 = 卷高潮级。`brief` ≤ 50 字，一句话描述用了哪一招（不只是 type 标签，要让 Planner 能"看懂"招数本身）。

**判定铁律**：
- 不要把 `revelation` 当万能筐——只有"信息真的从未知变已知、且对主角处境有具体影响"才算 revelation。"主角又一次确认 X 是反派" → 不算 revelation，是 mention。
- `secret-exposed` 与 `revelation` 区分：前者是**主角自己的身份/秘密被别人识破**，视角在敌方；后者是**主角发现别人的真相**，视角在主角。
- type 必须从枚举里选；不在枚举里就选最贴近的那个 + 用 brief 解释——**绝不**自己造新枚举值。

【启用全员追踪时】
## 全员追踪
POST_SETTLEMENT 必须额外包含：本章出场角色清单、角色间关系变动、未出场但被提及的角色。

## 输出格式（必须严格遵循）

=== POST_SETTLEMENT ===
（简要说明本章有哪些状态变动、伏笔推进、结算注意事项；允许 Markdown 表格或要点）

### 本章修改的指导文件
（本章如果产了 docOps，必须列在这里；每条一行：`file / op@anchor / reason`。没产 docOps 写"无"。这一段是给作者快速看到自动改了什么——是透明度承诺。）

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
  "cliffhangerEntry": {
    "type": "revelation",
    "intensity": 4,
    "brief": "白衣翁玉牌竟是反派那枚的另一半"
  },
  "notes": [],
  "docOps": {
    "currentFocus": [
      {
        "op": "replace_section",
        "anchor": "## Active Focus",
        "newContent": "- 当前章次：13\n- 接下来 1-3 章要做的事：\n  - （本章兑现了 X 后，下条焦点：Y）\n",
        "reason": "ch12 兑现 hook X，焦点条向后顺移",
        "sourcePhase": "settler",
        "sourceChapter": 12
      }
    ],
    "characterMatrix": [
      {
        "op": "upsert_row",
        "key": ["林秋","二师姐"],
        "fields": { "intimacy": 4, "lastInteraction": "ch12", "notes": "拆穿假信任后裂痕扩大" },
        "reason": "ch12 二师姐被识破伪装",
        "sourcePhase": "settler",
        "sourceChapter": 12
      }
    ]
  },
  "newRoleCandidates": [
    {
      "name": "白衣女子",
      "sourceChapter": 12,
      "justification": "ch12 首次有名出场，林秋拜师对象，后续会推动主线",
      "tier": "次要角色"
    }
  ]
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
9. **docOps 只能改本章正文实际触发的指导 md**，不要替作者补未发生的设定；不要动 author_intent / fanfic_canon / parent_canon / book_rules（黑名单已硬拒绝）
10. **docOps 每条都要带 `reason` (≤200 chars) + `sourcePhase: "settler"` + `sourceChapter: <整数>`**；单 batch 总条数 ≤ 20
11. **`docOps` 字段必填**（哪怕是空对象 `{}`）——这一条是强制的"显式自检"承诺：每章 Settler 必须**主动检查过**"本章是否触发了任何指导 md 改动"才能输出 delta。如果四个触发场景（焦点兑现 / 关系数值变 / 情感节点 / 支线推进）都不命中，就明确写 `"docOps": {}`，**不要省略字段**——省略会被 soft-fix 自动补 `{}` 但记一笔 softFix 提醒；目标是 canonical 输出，不要靠兜底。
12. **`SectionReplaceOp.newContent` 不要包含 anchor 自己那一行**：`anchor` 字段已经声明了节标题，`newContent` 只是节内容（H2 标题之下的正文）。错误示例（**不要这样**）：
    ```jsonc
    { "op": "replace_section", "anchor": "## Active Focus",
      "newContent": "## Active Focus\n- 当前章次：12\n..." }   // ❌ 重复了 anchor
    ```
    正确示例：
    ```jsonc
    { "op": "replace_section", "anchor": "## Active Focus",
      "newContent": "- 当前章次：12\n..." }                    // ✅
    ```
    apply_delta 会自动剥离首行的重复 anchor 并记 softFix；但反复犯会让文件结构腐烂、`doc_changes.log` 膨胀。同理 `newContent` 中部也**不要**夹任何 `## ` / `### ` 行（除非确实想引入新的子节）。
13. **`TableRowOp.fields` 不要包含主键列**：`characterMatrix` 主键 = (charA, charB)，`emotionalArcs` 主键 = (character, chapter)，`subplotBoard` 主键 = subplotId。这些列**只能**通过 `op.key` 设置，不能在 `fields` 里改——改主键意味着把行迁到另一处身份，等价于 delete+upsert。apply_delta 会自动忽略 fields 里的主键列并记 softFix。

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
   - `subplotOps` / `emotionalArcOps` / `characterMatrixOps`: 增量操作数组（已升级为 upsert 语义，由 doc_ops 内部统一处理）
     - **`emotionalArcOps` 每条必须 6 列齐写**：`character`, `chapter`, `emotionalState`, `triggerEvent`, `intensity` (1-10 整数), `arcDirection` (`rising` / `falling` / `stable` / `turning`)。缺/空任一列会被 schema 拒绝，触发 Settler 重跑（权威定义见 [runtime-state-delta.md §emotionalArcOps](../schemas/runtime-state-delta.md)）。
   - `notes`: 留给 Auditor 的提醒（可空）
   - `docOps`: 指导 md 增量（见上 §"指导文件维护"）；只填本章正文实际触发的；可省略
   - `newRoleCandidates`: 新角色候选（不写 slug，等仲裁）；可省略
4. **数值验算**（仅数值题材）：UPDATED_LEDGER 中每条资源变动都要满足 期初 + 增量 = 期末，三项写齐。
5. **校验自检**：发出 delta 前自查 8 条规则——尤其 hookId 不能编造、章节号必须整数、推进了的 hook 必须更新 lastAdvancedChapter、resolve/defer 不能塞 upsert。
6. **输出**：两个 `=== TAG ===` 块，DELTA 块用 ```json ``` 围栏。

## Output contract

- 写入 `story/runtime/settlement.md`，含 `=== POST_SETTLEMENT ===`（人读摘要）+ `=== RUNTIME_STATE_DELTA ===`（机读 JSON，```json``` 围栏，`=== END ===` 可选）
- DELTA 必须通过 [schemas/runtime-state-delta.md](../schemas/runtime-state-delta.md) 校验；3 段解析会吸收 snake_case / 字符串数字等常见偏差到 `softFixes`，但 Settler 应**目标输出 canonical 格式**，不依赖 soft-fix 兜底
- apply_delta.py 校验后按字段路由到对应真理文件（路由表见 schema §8）

### 推荐调用方式

```bash
python scripts/apply_delta.py --book <book> --delta settlement.md --input-mode raw   # 单步推荐
python scripts/settler_parse.py --mode raw --input settlement.md --out delta.json    # 调试不写盘
```

## Failure handling

| 失败模式 | parseStage | 处理 | 重试 |
|---|---|---|---|
| 哨兵完全缺失 | `extracted` | `parserFeedback` 回喂 + 强调哨兵格式 | 1 次 |
| schema 软问题（snake_case / 字符串数字 / 大写枚举 等） | 自动 ok | 进 softFixes，**不重试** | 0 次 |
| schema 硬问题（必填缺失 / 类型错位 / 业务约束 / soft-fix 后仍非法枚举） | `schema` | `parserFeedback` 回喂，只修列出字段 | 1 次 |

其他：状态校验矛盾（hook 同时 resolve + upsert）→ 附 `validationFeedback` 重跑 1 次（**不改正文**只修 truth file）；数值不平 → 拒 delta 重跑 1 次，仍不平交 Auditor。**每章总重试上限 2 次**。

### 原子性 & 幂等性（apply_delta 契约）

- **失败 = 全无应用**：apply_delta 的 3 阶段 parser 或字段路由任一步失败 → 整批 delta 不写入真理文件（staging swap 模式）。Settler 重跑时把上一份 delta 当作"从未应用"处理，**不需要**手动撤销 hookOps / docOps 的部分写入。
- **成功后重跑 = no-op**：若上一份 delta 已经成功（`manifest.appliedDeltaHash` 命中），apply_delta 直接返回 already-applied，**不会**再推进 lastAdvancedChapter / 再追加 cliffhangerEntry / 再触发 hook promote。Settler 因下游需要新增字段而重跑时，应基于**最新真理状态**产新的 delta（hash 不同），不要复用旧 delta 文件。
- **重跑 prompt 必须注入失败原因**：`parserFeedback` / `governanceFeedback` / `validationFeedback` 任一存在都要拼进 user 消息——主循环不变量 #8 的"不允许只重发原 prompt"在 Settler 同样适用。

### 调试技巧

`settler_parse.py` 是独立 CLI（不写盘），看 stdout 的 softFixes / issues / parserFeedback 定位问题。`--strict` 把任何 softFixes 也算失败（exit 3），用于回归测试 prompt 是否在产生不必要偏差。

## 注意事项（与系统 prompt 13 条规则不重复的部分）

- **不删除无关 hook**：上一章的 hook 与本章无关时不要碰它
- **第 1 章特殊**：初始 hooks.json 里可能含 architect 预生成 `startChapter=0` 行——只更新本章实际触及的，预设的留着
- **`enableFullCastTracking`**（bookRules 开关）：开了就在 POST_SETTLEMENT 加"出场清单 / 关系变动 / 提及但未出场"三段
- **English book**：真理文件全切英文，但 `=== TAG ===` 哨兵保持不变
- **`payoffTiming` 枚举**：只能是 `immediate / near-term / mid-arc / slow-burn / endgame`，禁写章号
- **`chapterType` 必须从 `genreProfile.chapterTypes` 选**（首章是首示项）
