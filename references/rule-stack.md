# Rule Stack（四级规则栈）

> 端口自 `inkos` 的 `utils/context-assembly.ts` 中 `buildGovernedRuleStack`（L36-83），以及 `models/input-governance.ts` 中 `RuleStackSchema` / `ActiveOverride` 定义。本文档说明四级规则的优先级、覆盖契约、写入位置，以及 Writer / Reviser / Auditor 三个阶段如何消费这套栈。

## 四层规则与 precedence

inkos 的 `RuleStack.layers` 字段固定为四层，precedence 越高越硬：

| ID | name | precedence | scope | 来源 | 内容举例 |
|----|------|-----------:|-------|------|----------|
| L1 | hard_facts | 100 | global | `GenreProfile.rules`（题材级） | 玄幻禁止现代物理术语；都市禁止超自然金手指；网文章末必须留钩 |
| L2 | author_intent | 80 | book | `book_rules.md` 的 YAML frontmatter | 主角不杀女、CP 锁定、年代不晚于 1990、敏感词额外白名单 |
| L4 | current_task | 70 | local | Planner ChapterMemo / 当前 directive | 本章必须见到老李身份揭穿；本章 POV 锁定主角 |
| L3 | planning | 60 | arc | `ChapterIntent.mustKeep` / `mustAvoid` / `styleEmphasis` | 本卷需推进伏笔 H-007；近 5 章避免战斗场景 |

注意：在数组中 `L3` 排在 `L4` 之前，但 precedence L4(70) > L3(60)。这是 inkos 故意的——L4（当前任务，由 Planner 生成）可以**单向覆盖** L3（编排层），因为 Planner 已经把"本章相对于本卷的偏移"想清楚了；但 L4 不能越级覆盖 L1 / L2。

> L1 是最硬（题材护栏）、L2 是全书宪法、L3 是编排层默认值、L4 是本章 directive。L4 → L3 是被允许的覆盖，L4 → L2 / L1 被拒绝。

## 覆盖契约（override edges）

`RuleStack.overrideEdges` 显式声明哪些覆盖被允许：

| from | to | allowed | scope |
|------|----|---------|-------|
| L4 | L3 | true  | current_chapter |
| L4 | L2 | false | current_chapter |
| L4 | L1 | false | current_chapter |

> 语义（逐字端口自 `context-assembly.ts` L39-50 的注释 + L76-80 的数据）：
>
> "L4 → L3: per-chapter prohibitions narrow the planning layer for this chapter only. mustAvoid items come from rules-reader prohibitions + current_focus avoid section (planner.collectMustAvoid)."
>
> "L4 → L3: planner-issued style emphasis is also a per-chapter override on the planning layer. Style emphasis surfaces things like POV tightness or character-conflict focus that the writer must honor this chapter."

`activeOverrides` 是当前章实际生效的覆盖记录列表，每条形如：

```json
{
  "from": "L4",
  "to": "L3",
  "target": "chapter:42/mustAvoid",
  "reason": "本章避免战斗场景（来自 current_focus.avoid）"
}
```

reason 字段由 `truncateForOverrideReason` 处理：折叠空白后截断到 80 字符，超长截断尾部加 "…"。每条 mustAvoid / styleEmphasis 都生成一条 ActiveOverride。

## Sections（按硬度分桶的真理文件清单）

`RuleStack.sections` 把当前生效的真理文件按"硬度"分三档，供下游阶段渲染到 prompt：

```json
{
  "hard":       ["story_frame", "current_state", "book_rules", "roles"],
  "soft":       ["author_intent", "current_focus", "volume_map"],
  "diagnostic": ["anti_ai_checks", "continuity_audit", "style_regression_checks"]
}
```

- **hard**：违反即 critical（结构 / 状态 / 规则 / 角色档案）；
- **soft**：违反给 warning（作者意图 / 阶段焦点 / 卷大纲）；
- **diagnostic**：诊断类（去 AI 味、连贯审计、风格回归），由 reviewer 本身跑出 issue。

> Phase 5 名称迁移：`story_frame` 替代旧版 `story_bible`；`volume_map` 替代旧版 `volume_outline`。

## L4 → L3 override 的具体生成路径

`buildGovernedRuleStack(plan, chapterNumber)` 按下面顺序拼 `activeOverrides`：

