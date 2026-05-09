# loop_state — step-checkpoint 强制机制

主循环 `writeNextChapter` 的防跳步基础设施。每个 step 进入前必调 `require`、完成必调 `mark`；跳步直接被 exit 3 阻止。

实现：[`scripts/loop_state.py`](../scripts/loop_state.py)。状态文件：`<book>/story/runtime/loop_state-{NNNN}.json`，章结束归档到 `loop_state.history/`。

## Step 顺序（与 [00-orchestration.md](phases/00-orchestration.md) 对齐）

`1, 2, 3, 4, 5, 5b, 5c, 6, 7, 7.5, 8, 9, 10, 10.1, 10.5, 11, 11.0a, 11.0b, 11.0c, 11.05, 11.1, 11.2`

**条件可跳过**（缺失不计 `end` 失败）：`4` / `7.5` / `10.5` / `11.0a` / `11.0b` / `11.0c` / `11.1` / `11.2`

**critical 必跑**（`end` 缺失即 exit 4）：`1, 2, 3, 5, 5b, 5c, 7, 9, 10, 10.1, 11, 11.05`

## 命令

| 命令 | 何时调 | 失败 |
|---|---|---|
| `loop_state.py begin --book <bd> --chapter N [--allow-replay]` | 主循环入口（preflight 通过后） | 已有 in-flight state 且未 end → exit 5 |
| `loop_state.py require --book <bd> --chapter N --step <id>` | 每个 step 进入前 | 缺前置 step → exit 3，stderr 列出哪几步漏了 |
| `loop_state.py mark --book <bd> --chapter N --step <id> [--artifact <path>]` | 每个 step 完成后；`--artifact` 校验文件存在 | artifact 缺失 → exit 2 |
| `loop_state.py end --book <bd> --chapter N` | 章结束（chapter_index add 之后） | critical step 缺 → exit 4 |
| `loop_state.py status --book <bd> [--chapter N] [--json]` | 查询进度（单点指令） | — |

## 与现有工具的边界

- **不重叠** [`snapshot_state.py`](state-snapshots.md)：snapshot 是真理文件级快照，loop_state 是 step 级 in-flight 进度
- **不重叠** [`chapter_index.py`](schemas/chapter-index.md)：chapter_index 记录章节运营状态（ready-for-review / approved / published…），loop_state 只记 in-flight step
- **互补** [`recover_chapter.py`](../scripts/recover_chapter.py)：崩溃恢复时先读 loop_state.json 给"从哪一步续接"的精确建议

## 不强制的边界

- 单点指令（"审一下第 N 章" / "改一下这段"）**不进** loop_state，仅 `writeNextChapter` 主循环用
- 章结束 `end` 后不回滚；只用于 in-flight 防跳步
- `begin --allow-replay` 让用户显式重跑某章（覆盖现有 state）

## 失败示例

```bash
$ python scripts/loop_state.py require --book books/x --chapter 5 --step 5c
loop_state: cannot enter step 5c — missing prerequisites: 1, 2, 3, 5, 5b
  Go back and complete those steps (run `mark` after each), then retry.
{"ok": false, "step": "5c", "missing": ["1", "2", "3", "5", "5b"]}
# exit 3
```

LLM 看到 exit 3 必须**回去补完缺失的 step**，不允许覆写、跳过或自欺。
