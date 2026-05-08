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

## 1b. Parser stages（3 段式解析）

`scripts/settler_parse.py`（共享模块）+ `scripts/apply_delta.py --input-mode raw` 实现 inkos 同款 3 段式解析，目的是让 Settler 的小格式偏差不必整章重跑。

```
Settler raw chat output
    │
    ▼  Stage 1 — 宽松抽取（lenient_extract）
       从 === RUNTIME_STATE_DELTA === / END 哨兵之间抽 JSON 块；
       兼容缩进、缺少 END、外层 ```json```、纯 JSON 文件、嵌入 {…} 块
    │
    ▼  Stage 2 — soft-fix 归一化
       按 alias 表自动修复常见格式偏差，写入 softFixes 数组
    │
    ▼  Stage 3 — 严格 schema 校验（validate_delta）
       仍失败 → 返回 parserFeedback（中文反馈块）供 Settler 重试
    │
    ▼  apply（仅 apply_delta.py）
       parseStage 走到 "applied"
```

每个返回 JSON 都带：

| 字段 | 含义 |
|------|------|
| `parseStage` | `extracted` \| `softfix` \| `schema` \| `applied`——卡在哪一段 |
| `softFixes`  | 自动修复列表，每条 `{ path, fix, from?, to? }` |
| `parserFeedback` | 失败时给 Settler 的中文反馈块；成功时为 `""` |

### Soft-fix 修复表

`settler_parse.py` 在 Stage 2 自动修复以下偏差，**不**触发 Settler 重跑，但全部记录到 `softFixes`：

| 类别 | 示例（错） → 修正后 | 备注 |
|------|---------------------|------|
| 顶层键 alias | `chapterNumber` / `chapter_number` → `chapter` | TOP_KEY_ALIASES |
|              | `state_patch` / `statePatch` / `current_state_patch` → `currentStatePatch` | |
|              | `hook_ops` → `hookOps`；`new_hook_candidates` → `newHookCandidates` | |
|              | `chapter_summary` → `chapterSummary` | |
|              | `subplot_ops` / `emotional_arc_ops` / `character_matrix_ops` → camelCase | |
| HookRecord 键 alias | `hook_id` / `hookid` → `hookId` | HOOK_RECORD_ALIASES |
|                     | `start_chapter` → `startChapter`；`last_advanced_chapter` → `lastAdvancedChapter` | |
|                     | `expected_payoff` → `expectedPayoff`；`payoff_timing` → `payoffTiming` | |
| ChapterSummary alias | `chapter_type` → `chapterType`；`hook_activity` → `hookActivity` | |
|                       | `state_changes` → `stateChanges` | |
| CurrentStatePatch alias | `current_location` → `currentLocation` 等所有 snake_case → camelCase | |
| 类型强转 | `"chapter": "12"` → `"chapter": 12` | 含 "第12章" / "12回" 类自然语言数字提取 |
|          | `"chapter": 12.0` → `"chapter": 12`（仅当 .0 整数浮点） | |
| 枚举大小写 | `"status": "Resolved"` → `"resolved"` | 仅当小写后落入 VALID_HOOK_STATUS / VALID_PAYOFF_TIMING |
| 数组包装 | `"upsert": {单条}` → `"upsert": [{单条}]` | 同样适用 mention/resolve/defer / newHookCandidates / *_ops |
| 空值清理 | `"newHookCandidates": null` → 删除该键 | 同样适用 *_ops 数组 |
| notes 强转 | `"notes": "一行"` → `"notes": ["一行"]` | empty string → `[]` |
| 重复 alias | 同时出现 `chapter_number` 和 `chapter` → 丢弃 alias，保留 canonical | 记录 `drop_duplicate_alias` |

### Hard error（触发 Settler 重跑）

只有 Stage 3 仍失败的字段才作为 hard error 出现在 `parserFeedback` 中，例如：

- 必填字段缺失：`chapter` / `hookOps.upsert[0].hookId` / `chapterSummary.title` 等
- 类型完全错位：`chapter: "前言"`（无法提取数字）、`hookOps: "see notes"`（不是对象）
- 业务约束冲突：`chapterSummary.chapter !== 顶层 chapter`、`hookOps.upsert` 含未知顶层键
- 枚举值在 soft-fix 后仍非法：`status: "in-progress"`（不在 VALID_HOOK_STATUS 内）