1. 遍历 `plan.intent.mustAvoid[]`：每条生成 `{ from: "L4", to: "L3", target: "chapter:<N>/mustAvoid", reason: <truncated item> }`；
2. 遍历 `plan.intent.styleEmphasis[]`：每条生成 `{ from: "L4", to: "L3", target: "chapter:<N>/styleEmphasis", reason: <truncated item> }`；
3. `mustKeep[]` 不进 ActiveOverride（它走 hard 区，由 Composer 把对应真理文件喂进 ContextPackage）。

## Compose 阶段产出的 JSON shape

完整 `RuleStack` JSON（由 `RuleStackSchema.parse` 校验落地）：

```json
{
  "layers": [
    { "id": "L1", "name": "hard_facts",     "precedence": 100, "scope": "global" },
    { "id": "L2", "name": "author_intent",  "precedence":  80, "scope": "book"   },
    { "id": "L3", "name": "planning",       "precedence":  60, "scope": "arc"    },
    { "id": "L4", "name": "current_task",   "precedence":  70, "scope": "local"  }
  ],
  "sections": {
    "hard":       ["story_frame", "current_state", "book_rules", "roles"],
    "soft":       ["author_intent", "current_focus", "volume_map"],
    "diagnostic": ["anti_ai_checks", "continuity_audit", "style_regression_checks"]
  },
  "overrideEdges": [
    { "from": "L4", "to": "L3", "allowed": true,  "scope": "current_chapter" },
    { "from": "L4", "to": "L2", "allowed": false, "scope": "current_chapter" },
    { "from": "L4", "to": "L1", "allowed": false, "scope": "current_chapter" }
  ],
  "activeOverrides": [
    { "from": "L4", "to": "L3",
      "target": "chapter:42/mustAvoid",
      "reason": "本章避免战斗场景" },
    { "from": "L4", "to": "L3",
      "target": "chapter:42/styleEmphasis",
      "reason": "POV 锁定主角内心独白" }
  ]
}
```

落地：Compose 阶段（`references/phases/03-composer.md`）调用 `buildGovernedRuleStack` 后，连同 `ContextPackage` 与 `ChapterTrace` 一起作为 reduced control input 喂给下游。

## Writer / Reviser / Auditor 如何消费

三个阶段都接收 `RuleStack` 作为可选 option，并在 prompt 中渲染 "Governed Control Stack" / "本章控制输入" 段（参 `continuity.ts` L717-759）：

```
## 本章控制输入（由 Planner/Composer 编译）
<chapterIntent 文本>

### 已选上下文
- <source>: <reason> | <excerpt>
...

### 规则栈
- 硬护栏：story_frame、current_state、book_rules、roles
- 软约束：author_intent、current_focus、volume_map
- 诊断规则：anti_ai_checks、continuity_audit、style_regression_checks

### 当前覆盖
- L4 -> L3: 本章避免战斗场景 (chapter:42/mustAvoid)
- L4 -> L3: POV 锁定主角内心独白 (chapter:42/styleEmphasis)
```

各阶段消费方式：

- **Writer（05）**：硬护栏 `sections.hard` 必须在正文中得到执行，违反即触发 post-write critical；`activeOverrides` 是本章必须遵守的额外硬约束（mustAvoid 不能出现，styleEmphasis 必须体现）；mustKeep 通过 ContextPackage 注入，作为"必须落地的事实点"。
- **Reviser（10）**：在 audit 失败回环时同样接收 RuleStack，确保 revise 不会引入新的 L1/L2 违规；mustAvoid 列表在 reviser prompt 中作为"修改时不能恢复"的禁令。
- **Auditor（09）**：把 RuleStack 渲染进 user prompt 的 "Chapter Control Inputs" 段（中文版"本章控制输入"），让审计同时检查 activeOverrides 是否在成稿中得到落实；硬护栏 / 软约束 / 诊断规则的违反对应 critical / warning / info 三级。

## 注意事项

- `RuleStack` 是每章重新构建的，不持久化；持久化的是其依赖的真理文件 + `ChapterIntent` + `ChapterMemo`，可由 `buildGovernedRuleStack(plan, chapterNumber)` 任意时刻重放。
- `mustKeep` 不进 activeOverrides，原因是它表达"本章要做什么"而非"本章要禁止什么"——做什么由 ContextPackage 投喂，禁什么才需要覆盖契约。
- 覆盖一律是收紧（narrow），不允许放宽 L1 / L2 的硬约束；如需放宽（如 fanfic OOC 模式放宽 dim 1），改的是 audit-dimensions 的 severity，不是 RuleStack。
