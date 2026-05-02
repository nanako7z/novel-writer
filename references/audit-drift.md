# Audit Drift Guidance（审计纠偏喂料）

> 每章 audit-revise 回环结束之后，把"已经收尾但 audit 仍然标为 critical / warning 的硬问题"持久化成 `books/<id>/story/audit_drift.md`，下一章 Planner 在 phase 02 step 1a' 必须读它，正面映射到 chapter_memo 的"## 不要做" / "## 该兑现的 / 暂不掀的" / "## 本章 hook 账"。

> 这是工作流一致性问题 #5 的修复：inkos 源码 `pipeline/runner.ts#persistAuditDriftGuidance` 在每章持久化阶段把审计残留写到 `audit_drift.md`，下章 Planner 装配输入时读它；本 SKILL 之前没有对应实现，缺的就是"上章审计没改干净的问题"如何继续传递给下章。

## 何时调用

- **主循环 step 11.0b**（编排器自动跑）：snapshot_state 之后、Chapter Analyzer 之前。
- **手动重置**（`clear` 子命令）：作者跑了 retroactive 修订让上章 audit 重新干净，希望下章不再带这条历史包袱时。
- **手动检查**（`read` 子命令）：人想看下章 Planner 会看到什么。

不要在以下场景调它：

- 章节正文落盘失败（`audit-failed-best-effort` / `post-write-validate-failed`）—— issues 还在 step 7 audit-revise 回环里活着，没到"收尾后的残留"语义。
- 中间的 audit 轮次——只有**最后一轮**（与落盘版本对应的那一轮）的 critical/warning 才进 drift。前几轮的 issues 都已被 reviser 改掉或还在改，不算 drift。

## 文件格式

```
# 审计纠偏

## 审计纠偏（自动生成，下一章写作前参照）

> 第7章审计发现以下问题，下一章写作时必须避免：
> - [critical] 逻辑闭环: 主角对储物袋"绑定"过程的描述与第 4 章设定矛盾
> - [warning] 节奏: 中段 4 段连续景物描写造成失速感
```

`# 审计纠偏` / `## 审计纠偏（自动生成，下一章写作前参照）` / `> 第N章审计发现以下问题，下一章写作时必须避免：` 三行是 inkos 源码原样移植，**Composer 会按这个 pattern 识别并注入下章 user message**——不要改。

英文模式（`book.language === "en"`）改用：

```
# Audit Drift

## Audit Drift Correction

> Chapter 7 audit found the following issues to avoid in the next chapter:
> - [critical] ...
```

## Severity 过滤

- **critical**：保留——下章必须正面回应（不能只一句"避免"，要给具体替代动作）。
- **warning**：保留——下章 Planner 进 `## 不要做`，Writer 写的时候规避。
- **info**：丢弃——info 通常是软建议（"考虑加一段心理描写"），不进 drift；进了反而会让下章 memo 充满噪声。

filter 后**0 条**保留 → 删除 `audit_drift.md`（不留陈旧内容、不写空文件）。

## CLI

```bash
# 写：从 issues.json 写 drift（主循环 step 11.0b 用）
python scripts/audit_drift.py --book <bookDir> write \
    --chapter N --issues <path-to-issues.json> [--lang zh|en]

# 读：解析 audit_drift.md 回 JSON（Planner phase 02 step 1a' 用）
python scripts/audit_drift.py --book <bookDir> read [--json]

# 清：删除 audit_drift.md（手动 / Planner 消费完后由下次 step 11.0b 自动覆写）
python scripts/audit_drift.py --book <bookDir> clear

# 顺手清 current_state.md 里残留的"## 审计纠偏"块（写命令也会自动跑这步）
python scripts/audit_drift.py --book <bookDir> sanitize-current-state
```

`issues.json` shape：

```json
[
  {"severity": "critical", "category": "逻辑闭环", "description": "主角对储物袋..."},
  {"severity": "warning",  "category": "节奏",       "description": "中段..."},
  {"severity": "info",     "category": "建议",       "description": "..."}
]
```

