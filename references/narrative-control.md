# Narrative Control（叙事控制 / 文本卫生）

> 移植自 inkos `utils/narrative-control.ts`。一个 stdlib-only 的小 sanitizer，专门用来在文本进入 Writer / Reviser 的 prompt **之前**做最后一道清洗。

## 何时需要

写作主循环里有一些"含治理元数据"的文本会被 Composer 拼进 Writer 的 prompt 里——典型来源：

- `chapter_memo.md`：里头会出现 `H001` / `kebab-case-hook-slug` 这类 hook ID 与 hook slug
- 滑窗记忆的相关章摘录（含 `第 12 章` / `Chapter 12` 这种章节 ref）
- Planner 给的"前几章发生了什么"段落（带"前几章"、"本章要做的是"这种系统语气）
- 风格分析的 evidence 片段（带"仿佛 / 似乎"这种 AI 转折标志）

如果原样塞进 Writer prompt：
1. Writer 会把 `H001` 当成正文标记跟着写，破坏沉浸感（去 AI 味铁律里明令禁止 hook_id 出现在正文）
2. "本章要做的是…"、"前几章…"这种系统口吻会被 Writer 当作叙事声音模仿，写出"系统播报式"开头
3. "仿佛 / 似乎"是 §12 去 AI 味铁律盯防的转折/惊讶标记词，源自 prompt 里就该提前压住

`narrative_control.py` 就是为这个上游清洗设的。它**不替代** Writer 自己的去 AI 味铁律（§12），是它前面的那道滤纸。

## 用法

```bash
python {SKILL_ROOT}/scripts/narrative_control.py \
  --file <text.md> \
  [--lang zh|en] \
  [--no-strip-entities] [--no-soften] \
  [--json] [--out <path>]
```

两个独立 pass，**默认都开**：

| pass | 默认 | 作用 |
|---|---|---|
| `--strip-entities` | on | 把 `H\d+`（hook ID）、`kebab-case-slug`（hook 别名）、`第 X 章` / `Chapter X`（章节 ref）替换为中性短语（`这条线索` / `此前`，英文 `this thread` / `an earlier scene`）|
| `--soften` | on | 应用 zh / en 小词替换：`仿佛→像`、`似乎→像是`、`前几章→此前`、`本章要做的是→眼下要处理的是`、`previous chapters→earlier scenes`、`this chapter needs to→the current move is to` |

`--json` 输出：`{originalLength, sanitizedLength, language, replacements: [{pattern, replacement, count}], sanitized}`，方便 Composer 把命中数据回写 runtime log。

## 在主循环里的位置

Composer (phase 03) 装配 `composed_context.md` 之前，对以下输入跑一遍：

1. 从 `pending_hooks.md` / `chapter_memo.md` 抽出来的"上下文 evidence" 段
2. 滑窗记忆 (`memory_retrieve.py`) 输出里的相关章摘要
3. 风格指纹 evidence（如果启用风格模仿且参考文是带 hook ID 的旧稿）

注意：

- **不要对 chapter_memo.md 主体跑这个**——memo 的 7 段标题就是治理结构，sanitize 会把 `## 该兑现的 / 暂不掀的` 里的 hook ID 全替换成"这条线索"，反而让 Writer 失去具体目标。仅对要喂给 Writer **prompt** 的派生文本用。
- 与 §12 去 AI 味铁律是**叠加关系**：上游 sanitize 拦截"系统口吻 + 元数据泄漏"，下游 `ai_tell_scan.py` 拦截 Writer 自己生成的 AI 味。两者词表有少量重合（仿佛 / 似乎），不冲突——双重把关。

## 设计取舍

- **激进替换 over 保留语义**：`仿佛→像` 在文学语境里有时是损失，但 Writer prompt 是"指令文档"不是文学，这里宁愿过度清洗
- **hook slug 用 `[a-z]+(?:-[a-z]+){1,3}` 匹配**：可能误伤"some-english-phrase"这种短英文连字符词；语料几乎全中文，误伤率低；接受
- **替换字符串硬编码**：与 inkos 源保持一致，不引入用户可配项以免每次注入 prompt 漂移
