# State Snapshots（真理文件章节级备份）

> 每章正文落盘 + manifest 推进后，把当时的"7 个 markdown 真理文件 + 4 个 state JSON"打包到 `story/snapshots/<NNNN>/`。如果后续章节把真理状态写脏了（比如 hook 推升出错、character_matrix 被错误覆盖、consolidate 漏归档），你能直接 restore 回某一章那个时刻——比手动还原稳得多。

> 这是工作流一致性问题 #1 的修复：inkos 源码里 `state/manager.ts:296+` 的 `snapshotState` / `snapshotStateAt` 在每次 `chapter-persistence.ts:74` 章节落盘时调用；本 SKILL 之前没有对应实现，缺的就是这个安全网。

## 何时调用

- **主循环 step 11.0a**（编排器自动跑）：`chapter_index.py add` 之后、Chapter Analyzer 之前。
- **Consolidate 之前**（手动 + `--milestone`）：consolidator 重写 `chapter_summaries.json` 是有损操作，跑 phase 12 前先做一次 milestone snapshot。
- **任何手动改真理文件之前**：用户手贱想直接编 `story/state/*.json`（**不该这么做**，但万一），先打一个 snapshot 兜底。
- **断点恢复后**（`recover_chapter.py --clean` 删 runtime 前）：如果你担心 runtime 半成品已经污染了真理文件——其实不会，但偏执也无妨。

不要在以下场景调它：

- 章节落盘失败（`audit-failed-best-effort` 或 `post-write-validate-failed`）——根本没到 step 11，不用 snapshot；下次 retry 会重写 runtime/。
- 章节正文还没落盘的中间阶段——这时真理文件还没动，没东西可备份。

> Snapshot vs. recover_chapter：**recover_chapter 处理 runtime/ 残留**（"还没成功的本章"），**snapshot 处理 story/ + state/ 已成功的过去**（"已成功的过去章节"）。两者互不替代——见 [chapter-recovery.md](chapter-recovery.md)。

## 存储布局

```
books/<id>/story/snapshots/<NNNN>/
├── current_state.md          ← 7 个 md 真理文件（按需，不存在的就跳过）
├── particle_ledger.md
├── pending_hooks.md
├── chapter_summaries.md
├── subplot_board.md
├── emotional_arcs.md
├── character_matrix.md
├── state/                    ← 4 个 state JSON
│   ├── chapter_summaries.json
│   ├── current_state.json
│   ├── hooks.json
│   └── manifest.json
└── _meta.json                ← 元数据 + sha256 完整性
```

`<NNNN>` 是 4 位零填充的章节号（与 `chapters/{NNNN}.md` 一致；inkos 源码用 bare int，本 SKILL 选择对齐其它产物的命名）。`list` 子命令同时容忍 bare int 命名以兼容老 snapshot。

## `_meta.json` 格式

```json
{
  "chapter": 7,
  "createdAt": "2026-05-02T12:34:56.789Z",
  "note": "before consolidate vol 1",
  "milestone": false,
  "sha256": {
    "current_state.md": "ab12...",
    "pending_hooks.md": "cd34...",
    "state/manifest.json": "ef56..."
  },
  "sourceManifest": { "lastAppliedChapter": 7, ... },
  "files": {
    "markdown": ["current_state.md", "pending_hooks.md", ...],
    "state":    ["manifest.json", "hooks.json", ...]
  }
}
```

- `chapter`：章节号
- `createdAt`：UTC ISO8601（毫秒精度）
- `note`：可选作者注（"before consolidate"、"prod backup before risky edit"）
- `milestone`：true 表示 prune 时永不删（consolidate 触发的"里程碑 snapshot" 用这个）
- `sha256`：每个文件的 SHA-256，用于 `show` / `restore` 的完整性校验。键是相对 snapshot 目录的路径（md 文件直接用文件名；state JSON 加 `state/` 前缀）
- `sourceManifest`：snapshot 时刻 `story/state/manifest.json` 的完整副本（冗余，但方便快速看 "snapshot 时 lastAppliedChapter 是几"）
- `files`：实际落进 snapshot 的 markdown / state 文件名列表（既是清单也是按需快速 enumerate 用的）

## 完整性校验（sha256）

`show` 命令会对所有 `_meta.sha256` 里登记的文件重算 SHA-256 比对：

- 文件缺失 → `{rel}: file missing`
- 哈希不一致 → `{rel}: sha256 mismatch`

任何 issue 都填 `integrityIssues` 数组，`integrityOk = (issues == [])`。

`restore` 默认会先做完整性校验；失败直接 exit 2，附带 `--force` 提示。**这是给"snapshot 目录被人手动改过 / 误操作 rm 掉一个文件"留的兜底**——正常 restore 不会触发。

## Restore 语义

```bash
python scripts/snapshot_state.py --book <bookDir> restore --chapter N \
    [--target <bookDir>] [--dry-run] [--force]
```

