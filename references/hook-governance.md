# Hook 治理（Hook Governance）

伏笔从"被想到"到"被回收"要经过五个生命阶段。任何阶段越级跳，都会让 `pending_hooks.md` 变成噪声、让 settler 凭空捏造 hookId，或让 reviewer 查不到票根。本文件给出**确定性规则 + 唯一可执行入口**：`scripts/hook_governance.py`。

> 这不是 LLM 阶段。本治理子系统是纯 Python，stdlib only，所有判断都来自 `.inkos-src/utils/hook-{promotion,health,stale-detection,ledger-validator,governance}.ts` 的逻辑端口。

---

## 1. 生命周期

```
seed ──(promote-pass)──▶ promoted ──(write 命中 advance)──▶ active
                                                              │
                                                              ├──(half-life 过期)──▶ stale
                                                              ├──(settler resolve)──▶ resolved
                                                              └──(settler defer )──▶ deferred / expired
```

| 阶段 | 存放位置 | 谁产生 | 谁消费 |
|---|---|---|---|
| seed | `story/runtime/hook-seeds.json` | architect / observer 候选 | promote-pass |
| promoted | `story/state/hooks.json`（`promoted=true`） | promote-pass | composer 装上下文 |
| active | `story/state/hooks.json`（`status=open/progressing`） | settler `hookOps.upsert` | writer / auditor / reviser |
| stale | `hooks.json`（`stale=true`） | stale-scan | auditor (Hook Debt 警告) |
| resolved / deferred | `hooks.json`（`status=resolved/deferred`） | settler `hookOps.resolve/defer` | composer 跳过 |

> **唯一升级路径**：seed → promoted 由 promote-pass 写盘；其它真理文件改动一律走 `apply_delta.py`。

---

## 2. 四条 promotion 触发条件（任一命中即升级）

源：`.inkos-src/utils/hook-promotion.ts#shouldPromoteHook`

| 条件 | 何时触发 | 例子 |
|---|---|---|
| `core_hook` | hook 上 `coreHook === true` | architect 标的全书主线伏笔（每书 3-7 条） |
| `depends_on` 非空 | `dependsOn: ["H003"]` 之类 | "师叔玉牌"依赖"师债真相"——必须进 ledger 才能跟踪上游 |
| `advanced_count >= 2` | 在 ≥2 个章节里出现过推进 | 已经被读者跟踪两章的伏笔，必须留 |
| `cross_volume` | 跨卷生效，三种子情况之一 | 见下 |

**cross_volume 三种子情况**（`isCrossVolume` 算法）：

1. **Case A**：`dependsOn` 里某个上游 hook 的 `startChapter` 落在更晚的卷（说明这条伏笔会等更后面的卷才能解锁）
2. **Case B**：`paysOffInArc` 文案里出现 "第 N 卷 / volume N" 且 N ≠ seed 当前卷
3. **Case C**：`payoffTiming ∈ {endgame, slow-burn}` 且 seed 落在非最终卷

> Case B 的卷号识别支持中文数字（一/二/.../十）和阿拉伯数字。多过这两种格式 → 解析失败，跳过 Case B。

**OR 语义**：任何一条命中即 `promote=true`；多个命中时 `reasons` 同时记录，便于审计。

---

## 3. Stale 规则（per-type half-life）

源：`hook-promotion.ts#defaultHalfLifeChapters` + `hook-stale-detection.ts#computeHookDiagnostics`

```
distance = currentChapter - startChapter
stale    = (not resolved) AND startChapter > 0 AND distance > halfLife
```

`halfLife` 优先用 hook 自带 `halfLifeChapters`；缺省时按 `payoffTiming` 推：

| `payoffTiming` | 半衰期（章节数） |
|---|---|
| `immediate` / `near-term` | 10 |
| `mid-arc`（也是默认） | 30 |
| `slow-burn` / `endgame` | 80 |
| 未给 | 30（fallback to mid-arc） |

