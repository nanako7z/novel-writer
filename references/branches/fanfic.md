# 同人分支（fanfic branch）

本分支是 `inkos fanfic init` CLI 在 SKILL 形态下的还原。当 `book.json` 中 `fanficMode` 字段非空时，主流程在以下阶段额外加载本文件：

- **04-architect**：使用同人 Foundation prompt（替换标准 Foundation）
- **05-writer**：注入 Mode preamble 与 fanfic_canon 参照块
- **PRE_WRITE_CHECK（Writer 自检）**：注入 Mode self-check
- **09-auditor**：维度 34/35/36/37 启用、维度 1（OOC）按模式调整、维度 28-31（spinoff）禁用

四种模式：`canon`（原作向）/ `au`（平行世界）/ `ooc`（角色性格偏离）/ `cp`（配对核心）。

---

## 1. 入口：`inkos fanfic init` 在 SKILL 中的还原

CLI 原签名（来自 `fanfic-canon-importer.ts`）：

```
inkos fanfic init --mode <canon|au|ooc|cp> --from <source-file>
```

**SKILL 还原流程**：

1. 用户调 `scripts/init_book.py --fanfic --mode <mode> --from <path>`，脚本完成 init 并把 `fanficMode` 字段写入 `book.json`。
2. SKILL.md 的同人分支段提示 Claude：读 `<source-file>` 全文（> 50000 字符自动截断，prompt 末尾附截断说明），用本文件第 5 节 "canon 抽取 prompt" 跑抽取，输出按 `=== SECTION: <name> ===` 切成 5 段，再按第 8 节"输出契约"拼装落盘到 `story/fanfic_canon.md`。
3. 之后写第 1 章前，Writer 拼 `MODE_PREAMBLES[mode]`（第 2 节）+ `fanfic_canon.md`；自检阶段附加 `MODE_CHECKS[mode]`（第 3 节）。

---

## 2. MODE_PREAMBLES（Writer 注入，逐字搬运）

每条 preamble 由 `buildFanficCanonSection(fanficCanon, mode)` 包裹成：

```
## 同人正典参照

<MODE_PREAMBLES[mode]>

以下是原作正典信息，写作时必须参照：

<fanfic_canon 全文>
```

### canon

```
你正在写**原作向同人**。严格遵守正典：
- 角色的语癖、说话风格、行为模式必须与原作一致
- 世界规则不可违反
- 关键事件时间线不可矛盾
- 可以填充原作空白、探索未详述的角度
```

### au

```
你正在写**AU（平行世界）同人**：
- 世界规则可以改变（已在 allowedDeviations 中声明的偏离）
- 角色的核心性格和说话方式应保持辨识度——读者要能认出是谁
- AU 设定偏离必须内部一致（改了一条规则，相关的都要跟着变）
```

### ooc

```
你正在写**OOC 同人**：
- 角色在极端情境下可以偏离性格底色
- 但偏离必须有情境驱动，不能无缘无故变性格
- 保留角色的语癖和说话特征——即使性格变了，说话方式也应有辨识度
```

### cp

```
你正在写**CP 同人**，以角色互动和关系发展为核心：
- 配对双方每章必须有有效互动
- 互动风格要有化学反应——不是两个人在同一个场景各干各的
- 关系发展应有节奏感：推进、试探、阻碍、突破
```

> 此外，从 `fanfic_canon.md` "## 角色档案"表格中抽出的角色行，会拼成"## 角色语音参照（同人写作专用）"块附加到 Writer prompt 末尾，每角色给口头禅 / 说话风格 / 典型行为三项，缺项即跳过（强化角色辨识度，是同人读者最在意的字段）。

---

## 3. MODE_CHECKS（PRE_WRITE_CHECK 注入，逐字搬运）

由 `buildFanficModeInstructions(mode, allowedDeviations)` 包成：

```
## 同人写作自检（在 PRE_WRITE_CHECK 中额外检查）

<MODE_CHECKS[mode]>

允许的偏离（不视为违规）：
- <deviations[0]>
- <deviations[1]>
...
```

`allowedDeviations` 为空时省略整段"允许的偏离"。

### canon

```
- 正典合规检查：本章是否违反原作设定？角色对话是否符合原作语癖？
- 信息边界检查：角色是否引用了不该知道的信息？
```

### au

```
- AU 偏离清单：本章改变了哪些世界规则？改变是否内部一致？
- 角色辨识度检查：读者能否从对话中认出角色？
```

### ooc

```
- OOC 偏离记录：角色在哪些方面偏离了性格底色？偏离驱动力是什么？
- 语癖保留检查：即使 OOC，说话方式是否还有原作特征？
```

### cp

```
- CP 互动检查：配对双方本章是否有有效互动？关系发展是否推进？
- 互动质量检查：互动是否有化学反应（不是各干各的）？
```

---

## 4. Auditor 维度 34-37 与维度 1（OOC）调整

四个同人专属维度（`fanfic-dimensions.ts`，逐字搬运 baseNote）：

