---
name: novel-writer
description: 中文网文写作工作流（移植自 inkos CLI）。当用户要写小说、网文、同人或长篇虚构故事时使用，覆盖项目初始化、写下一章、审章/改章、风格模仿、同人 canon/AU/OOC/CP 设置等场景。流水线 Plan → Compose → (Architect) → Write → Normalize → Audit → Revise → Observe → Settle，含 37 个审计维度、9 类事实追踪、四级规则栈、去 AI 味与敏感词扫描。即使用户没明说"用 novel-writer"，只要意图是写网文章节、做长篇连载、做同人创作或做风格模仿写作，都应主动用此 skill。**不要触发**于一次性短文、产品文档、PRD、技术博客、诗词歌赋、剧本/编剧、学术论文这类非长篇虚构连载的场景。
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
Plan → Compose → (首章/卷尾才 Architect) → Write
     → Normalize（脚本 + 必要时 phase 08）
     → Audit-Revise（最多 3 轮，分数提升 < 3 即退出）
     → Observe → Settle → apply_delta 校验落盘
     → 章节正文写入 chapters/{NNNN}.md
```

每个阶段读对应文件，**先看那个 phase 文件再动手**：

| Phase | 文件 | 摘要 |
|---|---|---|
| 02 | [planner](references/phases/02-planner.md) | 生成 chapter_memo（YAML+md），定义本章要兑现什么、不做什么 |
| 03 | [composer](references/phases/03-composer.md) | 装配 context_pkg + rule_stack（无 LLM） |
| 04 | [architect](references/phases/04-architect.md) | 散文密度的基础设定（首章 / 卷切换才跑） |
| 05 | [writer](references/phases/05-writer.md) | 写正文，13-14 段 prompt 模块化拼装 |
| 06 | [observer](references/phases/06-observer.md) | 抽 9 类事实，输出 OBSERVATIONS 块 |
| 07 | [settler](references/phases/07-settler.md) | 产 RuntimeStateDelta JSON |
| 08 | [normalizer](references/phases/08-normalizer.md) | 单次长度修正 |
| 09 | [auditor](references/phases/09-auditor.md) | 37 维审计 + 评分 |
| 10 | [reviser](references/phases/10-reviser.md) | 6 模式修订（auto/polish/rewrite/rework/anti-detect/spot-fix） |

**主循环关键不变量**（违反就停下）：

1. 真理文件（`story/state/*.json`、`pending_hooks.md` 等）只能经 `scripts/apply_delta.py` 修改，不直接编辑
2. 章节正文落盘前必须经过 audit-revise 闸门——即便没过线也要标 `audit-failed-best-effort`
3. 阶段产物先写到 `story/runtime/chapter-{NNNN}.<phase>.md`，最终成果才落到 `chapters/` 与 `story/state/`
4. LLM 输出解析失败：Planner 重试 ≤ 3，Architect ≤ 2，audit-revise 整轮 ≤ 3
5. Reflector **不是**单独阶段；其职责并入 audit-revise loop

## 单点指令（不进主循环）

| 用户大致这么说 | 做什么 |
|---|---|
| "审一下第 N 章" | 只跑 [phase 09](references/phases/09-auditor.md)；输出审计结果，不动正文 |
| "把 XX 句改成 YY" | [phase 10 reviser](references/phases/10-reviser.md) `spot-fix` 模式 |
| "整章 polish" | phase 09 + 10 polish 模式（不动情节） |
| "AI 味太重，专项处理" | phase 10 `anti-detect` 模式，先跑 `scripts/ai_tell_scan.py` 拿证据 |
| "重做架构" | phase 04 architect 单跑 |
| "看一下当前进度" | 读 `story/state/manifest.json` + `chapter_summaries.json`，直接答 |

## 同人 / 风格分支

- **同人** ([references/branches/fanfic.md](references/branches/fanfic.md))：4 模式 canon/au/ooc/cp，每模式三件套——Writer preamble、self-check 注入、Auditor severity 调整。canon 抽取走 `fanfic_canon_importer` prompt（5 SECTION 输出）。
- **风格** ([references/branches/style.md](references/branches/style.md))：纯文本统计走 `scripts/style_analyze.py`，定性分析由 Claude 跑 LLM prompt。两者输出的 `style_profile.json` + `style_guide.md` 一起注入 Writer。

## 质量控制（确定性闸门）

audit 之前必跑这两个脚本。把它们的 issue 列表合并到 audit issues 一起评估：

```bash
python {SKILL_ROOT}/scripts/ai_tell_scan.py --file <draft.md>
python {SKILL_ROOT}/scripts/sensitive_scan.py --file <draft.md>
```

- `ai_tell_scan` 命中 critical（如出现"核心动机"等推理框架术语在正文里）→ 必须改
- `sensitive_scan` 政治词命中（severity=block）→ 必须删
- 性 / 极端暴力词（severity=warn）→ 标记给作者，不强删

参考词表与阈值见：
- [references/ai-tells.md](references/ai-tells.md)
- [references/sensitive-words.md](references/sensitive-words.md)

## 真理文件契约

任何阶段需要更新真理文件，**必须**通过：

```bash
python {SKILL_ROOT}/scripts/apply_delta.py --book <bookDir> --delta <runtime/chapter-NNNN.delta.json>
```

脚本会做 schema 校验（拒绝不合规 JSON），原子写入（`.tmp` + rename），并按字段路由到对应文件。schema 详见 [references/schemas/runtime-state-delta.md](references/schemas/runtime-state-delta.md)。

直接编辑 `story/state/*.json` 视为脏写，会污染 manifest，**禁止**。

## 规则栈

四级覆盖：L1 题材 → L2 全书 → L3 章节 → L4 runtime（Planner 当前指令）。详见 [references/rule-stack.md](references/rule-stack.md)。Writer 全读，Reviser 守 L1+L2+L3，Auditor 强制 L1 / 警告 L2 / 软引导 L3。

## 文件树速查

```
{SKILL_ROOT}/
├── SKILL.md                     ← 你正在读
├── references/
│   ├── phases/00-10-*.md        各阶段 prompt 与契约
│   ├── branches/{fanfic,style}.md
│   ├── rule-stack.md            四级规则栈
│   ├── audit-dimensions.md      37 维度全表
│   ├── ai-tells.md              去 AI 味词表与阈值
│   ├── sensitive-words.md       三级敏感词
│   └── schemas/                 4 个数据形状
├── templates/                   init 用的种子文件
├── scripts/                     6 个 Python 脚本
└── evals/evals.json             SKILL 自身的 7 个测试 prompt
```

## 注意事项

- **不要凭印象编 prompt**：每个 phase 文件里的"系统 prompt"块都搬自 inkos 源码，请整段照用，不要改写。改 prompt 等于改风格基线。
- **流程长但不要跳步**：哪怕用户催"快点写"，也要按主循环走——跳过 audit/observer 会让真理文件越写越脏，后面无法继续创作。
- **首次失败不慌**：Planner / Architect / audit-revise 都内置重试上限。到达上限仍失败，老老实实告诉用户哪里不通过，不要伪造通过。
- **保持中文**：所有面向作者和角色的文本都是中文。脚本日志可以英文。
- **不写 README**：本 SKILL 内不带 README；用法直接看本 SKILL.md 即可。
