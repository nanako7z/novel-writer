# 去 AI 味词表与检测阈值

> 移植自 inkos `packages/core/src/agents/ai-tells.ts`（结构性检测）+ `writer-prompts.ts` L229-242（5 铁律）。本文件是 `scripts/ai_tell_scan.py` 的真理来源，也是 Auditor 评判 dim 20-23 的依据。

---

## 检测目标

Auditor 维度对应：

| dim | 名称 | 触发条件 |
|---|---|---|
| 20 | 段落等长 | 段落长度变异系数 CV < 0.15（≥3 段） |
| 21 | 套话密度 | hedge 词 > 3 次/千字 |
| 22 | 公式化转折 | 任一 transition 词单章重复 ≥ 3 次 |
| 23 | 列表式结构 | 连续 ≥ 3 句使用相同句首前缀（中文取前 2 字 / 英文取首词） |

---

## HEDGE_WORDS（套话/犹豫词）

逐字搬自 inkos `ai-tells.ts` L24-27：

**zh**
```
似乎、可能、或许、大概、某种程度上、一定程度上、在某种意义上
```

**en**
```
seems, seemed, perhaps, maybe, apparently, in some ways, to some extent
```

匹配规则：中文用 `g` 标记、不转大小写；英文用 `gi`（忽略大小写）。每千字命中数 > 3 即触发 dim 21。

修复方向（Reviser 提示）：用确定性叙述替代模糊表达——去掉「似乎」直接描述状态，用具体细节替代「可能」。

---

## TRANSITION_WORDS（转折/衔接词）

逐字搬自 inkos `ai-tells.ts` L29-32：

**zh**
```
然而、不过、与此同时、另一方面、尽管如此、话虽如此、但值得注意的是
```

**en**
```
however, meanwhile, on the other hand, nevertheless, even so, still
```

匹配规则同上。**任一** transition 词在单章出现 ≥ 3 次即触发 dim 22；issue 中列出所有超阈词及计数（格式 `"<word>"×<count>`，中文以 `、` 拼接，英文以 `, `）。

修复方向：用情节自然转折替代转折词，或换用不同的过渡手法（动作切入、时间跳跃、视角切换）。

---

## 检测阈值汇总

| 检测项 | 阈值 | 触发条件 | 严重度 |
|---|---|---|---|
| 段落长度 CV | < 0.15 | 段落数 ≥ 3 时 | warning |
| hedge 密度 | > 3 / 千字 | 全文统计 | warning |
| transition 重复 | ≥ 3 次 | 单词单章计数 | warning |
| 列表式句首 | ≥ 3 句连续 | 中文取首 2 字 / 英文取首词 | info |

> CV = stdDev / mean，按段落字符数统计。inkos 原算法以 `\n\s*\n` 切段、`[。！？\n]` 切句（英文 `[.!?\n]`），过滤后空字符串与长度 ≤ 2 的句子被剔除。

---

## 5 铁律（来自 writer-prompts.ts L229-242，verbatim）

Writer 必须遵守、Auditor 必须强制——以下五条逐字搬运，不得改写：

```
- 【铁律】叙述者永远不得替读者下结论。读者能从行为推断的意图，叙述者不得直接说出。✗"他想看陆焚能不能活" → ✓只写踢水囊的动作，让读者自己判断
- 【铁律】正文中严禁出现分析报告式语言。三类术语黑名单（`scripts/ai_tell_scan.py` ANALYSIS_TERMS + `scripts/post_write_validate.py` REPORT_TERMS 同步维护）：(1) 推理报告标签——"核心动机""信息边界""信息落差""核心风险""利益最大化""当前处境""行为约束""性格过滤""情绪外化""锚定效应""沉没成本""认知共鸣""推理框架"；(2) Planner 节奏方法学——"情绪缺口""蓄压""释放阶段""后效阶段""cyclePhase""satisfactionPressure""satisfactionType""期待管理"；(3) 写作教学术语——"叙事张力""叙事节奏""叙事弧线""人物弧光""角色弧光""三幕结构""起承转合""情节驱动""戏剧反讽""主题升华"。人物内心独白必须口语化、直觉化。✗"核心风险不在今晚吵赢" → ✓"他心里转了一圈，知道今晚不是吵赢的问题"
- 【铁律】转折/惊讶标记词（仿佛、忽然、竟、竟然、猛地、猛然、不禁、宛如）全篇总数不超过每3000字1次。超出时改用具体动作或感官描写传递突然性
- 【铁律】同一体感/意象禁止连续渲染超过两轮。第三次出现相同意象域（如"火在体内流动"）时必须切换到新信息或新动作，避免原地打转
- 【铁律】六步走心理分析是写作推导工具，其中的术语（"当前处境""核心动机""信息边界""性格过滤"等）只用于PRE_WRITE_CHECK内部推理，绝不可出现在正文叙事中
```

