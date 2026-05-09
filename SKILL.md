---
name: novel-writer
description: 中文网文 / 长篇连载 / 同人 / 风格模仿写作工作流（移植自 inkos CLI）。覆盖立项、写下一章、审改、伏笔治理（揭 1 埋 1 + payoff 定位）、文字打磨、卷级压缩。13 阶段流水线 Plan→Compose→(Architect)→Write→写后检→Normalize→Audit→Revise→Settle→(Polish)→Analyze→(Consolidate)，含 15 题材 profile、37 审计维度、9 类事实追踪、四级规则栈、滑窗记忆、3 阶段 Settler delta 解析、段落 40–120 字反碎片硬尺、去 AI 味与敏感词扫描。用户在 `books/<id>/` 下做任何写作动作、说"写第 N 章"/"起一本新书"/"审一下这章"/"我想写《X》同人"/"学这段风格再写"，都应主动触发——即使没明说"用 novel-writer"。**不触发**：一次性短文、产品文档、PRD、技术博客、诗词、剧本、学术论文。
---

# novel-writer

[inkos CLI](https://github.com/Narcooo/inkos) 多 agent 网文写作流水线的 Claude Code 移植。

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

### 工作目录解析（用户没指定 workdir 时）

`init_book.py` 的 `--workdir` 接受任意路径，但**用户没给路径**时不要默认拿当前 cwd——cwd 可能是用户的家目录、Desktop、或别的无关项目根，直接在那儿落 `inkos.json` + `books/` 会污染。规则：

1. **用户显式给了路径**（"在 `~/my-novels` 下起一本"、"用 `/tmp/test-book`"）→ 直接用，不质疑。
2. **用户没给路径，但 cwd 已经是写作工作区**（cwd 里有 `inkos.json`，或 cwd 路径名暗示写作意图，如 `novels` / `writing` / `novel-writer-workspace` 类）→ 用 cwd。
3. **用户没给路径，cwd 也不像写作工作区** → **默认在 cwd 下创建 `novel-writer-workspace/` 子目录**当 workdir：
   ```bash
   python {SKILL_ROOT}/scripts/init_book.py --workdir ./novel-writer-workspace ...
   ```
   先告诉用户："没指定目录，我会在当前位置 (`<cwd>`) 下建一个 `novel-writer-workspace/` 来放项目。也可以告诉我你想放哪。" 用户认可或沉默继续 → 创建；用户给了别的路径 → 切到 §1。
4. **绝不静默拿 cwd 顶层落 `inkos.json`**——除非命中规则 2。这是防呆，不是猜用户。

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

`--workdir` 取值规则见 §"工作目录解析"——**用户没显式给路径就传 `./novel-writer-workspace`**，不要省略让脚本回退到 cwd 默认。

脚本会落地：根目录 `inkos.json` + `books/<id>/{book.json, story/*, story/state/*, chapters/, story/runtime/, story/outline/, story/roles/...}`。

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
| 5b/5c | (脚本) | `writer_parse.py` 拆 sentinel + `post_write_validate.py` 写后检（见 [post-write-validation](references/post-write-validation.md)） |
| 06 | [observer](references/phases/06-observer.md) | 抽 9 类事实，输出 OBSERVATIONS 块 |
| 07 | [settler](references/phases/07-settler.md) | 产 RuntimeStateDelta JSON（apply_delta `--input-mode raw` 走 3 阶段 parser） |
| 08 | [normalizer](references/phases/08-normalizer.md) | 单次长度修正 |
| 09 | [auditor](references/phases/09-auditor.md) | 37 维审计（按题材 profile 过滤）+ 评分 |
| 10 | [reviser](references/phases/10-reviser.md) | 6 模式修订（auto/polish/rewrite/rework/anti-detect/spot-fix） |
| 11 | [polisher](references/phases/11-polisher.md) | audit 真正过线（≥88）后的文字层打磨，单 pass、不动情节 |
| 12 | [consolidator](references/phases/12-consolidator.md) | 卷级摘要压缩 + 历史归档（手动触发；先跑 `consolidate_check.py` 检测） |
| 13 | [chapter analyzer](references/phases/13-analyzer.md) | 章节落盘后的定性回顾，写 `analysis.json` 喂下章 Planner（单向只读，不改任何真理文件） |

**主循环关键不变量**：

1. 真理文件（`story/state/*.json`、`pending_hooks.md` 等）只能经 `scripts/apply_delta.py` 修改（避开 manifest 漂移与钩子治理失效）
2. 章节正文落盘前必须经过 audit-revise 闸门——即便没过线也要标 `audit-failed-best-effort`（保证问责可追溯）
3. 阶段产物先写到 `story/runtime/chapter-{NNNN}.<phase>.md`，最终成果才落到 `chapters/` 与 `story/state/`
4. LLM 输出解析失败：Planner 重试 ≤ 3，Architect ≤ 2（含 [Foundation Reviewer](references/foundation-reviewer.md) 回环），audit-revise 整轮 ≤ 3（上限来自 inkos 实证调参）
5. Reflector **不是**单独阶段；其职责并入 audit-revise loop
6. Writer 输出走 sentinel 格式 → `writer_parse.py` + `post_write_validate.py` 是 Normalize 之前的强制检查；critical 命中允许 Writer 重写一次
7. **每章 Settler 必须主动同步周边状态**：完成正文后，Settler 在产出 delta 前必须**逐项审视** `current_focus` / `character_matrix` / `emotional_arcs` / `subplot_board` / `story/roles/<slug>.md` 是否需更新；不允许把检测责任甩给下一章 [docops_drift](references/phases/00-orchestration.md) 扫描。详细清单见 [07-settler.md](references/phases/07-settler.md) "主动性铁律"
8. **任一阶段 LLM 输出解析 / 校验失败重跑时，prompt 必须显式注入"上一次失败的具体原因"**——schema 错位、缺哪个块、违反哪条规则、命中哪个治理 issue。不允许只重发原 prompt 让 LLM "再猜一次"。已落地形式：Planner（[02-planner.md](references/phases/02-planner.md) `MEMO_RETRY_LIMIT`）、Architect（Foundation Reviewer 回环把 issues 注回）、Writer post-write retry（`postWriteFeedback`）、Settler（`parserFeedback` / `governanceFeedback`）。新增阶段必须沿用此模式

## 单点指令（不进主循环）

**措辞模糊时先问一句再做**：用户说"改一下这段" / "我觉得这段不够自然" / "这段表达不太好"——既可能是整章 polish，也可能是定点修一两句。先用一句问句确认范围（"是想整章润一遍，还是只动你指出的那一小段？"）再决定走 phase 11 polisher / phase 10 polish 模式 / phase 10 spot-fix。**不要**默认整章改——半径选错了既浪费 token，又容易把作者满意的段落改坏。

| 用户大致这么说 | 做什么 |
|---|---|
| "审一下第 N 章" | 只跑 [phase 09](references/phases/09-auditor.md)；输出审计结果，不动正文 |
| "把 XX 句改成 YY" | [phase 10 reviser](references/phases/10-reviser.md) `spot-fix` 模式 |
| "整章 polish / 文字打磨一遍" | 直接跑 [phase 11 polisher](references/phases/11-polisher.md)（绕过 audit 借线规则） |
| "用 reviser polish 模式改" | phase 09 + 10 polish 模式（结构层修订，不动情节） |
| "AI 味太重，专项处理" | phase 10 `anti-detect` 模式，先跑 `scripts/ai_tell_scan.py` 拿证据 |
| "重做架构" | phase 04 architect 单跑 |
| "看下伏笔池压力 / 伏笔健康度" | `python scripts/hook_governance.py --book <bookDir> --command health-report` |
| "校验一下真理文件没问题吧" | `python scripts/hook_governance.py --book <bookDir> --command validate` + `python scripts/chapter_index.py --book <bookDir> validate` |
| "看下哪些章节待审 / 已通过 / review list" | `python scripts/chapter_index.py --book <bookDir> list --status ready-for-review`（或 `approved` / `audit-failed` 等） |
| "把第 N 章标记为 approved / 已发布" | `python scripts/chapter_index.py --book <bookDir> set-status --chapter N --status approved`（或 `published` / `rejected`） |
| "回滚到第 N 章那一刻 / 真理文件写脏了" | `python scripts/snapshot_state.py --book <bookDir> restore --chapter N [--dry-run]`（先 `list`/`diff`；详见 [state-snapshots.md](references/state-snapshots.md)） |
| "看下上章审计还有什么没改干净 / drift" | `python scripts/audit_drift.py --book <bookDir> read --json`（详见 [audit-drift.md](references/audit-drift.md)） |
| "看下伏笔账兑现了没 / commitment ledger" | `python scripts/commitment_ledger.py --memo <chapter_memo.md> --draft <draft.md>` |
| "卷尾兑现率 / cross-volume payoff" | `python scripts/hook_governance.py --book <bookDir> --command volume-payoff --volume N` |
| "看 audit 改了几轮 / stagnation" | `python scripts/audit_round_log.py --book <bookDir> --chapter N --analyze` |
| "上下文太长了 / context 超 budget" | `python scripts/context_budget.py --input <context_pkg.json> --budget-total 80000`（composer step 5 自动调；hard-overflow 时给用户决策） |
| "锁住正在写的书 / 防多 session 撞写" | `python scripts/book_lock.py --book <bookDir> --json {acquire/status/release}`（详见 [book-lock.md](references/book-lock.md)） |
| "压缩前面卷 / consolidate / 摘要太多了 / 历史压缩一下" | 先跑 `python scripts/consolidate_check.py --book <bookDir>` 看是否该压；该压则进 [phase 12 consolidator](references/phases/12-consolidator.md) |
| "列出我所有书 / book list" | `python scripts/book.py list` |
| "看下《XX》详情 / book show" | `python scripts/book.py show <bookId>` |
| "调整字数 / 改章数目标 / 改状态 / 改题材 / 改语言 / 改平台" | `python scripts/book.py update <bookId> [--chapter-words N] [--target-chapters N] [--status outlining\|active\|paused\|completed\|archived] [--genre <id>] [--lang zh\|en] [--platform tomato\|feilu\|qidian\|other] [--title "..."]`（原子写，至少给一项；改 genre 时若新 id 没 profile 会回退 other.md 并 warn） |
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
| "看下都有哪些题材 / list genres" | `python scripts/genre.py list [--json]` |
| "看 xianxia 题材 profile / show genre" | `python scripts/genre.py show <id> [--json]` |
| "新增自定义题材 / 复制 profile 改" | `python scripts/genre.py add <newId> --from <baseId> [--name "..."] [--out <path>]` |
| "校验题材 profile schema" | `python scripts/genre.py validate [<id>]` |
| "调整章节焦点 / 角色关系 / 风格 / outline / role 档案"（白名单指导 md） | 走 user-directive docOps 流程（见 §"用户指令式调整设定"）——LLM 构造最小 docOps delta → 写到 `story/runtime/user-directive.delta.json` → 调 `apply_delta.py` |
| "改 author_intent / book_rules / fanfic_canon / parent_canon"（作者宪法） | LLM 直接 `Edit` 对应文件后，调 `python scripts/apply_delta.py --book <bookDir> log-direct-edit --file <path> --reason "..."` 补审计日志；不走 `.bak` |
| "看哪些 md 被改过 / 谁改的" | `cat story/runtime/doc_changes.log` |
| "回滚某次 docOp" | `python scripts/apply_delta.py --book <bookDir> revert-doc-op --op-id <sha8>`（仅适用于走 docOps 落盘的；宪法直 Edit 用 `git checkout` 或手动恢复） |
| "扫一下指导 md 是不是写脏了 / docops drift" | `python scripts/docops_drift.py --book <bookDir> --window 6 [--write]`（advisory；候选喂下章 Settler） |

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

## 用户指令式调整设定

当作者在主对话里说"调整 X 设定"/"把主角性格改成 Y"/"修一下风格指引"时，**两条路径**：

### 路径 A：白名单指导 md（current_focus / style_guide / character_matrix / emotional_arcs / subplot_board / outline/* / roles/*）

**不直接 `Edit`**——走 docOps user-directive 通道，保证 anchor 解析 / 表结构 / `.bak` 备份 / 可回滚。流程：先 `Read` 对应 md 看清结构（H2 节名 / 表格列名），再写 `story/runtime/user-directive.delta.json`：

```json
{
  "chapter": <manifest.lastAppliedChapter, 缺省 0>,
  "docOps": {
    "currentFocus": [{
      "op": "replace_section",
      "anchor": "## Active Focus",
      "newContent": "...",
      "reason": "用户：把焦点从 X 切到 Y",     // ≤ 200 chars，引作者原话
      "sourcePhase": "user-directive",
      "sourceChapter": <lastAppliedChapter>
    }]
  }
}
```

然后 `python scripts/apply_delta.py --book <bookDir> --delta story/runtime/user-directive.delta.json --skip-hook-governance --skip-commitment-ledger --skip-book-metadata`，把 `docOpsApplied` 回执作者。

### 路径 B：作者宪法（author_intent / book_rules / fanfic_canon / parent_canon）

这四个文件**自动通道永远只读**（schema 阶段就拒），但作者明示指令时 LLM 可直接 `Edit`——作者主权例外。流程：

1. LLM 直接 `Edit` 目标文件（`book_rules` = `book.json#bookRules` 子树）
2. 调 helper 补审计日志（必走，写入 `story/runtime/doc_changes.log`，`opId` 自动 SHA8）：
   ```bash
   python scripts/apply_delta.py --book <bookDir> log-direct-edit \
       --file story/author_intent.md --reason "用户：把核心命题改成 X"
   ```
3. 不走 `.bak`——宪法变更频次低，靠 git 或手动备份兜底。`revert-doc-op` 对 direct_edit 不支持；回滚走 `git checkout`。
4. 把 helper 回执的 `opId` 告诉作者

### LLM 自己起意改任何 md → 必须先问作者

如果 LLM 觉得"这条焦点该推进了"但作者没明示，**先问作者**再决定走 A 还是 B（防止隐性设定漂移）。Settler / Architect 通过自动 docOps 改白名单 md 是合法的——因为有本章正文驱动；跳出主循环的"主动改"必须由作者授权。

## 真理文件契约

任何阶段需要更新真理文件，**必须**通过：

```bash
# Settler 直接给的原始输出（含 === POST_SETTLEMENT === / === RUNTIME_STATE_DELTA === sentinel）
python {SKILL_ROOT}/scripts/apply_delta.py --book <bookDir> --delta <settler.raw.md> --input-mode raw

# 或：已经清洗好的 JSON
python {SKILL_ROOT}/scripts/apply_delta.py --book <bookDir> --delta <runtime/chapter-NNNN.delta.json>
```

3 阶段 parser：(1) lenient 提 RUNTIME_STATE_DELTA 块；(2) soft-fix（key alias / 类型 coercion / 数组 wrap，详见 [schemas/runtime-state-delta.md](references/schemas/runtime-state-delta.md) §1b）；(3) 严格 schema 校验。原子写入，按字段路由，**自动调用** `hook_governance.py` validate + stale-scan 闸门。

**apply_delta 不写 `chapters/index.json`**——那是章节运营索引（status / auditIssues / wordCount），由 orchestration step 11 单独调 [`chapter_index.py`](references/schemas/chapter-index.md)。

行为约定：
- 解析失败 → 返回 `parserFeedback` 喂回 Settler，不 crash
- `validate` critical → exit 1 + `hookGovernanceBlocked: true`，Settler 重写
- `stale-scan` → 不阻断，写回 `stale: true` 供后续 Planner 参考
- 调试不写盘：`scripts/settler_parse.py --input <raw.md> --mode raw --out /tmp/d.json`

**原子回滚硬承诺**：3 阶段 parser（lenient → soft-fix → strict schema）+ 字段路由的任一阶段失败 → **整批 delta 全部不应用**，已写出的临时改动必须回滚（实现侧靠"先暂存到 staging，全过才 swap 到真理文件"）。绝不允许"hookOps 写进去了 / docOps 写一半 schema 又拒"的半提交。Settler 重跑相同 delta 应是**幂等**——若上次失败已被完整回滚，重跑等同于第一次跑；若上次成功，重跑必须检测出"已应用"并 no-op（看 `manifest.lastAppliedChapter` + `appliedDeltaHash`），不重复推进 hookOps 的 lastAdvancedChapter / 不重复 append cliffhangerEntry。

钩子治理逻辑见 [references/hook-governance.md](references/hook-governance.md)。直写 `story/state/*.json` 视为脏写——见主循环不变量 #1。

### 伏笔 / hook / cliffhanger 术语表

避免散落在多 phase 文档里的同义词混淆，以本表为准：

| 术语 | 含义 | 来源 / 存放 | 谁更新 |
|---|---|---|---|
| **伏笔（hook）** | 一条延续到后续章节、有具体回收方向的未解叙事承诺 | `story/pending_hooks.md`（人读）+ `story/state/hooks.json`（机读，权威） | Settler 走 `apply_delta` 落 hookOps |
| **hookCandidate** | 本章新出现、尚未拿到正式 hookId 的候选 | Settler delta 的 `newHookCandidates` 字段 | Settler 产候选；`hook_governance promote-pass` 仲裁推升为正式 hook |
| **payoffTiming** | hook 的**语义节奏档位**（不是具体章号）：`immediate` / `near-term` / `mid-arc` / `slow-burn` / `endgame`。**禁止**写章号 | hooks.json 字段 | Settler 在 hookOps.upsert 里写；Architect 大改时重置 |
| **committedToChapter** | 给某条 hook 强绑定的**最迟兑现章号**（实指承诺） | hooks.json 字段（可选） | Planner 写 `## 本章 hook 账` 时声明；commitment_ledger 校验本章是否兑现 |
| **cliffhanger** | 章末勾子（章末最后一段的收尾形态），12 类枚举 + intensity 1-5 | `story/state/cliffhanger_history.json` | Settler 必输出 `cliffhangerEntry`；Planner 读最近 6 条防套路重复 |
| **foreshadow / 揭 1 埋 1** | 写作动作（"每章揭 1 旧 hook + 埋 1 新 hook"的节奏目标）；不是数据字段 | Planner / Auditor 维度内部 | 不直接落盘，体现在 hookActivity 字段 |

**简言之**：伏笔 = hook（同一概念中英对照）；hooks.json 是权威，pending_hooks.md 是人读视图；payoffTiming 是档位不是章号；cliffhanger 是章末写法分类，与 hook 是不同维度——一个 cliffhanger 章可以同时埋 / 揭多条 hook。

## 规则栈与题材 profile

四级覆盖：L1 题材 → L2 全书 → L3 章节 → L4 runtime（Planner 当前指令）。详见 [references/rule-stack.md](references/rule-stack.md)。Writer 全读，Reviser 守 L1+L2+L3，Auditor 强制 L1 / 警告 L2 / 软引导 L3。

**L1 题材的具体形状**来自 `templates/genres/<book.genre>.md`——15 个内置 profile（仙侠、玄幻、都市、科幻、异世界、塔爬、地牢核、修仙、进展流、惊悚、温馨、罗曼塔、LitRPG、系统末日、其他）。每个 profile 含：fatigueWords（注入 Writer 的反 AI 词表）、chapterTypes、satisfactionTypes、pacingRule、auditDimensions（限定 Auditor 的 37 维子集）、numericalSystem/powerScaling/eraResearch 三个 toggle。题材 id 不在 catalog 内会回退 `other.md`。详见 [references/genre-profile.md](references/genre-profile.md)。

### 题材 catalog 管理

用 [`scripts/genre.py`](scripts/genre.py)（`list / show / add / validate`）。查找顺序：`<workdir>/genres/<id>.md`（user override，可选）→ `{SKILL_ROOT}/templates/genres/<id>.md`（15 内置）。

加自定义题材：`python scripts/genre.py add wuxia --from xianxia --name 武侠`，落地骨架到 `<workdir>/genres/wuxia.md`，作者手 edit 内容；`book.json#genre` 写新 id 即生效。

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
├── SKILL.md                  ← 你正在读
├── references/
│   ├── phases/00-13-*.md     14 个阶段（编排 + radar + 10 agent + polisher + consolidator + analyzer）
│   ├── branches/{fanfic,style}.md
│   ├── schemas/              6 个数据形状（含 chapter-index, audit-result, cadence-policy 等）
│   └── *.md                  rule-stack / genre-profile / audit-dimensions / ai-tells / sensitive-words
│                             / hook-governance / memory-retrieval / foundation-reviewer / post-write-validation
│                             / long-span-fatigue / pov-filter / cadence-policy / chapter-recovery
│                             / state-projections / narrative-control / writing-methodology / state-snapshots
│                             / audit-drift / context-budget / book-lock / role-template / truth-files
├── templates/
│   ├── inkos.json + book.json     元数据种子
│   ├── story/{*.md, state/*.json} 真理文件种子
│   └── genres/                    15 题材 profile（init 时按 --genre 选用）
├── scripts/                  36 个 Python 工具脚本
└── evals/evals.json          SKILL 自身的 7 个测试 prompt
```

脚本入口去"单点指令"表查；阶段内细节去对应 phase 文件查。

## 流程红线（不跳步硬尺）

进入主循环前先回答以下 5 个问题——任何一个答"不知道"都说明你**不该开始**，先回去读对应 phase 文件：

1. 我现在要进入主循环的哪一步？（编号 1–13）
2. 上一步的产物落在 `story/runtime/` 哪个文件里？我读了吗？
3. 这一步的输出文件名是什么？要不要走 `apply_delta.py` 才能动真理文件？
4. 这一步内部有几个**确定性脚本闸门**（例如 step 5 的 `writer_parse` + `post_write_validate`；step 7 的 `ai_tell_scan` + `sensitive_scan` + `commitment_ledger`）？我都跑了吗？
5. 我准备跳过哪一步？为什么跳？（**默认禁止**——只有 [00-orchestration.md](references/phases/00-orchestration.md) "何时跳过主循环" 列出的四种情况允许；用户催"快点写"**不**是允许跳的理由）

**绝不跳的最小集**（任意一项缺失都会让真理文件污染或下章 Planner 喂料断流）：

- step 5b/5c：`writer_parse` + `post_write_validate`（Writer 落盘后的 sentinel 拆分 + 机械层闸门）
- step 7：audit-revise 整轮闭环（即便单轮就过线也要落 `audit-r0.json`）
- step 7a：`ai_tell_scan` + `sensitive_scan` + `commitment_ledger`（**每一轮都跑**，不是只第一轮）
- step 9 + 10：Settler 主动 5 项 + `apply_delta`（直接编辑 `story/state/*.json` = 脏写）
- step 10.1：`hook_governance --command promote-pass`（让本章新 hook 在下章 Composer 看到前过门槛）
- step 11：`chapters/{NNNN}.md` + `chapter_index.py add`（任一缺失都让 `inkos review list` 看不见这章）
- step 11.0a：`snapshot_state.py create`（回滚兜底；不喂下章但回不去就是回不去）
- step 11.0b：`audit_drift.py write`（喂下章 **Planner**，承接本章未改干净的 critical/warning）
- step 11.0c：`docops_drift.py --write`（喂下章 **Settler**，建议性的指导 md 漂移候选）
- step 11.05：Chapter Analyzer（写 stub 也要写，**不允许**直接不调用）
- 任一阶段重试：必须把上次失败原因（schema 错位 / 治理 issue / parser feedback）注入下次 prompt——**不允许**只重发原 prompt 让 LLM 再猜（详见主循环不变量 #8）

每个 phase 文件顶部都有一个 ⛔ **硬约束** 块——LLM 单读某个 phase md 时也必须先读它。orchestration 不变量与每个 phase 顶部硬约束块互为冗余，谁丢都不行。

## 注意事项

- **照搬 inkos 系统 prompt**：每个 phase 文件里的"系统 prompt"块都从 inkos 源码搬来——整段照用，改 prompt 等于改风格基线。
- **不跳步**：详见 §流程红线。用户催"快点写"也按主循环走，跳过 audit/observer 会让真理文件越写越脏，后面无法继续创作。
- **失败如实回报**：Planner / Architect / audit-revise 都内置重试上限；到达上限仍失败，告诉用户哪里不通过，不伪造通过。
- **保持中文**：面向作者和角色的文本都是中文，脚本日志可以英文。
- **不写 README**：用法直接看本 SKILL.md。
