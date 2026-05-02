# State Projections（真理文件压缩视图）

> Composer 默认读原始真理文件（`character_matrix.md` / `pending_hooks.md` / `chapter_summaries.json` / 等）拼 `context_package`。但 30+ 章后这几份文件会越来越胖，整段灌进去会挤掉给 Writer 的正文 token。State Projections 是**预算紧时**的替代——按常用查询维度算一份压缩视图，给 Composer 装进 selectedContext。

projections **不是缓存**——每次现算、不写盘。它们也**不替代真理文件**——Settler 仍然只改原始文件，apply_delta 仍然按原 schema 走。

## 4 种 view

| view | 主要回答 | 主要数据源 |
|---|---|---|
| `characters-in-scene` | 最近 N 章谁出场过、出场频率、主导情绪 | `chapter_summaries.json` |
| `hooks-grouped` | 钩子按主线 / 支线 / 孤立分组 | `hooks.json`（看 `coreHook` / `tags` / `subplotId` / `dependsOn`）|
| `emotional-trajectories` | 每个角色的（章, mood, intensity）时间序列 | `emotional_arcs.md`（缺则从 chapter_summaries 推断）|
| `subplot-threads` | 每条支线 + 它的活跃记录 + 关联钩子 | `subplot_board.md` + 关联 hooks |

## 用法

```bash
python {SKILL_ROOT}/scripts/state_project.py \
  --book <bookDir> \
  --current-chapter N \
  --view characters-in-scene|hooks-grouped|emotional-trajectories|subplot-threads \
  [--window 10] \
  [--json|--markdown]
```

- `--current-chapter`：参考章；所有 view 严格只看 `chapter < N` 的历史
- `--window`：lookback 章数（默认 10），仅对 `characters-in-scene` / `emotional-trajectories`（fallback 模式）有意义
- `--markdown`：输出可直接贴进 prompt 的 markdown 摘要；不带时输出 JSON

## 各 view 的 schema

### characters-in-scene

```json
{
  "view": "characters-in-scene",
  "currentChapter": 50,
  "window": 10,
  "windowChapterRange": [41, 49],
  "characters": [
    {"character": "林秋", "chaptersAppeared": [41,42,43,44,45,46,47,48,49],
     "lastChapter": 49, "appearanceCount": 9, "dominantMood": "压抑"},
    {"character": "周九影", "chaptersAppeared": [42,45,48],
     "lastChapter": 48, "appearanceCount": 3, "dominantMood": "戒备"}
  ]
}
```

排序：appearanceCount 降序 → lastChapter 降序 → 角色名升序。

### hooks-grouped

```json
{
  "view": "hooks-grouped",
  "mainLine": [
    {"hookId": "H001", "type": "...", "status": "near_payoff",
     "startChapter": 1, "lastAdvancedChapter": 48, "expectedPayoff": "...",
     "subplotId": null, "dependsOn": [], "tags": ["main"], "coreHook": true}
  ],
  "subPlots": [...],
  "orphans": [...],
  "subplotIndex": {"S004": ["H012", "H019"], ...},
  "totals": {"mainLine": 3, "subPlots": 8, "orphans": 2}
}
```

分组规则（按优先级）：

1. **mainLine**: `coreHook == true` OR `tags` 含 "main" / "主线"
2. **subPlots**: `subplotId` 非空 OR `tags` 含 "sub" / "支线" OR `dependsOn` 非空
3. **orphans**: 都不是——这通常是 _需要清理_ 的钩子

### emotional-trajectories

```json
{
  "view": "emotional-trajectories",
  "currentChapter": 50,
  "window": 10,
  "source": "emotional_arcs.md",
  "characters": [
    {"character": "林秋", "samples": 8,
     "trajectory": [
       {"chapter": 42, "mood": "焦灼", "intensity": 7, "direction": "rising", "trigger": "..."},
       {"chapter": 45, "mood": "克制", "intensity": 8, "direction": "stable", "trigger": "..."},
       ...
     ]}
  ]
}
```

