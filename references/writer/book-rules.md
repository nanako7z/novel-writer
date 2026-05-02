# Writer Sub-Reference — 全书 / 主角 / 角色矩阵规则（Book Rules & Cast Tracking）

## 功能说明

本文件汇总 Writer 系统 prompt 中**所有"单本书层规则"的注入段**，覆盖三块：

1. **§8.B 主角铁律**——从 `book_rules.md` 取出主角设定锁、行为约束、本书禁忌、风格禁区；`book_rules.protagonist != null` 时启用；
2. **§9 book_rules + style_guide**——9.A 注入 `book_rules.md` 用户手写的全书规则正文（与 8.B 的结构化字段互补）；9.B 注入 `story/style_guide.md`；
3. **§13 全员追踪（Full Cast Tracking）**——让 POST_SETTLEMENT 输出额外角色清单，方便 Observer 抽事实；`book_rules.enableFullCastTracking == true` 时启用。

这三段共同构成 SKILL 的 **L2 单本书规则层**——比 L1 题材规则（[genre-injection](./genre-injection.md)）优先级高，**冲突时以本组规则为准**（如题材允许的疲劳词在主角风格禁区里被禁，则禁用）。

启用条件按各小节分别判定：§8.B / §9.A 看 book_rules 内容是否为空、§9.B 看 style_guide 是否为占位、§13 看 enableFullCastTracking 开关。任一不满足就跳过对应整段。

---

### 8.B 主角铁律（条件含）

**作用**：从 `book_rules.md` 取出主角设定锁、行为约束、本书禁忌、风格禁区。

**何时启用**：`book_rules.protagonist != null`。

**模板**：

```
## 主角铁律（<protagonist.name>）

性格锁定：<personalityLock 列表，"、"拼接>

行为约束：
- <constraint_1>
- <constraint_2>
...

本书禁忌：
- <prohibition_1>
- <prohibition_2>
...

风格禁区：禁止出现<genreLock.forbidden 列表>
```

各小节为空则跳过；至少 `personalityLock` 或 `behavioralConstraints` 有一条才输出整段。

---

### 9. book_rules + style_guide

#### 9.A 本书专属规则

**作用**：注入 `book_rules.md` 用户手写的全书规则正文（与 8.B 的结构化字段互补）。

**何时启用**：`book_rules.body` 非空。

**模板**：

```
## 本书专属规则

<body markdown verbatim>
```

#### 9.B 文风指南

**作用**：注入 `story/style_guide.md`（用户手写或 style 分支生成）。

**何时启用**：`style_guide.md` 存在且非默认占位 `(文件尚未创建)`。

**模板**：

```
## 文风指南

<style_guide.md verbatim>
```

> v10 之后 `style_guide.md` 会内嵌 PRE_WRITE_CHECKLIST 的部分条目，不再单独注入 PreWriteChecklist 段。

---

### 13. （条件）全员追踪（Full Cast Tracking）

**作用**：让 POST_SETTLEMENT 输出额外角色清单，方便 Observer 抽事实。

**何时启用**：`book_rules.enableFullCastTracking == true`。

**Verbatim**：

```
## 全员追踪

本书启用全员追踪模式。每章结束时，POST_SETTLEMENT 必须额外包含：
- 本章出场角色清单（名字 + 一句话状态变化）
- 角色间关系变动（如有）
- 未出场但被提及的角色（名字 + 提及原因）
```

---

## 与上层 Writer 阶段的关系

在 Writer system prompt 拼装顺序中：

- **§8.B 紧跟 §8.A 题材规则之后**——L1 题材规则定完通用底色，立刻在同一个 §8 块内切换到 L2 主角铁律，两段共享 "## 主角铁律" / "## 题材规范" 的小节标题级，结构清晰；
- **§9 紧跟 §8 之后**——9.A 是 8.B 结构化字段的散文版互补，9.B 是文风层规则，与 8.B 的"风格禁区"字段呼应；
- **§13 在 §12 去 AI 味之后、§14 输出格式契约之前**——全员追踪段会改写 §14 POST_SETTLEMENT 的输出形态（追加出场角色清单），所以必须在 §14 之前注入。

**冲突优先级**：
- L2（本组规则）> L1（[genre-injection](./genre-injection.md) 题材规则）：主角风格禁区禁用的词，即使是题材允许的，也禁用；
- 与 fanfic canon：fanfic 模式下 canon 描述的世界规则 > 题材 body 通用规则 > book_rules（fanfic 模式下 canon 是事实）；
- 与 §10 fanfic：本组规则中"主角铁律"的人物锁定段，与 fanfic 的 character_voice_profiles 同时启用时，character_voice_profiles 提供"原作角色怎么说话"的样本，主角铁律提供"本书主角的设定锁"，两者互不覆盖。

下游消费：
- Auditor dim 18 / 19 用 §8.B 检查主角是否破设定；
- Auditor dim 14 用 §9.A 检查全书禁忌；
- Observer 在 §13 启用时多抽一份"出场角色清单"事实，喂给 Settler 更新 character_matrix。

回主文件参见 [phases/05-writer.md](../phases/05-writer.md)。