`parserFeedback` 模板：

```
=== SETTLER_FEEDBACK ===
上一次输出的 RUNTIME_STATE_DELTA 有 N 处问题需要修正：
- $.<path>: 必填字段缺失，期望 <type>
- $.<path>: 你写了 "x"，但允许值是 <enum>
请仅修正这几处，其余字段保持原样重新输出 RUNTIME_STATE_DELTA。
=== END ===
```

如果连 Stage 1 都失败（沒有 `=== RUNTIME_STATE_DELTA ===` 哨兵 / 找不到任何 JSON 块），`parserFeedback` 改为提示 Settler 严格使用哨兵格式。

### 推荐流水线

```
Settler raw output
   │
   ▼ scripts/settler_parse.py --mode raw --input settlement.txt --out delta.json
   │   （只跑 Stage 1+2+3，不写真理文件；调试 / 离线检查用）
   ▼
delta.json (cleaned)
   │
   ▼ scripts/apply_delta.py --book <book> --delta delta.json
   │   （--input-mode 默认 json，对清洗后的 JSON 再跑一次 Stage 2+3 + 落盘）
   ▼
truth files updated
```

或单步走完：`scripts/apply_delta.py --book <book> --delta settlement.txt --input-mode raw` ——直接把 Settler 原始输出喂给 apply_delta，省掉中间文件。

`--input-mode` 取值：

| 值 | 含义 |
|----|------|
| `json`（默认） | 输入为干净的 RuntimeStateDelta JSON。跳过 Stage 1，仍跑 Stage 2 + 3。**完全向后兼容**——所有旧调用方无需改动。 |
| `raw` | 输入为 Settler 原始聊天输出（带 `=== POST_SETTLEMENT ===` / `=== RUNTIME_STATE_DELTA ===` 哨兵和前后散文）。运行完整 Stage 1 + 2 + 3。 |

`scripts/settler_parse.py` 单独 CLI（同样支持 `--mode raw|json`，加 `--out` 写清洗后的 JSON、加 `--strict` 让任何 softFix 也算失败）适合调试 Settler 输出而**不**触碰真理文件。

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
| `emotionalArcOps` | array<object> | 是（默认 []） | 情感弧线变动；每项**必填**：`character` (string), `chapter` (int≥1), `emotionalState` (string), `triggerEvent` (string), `intensity` (int 1-10), `arcDirection` (`rising`\|`falling`\|`stable`\|`turning`)。缺/空任一列会被拒。 |
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
| `committedToChapter` | int ≥ 1 | 否 | 章节级 forward-looking 承诺：planner / architect 显式声明本钩子最迟必须在第 N 章兑现。比 `payoffTiming` 的 4 档枚举更精确——后者只能给"slow-burn / mid-arc"等粗粒度档位。缺省 → 退回 `payoffTiming` 启发式。`scripts/commitment_ledger.py` 与 `scripts/hook_governance.py --command volume-payoff` 都读这个字段；committed-but-not-paid by chapter N → critical 闸门。设置入口：`hook_governance.py --book BK commit-payoff --hook-id H001 --chapter 47`（同义旧名 `committedPayoffChapter` 也兼容读，新写一律落 `committedToChapter`）。 |

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

## 7b. `docOps` 子 schema（指导 md 维护）

`docOps` 让 LLM 通过同一条 RuntimeStateDelta 通道**主动修改作者域指导 md**。和 `hookOps` 平级，由 `apply_delta.py → doc_ops.apply()` 落盘，写前自动 `.bak`，每条 op 在 `story/runtime/doc_changes.log` 留 NDJSON 痕迹，可通过 `apply_delta.py revert-doc-op --op-id <sha8>` 回滚。

### 顶层形状

