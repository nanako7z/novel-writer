# Writer Sub-Reference — 题材注入（Genre Injection）

## 功能说明

本文件汇总 Writer 系统 prompt 中**所有"题材风味"相关的注入段**，包括三块：

1. **§1 题材引言** —— Writer 身份与平台声明，把叙事声音先拉到目标题材；恒启。
2. **§8.A 题材规则** —— 注入 `genre_profiles/<genre>.md` 的疲劳词、节奏规则、章节类型清单、题材专属 body；恒启。
3. **§11.5 题材 profile 注入** —— 把当前书的 `templates/genres/<book.genre>.md` 完整解析后，按字段定向注入 Writer 多个段，并打通 Planner / Auditor 的题材联动。

这三段一起构成 Writer 的"题材底色"。**缺了 §11.5 整个 SKILL 退化成无题材通用作家**——即使 §1 和 §8.A 仍在原位，没有 §11.5 把 frontmatter 字段扇出到 PRE_WRITE_CHECK / POST_SETTLEMENT，Writer 不知道本章爽点该归到哪个 satisfactionType、不知道 numericalSystem 该不该追加 LEDGER 区块。

启用条件：三段全部恒启（Writer 进入即必读）。题材文件解析失败时回退到 `templates/genres/other.md`。

---

### 1. 题材引言（必含）

**作用**：定身份与平台，把叙事声音拉到目标题材。

**何时启用**：恒启。

**Verbatim**：

```
你是一位专业的<genre.name>网络小说作家。你为<book.platform>平台写作。
```

> 占位符 `<genre.name>` 取自 `genre_profiles/<genre>.md` 的标题（如 "玄幻"、"都市"、"末世"）；`<book.platform>` 取自 `book.json#platform`。

---

### 8.A 题材规则（必含）

**作用**：注入 `genre_profiles/<genre>.md` 的题材专有约束（疲劳词、节奏规则、章节类型清单、题材专属 body）。

**何时启用**：恒启。

**模板**：

```
## 题材规范（<genre.name>）

- 高疲劳词（<gp.fatigueWords...>）单章最多出现1次
- 节奏规则：<gp.pacingRule>

动笔前先判断本章类型：
- <chapterType_1>
- <chapterType_2>
...

<genre body markdown — 来自 genre_profiles/<genre>.md 正文>
```

> `fatigueWords` / `pacingRule` / `chapterTypes` 任何一项为空就跳过对应行；`genre body` 直接灌全文。

---

### 11.5 题材规则注入（Genre Profile）

**作用**：把当前书的 `templates/genres/<book.genre>.md` 完整解析后，按字段定向注入 Writer 的多个段（§1 题材引言、§8.A 题材规范、§14 输出格式契约），并打通 Planner / Auditor 的题材联动。这一节是"题材风味"的总开关；缺了它整个 SKILL 退化成无题材通用作家。

**何时启用**：恒启。Writer 进入即必读。

**做的事（按顺序）**：

1. **入口加载**：`book = read books/<id>/book.json` → 拿到 `book.genre`。
2. **解析 genre profile**：读 `{SKILL_ROOT}/templates/genres/<book.genre>.md`；解析失败或文件缺失 → 回退 `templates/genres/other.md`。frontmatter + body 都进 context。详见 `references/genre-profile.md`。
   - 若用户在书目录下放了 `books/<id>/genres/<id>.md` 项目级 override，**优先项目级**。
3. **§1 题材引言**：用 `gp.name` 填 "你是一位专业的<gp.name>网络小说作家"。
4. **§8.A 题材规范段**：按 `buildGenreRules` 拼装：
   - `gp.fatigueWords` → 注入 `## 高疲劳词（X、Y、Z）单章最多出现1次` 行。**与 `references/ai-tells.md` 通用 AI 标记词列表叠加**（不是替代）：通用词表照样查 + 题材词单独限频。
   - `gp.pacingRule` → 注入 `## 节奏规则：…` 行。Writer 在写过渡 / 高潮章时主动对齐。
   - `gp.chapterTypes` → 注入 `动笔前先判断本章类型：` 列表，让 Writer 在 PRE_WRITE_CHECK 的"章节类型"列里挑且只挑一个。
   - `genre body markdown` → 整段 verbatim 灌入题材规范段末尾，不裁剪、不二次编辑。
