# AuditResult（Auditor 输出 schema）

09-auditor 阶段的输出契约。来源：`agents/continuity.ts` L18-36（TS interface）+ L470-493 / L507-530（JSON 输出格式中英双版）。

Auditor 必须返回一段严格 JSON。SKILL 实施时由 Claude 直接生成，并由后续 10-reviser 据此决定是否回环。

---

## 1. JSON 形状

```json
{
  "passed": true,
  "overall_score": 87,
  "issues": [
    {
      "severity": "warning",
      "category": "节奏检查",
      "description": "第二节连续三段都是赵执事的内心戏，节奏停滞",
      "suggestion": "把第二节中段切到林秋视角的动作"
    },
    {
      "severity": "info",
      "category": "文风检查",
      "description": "'仿佛'一词出现 4 次，密度偏高",
      "suggestion": "Polisher 阶段处理"
    }
  ],
  "summary": "整体结构成立，节奏有局部停滞，文笔层面留给 Polisher"
}
```

---

## 2. 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `passed` | bool | 是 | 是否通过审稿 |
| `overall_score` | int 0-100 | 是（auditor 支持评分时） | 综合质量分 |
| `issues` | array<Issue> | 是 | 问题列表（空数组合法） |
| `summary` | string | 是 | 一句话总结审查结论 |

> TS interface 中 `overallScore` 是 `optional`（旧版 auditor 不打分），但 v1 移植统一要求评分。

---

## 3. `Issue` 子 schema

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `severity` | enum | 是 | `critical` \| `warning` \| `info` |
| `category` | string | 是 | 审查维度名称（中文，如"OOC检查"、"节奏检查"），来自 `references/audit-dimensions.md` |
| `description` | string | 是 | 具体问题描述，包含定位（哪段/哪句） |
| `suggestion` | string | 是 | 修改建议 |

---

## 4. severity 含义

| severity | 含义 | 影响 passed | 影响 overall_score |
|----------|------|-------------|--------------------|
| `critical` | 结构性问题，必须修，否则不合格 | 一旦存在则 `passed=false` | 显著拉低分数 |
| `warning` | 影响阅读体验但故事主干完整 | 不影响 passed | 中度拉低分数 |
| `info` | 文笔层面 / 提示性记录，归 Polisher 处理 | 不影响 passed | **不计入 overall_score** |

**审稿边界**（来自 prompt 硬约束）：

- Auditor 只审完成度 + 结构，**不审文笔、排版、句式**
- 文笔问题只能以 `severity="info"` 标注供 Polisher 参考
- info 问题**不计入** `passed` / `overall_score`，也**绝不可**标为 `critical`

---

## 5. pass 规则

```
passed == true   ⟺   issues 中不存在 severity == "critical" 的项
```

**仅当**存在 critical 级别问题时，`passed` 才为 `false`。warning 数量再多也不直接判 fail（但会拉低分数）。

---

## 6. overall_score 评分校准（中英双版逐字）

中文版（`continuity.ts` L524-530）：

| 区间 | 含义 |
|------|------|
| 95-100 | 可直接发布，无明显问题 |
| 85-94  | 有小瑕疵但整体流畅可读，读者不会出戏 |
| 75-84  | 有明显问题但故事主干完整，需要修但不紧急 |
| 65-74  | 多处影响阅读体验的问题，节奏或连续性有断裂 |
| < 65   | 结构性问题，需要大幅重写 |

英文版评分校准（`continuity.ts` L487-492）：

| 区间 | 含义 |
|------|------|
| 95-100 | Publishable as-is, no noticeable issues |
| 85-94  | Minor blemishes but smooth reading, the reader won't break immersion |
| 75-84  | Noticeable problems but the story backbone holds, needs revision but not urgent |
| 65-74  | Multiple issues hurt the reading experience, pacing or continuity has gaps |
| < 65   | Structural breakdown, needs major rewrite |

> 综合评分，不要因为单一小问题大幅拉低分数。

---

## 7. audit-revise 主循环阈值

来源：`pipeline/chapter-review-cycle.ts` L33-35。

