# Phase 12 — Consolidator（卷级摘要压缩 / 历史归档）

> ⛔ **硬约束 / 不跳步**：
> 1. **前置**：用户**明确点头**（重写 `chapter_summaries.json` 是有损操作）；`consolidate_check.py` 显示 `shouldConsolidate=true`；`snapshot_state.py create --milestone` 已建好兜底快照
> 2. **本阶段必跑**：卷压缩 + step 4 `hook_governance --command promote-pass` **必跑一次**（advancedCount 变化需重判 promoted flag）
> 3. **退出条件**：`volume_summaries.md` 更新 + `story/archive/volume-{N}.json` 归档 + 活跃 `chapter_summaries.json` 仅留最新卷
> 4. **重试规则**：用户审过再落盘；**禁止**自动跑（绕过用户确认 = 数据丢失风险）

> Consolidator **不是** writeNextChapter 主循环里的常驻阶段，而是**手动触发的侧流**——当书写到一定规模、`chapter_summaries.json` 行数够多、且至少有一卷已完结时，把已完结卷的逐章摘要压成一段**卷级叙事段落**；该卷的逐行明细归档到 `story/archive/volume-{N}.json`，活跃 `chapter_summaries.json` 只保留**最新（未完）卷**的逐行明细。

它**和 [memory_retrieve.py](../memory-retrieval.md) 互补**：memory_retrieve 决定"哪些行进 Composer context"（横切——选最近 N 章 + 命中 anchor 的旧章），Consolidator 改变"远端历史的形态"（纵切——把整卷压成一段散文）。100+ 章后两者叠加才能让 prompt 不爆。

---

## 何时进入

Consolidator 由两类事件触发，**不**进 writeNextChapter 主循环：

1. **用户手动触发**——典型措辞："压一下前面卷的摘要 / consolidate 一下 / 摘要太多了 / 把前面的卷归档 / 历史压缩一下"。直接对当前 book 跑 Consolidator，无视章节数。
2. **自动建议（不自动跑）**：在 writeNextChapter 主循环的 step 11 落盘后，跑一次 `scripts/consolidate_check.py`；当：
   - `chapter_summaries.json` 行数 ≥ **threshold（默认 60）**
   - **且**至少有 1 卷已完结（卷的 `endCh <= lastAppliedChapter`）

   就向用户**提示一句**："前面 N 卷已完结，章节摘要 K 条，要不要做一次 consolidate？"——**不擅自跑**。Consolidator 是有损操作（活跃明细被替换为段落），必须用户点头。

**不触发**：

- 每写完一章自动跑——这是滑窗记忆（[memory-retrieval.md](../memory-retrieval.md)）的活，Consolidator 只在阈值跨过 + 卷边界都满足时才提议。
- 当前卷尚未写完——卷未闭合不能归档；当前卷继续保持逐章明细，Composer 滑窗才有东西取。
- 章节摘要总量很小（< threshold）—— 直接读全表也不会爆 context，没必要压。
- writeNextChapter **正在进行中**——consolidate 会重写 `chapter_summaries.json`，与正在产出 chapterSummary delta 的 Settler 冲突；同一 book 同一时刻只能有一个流程在动真理文件。

## Inputs

Consolidator 这一阶段读以下文件：

- `story/outline/volume_map.md`（必读）—— 解析卷边界（每卷的 `name` / `startCh` / `endCh`）。Consolidator 自带正则解析"第 X 卷（第 N-M 章）"或"Volume N (Chapters X-Y)"两种格式。**回退**：`story/volume_outline.md`（老书；与 inkos `readVolumeMap` 行为一致）。
- `story/state/chapter_summaries.json`（必读）—— 当前活跃的逐章摘要数据。形如 `{ "summaries": [ { "chapter": N, "title": ..., "characters": ..., "events": ..., "stateChanges": ..., "hookActivity": ..., "mood": ..., "chapterType": ... }, ... ] }`。
- `story/state/manifest.json` —— 取 `lastAppliedChapter` 判定哪些卷"已完结"（`vol.endCh <= lastAppliedChapter`）。
- `story/pending_hooks.md`（可选）—— Consolidator 顺手跑一次"按 advancedCount 重判 promotion"（Phase 7 hotfix 2）。SKILL 实现把这条副作用委托给 [hook_governance.py promote-pass](../hook-governance.md)，详见 §Output contract 的 `promotedHookCount` 段。
- `book.json#language` —— 决定走中文还是英文 system prompt（系统 prompt 末有 "Write in the same language as the input"，模型会自适应；language 字段主要影响日志措辞）。

**不读**：`hooks.json` / `current_state.json`。Consolidator 只压摘要，不改设定也不动伏笔池。

## Process

### 1. 系统 prompt（verbatim 来自 `consolidator.ts` L113-117）

```
You are a narrative summarizer. Compress chapter-by-chapter summaries into a single coherent paragraph (max 500 words) that captures the key events, character developments, and plot progression of this volume. Preserve specific names, locations, and plot points. Write in the same language as the input.
```

