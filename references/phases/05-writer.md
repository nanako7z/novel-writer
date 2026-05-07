# Phase 05 — Writer（写正文）

> 移植自 inkos `packages/core/src/agents/writer.ts` 与 `writer-prompts.ts`（~865 行系统 prompt）。本文件是整套 SKILL 的**核心**：Writer 阶段的全部 13-14 个可拼装段落按当前章节状态 / `book.json` / `book_rules` / fanfic 标记 / 风格指纹按需组合。
>
> 因系统 prompt 拼装段较多，本 phase 文件已**拆分为 1 主调度器 + 6 个 sub-reference**。本文件保留概览 + 拼装顺序 + 失败处理 + 注意事项；各段 verbatim prompt 文本与启用条件归到对应 sub-reference 文件。

---

## 何时进入

- Composer (03) 已落 `composed_context.md`、Architect (04) 在首章/重置时已写 `prose_density.md`。
- 当前章 `chapter_memo.md` 已由 Planner (02) 产出且通过 schema 校验。
- 上一章（如有）的 Settler (07) 已成功 apply delta，真理文件无 dirty 标记。
- 触发表述："写第 N 章" / "继续往下写" / `/novel-writer write-next`。

## Inputs

读以下文件（顺序即 Writer 系统 prompt 拼装顺序）：

1. `inkos.json` + `book.json` —— 取 `platform`、`genre`、`chapterWordCount`、`fanficMode`、`language`、`bookRules.enableFullCastTracking` 等。
2. `genre_profiles/<genre>.md`（题材规则）+ `book_rules.md`（主角铁律 + 全书禁忌）。
3. `chapter_memo.md`（本章备忘，7 段）。
4. `composed_context.md`（Composer 装配的 rule-stack + 上下文窗口）。
5. `story/state/current_state.json` + `hooks.json` + `chapter_summaries.json`（真理文件）。
6. `style_guide.md`（必读，可能是空的）。
7. `style_profile.json`（如启用风格模仿 — 见 references/branches/style.md）。
8. `fanfic_canon.md`（如 `book.json#fanficMode != null` — 见 references/branches/fanfic.md）。
9. `prose_density.md`（首章/重置后 Architect 产物，给散文密度基准）。
10. `LengthSpec`（由 `book.chapterWordCount` 经 `utils/length-metrics.buildLengthSpec` 解析）。

## Process

Writer system prompt 由以下 14 + 1 个具名子段按需拼装（§7.5 是 commit `b1cc3a7` 落地的密度纪律插槽，置于 §7 之后、§8 之前）。每段的"作用 / 何时启用 / verbatim 中文 prompt"详情已下沉到对应 sub-reference 文件。本 phase 文件只给概览表 + 拼装顺序 + §10 / §11 两处简短指针段。

### Process 概览表（按拼装顺序）