> stale 不会自动降级或删除——只是给 hook 加 `stale=true` 标志，让 auditor 在"Hook Debt"维度抛 warning。**作者决定何时回收。**

**额外**：`blocked` 标志——`dependsOn` 引用的上游 hook **未植入或未回收**时为真。如果上游本身不在 hooks.json 里，认为 blocked 自该 hook 自己被植入起就成立。

---

## 4. 跨文件一致性规则（validate 命令）

`hook_governance.py --command validate` 做四类检查：

| 类别 | 严重度 | 触发条件 |
|---|---|---|
| `dep_cycle` | **critical** | `dependsOn` 形成环（DFS 检测，节点 frozenset 去重） |
| `dangling_dep` | warning | hook A 的 dependsOn 包含一个 hooks.json 里查不到的 id |
| `stale_ledger_row` | warning | `pending_hooks.md` 里出现一个 id，但 hooks.json 没有对应记录 |
| `summary_unknown_token` | info | `chapter_summaries.json` 里 hookActivity 字段提到了一个不在 hooks.json 的 id-like token |
| `unjustified_promotion` | warning | hook `promoted=true` 但当前没有任何 promotion 条件成立 |

**只有 `critical` 是 apply_delta.py 的硬闸**——其它都只是 issue 列表里的提示。

---

## 4b. 揭 1 埋 1 硬底线 + payoff 可定位约束（新增，移植自 inkos commit `b1cc3a7` + `ab39bd6`）

> 这一节把 inkos `hook-ledger-validator.ts` 里的"揭 1 埋 1"硬底线和 `writer-prompts.ts` 里的"hook 兑现段必须可定位"硬约束端口到 SKILL。两条规则都进入 `hook_governance.py --command validate`，severity = `critical`，会被 apply_delta.py 闸门拦下。

### 规则 A — 揭 1 埋 1 硬底线（番茄老师弈青锋）

本章 `chapter_memo.hook_ops` 满足：

- 计 `resolved_count = len(hook_ops.resolved)`、`new_open_count = len(hook_ops.opened)`
- 当 `resolved_count > 0` 时，**必须 `new_open_count >= resolved_count`**
- 违反 → `code: REVEAL_BURY_FLOOR, severity: critical`，章节判退、不进 Settler

`new_open_count` 字段会一并落进 `story/state/hook_ledger.json` 的本章行，给 reviewer / commitment ledger 后续读。

> **设计要点**：planner P14 推荐"揭 1 埋 2"——这是劝导，不卡；hook_governance 卡的是"揭 1 埋 1"硬底线——劝导和硬尺分层放置，给作者留松紧。

### 规则 B — payoff 可定位约束（番茄老师弈青锋）

本章 `chapter_memo.hook_ops.advance` ∪ `hook_ops.resolved` 里出现的**每一个 `hook_id`**，必须在正文 prose 里有一段**可定位兑现场景**：

- 长度 **≥ 60 字**
- 含**可观察动作 / 对话 / 物件**（人物对着具体物件 / 事件 / 信息做出可观察的动作或交谈）
- **纯内心回想不算**——"他想起借条还在抽屉里"不算兑现，必须实际伸手摸到 / 看到 / 拿起 + 做出动作
- defer 不需要 prose 锚；open 只需在章末附近有一个自然引出的种子即可

违反 → `code: HOOK_PAYOFF_UNLOCATED, severity: critical`，章节判退。

### Writer 自检契约

Writer 写完初稿后必须自检一遍 hook 账：把 `advance / resolve` 的每个 `hook_id` 列下来，对照正文，确认每一条都能指向一段带具体动作 / 物件 / 对话的 prose 段。指不到 → 回去补写；不要提交"账本在 memo 里、正文里没落"的稿子。

可选地，Writer 在 14.A 全量输出模式下额外输出 `=== BLOCK: HOOK_PAYOFF_AUDIT ===` 区块，把每个 hook_id → prose anchor 对照表显式列出（详见 [writer/output-format.md §14.A.HOOK_PAYOFF_AUDIT](writer/output-format.md)）。Settler 拿到这个块就直接对照；没有这个块时由 hook_governance.py 在正文里 fallback 搜锚。

