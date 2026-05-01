# Phase 08: Length Normalizer（单次长度修正）

## 何时进入

主循环在 Settler 之后、Auditor 之前调到这里。先用 `scripts/word_count.py` 量本章字数（中文按字符、英文按词），与 LengthSpec 比对：

- 在 `softMin..softMax` 之内 → **跳过本阶段**，直接进 Auditor。
- 在 `hardMin..hardMax` 之外 → 触发 normalizer，按方向选 `compress` 或 `expand`。
- 在 soft 与 hard 之间 → 选 normalizer 也可，由 chooseNormalizeMode 决定（缺省偏向"不动"，给 Auditor 用 spot-fix 处理）。

**只跑一次，不递归**。即使一次没把字数拉回 soft 区间，也直接交给 Auditor / Reviser 后续处理。

## Inputs

Claude 在这一阶段需要读：

- 刚写完且经 Settler 结算的章节正文（`chapters/<NNNN>.md`）
- `book.json` 里的 `lengthSpec`（target / softMin / softMax / hardMin / hardMax / countingMode / normalizeMode）
- `story/runtime/chapter_memo.md` 的 `goal` 字段作为 chapterIntent（保留章节意图，避免 Normalizer 改向）
- `story/runtime/rule_stack.json` 的精简版 controlBlock（resourceCaps / 主角 personalityLock 等不可越界）

## Process

Claude 在心中扮演"章节长度修正器"。模式由 `chooseNormalizeMode` 决定（参 inkos `length-metrics.ts`）：

- 当前字数 > hardMax 或 > softMax 较多 → `compress`（压缩）
- 当前字数 < hardMin 或 < softMin 较多 → `expand`（扩写）
- 在 soft 区间内 → `none`（直接返回原文，applied=false）

### 系统 prompt（搬自 inkos `length-normalizer.ts` L69-80，请 Claude 在心中扮演这个角色）

`compress` 模式：

```
你是一位章节长度修正器。你的任务是对章节正文做一次单次修正，只能执行一次，不得递归重写。

修正目标：
- compress 章节长度到给定目标区间
- 保留章节原有事实、关键钩子、角色名和必须保留的标记
- 不要引入新的支线、未来揭示或额外总结
- 不要在正文外输出任何解释
```

`expand` 模式：把第一行的 `compress` 替换为 `expand`，其余保持。

### 用户消息模板（搬自 inkos L95-115）

```
请对下面正文做一次{压缩|扩写}修正。

## Length Spec
- Target: {target}
- Soft Range: {softMin}-{softMax}
- Hard Range: {hardMin}-{hardMax}
- Counting Mode: {chinese-chars | english-words}

## Current Count
{originalCount}

## Correction Rules
- 只修正一次，不要递归
- 保留正文中的关键标记、人物名、地点名和已有事实
- 不要凭空新增子情节
- 不要插入解释性总结或分析
- 输出修正后的完整正文，不要加标签

## Chapter Intent
{chapterIntent}        # 仅在有时附加

## Reduced Control Block
{reducedControlBlock}  # 仅在有时附加

## Chapter Content
{chapterContent}
```

### 工作步骤

1. **数字数**：用 `scripts/word_count.py --mode <chinese-chars|english-words> chapters/<NNNN>.md`，拿到 originalCount + 区间判定（in-range / under / over）。
2. **决策模式**：
   - `lengthSpec.normalizeMode === "none"` → 用 chooseNormalizeMode 自动选；
   - 否则用 `lengthSpec.normalizeMode`（用户配置的强制模式）；
   - 选出 `compress` / `expand` / `none`。
3. **none → 直接返回**：不调 LLM，applied=false，warning=undefined。
4. **compress / expand → 跑一次 LLM**（temperature=0.2），按 prompt 输出修正后的完整正文。
5. **清洗输出**（参 inkos `sanitizeNormalizedContent`）：
   - 提取首个 ``` 围栏块作为正文（如果有）；
   - 剥除常见包装行：`下面是压缩后的正文`、`Here is the revised chapter`、`# 说明` 段头、`我将对正文做压缩处理` 等；
   - 如果剥除后剩余 < 50% → 保留原始 LLM 输出（避免误剥）；
   - 如果剥除后为空 → 回退到原章正文（normalizer 失败但不报错）。