| 字段 | 路由到 | op 类型 |
|------|--------|---------|
| `currentFocus` | `story/current_focus.md` | SectionReplaceOp |
| `styleGuide` | `story/style_guide.md` | SectionReplaceOp |
| `storyFrame` | `story/outline/story_frame.md` | SectionReplaceOp |
| `volumeMap` | `story/outline/volume_map.md` | SectionReplaceOp |
| `characterMatrix` | `story/character_matrix.md` | TableRowOp |
| `emotionalArcs` | `story/emotional_arcs.md` | TableRowOp |
| `subplotBoard` | `story/subplot_board.md` | TableRowOp |
| `roles` | `story/roles/<slug>.md` | RoleFileOp |

### 黑名单（永远禁止，schema-fail）

下列顶层键**不允许**出现在 `docOps` 里——它们是作者宪法，仅作者本人通过对话明示指令时由 LLM 直接 `Edit`：

- `authorIntent`（`story/author_intent.md`）
- `fanficCanon`（`story/fanfic_canon.md`）
- `parentCanon`（`story/parent_canon.md`）
- `bookRules`（`book.json#bookRules`）

### 通用必填字段（每条 op）

| 字段 | 类型 | 说明 |
|------|------|------|
| `op` | enum | 见各 op kind |
| `reason` | string ≤ 200 chars | 必填，本次修改的原因 |
| `sourcePhase` | enum | `settler` / `auditor-derived` / `architect` / `user-directive` |
| `sourceChapter` | int ≥ 0 | 当前章节号（user-directive 用 `manifest.lastAppliedChapter`） |

### `SectionReplaceOp`（散文型）

| 字段 | 类型 | 说明 |
|------|------|------|
| `op` | enum | `replace_section` / `append_section` / `delete_section` |
| `anchor` | string | 完整 H2/H3 行（如 `## Active Focus`），必须以 `## ` 或 `### ` 开头 |
| `newContent` | string | 新节正文；`replace_section` / `append_section` 必填 |

newContent 字符上限：`currentFocus` 2000 / `styleGuide` 3000 / `storyFrame` 5000 / `volumeMap` 5000。

### `TableRowOp`（结构化表格）

| 字段 | 类型 | 说明 |
|------|------|------|
| `op` | enum | `upsert_row` / `update_row` / `delete_row` |
| `key` | string \| array<string\|int> | 主键（`characterMatrix` = `[charA, charB]`；`emotionalArcs` = `[character, chapter]`；`subplotBoard` = `[subplotId]`） |
| `fields` | object | 列名→新值；`upsert_row` / `update_row` 必填 |

upsert 允许出现 header 中尚未存在的列名——doc_ops 会自动扩 header（其他行该列补空字串）。这是 append-only → upsert 的语义升级。

### `RoleFileOp`（角色档案）

| 字段 | 类型 | 说明 |
|------|------|------|
| `op` | enum | `create_role` / `patch_role_section` / `rename_role` / `delete_role` |
| `slug` | string | 文件名 stem（可中文）；映射到 `story/roles/<tier>/<slug>.md`。≤ 80 chars，不含 `/`、控制字符、不以 `.` 开头 |
| `tier` | enum | `主要角色` / `次要角色`，仅 `create_role` 用；省略默认 `次要角色` |
| `displayName` | string | 仅 `create_role` 用；写到文件 H1。省略默认与 `slug` 相同 |
| `initialContent` | string ≤ 4000 chars | 仅 `create_role` 用；省略则用 `references/role-template.md` |
| `anchor` | string | `patch_role_section` 必填，H2/H3 完整行 |
| `newContent` | string ≤ 4000 chars | `patch_role_section` 必填 |
| `newSlug` | string | `rename_role` 必填，文件名规则同 `slug` |

`delete_role`：删除整文件（写 `.bak` 后 `unlink`）。仅在确认该角色档案确实是误开 / 弃用时使用——如果只是阶段性退场，应该走 `patch_role_section` 改"## 当前现状"为"已退场"。**Settler 一般不主动 `delete_role`**——这通常是 user-directive 路径或 Architect cascade 时使用。

**Settler 一般不直接用 `create_role`** ——更推荐通过 `newRoleCandidates` 候选池让 `role_arbitrate.py` 决定 created / mapped / rejected。Architect cascade 与 user-directive 流程可以直接用 `create_role`（作者明示意图）。

