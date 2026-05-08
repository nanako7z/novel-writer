# Audit Dimensions（37 维度全表）

> Auditor 在 prompt 中按 mode 动态拼"活跃维度清单"，每条形如 `<id>. <中文名>（<note>）`。本表是真理来源（移植自 inkos `continuity.ts` + `fanfic-dimensions.ts`）。

> **题材过滤（必读）**：下表「适用条件」列是该维度在 inkos 全流水线的**最大启用范围**。实际进 Auditor system prompt 的活跃维度还要 **与 `genreProfile.auditDimensions` 取交集**——id 不在数组里的不会被询问。同时 `genreProfile.numericalSystem` / `powerScaling` / `eraResearch` 三个 toggle 即便不在 `auditDimensions` 里，也会**强制激活**对应的 dim 5 / 4 / 12（参 [genre-profile.md](genre-profile.md) 与 [phases/09-auditor.md §2.5](phases/09-auditor.md)）。本表整体保持完整、不按题材裁剪。

## 维度全表

| ID | 中文名 | EN name | 适用条件 | 读取的真理文件 | 检查要点 |
|----|--------|---------|----------|----------------|----------|
| 1 | OOC检查 | OOC Check | universal（fanfic canon 收紧、fanfic ooc 放宽） | character_matrix.md, story/roles/<slug>.md#voiceProfile, fanfic_canon.md（同人模式） | 角色行为/对白是否符合人设；**对每个开口角色对照其 voiceProfile 7 项识别记号**——雷区台词清单触线 = critical（不分 fanfic 模式）；用错口头禅 / 句长偏好相反 = warning。同人 canon 模式额外按 fanfic_canon 角色档案严格判；ooc 模式只对 voiceProfile 雷区台词部分判 critical，其余 ooc-acceptable |
| 2 | 时间线检查 | Timeline Check | universal | current_state.json, chapter_summaries.md, 上一章全文 | 时间推进是否前后一致，无穿越/倒叙错位 |
| 3 | 设定冲突 | Lore Conflict Check | universal | current_state.json, story_frame, parent_canon/fanfic_canon | 是否违反已确立的世界观/设定 |
| 4 | 战力崩坏 | Power Scaling Check | universal（按 GenreProfile 启用） | current_state.json（power level）, particle_ledger.md | 角色实力升降合规、与设定的力量体系一致 |
| 5 | 数值检查 | Numerical Consistency Check | `gp.numericalSystem=true` | particle_ledger.md | 资源/数值账本一致；不出现凭空增减 |
| 6 | 伏笔检查 | Hook Check | universal（Phase 7 hook-debt 升级规则） | pending_hooks.md（含 stale/blocked/core_hook/depends_on/promoted 列） | promoted=true 的伏笔过期/受阻分级升级；core_hook 过期 >10 章升 critical；卷尾仍 open 升 critical；非 promoted 一律 info |
| 7 | 节奏检查 | Pacing Check | universal | chapter_summaries.md, emotional_arcs.md | 最近 3-5 章是否形成「蓄压→升级→爆发→后效」周期；连续 5 章无爆发标记停滞；高潮后影响缺失需标 |
| 8 | 文风检查 | Style Check | universal | style_guide.md, style_profile（如启用） | 文风/语气与全书一致；按"info-only"原则，不计入 critical |
| 9 | 信息越界 | Information Boundary Check | universal | current_state.json, chapter_summaries.md | 角色不能知道当前章节边界外的信息 |
| 10 | 词汇疲劳 | Lexical Fatigue Check | universal（按 fatigueWords 启用） | style_guide.md, GenreProfile.fatigueWords / book_rules.fatigueWordsOverride | 高疲劳词命中；AI 标记词（仿佛/不禁/宛如/竟然/忽然/猛地）每 3000 字 >1 次即 warning |
| 11 | 利益链断裂 | Incentive Chain Check | universal（按 GenreProfile 启用） | current_state.json, subplot_board.md | 角色行为动机链是否连贯，无突兀利他/无利可图行动 |
| 12 | 年代考据 | Era Accuracy Check | `gp.eraResearch=true` 或 `bookRules.eraConstraints.enabled=true` | book_rules.eraConstraints (period/region), 联网 search_web | 真实年代/地理/人物/政策需联网核实，≥2 来源交叉 |
| 13 | 配角降智 | Side Character Competence Check | universal | character_matrix.md | 配角不应为衬主而临时降智 |
| 14 | 配角工具人化 | Side Character Instrumentalization Check | universal | character_matrix.md, subplot_board.md | 配角不应只为推进主角剧情存在 |
| 15 | 爽点虚化 | Payoff Dilution Check | universal（按 satisfactionTypes） | GenreProfile.satisfactionTypes, emotional_arcs.md | 是否制造情绪缺口或兑现超预期；满足 70% 期待即视为虚化；后效需展示具体改变（地位/关系/资源） |
| 16 | 台词失真 | Dialogue Authenticity Check | universal | character_matrix.md, style_guide.md | 角色台词与人设/语癖一致 |
| 17 | 流水账 | Chronicle Drift Check | universal | chapter_summaries.md | 日常/过渡章是否承担了埋伏笔/推关系/反差/蓄压任务 |
| 18 | 知识库污染 | Knowledge Base Pollution Check | universal | current_state.json | 是否引入未确立的"伪事实" |
| 19 | 视角一致性 | POV Consistency Check | universal | book_rules.pov, style_guide.md | POV 切换是否有过渡、是否与设定视角一致 |
| 20 | 段落等长 | Paragraph Uniformity Check | universal | （直接看正文） | 段落长短分布；过度等长是 AI 痕迹 |
| 21 | 套话密度 | Cliche Density Check | universal | style_guide.md | 网文套话密度过高扣分 |
| 22 | 公式化转折 | Formulaic Twist Check | universal | chapter_summaries.md | "突然/没想到"型转折公式化 |
| 23 | 列表式结构 | List-like Structure Check | universal | （直接看正文） | 句首/段首列表式排比，AI 痕迹 |
| 24 | 支线停滞 | Subplot Stagnation Check | universal | subplot_board.md, chapter_summaries.md | 支线沉寂到接近被遗忘，或近期连续只重复提及未真实推进 |
| 25 | 弧线平坦 | Arc Flatline Check | universal | emotional_arcs.md, chapter_summaries.md | 主要角色情绪线是否在同一压力形态停滞；区分"处境未变"和"内心未变"；含人设三问检查 |
| 26 | 节奏单调 | Pacing Monotony Check | universal | chapter_summaries.md（章节类型分布） | 近期章节类型序列是否过度同型；回收/释放/高潮缺席过久给 warning |
| 27 | 敏感词检查 | Sensitive Content Check | universal | sensitive-words 词表（block/warn 政治/性/极端暴力） | 三级词表扫描；block 级命中强制 fail |
| 28 | 正传事件冲突 | Mainline Canon Event Conflict | spinoff（parent_canon.md 存在 + 非 fanfic 模式） | parent_canon.md | 番外事件不得与正典约束表矛盾 |
| 29 | 未来信息泄露 | Future Knowledge Leak Check | spinoff | parent_canon.md（信息边界表） | 角色不得引用分歧点之后才揭示的信息 |
| 30 | 世界规则跨书一致性 | Cross-Book World Rule Check | spinoff | parent_canon.md | 番外不得违反正传力量体系/地理/阵营 |
| 31 | 番外伏笔隔离 | Spinoff Hook Isolation Check | spinoff | parent_canon.md, pending_hooks.md | 番外不得越权回收正传伏笔（warning 级别） |
| 32 | 读者期待管理 | Reader Expectation Check | universal always-active | pending_hooks.md, emotional_arcs.md, chapter_summaries.md | 章尾是否点燃新好奇心；承诺回收按伏笔节奏落地；压力是否得到释放；高潮后效是否展示具体改变 |
| 33 | 章节备忘偏离 | Chapter Memo Drift Check | universal always-active | ChapterMemo（goal + 7 段 body） | 成稿是否兑现 memo goal、7 段正文是否留下落地痕迹；缺失/写反 → critical；稀疏 memo 不判 incomplete |
| 34 | 角色还原度 | Character Fidelity Check | fanfic only（mode 决定 severity） | fanfic_canon.md（角色档案） | 语癖/说话风格/行为模式是否与档案一致；偏离需有情境驱动 |
| 35 | 世界规则遵守 | World Rule Compliance Check | fanfic only | fanfic_canon.md（地理/力量/阵营） | 章节是否违反原作世界规则 |
| 36 | 关系动态 | Relationship Dynamics Check | fanfic only | fanfic_canon.md（关键关系） | 角色关系是否合理或有可解释的发展 |
| 37 | 正典事件一致性 | Canon Event Consistency Check | fanfic only | fanfic_canon.md（关键事件时间线） | 章节是否与原作时间线矛盾 |

