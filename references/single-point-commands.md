# 单点指令速查（不进主循环）

**措辞模糊时先问一句再做**：用户说"改一下这段" / "我觉得这段不够自然" / "这段表达不太好"——既可能是整章 polish，也可能是定点修一两句。先用一句问句确认范围（"是想整章润一遍，还是只动你指出的那一小段？"）再决定走 phase 11 polisher / phase 10 polish 模式 / phase 10 spot-fix。**不要**默认整章改——半径选错了既浪费 token，又容易把作者满意的段落改坏。

## 完整指令表

| 用户大致这么说 | 做什么 |
|---|---|
| "审一下第 N 章" | 只跑 [phase 09](phases/09-auditor.md)；输出审计结果，不动正文 |
| "把 XX 句改成 YY" | [phase 10 reviser](phases/10-reviser.md) `spot-fix` 模式 |
| "整章 polish / 文字打磨一遍" | 直接跑 [phase 11 polisher](phases/11-polisher.md)（绕过 audit 借线规则） |
| "用 reviser polish 模式改" | phase 09 + 10 polish 模式（结构层修订，不动情节） |
| "AI 味太重，专项处理" | phase 10 `anti-detect` 模式，先跑 `scripts/ai_tell_scan.py` 拿证据 |
| "重做架构" | phase 04 architect 单跑 |
| "看下伏笔池压力 / 伏笔健康度" | `python scripts/hook_governance.py --book <bookDir> --command health-report` |
| "校验一下真理文件没问题吧" | `python scripts/hook_governance.py --book <bookDir> --command validate` + `python scripts/chapter_index.py --book <bookDir> validate` |
| "看下哪些章节待审 / 已通过 / review list" | `python scripts/chapter_index.py --book <bookDir> list --status ready-for-review`（或 `approved` / `audit-failed` 等） |
| "把第 N 章标记为 approved / 已发布" | `python scripts/chapter_index.py --book <bookDir> set-status --chapter N --status approved`（或 `published` / `rejected`） |
| "回滚到第 N 章那一刻 / 真理文件写脏了" | `python scripts/snapshot_state.py --book <bookDir> restore --chapter N [--dry-run]`（先 `list`/`diff`；详见 [state-snapshots.md](state-snapshots.md)） |
| "看下上章审计还有什么没改干净 / drift" | `python scripts/audit_drift.py --book <bookDir> read --json`（详见 [audit-drift.md](audit-drift.md)） |
| "看下伏笔账兑现了没 / commitment ledger" | `python scripts/commitment_ledger.py --memo <chapter_memo.md> --draft <draft.md>` |
| "卷尾兑现率 / cross-volume payoff" | `python scripts/hook_governance.py --book <bookDir> --command volume-payoff --volume N` |
| "看 audit 改了几轮 / stagnation" | `python scripts/audit_round_log.py --book <bookDir> --chapter N --analyze` |
| "上下文太长了 / context 超 budget" | `python scripts/context_budget.py --input <context_pkg.json> --budget-total 80000`（composer step 5 自动调；hard-overflow 时给用户决策） |
| "锁住正在写的书 / 防多 session 撞写" | `python scripts/book_lock.py --book <bookDir> --json {acquire/status/release}`（详见 [book-lock.md](book-lock.md)） |
| "压缩前面卷 / consolidate / 摘要太多了 / 历史压缩一下" | 先跑 `python scripts/consolidate_check.py --book <bookDir>` 看是否该压；该压则进 [phase 12 consolidator](phases/12-consolidator.md) |
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
| "看现在写到主循环哪一步 / loop state" | `python scripts/loop_state.py status --book <bookDir>`（in-flight 章节进度；详见 [invariants.md #11](invariants.md)） |
| "导出 / 出版 / 打包成 epub/txt/md" | `python scripts/export_book.py --book <bookDir> --format txt\|md\|epub [--include-summary]` |
| "看下跨章疲劳 / 是不是写重复了" | `python scripts/fatigue_scan.py --book <bookDir> --current-chapter N [--window 5]`（advisory） |
| "节奏 / 爽点压力 / cadence" | `python scripts/cadence_check.py --book <bookDir> --current-chapter N`（Planner 阶段也会自动跑） |
| "看下当前网文市场什么火 / 市场雷达 / radar / 现在玄幻什么火" | 进 [phase 01 radar](phases/01-radar.md)；该 phase 内部先跑 `radar_fetch.py scan` 三档递进抓榜（fanqie/qidian/feilu/jjwxc/zongheng/sfacg），再走 LLM 分析 |
| "拉一下番茄玄幻榜 / 单跑 X 平台 Y 题材榜 / radar fetch" | `python scripts/radar_fetch.py scan --sites <id\|all> --genre <id\|all> [--top 15] [--no-cache]`（详见 [phase 01](phases/01-radar.md) Stage A + [radar-sources.md](radar-sources.md)） |
| "看下都有哪些题材 / list genres" | `python scripts/genre.py list [--json]` |
| "看 xianxia 题材 profile / show genre" | `python scripts/genre.py show <id> [--json]` |
| "新增自定义题材 / 复制 profile 改" | `python scripts/genre.py add <newId> --from <baseId> [--name "..."] [--out <path>]` |
| "校验题材 profile schema" | `python scripts/genre.py validate [<id>]` |
| "调整章节焦点 / 角色关系 / 风格 / outline / role 档案"（白名单指导 md） | 走 user-directive docOps 流程，详见 [user-directive-flow.md](user-directive-flow.md) |
| "改 author_intent / book_rules / fanfic_canon / parent_canon"（作者宪法） | LLM 直接 `Edit`，再调 `apply_delta.py log-direct-edit` 补审计日志，详见 [user-directive-flow.md](user-directive-flow.md) |
| "看哪些 md 被改过 / 谁改的" | `cat story/runtime/doc_changes.log` |
| "回滚某次 docOp" | `python scripts/apply_delta.py --book <bookDir> revert-doc-op --op-id <sha8>`（仅适用于走 docOps 落盘的；宪法直 Edit 用 `git checkout` 或手动恢复） |
| "扫一下指导 md 是不是写脏了 / docops drift" | `python scripts/docops_drift.py --book <bookDir> --window 6 [--write]`（advisory；候选喂下章 Settler） |
