# Genre Profile（题材规则）

> 移植自 inkos `packages/core/src/models/genre-profile.ts`（schema）+ `agents/rules-reader.ts`（loader）+ `agents/writer-prompts.ts`（注入 Writer）+ `agents/continuity.ts`（注入 Auditor）。本文件说明 SKILL 怎么读 / 用 / 扩展题材规则。

题材规则（GenreProfile）是**单本书的"L1 规则层"**——四级规则栈最底层（见 `references/rule-stack.md`）。它由两部分组成：YAML frontmatter（结构化字段，被 prompt 拼装与 audit 维度过滤直接消费）+ markdown body（题材专属禁忌 / 修炼规则 / 节奏指导，整段灌进 Writer 的"题材规范"段）。

## 1. YAML frontmatter schema

```yaml
---
name: 仙侠                                  # 题材展示名（中文/英文均可）；Writer 会用作"你是一位专业的<name>网络小说作家"
id: xianxia                                 # 题材唯一 id（kebab-case）；必须等于文件名 stem，并被 book.json#genre 引用
language: zh                                # "zh" | "en"，默认 "zh"；决定 Writer system prompt 中英文版本
chapterTypes: ["战斗章", "悟道章", "布局章", "过渡章", "回收章"]
                                            # 章节类型登记表；Writer 在 PRE_WRITE_CHECK 必须挑一个并在正文承担
fatigueWords: ["冷笑", "蝼蚁", "倒吸凉气", "瞳孔骤缩", "天道", "因果", "仿佛", "不禁", "宛如", "竟然"]
                                            # 题材专属高疲劳词；单章最多 1 次；Auditor dim 10 以此判定
numericalSystem: true                       # 是否启用资源账本 / 数值结算；true → 强制激活 dim 5（数值检查）
powerScaling: true                          # 是否有等级 / 战力体系；true → 强制激活 dim 4（战力崩坏）
eraResearch: false                          # 是否需要年代 / 现实考据；true → 激活 dim 12 + Auditor 联网搜索许可
pacingRule: "修炼/悟道与战斗交替，每3-5章一次小突破或关键收获"
                                            # 节奏规则；Planner 在 chapter_memo 的"读者此刻在等什么"段考虑这条
satisfactionTypes: ["悟道突破", "斗法碾压", "法宝收获", "身份揭示", "天劫渡过", "因果了结"]
                                            # 题材爽点类型登记；Writer 用来规划本章爽点；Auditor dim 15 以此判爽点虚化
auditDimensions: [1,2,3,4,5,6,7,8,9,10,11,13,14,15,16,17,18,19,24,25,26]
                                            # 该题材活跃的 audit 维度子集（37 维选择性激活）
cadence:                                    # 可选：结构化节奏政策；不写时由 cadence_check 推断
                                            # 字段定义见 references/schemas/cadence-policy.md
  satisfactionWindow: 5
  satisfactionSequence: [{type: "...", weight: N}, ...]
  volumeBeatDistribution: {early: {...}, middle: {...}, late: {...}}
  fatigueGuards: [{pattern: "...", action: "..."}, ...]
---
```

字段语义参 `models/genre-profile.ts#GenreProfileSchema`。schema 用 zod 校验；缺字段 / 类型不符 → 拒绝加载，返回 `Genre profile not found... fallback "other.md" is missing`-类报错。

> **`cadence:` 子对象**是 `pacingRule` 散文的结构化版本——`cadence_check.py` 直接消费它给出 `recommendedNext` / `fatigueAlerts`。现已落地在 `xianxia / urban / sci-fi` 三个 builtin profile；其余题材保持只有 `pacingRule`，运行时 cadence_check 会按 `references/cadence-policy.md` 表 + `references/schemas/cadence-policy.md` §2 推断默认值。详见 schemas/cadence-policy.md。

## 2. Markdown body 段落

frontmatter 之后是题材专属正文，每个文件结构略有差异。常见 H2 段（按题材出现频率）：

- `## 题材禁忌` —— 该题材的硬红线（如"修为无铺垫跳跃式突破"、"用大道无形跳过具体修炼"）
- `## 修炼规则` / `## 力量体系` / `## 系统规则` —— 题材内功法 / 等级 / 资源衰减规则
- `## 节奏` / `## 叙事指导` —— 节奏与叙事美学补充
- `## 战斗描写` / `## 商战展开` / `## 氛围营造` —— 题材核心场景类型的工艺要点
- `## 角色塑造` —— 该题材的角色刻板印象与反刻板模板

body 没有强制结构；Writer 注入时整段 verbatim 灌入 §8.A "题材规范"段（见 `references/phases/05-writer.md`），不做二次裁剪。

