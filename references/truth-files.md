# Truth Files（真理文件总览）

> 一本书 (`books/<id>/`) 里所有"被当作权威"的文件都在这。哪些是真权威、哪些是渲染视图、哪些是模板，一表说清。新增任何"持久状态"文件时回来对账。

## 5 类真理文件

| 类 | 含义 | 写入者 | 例子 |
|---|---|---|---|
| ① author-constitution | 作者宪法，运行时永不被自动改 | 仅人工 / `init_book` 时 Architect 一次成型 | `book.json#bookRules`、`story/author_intent.md`、`story/fanfic_canon.md`、`story/parent_canon.md` |
| ② 段落型真理 | 章节 / 卷 / 风格的 prose-style 配置 | docOps（`sourcePhase=settler/auditor-derived/user-directive`）| `story/current_focus.md`、`story/style_guide.md`、`story/outline/story_frame.md`、`story/outline/volume_map.md`、`story/roles/<层级>/<slug>.md` |
| ③ 表格型真理 | 多行结构化状态，按键列 upsert | docOps（settler 内部转译路径走同一通道，见下） | `story/character_matrix.md`、`story/emotional_arcs.md`、`story/subplot_board.md` |
| ④ JSON 状态权威 | 跨章追加的纯结构化数据 | apply_delta（`currentStatePatch` / `hookOps` / `chapterSummary`）| `story/state/current_state.json`、`story/state/hooks.json`、`story/state/chapter_summaries.json`、`story/state/manifest.json` |
| ⑤ 视图渲染 | 由 ④ 同步 render 的人类可读 md | apply_delta（json 写完后渲染 md）| `story/current_state.md`、`story/pending_hooks.md`、`story/chapter_summaries.md` |

> ⑤ 与 ④ 一一对应、由 apply_delta **每章自动同步** ── md 不是真权威、也不是缓存；想改就改 json，重新跑 apply_delta 渲染会覆盖 md。`runtime-state-delta.md` §"writeTargets" 是这一约定的合同。

## 写入通道

```
作者 / Claude（手工 Edit）
        │
        ├─→ ①  author-constitution（permitted: 仅 init / 手工）
        │
        │   docOps（delta.docOps.* with sourcePhase）
        ├─→ ②  段落 md（current_focus / style_guide / outline / roles）
        │
        │   docOps（settler 内部把 *Ops 转译成 docOps；其它 sourcePhase 直接进）
        └─→ ③  表格 md（character_matrix / emotional_arcs / subplot_board）

Settler delta（apply_delta）
        │
        ├─→ ④  JSON state（currentStatePatch / hookOps / chapterSummary）
        │       │
        │       └─→ ⑤  render md（current_state / pending_hooks / chapter_summaries）
        │
        └─→ ③  也可以走（内部转译为 docOps batch）
```

不在表里的"看上去像状态"的文件：

- `story/runtime/*` — 章内中间件；写完一章就归档/清理，不入 snapshot 不入读上下文。
- `story/runtime/views/*` — `state_project.py --write` 出的快照视图；只读派生，可随时删。
- `story/snapshots/<NNNN>/` — 章末整书快照，由 `snapshot_state.py` 管。
- `story/audit_drift.md` — auditor 的 transient 笔记，不入 snapshot；与 ④/⑤ 解耦。
- `story/raw_writer/*` — Writer 阶段的原始落稿快照，不属真理。

## 派生（projection）≠ 真理

`scripts/state_project.py` 现算 4 种 view（`characters-in-scene` / `hooks-grouped` / `emotional-trajectories` / `subplot-threads`），**不写盘**（除非用 `--write` 显式落到 `story/runtime/views/<view>-c<NNNN>.md`）。

| 这些是真理 | 这些是派生 |
|---|---|
| `state/chapter_summaries.json` | `view: characters-in-scene`、`emotional-trajectories(fallback)`、`subplot-threads.activity` |
| `state/hooks.json` | `view: hooks-grouped` |
| `emotional_arcs.md` | `view: emotional-trajectories` |
| `subplot_board.md` | `view: subplot-threads`（threads + 活动） |

详见 [state-projections.md](state-projections.md)。

## 黑名单常量（① 类的代码登记处）

`scripts/_constants.py#AUTHOR_CONSTITUTION_PATHS` 是禁写名单的单一来源，被以下两处共用：

- `scripts/doc_ops.py#DOC_OPS_BLACKLIST` ── docOps batch 拒绝
- `scripts/apply_delta.py` 里"author-constitution direct-Edit guard" ── 直接 Edit 拒绝

新增 ① 类文件时**只改 `_constants.py`**。

## 设计原则

1. **JSON 是结构化真理，md 是人类可读视图**。读取脚本默认读 JSON（少数兜底读 md，例如 `pov_filter` 在 json 缺失时退读 `chapter_summaries.md`）。
2. **每张表/状态只能有一条写入路径**。② 类只走 docOps；④ 类只走 apply_delta；③ 类走 docOps（settler 的 `*Ops` 在 apply_delta 内部转译成 docOps）。
3. **派生不写盘**（除非显式 `--write`）。projections 永远是只读现算。
4. **作者宪法在两道关卡都禁写**：schema 层（`DOC_OPS_BLACKLIST`）+ 直接 Edit 守卫。

## 排查清单

| 现象 | 先看 |
|---|---|
| md 视图与 json 不一致 | `apply_delta` 是否成功跑完？看 `_meta.json` 与最近 `runtime/`；不一致就重跑 apply_delta（幂等） |
| 表格 md 出现重复行 | 是否 settler delta 同时填了 `*Ops` 与 `docOps.同表`？转译层应避免重复 |
| docOps 整批被拒 | `book.json#docOpsConfig.allowSourcePhases` / `denyPaths` 检查；① 类禁写名单也会触发 |
| 角色卡 slug 冲突 | `roles/_resolve_role_path` 用 `references/role-template.md` 作模板；slug 必须 kebab-case |

## 何时新增真理

- 用户提需求 → 看是 ② / ③ / ④ 哪一类 → 加进对应通道（docOps whitelist / apply_delta json 字段 / 渲染表）。
- **不要**新建一个 md 让脚本各自往里追加—— ③ 类的双写 race 就是这么来的。