如果 `emotional_arcs.md` 没数据，`source` 会变成 `"chapter_summaries.json#mood (inferred)"`，per-character mood 从 summaries 推（缺 intensity / direction）。

### subplot-threads

```json
{
  "view": "subplot-threads",
  "currentChapter": 50,
  "threads": [
    {"subplotId": "S004", "name": "周九影身世",
     "status": "active", "lastAdvancedChapter": 48,
     "characters": "周九影,林秋", "notes": "...",
     "activity": [
       {"chapter": 12, "title": "...", "events": "..."},
       {"chapter": 27, "title": "...", "events": "..."}
     ],
     "relatedHooks": [{"hookId": "H012", "status": "near_payoff", "lastAdvancedChapter": 45}]
    }
  ],
  "totals": {"subplots": 5, "active": 3}
}
```

排序：active 在前 → lastAdvancedChapter 降序 → subplotId 升序。

## Composer 何时用

详见 `references/phases/03-composer.md` 里追加的 §6 节。简版判断：

- 默认按原表 §2 装 raw 真理文件
- 当任意一项触发，改用 projection 替换原 row：
  - `chapter_summaries.json` 行数 > 100（context window 压力大）→ 用 `characters-in-scene` 替原 row 9-11
  - `pending_hooks.md` 行数 > 30（hook 池胀大）→ 用 `hooks-grouped` 替原 row 16
  - 当章 chapter_memo 含 `cliffResolution`（即将兑现核心 hook）→ 加跑 `subplot-threads` 看上下游
  - 当章 chapter_memo `arcTransition` 或 audit drift 提到"角色情绪平/单一" → 加跑 `emotional-trajectories`

具体 view 选择优先级：`hooks-grouped` > `characters-in-scene` > `subplot-threads` > `emotional-trajectories`。前两者最常用、收益最高。

## 新鲜度（freshness）

projections **始终现算**——没有持久化、没有 timestamp、没有 invalidation。每次 `state_project.py` 跑一遍读最新真理文件状态。Composer 不要把 projection JSON 缓存到 runtime/——下章再用要重跑。

## 与 truth files 的关系

| 真理文件 | 哪些 view 在读 | 写不写它 |
|---|---|---|
| `chapter_summaries.json` | characters-in-scene / emotional-trajectories(fallback) / subplot-threads | **永不写**——只 Settler 改 |
| `hooks.json` | hooks-grouped / subplot-threads | **永不写** |
| `emotional_arcs.md` | emotional-trajectories | **永不写**——apply_delta 走 emotionalArcOps |
| `subplot_board.md` | subplot-threads | **永不写**——apply_delta 走 subplotOps |
| `character_matrix.md` | （目前不读） | **永不写** |

projections 是**单向只读派生**。如果发现 view 出来的数据"奇怪"——比如某角色出场 0 次但你印象里他常露面——那是 chapter_summaries 的 `characters` 字段被 Settler 漏写了，要回去看 [Phase 07 Settler](phases/07-settler.md) 的 chapter_summaries op 而不是改 view。

## 注意事项

- `--current-chapter` 卡得严：所有 view 仅看 chapter < N 的数据。如果你想看截至本章（含 N）的状态，传 `N+1`
- `hooks-grouped` 不依赖 chapter_summaries——纯从 `hooks.json` 推断分组；hooks 文件不全则结果不全
- `emotional-trajectories` 在 fallback 模式下**没有 intensity / direction**（chapter_summaries 没记），如果作者要做情感走线分析，建议先把 `emotional_arcs.md` 填起来（让 Observer / Settler 出 emotionalArcOps）
- `subplot-threads` 的 `activity` 是**子串匹配**——如果 subplotId 是 `S1`，恰好 events 文本里有"S1架构"无关字样，会误中。建议 subplotId 用 ≥ 3 字符且语义独立的 token
- 这是**确定性**脚本，不调 LLM；产出可重现
