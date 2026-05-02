# Chapter State Recovery（章节断点续跑）

> 当 `write-next-chapter` 主循环中途崩了——Writer 抛错、网络断、用户 Ctrl-C、`git pull` 把别人推送的 runtime 拉了下来——`story/runtime/` 里会留下半成品（intent.md 在、normalized.md 不在、delta.json 写到一半），但 `manifest.json#lastAppliedChapter` **没有推进**。这时不要从 Plan 重头跑，先用 `recover_chapter.py` 看一眼断在哪里。

## 何时调用

- 你或用户**意外终止**了一次 `write-next` 主循环（任意阶段）
- 系统崩溃 / 终端被关 / SSH 掉线后回到这本书
- `git pull` / `git stash pop` 之后 runtime 目录可能被外部改写
- 任何"我记得这章我已经写过一半了，到底跑到哪一步"的场景

不要在以下场景调它：
- 章节已经完整 finalize（`chapters/{NNNN}.md` 落盘 + manifest 推进）后做"复盘"——直接读 manifest 即可
- 主循环正常进行中（每个阶段紧接着下一阶段，没有半成品）

## 用法

```bash
python {SKILL_ROOT}/scripts/recover_chapter.py --book <bookDir> [--clean] [--json]
```

- `--book`：书目录（含 `story/`、`book.json`）
- `--json`：纯 JSON 输出，无 prose、无 `--clean` 确认提示（CI / 程序消费用这个）
- `--clean`：删除该章节的所有 `story/runtime/chapter-{NNNN+1}.*` 文件。**慎用**：等于放弃所有半成品、要从 Plan 重新开始。非 JSON 模式会让你按 y/N 确认；JSON 模式直接删（不交互）

## 输出 schema

```json
{
  "nextChapter": 7,
  "latestPhase": "delta",
  "chapterFinalized": false,
  "presentArtifacts": ["intent.md", "context.json", "draft.md", "normalized.md", "audit.json", "delta.json"],
  "auxiliaryArtifacts": ["rule-stack.json", "trace.json"],
  "missingArtifacts": ["polish.json", "analysis.json"],
  "recommendedAction": "resume from polish (or final-write if audit borderline)",
  "stalenessWarnings": []
}
```

字段释义：

| 字段 | 含义 |
|---|---|
| `nextChapter` | `manifest.lastAppliedChapter + 1`，即"应该写但还没 finalize"的章节号 |
| `latestPhase` | 已完成的最晚阶段，按下面的 phase 流水线判（仅看 PHASE_ARTIFACTS 列表里的关键产物） |
| `chapterFinalized` | 该章节正文已落 `chapters/{NNNN}.md` 且 manifest 已推进——若为 true，说明上次循环跑完了，没什么要恢复的 |
| `presentArtifacts` | 当前实际存在的 `chapter-{NNNN+1}.*` 关键产物（按字母序） |
| `auxiliaryArtifacts` | 同章节的辅助产物（`rule-stack.json` / `trace.json`，不影响 latestPhase 判定） |
| `missingArtifacts` | 流水线 8 个关键产物中缺的部分（用于人类速览） |
| `recommendedAction` | 文字版下一步动作建议；建议你照这个进入主循环对应阶段 |
| `stalenessWarnings` | 任何已存在产物的 mtime 超过 7 天 → 列出来；老的半成品多半是历史遗留，重新做更安全 |

## phase 流水线（产物 ↔ 阶段映射）

```
intent.md       ← Phase 02 Planner          (00-orchestration step 2)
context.json    ← Phase 03 Composer         (step 3)
draft.md        ← Phase 05 Writer           (step 5/5b 拆完 sentinel 后)
normalized.md   ← Phase 08 Normalizer       (step 6 长度治理后)
audit.json      ← Phase 09 Auditor          (step 7 audit-revise 回环最后一次)
delta.json      ← Phase 07 Settler          (step 9，apply_delta 之前的 delta JSON)
polish.json     ← Phase 11 Polisher         (step 10.5)
analysis.json   ← Phase 13 Chapter Analyzer (step 11.05)
```

每条文件落盘标志着前面那个 phase 已完整跑完（Composer 三件套 `context/rule-stack/trace` 同时落盘——只检 `context.json` 即可）。

`final` 不是单独一个 runtime 文件，而是 `chapters/{NNNN+1}.md` 存在 + manifest 推进过的复合状态——见 `chapterFinalized` 字段。

## 4 个典型场景

### 场景 1：Writer 中途挂了

```
nextChapter: 7
latestPhase: "context"
presentArtifacts: ["intent.md", "context.json"]
recommendedAction: "resume from Write"
```

**做什么**：直接进 [Phase 05 Writer](phases/05-writer.md)，把 `chapter-0007.context.json` + `rule-stack.json` 当输入装回 prompt 重写。intent / context 仍然是有效的，不用重跑 Planner / Composer。

### 场景 2：audit-revise 回环卡住，已经退出但 delta 没产

```
nextChapter: 12
latestPhase: "audit"
presentArtifacts: ["intent.md", "context.json", "draft.md", "normalized.md", "audit.json"]
missingArtifacts: ["delta.json", "polish.json", "analysis.json"]
recommendedAction: "resume from Settle (Observe + delta)"
```

**做什么**：从 [Phase 06 Observer](phases/06-observer.md) → [Phase 07 Settler](phases/07-settler.md) 续跑。注意：上次 audit-revise 回环可能已经是 `audit-failed-best-effort`，要看 `audit.json` 里 `overall_score` 决定是否还要再 revise 一轮再进 Settler。

### 场景 3：`apply_delta.py` 失败，delta JSON 在但没落到真理文件

```
nextChapter: 5
latestPhase: "delta"
presentArtifacts: ["intent.md", "context.json", "draft.md", "normalized.md", "audit.json", "delta.json"]
recommendedAction: "resume from Polish (or final-write if audit borderline)"
```

**做什么**：先单独跑 `python scripts/apply_delta.py --book <dir> --delta story/runtime/chapter-0005.delta.json` 看是治理 critical 还是 schema 不合规。修好再继续 Polisher（如有过线）→ 写盘。**不要**直接进 Polisher——真理文件还没更新。

### 场景 4：上次跑完整、这次只是来检查

```
nextChapter: 13
latestPhase: "none"
presentArtifacts: []
chapterFinalized: false
recommendedAction: "start from Plan (no runtime artifacts present)"
```

**做什么**：什么都没有，干净起步——直接进主循环 Phase 02 Planner。

如果输出的是 `chapterFinalized: true`，说明 manifest 已推进、上一章是完整 finalize 的——这时 `nextChapter` 实际上是新一章，干净起跑即可。

## stalenessWarnings 的处理

如果你看到 `"draft.md is 12.3 days old (> 7d) — likely stale"`：那十有八九是几周前断的、设定 / hooks 都已经飘移过——按几率 reasonable 的处理是：

```bash
python {SKILL_ROOT}/scripts/recover_chapter.py --book <dir> --clean
```

砸掉重做。比起接着旧 draft 强续，重写一遍更稳。

## 与 `apply_delta.py` 的关系

apply_delta 的 `--skip-hook-governance` 和本脚本是**正交**的：

- 本脚本只检测 runtime/ 里有什么文件，不调 hook governance、不验 schema、不修 manifest
- 真要 resume，最终还是要让 apply_delta 落 delta、让主循环按 phase 走 audit-revise

本脚本是**read-only 诊断器**（除非加 `--clean`），不是 resume 自动驾驶。它告诉你断在哪、建议怎么做；继续跑由你决定。
