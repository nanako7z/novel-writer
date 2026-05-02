# Phase 10: Reviser（按模式修章）

## 何时进入

主循环在 Auditor 给出 issues 后调到这里。Auditor 报告 critical / warning issue 数 ≥ 1（且整体分提升预期 ≥ 3 分）→ 进 Reviser。如果 Auditor 报告 critical=0 且 warning ≤ 阈值 → 跳过 Reviser 直接落盘。

每章 Reviser 最多回环 3 轮（参 inkos audit-revise loop 阈值），每轮跑完跑 Auditor 复审；连续 2 轮分数提升 < 3 → 提前退出。

## Inputs

Claude 在这一阶段需要读：

- 当前正文（`chapters/<NNNN>.md`）
- Auditor 输出（`story/runtime/audit_result.json`）——含 issues 数组（severity / category / description / suggestion）
- `story/runtime/chapter_memo.md`（保 chapterIntent / chapterMemo / contextPackage / ruleStack）
- `story/runtime/rule_stack.json`
- `story/current_state.md`、`story/pending_hooks.md`、`story/style_guide.md`、`story/outline/volume_map.md`、`story/outline/story_frame.md`、`story/character_matrix.md`、`story/chapter_summaries.md`
- `story/parent_canon.md`、`story/fanfic_canon.md`（如有）
- `book.json` + `references/genre-profiles/<genre>.md`（拿 numericalSystem 开关、language 等）
- 用户指定 mode（如果没指定，按下方决策树自动选）

## Process

Reviser 有 6 个模式，每个模式定义不同的"允许动什么、不允许动什么"。Claude 必须先按决策树确定 mode，再带上对应的 system prompt 修章。

### 6 模式定义（搬自 inkos `reviser.ts` L91-109）

```
auto: （由系统按 issues tier 自动路由——见 buildAutoSystemPrompt）

polish: 润色：只改表达、节奏、段落呼吸，不改事实与剧情结论。禁止：增删段落、改变人名/地名/物品名、增加新情节或新对话、改变因果关系。只允许：替换用词、调整句序、修改标点节奏

rewrite: 改写：允许重组问题段落、调整画面和叙述力度，但优先保留原文的绝大部分句段。除非问题跨越整章，否则禁止整章推倒重写；只能围绕问题段落及其直接上下文改写，同时保留核心事实与人物动机

rework: 重写：可重构场景推进和冲突组织，但不改主设定和大事件结果

anti-detect: 反检测改写：在保持剧情不变的前提下，降低 AI 生成可检测性。

改写手法（附正例）：
1. 打破句式规律：连续短句 → 长短交替，句式不可预测
2. 口语化替代：✗"然而事情并没有那么简单" → ✓"哪有那么便宜的事"
3. 减少"了"字密度：✗"他走了过去，拿了杯子" → ✓"他走过去，端起杯子"
4. 转折词降频：✗"虽然…但是…" → ✓ 用角色内心吐槽或直接动作切换
5. 情绪外化：✗"他感到愤怒" → ✓"他捏碎了茶杯，滚烫的茶水流过指缝"
6. 删掉叙述者结论：✗"这一刻他终于明白了力量" → ✓ 只写行动，让读者自己感受
7. 群像反应具体化：✗"全场震惊" → ✓"老陈的烟掉在裤子上，烫得他跳起来"
8. 段落长度差异化：不再等长段落，有的段只有一句话，有的段七八行
9. 消灭"不禁""仿佛""宛如"等 AI 标记词：换成具体感官描写

spot-fix: 定点修复：只修改审稿意见指出的具体句子或段落，其余所有内容必须原封不动保留。修改范围限定在问题句子及其前后各一句。禁止改动无关段落
```

### spot-fix 模式的两种产物形态

spot-fix 模式 LLM 可以**任选其一**：

1. **整章 REVISED_CONTENT 重出**——和其他模式一样，覆盖 `chapters/<NNNN>.md`。简单稳健，但 LLM 容易顺手改到不该改的段。
2. **结构化 patches**（推荐，定点专用）——LLM 只输出一份 patches.json，Claude 调 `scripts/spot_fix_patches.py` 在本地确定性应用，从源头杜绝越界改动。

#### patches.json 形状

```json
{
  "patches": [
    {
      "line": 42,
      "find": "原句中需要替换的精确字符串（必须能在该行附近唯一匹配）",
      "replace": "替换后的字符串",
      "reason": "对齐 audit issue #3：避免直白心理标签"
    },
    {
      "line": 87,
      "find": "another exact span",
      "replace": "rewritten",
      "reason": "..."
    }
  ]
}
```