| § | 段名 | 何时启用 | 详情 | 关键合约 |
|---|---|---|---|---|
| 1 | 题材引言 | 恒启 | [genre-injection §1](../writer/genre-injection.md) | 定身份与平台 |
| 2 | 输入治理契约 + 章节备忘对齐 | governed 模式 | [governed-context §2](../writer/governed-context.md) | chapter_memo 7 段必须落地 |
| 3 | LengthSpec 字数治理 | 恒启 | [governed-context §3](../writer/governed-context.md) | 5 区间值 target/soft/hard |
| 4 | 写作工艺卡（14 craft rules + 段落字数硬尺） | 恒启 | [craft-and-anti-ai §4 / §4.X](../writer/craft-and-anti-ai.md) | 选下一句的判据；§4.X 段落 40-120 字 / 短段 ≤ 5 / 连续短段 ≤ 2 |
| 5 | 创作宪法（14 原则散文版） | 恒启 | [craft-and-anti-ai §5](../writer/craft-and-anti-ai.md) | internalise，never quote |
| 6 | 沉浸感六支柱 | 恒启 | [craft-and-anti-ai §6](../writer/craft-and-anti-ai.md) | 每场景前几页静默立柱 |
| 7 | 黄金开场纪律（前 3 章） | chapterNumber ≤ 3 | [golden-opening §7](../writer/golden-opening.md) | 7.A 散文版 + 7.B 列表版；前 300 字反转钩 / 开篇人物 ≤ 2 / 80/20 断章 |
| 7.5 | 看点密集度（番茄老师鎏旗，硬尺） | 恒启 | [density-discipline §7.5](../writer/density-discipline.md) | 300 字爽点 / 500 字钩子 / 1000-1500 字完整悬念 |
| 8 | 题材规则 + 主角铁律 | 8.A 恒启 / 8.B 见条件 | [genre-injection §8.A](../writer/genre-injection.md) + [book-rules §8.B](../writer/book-rules.md) | L1 题材 + L2 主角，冲突取 L2 |
| 9 | book_rules + style_guide | 见各小节条件 | [book-rules §9](../writer/book-rules.md) | 全书规则正文 + 文风指南 |
| 10 | （条件）Fanfic Canon | `book.json#fanficMode != null` | 简短指针段 inline 见下 → [branches/fanfic.md](../branches/fanfic.md) | canon > 题材 body |
| 11 | （条件）Style Fingerprint | `style_profile.json` 存在 | 简短指针段 inline 见下 → [branches/style.md](../branches/style.md) | 模仿目标统计指纹 |
| 11.5 | 题材 profile 注入 | 恒启 | [genre-injection §11.5](../writer/genre-injection.md) | frontmatter 字段扇出到多段 |
| 12 | 去 AI 味铁律 + 硬性禁令 | 恒启 | [craft-and-anti-ai §12](../writer/craft-and-anti-ai.md) | 5 类典型 AI 痕迹 + 3 条硬性禁令 |
| 13 | 全员追踪 | `enableFullCastTracking == true` | [book-rules §13](../writer/book-rules.md) | POST_SETTLEMENT 多角色清单 |
| 14 | 输出格式契约（OUTPUT FORMAT） | 恒启（full/creative 分支） | [output-format §14](../writer/output-format.md) | sentinel verbatim 大写；14.A 末尾可选 `=== HOOK_PAYOFF_AUDIT ===` 自检块 |

> 段号不连续是正常的——§11.5 是从 inkos 移植时为兼容已有段号在 §11 之后插入的 "题材联动" 总开关，不是排版错误。

### §10 简短指针段（inline）

```
## 同人原作语料（mode: <fanficMode>）

<从 fanfic_canon.md 注入的 5 段：world_rules / character_profiles / key_events / power_system / writing_style>

## 角色原话采样
<character_voice_profiles — 每个核心角色 3-5 条典型语癖与口头禅>

## 模式指令（<fanficMode>）
<canon: 严格忠于原作世界观与人物 / au: 允许 setting 漂移但角色魂保留 / ooc: 故意反差但需自圆其说 / cp: 配对优先，原作其他线降级>
```

> 启用条件 `book.json#fanficMode != null`。完整 mode 三件套（preamble / self-check / severity 调整）+ 维度 34/35/36/37 的覆盖规则在 [branches/fanfic.md](../branches/fanfic.md) 里给。本节只负责"在 Writer 系统 prompt 的正确位置开个槽"。

### §11 简短指针段（inline）

```
## 文风指纹（模仿目标）

以下是从参考文本中提取的写作风格特征。你的输出必须尽量贴合这些特征：

<styleFingerprint markdown — 句长均值 / 段长均值 / TTR / 句首 top-5 / 修辞密度等>
```

> 启用条件 `styleFingerprint` 字段非空（来自 `style_profile.json` 或 `style_analyze.py --inject`）。风格分析 LLM prompt（定性）与统计算法（`style_analyze.py`）的注入策略、`--stats-only` 与全量模式的差异在 [branches/style.md](../branches/style.md) 里给。

---

## Output contract

写出的"全章 LLM 响应"必须满足：

1. 严格按 §14 区块顺序，每块标题独占一行（`=== BLOCK ===`）。
2. `CHAPTER_CONTENT` 字数落入 `[hardMin, hardMax]`；目标 `[softMin, softMax]`。
3. `PRE_WRITE_CHECK` 表格每行的"本章记录"列必须有内容，不得留空或只写"略"。
4. `UPDATED_HOOKS` 中所有 hook_id 必须能在 `story/state/hooks.json` 找到（新增的 hook 由 Settler 落入）；正文中**禁止**出现 hook_id。
5. 角色矩阵的"已知/未知"字段必须基于真理文件，不得越界。
6. 全文不得违反 §12 硬性禁令（"不是…而是…"句式 / 破折号 `——`）。

