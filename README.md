# novel-writer

把 [inkos CLI](https://github.com/Narcooo/inkos) 的多 agent 中文网文写作流水线，移植成可在 [Claude Code](https://docs.claude.com/en/docs/claude-code) 里直接驱动的 SKILL 包。

不是 inkos 的 fork，也不是套壳——是把 inkos 里 34 个 agent / 9 个 pipeline / 35 个 utility / 27 个 CLI 命令的核心能力重写成 Claude 能直接调度的 phase 文件 + Python 脚本，跑在 Claude Code 一个 session 里，不需要 Node、不需要 LLM provider 配置、不需要 SQLite。

**当前规模**：14 phase 文档（含编排）+ 22 篇 references 设定 / 算法 / 治理文档 + 7 份数据契约 schema + 41 个 Python 脚本（标准库，跑 `doctor.py` 自检 45 ok / 0 fail）+ 15 题材 profile。

---

## 快速开始

### 1. 装到 Claude Code

```bash
# 方式 A：直接 cp 到 SKILL 目录
cp -r /path/to/this/repo ~/.claude/skills/novel-writer

# 方式 B：在你的项目目录里做软链
ln -s /path/to/this/repo ~/.claude/skills/novel-writer
```

不需要 `pip install`——所有脚本都用标准库。

### 2. 启动

在 Claude Code 里打开任意目录，对 Claude 说：

> 起一本仙侠新书，叫《青云志》，目标 200 章，番茄平台

Claude 会先确认工作目录：
- 你**给了路径**（"放在 `~/my-novels` 下" / "用 `./drafts`"）→ 用你给的
- 你**没给且 cwd 已经是写作工作区**（cwd 里已有 `inkos.json`，或路径名是 `novels` / `writing` / `novel-writer-workspace`）→ 直接用 cwd
- 你**没给且 cwd 看起来无关**（家目录、Desktop、别的项目根）→ **默认在 cwd 下建 `novel-writer-workspace/`** 当工作目录，先告诉你位置再落

然后调 `init_book.py` 落地完整目录结构，给你填一下 `author_intent.md` / `current_focus.md`，就能：

> 写第 1 章

主循环（Plan → Compose → Write → Audit → Revise → Settle → Polish）会自动跑。

### 3. 单点指令也能用

```
"审一下第 5 章" → 只跑 Auditor
"看下伏笔健康度" → hook_governance.py health-report
"上次写到一半崩了" → recover_chapter.py
"导出 epub" → export_book.py --format epub
```

完整路由表见 [SKILL.md](SKILL.md) 的"单点指令"小节。

---

## 常用配方

下面是几个高频场景，复制场景 1-2 行说出意图给 Claude 即可，不用记脚本名。

### 配方 1：从零写一本网文（带 brief）

适合心里已经有想法、不想反复打磨 author_intent 的场景。

```
我要写一本仙侠+穿越，主角现代医学博士穿到修真界，靠现代知识破病机；
卷一目标：从凡人到金丹；卷二：宗门权斗。叫《青囊志》，目标 150 章。
```

Claude 会：
1. 解析工作目录（见 §"启动"3 条规则）；没给路径就在 cwd 下建 `novel-writer-workspace/`
2. 调 `init_book.py --workdir <解析后的路径> --brief <你给的那段> --title 青囊志 --genre xianxia`，把 brief 落到 `story/brief.md` + 灌进 `author_intent.md`
3. 立即进 Architect（`nextStep="architect"`），生成 `story_frame.md` + `volume_map.md` + `roles/` + `book_rules.md` + `pending_hooks.md` + `chapter_summaries.json` 初始结构
4. 拍 `snapshots/0000/` 立项原点
5. 提示你"基础架构已生成，可以让我写第 1 章"

第 1 章会自动按"黄金开场"特殊处理。

### 配方 2：续写已有的书（半成品 / 上次没写完）

```
继续写《青囊志》第 12 章
# 或：上次写到一半崩了，从哪续
```

Claude 会：
1. 跑 `recover_chapter.py --book books/青囊志` 看 `runtime/` 残留物，识别上次卡在哪个 phase
2. 按推荐续接点恢复主循环
3. 写第 12 章

### 配方 3：写《XX》同人

```
我想写《盗墓笔记》的同人，瓶邪向，AU 模式，主角不死
```

Claude 会：
1. 调 `init_book.py --fanfic-mode au` 立项
2. 进 [`branches/fanfic.md`](references/branches/fanfic.md) 走 canon importer，让你贴原作设定
3. 抽 `fanfic_canon.md`（5 SECTION：核心设定 / 角色基线 / 关键事件 / 风格基调 / 禁碰红线）
4. 之后按主循环写，Writer/Auditor 自动注入 fanfic 模式约束

支持 4 模式：`canon`（贴原作）/ `au`（架空）/ `ooc`（性格魔改）/ `cp`（cp 向重塑）。

### 配方 4：学某作者风格再写

```
我想学一下天蚕土豆的风格，这是他《斗破》的几章 [贴原文]，
然后用这个风格写我的玄幻新书
```

Claude 会：
1. 进 [`branches/style.md`](references/branches/style.md)
2. 调 `style_analyze.py` 算 5 项纯文本统计（句长、段长、词汇丰富度、修辞密度、节奏特征）
3. 跑 LLM 定性分析（开篇钩、对话密度、世界观铺陈方式等）
4. 落 `story/style_profile.json` + `story/style_guide.md`
5. 之后写正文时 Writer 会注入这两个文件作为风格基线

### 配方 5：长篇连载的章节运营

写到 60 章后，定期做：

```
看下伏笔健康度       → hook_governance.py health-report
看下哪些章节待审      → chapter_index.py list --status ready-for-review
压缩一下前面卷       → consolidate_check.py 看是否值得；点头则进 phase 12
看下跨章疲劳         → fatigue_scan.py（advisory）
查节奏压力          → cadence_check.py（推荐下章 chapterType + satisfactionType）
```

也可以让 Claude 自己判断要不要做这些（"看一下整体进度"会触发 status + analytics + hook health 综合报告）。

### 配方 6：章节出问题了

```
第 7 章 audit 没过线
# 或：第 7 章重写
# 或：把第 7 章里"他怒视对方"那句改成 [新句]
```

Claude 会按场景路由：
- audit fail → phase 10 reviser 6 模式之一（auto/polish/rewrite/rework/anti-detect/spot-fix）
- spot 替换 → spot-fix 模式 + `spot_fix_patches.py`
- 重写 → rework 模式

### 配方 7：写脏了真理文件想回滚

```
回滚到第 5 章那一刻 / 真理文件写脏了
```

Claude 会：
1. `snapshot_state.py list` 看可恢复的快照
2. `snapshot_state.py diff --from <现在> --to 5` 给你看差异
3. 你确认后 `snapshot_state.py restore --chapter 5`，原子覆盖
4. 删除 5 之后的 chapters/{NNNN}.md 和 chapter_index 条目

snapshot 在每章落盘后自动产，立项时也有 `snapshots/0000/` 原点。

### 配方 8：导出

```
导出 epub
# 或：txt 导出第 1-30 章
```

调 `export_book.py --format epub|md|txt [--from-chapter N] [--to-chapter M] [--include-summary]`。txt/md 几 KB 即出，epub 是合规 EPUB 3 包。

---

## 架构

主循环 14 个 phase，每个 phase 一个 markdown 文件描述「何时进入 / Inputs / Process / Output contract / Failure handling」，Claude 在跑到对应阶段时严格按 phase 文件操作。

```
┌─ 02 Planner ──────── 生成 chapter_memo（YAML+md），定义本章兑现什么
├─ 03 Composer ─────── 装配 context_pkg + rule_stack（含 memory_retrieve 滑窗）
├─ 04 Architect ────── 散文密度的基础设定（首章/卷尾才跑；含 Foundation Reviewer 闸门）
├─ 05 Writer ───────── 13-14 段 prompt 模块化拼装 + 题材 profile 注入 + sentinel 输出
├─ 5b/5c 写后检 ────── writer_parse + post_write_validate（机械错检测）
├─ 06 Observer ─────── 抽 9 类事实
├─ 07 Settler ──────── 产 RuntimeStateDelta JSON
├─ 08 Normalizer ───── 单次长度修正
├─ 09 Auditor ──────── 37 维审计（按题材 profile 过滤）+ 评分
├─ 10 Reviser ──────── 6 模式修订（auto/polish/rewrite/rework/anti-detect/spot-fix）
├─ 11 Polisher ─────── audit 真正过线（≥88）后的文字层打磨
├─ 12 Consolidator ─── 卷级摘要压缩 + 历史归档（手动触发）
└─ 13 Chapter Analyzer 章节定性回顾，喂下章 Planner（单向只读）
```

详细伪代码见 [references/phases/00-orchestration.md](references/phases/00-orchestration.md)。

---

## 已移植的能力

### LLM agent / phase（13 个）

| inkos 来源 | 本仓库实现 |
|---|---|
| planner | [phase 02](references/phases/02-planner.md) |
| composer | [phase 03](references/phases/03-composer.md) |
| architect + foundation-reviewer | [phase 04](references/phases/04-architect.md) + [foundation-reviewer.md](references/foundation-reviewer.md) |
| writer + writer-prompts + writer-parser | [phase 05](references/phases/05-writer.md) + [post-write-validation](references/post-write-validation.md) |
| observer | [phase 06](references/phases/06-observer.md) |
| settler + settler-parser + settler-delta-parser | [phase 07](references/phases/07-settler.md) + 3 阶段 parser |
| length-normalizer | [phase 08](references/phases/08-normalizer.md) |
| auditor + continuity (37 维) | [phase 09](references/phases/09-auditor.md) + [audit-dimensions.md](references/audit-dimensions.md) |
| reviser (6 模式) | [phase 10](references/phases/10-reviser.md) |
| polisher | [phase 11](references/phases/11-polisher.md) |
| consolidator | [phase 12](references/phases/12-consolidator.md) |
| chapter-analyzer | [phase 13](references/phases/13-analyzer.md) |
| radar | [phase 01](references/phases/01-radar.md)（简化为人工输入） |
| fanfic（4 模式） | [branches/fanfic.md](references/branches/fanfic.md) |
| style-analyzer | [branches/style.md](references/branches/style.md) |

### 确定性脚本（41 个公开 + 4 个内部 helper）

| 类别 | 脚本 |
|---|---|
| **项目管理** | `init_book.py` `book.py`（list/show/rename/delete/copy）`book_lock.py`（多 session 撞写防护）`genre.py`（题材 catalog 管理） |
| **真理文件** | `apply_delta.py`（含 3 阶段 parser + 仲裁 + 治理 + 原子回滚）`settler_parse.py` `doc_ops.py`（指导 md docOps 应用）`hook_arbitrate.py` `hook_governance.py` `role_arbitrate.py`（角色候选仲裁）`docops_drift.py`（指导 md 漂移扫描）`repair_doc_md.py`（受损 md 修复）`snapshot_state.py`（快照 + 回滚）|
| **上下文** | `memory_retrieve.py`（滑窗）`context_filter.py`（降噪）`context_budget.py`（token 预算）`state_project.py`（视图投影）`pov_filter.py` |
| **写前 / 写后检** | `writer_parse.py`（sentinel 拆分）`post_write_validate.py`（机械错） |
| **质量控制** | `ai_tell_scan.py`（去 AI 味）`sensitive_scan.py`（敏感词）`fatigue_scan.py`（跨章疲劳）`word_count.py`（LengthSpec）`commitment_ledger.py`（hook 账兑现校验） |
| **写作辅助** | `narrative_control.py`（实体剥离 + 软化）`writing_methodology.py`（方法论 prompt）`spot_fix_patches.py`（line-anchored patch） |
| **章节运营** | `split_chapter.py`（超长拆分）`recover_chapter.py`（断点续跑）`cadence_check.py`（节奏压力）`chapter_index.py`（章节运营索引）`audit_drift.py`（审计纠偏喂料）`audit_round_log.py`（audit-revise 跨轮分析） |
| **诊断** | `status.py` `doctor.py`（self-test + book 子树校验）`analytics.py` `e2e_test.py`（端到端冒烟） |
| **风格 / 出版** | `style_analyze.py`（5 项纯文本统计）`consolidate_check.py`（卷压缩判定）`export_book.py`（txt/md/epub） |

内部 helper（`_chapter_files.py` / `_constants.py` / `_schema.py` / `_summary.py`）不直接用，由上面脚本 import。

### 题材 profile（15 个）

`templates/genres/`：cozy / cultivation / dungeon-core / horror / isekai / litrpg / other / progression / romantasy / sci-fi / system-apocalypse / tower-climber / urban / xianxia / xuanhuan。

每个 profile 含：fatigueWords / chapterTypes / satisfactionTypes / pacingRule / auditDimensions（限定 Auditor 的 37 维子集）/ numericalSystem / powerScaling / eraResearch 三个 toggle。

### 数据契约 schema（7 个）

- [chapter-memo.md](references/schemas/chapter-memo.md)：Planner 产出（YAML+md）
- [runtime-state-delta.md](references/schemas/runtime-state-delta.md)：Settler 产出（含 3 阶段 parser 文档 + docOps / hookOps / cliffhangerEntry / emotionalArcOps 等子 schema）
- [audit-result.md](references/schemas/audit-result.md)：Auditor 产出 + 单轮 audit-r{i}.json artifact 契约
- [chapter-index.md](references/schemas/chapter-index.md)：章节运营索引（status / auditIssues / wordCount / auditRoundAnalysis）
- [cadence-policy.md](references/schemas/cadence-policy.md)：节奏压力 / chapterType 推荐
- [migration-log.md](references/schemas/migration-log.md)：schema 迁移记录
- [style-profile.md](references/schemas/style-profile.md)：style 分支产出

---

## 与 inkos 的差异

| 方面 | inkos | novel-writer |
|---|---|---|
| 运行环境 | Node CLI 进程 | Claude Code 一个 session |
| 调度器 | `runner.ts` 程序化 | Claude 读 phase 文件按伪代码执行 |
| LLM provider | OpenAI / 自托管 / 多家 | 固定 Claude（用 Claude Code 自带） |
| 真理文件存储 | SQLite memory.db + markdown | 仅 markdown / JSON（Claude 直接读） |
| Memory 检索 | SQLite FTS + 嵌入相关性 | Python 滑窗脚本（角色/钩子重叠筛选） |
| 平台 Radar | 实时抓取榜单 | 人工贴数据让 Claude 分析 |
| 部署形态 | `npm install -g` | `cp -r` 到 `~/.claude/skills/` |
| 自动定时写章 | `inkos up/down` daemon | 不做（Claude 是交互式） |
| Web UI / TUI | `inkos studio` / `inkos` | 不做（Claude Code 就是 UI） |
| 通知 | Telegram / 飞书 | 不做 |

---

## 不实现（明确忽略）

下列 inkos 能力**不计划移植**，列在这里以免误以为是 TODO：

- **AIGC 外部检测器**（`detector` / `detection-insights` / `detection-runner`）——需配置 GPTZero / Originality 等 API key，SKILL 形态意义不大
- **`import chapters`**——用 LLM 反向给已有作品生成 truth 文件，工作量大且使用频次低
- **`review list/approve/reject`**——人工审核队列工作流，Claude Code 交互形态下用对话直接做更顺
- **English variant prompts**（`en-prompt-sections`）——全套英文 Writer/Planner/Architect prompt，目前只支持中文写作
- **Daemon / Studio Web UI / TUI dashboard**——见上表，runtime 形态不同
- **Telegram / 飞书通知**——同上
- **多 LLM provider 配置**——SKILL 形态固定 Claude
- **`inkos update`**——`cp -r` / `git pull` 即可
- **`inkos agent / interact`**——Claude Code 本体就是这个

---

## 目录布局

```
novel-writer/
├── SKILL.md                     ← Claude 加载这个文件作为 SKILL 入口
├── README.md                    ← 你正在读
├── references/                  ← 各阶段 prompt 与契约（22 篇）
│   ├── phases/00-13-*.md        ← 14 个 phase（编排 + radar + 10 agent + polisher + consolidator + analyzer）
│   ├── branches/{fanfic,style}.md
│   ├── schemas/                 ← 7 个数据形状（chapter-memo / runtime-state-delta / audit-result /
│   │                               chapter-index / cadence-policy / migration-log / style-profile）
│   ├── genre-profile.md         ← 15 题材 schema + 注入
│   ├── audit-dimensions.md      ← 37 维度全表
│   ├── audit-drift.md           ← 审计纠偏喂下章 Planner
│   ├── ai-tells.md / sensitive-words.md
│   ├── hook-governance.md       ← 伏笔生命周期 + 治理闸门
│   ├── memory-retrieval.md      ← 滑窗算法
│   ├── foundation-reviewer.md   ← Architect 闸门
│   ├── post-write-validation.md ← Writer 落盘后机械错检测
│   ├── long-span-fatigue.md / pov-filter.md / cadence-policy.md
│   ├── chapter-recovery.md / state-projections.md / state-snapshots.md
│   ├── context-budget.md / book-lock.md / role-template.md / truth-files.md
│   ├── narrative-control.md / writing-methodology.md
│   └── rule-stack.md            ← L1-L4 四级覆盖
├── templates/                   ← init 用的种子文件
│   ├── inkos.json + book.json
│   ├── story/{*.md, state/*.json, roles/}
│   └── genres/                  ← 15 题材 profile
├── scripts/                     ← 41 个公开 Python 脚本 + 4 个 _ helper（标准库）
└── evals/evals.json             ← SKILL 自身的测试 prompt
```

用户写的书会落到工作目录的 `books/<id>/` 下，**不进本仓库**——`.gitignore` 已排除。工作目录解析规则见 [SKILL.md §"工作目录解析"](SKILL.md)。

---

## 开发与扩展

### 自检

```bash
python3 scripts/doctor.py
```

会跑 Python 版本 / SKILL 根布局 / 模板与题材完整性 / 41 个公开脚本 `--help` 自检 / 16 步 e2e 冒烟链。当前状态：**45 ok / 0 warning / 0 fail**。

### 加自定义题材

复制 `templates/genres/other.md` 到新 id，按 YAML 头改字段。`book.json#genre` 设成新 id 即可生效。详见 [references/genre-profile.md](references/genre-profile.md)。

### 加新 phase

1. 在 `references/phases/` 加一个 `<NN>-<name>.md` 跟标准模板（何时进入 / Inputs / Process / Output contract / Failure handling / 注意事项）
2. 在 `references/phases/00-orchestration.md` 写进伪代码
3. 在 `SKILL.md` 的 phase 表加一行
4. 跑 `doctor.py` 验完整性

### 加 Python 脚本

放进 `scripts/`，加到 `scripts/doctor.py` 的 `EXPECTED_SCRIPTS` 列表，确保支持 `--help` 退 0。

---

## 致谢

- [inkos](https://github.com/Narcooo/inkos) by Narcooo —— 整套写作流水线的设计原型
- [Anthropic Claude](https://claude.com/claude-code) —— 跑这一切的引擎

---

## License

跟 inkos 上游保持一致：以原作 LICENSE 为准。本仓库内代码与文档，作为对 inkos 概念的 SKILL 形态再实现，未单独声明许可时同样以上游为准。
