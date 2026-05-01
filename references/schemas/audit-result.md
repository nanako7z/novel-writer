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
