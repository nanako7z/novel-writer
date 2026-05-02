# Cadence Policy（节奏 / 爽点节拍政策）

> 题材 profile 里那一行 `pacingRule:` 是**全书静态规则**——它告诉 Writer "每 3-5 章应有一次小突破"，但它不知道我们**实际**走到第几章、距离上次爽点已经隔了几章。Cadence Policy 把节奏管控分成 4 个层次（chapter / volume / book / arc），用 `cadence_check.py` 把"该不该埋个爽点"这件事**算成数字**喂给 Planner。

## 4 层节拍模型

| 层级 | 周期 | 决策点 | 主控数据源 |
|---|---|---|---|
| **chapter beat** | 每 3-5 章 | 该不该来一次爽点 / 突破 / 关键揭示？ | `chapter_summaries.json#chapterType / mood` |
| **volume beat** | 一卷（30-60 章） | 接近卷中点 → 关键转折；接近卷尾 → 高潮 | `volume_map.md` 的卷段 + 当前 chapterNo |
| **book beat** | 一书（200-1000 章） | 接近 book.targetChapters 的 50% / 80% 必须升级冲突等级 | `book.json#targetChapters` + manifest |
| **arc beat** | 不固定（5-15 章为一个故事弧） | 弧内：开 → 涨 → 收；弧间："余响"章过渡 | `current_focus.md` + chapter_memo `arcTransition` 标志 |

`cadence_check.py` **当前只精算 chapter + volume**——book / arc 层 Planner 自己结合 `current_focus.md` 判。后两层加进来后行为会扩展，先用前两层 cover 80% 场景。

## 题材默认 cadence（从 `pacingRule` 抽取的具体节拍）

下表把 15 个内置题材 profile 的 `pacingRule` 字段**精确化**——给出可量化的"satisfactionGap 高压阈值"，供 `cadence_check.py` 的输出对照：

| genre id | pacingRule（profile 原文）| 高压阈值 | 中压阈值 | 主要 satisfactionTypes |
|---|---|---|---|---|
| `xianxia` | 修炼/悟道与战斗交替，每3-5章一次小突破 | 5 | 3 | 悟道突破/斗法碾压/法宝收获 |
| `xuanhuan` | 战斗 + 数值飞升，每章必有冲突或晋级 | 4 | 2 | 击杀/晋级/收徒/打脸 |
| `urban` | 商战 / 社交，每 3-4 章一次反转 | 5 | 3 | 信息揭示/合同签下/社交反转 |
| `horror` | 氛围递进 + 揭示，节奏不快但每章必长一格压力 | 6 | 4 | 真相揭示/逃脱/反杀 |
| `cultivation` (en) | breakthrough sequence every 3-5 chapters | 5 | 3 | breakthrough/insight/duel-victory |
| `litrpg` | level-up rhythm tight, ≤ 3 章必有数值进展 | 3 | 2 | level-up/loot/quest-clear |
| `progression` | every tier feels fundamentally new, ≤ 5 章 | 5 | 3 | tier-up/new-power-class |
| `tower-climber` | 3-8 章一段 floor arc | 6 | 4 | floor-clear/boss-defeat |
| `system-apocalypse` | 早期生存压力每章；中期 ≤ 4 章 | 4 | 2 | survival-win/faction-coup/kill |
| `dungeon-core` | 双 POV，每 4-6 章 invader vs defender 切换 | 6 | 4 | trap-success/expansion |
| `isekai` | 文化冲突 + 探索，每 4-6 章一次"原世界对照" | 6 | 4 | cultural-bridge/skill-aha |
| `cozy` | 慢节奏，每 5-8 章一次社区进展 | 8 | 5 | community-bond/seasonal-shift |
| `romantasy` | 浪漫节拍每幕断点，每 3-4 章一次关系推进 | 4 | 2 | confession/intimacy/jealousy-flare |
| `sci-fi` | 探索 + 政治，每 5-7 章一次世界观揭示 | 7 | 4 | first-contact/tech-reveal/coup |
| `other` | 兜底；3-5 章一次有意义改变 | 5 | 3 | 无固定列表 |

> **注**：自从结构化 cadence schema 落地（见 `references/schemas/cadence-policy.md`），脚本会优先读题材 frontmatter 里的 `cadence.satisfactionWindow` 并推导阈值（`high = window`，`medium = ceil(window*0.6)`）。当题材 profile 没写 `cadence:` 子对象时，脚本会查上表的"高压阈值"列作为 `satisfactionWindow` 的推断默认值；再退一步是常量 `PRESSURE_HIGH=5 / PRESSURE_MED=3`。如要按题材精调，**首选方式**是在 profile frontmatter 里加 `cadence:` 子对象（最干净），其次才是改本表 / 改脚本。

## `cadence_check.py` 怎么算

```bash
python {SKILL_ROOT}/scripts/cadence_check.py \
  --book <bookDir> \
  --current-chapter N \
  [--lookback 20] [--json]
```

读：

1. `book.json#genre` → 加载 `templates/genres/<genre>.md`（缺则回退 `other.md`），抽 `chapterTypes / satisfactionTypes / pacingRule`
2. `story/state/chapter_summaries.json` 最近 `--lookback`（默认 20）条
3. `story/outline/volume_map.md` 解析卷段（缺则回退 `volume_outline.md`）

算：