> 调用参数：`temperature = 0.3`（与 Polisher 接近——压缩要稳，不要发散）。

### 2. User message 拼装（verbatim 来自 `consolidator.ts` L119-121）

```
Volume: <vol.name> (Chapters <startCh>-<endCh>)

Chapter summaries:
<header>
<volSummaryRows>
```

`header` 是 `chapter_summaries.json` 渲染成 markdown 表的列名行（保持与 inkos 兼容）；`volSummaryRows` 是该卷区间内所有摘要按章节升序，每条渲染成一行 `| chapter | title | characters | events | ... |`，再以 `\n` join。

**一卷一调**，不批量——避免单 prompt 把多卷压成一段、丢卷级边界。

### 3. 工作步骤

1. **预检（脚本，无 LLM）**：
   ```bash
   python {SKILL_ROOT}/scripts/consolidate_check.py --book <bookDir> --threshold 60 --json
   ```
   返回 `{ shouldConsolidate, totalChapters, totalVolumes, completedVolumes, completedVolumeNumbers, threshold, reason }`（详见脚本本身）。
   - `shouldConsolidate == false` 且**用户没有显式触发** → 退出，`archivedVolumes: 0`。
   - `completedVolumeNumbers == []` → 退出，记 reason="no completed volumes"。

2. **逐卷 LLM 压缩**：对 `completedVolumeNumbers` 中**尚未归档**（即 `story/archive/volume-{N}.json` 不存在）的每一卷：
   - a. Claude 执行 §1 system prompt + §2 user message 一次，拿到 ≤ 500 词的卷级叙事段。
   - b. 解析检查：非空、关键字（卷名、首末章号）至少之一出现在文中。失败 → 重试 1 次（同 prompt）。仍失败 → **跳过该卷，不修改任何文件，记 `status: "skipped-unparseable"`**。
   - c. 把段落收集到内存中的 `volumeSummaries` 字典：`{ N: { name, startCh, endCh, paragraph } }`。

3. **归档明细 + 重写活跃文件（确定性、原子）**：
   - a. 对每个待归档卷 N，把 `chapter_summaries.json` 中 `chapter ∈ [startCh, endCh]` 的所有行**先复制**到 `story/archive/volume-{N}.json`（结构 `{ "volume": N, "name": ..., "startCh": ..., "endCh": ..., "summaries": [...] }`）。
   - b. 验证 archives 文件存在、非空、JSON 可解析。
   - c. 重写 `story/state/chapter_summaries.json`：保留 `chapter > max(endCh of all archived volumes)` 的行（即最新未完卷的逐章 + 不在任何卷范围里的散行）。
   - d. 把 §2 收集的卷级段落写到 `story/volume_summaries.md`（不存在则建文件，存在则追加 `## <name> (Ch.X-Y)\n\n<paragraph>`）。
   - e. 维护 `story/state/manifest.json` 的 `consolidatedVolumes` 数组（追加本次归档的卷号）。

   **顺序硬约束**：`archive 先 → 验证 → 改活跃 → 写卷级 .md → 更新 manifest`。任一步失败，**不进入下一步**；前面已写的 archives 保留作为回滚证据。**禁止 mv**（不可逆）。

4. **Hook 治理副作用（Phase 7 hotfix 2 promote-pass）**：
   ```bash
   python {SKILL_ROOT}/scripts/hook_governance.py --book <bookDir> --command promote-pass --current-chapter <lastAppliedChapter>
   ```
   把它的 `flippedCount` 收集到 `promotedHookCount` 字段。Consolidator 不复刻 promote 逻辑——委托给 hook_governance 即可。

5. **落盘 ConsolidationResult 元数据**：写到 `story/runtime/consolidation-<timestamp>.json`，结构见下。

### 4. 输出契约（与 inkos `ConsolidationResult` 等价）

```json
{
  "volumeSummaries": "...本次跑完后整个 volume_summaries.md 的全文 markdown ...",
  "archivedVolumes": 2,
  "retainedChapters": 7,
  "promotedHookCount": 1
}
```

字段说明：

- `volumeSummaries: string`：本次跑完后 `volume_summaries.md` 的全文（含历史已存在的卷级段 + 本次新增的段；方便调用方直接读，省 IO）。
- `archivedVolumes: number`：本次实际归档的卷数（已归档过的不重复计；解析失败被跳过的不计）。
- `retainedChapters: number`：重写后活跃 `chapter_summaries.json` 里剩多少行（应等于"当前未完卷的章节摘要行数"+ 不在任何卷范围里的散行数）。
- `promotedHookCount: number`：本次 promote-pass 里从 `promoted=false` 翻到 `true` 的钩子数（来源：`hook_governance.py promote-pass` 的 stdout）。0 表示没有钩子越过 advancedCount 阈值，或 pending_hooks.md 缺失。

## Failure handling

