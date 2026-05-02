# Schema: chapters/index.json

`books/<id>/chapters/index.json` 是**章节运营索引**——按章号记录每章的"现在是什么状态、字数多少、有什么 audit 问题、什么时候建/改的、跑了多少 token、AI 检测得分"。

它跟 `story/state/chapter_summaries.json` 不一样：

| 文件 | 作用 | 数据形态 |
|---|---|---|
| `chapters/index.json` | 运营索引（按状态查 / 待审 / 统计） | per chapter: 状态 + 字数 + 时间戳 + 审计 issues + token usage |
| `story/state/chapter_summaries.json` | 叙事记忆（喂下章 Planner / Composer 滑窗） | per chapter: events / characters / mood / hookActivity |

`status.py` / `analytics.py` / `book.py delete --json` / 待审章节查询都从 `chapters/index.json` 读。

---

## 字段定义（移植自 inkos `models/chapter.ts` ChapterMetaSchema）

```ts
ChapterMeta = {
  number: int >= 1                    // 必填
  title: string                       // 必填
  status: ChapterStatus               // 必填，枚举见下
  wordCount: int                      // 默认 0
  createdAt: ISO8601 datetime         // 必填
  updatedAt: ISO8601 datetime         // 必填
  auditIssues: string[]               // 默认 []，每条形如 "[critical] 描述"
  lengthWarnings: string[]            // 默认 []
  reviewNote?: string                 // 可选，人工审核留言或 state-degraded 自动注释
  detectionScore?: float [0, 1]       // 可选，AI 检测分（如有 detector）
  detectionProvider?: string          // 可选，"gptzero" / "originality" / "custom"
  detectedAt?: ISO8601                // 可选，最近一次检测时间
  lengthTelemetry?: object            // 可选，per LengthTelemetrySchema
  tokenUsage?: {                      // 可选
    promptTokens: int
    completionTokens: int
    totalTokens: int
  }
}
```

文件本体是 `ChapterMeta[]` 的 JSON 数组，按 `number` 升序排。

---

## 状态枚举（14 种，按生命周期）

| 状态 | 含义 | 何时进入 |
|---|---|---|
| `card-generated` | 已有 chapter_memo，未开始写 | Planner 落盘 chapter_memo.intent.md 后 |
| `drafting` | 正在写正文 | Writer 跑到一半（runtime/.draft.md 存在） |
| `drafted` | 写完了，等长度治理 / audit | Writer 完成、Normalize 之前 |
| `auditing` | 正在审 | Auditor 跑到一半 |
| `audit-passed` | 通过审 | audit.overall_score ≥ 85 + 闸门全过 |
| `audit-failed` | 审不通过且回环用尽 | 3 轮 revise 后仍 < 85 |
| `state-degraded` | 真理文件被章节落盘后污染（罕见） | chapter-truth-validation 报 critical |
| `revising` | 进入 reviser 修订中 | Reviser 跑到一半 |
| `ready-for-review` | 等人审 | apply_delta + Polisher 完成 |
| `approved` | 人审通过 | 用户/Claude 显式确认 |
| `rejected` | 人审拒绝 | 用户/Claude 显式拒绝 |
| `published` | 已发到平台 | export / publish 后 |
| `imported` | 从已有作品反向导入 | `import chapters` 命令（暂未实现） |

---

## 何时写

| 操作 | 触发的索引动作 |
|---|---|
| Planner 落盘 chapter_memo | `chapter_index.py add --status card-generated` |
| Writer 开写 | `update --status drafting` |
| Writer 完成、Normalize 前 | `update --status drafted --word-count <N>` |
| Auditor 开始 | `update --status auditing` |
| Auditor 通过 | `update --status audit-passed --audit-issues '[...]'` |
| Auditor 失败回环用尽 | `update --status audit-failed --audit-issues '[...]' --review-note '原因'` |
| Reviser 入场 | `update --status revising` |
| apply_delta + Polisher 完成、章节 .md 落盘 | `update --status ready-for-review --token-usage '{...}'` |
| 用户 approve / reject | `set-status --status approved\|rejected --review-note ...` |
| export / publish | `set-status --status published` |

简化做法：流水线只在两端写——`add --status card-generated`（Planner 完成）和 `update --status ready-for-review`（章节落盘）。中间状态走过场，可以一笔跳过。完整状态轨迹便于事后查、能复用。

---

## 与其他文件的关系

```
manifest.json#lastAppliedChapter ←→ index.json 最大 number    （应一致）
chapter_summaries.json[].chapter ←→ index.json 同号 entry      （应一一对应）
chapters/{NNNN}[_<title>].md     ←→ index.json 每个 entry      （文件应存在）
```

`chapter_index.py validate` 会把这三组关系都检查一遍。

---

## 仲裁权

- 索引的"权威字段"：**status / auditIssues / reviewNote / tokenUsage / detectionScore**——只能经 `chapter_index.py` 修改
- 索引的"派生字段"：**wordCount**——可以从 chapters/{NNNN}.md 重新计算（`word_count.py`）
- 索引**不存储**：章节正文（在 `chapters/{NNNN}[_<title>].md`，命名兼容 inkos 的 `{NNNN}_<title>.md` 与 novel-writer 的 `{NNNN}.md`）、叙事摘要（在 `chapter_summaries.json`）、伏笔（在 `hooks.json`）

直接编辑 `chapters/index.json` 视为脏写；`apply_delta.py` 不动它（它只管真理文件 `story/state/*.json`）。

---

## CLI 速查

```bash
# 加章节（流水线 step 11 自动调）
python scripts/chapter_index.py --book <bookDir> add \
    --chapter N --status ready-for-review --title "..." --word-count NNNN \
    [--audit-issues '[...]'] [--token-usage '{...}']

# 改字段
python scripts/chapter_index.py --book <bookDir> update --chapter N --status approved
python scripts/chapter_index.py --book <bookDir> set-status --chapter N --status published --review-note "已发布"

# 查
python scripts/chapter_index.py --book <bookDir> list [--status ready-for-review,approved] [--from 10] [--to 20]
python scripts/chapter_index.py --book <bookDir> get --chapter N
python scripts/chapter_index.py --book <bookDir> validate
```

完整字段参数见 `--help`。