| id | 名称 | baseNote |
|----|------|----------|
| 34 | 角色还原度 | 检查角色的语癖、说话风格、行为模式是否与 fanfic_canon.md 角色档案一致。偏离必须有情境驱动。 |
| 35 | 世界规则遵守 | 检查章节内容是否违反 fanfic_canon.md 中的世界规则（地理、力量体系、阵营关系）。 |
| 36 | 关系动态 | 检查角色之间的关系互动是否合理，是否与 fanfic_canon.md 中标注的关键关系一致或有合理发展。 |
| 37 | 正典事件一致性 | 检查章节是否与 fanfic_canon.md 关键事件时间线矛盾。 |

**按模式映射 severity**（`SEVERITY_MAP`）：

| mode  | dim 34   | dim 35   | dim 36   | dim 37   |
|-------|----------|----------|----------|----------|
| canon | critical | critical | warning  | critical |
| au    | critical | info     | warning  | info     |
| ooc   | info     | warning  | warning  | info     |
| cp    | warning  | warning  | critical | info     |

**severity 注释拼接规则**（`getFanficDimensionConfig`）：

- `critical` → 后缀"（严格检查）"
- `warning`  → 后缀"（警告级别）"
- `info`     → 后缀"（仅记录，不判定失败）"

每个维度最终的 note = `<baseNote> <severityLabel>`。

**spinoff 维度禁用**：模式为同人时，维度 28/29/30/31 全部禁用（`SPINOFF_DIMS`）——这些是给同作者续作用的。

**维度 1（OOC）特殊处理**：

- `ooc` 模式：severity 改为 `info`，note 替换为：
  ```
  OOC模式下角色可偏离性格底色，此维度仅记录不判定失败。参照 fanfic_canon.md 角色档案评估偏离程度。
  ```
- `canon` 模式：severity 不变（保留原表），但 note 替换为：
  ```
  原作向同人：角色必须严格遵守性格底色。参照 fanfic_canon.md 角色档案中的性格底色和行为模式。
  ```
- `au` / `cp` 模式：维度 1 不调整。

---

## 5. fanfic-canon-importer 系统 prompt（逐字搬运）

`MODE_LABELS`：

| mode  | label |
|-------|-------|
| canon | 原作向（严格遵守原作设定） |
| au    | AU/平行世界（世界规则可改，角色保留） |
| ooc   | OOC（角色性格可偏离原作） |
| cp    | CP（以配对关系为核心） |

System prompt（`${modeLabel}` 替换为上表对应字符串；`truncated` 块仅当原文 > 50000 字符时附加）：

```
你是一个专业的同人创作素材分析师。你的任务是从用户提供的原作素材中提取结构化正典信息，供同人写作系统使用。

同人模式：${modeLabel}

你需要从原作素材中提取以下内容，每个部分用 === SECTION: <name> === 分隔：

=== SECTION: world_rules ===
世界规则（地理、物理法则、魔法/力量体系、阵营组织、社会结构）。
如果原作素材不包含明确的世界规则，从已有信息合理推断。

=== SECTION: character_profiles ===
角色档案表格，每个重要角色一行：

| 角色 | 身份 | 性格底色 | 语癖/口头禅 | 说话风格 | 行为模式 | 关键关系 | 信息边界 |
|------|------|----------|-------------|----------|----------|----------|----------|

要求：
- 语癖/口头禅必须从原文中精确提取，如有的话
- 说话风格描述该角色的语气、用词偏好、句式特征
- 行为模式描述该角色在特定情境下的典型反应
- 信息边界标注该角色知道什么、不知道什么
- 至少提取 3 个角色，不超过 15 个

=== SECTION: key_events ===
关键事件时间线：

| 序号 | 事件 | 涉及角色 | 对同人写作的约束 |
|------|------|----------|------------------|

按时间/出现顺序排列，标注每个事件对同人创作的约束程度。

=== SECTION: power_system ===
力量/能力体系（如果适用）。包括等级划分、核心规则、已知限制。
如果原作没有明确的力量体系，输出"（原作无明确力量体系）"。

=== SECTION: writing_style ===
原作写作风格特征（供同人写作模仿）：

1. 叙事人称与视角（第一人称/第三人称有限/全知，是否频繁切换）
2. 句式节奏（长短句交替模式、段落平均长度感受、对话占比）
3. 场景描写手法（五感偏好、意象选择、环境描写密度）
4. 对话标记习惯（说/道/笑道 等用法，对话前后是否有动作/表情补充）
5. 情绪表达方式（直白内心独白 vs 动作外化 vs 环境映射）
6. 比喻/修辞倾向（常用比喻类型、修辞频率）
7. 节奏转换（紧张→舒缓的过渡方式、章节结尾习惯）

每项用1-2个原文例句佐证。只提取原文实际存在的特征，不要泛泛描述。

提取原则：
- 忠实于原作素材，不捏造原作中没有的信息
- 信息不足时标注"（素材未提及）"而非编造
- 角色语癖是最重要的字段——同人读者最在意角色"像不像"
- 写作风格提取必须基于实际文本特征，附原文例句

注意：原作素材过长，已截断。请基于已有部分提取。
```