| 常量 | 值 | 用途 |
|------|----|------|
| `MAX_REVIEW_ITERATIONS` | `3` | audit-revise 最多回环 3 轮 |
| `NET_IMPROVEMENT_EPSILON` | `3` | 提前退出阈值：如果新一轮 `overall_score - 上一轮 overall_score < 3`，提前退出并保留历轮中 score 最高的版本 |

伪代码：

```python
best = None
prev_score = -inf
for i in range(MAX_REVIEW_ITERATIONS):
    audit = run_auditor(chapter_text)
    if best is None or audit.overall_score > best.score:
        best = (audit, chapter_text)
    if audit.passed and audit.overall_score >= 85:
        return best
    delta = audit.overall_score - prev_score
    if i > 0 and delta < NET_IMPROVEMENT_EPSILON:
        # 改了一轮但没明显改善——保留最高分版本
        return best
    chapter_text = run_reviser(chapter_text, audit.issues)
    prev_score = audit.overall_score
return best
```

> 实际 inkos 实现可能更细致（按 critical 数 + 分数综合判断），SKILL 移植时以 `passed && score >= 85` 与 `delta < 3` 两个早退条件为主。

---

## 8. 解析失败回退

来源：`continuity.ts` L647-678。auditor 输出可能不是严格 JSON（小模型场景），SKILL 应实现以下兜底解析（按顺序）：

1. **Strategy 1**：从输出中找平衡的 JSON 对象（非贪婪匹配 `{...}`）
2. **Strategy 2**：把整段输出当 JSON 直接 parse
3. **Strategy 3**：用正则单独抽 `"passed"`、`"issues"`、`"summary"` 字段

3 种策略都失败 → 把这一轮当作 audit failed，触发下一轮回环，最多 3 轮后中断。

---

## 9. 与 10-reviser 的衔接

Reviser 根据 issues 数组选择修改模式（详见 `references/phases/10-reviser.md`）：

- critical 数 ≥ 1 且涉及剧情 → `rework`
- critical 全是台词/语言层 → `polish`
- AI 味/敏感词专项 → `anti-detect`
- 用户单点指明 → `spot-fix`
- 其他多 warning → `rewrite`
- 混合 → `auto`（让 Claude 自决）

issues 中每条的 `suggestion` 字段是 reviser 的直接输入，必须填写可执行的具体改法（不是"再润色一下"这种空话）。

---

## 10. audit-r{i} 单轮 artifact

audit-revise 闭环每一轮（含 round 0 初评）都被 orchestration 落到：

```
books/<id>/story/runtime/chapter-{NNNN}.audit-r{i}.json
```

`i` 是 0-based 轮序号（与 orchestration 的 `iter` 变量同步）：

- `audit-r0.json` —— 初评（normalize 后的第一次 audit，未经过任何 reviser）
- `audit-r1.json` —— reviser 第 1 轮跑完后的复审
- `audit-r2.json` —— reviser 第 2 轮跑完后的复审（若到此还未 pass，闭环停在这里
  或更早，由 EPSILON 早退）

写盘 / 读取 / 跨轮分析的工具是 [`scripts/audit_round_log.py`](../../scripts/audit_round_log.py)。

### 10.1 文件 schema

```json
{
  "chapter": 12,
  "round": 0,
  "timestamp": "2026-05-02T10:11:12.345Z",
  "audit": {
    "overall_score": 78,
    "passed": false,
    "issues": [
      {
        "dim": 9,
        "severity": "critical",
        "category": "POV violation",
        "description": "第二节在主角 POV 中插入了配角内心独白",
        "evidence": "...原文片段..."
      }
    ]
  },
  "deterministic_gates": {
    "ai_tells":          { "critical": 0, "warning": 2 },
    "sensitive":         { "blocked": false },
    "post_write":        { "critical": 0, "warning": 1 },
    "fatigue":           { "critical": 0, "warning": 0 },
    "commitment_ledger": { "violations": 0 }
  },
  "reviser_action": {
    "mode": "polish",
    "target_issues": ["dim-9", "dim-25"],
    "outcome": "applied"
  },
  "delta": {
    "score_change": 5,
    "issues_resolved":   ["上一轮某 issue 的 description"],
    "issues_introduced": ["本轮新出现的 issue 的 description"]
  }
}
```