5. **§14 输出格式联动**：
   - `gp.numericalSystem == true` → 输出格式追加 `=== UPDATED_LEDGER ===` 区块；PRE_WRITE_CHECK 多两行（"当前资源总量"、"本章预计增量"）；POST_SETTLEMENT 多两行（"资源账本"、"重要资源"）。`false` 则全部省略。
   - `gp.powerScaling == true` → PRE_WRITE_CHECK "风险扫描"行追加 `/战力崩坏` 项。
   - `gp.chapterTypes` → 喂 PRE_WRITE_CHECK 与 CHAPTER_SUMMARY 的"章节类型"列；空则退化成 `过渡/冲突/高潮/收束`。
6. **本章爽点规划**：用 `gp.satisfactionTypes` 在 PRE_WRITE_CHECK 自报"本章爽点属于 <type>"。Writer 必须从清单里挑一个落地——不是泛泛说"主角变强了"，而是说"本章爽点属于'打脸'，对应章末李某当众认怂"。Auditor dim 15 据此判爽点虚化。
7. **honor pacingRule**：每章节奏与 `gp.pacingRule` 对齐（如"每3-5章一次小突破"），Writer 在心里数当前距上一次突破多少章；若 chapter_memo 没显式给突破指令，Writer 不擅自塞——交回 Planner / Auditor 仲裁。
8. **eraResearch 提示**：`gp.eraResearch == true` 时，Writer 在写到具体年代 / 地理 / 政策 / 历史人物时**自检合理性**（不主动联网；联网由 Auditor dim 12 在审稿阶段做）。Writer 输出可信范围内，留疑点给 Auditor 核。

**与其他段的协作**：

- 与 §12 去 AI 味的疲劳词：`gp.fatigueWords`（题材专属）与通用 AI 标记词（`references/ai-tells.md`）**两套独立计数**，都不能违反，单章 1 次上限分别计。
- 与 §8.B 主角铁律：`book_rules.md` 的"风格禁区 / 全书禁忌"是 L2，**优先级高于** L1 题材；冲突时按主角铁律。
- 与 §10 fanfic：fanfic canon 与题材 body 同时出现时，canon 描述的世界规则 > 题材 body 通用规则（fanfic 模式下 canon 是事实，题材 body 是建议）。
- 与 §7 黄金开场：黄金开场的硬约束（前 800 字进冲突等）**凌驾于** `gp.pacingRule`；前 3 章不要被节奏规则拖慢开场。

> 详见 `references/genre-profile.md`。Schema 改动 / 新增题材 / 项目级 override 全部归那一份文档。本节只描述 Writer 怎么用 frontmatter 字段。

---

## 与上层 Writer 阶段的关系

在 Writer system prompt 拼装顺序中，本文件覆盖三段：

- **§1（首段）**：所有 prompt 的开头都从这里起步——身份声明先于一切其他规则；
- **§8.A**：紧跟在 §7 黄金开场之后、§8.B 主角铁律之前，承上启下：题材规则是 L1 通用底色，主角铁律是 L2 单本书覆盖，主角铁律可以凌驾题材规则；
- **§11.5**：逻辑上贯穿全 prompt，**实际只在 §11 之后作为一段"打通规则"出现**——它的副作用（输出格式追加 LEDGER、PRE_WRITE_CHECK 加列、风险扫描加项）已经在前面的段里落地，本段只负责"声明这些副作用的来源是 genre profile"。

回主文件参见 [phases/05-writer.md](../phases/05-writer.md)。
