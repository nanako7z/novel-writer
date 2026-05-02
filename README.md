# novel-writer

把 [inkos CLI](https://github.com/Narcooo/inkos) 的多 agent 中文网文写作流水线，移植成可在 [Claude Code](https://docs.claude.com/en/docs/claude-code) 里直接驱动的 SKILL 包。

不是 inkos 的 fork，也不是套壳——是把 inkos 里 34 个 agent / 9 个 pipeline / 35 个 utility / 27 个 CLI 命令的核心能力重写成 Claude 能直接调度的 phase 文件 + Python 脚本，跑在 Claude Code 一个 session 里，不需要 Node、不需要 LLM provider 配置、不需要 SQLite。

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

在 Claude Code 里打开任意空目录，对 Claude 说：

> 起一本仙侠新书，叫《青云志》，目标 200 章，番茄平台

Claude 会调 `init_book.py` 落地完整目录结构，给你填一下 `author_intent.md` / `current_focus.md`，然后就能：

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

### 确定性脚本（28 个）

| 类别 | 脚本 |
|---|---|
| **项目管理** | `init_book.py` `book.py`（list/show/rename/delete/copy） |
| **真理文件** | `apply_delta.py`（含 3 阶段 parser + 仲裁 + 治理） `settler_parse.py` `hook_arbitrate.py` `hook_governance.py` |
| **上下文** | `memory_retrieve.py`（滑窗）`context_filter.py`（降噪）`state_project.py`（视图）`pov_filter.py` |
| **写前/写后检** | `writer_parse.py`（sentinel）`post_write_validate.py`（机械错） |
| **质量控制** | `ai_tell_scan.py`（去 AI 味）`sensitive_scan.py`（敏感词）`fatigue_scan.py`（跨章疲劳）`word_count.py`（LengthSpec） |
| **写作辅助** | `narrative_control.py`（实体剥离 + 软化）`writing_methodology.py`（方法论 prompt）`spot_fix_patches.py`（line-anchored patch） |
| **章节运营** | `split_chapter.py`（超长拆分）`recover_chapter.py`（断点续跑）`cadence_check.py`（节奏压力） |
| **诊断** | `status.py` `doctor.py` `analytics.py` |
| **风格 / 出版** | `style_analyze.py`（5 项纯文本统计）`consolidate_check.py`（卷压缩判定）`export_book.py`（txt/md/epub） |

### 题材 profile（15 个）

`templates/genres/`：cozy / cultivation / dungeon-core / horror / isekai / litrpg / other / progression / romantasy / sci-fi / system-apocalypse / tower-climber / urban / xianxia / xuanhuan。

每个 profile 含：fatigueWords / chapterTypes / satisfactionTypes / pacingRule / auditDimensions（限定 Auditor 的 37 维子集）/ numericalSystem / powerScaling / eraResearch 三个 toggle。

### 数据契约 schema（4 个）

- [chapter-memo.md](references/schemas/chapter-memo.md)：Planner 产出
- [runtime-state-delta.md](references/schemas/runtime-state-delta.md)：Settler 产出（含 3 阶段 parser 文档）
- [audit-result.md](references/schemas/audit-result.md)：Auditor 产出
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
├── references/                  ← 各阶段 prompt 与契约
│   ├── phases/00-13-*.md        ← 14 个 phase（编排 + radar + 10 agent + polisher + consolidator + analyzer）
│   ├── branches/{fanfic,style}.md
│   ├── schemas/                 ← 4 个数据形状
│   ├── genre-profile.md         ← 15 题材 schema + 注入
│   ├── audit-dimensions.md      ← 37 维度全表
│   ├── ai-tells.md / sensitive-words.md
│   ├── hook-governance.md       ← 伏笔生命周期
│   ├── memory-retrieval.md      ← 滑窗算法
│   ├── foundation-reviewer.md   ← Architect 闸门
│   ├── post-write-validation.md
│   ├── long-span-fatigue.md / pov-filter.md / cadence-policy.md
│   ├── chapter-recovery.md / state-projections.md
│   ├── narrative-control.md / writing-methodology.md
│   └── rule-stack.md            ← L1-L4 四级覆盖
├── templates/                   ← init 用的种子文件
│   ├── inkos.json + book.json
│   ├── story/{*.md, state/*.json}
│   └── genres/                  ← 15 题材 profile
├── scripts/                     ← 28 个 Python 脚本（标准库）
└── evals/evals.json             ← SKILL 自身的 7 个测试 prompt
```

用户写的书会落到调用目录的 `books/<id>/` 下，**不进本仓库**——`.gitignore` 已排除。

---

## 开发与扩展

### 自检

```bash
python scripts/doctor.py
```

会跑 Python 版本 / SKILL 根布局 / 16 个模板 + 15 个题材完整性 / 28 个脚本 `--help` 自检。当前状态：31 ok / 0 warning / 0 fail。

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