## Dimension activation matrix

不同模式启用的维度集合（从 `buildDimensionList` 推导）：

| Mode | 1-27（按 GenreProfile） | 12（年代考据） | 28-31（spinoff） | 32-33（always） | 34-37（fanfic） |
|------|-------------------------|----------------|------------------|------------------|------------------|
| standalone | ✓（取 `gp.auditDimensions ∪ bookRules.additionalAuditDimensions`） | 仅当 `gp.eraResearch` 或 `eraConstraints.enabled` | ✗ | ✓ | ✗ |
| spinoff（`parent_canon.md` 存在，非 fanfic） | ✓ | 同上 | ✓ | ✓ | ✗ |
| fanfic / canon | ✓（dim 1 收紧为"严格遵守底色"） | 同上 | ✗（强制关闭） | ✓ | ✓（severity 见下表） |
| fanfic / au | ✓ | 同上 | ✗ | ✓ | ✓ |
| fanfic / ooc | ✓（dim 1 放宽为 info-only） | 同上 | ✗ | ✓ | ✓ |
| fanfic / cp | ✓ | 同上 | ✗ | ✓ | ✓ |

注：

- "✓（按 GenreProfile）"指实际维度集是 `new Set(gp.auditDimensions)` ∪ `bookRules.additionalAuditDimensions`（支持数字 ID 或中/英文名字符串模糊匹配）。
- 32 / 33 不依赖配置，永远 add：`activeIds.add(32); activeIds.add(33);`。
- fanfic 模式下，spinoff 维度集 `[28, 29, 30, 31]` 会被 `deactivatedIds` 显式从 activeIds 中移除（即使 `parent_canon.md` 也存在）。