### 与 §4 的合表关系

| 类别 | 严重度 | 触发条件 |
|---|---|---|
| `REVEAL_BURY_FLOOR` | **critical** | `resolved_count > 0 && new_open_count < resolved_count` |
| `HOOK_PAYOFF_UNLOCATED` | **critical** | `advance ∪ resolved` 里某个 hook_id 在正文找不到 ≥ 60 字、含可观察动作 / 对话 / 物件的兑现段 |

两条都跟 §4 的 `dep_cycle` 同级，是 apply_delta.py 的硬闸。

### 配套 schema 变更

- `story/state/hook_ledger.json` 新增字段 `newOpenCount: int`（每章一行的本章打开计数）
- `_constants.py` 新增违规码 `REVEAL_BURY_FLOOR / HOOK_PAYOFF_UNLOCATED`
- `_schema.py` 在 hook_ledger schema 里把 `newOpenCount` 标为 required（旧仓库无此字段时由 `apply_delta.py` 自动补 0，向后兼容）

---

## 5. Health metrics（health-report 命令）

每条 hook 输出：

| 字段 | 含义 |
|---|---|
| `freshness` | 0.0 ~ 1.0；线性衰减：`1 - chaptersSinceAdvance/halfLife`，clamp 到 [0,1] |
| `distance` | `currentChapter - startChapter` |
| `halfLife` | 实际生效的半衰期 |
| `stale` / `blocked` | 见 §3 |
| `promoted` / `coreHook` | 透传 |

聚合层（顶层字段）：

| 字段 | 含义 |
|---|---|
| `activeCount` | 排除 resolved/deferred 后的 hook 数 |
| `staleCount` | 当前 stale 的 active hook 数 |
| `blockedCount` | 当前 blocked 的 active hook 数 |
| `chaptersSinceAnyAdvance` | 距上一次任何 hook 推进过去多少章；与 `noAdvanceWindow=5` 对比 |
| `ledgerPressure` | `ok` / `warn` / `high`；active > maxActiveHooks(12) → high；total > 30 → high |

---

## 6. 何时调哪个命令

| 时机 | 命令 | 原因 |
|---|---|---|
| 每次 Settler 完成、`apply_delta.py` 落盘后（自动） | `validate` + `stale-scan` | apply_delta 已硬编码这一步，**不需要手动调** |
| Architect 写完 seeds 后 / 作者手动催"看看哪些伏笔该正式入册" | `promote-pass` | 把 seed 升级到正式 hook |
| 每周 / 每卷尾 / 用户问"伏笔池现在乱不乱" | `health-report` | 给作者一个全局体检 |
| 每章 audit 之前的可选信息源 | `health-report` | 把 staleCount / ledgerPressure 当作 auditor 的输入信号 |

调用形式统一：

```bash
python {SKILL_ROOT}/scripts/hook_governance.py \
  --book <bookDir> \
  --command <promote-pass|stale-scan|validate|health-report> \
  [--current-chapter N]
```

`--current-chapter` 缺省时从 `story/state/manifest.json#lastAppliedChapter` 读。

---

## 7. Claude 决策树：拿到 issue 列表怎么办？

```
issue.severity == "critical"
   → 这是 apply_delta 的硬闸；exit 1。
   → 不要"绕开"——回到 Settler，把违规的 delta 改干净，重跑 apply_delta。
   → dep_cycle 是最常见的 critical：让 settler 砍掉一条 dependsOn 边。

issue.severity == "warning"
   → 写进章节 runtime log；继续后续阶段。
   → 累积 ≥3 个 warning 时应在 audit 阶段加一条"hook ledger 卫生"提醒。
   → dangling_dep / stale_ledger_row 通常说明 hook 在某次 settler 输出里被遗漏，
     下一章的 planner 应顺手补一笔（mention 或 resolve）。

issue.severity == "info"
   → 静默；除非作者主动问伏笔状态，不需要 surface 给用户。
```