附反例→正例（同源）：

```
✗"虽然他很强，但是他还是输了" → ✓"他确实强，可对面那个老东西更脏"
✗"然而事情并没有那么简单" → ✓"哪有那么便宜的事"
✗"这一刻他终于明白了什么是力量" → ✓删掉，让读者自己感受
```

附硬性禁令（writer-prompts.ts L240-242）：

```
- 【硬性禁令】全文严禁出现"不是……而是……""不是……，是……""不是A，是B"句式
- 【硬性禁令】全文严禁出现破折号"——"，用逗号或句号断句
- 正文中禁止出现 hook_id / 账本式数据，数值结算只放 POST_SETTLEMENT
```

铁律 3 中的"突然性词表"（仿佛/忽然/竟/竟然/猛地/猛然/不禁/宛如）由 `ai_tell_scan.py` 额外扫描——总计数 > 章节字数 / 3000 即触发 warning（与 hedge 同等严重度）。

---

## 严重度映射

inkos `analyzeAITells` 输出 `"warning" | "info"`：

| 检测项 | severity |
|---|---|
| dim 20 段落等长 | warning |
| dim 21 hedge 密度 | warning |
| dim 22 transition 重复 | warning |
| dim 23 列表式结构 | info |
| 突然性词超阈（5 铁律） | warning |
| 五铁律语义违规（推理术语入正文等） | critical（由 Auditor LLM 判，脚本扫不到） |
| 硬性禁令（"不是...而是..." / "——"） | critical（脚本可正则扫） |

Auditor 汇总时：`warning` ≥ 3 条或 `critical` ≥ 1 条 → 触发 Reviser 的 `anti-detect` / `polish` 模式。

---

## `scripts/ai_tell_scan.py` 接口契约

脚本职责（与 inkos `analyzeAITells` + 5 铁律的脚本可扫部分对齐）：

1. 输入：`--text <path>`（章节正文）+ 可选 `--language zh|en`（默认 `zh`）。
2. 检测项（按 inkos 算法 1:1 实现）：
   - 段落 CV：以 `\n\s*\n` 切段、统计字符数、计算 CV，< 0.15 触发。
   - hedge 密度：按上述 zh/en 词表全文 `findall`，归一化到每千字。
   - transition 重复：按词表逐词计数，记录所有 ≥ 3 的词。
   - 列表式句首：按 `[。！？\n]`（zh）/ `[.!?\n]`（en）切句，跳过长度 ≤ 2，统计连续相同前缀的最大长度，≥ 3 触发。
   - 突然性词（铁律扩展）：词表 `["仿佛","忽然","竟","竟然","猛地","猛然","不禁","宛如"]`，总计数 > `len(text) / 3000` 触发。
   - 硬性禁令正则：`不是.{0,12}(?:而是|，\s*是)`、`——` 命中即 critical。
3. 输出 JSON 到 stdout：
   ```json
   {
     "issues": [
       {"severity":"warning","category":"段落等长","description":"...","suggestion":"..."},
       {"severity":"warning","category":"套话密度","description":"...","suggestion":"..."}
     ],
     "metrics": {
       "paragraph_count": 17,
       "paragraph_cv": 0.123,
       "hedge_per_1k": 4.2,
       "transition_counts": {"然而": 5, "不过": 3},
       "max_same_prefix_run": 4,
       "abrupt_word_total": 7,
       "char_count": 3024
     }
   }
   ```
4. 退出码：
   - `0`：无 warning / critical（仅 info 也算 0）
   - `1`：至少一条 critical（硬性禁令命中）
   - `2`：脚本错误
5. 中文文案与提示语必须与 inkos 原 `description` / `suggestion` 字符串保持一致（已在上方各小节给出）。
6. 不修复，不调用 LLM。

---

## 与 Auditor / Reviser 的衔接

- **Auditor**（09-auditor.md）：dim 20-23 评分直接读 `ai_tell_scan.py` 的 metrics；五铁律的语义违规（推理术语入正文等）由 Auditor LLM 二次判定。
- **Reviser**（10-reviser.md）：当主要 issue 来自本表 → `anti-detect` 模式优先；段落 CV 低 → `polish` 模式重排段落。
- **Writer**（05-writer.md）：在"去AI味铁律"段落内联 5 铁律全文，写作时主动避让；但 Writer 不能依赖自检——`ai_tell_scan.py` 是确定性闸门。
