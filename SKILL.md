---
name: novel-writer
description: 中文网文 / 长篇连载 / 同人 / 风格模仿写作工作流（移植自 inkos CLI）。覆盖立项、写下一章、审改、伏笔治理（揭 1 埋 1 + payoff 定位）、文字打磨、卷级压缩。13 阶段流水线 Plan→Compose→(Architect)→Write→写后检→Normalize→Audit→Revise→Settle→(Polish)→Analyze→(Consolidate)，含 15 题材 profile、37 审计维度、9 类事实追踪、四级规则栈、滑窗记忆、3 阶段 Settler delta 解析、段落 40–120 字反碎片硬尺、去 AI 味与敏感词扫描。用户在 `books/<id>/` 下做任何写作动作、说"写第 N 章"/"起一本新书"/"审一下这章"/"我想写《X》同人"/"学这段风格再写"，都应主动触发——即使没明说"用 novel-writer"。**不触发**：一次性短文、产品文档、PRD、技术博客、诗词、剧本、学术论文。
---

# novel-writer

[inkos CLI](https://github.com/Narcooo/inkos) 多 agent 网文写作流水线的 Claude Code 移植。

## 触发

**触发**（用户大致这么说就用）：
- "起一本新书 / 立项 / 初始化网文项目"
- "写第 N 章 / 写下一章 / 续写"
- "审一下这章 / 评分 / 看看有什么问题"
- "改一下 / polish / 把 XX 句改成 XX"
- "我想写《XX》的同人"
- "学一下这段文字的风格再写"
- 用户在已有的 `books/<id>/` 下做任何写作动作

**不触发**：一次性短文、产品文档、技术 / 学术写作、诗词、剧本、PRD；用户改的是 `books/` 之外的文件；用户问"网文行业怎么样"等纯咨询。

不确定时**先问一句**："你是要长篇连载小说，还是别的写作？"——别贸然进流水线。

## 决策路由

读取**用户当前工作目录**和最近一两轮对话，按下表决定下一步：

| 状态 | 决策 |
|---|---|
| 工作目录里没有 `inkos.json` | 走"项目初始化"小节 |
| 有 `inkos.json` 但没有任何 `books/<id>/` | 走"创建一本书"小节 |
| 有 `books/<id>/` 但用户问的是某本具体的书 | 进主循环 / 单点指令 |
| 用户给市场/题材问题（不立项） | 走 [phase 01 radar](references/phases/01-radar.md) |
| 用户给同人原作 + 模式 | 走 [branches/fanfic](references/branches/fanfic.md) |
| 用户给参考文本求风格 | 走 [branches/style](references/branches/style.md) |

详细路由表见 [references/phases/00-orchestration.md](references/phases/00-orchestration.md)。

## 项目初始化 / 创建一本书

调脚本，不要手动逐文件创建：

```bash
python {SKILL_ROOT}/scripts/init_book.py \
  --workdir <用户给的目录 | ./novel-writer-workspace（默认）> \
  --id <kebab-case-id> \
  --title "<书名>" \
  --genre <xianxia|xuanhuan|urban|...> \
  --platform <tomato|feilu|qidian|other> \
  --target-chapters <int> \
  --chapter-words <int> \
  [--lang zh|en] \
  [--fanfic-mode canon|au|ooc|cp] \
  [--parent-book-id <id>] \
  [--brief <path-to-brief.md>] \
  [--current-focus "<inline string>" 或 @<path>]
```

**`--workdir` 取值**：用户没显式给路径就传 `./novel-writer-workspace`，不要省略让脚本回退到 cwd 默认。完整规则见 [references/setup/workdir-rules.md](references/setup/workdir-rules.md)。

**当用户初始请求里已给 brief**（"我想写一本…"+ 主题/主角/世界观/卷规划），别让它停在占位 author_intent.md 里——通过 `--brief` 喂进去。已有文件直接 `--brief path/to/brief.md`；用户散文先 `Write` 到 `/tmp/brief.md` 再喂。

**初始化后**：检查 init_book 输出的 JSON `nextStep` 字段：

| `nextStep` | 含义 | 你接下来要做 |
|---|---|---|
| `"architect"` | 用户给了 brief，author_intent 已写入 | **立刻**进入 [phase 04 architect](references/phases/04-architect.md)（不要等"写第 1 章"），跑 5 SECTION + Foundation Reviewer，落盘 outline / roles / pending_hooks。完成后告诉用户"基础架构已生成，可以让我写第 1 章了"。 |
| `"author_intent"` | 没给 brief，仍是占位 | 让用户填 `author_intent.md`（核心命题、主题）和 `current_focus.md`（接下来 1-3 章），等用户说"写第 1 章"时再触发 Architect。 |

无论哪条路，fanfic 模式（`--fanfic-mode` 已设）都需要再跑一遍 [branches/fanfic.md](references/branches/fanfic.md) 抽 `fanfic_canon.md`。

## 主循环：写下一章

完整伪代码见 [references/phases/00-orchestration.md](references/phases/00-orchestration.md)。一图：

```
Plan → Compose（含 memory_retrieve 滑窗）→ (首章/卷尾才 Architect) → Write
     → Normalize（脚本 + 必要时 phase 08）
     → Audit-Revise（最多 3 轮；分数提升 < 3 即退出）
     → Observe → Settle → apply_delta（hook 治理闸门）
     → audit 过线 ≥88 时跑 Polisher
     → 章节正文写入 chapters/{NNNN}.md
```

每个阶段动手前先读对应 phase 文件：

| Phase | 文件 | 摘要 |
|---|---|---|
| 02 | [planner](references/phases/02-planner.md) | 生成 chapter_memo（YAML+md），定义本章要兑现什么、不做什么 |
| 03 | [composer](references/phases/03-composer.md) | 装配 context_pkg + rule_stack（无 LLM；先调 `memory_retrieve.py` 取滑窗） |
| 04 | [architect](references/phases/04-architect.md) | 散文密度的基础设定（首章 / 卷切换才跑；含 [Foundation Reviewer](references/foundation-reviewer.md) 闸门） |
| 05 | [writer](references/phases/05-writer.md) | 写正文，13-14 段 prompt 模块化拼装 + 题材 profile 注入 + sentinel 输出格式 |
| 5b/5c | (脚本) | `writer_parse.py` 拆 sentinel + `post_write_validate.py` 写后检 |
| 06 | [observer](references/phases/06-observer.md) | 抽 9 类事实，输出 OBSERVATIONS 块 |
| 07 | [settler](references/phases/07-settler.md) | 产 RuntimeStateDelta JSON（apply_delta `--input-mode raw` 走 3 阶段 parser） |
| 08 | [normalizer](references/phases/08-normalizer.md) | 单次长度修正 |
| 09 | [auditor](references/phases/09-auditor.md) | 37 维审计（按题材 profile 过滤）+ 评分 |
| 10 | [reviser](references/phases/10-reviser.md) | 6 模式修订（auto/polish/rewrite/rework/anti-detect/spot-fix） |
| 11 | [polisher](references/phases/11-polisher.md) | audit 真正过线（≥88）后的文字层打磨，单 pass、不动情节 |
| 12 | [consolidator](references/phases/12-consolidator.md) | 卷级摘要压缩 + 历史归档（手动触发；先跑 `consolidate_check.py` 检测） |
| 13 | [chapter analyzer](references/phases/13-analyzer.md) | 章节落盘后的定性回顾，写 `analysis.json` 喂下章 Planner（单向只读） |

**主循环不变量**：单一来源见 [references/invariants.md](references/invariants.md)（11 条编号化规则）。本文件不复述。

**step-checkpoint 进度跟踪**（见 invariant #11）：每个 step 入口推荐先调 `scripts/loop_state.py require --step <id>`，完成后 `mark`。`require` 间相互识别失序（exit 3），但**不**校验真理文件写脚本（apply_delta / chapter_index / snapshot_state）——它是 advisory 进度可见性，不是硬 gate。硬约束由各脚本自己的 schema 校验（`apply_delta`、`commitment_ledger`、`post_write_validate` 等）兜底。

## 单点指令（不进主循环）

**措辞模糊时先问一句**："是想整章润一遍，还是只动你指出的那一小段？"——半径选错了既浪费 token，又容易把作者满意的段落改坏。

最常用速查（完整 40+ 行表格见 [references/single-point-commands.md](references/single-point-commands.md)）：

| 用户大致这么说 | 做什么 |
|---|---|
| "审一下第 N 章" | phase 09 单跑 |
| "把 XX 句改成 YY" | phase 10 reviser `spot-fix` |
| "整章 polish" | phase 11 polisher（绕过 audit 借线） |
| "AI 味太重" | phase 10 `anti-detect`，先跑 `ai_tell_scan.py` |
| "看进度 / status" | `scripts/status.py` |
| "看 loop state / 写到哪一步了" | `scripts/loop_state.py status --book <bd>` |
| "回滚到第 N 章" | `scripts/snapshot_state.py restore` |
| "调整指导 md / 设定 / 焦点" | 走 user-directive docOps，详见 [user-directive-flow.md](references/user-directive-flow.md) |
| "改 author_intent / book_rules / fanfic_canon" | 直接 `Edit` + `apply_delta.py log-direct-edit`，详见 [user-directive-flow.md](references/user-directive-flow.md) |

## 同人 / 风格分支

- **同人** ([branches/fanfic.md](references/branches/fanfic.md))：4 模式 canon/au/ooc/cp，每模式三件套——Writer preamble、self-check 注入、Auditor severity 调整。canon 抽取走 `fanfic_canon_importer` prompt（5 SECTION 输出）。
- **风格** ([branches/style.md](references/branches/style.md))：纯文本统计走 `scripts/style_analyze.py`，定性分析由 Claude 跑 LLM prompt。两者输出的 `style_profile.json` + `style_guide.md` 一起注入 Writer。

## 质量控制（确定性闸门）

写完一章到 audit 通过之间，按顺序跑这几个脚本。每个的 issue 列表都合并进 audit issues 一起评估。

1. **Writer 落盘后第一步**：`writer_parse.py --strict` 拆 sentinel（缺关键 sentinel exit 2）
2. **拆完做写后检**：`post_write_validate.py`（机械错；critical exit 2 让 Writer 重写一次）
3. **audit 之前**：`ai_tell_scan.py`（去 AI 味）+ `sensitive_scan.py`（敏感词；政治词 block 必删，性/极端暴力 warn 标记）

参考词表见 [ai-tells.md](references/ai-tells.md) 和 [sensitive-words.md](references/sensitive-words.md)。详细规则见 [post-write-validation.md](references/post-write-validation.md)。

## 真理文件契约

任何阶段需要更新真理文件，**必须**通过 `scripts/apply_delta.py`（见 invariant #1）：

```bash
# Settler 直接给的原始输出（含 sentinel）
python {SKILL_ROOT}/scripts/apply_delta.py --book <bookDir> --delta <settler.raw.md> --input-mode raw

# 或：已经清洗好的 JSON
python {SKILL_ROOT}/scripts/apply_delta.py --book <bookDir> --delta <runtime/chapter-NNNN.delta.json>
```

3 阶段 parser + 字段路由 + 自动 hook_governance validate / stale-scan + 原子回滚硬承诺。详见 [hook-governance.md](references/hook-governance.md) 与 [schemas/runtime-state-delta.md](references/schemas/runtime-state-delta.md)。

伏笔 / hook / cliffhanger 术语统一见 [terminology.md](references/terminology.md)。

## 规则栈与题材 profile

四级覆盖：L1 题材 → L2 全书 → L3 章节 → L4 runtime。详见 [rule-stack.md](references/rule-stack.md)。

L1 题材具体形状来自 `templates/genres/<book.genre>.md`——15 个内置 profile（仙侠 / 玄幻 / 都市 / 科幻 / 异世界 / 塔爬 / 地牢核 / 修仙 / 进展流 / 惊悚 / 温馨 / 罗曼塔 / LitRPG / 系统末日 / 其他）。题材 id 不在 catalog 内会回退 `other.md`。详见 [genre-profile.md](references/genre-profile.md)。题材 catalog 管理用 [`scripts/genre.py`](scripts/genre.py) `list / show / add / validate`。

## 滑窗记忆

Composer 阶段第 0 步必须先调：

```bash
python {SKILL_ROOT}/scripts/memory_retrieve.py \
  --book <bookDir> --current-chapter N \
  [--window-recent 6] [--window-relevant 8] \
  [--include-resolved-hooks] [--format json|markdown]
```

不直接读全部 chapter_summaries 是为了让 30+ 章后 context 不爆。算法见 [memory-retrieval.md](references/memory-retrieval.md)。

## 流程红线（不跳步硬尺）

进入主循环前先回答以下 5 个问题——任何一个答"不知道"都说明你**不该开始**，先回去读对应 phase 文件：

1. 我现在要进入主循环的哪一步？（编号 1–13）
2. 上一步的产物落在 `story/runtime/` 哪个文件里？我读了吗？
3. 这一步的输出文件名是什么？要不要走 `apply_delta.py` 才能动真理文件？
4. 这一步内部有几个**确定性脚本闸门**（例如 step 5 的 `writer_parse` + `post_write_validate`；step 7 的 `ai_tell_scan` + `sensitive_scan` + `commitment_ledger`）？我都跑了吗？
5. 我准备跳过哪一步？为什么跳？（**默认禁止**——只有 [00-orchestration.md](references/phases/00-orchestration.md) "何时跳过主循环" 列出的四种情况允许；用户催"快点写"**不**是允许跳的理由）

**绝不跳的最小集**（任意一项缺失都会让真理文件污染或下章 Planner 喂料断流）：

- step 5b/5c：`writer_parse` + `post_write_validate`
- step 7：audit-revise 整轮闭环（即便单轮就过线也要落 `audit-r0.json`）
- step 7a：`ai_tell_scan` + `sensitive_scan` + `commitment_ledger`（**每一轮都跑**）
- step 9 + 10：Settler 主动 5 项（invariant #7）+ `apply_delta`
- step 10.1：`hook_governance --command promote-pass`
- step 11：`chapters/{NNNN}.md` + `chapter_index.py add`
- step 11.0a：`snapshot_state.py create`
- step 11.0b：`audit_drift.py write`（喂下章 Planner）
- step 11.0c：`docops_drift.py --write`（喂下章 Settler）
- step 11.05：Chapter Analyzer（写 stub 也要写）
- 任一阶段重试：必须把上次失败原因注入下次 prompt（invariant #5）

每个 phase 文件顶部都有一个 ⛔ **硬约束** 块——LLM 单读某个 phase md 时也必须先读它。

## 注意事项

- **照搬 inkos 系统 prompt**：每个 phase 文件里的"系统 prompt"块都从 inkos 源码搬来——整段照用，改 prompt 等于改风格基线。
- **不跳步**：详见 §流程红线；invariant #11 的 loop_state 是 advisory 进度跟踪，硬约束靠各脚本自己的 schema 校验（apply_delta / commitment_ledger / post_write_validate 等）兜底。用户催"快点写"也按主循环走。
- **失败如实回报**：到达重试上限仍失败，告诉用户哪里不通过，不伪造通过（invariant #4 / #5）。
- **保持中文**：面向作者和角色的文本都是中文，脚本日志可以英文。
- **不写 README**：用法直接看本 SKILL.md。