6. **再数一次字数** → finalCount。
7. **生成 warning**：
   - finalCount 在 soft 区间内：无 warning
   - finalCount 在 soft 外、hard 内：`Final count {N} is outside the soft range {softMin}-{softMax} after one normalization pass.`
   - finalCount 在 hard 外：`Final count {N} is outside the hard range {hardMin}-{hardMax} after one normalization pass.`
8. **写回正文**：覆盖 `chapters/<NNNN>.md`。

## Output contract

- 修正后的章节正文写回 `chapters/<NNNN>.md`（覆盖式）
- 在 `story/runtime/normalize_log.json` 记录一条：
  ```json
  {
    "chapter": 12,
    "mode": "compress",
    "originalCount": 4321,
    "finalCount": 3287,
    "applied": true,
    "warning": null
  }
  ```
- 如果 warning 非空 → Auditor 阶段会读到并提高对长度问题的关注。

不需要独立 schema 文件——结构简单。

## Failure handling

- **LLM 输出无法清洗出有效正文**（剥除后为空、或仅含 ``` 围栏标记）→ 回退到原始正文，applied=false，warning=`"Normalizer output unparseable, fell back to original."`。
- **finalCount 比 originalCount 还远离目标**（修反方向了）→ 仍然写回（信任 LLM 一次），但 warning 强制设为 `"Normalizer pass moved further from target ({originalCount} → {finalCount})."`，Auditor 会处理。
- **绝不递归**：一章只允许 normalizer 一次。再次失控由 Auditor + Reviser（mode=spot-fix 或 polish）接管。

## 注意事项

- **temperature 低**（0.2）：本阶段是文本压扩，不是创作；要忠实于原意。
- **保留所有事实标记**：人名、地名、物品名、关键数值（"30 块灵石"）、关键钩子动作（"林秋摘下杂役腰牌"）必须原样保留。
- **不准引入新支线**：哪怕扩写时手痒——任何新角色、新地点、新事件都是越权。
- **不准插入解释性总结**：开头不要"本章主要讲了…"，结尾不要"以上就是修改后的内容"。
- **包装行清洗**：常见包装行模式有
  - `^```` 围栏行
  - `^#+ \s*(说明|解释|注释|analysis|analysis note)`
  - `^(下面是|以下是).*(正文|章节|压缩|扩写|修正|修改|调整|改写|润色|结果|内容|输出|版本)`
  - `^我先.*(压缩|扩写|修正|修改|调整|改写|润色|处理).*(正文|章节)?`
  - `^(here'?s| is|below is).*(chapter|draft|content|rewrite|revised|compressed|expanded|normalized|adjusted|output|version|result)`
  - `^i(?:'ll| will)\s+(rewrite|revise|reword|compress|expand|normalize|adjust|shorten|lengthen|trim|fix)`
- **chapterIntent 要带**：把 `memo.goal` 做为 chapterIntent 块附进 user 消息，避免 Normalizer 在压缩时砍掉关键剧情；扩写时引导补在原意范围内。
- **reducedControlBlock**：从 rule_stack.json 抽 protagonist.personalityLock + resourceCaps（如有）即可，不要把整个 ruleStack 都塞——会撑爆 token。
- **目标区间判断**：参 inkos `isOutsideSoftRange` / `isOutsideHardRange`；soft 是建议区间，hard 是硬约束。
- **Auditor 接力**：normalizer 警告会被 Auditor 看到——Auditor 第 33 维度（章节长度）会判定是否要触发 Reviser spot-fix 进一步处理。
- **不要把审稿当 Normalizer 用**：Normalizer 只动字数，不修事实漏洞 / 风格 / 节奏 / AI 味——那是 Auditor + Reviser 的活。
