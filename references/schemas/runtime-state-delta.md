# RuntimeStateDelta（Settler 输出 schema）

Settler 阶段的输出契约。来源：`models/runtime-state.ts` L127-142（Zod schema）+ `agents/settler-prompts.ts` L88-159（输出格式）。

每章正文写完后，Settler 必须输出**一段 markdown 总结 + 一段严格 JSON delta**，由 `scripts/apply_delta.py` 校验后路由到对应真理文件。

---

## 1. Settler 输出包装格式

```
=== POST_SETTLEMENT ===
（简要说明本章有哪些状态变动、伏笔推进、结算注意事项；允许 Markdown 表格或要点）

=== RUNTIME_STATE_DELTA ===
（必须输出 JSON，不要输出 Markdown，不要加解释）
```json
{
  "chapter": 12,
  ...
}
```
```

> `apply_delta.py` 用正则 `=== RUNTIME_STATE_DELTA ===\\s*```json\\s*([\\s\\S]*?)```` 抽出 JSON 段做 schema 校验。

---

## 2. JSON 完整示例

```json
{
  "chapter": 12,
  "currentStatePatch": {
    "currentLocation": "雷脉密室",
    "protagonistState": "灵海受创，强行运功",
    "currentGoal": "拿到师叔的雷符残片",
    "currentConstraint": "三日内不可强行突破",
    "currentAlliances": "暂时联手二师姐",
    "currentConflict": "与赵执事的资源争夺"
  },
  "hookOps": {
    "upsert": [
      {
        "hookId": "mentor-oath",
        "startChapter": 8,
        "type": "relationship",
        "status": "progressing",
        "lastAdvancedChapter": 12,
        "expectedPayoff": "揭开师债真相",
        "payoffTiming": "slow-burn",
        "notes": "本章第二节，师叔留下半枚玉牌"
      }
    ],
    "mention": ["seven-gate-mark"],
    "resolve": ["lost-token"],
    "defer": ["distant-courier"]
  },
  "newHookCandidates": [
    {
      "type": "mystery",
      "expectedPayoff": "雷脉残片的来历",
      "payoffTiming": "near-term",
      "notes": "残片刻有"庚辰"二字，暗示前代弟子"
    }
  ],
  "chapterSummary": {
    "chapter": 12,
    "title": "雷脉密室",
    "characters": "林秋,二师姐,赵执事",
    "events": "林秋夺到雷符残片但灵海受创",
    "stateChanges": "新增临时盟友；与赵执事敌意公开化",
    "hookActivity": "mentor-oath advanced; lost-token resolved",
    "mood": "紧绷",
    "chapterType": "主线推进"
  },
  "subplotOps": [],
  "emotionalArcOps": [],
  "characterMatrixOps": [],
  "notes": []
}
```

---

## 3. 顶层 schema

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `chapter` | int ≥ 1 | 是 | 当前章节号，必须等于 Settler 处理的章节 |
| `currentStatePatch` | object | 否 | 当前态局部更新；省略=本章无 current_state 变动 |
| `hookOps` | object | 是（默认空 4 数组） | 伏笔变动操作 |
| `newHookCandidates` | array | 是（默认 []） | 候选新伏笔；不直接发明 hookId，由 hook-arbiter 决定映射或新建 |
| `chapterSummary` | object | 否 | 本章摘要行；省略=不追加摘要（极少见） |
| `subplotOps` | array<object> | 是（默认 []） | 支线进度变动；schema 宽松（`z.record(z.unknown())`） |
| `emotionalArcOps` | array<object> | 是（默认 []） | 情感弧线变动；schema 宽松 |
| `characterMatrixOps` | array<object> | 是（默认 []） | 角色交互矩阵变动；schema 宽松 |
| `notes` | array<string> | 是（默认 []） | 自由文本备注 |

---

## 4. `currentStatePatch` 子 schema

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `currentLocation` | string | 否 | 主角当前所在地 |
| `protagonistState` | string | 否 | 主角身体/精神状态简述 |
| `currentGoal` | string | 否 | 当前阶段目标 |
| `currentConstraint` | string | 否 | 当前阶段约束（不可做的事） |
| `currentAlliances` | string | 否 | 当前盟友/合作关系 |
| `currentConflict` | string | 否 | 当前主要冲突 |

> 全部字段都是可选——只更新本章变动的字段，不要重述未变项。

---

