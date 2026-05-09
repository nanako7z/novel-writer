# 术语表（伏笔 / hook / cliffhanger）

避免散落在多 phase 文档里的同义词混淆，以本表为准。

| 术语 | 含义 | 来源 / 存放 | 谁更新 |
|---|---|---|---|
| **伏笔（hook）** | 一条延续到后续章节、有具体回收方向的未解叙事承诺 | `story/pending_hooks.md`（人读）+ `story/state/hooks.json`（机读，权威） | Settler 走 `apply_delta` 落 hookOps |
| **hookCandidate** | 本章新出现、尚未拿到正式 hookId 的候选 | Settler delta 的 `newHookCandidates` 字段 | Settler 产候选；`hook_governance promote-pass` 仲裁推升为正式 hook |
| **payoffTiming** | hook 的**语义节奏档位**（不是具体章号）：`immediate` / `near-term` / `mid-arc` / `slow-burn` / `endgame`。**禁止**写章号 | `hooks.json` 字段 | Settler 在 hookOps.upsert 里写；Architect 大改时重置 |
| **committedToChapter** | 给某条 hook 强绑定的**最迟兑现章号**（实指承诺） | `hooks.json` 字段（可选） | Planner 写 `## 本章 hook 账` 时声明；commitment_ledger 校验本章是否兑现 |
| **cliffhanger** | 章末勾子（章末最后一段的收尾形态），12 类枚举 + intensity 1-5 | `story/state/cliffhanger_history.json` | Settler 必输出 `cliffhangerEntry`；Planner 读最近 6 条防套路重复 |
| **foreshadow / 揭 1 埋 1** | 写作动作（"每章揭 1 旧 hook + 埋 1 新 hook"的节奏目标）；不是数据字段 | Planner / Auditor 维度内部 | 不直接落盘，体现在 hookActivity 字段 |

**简言之**：

- 伏笔 = hook（同一概念中英对照）
- `hooks.json` 是权威，`pending_hooks.md` 是人读视图
- `payoffTiming` 是档位不是章号
- `cliffhanger` 是章末写法分类，与 hook 是不同维度——一个 cliffhanger 章可以同时埋 / 揭多条 hook