---

## 8. 与 `apply_delta.py` 的耦合

apply_delta 在写完所有真理文件后**自动**调：

1. `hook_governance --command validate`
2. `hook_governance --command stale-scan`

二者输出合并到 apply_delta 的 stdout JSON 里（`hookGovernance.{validate,staleScan}`）。
若 validate 报告 `counts.critical > 0`，apply_delta 退出码 1 并设 `hookGovernanceBlocked: true`——**主循环 step 11 必须看这两个字段**，不能拿 step 10 的真理文件直接落章节正文。

紧急情况下（如手工修复中）可加 `--skip-hook-governance` 开关，但日常流水线**绝不**该加。

---

## 8b. 钩子仲裁 (Arbitration)

源：`.inkos-src/utils/hook-arbiter.ts` → 端口 `scripts/hook_arbitrate.py`，`apply_delta.py` 在 schema 校验之后、写盘之前**自动**调一次。仲裁与治理 (validate / promote-pass) 都不是同一件事——区别如下：

| 子系统 | 时机 | 输入 | 输出 |
|---|---|---|---|
| **arbiter** | settler 出 delta → 写盘**之前** | `delta.hookOps` + `delta.newHookCandidates` + 当前 `hooks.json` | 已 remap 的 delta（candidates 全部解决），4 类决策记录 |
| **promote-pass** | architect 写完 seeds → composer 装上下文**之前** | `hook-seeds.json` + `hooks.json` | 升级后的 `hooks.json`（seed → 正式 hook，加 `promoted=true`） |
| **validate** | apply_delta 写盘之后 | 落盘的 `hooks.json` + `pending_hooks.md` + `chapter_summaries.json` | 跨文件一致性 issues |

> 一句话：**arbiter 处理"候选"——promote-pass 处理"种子"——validate 处理"已落盘"**。

### 4 种 verdict

每条 candidate（含 hookOps.upsert 里 id 不在 hooks.json 的"伪 upsert"）走完 `evaluateHookAdmission` 后产出以下之一：

| action | 触发条件 | 副作用 |
|---|---|---|
| `created` | admission 通过（type 非空 + 有 payoff signal + 无 duplicate_family + 未超 maxActiveHooks） | 生成新 canonical hookId（slug 自 type+payoff+notes，碰撞时加 `-2`/`-3` 后缀），追加进 hookOps.upsert |
| `mapped` | admission 拒绝且 `reason=duplicate_family`，但 candidate 相对 matched hook 有**新增信息** | 把 candidate merge 进 matched hook 的 upsert 条目（`preferRicher` 文本 + 推进 lastAdvancedChapter + 重新解析 payoffTiming） |
| `mentioned` | admission 拒绝且 `reason=duplicate_family`，candidate 与 matched hook 是**纯复述**（无新词、新汉字 bigram <2） | 不写 upsert，把 matched hookId 加入 hookOps.mention（确保它不在 resolve/defer 集合里） |
| `rejected` | 缺 type / 缺 payoff signal / duplicate_family 但匹配的 hookId 已不存在 / `ledger_full`（活跃 ≥ maxActiveHooks） | 候选丢弃，记 decision，不写盘 |

### 与 promote-pass 的分工

- **arbiter** 看到的是**当前章节这一轮的产物**（settler 刚交的 delta），它解决的是"同一章里 settler 把同一伏笔讲了两遍"或"settler 给的 hookId 与 ledger 里已有那条其实是一回事"。
- **promote-pass** 看到的是**沉淀过的 seed 池**（architect 阶段累计的候选），它解决的是"哪些 seed 已经够格变成正式 hook"。
- arbiter **不**升级 seed → hook（那是 promote-pass 的职责），arbiter 只决定"这条 candidate 是不是该进 ledger / 进哪条 ledger 行"。
- arbiter **不**写 `hooks.json`（那是 apply_delta 的下一步），arbiter 只 remap delta。