`write` 子命令做这些：
1. 顺手 sanitize `current_state.md`（剥掉历史版本内嵌的 `## 审计纠偏` 块）。
2. 过滤 issues 到 critical+warning，info 丢弃。
3. 过滤后非空 → 写 `audit_drift.md`（原子 `.tmp + os.replace`）。
4. 过滤后为空 → 删 `audit_drift.md`（如果存在）。

## 与 chapter_summaries 的边界

| 维度 | audit_drift | chapter_summaries |
|---|---|---|
| 寿命 | 下章 Planner 消费完即过期；下次 step 11.0b 整体覆写或清空 | **永久**——是叙事真理的一部分，consolidate 才会归档压缩 |
| 内容 | 只关心**未解决**的硬审计问题（critical/warning） | 章节事件、情绪、新增 hook、状态 delta 等完整记录 |
| 写入位置 | `story/audit_drift.md`（顶层 md，与 current_state.md 同级） | `story/state/chapter_summaries.json`（state truth file） |
| 写入闸门 | `audit_drift.py write`（专用） | `apply_delta.py`（与其它真理文件一起） |
| Planner 消费方式 | step 1a' 必须正面回应每条 critical | step 0（memory_retrieve 滑窗）— 取最近 3-6 章作上下文，**不**逐条回应 |

drift 是 transient（"上一章这个问题没改干净，下章别再犯"），summaries 是 permanent（"上一章发生了 X / 留下了 Y hook"）——别混。

## 与上章 Analyzer 反馈的边界

两者**都是**给下章 Planner 的喂料，但视角不同：

- **Analyzer 反馈**（[phase 13](phases/13-analyzer.md)）：定性、建议性——节奏怎样、疲劳词、爽点是否重复、可复用 motif。下章 Planner 用它**优化**配方。
- **audit drift**：审计、强制性——上章哪条没修干净、哪条人设崩了。下章 Planner 用它**避雷**或**补救**。

冲突时（drift 要补救 + Analyzer 要换节奏 + 字数装不下）按 phase 02 §"冲突仲裁"上报用户决策，不要 Planner 自己丢信号。

## 与其它脚本的协作

- `apply_delta.py` 不调 audit_drift——drift 不是真理文件，没有 RuntimeStateDelta 通道。
- `chapter_index.py` 不与 drift 互动——前者管运营索引（status/wordCount/auditIssues 数组），drift 管"下章避坑提示"。chapter_index 的 `auditIssues` 是**全部** issue 的字符串化记录（含 info）；drift 是**严格过滤**（只 critical+warning）的下章喂料。两者关注点不重叠。
- `snapshot_state.py` 不动 drift——drift 是 transient，进 snapshot 反而会在 restore 时把过期的"避坑提示"复活。snapshot 只覆盖 7 个 md 真理文件 + state JSON，**不含** `audit_drift.md`（也不含 `current_state.md` 里被 sanitize 掉的那部分）。

## 关键不变量

1. **drift 只反映最后一轮 audit**：step 7 audit-revise 回环里中间轮次的 issues 不进 drift——它们要么已被 reviser 改掉，要么还在改。step 11.0b 看的是 step 7 退出时（passed 或 best-effort 落盘）那一轮的 `audit.issues`。
2. **info 永远不进 drift**：info 是软建议，与"硬避雷"的语义不符，会污染下章 memo。
3. **写入是覆盖式 + 原子**：`.tmp + os.replace`，不 append、不读取旧 drift 合并。每章重写整份。
4. **issues 列表为空 → 删文件**：不写空 markdown 也不留陈旧内容。"无 drift"的物理表现就是**文件不存在**。
5. **Planner 不删 drift**：消费完 drift 由下次 step 11.0b 在新一轮 audit 之后**整体覆写或清空**。Planner 只读不删——避免"Planner 删了但本章中途崩溃 / 用户回退"的不一致状态。
6. **stdlib only**：本脚本不依赖第三方包，可在最小环境下跑。
