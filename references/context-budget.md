# Context Budget Enforcement

Composer 末段的硬化闸门：按 category 给 `context_pkg.json` 设字符上限，注入 Writer prompt 前按优先级丢内容。

## 为什么需要它

inkos 的 `buildGovernedContextPackage` 在 TS 层就把每 category 砍到上限；SKILL 形态下 Composer 是"读 + 拼"流程，没这层闸门。后果：

- 30+ 章后 `recentSummaries` / `relevantSummaries` 越拼越胖，挤掉 `currentState` / `activeHooks` / `auditDriftGuidance` 等承重段。
- Writer 看到的 `context_package.json` 表面段全在，实际 hook 段已被 token 截断（且无提示）。

`scripts/context_budget.py` 在 Composer step 5（写盘前）做最后处理：按固定 profile **结构化地**砍超载部分（drop 旧章 / 压缩条目 / 删已结支线），而非简单尾截。

## 触发位置

phase 03 Composer 步骤 5（写入 runtime 工件）之前调一次：

```bash
python {SKILL_ROOT}/scripts/context_budget.py \
  --input story/runtime/context_package.json \
  --profile default \
  --budget-total 80000 \
  --out story/runtime/context_package.budgeted.json \
  --json
```

调用后用 `context_package.budgeted.json` 替代原 `context_package.json` 喂给
Writer。原文件保留作为审计参照，写进 `chapter_trace.composerInputs`。

## 默认 profile（characters）

| Category | budget | drop priority (1=drop first) | 处理策略 |
|---|---:|---:|---|
| `chapterMemo`         | 4000 | 5 (load-bearing) | 永不丢；超载只 tail-truncate |
| `currentState`        | 3000 | 4 | 可压缩（按 facts 行截断），不丢键 |
| `recentSummaries`     | 12000 | 3 | 先丢最旧章，仍超再压缩条目 |
| `relevantSummaries`   | 8000 | 2 | 先丢最旧章 |
| `activeHooks`         | 5000 | 4 | 压缩条目（保留 id/status/expectedPayoff），不丢条目 |
| `characterMatrix`     | 4000 | 3 | tail trim 矩阵行（保最近交互） |
| `subplotBoard`        | 3000 | 2 | 先丢 closed/resolved 的 |
| `emotionalArcs`       | 2000 | 2 | 先丢最旧 |
| `styleGuide`          | 4000 | 4 | tail-truncate |
| `genreProfile`        | 5000 | 5 | 永不丢 |
| `bookRules`           | 3000 | 4 | tail-truncate |
| `fanficCanon`         | 6000 | 4 | 仅 fanfic 模式存在 |
| `auditDriftGuidance`  | 1500 | 5 | 永不丢（下章避坑硬信号） |

总 budget 默认 80000 chars。可用 `--budget-total` 覆盖。

profiles：

- `default` —— 上表
- `strict` —— 每个 quota × 0.75（卷尾 / 长篇压力大时）
- `loose` —— 每个 quota × 1.35（短篇 / 章节少时）

## Drop priority 算法

四个递增 pass：

1. **Pass 1**：扫所有 category，超过 quota 的就在原地按 truncator 缩回 quota
   大小。这一步一般就够了。
2. **Pass 2-5**（仅当 Pass 1 后总量仍 > budget-total）：按 priority 1→4
   依次走，每个 priority 内挑当前最大的 category，让它再缩一档（缩到至少能
   把 overage 抵消，floor 不低于 quota × 0.30）。priority 5 是最后兜底。
3. 若 priority 5 都触底仍超 → `budgetStatus = "hard-overflow"`，由调用方
   决定（Composer 应当 abort 并提示用户）。

## Hard floor

每个 category 的硬下限是 `quota × 0.30`。永不低于这个值——再低就连骨架都
没了，Writer 完全不知道自己在写什么书。

## hard-overflow 处置

`budgetStatus == "hard-overflow"` 时 Composer **不要硬塞给 Writer**，而是
向用户上报：

```
Context budget hard-overflow:
  total = 92847 chars > budget = 80000 chars (after all priority passes)
  load-bearing categories already at floor: chapterMemo, auditDriftGuidance

Suggest one of:
  1. python scripts/consolidate_check.py --book <bk>  → fold older summaries
  2. python scripts/state_project.py --view hooks-grouped → replace raw hook list
  3. raise --budget-total (only if your model context allows)
```

不要自动重试——预算超限是真实的输入压力信号。

## Truncator 语义对照

| Category | 一档 | 二档（仍超时） |
|---|---|---|
| `recentSummaries` | 丢最旧章 | 压缩到 chapter+title+events(<=200ch) |
| `relevantSummaries` | 同上 | 同上 |
| `activeHooks` | 压缩条目 | 不再丢；停在压缩态 |
| `subplotBoard` | 丢 closed/resolved | 尾部 trim 列表 |
| `emotionalArcs` | 丢最旧 | （priority 2 — 不会再降） |
| `characterMatrix` | tail trim 行 | （priority 3 — 不会再降） |
| 其它 | tail-truncate | （触 floor 即停） |

## 输出 schema

```json
{
  "ok": true,
  "budgetStatus": "ok|adjusted|hard-overflow",
  "totalCharsBefore": 92847,
  "totalCharsAfter": 79231,
  "budgetTotal": 80000,
  "profile": "default",
  "perCategory": [
    {
      "name": "recentSummaries",
      "before": 18234,
      "after": 11800,
      "action": "truncated-summaries",
      "kept": 6,
      "dropped": 4
    }
  ],
  "warnings": [],
  "budgetedContext": { ... }
}
```

`action` 取值：`noop` / `tail-truncate` / `truncated-summaries` /
`truncated-summaries+compressed-entries` / `compressed-hook-entries` /
`dropped-closed-subplots` / `dropped-closed+truncated` /
`dropped-old-arcs` / `trimmed-matrix-rows` / `tail-truncate-serialized`。

## 调用约定

- **位置**：phase 03 Composer 步骤 5 之前。把已构建的 `context_package.json`
  写到临时路径，跑 `context_budget.py`，再把 `budgetedContext` 替换原文件。
- **审计**：`perCategory` 列表整段写入 `chapter_trace.composerInputs.budget`
  方便后期 replay / 审计。
- **不调 LLM**：纯确定性脚本，stdlib only。
- **失败语义**：脚本本身只在 IO / 参数错误时返回 exit 1；budgetStatus
  通过 JSON 字段表达，调用方读字段决策，不读 exit code。