## 3. Claude 怎么加载（运行时契约）

Writer / Auditor / Planner 在阶段入口都要做这件事：

```
1. 读 books/<bookId>/book.json 取 .genre
2. 解析 templates/genres/<genre>.md（YAML + body）
3. 若文件不存在 → 回退 templates/genres/other.md
4. 把 frontmatter 字段 + body 缓存到本阶段 context 包，按下文规则注入 prompt
```

> 项目级 override：用户可以在工作目录 `<workdir>/genres/<id>.md` 自定义同名文件（与 inkos 的 BUILTIN_GENRES_DIR / projectRoot 双层 lookup 一致），优先读项目级。**这一层由 [`scripts/genre.py`](../scripts/genre.py) 统一管理**——`genre.py list` 同时列出 bundled + user，`genre.py add <id> --from <base>` 直接落到 `<workdir>/genres/<id>.md`。Writer / Auditor / Planner 读题材时，约定调用方走 `resolve_genre(workdir, id)` 等价的查找顺序：先 `<workdir>/genres/<id>.md`，再 `templates/genres/<id>.md`，都缺则回退 `other.md`。

加载失败 / `other.md` 也缺失 → 直接停下来报错，不要继续，否则 Writer 会按裸 prompt 写出"无题材风味"的稿。

## 4. 怎么改各阶段（Phase × Genre 注入矩阵）

| 字段 | Writer (05) | Auditor (09) | Planner (02) | Composer (03) |
|---|---|---|---|---|
| `name` | "你是一位专业的<name>作家" 引言 | "你是一位严格的<name>结构审稿编辑" | — | — |
| `chapterTypes` | §14 PRE_WRITE_CHECK / CHAPTER_SUMMARY 的"章节类型"列；§8.A 列出供 Writer 挑选 | dim 26 节奏单调（章节类型分布）参照 | chapter_memo 给出"建议章节类型"提示 | rule_stack L1 注入 |
| `fatigueWords` | §8.A "高疲劳词单章最多 1 次"；与 `references/ai-tells.md` 通用词表**叠加**（不是替代） | dim 10 词汇疲劳判定基准 | — | rule_stack L1 注入 |
| `numericalSystem` | §14 增加 UPDATED_LEDGER 区块 / PRE_WRITE_CHECK 增"当前资源总量""本章预计增量"行 | dim 5 强制激活 | chapter_memo 强制声明本章资源增量 | — |
| `powerScaling` | §14 PRE_WRITE_CHECK"风险扫描"加"战力崩坏"项 | dim 4 强制激活 | chapter_memo "不要做"段强调战力跃迁 | — |
| `eraResearch` | §8.A body 里如有年代设定，Writer 自检；不主动联网 | dim 12 强制激活 + Auditor 系统 prompt 加联网搜索许可 | — | — |
| `pacingRule` | §8.A "节奏规则：…" 单行 | dim 7 节奏检查的预期模板 | chapter_memo "读者此刻在等什么" + 卷纲节奏对齐 | — |
| `satisfactionTypes` | Writer 在 PRE_WRITE_CHECK 自报"本章爽点属于 <type>" | dim 15 爽点虚化判定基准 | chapter_memo "当前任务"段挑选爽点类型 | — |
| `auditDimensions` | — | **过滤 37 维清单**：只跑列表内 ID（与全局 universal/spinoff/fanfic 触发条件取交集） | — | — |
| body markdown | §8.A 题材规范末尾整段注入 | 不直接注入，但 dim 1-3 / 26 检查时把 body 当作设定参照 | — | — |

详细对应代码：

- Writer 拼装：`writer-prompts.ts#buildGenreRules`（拼 fatigueWords / pacingRule / chapterTypes / body）
- Auditor 维度过滤：`continuity.ts#buildDimensionList`（先用 `gp.auditDimensions` 取交集再按 mode 过滤）
- 三大 toggle 联动：`buildDimensionNote` 内部 `if (gp.numericalSystem) activate(5)` / 同理 4 / 12

## 5. 支持的 15 个题材（builtin catalog）