### `newRoleCandidates`（顶层，与 `docOps` 平级）

由 Settler 写入；apply_delta 调 `role_arbitrate.py` 仲裁，admit 的转成 `docOps.roles[].create_role`，map 的产生 decision（不开新文件），reject 的丢弃（thin justification / roster full / stop name）。

```jsonc
"newRoleCandidates": [
  { "name": "白衣女子", "sourceChapter": 12, "justification": "ch12 首次有名出场，林秋拜师对象" }
]
```

字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 候选角色名（作为 slug + displayName） |
| `sourceChapter` | int ≥ 0 | 首次出场章节号 |
| `justification` | string ≥ 6 chars | 为什么这是真角色而非路人甲；< 6 chars 直接 reject |
| `tier` | enum | 可选；`主要角色` / `次要角色`，省略 = `次要角色` |

仲裁逻辑见 [scripts/role_arbitrate.py](../../scripts/role_arbitrate.py)：
- 名字归一化（去敬称：先生 / 师姐 / 大叔 等；去前缀：老 / 小 / 阿）
- bigram Jaccard 相似度 ≥ 0.5 → `mapped` 到已有角色（不开新文件）
- 名字为停用词（他 / 她 / 众人 等）→ `rejected`
- 满载（默认 30 角色）→ `rejected`
- justification < 6 chars → `rejected`

`--max-roster <N>` 调节满载阈值；`-1` 关闭。

### 全局上限

- 单次 delta 的 `docOps` 总条数 ≤ **20**（防 LLM 一次刷爆）；超限 schema-fail
- 单 op 的 `reason` ≤ 200 chars；超限 schema-fail
- 单 op 的 `newContent` 按上述 per-target 上限；超限 schema-fail

### 配置开关 `book.json#docOpsConfig`

```jsonc
"docOpsConfig": {
  "deny": ["story/style_guide.md"],          // 临时只读，apply 时直接 warning + 跳过
  "allowSourcePhases": ["settler","architect"]  // 限定 sourcePhase；缺省 = 全部允许
}
```

### `docOpsApplied` 输出与 `doc_changes.log`

apply_delta 输出 JSON 增 `docOpsApplied: [{file, op, anchor, reason, sourcePhase, backupPath, opId}]`。同时在 `story/runtime/doc_changes.log` 追加 NDJSON：
```jsonc
{"appliedAt":"2026-05-07T...","chapter":12,"file":"story/current_focus.md","op":"replace_section","anchor":"## Active Focus","reason":"ch12 兑现 X 后下一段","sourcePhase":"settler","backupPath":"story/runtime/doc_ops.bak/0012/story__current_focus.md.1.bak","opId":"a1b2c3d4"}
```

### 回滚

```bash
python scripts/apply_delta.py --book <bk> revert-doc-op --op-id <sha8>
```

从 `doc_changes.log` 反查 `backupPath`，原子还原。

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
| `docOps` | 作者域指导 md（白名单 8 个） | `doc_ops.apply()`，每条写前 `.bak`，应用后追加 `doc_changes.log` |
| `newRoleCandidates` | role-arbiter（P0 暂未实现，候选保留在 delta 内） | 决定 created / mapped / rejected |

校验后写入：先写 `<file>.tmp` → `os.rename` 原子替换，避免半成品污染。

---

## 9. 保留键 / 不可在 delta 里出现的键

以下顶层键 **不属于** RuntimeStateDelta，绝不能塞进 delta JSON：

- `manifest` / `schemaVersion` / `language` / `lastAppliedChapter` / `projectionVersion` / `migrationWarnings`——属于 `manifest.json`，由 apply_delta.py 自己维护
- `hooks`（裸数组）——必须包成 `hookOps.upsert`
- `facts`（裸数组）——必须包成 `currentStatePatch`
- `rows`（裸数组）——chapterSummary 一次只追一行
- `bookConfig` / `genreProfile` / `bookRules`——这些是只读输入；`bookRules` 也属作者宪法，仅作者明示指令时由 LLM 直接 `Edit book.json`，不走 delta
- `authorIntent` / `fanficCanon` / `parentCanon`——作者宪法（参考 §7b 黑名单），自动通道永不可写

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