字段：

- `line` — 1-based 行号锚点。脚本默认在 ±2 行窗口内找 `find`，找不到再退回全文唯一匹配
- `find` — 精确子串。窗口内找不到精确匹配 → 自动退回 whitespace-normalized 模糊匹配（仅当目标长度 ≥10 字节）。两种匹配都要求在窗口里**唯一**，多重匹配直接报错
- `replace` — 替换字符串，可多行；行数变化不影响后续 patch 的 line 锚点（脚本顺序处理，但每条只在自己的 ±window 里找，不依赖固定行号）
- `reason` — 可选；只用于日志，不参与匹配

#### 调脚本

```bash
python {SKILL_ROOT}/scripts/spot_fix_patches.py \
  --file books/<id>/chapters/0042.md \
  --patches /tmp/spot_fix_patches.json \
  [--out <path>] \
  [--dry-run] \
  [--anchor-window 2] \
  [--json]
```

- `--dry-run`：只报告哪些 patch 能 / 不能匹配，不写文件
- 不带 `--out` 时**原地覆盖** `--file`，使用原子写（`.tmp` + `os.replace`）
- `--json` 给 Claude 回吃：`{totalPatches, applied, skipped, appliedDetails: [{index, line, mode, ...}], errors: [{index, error, patch}]}`
- 退出码：`0` 全部成功 / `1` 部分成功或全失败 / `2` 致命（文件或 JSON 不存在）

#### 落到主循环里

spot-fix 模式 §"工作步骤" 第 5 步可以分两叉：

- **整章 REVISED_CONTENT** 路径：照原流程，把 LLM 输出覆盖 `chapters/<NNNN>.md`
- **patches** 路径：把 LLM 给的 patches.json 落到 `story/runtime/chapter-{NNNN}.spot-patches.json`，调上面的脚本，得到 `applied/skipped` 摘要后再决定是否回环 Auditor

patches 路径的好处：

1. **物理保证**不越过模式动作半径——脚本只动 `find` 命中的 span，问题句之外的 0 字节都不会被改
2. **可审计**——`appliedDetails` 数组完整记录哪行哪段被改、改了多少字节、用了 exact 还是 fuzzy 模式
3. **错误透明**——非唯一匹配 / 找不到 `find` 时，把 `errors` 数组喂回 LLM 让它修 patch（而不是默默放弃）

### 模式选择决策树

> 用户显式指定 mode → 直接用。否则按下面顺序判定：

```
if issues 全是用户指定的单点（"把第 3 段那句改一下"）：
    → spot-fix
elif issues 由 ai_tell_scan / sensitive_scan 主导（AI 味 / 敏感词专项）：
    → anti-detect
elif critical >= 1 且 critical 全部是台词 / 表达 / 语言层（dim 14 / 25 / 27 等）且不涉剧情：
    → polish
elif critical >= 1 且 critical 涉及剧情走向 / 角色动机 / 大事件结果：
    → rework
elif issues 跨剧情 + 表达 + 节奏 + 长度 多类混合：
    → auto    # 让 system prompt 内自决，按 tier 区别处理
else:
    → rewrite # 默认稳健挡，围绕问题段落改写但保留主体
```

各模式的"动作半径"（从小到大）：

| mode        | 动作半径                                          | 改剧情？ | 改人名？ | 改段落数？ |
|-------------|--------------------------------------------------|----------|----------|------------|
| spot-fix    | 问题句 ± 1 句                                    | 否       | 否       | 否         |
| polish      | 句内 / 段内表达                                  | 否       | 否       | 否         |
| anti-detect | 全章句式 / 用词 / 段长（保剧情）                  | 否       | 否       | 段长可变   |
| rewrite     | 问题段落及其直接上下文                            | 否（保事实）| 否    | 段落可重组 |
| rework      | 场景推进与冲突组织                                | 部分（保主设定 / 大事件结果） | 否 | 段落可重组 |
| auto        | 由 system prompt 内 tier 路由——critical 段重写 / warning 段补丁 / info 段保留 | 视 issue 而定 | 否 | 视 issue 而定 |

### 工作步骤

