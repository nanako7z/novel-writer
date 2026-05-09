# Planner 反馈消费规则（Analyzer + audit drift）

Planner 必须在生成 chapter_memo 前消费两份上章遗留物：

| 文件 | 来源 | 语义 |
|---|---|---|
| `story/runtime/chapter-{N-1}.analysis.json` | [phase 13 Analyzer](phases/13-analyzer.md) | 定性回顾、节奏建议、疲劳信号 |
| `story/audit_drift.md` | [audit_drift.py write](audit-drift.md) (主循环 step 11.0b) | 上章审计**没改干净**的硬问题 |

注意：`docops_drift.json` 不是 Planner 输入——那是 Settler 的喂料。

## 1. Analyzer 反馈（chapter-{N-1}.analysis.json）

**读取**：尝试读文件。

- 不存在 → 视为无定性输入（不抛错）
- `warning === "analyzer-failed"` → stub 文件代表 Analyzer 跑挂了，不阻断

**消费规则**：

| 字段 | 处理 |
|---|---|
| `warningsForNextChapter`（priority=high） | 必须正面回应——按下表 cheat sheet 写进对应 memo 段 |
| `fatigueSignals`（如 `"'冷笑' 出现 4 次"`）| 直接转成 `## 不要做` 里的一条「本章避免使用 '冷笑' 等上章疲劳词」 |
| `reusableMotifs` / `pacingBeats` / `satisfactionHits` | 软性参考，不强制——避免连续 3 章打同一个爽点 |

**warning → memo 映射 cheat sheet**：

| warning 形式 | 进 memo 哪一段 |
|---|---|
| `"H001 已连续 3 章未推进，下章必须推一下"` | `## 本章 hook 账` 的 advance/resolve（**不能 defer**） |
| `"chapter_memo 承诺的『七号门实证』只兑现一半"` | `## 该兑现的 / 暂不掀的` 补一条「续兑现：七号门实证」 |
| `"AI 味集中在 X 段，下章注意"` | `## 不要做` 加一条具体避坑点 |

**冲突仲裁**：多条 high-priority warning 同时挤压且字数装不下 → **不要硬塞**，把冲突摘要给用户上报决策。不要 Planner 自己悄悄丢。

**不要发明 hook_id**：warning 里引用的 hook_id 必须在当前 `pending_hooks.md` 能查到。查不到（已 resolve）就转成 `## 不要做` 里的一条说明（"不要再提及 H001，已收"）。

## 2. audit drift（story/audit_drift.md）

**读取**：`python scripts/audit_drift.py --book <bd> read --json`

- `exists: false` → 上章 audit 干净通过，跳过
- `exists: true` → `issues` 是 `[{severity, category, description}]` 数组

**issue → memo 映射**：

| issue.category 例子 | severity | 进 chapter_memo 哪一段 |
|---|---|---|
| `逻辑闭环` / `因果链` / `动机不通` | critical | `## 不要做` 写"本章必须修复：上章 X 留下的因果断点"；涉及 hook 同时进 `## 本章 hook 账` advance |
| `人设崩坏` / `角色行为反差` | critical | `## 不要做` "本章不要再让 X 表现 Y" |
| `节奏拖沓` / `信息倾倒` | warning | `## 当前任务` + `## 章尾必须发生的改变` 给出具体动作；`## 日常/过渡承担什么任务` 收紧 |
| `重复套路` / `近 N 章雷同` | warning | `## 不要做` "本章避免重复上章 X 的桥段" |
| 其它 warning | warning | `## 不要做` 原文照搬 description |

**critical 必须正面回应**：不是一句"避免 X"——要给 Writer 具体的替代动作。

**drift 是建议性**：下章 Planner 消费完后由 step 11.0b 在新一轮 audit 后整体重写或清空。Planner **只读不删**。

**drift 与 Analyzer 反馈冲突**：与 §1 同样按"上报用户决策"流程处理。

## 3. 合并写入 cheat sheet

| 类型 | 进哪一段 |
|---|---|
| 兑现型 warning | `## 该兑现的 / 暂不掀的` |
| hook 推进型 warning | `## 本章 hook 账`（advance / resolve） |
| 避坑型 warning + fatigueSignals | `## 不要做` |
| audit drift critical | 优先 `## 不要做` + `## 本章 hook 账`（如涉及 hook） |
| audit drift warning | `## 不要做` + 影响 `## 当前任务` 表述 |

合流时同一段里两边的输入都列上，避免"先按 Analyzer 写完又被 drift 推翻"。