- 把 snapshot 里的 7 个 md + 4 个 state JSON **覆盖**到 `<target>/story/` 与 `<target>/story/state/`。
- `--target` 不给就默认 `--book`；给的话可以"把书 A 的 snapshot N restore 到书 B 的目录"——方便做 fork / 分支实验。
- **会被覆盖的**：snapshot 里有的 7 个 md + 4 个 state JSON。
- **不会被动**：`chapters/*.md`、`chapters/index.json`、`story/runtime/*`、`story/raw_writer/*`、`book.json`、`story/author_intent.md` / `story/fanfic_canon.md` / `story/parent_canon.md`（作者宪法，永不被动）。
- **新规则（v1.4 起）**：`story/current_focus.md` / `story/style_guide.md` / `story/character_matrix.md` / `story/emotional_arcs.md` / `story/subplot_board.md` / `story/outline/*` / `story/roles/*` 现在**会**被 `docOps` 通道（[runtime-state-delta.md §7b](schemas/runtime-state-delta.md)）修改；它们的 docOps 中间态备份在 `story/runtime/doc_ops.bak/<NNNN>/`，每次落盘有 `doc_changes.log` 留痕。`snapshot_state.py` 的行为本身**不变**——它依然按章打整书快照；docOps 的 `.bak` 是更细粒度的中间安全网（保留最近 5 章），互不干扰。
- snapshot 里**没有**的可选 md（比如 `particle_ledger.md` 在没数值系统的题材下不存在）会**从 target 删掉**——这与 inkos `restoreState` 行为一致：snapshot 是"完整快照"，缺即删。
- `current_state.md` 与 `pending_hooks.md` 是 required；snapshot 缺这两个会拒绝 restore（除非 `--force`）。

### 安全闸门

- 当前 `target/story/state/manifest.json#lastAppliedChapter > N` → restore 会"丢章节"（章节正文还在，但真理文件回到 N）。默认 exit 2 拒绝；想清楚后用 `--force` 通过，或先 `--dry-run` 看影响。
- snapshot 完整性校验失败 → exit 2 拒绝，`--force` 通过。
- `--dry-run`：列出每个文件 `{action, rel, changed, oldSize, newSize}`，不写盘。

### 不会自动同步的事

- `chapters/index.json` 不会跟 manifest 一起回退——如果你 restore 到 chapter 5 但 chapters/0006.md ~ 0010.md 还在硬盘上，**章节正文与真理状态会脱节**。这是有意的：作者可能想保留废稿做参考。要彻底回退请手动删多余的 chapter 文件 + `chapter_index.py` 删条目。
- `story/runtime/*` 不会被动——下次写 chapter (N+1) 时该清还是会清。

## Diff（两个 snapshot 之间）

```bash
python scripts/snapshot_state.py --book <bookDir> diff --from N --to M [--file <name>]
```

字节级比对。每个文件输出：

```json
{
  "file": "pending_hooks.md",
  "changed": true,
  "oldSize": 1234,
  "newSize": 1567,
  "summary": "size 1234 -> 1567 (delta +333)"
}
```

- `changed` 是 sha256 比对结果（不是简单看大小）。
- `summary` 取 `unchanged` / `added` / `removed` / `size X -> Y (delta ±N)` 之一。
- `--file` 缩到单一文件（便于 "为啥这章 hooks 突然变了" 的定位）。

不做行级 diff——那留给 git / `diff -u`。这里只回答 "哪个文件变了 / 变了多少字节"。

## Prune（保留策略）

```bash
python scripts/snapshot_state.py --book <bookDir> prune --keep-last K [--dry-run]
```

- 保留**所有** `milestone: true` snapshot（永不删）。
- 在非 milestone 中，按章节号升序，保留最近 K 个。
- 其余删掉（`--dry-run` 只列不删）。

### 默认策略建议

| 场景 | 推荐 |
|---|---|
| 日常每章自动 snapshot | 保留最近 30 章（`--keep-last 30`） |
| Consolidate 之前 | 手动 `create --chapter <last> --milestone --note "pre-consolidate vol N"` |
| 大改架构之前 | 同上 milestone |
| 项目长期归档（>100 章） | 周期性 prune `--keep-last 30`；milestone 永留 |

每章 snapshot 实际占用：7 个 md（每个几 KB）+ 4 个 state JSON（chapter_summaries.json 最大、随章节累积）。300 章累积约几十 MB；prune 不是必须，是给在意磁盘的人准备的。

## 与其它脚本的协作

- `apply_delta.py` 不调 snapshot——snapshot 在 chapter 落盘之后才有意义。
- `recover_chapter.py` 看 `story/runtime/*` 半成品；不看 snapshot。两者**互不重叠**。
- `consolidate_check.py` 应该建议作者"先打一个 milestone snapshot 再跑 phase 12"——这是 phase 12 文档里的责任，本 snapshot 脚本只提供能力。
- `chapter_index.py` 与 snapshot 互不耦合——前者管运营索引，后者管真理状态备份。

## 关键不变量

1. **snapshot 是只读历史**：`create` 之后 snapshot 目录不应再被改动。`show` 的完整性校验就是用来在改动后报警的。
2. **create 是原子的**：staging 在 `<NNNN>.tmp/`，全部文件落盘 + `_meta.json` 写入后才 rename 成 `<NNNN>/`。中途崩了顶多留一个 `.tmp` 残留，不会让 list 看到半成品。
3. **create 是幂等的**：同一个 chapter 多次 create 后写覆盖（带新 `createdAt` 与 `note`）——不会 append、不会报错。需要保留多个版本就用不同 chapter 号或 milestone 标记。
4. **restore 不动 runtime 与 chapters/**：见 "Restore 语义"。
5. **stdlib only**：本脚本不依赖第三方包，可在最小环境下跑。