### 10.2 字段语义

| 字段 | 类型 | 说明 |
|------|------|------|
| `chapter` | int | 章节号 (>=1) |
| `round` | int | 0-based 轮序号 |
| `timestamp` | ISO8601 string | 写盘时间（UTC） |
| `audit.overall_score` | int 0-100 | 本轮 LLM 评分（与 §1 一致） |
| `audit.passed` | bool | 本轮是否通过（与 §5 pass 规则一致） |
| `audit.issues` | array<Issue> | 本轮 issues（结构与 §3 一致；可额外携带 `dim` / `evidence` 字段） |
| `deterministic_gates` | object | ai_tells / sensitive / post_write / fatigue / commitment_ledger 五道确定性闸门的 critical/warning 计数 |
| `reviser_action.mode` | enum / null | 本轮 reviser 选用的模式（passed=true 时 null） |
| `reviser_action.target_issues` | array<string> | reviser 本轮明确瞄准的 issue id / dim |
| `reviser_action.outcome` | `"applied"` \| `"skipped"` | passed=true → skipped；其它情况 applied |
| `delta.score_change` | int | 本轮 score - 上一轮 score（round 0 = 0） |
| `delta.issues_resolved` | array<string> | 上一轮在、本轮不在的 issue description 集合 |
| `delta.issues_introduced` | array<string> | 上一轮不在、本轮新出现的 issue description 集合 |

`delta` 三字段由 `audit_round_log.py --write` 自动计算（读 `audit-r{i-1}.json`
做 set diff），调用方传入的 `delta` 会被覆盖——单一真相来源。

### 10.3 跨轮分析（`--analyze` 输出）

```json
{
  "ok": true,
  "chapter": 12,
  "totalRounds": 3,
  "scoreProgression": [62, 71, 78],
  "stagnationDetected": false,
  "recurringIssues": [
    {
      "description": "节奏拖",
      "category": "节奏检查",
      "severity": "critical",
      "appearedInRounds": [0, 1, 2],
      "roundsCount": 3
    }
  ],
  "summary": "3 round(s); score 62 -> 78 (delta +16); 1 recurring issue(s)"
}
```

- `recurringIssues`：同一 description 在 ≥ 2 个 round 出现的 issue。Reviser 看
  到的 critical 是"上一轮已试但没解决"——必须升级 mode（见
  [10-reviser.md §防呆](../phases/10-reviser.md#防呆避免轮间漏看per-round-artifact-契约)）。
- `stagnationDetected`：存在 `severity=critical` 的 recurringIssue 且其
  `appearedInRounds` 包含**连续两个**轮序号（如 `[0,1]` 或 `[1,2]`）→ orchestration
  step 7c.1 自动把 reviser mode 升一级。

### 10.4 CLI 速查

```bash
# 写一轮（payload JSON 文件路径用 --write 传）
python scripts/audit_round_log.py --book <bookDir> --chapter N --round i \
    --write /tmp/round-i.json

# 列所有轮
python scripts/audit_round_log.py --book <bookDir> --chapter N --list [--json]

# 读某一轮
python scripts/audit_round_log.py --book <bookDir> --chapter N --read --round i

# 跨轮分析
python scripts/audit_round_log.py --book <bookDir> --chapter N --analyze

# 清空（章节大改/外科级 rework 后重启 audit-revise 时用）
python scripts/audit_round_log.py --book <bookDir> --chapter N --clear
```

### 10.5 与既有字段的关系

audit-r 文件里的 `audit` 子对象**就是** §1 的 AuditResult JSON——同一份内容，外加
轮号、确定性闸门快照、reviser 动作、跨轮 delta 这四个 audit-revise 闭环专用维度。
落 `story/state/audit_log/<NNNN>.json`（§9 提到的最终 snapshot）时，挑 `passed=true`
或最后一轮 audit-r 中 `score` 最高者复制过去；audit-r 系列保留在 runtime/ 作为
回溯证据。
