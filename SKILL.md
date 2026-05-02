---
name: novel-writer
description: 中文网文写作工作流（移植自 inkos CLI）。当用户要写小说、网文、同人或长篇虚构故事时使用，覆盖项目初始化、写下一章、审章/改章、风格模仿、同人 canon/AU/OOC/CP 设置、伏笔治理、章节文字打磨、卷级摘要压缩等场景。流水线 Plan → Compose → (Architect+FoundationReviewer) → Write → 写后检 → Normalize → Audit → Revise → Settle → (Polish)，含 15 个题材 profile、37 个审计维度、9 类事实追踪、四级规则栈、伏笔生命周期治理、滑窗记忆、3 阶段 Settler delta 解析、去 AI 味与敏感词扫描、章节卷级压缩。即使用户没明说"用 novel-writer"，只要意图是写网文章节、做长篇连载、做同人创作或做风格模仿写作，都应主动用此 skill。**不要触发**于一次性短文、产品文档、PRD、技术博客、诗词歌赋、剧本/编剧、学术论文这类非长篇虚构连载的场景。
---

# novel-writer

把 [inkos CLI](https://github.com/Narcooo/inkos) 的多 agent 网文写作流水线，移植成可在 Claude Code 里直接驱动的 SKILL。

## 触发与不触发

**触发**（用户大致这么说就用）：
- "起一本新书 / 立项 / 初始化网文项目"
- "写第 N 章 / 写下一章 / 续写"
- "审一下这章 / 评分 / 看看有什么问题"
- "改一下 / polish / 把 XX 句改成 XX"
- "我想写《XX》的同人"
- "学一下这段文字的风格再写"
- 用户在已有的 `books/<id>/` 下做任何写作动作

**不触发**：
- 一次性短文、产品文档、技术 / 学术写作、诗词、剧本、PRD
- 用户问"网文行业怎么样"等纯咨询（这是闲聊不是创作）
- 用户改的是 `books/` 之外的文件

不确定时**先问一句**："你是要长篇连载小说，还是别的写作？"——别贸然进流水线。

## 第一步：判断用户意图

读取**用户当前工作目录**和最近一两轮对话，按下表决定下一步：

| 状态 | 决策 |
|---|---|
| 工作目录里没有 `inkos.json` | 走"项目初始化"小节 |
| 有 `inkos.json` 但没有任何 `books/<id>/` | 走"创建一本书"小节 |
| 有 `books/<id>/` 但用户问的是某本具体的书 | 进"主循环 / 单点指令" |
| 用户给市场/题材问题（不立项） | 走 [phase 01 radar](references/phases/01-radar.md) |
| 用户给同人原作 + 模式 | 走 [branches/fanfic](references/branches/fanfic.md) |
| 用户给参考文本求风格 | 走 [branches/style](references/branches/style.md) |

详细路由表见 [references/phases/00-orchestration.md](references/phases/00-orchestration.md#路由表用户指令--入口)。

## 项目初始化 / 创建一本书

调脚本，不要手动逐文件创建：

```bash
python {SKILL_ROOT}/scripts/init_book.py \
  --workdir <用户给的目录> \
  --id <kebab-case-id> \
  --title "<书名>" \
  --genre <xianxia|xuanhuan|urban|...> \
  --platform <tomato|feilu|qidian|other> \
  --target-chapters <int> \
  --chapter-words <int> \
  [--lang zh|en] \
  [--fanfic-mode canon|au|ooc|cp] \
  [--parent-book-id <id>]
```

脚本会落地：根目录 `inkos.json` + `books/<id>/{book.json, story/*, story/state/*, chapters/, story/runtime/, story/outline/, story/roles/...}`。

**初始化后你需要做的事**（脚本不做语义工作）：
1. 把用户给的题材描述、长期愿景填进 `books/<id>/story/author_intent.md`（替换"(Describe...)"占位）
2. 把"接下来 1-3 章想做什么"填进 `current_focus.md`
3. fanfic 模式：跑 [branches/fanfic.md](references/branches/fanfic.md) 抽 `fanfic_canon.md`
4. 提醒用户："基础有了，现在可以让我写第 1 章。第 1 章会自动当成黄金开场处理。"

## 主循环：写下一章

详细伪代码 + 顺序见 [references/phases/00-orchestration.md](references/phases/00-orchestration.md)。简版：

```
Plan → Compose（含 memory_retrieve 滑窗）→ (首章/卷尾才 Architect) → Write
     → Normalize（脚本 + 必要时 phase 08）
     → Audit-Revise（最多 3 轮，分数提升 < 3 即退出）
     → Observe → Settle → apply_delta 校验（含 hook 治理闸门）
     → (audit 真正过线 ≥88 时跑 Polisher 文字打磨)
     → 章节正文写入 chapters/{NNNN}.md
```

每个阶段读对应文件，**先看那个 phase 文件再动手**：

| Phase | 文件 | 摘要 |
|---|---|---|
| 02 | [planner](references/phases/02-planner.md) | 生成 chapter_memo（YAML+md），定义本章要兑现什么、不做什么 |
| 03 | [composer](references/phases/03-composer.md) | 装配 context_pkg + rule_stack（无 LLM；先调 `memory_retrieve.py` 取滑窗） |
| 04 | [architect](references/phases/04-architect.md) | 散文密度的基础设定（首章 / 卷切换才跑；含 [Foundation Reviewer](references/foundation-reviewer.md) 闸门） |
| 05 | [writer](references/phases/05-writer.md) | 写正文，13-14 段 prompt 模块化拼装 + 题材 profile 注入 + sentinel 输出格式 |
| 5b/5c | (脚本) | `writer_parse.py` 拆 sentinel + `post_write_validate.py` 写后检（见 [post-write-validation](references/post-write-validation.md)） |
| 06 | [observer](references/phases/06-observer.md) | 抽 9 类事实，输出 OBSERVATIONS 块 |
| 07 | [settler](references/phases/07-settler.md) | 产 RuntimeStateDelta JSON（apply_delta `--input-mode raw` 走 3 阶段 parser） |
| 08 | [normalizer](references/phases/08-normalizer.md) | 单次长度修正 |
| 09 | [auditor](references/phases/09-auditor.md) | 37 维审计（按题材 profile 过滤）+ 评分 |
| 10 | [reviser](references/phases/10-reviser.md) | 6 模式修订（auto/polish/rewrite/rework/anti-detect/spot-fix） |
| 11 | [polisher](references/phases/11-polisher.md) | audit 真正过线（≥88）后的文字层打磨，单 pass、不动情节 |
| 12 | [consolidator](references/phases/12-consolidator.md) | 卷级摘要压缩 + 历史归档（手动触发；先跑 `consolidate_check.py` 检测） |
| 13 | [chapter analyzer](references/phases/13-analyzer.md) | 章节落盘后的定性回顾，写 `analysis.json` 喂下章 Planner（单向只读，不改任何真理文件） |

**主循环关键不变量**（违反就停下）：

1. 真理文件（`story/state/*.json`、`pending_hooks.md` 等）只能经 `scripts/apply_delta.py` 修改，不直接编辑
2. 章节正文落盘前必须经过 audit-revise 闸门——即便没过线也要标 `audit-failed-best-effort`
3. 阶段产物先写到 `story/runtime/chapter-{NNNN}.<phase>.md`，最终成果才落到 `chapters/` 与 `story/state/`
4. LLM 输出解析失败：Planner 重试 ≤ 3，Architect ≤ 2（含 [Foundation Reviewer](references/foundation-reviewer.md) 回环），audit-revise 整轮 ≤ 3
5. Reflector **不是**单独阶段；其职责并入 audit-revise loop
6. Writer 输出走 sentinel 格式 → `writer_parse.py` + `post_write_validate.py` 是 Normalize 之前的强制检查；critical 命中允许 Writer 重写一次

## 单点指令（不进主循环）

| 用户大致这么说 | 做什么 |
|---|---|
| "审一下第 N 章" | 只跑 [phase 09](references/phases/09-auditor.md)；输出审计结果，不动正文 |
| "把 XX 句改成 YY" | [phase 10 reviser](references/phases/10-reviser.md) `spot-fix` 模式 |
| "整章 polish / 文字打磨一遍" | 直接跑 [phase 11 polisher](references/phases/11-polisher.md)（绕过 audit 借线规则） |
| "用 reviser polish 模式改" | phase 09 + 10 polish 模式（结构层修订，不动情节） |
| "AI 味太重，专项处理" | phase 10 `anti-detect` 模式，先跑 `scripts/ai_tell_scan.py` 拿证据 |
| "重做架构" | phase 04 architect 单跑 |
| "看下伏笔池压力 / 伏笔健康度" | `python scripts/hook_governance.py --book <bookDir> --command health-report` |
| "校验一下真理文件没问题吧" | `python scripts/hook_governance.py --book <bookDir> --command validate` |
| "压缩前面卷 / consolidate / 摘要太多了 / 历史压缩一下" | 先跑 `python scripts/consolidate_check.py --book <bookDir>` 看是否该压；该压则进 [phase 12 consolidator](references/phases/12-consolidator.md) |
| "列出我所有书 / book list" | `python scripts/book.py list` |
| "看下《XX》详情 / book show" | `python scripts/book.py show <bookId>` |
| "重命名 book id" | `python scripts/book.py rename <old> <new>` |
| "删除某本书 / book delete" | `python scripts/book.py delete <bookId> [--archive]`（默认归档不真删） |
| "拷一份 / 用作模板 / book copy" | `python scripts/book.py copy <src> <new>` |
| "看一下当前进度 / status" | `python scripts/status.py [--book <bookDir>] [--chapters]`（默认列所有书） |
| "环境体检 / doctor / 看下 SKILL 是否完整" | `python scripts/doctor.py [--book <bookDir>]`（self-test 12 个脚本 + templates 完整性 + 可选 book 子树校验） |
| "看下 token 用量 / 字数曲线 / 通过率 / analytics" | `python scripts/analytics.py --book <bookDir> [--chapters] [--detection]` |
| "上次写到一半崩了 / 看下是不是有半成品 / recover" | `python scripts/recover_chapter.py --book <bookDir> [--clean]`（识别 runtime/ 残留 + 推荐续接点） |
| "导出 / 出版 / 打包成 epub/txt/md" | `python scripts/export_book.py --book <bookDir> --format txt\|md\|epub [--include-summary]` |
| "看下跨章疲劳 / 是不是写重复了" | `python scripts/fatigue_scan.py --book <bookDir> --current-chapter N [--window 5]`（advisory） |
| "节奏 / 爽点压力 / cadence" | `python scripts/cadence_check.py --book <bookDir> --current-chapter N`（Planner 阶段也会自动跑） |

## 同人 / 风格分支

- **同人** ([references/branches/fanfic.md](references/branches/fanfic.md))：4 模式 canon/au/ooc/cp，每模式三件套——Writer preamble、self-check 注入、Auditor severity 调整。canon 抽取走 `fanfic_canon_importer` prompt（5 SECTION 输出）。
- **风格** ([references/branches/style.md](references/branches/style.md))：纯文本统计走 `scripts/style_analyze.py`，定性分析由 Claude 跑 LLM prompt。两者输出的 `style_profile.json` + `style_guide.md` 一起注入 Writer。

## 质量控制（确定性闸门）

写完一章到 audit 通过之间，按顺序跑这几个脚本。每个的 issue 列表都合并进 audit issues 一起评估。

**1. Writer 落盘后第一步：拆 sentinel**

```bash
python {SKILL_ROOT}/scripts/writer_parse.py --file <raw_writer_output.md> --strict
```

Writer 必须按 [phase 05](references/phases/05-writer.md) 的 sentinel 格式（`=== CHAPTER_TITLE === / === CHAPTER_CONTENT === / === CHAPTER_SUMMARY === / === POSTWRITE_ERRORS ===`）输出。`writer_parse.py` 严格按 sentinel 拆 title/body/summary/postWriteErrors 出 JSON。`--strict` 缺关键 sentinel 直接 exit 2。

**2. 拆完做写后检（mechanical 错误）**

```bash
python {SKILL_ROOT}/scripts/post_write_validate.py --file <body.md> --chapter N
```

抓的是 audit 不太管的机械错：章节编号 self-reference 对不上、段落形态（monolithic / 单段 / 碎片）、对话标点（半角 `"` 紧贴中文、配对错误）、注释泄漏（`[作者按]` / `<TODO>`）、长度 sanity。critical 命中（exit 2）就让 Writer 重写一次（详见 [references/post-write-validation.md](references/post-write-validation.md)）。

**3. audit 之前的去 AI 味 + 敏感词**

```bash
python {SKILL_ROOT}/scripts/ai_tell_scan.py --file <draft.md>
python {SKILL_ROOT}/scripts/sensitive_scan.py --file <draft.md>
```

- `ai_tell_scan` 命中 critical（如"核心动机"等推理框架术语漏进正文）→ 必须改
- `sensitive_scan` 政治词命中（severity=block）→ 必须删
- 性 / 极端暴力词（severity=warn）→ 标记给作者，不强删

参考词表与阈值见 [references/ai-tells.md](references/ai-tells.md) 和 [references/sensitive-words.md](references/sensitive-words.md)。

## 真理文件契约

任何阶段需要更新真理文件，**必须**通过：

```bash
# Settler 直接给的原始输出（含 === POST_SETTLEMENT === / === RUNTIME_STATE_DELTA === sentinel）
python {SKILL_ROOT}/scripts/apply_delta.py --book <bookDir> --delta <settler.raw.md> --input-mode raw

# 或：已经清洗好的 JSON
python {SKILL_ROOT}/scripts/apply_delta.py --book <bookDir> --delta <runtime/chapter-NNNN.delta.json>
```

脚本走 3 阶段 parser：(1) lenient 提 RUNTIME_STATE_DELTA 块（容忍前后 prose）；(2) soft-fix（key alias / 类型 coercion / 数组 wrap，详见 [schemas/runtime-state-delta.md](references/schemas/runtime-state-delta.md) §1b）；(3) 严格 schema 校验。原子写入（`.tmp` + rename），按字段路由到对应文件，并**自动调用** `hook_governance.py` 的 `validate` + `stale-scan` 作为闸门：

- 解析失败：返回 `parserFeedback`（结构化反馈）→ 喂回 Settler 让它修，不是直接 crash
- `validate` 报 critical → 退出码 1，`hookGovernanceBlocked: true`，要求 Settler 重写而不是落盘
- `stale-scan` 标记过期钩子 → 不阻断，但写回 `stale: true` 标志供后续 Planner 参考
- 想跳过治理（不推荐）：`apply_delta.py --skip-hook-governance`

调试 Settler 输出时（不写盘只看解析结果）：`python scripts/settler_parse.py --input <raw.md> --mode raw --out /tmp/d.json`。钩子治理逻辑见 [references/hook-governance.md](references/hook-governance.md)。

直接编辑 `story/state/*.json` 视为脏写，会污染 manifest，**禁止**。

## 规则栈与题材 profile

四级覆盖：L1 题材 → L2 全书 → L3 章节 → L4 runtime（Planner 当前指令）。详见 [references/rule-stack.md](references/rule-stack.md)。Writer 全读，Reviser 守 L1+L2+L3，Auditor 强制 L1 / 警告 L2 / 软引导 L3。

**L1 题材的具体形状**来自 `templates/genres/<book.genre>.md`——15 个内置 profile（仙侠、玄幻、都市、科幻、异世界、塔爬、地牢核、修仙、进展流、惊悚、温馨、罗曼塔、LitRPG、系统末日、其他）。每个 profile 含：fatigueWords（注入 Writer 的反 AI 词表）、chapterTypes、satisfactionTypes、pacingRule、auditDimensions（限定 Auditor 的 37 维子集）、numericalSystem/powerScaling/eraResearch 三个 toggle。题材 id 不在 catalog 内会回退 `other.md`。详见 [references/genre-profile.md](references/genre-profile.md)。

## 滑窗记忆

Composer 阶段第 0 步必须先调：

```bash
python {SKILL_ROOT}/scripts/memory_retrieve.py \
  --book <bookDir> --current-chapter N \
  [--window-recent 6] [--window-relevant 8] \
  [--include-resolved-hooks] [--format json|markdown]
```

输出包含：近窗章节摘要（全文）、相关窗章节摘要（按角色 / hook 重叠筛选，仅 events 字段）、活跃钩子、最近还的钩子（可选）、角色花名册、当前状态快照。

不直接读全部 chapter_summaries 是为了让 30+ 章后 context 不爆。算法说明与可调参见 [references/memory-retrieval.md](references/memory-retrieval.md)。

## 文件树速查

```
{SKILL_ROOT}/
├── SKILL.md                     ← 你正在读
├── references/
│   ├── phases/00-13-*.md        14 个阶段（编排 + radar + 10 agent + polisher + consolidator + chapter analyzer）
│   ├── branches/{fanfic,style}.md
│   ├── rule-stack.md            四级规则栈
│   ├── genre-profile.md         15 题材 profile schema + 注入指南
│   ├── audit-dimensions.md      37 维度全表（按题材 profile 过滤）
│   ├── ai-tells.md              去 AI 味词表与阈值
│   ├── sensitive-words.md       三级敏感词
│   ├── hook-governance.md       伏笔生命周期 + 4 治理命令
│   ├── memory-retrieval.md      滑窗记忆算法
│   ├── foundation-reviewer.md   Architect 5-section 产物的 LLM 审稿闸门
│   ├── post-write-validation.md 写后检规则与 sentinel parser
│   ├── long-span-fatigue.md     跨章疲劳 5 类检测
│   ├── pov-filter.md            POV 可见性三档 + blindspots
│   ├── cadence-policy.md        4 层节奏模型 + per-genre 默认
│   ├── chapter-recovery.md      断点续跑识别与推荐
│   ├── state-projections.md     真理文件压缩视图
│   ├── narrative-control.md     文本上游清洗（实体剥离 + zh/en 软化）
│   ├── writing-methodology.md   通用写作方法论（6 节，可注入 Writer prompt）
│   └── schemas/                 4 个数据形状
├── templates/
│   ├── inkos.json + book.json   元数据种子
│   ├── story/{*.md, state/*.json}  真理文件种子
│   └── genres/                  15 题材 profile（init 时按 --genre 选用）
├── scripts/                     28 个 Python 脚本
│   ├── init_book.py             创建 books/<id>/ 子树
│   ├── book.py                  多书 CRUD（list / show / rename / delete / copy；删除默认归档）
│   ├── apply_delta.py           真理文件唯一写入闸门（3 阶段 parser + hook 仲裁 + governance）
│   ├── settler_parse.py         Settler 输出独立 parser（debug 用）
│   ├── hook_governance.py       promote-pass / stale-scan / validate / health-report
│   ├── hook_arbitrate.py        新候选 → created/mapped/mentioned/rejected 仲裁（apply_delta 自动调）
│   ├── context_filter.py        truth file 轻量降噪（hooks/summaries/subplots/emotional-arcs）
│   ├── narrative_control.py     文本实体剥离 + 软化替换（Composer 上游清洗用）
│   ├── writing_methodology.py   生成 Writer 注入用的写作方法论 markdown
│   ├── spot_fix_patches.py      phase 10 spot-fix 模式的 patches 应用器
│   ├── memory_retrieve.py       Composer 阶段 0 调
│   ├── consolidate_check.py     phase 12 触发检测（read-only）
│   ├── writer_parse.py          Writer 输出 sentinel 严格 parser
│   ├── post_write_validate.py   写后检（机械错 / 段落 / 对话标点 / 注释泄漏）
│   ├── status.py                项目速查（多书 / 单书 / 章节明细）
│   ├── doctor.py                环境 + 模板 + 脚本自检
│   ├── analytics.py             token 用量 / 通过率 / 字数曲线 / 钩子活动
│   ├── fatigue_scan.py          跨章疲劳 5 类检测（advisory）
│   ├── pov_filter.py            POV 可见性过滤（Composer 单 POV 章节调用）
│   ├── split_chapter.py         超长章节拆分候选（target × 1.5+ 时入场）
│   ├── export_book.py           导出 txt / md / epub
│   ├── recover_chapter.py       断点续跑识别（扫 runtime/ 残留）
│   ├── cadence_check.py         节奏压力探针（Planner 阶段调）
│   ├── state_project.py         真理文件 4 类压缩视图
│   ├── word_count.py            LengthSpec 区间判定
│   ├── style_analyze.py         5 项纯文本风格统计
│   ├── ai_tell_scan.py          去 AI 味确定性闸门
│   └── sensitive_scan.py        三级敏感词扫描
└── evals/evals.json             SKILL 自身的 7 个测试 prompt
```

## 注意事项

- **不要凭印象编 prompt**：每个 phase 文件里的"系统 prompt"块都搬自 inkos 源码，请整段照用，不要改写。改 prompt 等于改风格基线。
- **流程长但不要跳步**：哪怕用户催"快点写"，也要按主循环走——跳过 audit/observer 会让真理文件越写越脏，后面无法继续创作。
- **首次失败不慌**：Planner / Architect / audit-revise 都内置重试上限。到达上限仍失败，老老实实告诉用户哪里不通过，不要伪造通过。
- **保持中文**：所有面向作者和角色的文本都是中文。脚本日志可以英文。
- **不写 README**：本 SKILL 内不带 README；用法直接看本 SKILL.md 即可。
