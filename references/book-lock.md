# Book Write Lock（书级写锁）

> 单本书的"忙写状态"指示器。落地为 `<bookDir>/.book.lock`，是一份装着 `pid / operation / acquiredAt / expiresAt / host` 的小 JSON。脚本 `scripts/book_lock.py` 提供 `acquire / release / status` 三个子命令。`apply_delta.py` 与 `chapter_index.py`（写入命令）启动时会自动拿；读类 / 一次性 CLI 不参与。

## 1. 这是 advisory lock，不是 OS 级互斥

要先把这点说清楚：本锁是 **协作式（advisory）**，不是 `fcntl` / `flock`。如果某个并发进程没有读 `.book.lock` 直接动手写，它**仍然能写**——锁阻挡不了它。

它要解决的是**人为意外**：

- 同一本书在两个 Claude Code 窗口里被同时编辑
- 用户手动跑 `apply_delta.py` 时背后 writeNextChapter 流水线还没收尾
- 误把同一脚本叠开两个 shell 跑

这种场景下，第二个进程 `acquire` 会被拒（exit 2），用户立刻看到 "lock held by another owner"，自己决定下一步。

## 2. TTL 语义

每次 `acquire` 写一个 `expiresAt = now + ttl`，默认 ttl = 1800 秒（30 min）。

- **未过期**：第二个 `acquire` 拒绝，`status` 显示 `held`
- **已过期**（current time > expiresAt）：第二个 `acquire` 直接接管（"tookOver"），并在 JSON 里带 `previousLock` + `warning: "took over expired lock"` 让上游知道
- **缺 expiresAt** / 解析失败：当作过期处理，可被接管

为什么有 TTL？防止某次进程被强杀（kill -9 / 关 terminal）后锁文件留在那永远拦后续写。30 min 是个"长到不会误中断、短到不会卡太久"的折中；写一章正常 5-15 min 之内。

## 3. 哪些脚本会拿锁

| 脚本 | 子命令 | 行为 |
|---|---|---|
| `apply_delta.py` | （所有调用） | 启动时 `acquire`（operation="apply-delta"），结束 / 异常一律 release。`--skip-lock` 跳过 |
| `chapter_index.py` | `add` / `update` / `set-status` | 启动时 `acquire`（operation="chapter-index-<cmd>-ch-<N>"），结束 release。`--skip-lock` 跳过 |
| `chapter_index.py` | `list` / `get` / `validate` | 不拿锁——纯读 |
| 其他写类 / 状态投影类 | — | 当前不拿；如果你把它们叠在一起跑要小心 |

> **设计意图**：`apply_delta` 与 `chapter_index` 写命令是**最常并发**的两组（一个写 `chapter_summaries.json` / `hooks.json`，一个写 `chapters/index.json`），互相竞争同一本书的 truth files；它们覆盖了 80% 的 race 场景。其他脚本是"批处理一次性"性质，一般不与写流水线交叉跑。

## 4. CLI 用法

```bash
# 状态
python {SKILL_ROOT}/scripts/book_lock.py --book <bookDir> status

# 主动拿锁（单步调试 / 长时间手工编辑前）
python {SKILL_ROOT}/scripts/book_lock.py --book <bookDir> acquire \
    --operation "manual-edit" --ttl 3600

# 释放——只能释放自己拿的
python {SKILL_ROOT}/scripts/book_lock.py --book <bookDir> release

# 强制释放（修锁残留）
python {SKILL_ROOT}/scripts/book_lock.py --book <bookDir> release --force
```

退出码：

- `0` — 成功
- `1` — 用法 / IO 错误
- `2` — `acquire` 被拒（锁被持有且未过期）
- `3` — `release` 被拒（不是自己的锁，又没传 `--force`）

## 5. 常见恢复场景

### 场景 A：上一次 apply_delta 被强杀后锁残留

```bash
# 看一下
python scripts/book_lock.py --book <bookDir> status
# 输出："held" + pid 12345 + acquiredAt 1 小时前
```

如果 `expiresAt` 已过 → 下一次 `apply_delta` / `chapter_index add` 会自动接管，不需要手动处理。
如果 `expiresAt` 还没到（你用了非默认 ttl）→ 确认那个 pid 真的不在了，然后：

```bash
python scripts/book_lock.py --book <bookDir> release --force
```

### 场景 B：两个 Claude 窗口同时尝试写

第二个窗口的 `apply_delta` 会立刻拿不到锁，stderr 输出：

```json
{
  "ok": false,
  "stage": "lock",
  "error": "could not acquire book write lock",
  "lockReport": {"currentLock": {...}, "hint": "..."}
}
```

退出码 3。让用户在另一个窗口先收工，再跑这边——或者主动 `release --force` 强夺（**只在你确定那边已经停了**）。

### 场景 C：流水线已经在外层拿了锁

例如某个集成 wrapper 自己 `book_lock.py acquire` 后再调 `apply_delta`。这时 `apply_delta` 用 `--skip-lock` 旁路：

```bash
python scripts/apply_delta.py --book <bookDir> --delta <file> --skip-lock
```

## 6. 与 hook governance / arbiter 的关系

这三层互不冲突：

- **book_lock**：本进程能不能写（互斥层）
- **hook arbiter**（pre-write）：写之前先洗 hookOps 形状（数据塑形层）
- **hook governance**（post-write）：写完后看有没有 critical（守护层）

`apply_delta` 的执行顺序：lock acquire → parse → arbitrate → apply → governance → release。

## 7. 设计取舍

- **为什么不用 fcntl？** 跨平台、stdlib 内、调试时锁文件本身可读可见——就是要让用户能 `cat .book.lock` 看到现状。fcntl 适合"千万别误并发"的场景，本 SKILL 更看重"不小心并发了，留个清楚的脚印"
- **为什么不持锁等待 / 排队？** SKILL 形态本来就是单 session 串行，写满阻塞队列只会让"两窗口"场景更难诊断。直接拒并提示，让用户先收一边
- **为什么 TTL 不是 5 min？** 一章 audit pass 就可能 8-10 min；过短 TTL 会导致正常流程中途自我接管，丢锁含义。30 min 给足余量
- **为什么 read-only 命令不拿锁？** `chapter_index list` 与 `validate` 跑统计 / 报告时被锁住没意义；"我这边正在写，那边不能看"也不是合理约束

## 8. 已知限制

- **NFS / 网络盘**：`os.replace` 在网络文件系统上不一定原子（取决于驱动），跨机器协作请慎用
- **PID 复用**：理论上一个长 TTL 锁残留时，进程退出后 OS 复用 PID 给其他进程，`_ours` 误判返回 True；实际中 30 min 内 PID 复用极小概率，且 `release --force` 兜底
- **不防本进程内重入**：同一进程内顺序两次 `acquire` 后只 `release` 一次会留锁——SKILL 内不会出现这种代码模式（`apply_delta` / `chapter_index` 各自只在 `main()` 入口拿一次）