- **`chaptersSinceSatisfaction`**：从最近 lookback 条里向前找最后一条 `chapterType` 或 `mood` 或 `events` 或 `title` 含任一 `satisfactionTypes` 子串的章；当前章号减它就是 gap。一次都没找到 → gap = `current_chapter - 最早章号`
- **`satisfactionPressure`**：`gap >= 5` → high；`gap >= 3` → medium；否则 low
- **`recommendedChapterTypes`**：高/中压时按"和 satisfactionTypes 子串重叠多的优先"排，永远剔除最近最高频出现的那个 chapterType（避免单调）；低压时按"最少出现"排
- **`pacingBeatPressure`**（隐含）：最近 5+ 章里"过渡章 / 日常章"占比 ≥ 5 时输出"transitional 警告" → `pacingNotes` 里看
- **`volumeBeatStatus`**：当前章在 volume 段内的位置——
  - `progress ≈ 0.5 ± 0.1` → "approaching mid-point"
  - `progress >= 0.85` → "climax window"
  - `progress >= 1.0` → "past volume end"（说明卷段没更新）
  - 其余按"early / late buildup"叙述

输出 schema：

```json
{
  "currentChapter": 12,
  "currentVolume": {"index": 1, "name": "...", "startCh": 1, "endCh": 30},
  "chaptersSinceSatisfaction": 6,
  "satisfactionPressure": "high",
  "lastSatisfactionChapter": 6,
  "recommendedChapterTypes": ["战斗章", "悟道章"],
  "recommendedNext": {
    "chapterType": "战斗章",
    "satisfactionType": "斗法碾压",
    "reasoning": "band=middle; target 4-ch satisfaction cadence; gap=6; pressure=high → satisfactionType prioritized"
  },
  "fatigueAlerts": [
    {"pattern": "连续 5 章无爽点", "action": "satisfactionEmergency", "evidence": {"gap": 6, "window": 5}}
  ],
  "cadencePolicy": {"satisfactionWindow": 5, "source": "embedded"},
  "pacingNotes": ["genre pacingRule: 修炼/悟道与战斗交替...", "satisfaction gap = 6 chapters → high pressure..."],
  "volumeBeatStatus": "approaching mid-point (ch 12 of 1-30)",
  "volumeBand": "middle",
  "satisfactionTypes": [...],
  "chapterTypes": [...],
  "pacingRule": "...",
  "lookbackChapters": [...]
}
```

> **结构化 cadence 政策**（`cadence:` 子对象）的字段定义见 `references/schemas/cadence-policy.md`。`recommendedNext` 与 `fatigueAlerts` 都依赖于它；profile 缺失时按推断默认值跑（行为见 schema 文 §2）。

## Planner 怎么消费

详见 `references/phases/02-planner.md` "Inputs" 一节里追加的 cadence 步骤。简版三条：

1. `satisfactionPressure == "high"` → memo `## 当前任务` 必须直接对应一个 satisfactionType；不许写"日常 / 整顿 / 走访"这类绵软任务
2. `volumeBeatStatus` 含 "approaching mid-point" 或 "climax window" → memo `## 章尾必须发生的改变` 至少含一条**方向级**改变（不只是位置/物品）
3. `recommendedChapterTypes` 第一名 → memo frontmatter 隐含的"建议章节类型"——Writer 在 §14 PRE_WRITE_CHECK 会看到，作为 chapterType 的 default

## 推荐阈值（用于人类调参，不是脚本硬编码）

| 信号 | 推荐阈值 | 为什么 |
|---|---|---|
| satisfaction high pressure | gap ≥ 5 | 网文读者 5 章无新鲜感就开始流失（参 起点 / 番茄 数据） |
| satisfaction medium | gap ≥ 3 | 提前一档就让 Planner 排队，不要等到 high 才急 |
| transitional dominant | 5/20 章是过渡 | ≥ 25% 过渡密度已经偏高 |
| mid-point band | progress ∈ [0.4, 0.6] | 卷的中点是结构转折常用区，10% 容差 |
| climax band | progress ≥ 0.85 | 最后 15% 是收束区，必须开始降压 |

## 扩展点

- **按题材切换阈值**：~~常量~~ 已经替换为"读题材 `cadence.satisfactionWindow`"路径，详见 `references/schemas/cadence-policy.md`。如需进一步细化某题材的中/高压比例，可改 `cadence_check.classify_pressure()` 里的 `ceil(window*0.6)` 比例
- **arc beat 层**：从 chapter_memo `arcTransition` / `cliffResolution` 标志反推；当前未实现
- **book beat 层**：拿 `manifest.lastAppliedChapter / book.targetChapters` 算 `bookProgress`；接近 50% / 80% 时输出"全书冲突等级该升一档"提示
- **题材级 fatigueGuards**：`cadence:` 里可加自定义触发器；当前 `cadence_check.py` 实现了 `force-switch-type` / `satisfactionEmergency` / `lower-tension` 三个 action，新增 action 须在 `evaluate_fatigue_guards()` 里挂判定逻辑

## 注意事项

- `cadence_check.py` 是 **read-only**——不写真理文件，不调 LLM
- 它的 `recommendedChapterTypes` 是**建议**，Planner 有最终决策权（作者意图 / 卷纲 / hook 账本可以否决）
- 如果 `book.json#genre` 是 catalog 之外的 id，会回退 `other.md`，satisfactionTypes 会变成兜底列表（很短或为空）→ 此时 `pacingRule` 也会缺失，gap 计算变得不准。**写自定义题材一定要填这两个字段**
- 历史不足时（< 3 条 summaries）：脚本输出 pressure="low"，gap=0；Planner 不要因此放松——本来就是开局，按"黄金三章"模板走