| 失败种类 | 检测方式 | 处理 |
|---|---|---|
| `volume_map.md` / `volume_outline.md` 都缺 | `consolidate_check.py` 返回 `totalVolumes: 0` | 优雅退出，`archivedVolumes: 0`，stderr 提示"volume_map missing—无法压缩" |
| 没有已完结卷（`completedVolumeNumbers == []`） | 同上 | 优雅退出，`archivedVolumes: 0`，reason="no completed volumes" |
| LLM 单卷输出空 / 全空白 | `len(text.strip()) == 0` | 重试 1 次；仍空 → 跳过该卷，**不动该卷的活跃明细**，记 `status: "skipped-empty-llm"` |
| LLM 输出超 800 词（远超 500 词上限） | 字数复算 | 重试 1 次，prompt 末加 "Tighten to under 500 words"；仍超则截断到末尾完整句号处保守落盘 |
| LLM 输出丢失关键 anchor（卷名 + 首末章号都没有） | 关键字检查 | 重试 1 次；仍失败 → 跳过该卷，记 `status: "skipped-missing-anchors"` |
| archive 文件写失败（IO） | open/write 异常 | 立即退出，**不**进入"重写活跃文件"步骤；活跃文件保持原样，`archivedVolumes: 0` |
| archive 写完但读不回（损坏） | json.load archives 失败 | 删除损坏的 archives 文件；退出，活跃文件未动，整卷视为未压缩 |
| 重写活跃 `chapter_summaries.json` 失败 | open/write 异常 | archives 已存在但活跃文件未变——下次重跑会跳过该卷（archives 已存在），需要管理员手动 rm archives 才能重压 |
| `consolidatedVolumes` manifest 写失败 | 同上 | 容忍——下次跑时 archives 已在，会自动跳过；manifest 不更新只影响显示 |

**关键约束**：

- 单卷 LLM 重试 ≤ 1（不是 ≤ 3）——压缩 prompt 简单，多次失败说明摘要本身就有问题，不要死磕。
- **archive 文件存在 ⇔ 该卷已被压缩**——这是幂等性的唯一真理；`consolidatedVolumes` 字段只是镜像，不当判据。
- LLM 输出不可解析 / 不合规时，**跳过整卷**而不是写半段——半段污染 volume_summaries.md 比缺一段更难发现。

## 注意事项

1. **不可逆-ish**：`chapter_summaries.json` 一旦被重写，原本的逐章行只能从 `story/archive/volume-{N}.json` 找回。archives 是只读历史档案，**不要再做二次压缩**。Reviser 做 `spot-fix` 修改老章节时，需要回看明细 → 从 archives 取。
2. **绝不在 writeNextChapter 进行中跑**：consolidate 会重写 `chapter_summaries.json`，与正在产 chapterSummary delta 的 Settler 冲突。SKILL 路由判定时**先看是否有 `story/runtime/chapter-{NNNN}.delta.json` 但 chapter > lastAppliedChapter**——有则该章主循环未完，拒绝 consolidate。
3. **当前卷不动**：用户哪怕显式说"全压了"，也只能压 `endCh <= lastAppliedChapter` 的卷。当前卷继续保持逐章明细，让 Composer 滑窗 hit 得到。
4. **threshold 60 是经验值**，不是硬约束：常规章 200-400 词摘要，60 行约 12-24K 词，Composer 全读会挤掉 30%+ 的 prompt。书的章节摘要更长（含 hookActivity/stateChanges 等结构化字段）时可以下调到 40；摘要短（只 events 一行）可以放到 80。在 `consolidate_check.py --threshold` 显式覆盖。
5. **Hook 治理 promote-pass 是 inkos 的"顺手"副作用**：把它放在 Consolidator 里是 Phase 7 hotfix 2 的设计——卷边界跨越往往伴随 advancedCount 阈值跨越。SKILL 把这条委托给独立的 `hook_governance.py promote-pass`，结果回填到 `promotedHookCount` 字段——**调用方不需要单独再跑 promote-pass**，Consolidator 已经替它跑过一次。
6. **卷级段写入位置 = `story/volume_summaries.md`**（不是 `story/state/volume_summaries.md`）——状态目录只放 JSON 真理；散文产物（人读 + LLM 远端记忆）放 `story/` 根，与 author_intent / current_focus / chapter_summaries.md 同层。Composer 在 30+ 章时优先读 `volume_summaries.md`（便宜）+ `chapter_summaries.json` 当前卷行（精细），不再读 archives（除非用户 `spot-fix`）。
7. **跨卷剧情连续性**：卷级摘要是"事后回顾"，**不是"未来预告"**——压缩 prompt 里不要让模型补"这件事影响后续"之类的预测，让它只描述本卷内已发生的事。Planner 做下一卷规划时，会把上一卷的 volume_summary 当成已知历史读。
8. **promotedHookCount=0 不一定代表 hook 池健康**：可能是没钩子越过阈值，也可能是 pending_hooks.md 不存在。要看 pool 健康度请单独跑 `hook_governance.py --command health-report`。