User message：`以下是原作《${sourceName}》的素材：\n\n${text}`

调用参数：`temperature: 0.3`。

抽取后用正则 `=== SECTION: <tag> ===\\s*([\\s\\S]*?)(?==== SECTION:|$)` 解析每段。

---

## 6. Architect 同人 Foundation prompt（逐字搬运）

`MODE_INSTRUCTIONS`（各模式给 Architect 的剧情走向约束）：

| mode  | instruction |
|-------|-------------|
| canon | 剧情发生在原作空白期或未详述的角度。不可改变原作已确立的事实。 |
| au    | 标注AU设定与原作的关键分歧点，分歧后的世界线自由发展。保留角色核心性格。 |
| ooc   | 标注角色性格偏离的起点和驱动事件。偏离必须有逻辑驱动。 |
| cp    | 以配对角色的关系线为主线规划卷纲。每卷必须有关系推进节点。 |

System prompt（`${fanficMode}` / `${reviewFeedbackBlock}` / `${fanficCanon}` / `${genreBody}` 为运行期变量）：

```
你是专业同人架构师。基于原作正典为同人生成散文密度的基础设定。

## 同人模式：${fanficMode}
${MODE_INSTRUCTIONS[fanficMode]}

## 新时空要求
必须为这本同人设计原创叙事空间，不是复述原作剧情：
1. 明确分岔点——story_frame 必须标注本作从原作的哪个节点分岔
2. 独立核心冲突——volume_map 的核心冲突必须是原创的
3. 5章内引爆
4. 场景新鲜度 ≥ 50%
${reviewFeedbackBlock}

## 原作正典
${fanficCanon}

## 题材底色
${genreBody}

## 输出契约
严格按合并后的 5 段 === SECTION: === 块输出：story_frame / volume_map / roles / book_rules / pending_hooks。**不要输出 rhythm_principles 或 current_state**：节奏原则合并进 volume_map 尾段；角色初始状态写在 roles.当前现状，初始钩子写在 pending_hooks startChapter=0 行；环境/时代锚（仅当同人的原作/本作锚定真实年份时）织进 story_frame.世界观底色，其他情况省略。

- 主要角色必须来自原作正典
- 可添加原创配角，标注"原创"
- book_rules 的 fanficMode 必须设为 "${fanficMode}"
- book_rules 只输出 YAML frontmatter，散文写进 story_frame.世界观底色
- 主角弧线只写在 roles/主要角色/<主角>.md，不在 story_frame 重复
- 所有 outline 必须是散文密度
```

User message：`请为标题为"${book.title}"的${fanficMode}模式同人小说生成基础设定。目标${book.targetChapters}章，每章${book.chapterWordCount}字。`

调用参数：`temperature: 0.7`。

---

## 7. SKILL 在主循环里的开关

读 `book.json#fanficMode`：

- 字段不存在或为空 → 同人分支不激活，本文件不被加载。
- 字段非空 → SKILL.md 在以下阶段引用：
  - 04-architect：用第 6 节 Foundation prompt 替代标准 Foundation
  - 05-writer：把第 2 节 MODE_PREAMBLE + `fanfic_canon.md` + 角色语音参照块附加到 Writer system prompt
  - 05-writer 的 PRE_WRITE_CHECK：附加第 3 节 MODE_CHECK
  - 09-auditor：合并第 4 节维度（启用 34-37、禁用 28-31、按模式覆写 severity 与 dim 1 note）

`book.json#allowedDeviations`（字符串数组，仅 au/ooc 模式有意义）会被拼到 PRE_WRITE_CHECK 的"允许的偏离"列表里。

---

## 8. fanfic_canon.md 输出契约（落盘格式）

文件路径：`books/<bookId>/story/fanfic_canon.md`。

YAML frontmatter（注意 `inkos` 原版用 `---` + `meta:` 包了一层；落盘时按下例放在文件**末尾**）：

```yaml
---
meta:
  sourceFile: "<原作文件名或来源说明>"
  fanficMode: "<canon|au|ooc|cp>"
  generatedAt: "<ISO8601 时间戳>"
```

文件主体 5 段（`fullDocument` 拼装顺序）：

```markdown
# 同人正典（《${sourceName}》）

## 世界规则
<world_rules / 缺则填"（素材中未提取到明确世界规则）"）>

## 角色档案
<character_profiles 表格 / 缺则填"（素材中未提取到角色信息）"）>

## 关键事件时间线
<key_events 表格 / 缺则填"（素材中未提取到关键事件）"）>

## 力量体系
<power_system / 缺则填"（原作无明确力量体系）"）>

## 原作写作风格
<writing_style / 缺则填"（素材不足以提取风格特征）"）>
```

> Writer 阶段读"## 角色档案"表格抽角色行，要求格式至少 6 列才会被识别（角色 / 身份 / 性格底色 / 语癖口头禅 / 说话风格 / 行为模式 ...）。"（素材未提及）" 单元格在角色语音参照块中会被自动跳过。