| id | name | language | 一句话定位 |
|---|---|---|---|
| `xianxia` | 仙侠 | zh | 修真悟道、法宝天劫，因果气运为叙事工具 |
| `xuanhuan` | 玄幻 | zh | 战斗 + 数值 + 等级体系，节奏快、爽点密 |
| `urban` | 都市 | zh | 商战 / 社交 / 人脉，年代考据开启 |
| `horror` | 恐怖 | zh | 氛围递进 + 揭示，无数值无战力 |
| `other` | 通用 | zh | 兜底 fallback；最小章节类型 + 通用爽点 |
| `cultivation` | English Cultivation | en | 仙侠英文向，强调 disciplined breakthrough |
| `litrpg` | LitRPG | en | 数值 + 战力双开，level-up 节奏紧密 |
| `progression` | Progression Fantasy | en | 等级跃迁，每 tier 必须感觉根本不同 |
| `tower-climber` | Tower Climbing | en | 楼层结构 3-8 章一段 arc，难度可见上升 |
| `system-apocalypse` | System Apocalypse | en | 末世 + 系统 + 派系；早期生存压力每章 |
| `dungeon-core` | Dungeon Core | en | 双 POV（dungeon / adventurer）交替；数值开启 |
| `isekai` | Isekai / Portal Fantasy | en | 异世界 + 文化冲突；战力开但数值关 |
| `cozy` | Cozy Fantasy | en | 慢节奏、社区感、季节循环；数值/战力全关 |
| `romantasy` | Romantasy | en | 言情 + 奇幻，浪漫节拍每幕断点 |
| `sci-fi` | Science Fiction | en | 科技 + 政治 / 探索；强制开 eraResearch |

> 中英分布：5 个中文（xianxia / xuanhuan / urban / horror / other），10 个英文。中文 Writer 系统 prompt 走 `writer-prompts.ts` 的 `zhRules`，英文走 `enRules`，由 `language` 字段决定。

## 6. 怎么加自定义题材

1. 复制 `templates/genres/other.md` 为蓝本（兜底，所有字段保守）。
2. 改 `id`（必须等于新文件名 stem，全部小写、kebab-case）。
3. 按本文 §1 schema 定 frontmatter；至少要给 `name` / `id` / `chapterTypes` / `fatigueWords` 这 4 个非默认字段。
4. body 里写题材禁忌 / 节奏 / 叙事指导，长度建议 30-60 行（参考 `xianxia.md` / `litrpg.md`）。
5. 把文件放到：
   - **全局**（推荐用户做 PR 进 SKILL）：`{SKILL_ROOT}/templates/genres/<id>.md`
   - **项目级 override**：`books/<bookId>/genres/<id>.md`（同名优先项目级）
6. 在 `books/<id>/book.json#genre` 填入新 id；下一次 Writer / Auditor 启动会自动 pick 上。

最小可用样例（最少字段）：

```markdown
---
name: 武侠
id: wuxia
chapterTypes: ["对决章", "江湖章", "过渡章"]
fatigueWords: ["眼中闪过一丝", "气血翻涌", "仿佛", "竟然"]
numericalSystem: false
powerScaling: true
eraResearch: true
pacingRule: "每 3 章一次有意义的对决或江湖事件"
satisfactionTypes: ["以武会友", "门派恩怨", "侠义抉择"]
auditDimensions: [1,2,3,4,6,7,8,9,10,13,14,15,16,17,18,19,24,25,26]
---

## 题材禁忌
- 主角靠"突然顿悟"跳级，无具体武理铺垫
- 江湖人际关系扁平化（拜把子/灭门理由不立）

## 节奏
内功外功穿插，对决前必有信息差或心法铺垫。
```

## 7. 与其他规则层的关系（rule-stack）

GenreProfile 是 L1（最底层、跨章不变）。覆盖优先级（高 → 低）：

```
L4 runtime（Planner 当前指令） >
L3 章节（chapter_memo "不要做" / "暂不掀") >
L2 全书（book_rules.md 主角铁律 / 全书禁忌） >
L1 题材（templates/genres/<id>.md）
```

Writer 全读四层；Reviser 守 L1+L2+L3（不擅自冲撞 runtime override）；Auditor 强制 L1（题材禁忌违反 → critical）/ 警告 L2 / 软引导 L3。详见 `references/rule-stack.md`。

## 8. 常见错误

- `book.json#genre` 写了 catalog 之外的 id（如 "fantasy"）→ Writer 会回退 `other.md`，但用户可能没意识到爽点 / 章节类型全是通用模板。**务必在 init 阶段告知用户回退**。
- 项目级 override 文件没改 `id` 字段（仍写 `id: xianxia`）→ schema 校验过得了，但与文件名不一致，列表展示混乱。
- 自定义题材的 `auditDimensions` 漏掉 dim 27（敏感词）→ 即便漏了，敏感词扫描仍由确定性脚本 `sensitive_scan.py` 保底，但 LLM 这一道审失效，建议默认含 27。
- `numericalSystem: true` 但 body 里没写资源衰减规则 → Writer 会乱算账本；最少补一段"同质资源 N 次后衰减 X%"之类的硬约束。