落盘约定：

- Writer 输出**整段保留**写到 `story/raw_writer/<chapter_id>.md`（保留 PRE_WRITE_CHECK 与 POST_SETTLEMENT 供回查）。
- Writer-Parser 把 `CHAPTER_TITLE` + `CHAPTER_CONTENT` 拆出，写到 `chapters/<chapter_id>.md`（带 frontmatter）。
- 其余 UPDATED_* 区块进入 Observer (06) → Settler (07) 流程，不直接落真理文件。

## Failure handling

| 失败种类 | 检测方式 | 处理 |
|---|---|---|
| 字数越 hard 区间 | `word_count.py` | 立即重写（不走 Normalizer） |
| 字数越 soft 区间但未越 hard | `word_count.py` | 走 Phase 08 Normalizer 单次 compress / expand |
| 缺失任一 `=== BLOCK ===` 头 | Writer-Parser | 让 Writer 重新输出**仅缺失部分**（最多 2 次） |
| `PRE_WRITE_CHECK` 与 chapter_memo 七段对不上 | Auditor dim 1 检查 | 进入 audit-revise 循环 |
| 命中 §12 硬性禁令（脚本可正则扫） | `ai_tell_scan.py` 退出码 1 | 立即 `anti-detect` Reviser |
| 命中政治敏感词（block 级） | `sensitive_scan.py` 退出码 1 | 立即 `anti-detect` Reviser；不允许进入 Settler |
| `UPDATED_HOOKS` 引用了不存在的 hook_id | Settler `apply_delta.py` schema 校验 | 打回 Writer，提示 hook_id 必须先经 Settler 注册 |
| Writer 一次输出超长（截断） | Writer-Parser 末尾未见 `=== UPDATED_CHARACTER_MATRIX ===` | 续写：用 "继续" 让 Writer 接着输出剩余区块（最多 1 次） |

audit-revise 循环最多 3 轮，分数提升 < 3 即提前退出（沿用 inkos 阈值，见 `chapter-review-cycle.ts`）。

## 注意事项

1. **拼装顺序不可变**：题材引言 → 输入治理 → 章节备忘 → 长度规格 → 工艺卡（含 §4.X 段落字数硬尺） → 创作宪法 → 沉浸支柱 → 黄金开场（前 3 章）→ **看点密集度 §7.5** → 题材+主角 → 本书规则+文风 → fanfic → style → 去 AI 味 → 全员追踪 → 输出格式。任意调换会让 Writer 把上下文优先级搞反（例如长度规格被推到题材规则之后，Writer 会把题材本能凌驾于硬区间之上）。
2. **§5 创作宪法 / §6 沉浸支柱 / §10-11 fanfic+style 是"内化"段**：写作时必须避免把段落里的小标题、关键词原样复述到正文（"这一刻他终于明白了什么是 show don't tell"——这是最严重的 AI 味）。Auditor dim 24（叙述者姿态）会重点抓这种自我引用。
3. **黄金开场（§7）只在 chapterNumber ≤ 3 注入**：第 4 章及之后整段移除，以免把"开篇约束"误用到中卷。
4. **fanfic 与 style 是正交的**：可以同时启用（同人 + 风格模仿），两段都要注入；任一缺失就跳过对应整段。
5. **`style_guide.md` 内嵌 PRE_WRITE_CHECKLIST**（v10 之后）：Writer 不再需要单独的 PreWriteChecklist 段；旧模板的 13 项自检条目应迁移到 style_guide.md 内部。
6. **不要把 chapter_memo 7 段的小标题写进正文**：Writer 必须把 7 段"翻译成场景与动作"，而不是当作 PRE_WRITE_CHECK 之外的章节标题。
7. **POST_SETTLEMENT 与 UPDATED_HOOKS 是 Settler 的输入**：Writer 这里只是"声明本章有什么变动"，真正的真理文件落盘是 Settler 通过 `apply_delta.py` 完成的——Writer 不得自行修改 `story/state/*.json`。
8. **Writer 出的稿如果走顺通过 audit，会再过一道 polisher 文字打磨——见 phases/11-polisher.md。Writer 不必为"文字最终态"负责。**