## 5. `hookOps` 子 schema

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `upsert` | array<HookRecord> | 是（默认 []） | 新增或推进伏笔；hookId 必须已在伏笔池中存在 |
| `mention` | array<string> | 是（默认 []） | 仅被提及但无状态变化的 hookId（不更新 lastAdvancedChapter） |
| `resolve` | array<string> | 是（默认 []） | 已回收的 hookId |
| `defer` | array<string> | 是（默认 []） | 标记延后的 hookId |

### `HookRecord` 子 schema

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `hookId` | string（非空） | 是 | 伏笔唯一 id |
| `startChapter` | int ≥ 0 | 是 | 伏笔首次出现的章节号 |
| `type` | string（非空） | 是 | 伏笔类型，如 `mystery` / `relationship` / `power` / `oath` |
| `status` | enum | 是 | `open` \| `progressing` \| `deferred` \| `resolved` |
| `lastAdvancedChapter` | int ≥ 0 | 是 | 最近一次推进/推进章节号；本章推进则等于 `chapter` |
| `expectedPayoff` | string（默认 `""`） | 是 | 预期回收方向 |
| `payoffTiming` | enum | 否 | `immediate` \| `near-term` \| `mid-arc` \| `slow-burn` \| `endgame` |
| `notes` | string（默认 `""`） | 是 | 本章为何推进/延后/回收 |
| `dependsOn` | array<string> | 否 | Phase 7：依赖的上游 hookId 列表 |
| `paysOffInArc` | string | 否 | Phase 7：兑现归属的 arc 标签 |
| `coreHook` | bool | 否 | Phase 7：是否核心伏笔（全书 3-7 条） |
| `halfLifeChapters` | int > 0 | 否 | Phase 7：建议半衰期章数 |
| `advancedCount` | int ≥ 0 | 否 | Phase 7：累计推进次数 |
| `promoted` | bool | 否 | Phase 7 hotfix 2：升级标志，由 architect-seed / consolidator 写入 |

---

## 6. `newHookCandidates` 子 schema

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string（非空） | 是 | 候选伏笔类型 |
| `expectedPayoff` | string（默认 `""`） | 是 | 预期回收方向 |
| `payoffTiming` | enum | 否 | 同 HookRecord |
| `notes` | string（默认 `""`） | 是 | 本章为什么形成新的未解问题 |

> **不要在这里写 hookId**——hook-arbiter 会决定它映射到旧 hook、变成真正新 hook、还是被拒绝为重述。

---

## 7. `chapterSummary` 子 schema

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `chapter` | int ≥ 1 | 是 | 必须等于顶层 `chapter` |
| `title` | string（非空） | 是 | 章节标题 |
| `characters` | string（默认 `""`） | 是 | 出场角色列表，逗号分隔 |
| `events` | string（默认 `""`） | 是 | 一句话概括关键事件 |
| `stateChanges` | string（默认 `""`） | 是 | 一句话概括状态变化 |
| `hookActivity` | string（默认 `""`） | 是 | 本章对 hook 的动作摘要，如 `mentor-oath advanced` |
| `mood` | string（默认 `""`） | 是 | 章节情绪标签 |
| `chapterType` | string（默认 `""`） | 是 | 章节类型，参考 `genre_profile.chapterTypes` |

---

## 8. 字段→真理文件路由表

| Delta 字段 | 路由到 | 写入方式 |
|------------|--------|---------|
| `chapter` | 校验用，不直接落盘 | apply_delta.py 校验 == lastAppliedChapter + 1 |
| `currentStatePatch` | `story/state/current_state.json` 的 `facts` / `current_state.md` | 局部覆盖对应字段；同步渲染 markdown |
| `hookOps.upsert` | `story/state/hooks.json` + `story/pending_hooks.md` | upsert 到 hooks 数组（相同 hookId 合并） |
| `hookOps.mention` | `story/state/hooks.json` | 不改 hooks，但记录 mention 计数（可选 metadata） |
| `hookOps.resolve` | `story/state/hooks.json` | 状态改 `resolved`，lastAdvancedChapter = chapter |
| `hookOps.defer` | `story/state/hooks.json` | 状态改 `deferred` |
| `newHookCandidates` | hook-arbiter（`utils/hook-arbiter.ts`）| 由仲裁器决定：映射旧 hook / 真新 hook / 拒绝 |
| `chapterSummary` | `story/state/chapter_summaries.json` + `story/chapter_summaries.md` | append 一行（先校验 chapter == delta.chapter） |
| `subplotOps` | `story/subplot_board.md` | 自由结构 op，由 reducer 解释 |
| `emotionalArcOps` | `story/emotional_arcs.md` | 同上 |
| `characterMatrixOps` | `story/character_matrix.md` | 同上 |
| `notes` | `story/state/manifest.json#migrationWarnings`（可选） | 也可作为本章日志 |