### 调用入口

apply_delta 自动调，无需手动；调试/dry-run 可单独跑：

```bash
python {SKILL_ROOT}/scripts/hook_arbitrate.py \
  --hooks <book>/story/state/hooks.json \
  --delta <runtime/chapter-NNNN.delta.json> \
  [--max-active 12]
```

输出含 `decisions[]` + `resolvedDelta` + `summary`（`n_created=…`）。`apply_delta.py` 的 stdout JSON 里同样把 decisions 放在 `arbitration.decisions`。

紧急情况下可用 `apply_delta.py --skip-arbitration` 走老的 last-write-wins 路径，**仅**当外部已有别的仲裁器时。日常流水线**不要**关。

---

## 8c. 章节 hook 账（commitment ledger）

每一章 Planner 必须在 chapter_memo 末尾写一个 `## 本章 hook 账` 段，按四个 subsection 列出本章对活跃伏笔的动作（详见 [phase 02 planner](phases/02-planner.md#本章-hook-账must-write)）。Writer 拿到 memo 后，正文里必须真的"做掉"declared 的 advance / resolve——否则 planner 与 writer 之间就形成了"承诺/兑现失配"。

**确定性闸门**：`scripts/commitment_ledger.py`（端口自 inkos `utils/hook-ledger-validator.ts`）。

```bash
python {SKILL_ROOT}/scripts/commitment_ledger.py \
  --memo story/runtime/chapter_memo.md \
  --draft story/runtime/chapter-{NNNN}.draft.md \
  [--hooks story/state/hooks.json --chapter N] \
  [--json] [--strict-empty]
```

**判定规则**：

1. 解析 `## 本章 hook 账` / `## Hook ledger for this chapter` 段，按 4 个 subsection（open / advance / resolve / defer）拆 entry。
2. 对每条 `advance` / `resolve` entry：
   - 优先取双引号内的钩子名（最具信息量），其次取 `→` / `->` 之前的描述。
   - 抽 token：2+ 字 CJK 序列 + 3+ 字母 ASCII 词（小写、去 stopwords）；4+ 字 CJK 还会拆首尾 2-gram 让部分回声也算数。
   - 在 draft 里查任一 token——只要命中一个就算"作者写到了"。**不要求** draft 重复 `H001` 这种纯 ID。
3. 占位行（`无 / none / nil / null / 暂无 / n/a / na / tbd / todo / 待定`）忽略，不参与校验。
4. `defer` / `open` 不校验——前者是"刻意不动"，后者是新种子（还没有可比对的 descriptor）。
5. 任一 advance/resolve entry 在 draft 中无证据 → 输出 `severity=critical` 的 violation，类别 `hook 账未兑现`，给具体 hookId + 修复建议（"加入对 X 的具体落地动作，或把它移到 defer 并给出理由"）。
6. **可选 stricter check**：如果传了 `--hooks` + `--chapter`，脚本还会扫 `hooks.json`：任何记录的 `committedToChapter`（或兼容 `committedPayoffChapter`）等于本章号，**且**未在 ledger 的 advance/resolve 里登记，**且**未 resolved → critical（类别 `committedToChapter 未兑现`）。这覆盖 planner 漏写的情况。

**退出码**：0 = 无 critical；2 = 有 critical；3 = bad input（找不到文件 / `--strict-empty` 模式下 ledger 完全缺失）。

**接入点**：[phase 09 auditor](phases/09-auditor.md) pre-audit 确定性闸门链中位于 `sensitive_scan` 之后、LLM auditor 之前。critical violation 合并进 `audit.issues`，作为 load-bearing 输入（不是 advisory）——reviser 必须在下一轮把缺失的落地动作补回正文。

---

## 8d. 卷尾兑现验证（cross-volume payoff）

每完成一卷的最后一章，必须验证"这一卷里开的伏笔，在卷尾前都得到了交代"——要么 resolve，要么 defer 到下卷（带 `committedToChapter` 指向后续章节作为承诺），要么显式写 cross-volume slow-burn。否则就是**卷间叙事漏账**——读者会在下一卷开头发现一堆没人提的旧伏笔。

**确定性闸门**：`scripts/hook_governance.py --command volume-payoff --volume N`（与已有的 `verify-volume-payoff` 是兄弟命令；前者按 gap #17 形式输出 `payoffRate / hooksOpenedInVolume / hooksResolvedByEnd / issues`，后者按 4-类分类输出）。

```bash
python {SKILL_ROOT}/scripts/hook_governance.py \
  --book <bookDir> --command volume-payoff --volume 1 \
  [--current-chapter N]
```

**算法**：

1. 读 `story/outline/volume_map.md`（fallback `volume_outline.md`）找第 N 卷的 `[startCh, endCh]`。无 volume map → 返回 `ok=true` + warning，graceful 跳过。
2. 圈出本卷 in-volume 的 hooks（`vstart ≤ startChapter ≤ vend`）中，满足任一：
   - `payoffTiming ∈ {volume-end, mid-arc}`，或
   - `committedToChapter ∈ [vstart, vend]`，或
   - `coreHook=true`
3. 对每条：
   - status ∈ {resolved} 且 `lastAdvancedChapter ≤ vend` → ok（计入 `hooksResolvedByEnd`）。
   - 有 `committedToChapter > vend` → ok（forward-committed，下一卷的事）。
   - 有 `committedToChapter ∈ [vstart, vend]` 但未 resolved → **critical**（类别 `committed payoff missed`）——planner 自己许下的具体章节承诺破产。
   - `coreHook=true` 且未 resolved → **critical**（类别 `core hook unresolved at volume end`）。
   - 其余开着没收 → **warning**（类别 `hook opened but unresolved at volume end`）。
4. `payoffRate = hooksResolvedByEnd / hooksOpenedInVolume`。

**何时调**：[phase 00 orchestration](phases/00-orchestration.md) step 11.2——仅当本章是某卷的最后一章（`chapterNumber == volume.endCh`）时触发一次。critical issue 不阻断本章落盘（章节正文已经过 audit + apply_delta 闸门），但要回写到 chapters/index.json 的 `reviewNote` 提示作者：本卷尾还欠账，下卷 Planner 要先补完。

**与 §8c 的分工**：

| 子系统 | 时机 | 输入 | 输出粒度 |
|---|---|---|---|
| **commitment_ledger** | 本章 audit 之前 | chapter_memo + draft | 单章承诺 vs 单章正文 |
| **volume-payoff** | 本卷最后一章落盘后 | volume_map + hooks.json | 整卷开过的伏笔 vs 卷尾状态 |

两者都不会写真理文件——前者只是 issue 喂回 audit/reviser，后者只是回写 chapters/index.json 的 `reviewNote`。

---

## 9. 与 Phase 7 / Phase 9 的关系

- **Phase 7 (Settler)** 产 `hookOps.upsert`；只能引用现存 hookId，新候选写在 `newHookCandidates`。promote-pass 之后 seed 才会变成可被 upsert 的 hook。
- **Phase 9 (Auditor)** 在 "Hook Debt" 维度直接读 `hooks.json` 里的 `stale` / `blocked` 标志——这两个标志由 stale-scan 写。所以 audit 之前 stale-scan 必须跑过（apply_delta 已保证）。

---

## 10. 不要做的事

- **不要**手工编辑 `hooks.json` 加 `promoted=true`——promote-pass 会因找不到任何条件证据而把它当成 unjustified_promotion warning。
- **不要**用 LLM 决定 `coreHook` / `dependsOn`——这些是 architect 阶段的产物，settler 阶段只能透传，不能新增。
- **不要**把 hook-seeds.json 当成 ledger 用——seed 文件是"候选池"，写入 pending_hooks.md 的只能是 promoted hook。
