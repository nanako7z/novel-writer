# 长跨度疲劳检测（Long-span Fatigue）

> `scripts/fatigue_scan.py` 的真理来源。源自 inkos `utils/long-span-fatigue.ts` + `cadence-policy.ts`。
> 单章扫描归 `ai_tell_scan.py`，跨章扫描归本脚本。Auditor 把两者的 issue 合并送 Reviser。

---

## 为什么需要跨章扫描

`ai_tell_scan.py` 只看当前一章——它能抓 hedge 密度、单章重复转折词、单章列表式句首；它**抓不到**：

- 同一个意象 / 同一个题材 fatigueWord 在 4 章里各用 1 次（单章不超阈）
- 4 章连续以"夜风吹过/雨打窗台/钟声响起"开篇（单章 OK，连看明显模板化）
- 3 章主结构都是"对话→揭露→爆发"（单章不算坏，连刷令人乏）
- A→B 角色关系连续 3 章都靠"吵→和"循环
- 跨 5 章里"瞳孔骤缩"出现 8 次（题材属于"仙侠"，本就该禁用）

跨章疲劳必须从滑窗角度检测。

---

## 检测项

| 类别 | 触发条件 | 默认严重度 | 备注 |
|---|---|---|---|
| `fatigue-word` | 题材 profile 的 `fatigueWords` 单词在窗口内累计 ≥ 3 次且横跨 ≥ 2 章 | warning（≥4 次升 warning，刚好 3 次为 info） | 仅在 `--genre-fatigue-words` 开启时检测 |
| `ngram` | 中文 3-5 字 n-gram（剔除粒子词 / 纯 ASCII / 高停用字比例）出现于 ≥ `--min-repeat` 章 | warning（命中章数 > min_repeat） / info | 长 n-gram 命中后会抑制其子串重复报告，避免噪声 |
| `opening-pattern` | 连续 ≥ 3 章首句使用同类入口（`weather` / `time` / `sound` / `dialogue` / `action`） | 4+ → critical；3 → warning | 与 `ai_tell_scan` 的"开头同构"互补：本检测看模式分类，`ai_tell_scan` 看字面相似度 |
| `conflict-trope` | 窗口内 ≥ 3 章主冲突形态相同（`fight-reveal` / `pure-fight` / `dialogue-reveal` / `dialogue-heavy` / `action-light`） | 3 → warning；≥4 → critical | 启发式：动作动词 + 揭示标记 + 对白密度组合判定 |
| `pair-overheat` | 同一 A→B 互动模式（bicker / flirt / threaten）连续 ≥ 3 章主导 | warning | 用关键词频次决定主导模式（每词需 ≥ 3 次才作数） |
| `style-drift` | 最近章节的 4 项风格指纹（`meanSentenceLen` / `meanParagraphLen` / `rhetoricalDensity` / `dialogueRatio`）相对窗口前 N 章 baseline 的 z-score：≥ 1.5σ → warning；≥ 2.5σ → critical | 见左 | 仅在 `--style-drift` 开启时检测；窗口需 ≥ 3 章；baseline stdev=0 时用 `mean*5%` 作 floor 防 false-quiet。**为什么独立**：单章 ai_tell 抓的是字面 / 句首 / 短模板，本检测抓的是**结构性偏移**——比如某章突然从"中长句叙事"切成"超短句感叹"是 LLM 风格漂移的早期信号 |

---

## 默认窗口 / 调参

```
--window 5              # 看回 N-5..N-1（含端点）
--min-repeat 2          # n-gram 重复门槛
--genre-fatigue-words   # 开关；默认 OFF（避免误伤改稿期）
--draft <path>          # 把当前未落盘的草稿当作第 N 章纳入窗口
```

窗口选择：

- 默认 5 是 inkos `summaryLookback=4` + 当前章的扩展。多于 5 容易把已经过去的剧情节奏当作疲劳；少于 3 则统计意义弱。
- 卷尾 / 大节点章节建议手动放大到 `--window 8`，因为节奏疲劳到此时才显现。
- 章节字数超大（超过 6000 字）的项目，建议 `--min-repeat 3`，否则 n-gram 会刷屏。

---

## 输出 schema

```json
{
  "currentChapter": 12,
  "windowChapters": [7, 8, 9, 10, 11],
  "issues": [
    {
      "severity": "warning",
      "category": "ngram",
      "description": "5-gram「瞳孔骤缩了一」在 3 章中重复出现，建议下章替换",
      "evidence": [
        {"chapter": 8, "text": "瞳孔骤缩了一"},
        {"chapter": 10, "text": "瞳孔骤缩了一"},
        {"chapter": 11, "text": "瞳孔骤缩了一"}
      ]
    },
    {
      "severity": "critical",
      "category": "opening-pattern",
      "description": "连续 4 章以「weather」型描写开篇，下章必须换入口",
      "evidence": [
        {"chapter": 8, "text": "夜风裹着..."},
        {"chapter": 9, "text": "雨水拍在..."},
        {"chapter": 10, "text": "雪粒打在..."},
        {"chapter": 11, "text": "云层压低..."}
      ]
    }
  ],
  "summary": "window=[7,8,9,10,11], issues=2 (critical=1, warning=1, info=0)"
}
```

退出码：**始终 0**——本脚本是建议性的，不阻断流水线。

---

## 严重度策略

- `critical`：连续 ≥ 4 章模式重复 / ≥ 4 章同冲突形态。Auditor 应当把这条当作 dim 25（节奏失控）或 dim 26（章节字数 / 标题疲劳的扩展）的硬证据，触发 Reviser 的 `polish` / `rework` 模式。
- `warning`：连续 3 章重复 / fatigueWord 累计 ≥ 4 次。下章 Planner 应避让；Auditor 不强制 fail。
- `info`：fatigueWord 刚好 3 次 / n-gram 刚好满足 min_repeat。仅作提示；Writer 在写下一章前心里有数即可。

---

## 与 Auditor / Composer 的衔接

**Auditor**（[09-auditor.md](phases/09-auditor.md)）：在跑 LLM audit 之前，先调用 `fatigue_scan.py`，把 issues 合并进 audit 的 issue 列表（同 `ai_tell_scan.py` 的处理方式）。critical 类提交给 Reviser；warning 类作 dim 25/26 的旁证；info 类只做记录。

**Composer / Planner**：跨章疲劳的 critical / warning 应当影响下一章 chapter_memo 的 doNot 段——Planner 在生成 memo 前可主动跑一次本脚本，把"避让 X 词 / 换 Y 入口 / 非 Z 形态"写进 memo 的"## 不要做"区。

**单点指令**：用户问"最近几章是不是越来越疲？"时，可以直接跑：

```bash
python scripts/fatigue_scan.py --book books/<id> --current-chapter <N> \
    --window 5 --genre-fatigue-words
```

---

## 实现注意

- 中文 n-gram 用字符切片，不切词。粒子字（的/了/在/是/和等）单独入 `ZH_STOP_CHARS`；含 ≥ 40% 停用字或首尾是停用字的 n-gram 被剔除。
- `opening-pattern` 不识别"other"——只要一章首句被分类成 other / empty 就打断连续计数。
- `pair-overheat` 需要每章至少触发 3 次某模式关键词，否则该章视为无主导模式（断开连续段）。
- `fatigue-word` 必须横跨 ≥ 2 章才记 issue——单章高频不属于"长跨度"问题（归 `ai_tell_scan`）。
- 没有 `--draft` 时窗口仅含已落盘章节（`chapters/{NNNN}.md`）；有 `--draft` 时把它视为第 N 章并入计算。