校验后写入：先写 `<file>.tmp` → `os.rename` 原子替换，避免半成品污染。

---

## 9. 保留键 / 不可在 delta 里出现的键

以下顶层键 **不属于** RuntimeStateDelta，绝不能塞进 delta JSON：

- `manifest` / `schemaVersion` / `language` / `lastAppliedChapter` / `projectionVersion` / `migrationWarnings`——属于 `manifest.json`，由 apply_delta.py 自己维护
- `hooks`（裸数组）——必须包成 `hookOps.upsert`
- `facts`（裸数组）——必须包成 `currentStatePatch`
- `rows`（裸数组）——chapterSummary 一次只追一行
- `bookConfig` / `genreProfile` / `bookRules`——这些是只读输入，不接受 delta 写入

任何未声明字段：宽松校验下保留（因为 `subplotOps` 等用 `z.record(z.unknown())`），但顶层若出现非 schema 键，apply_delta.py 给 warning。

---

## 9b. `hookOps.upsert` 与 promotion 子系统的关系

`hookOps.upsert` 不是"自由开新 hook 的入口"——它只能在**已被 promotion 子系统升级**的 hook 上动手。两条数据通道分得很清楚：

| 类别 | 写到哪 | 谁负责写 | 谁能读 |
|---|---|---|---|
| 原始 seed（architect / observer 候选，没升过级） | `story/runtime/hook-seeds.json` | architect、`hook_governance --command promote-pass` 维护 | composer 装 context 时**可以**读，但 `pending_hooks.md` 里**不会**出现 |
| 升级后的 hook（promoted=true） | `story/state/hooks.json`，并被 apply_delta 渲染到 `story/pending_hooks.md` | settler 通过 `hookOps.upsert` 推进；promote-pass 在升级时一次性补 `promoted=true` | writer / auditor / reviser 都读 |

### Settler 必须遵守的两条延伸规则

1. **`hookOps.upsert` 引用的 `hookId` 必须先在 `hooks.json` 中存在**。如果 settler 想推进的伏笔目前还只是 seed（即 `hooks.json` 里查不到），settler 应改写到 `newHookCandidates`，由后续的 `hook_governance --command promote-pass` 决定是否提升进 ledger。直接 upsert 一个仅存在于 `hook-seeds.json` 的 id 会被 apply_delta 警告（`stale_ledger_row`），并在下一次 validate 中复发。
2. **不要在 settler 里手设 `promoted=true`**。`promoted` 是治理层的输出而非 settler 输入；settler 透传旧值即可，新值由 promote-pass 写。

### 为什么这样设计

如果允许任何 seed 都直接落进 `pending_hooks.md`：
- 几章后 ledger 会被一次性观察候选淹没（observer 每章可能输出 5+ 候选）；
- 没有 dependsOn / coreHook / advanced ≥ 2 / cross-volume 任一信号的伏笔会停留为永久"信息噪声"，让 reviewer 误读；
- `chapter_summaries.json#hookActivity` 中的 token 集合会膨胀到无法对账。

promotion 闸口确保 ledger 里**每一条都至少有一个被读者持续追踪的理由**。详见 [`references/hook-governance.md`](../hook-governance.md)。

---

## 10. 校验硬规则（来自 settler-prompts.ts §"规则"块）

1. 只输出增量，不要重写完整 truth files
2. 所有章节号字段都必须是整数，不能写自然语言
3. `hookOps.upsert` 里只能写"当前伏笔池里已经存在"的 hookId，不允许发明新的 hookId
4. brand-new unresolved thread 一律写进 `newHookCandidates`，不要自造 hookId
5. 如果旧 hook 只是被提到、没有真实状态变化，把它放进 `mention`，不要更新 `lastAdvancedChapter`
6. 如果本章推进了旧 hook，`lastAdvancedChapter` 必须等于当前章号
7. 如果回收或延后 hook，必须放在 `resolve` / `defer` 数组里
8. `chapterSummary.chapter` 必须等于当前章节号

`apply_delta.py` 把以上规则全部固化为校验：违反任何一条都非零退出，错误细节走 stderr。