## Fanfic-mode severity table

逐字端口自 `fanfic-dimensions.ts` 的 `SEVERITY_MAP`（L39-44）：

| Mode | dim 34 角色还原度 | dim 35 世界规则遵守 | dim 36 关系动态 | dim 37 正典事件一致性 |
|------|--------------------|----------------------|-----------------|------------------------|
| canon | critical | critical | warning | critical |
| au    | critical | info     | warning | info     |
| ooc   | info     | warning  | warning | info     |
| cp    | warning  | warning  | critical | info    |

> 严重度标签（注入到 prompt 的 note 末尾）：
> - critical → "（严格检查）"
> - warning → "（警告级别）"
> - info → "（仅记录，不判定失败）"

## OOC mode 对 dim 1 的特殊放宽

`fanfic-dimensions.ts` L70-74 显式把 ooc 模式下 dim 1（OOC 检查）的 severity 重映射为 `info`，并替换其 note 文本。逐字搬运：

> "OOC模式下角色可偏离性格底色，此维度仅记录不判定失败。参照 fanfic_canon.md 角色档案评估偏离程度。"

对应英文版（`continuity.ts` L141-142）：

> "In OOC mode, personality drift can be intentional; record only, do not fail. Evaluate against the character dossiers in fanfic_canon.md."

Canon 模式则反向收紧 dim 1（`fanfic-dimensions.ts` L77-79）：

> "原作向同人：角色必须严格遵守性格底色。参照 fanfic_canon.md 角色档案中的性格底色和行为模式。"

## 维度 note 注入 hook（buildDimensionNote）

部分维度在 prompt 渲染时会被替换或追加上下文，不是静态文本：

- **dim 1**：fanfic mode 重写（canon / ooc 各一份）；
- **dim 7**：注入"波形周期检查"详细说明（蓄压→升级→爆发→后效，含日常/过渡章功能要求）；
- **dim 10**：拼接 `fatigueWords`（如有 `book_rules.fatigueWordsOverride` 优先），并附 AI 标记词阈值；
- **dim 12**：拼接 `eraConstraints.period + region`；
- **dim 15**：拼接 `gp.satisfactionTypes` + 欲望驱动检查（情绪缺口 / 70% 兑现虚化 / 后效需具体改变）；
- **dim 19 / 24 / 25 / 26 / 28 / 29 / 30 / 31**：固定结构性 note（见上表"检查要点"列）；
- **dim 32 / 33**：always-on 详细 note（读者期待管理 / memo 偏离），见上表；
- **dim 34-37**：拼接 `FANFIC_DIMENSIONS[id].baseNote` + 模式 severity 标签。

## Verbatim dim notes（搬自 inkos `agents/continuity.ts#buildDimensionNote`）

下面 4 个维度的 note 文本必须 verbatim 注入 Auditor system prompt——不要重写、不要简化。它们是 Auditor 把 pending_hooks 状态列、chapter_memo 7 段、satisfactionTypes 落地到打分的可执行依据。

### dim 6 — Hook-debt 升级规则（含 hotfix 2/3）