1. **确定 mode**：用户给了就用，没给按上面决策树挑。
2. **构造 issueList**：
   - mode=auto → 按 severity 分 tier：`## Critical（必须解决）` / `## High（应当改善）` / `## Medium（参考建议）`，每条 `[severity] category: description` + `建议: suggestion`
   - 其余 mode → 平铺一份带 severity 的列表
3. **拼 system prompt**：
   - 公共前缀（langPrefix / 题材底色 / 主角人设锁定 / 数值规则 / 长度护栏）
   - 模式特定段（上方 6 模式定义的对应文本）
   - mode=auto 时用 `buildAutoSystemPrompt`（按 tier 区别处理 critical / warning / info）
4. **拼 user message**：附正文 + issues + 资源账本（数值题材）+ 伏笔池 + 关键真理文件 + 章节意图 + memo + 上下文包 + 卷纲 + 角色矩阵 + 章节摘要
5. **生成修订正文**：以 `=== REVISED_CONTENT ===` 起首的纯文本块（同时可能附 `=== FIXED_ISSUES ===` / `=== PATCHES ===` / `=== UPDATED_STATE ===` / `=== UPDATED_HOOKS ===` 子块；v1 简化为只读 REVISED_CONTENT，其余给 Settler 走 delta 路径）
6. **写回**：覆盖 `chapters/<NNNN>.md`，并把 issues 与 fix 操作记录到 `story/runtime/revise_log.json`
7. **回环 Auditor**：跑一次 Auditor，看分数是否提升 ≥ 3；不达阈值或回环已 3 轮 → 退出

## Output contract

- 修订后的正文写回 `chapters/<NNNN>.md`（覆盖式）
- `story/runtime/revise_log.json` 追加：
  ```json
  {
    "chapter": 12,
    "round": 1,
    "mode": "rework",
    "issuesIn": 8,
    "issuesFixed": 5,
    "newScoreEstimate": 78
  }
  ```
- 如果产生 state / hook 增量，必须**重跑 Settler** 生成新的 RUNTIME_STATE_DELTA，而不是 Reviser 自己改真理文件

## Failure handling

- **正文回退**：Reviser 输出无法解析 / 长度跌出 hard 区间 / 显著破坏剧情（critical 数反而上升）→ 整轮回退原正文，把这次操作记成失败，进入下一轮（仍占用 3 轮预算之一）。
- **轮次预算**：每章 Reviser 最多 3 轮。
- **提前退出**：连续 2 轮分数提升 < 3 → 退出，把当前最佳版本落盘并向用户报告剩余 critical / warning。
- **mode 选择失误**：用户后续可显式指定其它 mode 重跑（不消耗当章 3 轮预算）。
- **数值不平**（数值题材修完后期初 + 增量 ≠ 期末）→ 强制 spot-fix 一轮专修数值；仍不平 → 报错给用户。

## 注意事项

- **不要扩范围**：spot-fix 不要顺手改隔壁段；polish 不要顺手改剧情；rewrite 不要顺手改人名。每次改完自检"我有没有越过本模式的动作半径"。
- **保留主角人设锁定**：bookRules.protagonist.personalityLock 是硬约束，所有模式都必须遵守。
- **数值题材数值不能凭空变**：`30 块灵石 + 失去 5 → 25 块`，修完一遍要回算。
- **Auto 模式的内部分层**：critical 当 rework / rewrite 的力度处理；warning 当 polish / spot-fix 的力度处理；info 不强制处理。
- **anti-detect 不要过度俗化**：第 2 条"口语化替代"是手段不是目标——文学性的"然而"、"宛如"在合适语境下保留即可，专砍重复出现的 AI 标记。
- **不重写 hookId 与人名**：所有模式都禁止动 hook_id、角色名、地点名、关键物品名（这些是真理文件锚点）。
- **修完跑一次 word_count.py**：长度跌出 hard 区间 → 回退或触发 Normalizer 再修一次。
- **English book**：所有 issueList、tier 标题切英文（`## Critical — Must Fix` / `## High — Should Improve` / `## Medium — Reference`）；REVISED_CONTENT 跟原文语言。
- **lengthSpec 护栏**：mode ≠ auto 时附"保持章节字数在目标区间内；只有在修复关键问题确实需要时才允许轻微偏离"；mode = auto 时长度交给 Normalizer，不在 Reviser 侧硬约束。
- **同人 / 续作 canon 锚点**：parent_canon.md / fanfic_canon.md 中的硬约束（人物语癖 / 设定边界）所有模式都不能违反。
