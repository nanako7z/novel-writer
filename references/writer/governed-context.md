# Writer Sub-Reference — 上下文治理与字数治理（Governed Context + LengthSpec）

## 功能说明

本文件汇总 Writer 系统 prompt 中**两组负责"拉硬约束"的段**：

1. **§2 输入治理契约 + 章节备忘对齐**——声明 chapter_memo / Variance Brief / Hook Debt 这些 governed-input 才有的高优先级输入；同时让 Writer 把 chapter_memo 7 段当作硬约束逐段落地。`inputProfile == "governed"` 时启用（v1 默认 governed）。
2. **§3 LengthSpec 字数治理**——把 `book.chapterWordCount` 解算成 5 个区间值（target / softMin / softMax / hardMin / hardMax），让 Writer 自我约束、Normalizer (08) 单次修正、Auditor 复核。恒启。

这两段共同把"本章要写什么"和"本章能写多长"两条最硬的约束钉死在 prompt 顶部偏后的位置——在题材引言之后、写作工艺卡之前——让 Writer 在风格还没展开前先吃上 governed-input 的优先级矩阵和字数硬区间。

启用条件：§2 仅在 governed 模式启用（v1 默认开启），§3 恒启。两段缺一不可的场景：governed 模式下若 §2 缺失，Writer 会把卷纲当全局最高规则、把 chapter_memo 当参考资料而非硬约束。

---

### 2. 输入治理契约 + 章节备忘对齐（governed 模式启用）

**作用**：声明 chapter_memo / Variance Brief / Hook Debt 这些 governed-input 才有的高优先级输入；同时让 Writer 把 chapter_memo 7 段当作硬约束逐段落地。

**何时启用**：`inputProfile == "governed"` 时（v1 默认 governed）。

#### 2.1 输入治理契约

```
## 输入治理契约

- 本章具体写什么，以提供给你的 chapter intent 和 composed context package 为准。
- 卷纲是默认规划，不是全局最高规则。
- 当 runtime rule stack 明确记录了 L4 -> L3 的 active override 时，优先执行当前任务意图，再局部调整规划层。
- 真正不能突破的只有硬护栏：世界设定、连续性事实、显式禁令。
- 如果提供了 English Variance Brief，必须主动避开其中列出的高频短语、重复开头和重复结尾模式，并完成 scene obligation。
- 如果提供了 Hook Debt 简报，里面包含每个伏笔种下时的**原始文本片段**。用这些原文来写延续或兑现场景——不是模糊地提一嘴，而是接着读者已经看到的具体承诺来写。
- 如果显式 hook agenda 里出现了可回收目标，本章必须写出具体兑现片段，回答种子章节中读者的原始疑问。
- 如果存在 stale debt，先消化旧承诺的压力，再决定是否开新坑；同类 sibling hook 不得随手再开。
- 多角色场景里，至少给出一轮带阻力的直接交锋，不要把人物关系写成纯解释或纯总结。
```

#### 2.2 章节备忘对齐（7 段合约）

```
## 章节备忘对齐

你将收到本章的 chapter_memo，由 7 段 markdown 组成：

- ## 当前任务 → 本章必须完成的具体动作，写作时始终对齐这条
- ## 读者此刻在等什么 → 控制情绪缺口的制造/延迟/兑现程度
- ## 该兑现的 / 暂不掀的 → 本章必须兑现的伏笔清单 + 必须压住不掀的底牌
- ## 日常/过渡承担什么任务 → 非冲突段落的功能映射（[段落位置] → [承担功能]）
- ## 关键抉择过三连问 → 关键人物选择必须过的检查
- ## 章尾必须发生的改变 → 结尾落地的 1-3 条具体改变（信息/关系/物理/权力）
- ## 不要做 → 硬约束红线

写作时按段落顺序落实，每一段都要在正文里有对应的兑现痕迹。如果某一段没有体现到正文里，本章不算完成。
```

> Auditor 阶段 (09) 会逐段反查"是否在正文里留下兑现痕迹"，不留即扣 dim 1（章节备忘对齐）的分。

---

### 3. 长度规格（LengthSpec）—— 字数治理

**作用**：把 `book.chapterWordCount` 解算成 5 个区间值（target / softMin / softMax / hardMin / hardMax），让 Writer 自我约束、Normalizer (08) 单次修正、Auditor 复核。

**何时启用**：恒启。

**计算口径**（参 `utils/length-metrics.buildLengthSpec`）：
- `countingMode`: `"chinese"`（按字符数）/ `"english"`（按 word 数）。
- `target = book.chapterWordCount`。
- `softMin / softMax = target × 0.85 / 1.15`。
- `hardMin / hardMax = target × 0.70 / 1.30`。

**Verbatim 注入**：

```
## 字数治理

- 目标字数：<target>字
- 允许区间：<softMin>-<softMax>字
- 硬区间：<hardMin>-<hardMax>字
```

> 越过 softMin/softMax 但未越 hardMin/hardMax → Normalizer 单次修正；越 hard 区间 → 直接打回 Writer 重写。

---

## 与上层 Writer 阶段的关系

在 Writer system prompt 拼装顺序中：

- **§2 紧跟 §1 题材引言**：身份定完立刻交代"今天的输入哪个最大、卷纲是不是最高规则、chapter_memo 7 段怎么落"——这是 governed 模式区别于"自由发挥"模式的核心切片；
- **§3 紧跟 §2**：把字数硬区间钉在 chapter_memo 之后、写作工艺卡之前——让 Writer 知道"在 chapter_memo 7 段都得落地的前提下，正文要在 [softMin, softMax] 区间内、最坏不能越 [hardMin, hardMax]"。

下游消费：Auditor dim 1（章节备忘对齐）逐段反查 §2.2 的 7 段；`scripts/word_count.py` 用 §3 的 5 个区间值判 in-soft / over-hard 状态；Normalizer (08) 把 over-soft-but-in-hard 的稿子拉回 soft 区间。

回主文件参见 [phases/05-writer.md](../phases/05-writer.md)。
