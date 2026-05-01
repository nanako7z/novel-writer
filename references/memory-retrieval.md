# Memory Retrieval（滑窗记忆选择）

简化版的"章节记忆调度器"，对应 inkos 源码 `utils/memory-retrieval.ts` + `state/memory-db.ts`。我们**不引入 SQLite**，改用一个 stdlib-only Python 脚本直接读 markdown / JSON 真理文件。脚本：`scripts/memory_retrieve.py`。

## 为什么需要它

主循环（写下一章）跑到 Composer 阶段时，要把"过去发生过什么"压成 Writer 能吃下的上下文。直接读全部 `chapter_summaries.json` 在 30 章之后就开始挤占 token：

- 50 章 × ~200 字摘要 ≈ 10K 字，仅事件流就占 prompt 的一大块
- 没有 hook/角色相关性筛选，远古旧章和近章一视同仁
- 跨卷长篇（>100 章）几乎不可能不裁剪

因此即便我们不要 SQLite 索引，也仍然需要"最近窗 + 相关窗"两段式选择。这个脚本就是那个选择器。

## 算法（与 inkos 一致，仅去掉 SQLite 后端）

输入：`--book <bookDir> --current-chapter N`，加可选窗口尺寸。

1. **Recent window**（默认 6 章）
   - 取 `chapter < N` 的最后 `--window-recent` 条 chapter_summaries
   - 全字段保留（events / characters / hookActivity / mood / chapterType）
2. **Anchor terms**
   - 把 recent window 里所有 `characters`（按中文/英文逗号切分）+ 所有"window 内的活跃 hookId"汇入一个 `anchor_terms` 集合（小写、去重）
3. **Relevant window**（默认 8 条，"deeper history"）
   - 取 `chapter < N - window-recent` 的旧章
   - 过滤：摘要任一字段里出现至少一个 anchor term
   - 按章节号倒序取前 N，**只保留 `events` 字段**（节省 token；这些是"提一嘴"型的远端引用，不是完整记忆）
4. **Active hooks**
   - 来自 `story/state/hooks.json`
   - `status ∈ {open, progressing, deferred}` 且满足 `lastAdvancedChapter ≥ N - 12 OR startChapter ≥ N - 12 OR coreHook === true`
5. **Recently resolved hooks**（仅当 `--include-resolved-hooks`）
   - `status == resolved` 且 `lastAdvancedChapter ≥ N - 3`
   - 用途：刚刚兑现的伏笔在下一两章可能要做"余响 / 角色回应"
6. **Character roster**
   - 从 recent window 的 `characters` 字段抽角色名集合
   - 在 `story/character_matrix.md` 中找 `charA` 或 `charB` 命中的行，按原表输出 `relationship / intimacy / lastInteraction`
7. **Current state snapshot**
   - 整个 `story/state/current_state.json` 原样塞进 payload（很小，就一组 facts）

不做的事：

- 没有全文搜索（inkos 的 SQLite FTS5 我们不需要）
- 没有 embedding 相似度（先跑通字符/hook 重叠这条朴素路线）
- 没有打分排序——recent 是直接切尾，relevant 是直接按章节号倒序

## Composer 何时调用

主循环 step 3：Planner 落盘 chapter_memo 之后、Composer 装 contextPackage 之前。

Composer 把脚本输出的 JSON 当作"记忆维度"的主输入塞进 selectedContext 表的 row 9–14（recent_titles / mood trail / 旧章摘要 / pending hooks）。详见 [phases/03-composer.md](phases/03-composer.md#memory-window).

## 调参（什么时候偏离默认）

| 场景 | window-recent | window-relevant | --include-resolved-hooks |
|---|---|---|---|
| 默认 | 6 | 8 | off |
| 黄金开场（ch 1–3） | 3 | 4 | off — 没什么过去要拉 |
| 卷尾兑现章（即将回收一个 core hook） | 6 | 12 | **on** — 把伏笔种植链上下文都拉来 |
| 兑现后的下一章（"余响"章） | 6 | 6 | **on** — 让角色对刚解决的事件有反应 |
| 新卷开篇 / arc 切换 | 8 | 12 | off — 多带点近况，少做远端旁征博引 |
| 节奏 / 关系日常章 | 4 | 4 | off — 当前事就够了 |

Composer 默认走"默认"那一行；只在 chapter_memo 显式标 `isGoldenOpening: true` / `cliffResolution: true` / `arcTransition: true` 时切换。

## 输出 schema

`--format json`（默认）：

```json
{
  "currentChapter": 12,
  "recentSummaries": [/* full StoredSummary objects */],
  "relevantSummaries": [
    { "chapter": 3, "title": "...", "events": "..." }
  ],
  "activeHooks": [/* full hook objects from hooks.json */],
  "recentlyResolvedHooks": [/* same shape, may be empty */],
  "characterRoster": [
    { "charA": "林秋", "charB": "二师姐", "relationship": "盟友",
      "intimacy": "+4", "lastInteraction": "ch11 联手夺符", "notes": "" }
  ],
  "currentState": { "facts": [...] },
  "stats": {
    "recentCount": 6, "relevantCount": 4,
    "activeHookCount": 12, "totalChars": 4521
  }
}
```

`--format markdown`：上述 payload 渲染成 ~2-3 KB 的人类可读 digest（# / ## / 列表），可直接粘进 prompt。

## 与 inkos SQLite 版的差异

| 维度 | inkos `memory-retrieval.ts` | 本脚本 |
|---|---|---|
| 后端 | `memory.db`（SQLite + FTS5） | 直接读 `chapter_summaries.json` / `hooks.json` |
| 选择标准 | 词元 score + 章距 score | 朴素的 substring 命中（recent window 的字符 + hookId） |
| 全文搜索 | 有（FTS5 全字段） | 无 |
| 复杂度 | O(log n) 索引查询 | O(n) 全扫，n 是历史章数 |
| 适用规模 | 数百章流畅 | 100-150 章无感；>200 章可考虑加缓存层 |
| stale hook 召回 | 有（`computeRecyclableHooks`） | 不在本脚本中——已经分到 `scripts/hook_governance.py` 里 |

短期如果遇到性能瓶颈（章数 > 200 + 调用频次密），优先考虑：(1) 给 chapter_summaries.json 加 chapter→summary 字典缓存；(2) 把脚本 import 成模块直接调，省掉子进程开销；(3) 仍卡才考虑改回 SQLite。

## 失败模式

- 真理文件缺失（`chapter_summaries.json` / `hooks.json`）→ 当作空数组继续，不报错，让 Composer 自己决定要不要继续
- `current_state.json` 损坏或非 JSON → 退化为 `{}`
- `--current-chapter < 1` → exit 1，stderr 出 JSON 错误
- `character_matrix.md` 缺失 → roster 为空数组

脚本永远不修改任何真理文件——它是只读的检索层。