```
Phase 7 hook-debt 升级规则（含 hotfix 2/3）。阅读 pending_hooks.md 伏笔池时不要只看"有没有悬而未决的伏笔"，要读状态列中的 stale / blocked 标记、core_hook 列、depends_on 列、以及升级列：

• critical 级别仅适用于升级=是（promoted=true）的伏笔。非升级的 stale/blocked 伏笔一律保持 info——升级标志是降噪的开关，因为架构师阶段会产出大量非承重的伏笔种子。
• 升级=是且 core_hook=是 的伏笔过期超过 10 章未回收 → warning 升级为 critical。全书只有 3-7 条核心伏笔，任何一条漂移这么久都是烂尾前兆（对应 new.txt L1569"严禁烂尾逻辑"）。
• 升级=是的受阻伏笔，状态列中"受阻于 X (已阻 Y 章)"且 Y ≥ 6 → warning。"已阻 Y 章"这个字面 token 直接读自账本，不要猜。描述中要写出具体的上游 hook_id，让 planner 能安排落地路径。
• 卷尾（volume_map 中任一卷的末章）仍有升级=是的主线伏笔处于 open 或 stale 且没有显式"延至下一卷"规划 → critical。
• 升级=否的 stale 伏笔 → info 级记录，不判本章失败，但保留以便 planner 安排清理。

description 中要明确引用 hook_id，并把状态列中 stale / blocked 的原文标记字面抄进去。本维度只审结构，不评价伏笔文笔。
```

英文路径见 inkos `continuity.ts` L196-220 enRules verbatim。

### dim 7 — 波形周期检查

```
检查节奏波形：最近 3-5 章是否形成了完整的「蓄压→升级→爆发→后效」周期？如果连续 5 章没有爆发（兑现/回报/翻转），标记为节奏停滞。如果上一章是爆发/高潮/大反转，本章是否写出了改变？如果直接跳到新蓄压而没有展示前一波爆发的影响，标记为「高潮后影响缺失」。非冲突章节中的日常/过渡/对话段落，是否至少承担了一项任务：埋伏笔、推关系、建立反差、准备下一轮蓄压。纯水日常标记为流水账风险。
```

### dim 15 — 欲望驱动 + 后效具体化

```
爽点类型：${gp.satisfactionTypes.join("、")}。检查欲望驱动：本章是否制造了情绪缺口（读者渴望释放）或完成了超出预期的兑现？只满足读者 70% 期待的兑现等于爽点虚化。如果本章处于小目标周期的后效阶段，检查是否展示了具体改变——不只是情绪反应，而是地位、关系或资源的实际变化。
```

> note 头部 `${gp.satisfactionTypes.join("、")}` 由题材 profile 注入；如题材无 satisfactionTypes 字段则该前缀整段省略（参 inkos `continuity.ts` L181-189）。

### dim 25 — 人设三问 + 情绪弧线

```
人设三问检查：(1)角色为什么这么做？(2)符合之前建立的人设吗？(3)只看过前面章节的读者会觉得突兀吗？同时检查角色情绪弧线是否在推进还是停滞。
```

### dim 32 — 读者期待管理

```
检查：章尾是否重新点燃好奇心，已经承诺的回收是否按伏笔自身节奏落地，压力是否得到释放，读者期待缺口是在持续累积还是在被满足。如果刚经历高潮，检查后效章节是否在开启新周期前展示了具体改变。
```

### dim 33 — 章节备忘偏离（7 段映射）

```
对照随章提供的 chapter_memo。成稿是否兑现了 memo 中的 goal，并在 7 段正文（**当前任务 / 该兑现·暂不掀 / 日常过渡功能 / 关键抉择三连问 / 章尾必须发生的改变 / 不要做** 等）中留下可见落地痕迹？任何段落缺失或被写反 → critical。提醒：稀疏 memo 合法（喘息章 memo 可以只有 goal + 骨架 body），只检查 memo 实际写出的段落，不能因为 memo 稀疏就判 incomplete。
```

> 7 段名（**当前任务 / 该兑现·暂不掀 / 日常过渡功能 / 关键抉择三连问 / 章尾必须发生的改变 / 不要做**）是 chapter_memo schema 的硬绑定标签——Auditor 必须按这 6 个标签逐段对照正文，不能用别的措辞。如果 memo 把某段省略了，本维度不审该段；如果 memo 写了但正文没落，立即 critical。

`bookRules.additionalAuditDimensions` 既支持数字 ID 也支持中/英文名（ exact → substring fuzzy 匹配），匹配后并入 activeIds。
