# Phase 11 — Polisher（文字层打磨）

> ⛔ **硬约束 / 不跳步**：
> 1. **前置**：audit 真正过线（`overall_score >= 88` 且 `passed`）；借线（85-87）**直接跳过**，不冒险；audit 未过线**严禁**入场（让 Reviser 兜底）
> 2. **本阶段必跑**：单 pass，磨表面不改结构；**禁止**开回环；polish 后 `ai_tell_scan` + `sensitive_scan` 必须复检
> 3. **退出条件**：`pre-polish.md` 备份 + `polish.json` 落盘；polishedContent 接管 draft；引入新 critical/block 即**回退原版**（log `polish-reverted-introduced-issues`）
> 4. **重试规则**：n/a（单 pass，不重跑）

> Polisher 是 audit 通过之后再走一道的**独立后置**润色阶段。和 Reviser 不同：Reviser 在 audit 失败时入场修结构、补 issue；Polisher 只在 audit 已通过的稿子上磨**文字表面**——句式、段落、用词、五感、对话自然度。**严禁动情节、人设、主线**。

---

## 何时进入

- **仅在 audit 已通过**（`audit.overall_score >= PASS=85` 且无 critical / 长度在 soft 区间内 / 敏感词未 block）之后才入场。
- 主循环里的位置：在 step 10 的 `apply_delta.py` 成功之后、step 11"章节正文落盘到 `chapters/{NNNN}.md`"之前——见 [00-orchestration.md](00-orchestration.md) 的 step 10.5。
- **审计未通过则跳过**——审计失败由 [phase 10 reviser](10-reviser.md) 处理，Polisher 不入场。
- **借线规则（borderline 跳过）**：分数虽过线但贴得太紧（`85 <= score < 88`），跳过 Polisher 直接落盘——这种稿子改动风险大于收益，宁可保守。
- **单点指令**："整章 polish"由用户主动触发时也走本 phase，不需要先跑 audit；但用户明确要"改剧情/改人设"时不走这里，走 Reviser 的对应模式。

## Inputs

Polisher 这一阶段读以下文件：

1. **章节正文** —— 直接读 `story/runtime/chapter-{NNNN}.normalized.md`（audit 通过的最终态）。注意：此时章节**尚未**写到 `chapters/{NNNN}.md`，Polisher 的输出才是最终落盘版本。
2. `story/runtime/chapter_memo.md` —— 用于让 Polisher 知道"本章要兑现什么"，避免润色时不小心把已兑现的情绪缺口磨没了。本段在 system prompt 之外作为 user message 注入（详见 §Process）。
3. `genre_profiles/<genre>.md` 的 `fatigueWords` 列表 —— Polisher 在打磨用词时需要避让这些题材专属高疲劳词（同一章不要再叠加）。
4. `book.json#language` —— 决定走中文 system prompt 还是英文 system prompt。
5. （可选）`story/style_guide.md` —— 让 Polisher 知道用户对文风的偏好，避免润色把作者风格磨平。

**不读**：真理文件（`story/state/*.json`、`hooks.json` 等）。Polisher 不动设定，所以不需要它们。

## Process

### 1. 系统 prompt（中文版，verbatim 来自 `polisher.ts` `buildChineseSystemPrompt()`）

```
你是一位专业中文网文文字层润色编辑。

## 润色边界（硬约束）

你只改文字层——句式 / 段落 / 排版 / 用词 / 五感 / 对话自然度。你禁止增删情节、改变人设、调整主线。发现情节/结构问题只能以 [polisher-note] 形式附在章末供下一轮 reviewer 参考，不能动正文。

结构的事归 Reviewer，不归你。如果读到人设崩、主线偏、冲突缺、memo 未兑现之类的问题，保留原意，不要替作者补情节。

## 6 条文笔类雷点（你要消灭的）

- 描写无效：冗长的环境描写、与主线无关的对话塞满页面。把无效描写删到"一笔带过"。
- 文笔华丽过度：为辞藻堆辞藻，情感失真，形容词地毯轰炸。让文字服从情绪，不要炫技。
- 文笔欠佳：句意含混、指代不清、逻辑跳跃、语言干瘪。重写成通顺、有画面感的句子。
- 排版不规范：段落过长、格式不统一、对话无换行。统一为手机阅读友好格式。
- （延伸）AI 味痕迹：转折词泛滥、"了"字堆砌、"仿佛/宛如/竟然"等情绪中介词、编剧旁白、分析报告式语言。替换成口语化表达或具体动作。
- （延伸）群像脸谱化：不写"众人齐声惊呼"，而是挑 1-2 个角色写具体反应。

## 文字层硬规约

- 段落：3-5 行/段（手机阅读），连续 7 行以上必须拆段，但不可把动作+反应拆碎到失去节奏。
- 句式：多样化，禁止连续 3 句以上同结构/同主语开头；长短交替。
- 动词 > 形容词：名词+动词驱动画面，一句话最多 1-2 个精准形容词。
- 五感代入：场景里至少 1-2 种感官细节（视/听/嗅/触/味），但不机械叠加，适度即可。
- 对话自然度：
  - 不同角色说话方式有辨识度（用词、句子长短、口头禅、方言痕迹）。
  - 对话符合说话人当前身份、情绪、信息掌握。
  - 不写"……"式敷衍应答替代实质交锋。
- 情绪外化：把"他感到愤怒"改为"他捏碎了茶杯，滚烫的茶水流过指缝"。
- 删除无意义的叙述者结论（"这一刻他终于明白了力量"—删）和"显然/不禁/仿佛"这类 AI 标记词。
- 禁止破折号 "——"，禁止"不是……而是……"句式（存量出现一律改写）。

## 输出契约

直接返回润色后的完整章节正文——不要 JSON、不要章节标题行、不要任何解释或进度说明。如果发现必须交给 reviewer 的情节/结构问题，在正文末尾另起一行以 "[polisher-note] " 开头写明，每条一行。没有问题就不加。

保留原文绝大多数句子。只改真正有问题的句子，不要整段重写。修改后章节总长变化不得超过原文字数 ±15%。
```

> 英文项目（`book.json#language == "en"`）改用 `buildEnglishSystemPrompt()` 版本，结构一一对应（"Polisher Scope" / "6 prose-level reader-pain patterns" / "Prose-layer hard rules" / "Output contract"），中文 SKILL v1 默认走中文版，英文版见 inkos 源码 L120-152。

### 2. User message 拼装

```
请润色第<chapterNumber>章。只返回完整的润色后正文，不要 JSON、不要标题、不要解释。

## 章节备忘（润色不得偏离此目标）
goal：<chapterMemo.goal>

<chapterMemo.body>

## 待润色章节
<chapterContent>
```

> 英文版头部改 "Polish chapter <N>. Return the polished chapter in full, nothing else — no JSON, no headers, no commentary." + "## Chapter Memo (do NOT let polish drift from this goal)" + "## Chapter Under Polish"。

调用参数：`temperature = 0.4`（比 Writer 的默认温度更低——润色要稳，不要再发散）。

### 3. 工作步骤

1. **守门检查**：
   - 读 `audit_result.json`，确认 `overall_score >= 88` 且 critical=0、长度 in-soft、sensitive 未 block。
   - `score in [85, 88)` → 跳过 Polisher，章节按 normalized.md 直接落到 `chapters/{NNNN}.md`，记一行 `polish: skipped (borderline)`。
   - 用户显式触发"整章 polish"则忽略借线规则。
2. **构造 prompt**：按 §1 拿 system prompt（按 language 分支），按 §2 拼 user message。`chapterMemo` 缺失则跳过 memo 块——但 SKILL 主循环里 chapter_memo 是必有的，缺失即异常。
3. **调用 LLM**：单次调用，不重试解析（输出格式简单——纯文本）。
4. **去围栏**：如果 LLM 防御性地把全文包在 ` ``` ` 代码块里，剥掉外层围栏（`stripWrappingFence`）。
5. **变更判定**：
   - `polishedContent === chapterContent` → `changed = false`，**早退**：把原文按 audit 通过的版本直接写到 `chapters/{NNNN}.md`，不留 pre-polish 备份。
   - 否则 `changed = true`：把 audit 通过的原文备份到 `story/runtime/chapter-{NNNN}.pre-polish.md`，把 Polisher 输出写到 `chapters/{NNNN}.md`。
6. **后置确定性扫描**：对 polishedContent 跑 `ai_tell_scan.py` 与 `sensitive_scan.py` 一次。
   - 命中 critical / block → **回退**到 pre-polish.md，把 Polisher 这一轮记成失败（`status: "polish-reverted-introduced-issues"`），仍按原文落盘 `chapters/{NNNN}.md`。
   - 未命中 → 保留 Polisher 输出，正常落盘。
7. **记录**：写一行到 `story/runtime/chapter-{NNNN}.polish.json`：
   ```json
   {
     "chapter": 12,
     "changed": true,
     "scoreAtEntry": 91,
     "preLength": 3120,
     "postLength": 3145,
     "polisherNotes": ["第三段铺垫被前一章兑现过，建议下一轮 reviewer 收掉"],
     "status": "applied"
   }
   ```

### 4. polish-don't-rewrite 契约（核心边界）

Polisher 的存在意义是**只磨表面**。系统 prompt 里"润色边界（硬约束）"段已经把这条钉死，但工程层还要再守一道：

- **保留原文绝大多数句子**——心里默算：单句替换的总数应 ≪ 全章句子数；改动幅度 > 30% 视为越界，回退。
- **章节总长变化在 ±15% 以内**（来自 system prompt 末段的硬规约）——脚本 `word_count.py` 复测，超出即回退。
- **不许动**：人名、地名、物品名、hook_id（不应出现在正文里，但偶尔会有残留）、章节标题、对话的"谁说的"归属。
- **遇到结构问题用 `[polisher-note]` 兜底**，不要在正文里替作者补——这是 Polisher 给下一轮 Reviewer 留的逃生口；如果章末出现 `[polisher-note]` 行，主循环要把它收集到 `polisherNotes` 字段，**不写入** `chapters/{NNNN}.md`（落盘前剥掉）。

### 5. 早退条件（changed=false）

LLM 自己就可能判断"原文已足够好"——返回与原文逐字相同（或仅空白差异）的内容。这种情况不写 pre-polish 备份、不更新章节文件，省一次 IO。

## Output contract

- **Polisher 直出**：纯文本——润色后的全章正文，可在末尾追加 0..n 行 `[polisher-note] ...`。**禁止** JSON / Markdown header / 解释段。
- **落盘**：
  - `changed == true` 且后置扫描通过 → `chapters/{NNNN}.md` 写 polishedContent（剥掉 polisher-note 行）；原 audit 通过版本备份到 `story/runtime/chapter-{NNNN}.pre-polish.md`。
  - `changed == false` → `chapters/{NNNN}.md` 直接由 normalized.md 内容写入；不产 pre-polish 备份。
  - 后置扫描失败 → 回退（同 changed==false 的落盘路径），但单独记 `status: "polish-reverted-introduced-issues"`。
- **元数据**：`story/runtime/chapter-{NNNN}.polish.json`（结构见 §Process step 7）。
- **polisher-note 集合**：写到 `polish.json` 的 `polisherNotes` 字段；下一次该书任意章节进入 [phase 02 planner](02-planner.md) 时，应该把上一章的 polisherNotes 作为"未消化反馈"提示给 Planner（让 Planner 在新章里补结构，或开新坑兑现旧承诺）。

## Failure handling

| 失败种类 | 检测方式 | 处理 |
|---|---|---|
| LLM 返回空 / 全是空白 | `polishedContent.length == 0` | 视为 changed=false，按原文落盘 |
| LLM 多吐内容（出现"## 润色说明"等解释段） | 简单正则 / 起首识别 | 单次重试；仍异常则视为 changed=false 落原文 |
| 长度变化超 ±15% | `word_count.py` 复算 | 回退到 pre-polish，状态记 `length-drift` |
| 后置 `ai_tell_scan` critical 命中 | 退出码 1 | 回退；不再二次 polish（避免和 Reviser 抢工作） |
| 后置 `sensitive_scan` block 命中 | 退出码 1 | 回退；产物视作脏数据丢弃 |
| Polisher 在正文里加新情节 / 改人名（人工检测） | 章末的 `[polisher-note]` 与正文 diff 不一致 | 回退；这种情况通常是 prompt 没守住，下次升级 prompt |

**关键约束**：

- **单 pass，不递归**——Polisher 跑完就跑完，不论结果如何不再重入。需要再改的话由用户显式触发"再 polish 一遍"或下一章的 Reviser 顺手处理。
- **不开 audit-revise 回环**——Polisher 不调用 Auditor 复评。它的前提是 audit 已经通过，再过一次 audit 是浪费 token；只跑 `ai_tell_scan` + `sensitive_scan` 两个确定性闸门即可。
- **失败不阻塞主循环**——回退即可，章节最终态由 audit 通过的版本兜底，整章交付不受影响。

## 注意事项

1. **Polisher ≠ Reviser polish 模式**：两者文本上接近，但调度位置完全不同——Reviser polish 是 audit **失败**后的修复手段（issue 列表驱动）；Polisher 是 audit **通过**后的提升手段（无 issue 输入，自驱）。SKILL 内部不要把它们的 prompt 互相借用。
2. **绝不增删情节**：Polisher 的模型温度低、上下文短，容易"自作聪明"补一句"风吹得他打了个寒战"——这种**新增的环境动作**已经越界（虽然看起来只是文字层）。判断标准：删除某句后语义是否丢失？只磨措辞不会丢，新增动作会。
3. **绝不改人名 / 物品名 / 地名**：哪怕原文里"赵元朗"被写成"赵远郎"看起来像错字，Polisher 也不动——真理文件里的名字才是 ground truth，Polisher 没读真理文件。这种问题留给 Reviser `spot-fix`。
4. **不替角色补对白**：哪怕觉得"这里张三应该回一句"，也不许加。补对白属于结构层，是 Reviser 的活。
5. **`[polisher-note]` 是逃生口不是工作量**：能 1 行说清的结构问题就 1 行说清；不要利用这个机制写长篇分析。Note 进 `polish.json`，不出现在 `chapters/{NNNN}.md` 里。
6. **保留作者风格**：如果用户启用了风格模仿（`style_profile.json` 存在），Polisher 的"句式多样化 / 段长 3-5 行"等硬规约和风格指纹**冲突时优先指纹**——除非指纹本身是 AI 味重灾区。这一项目前没法在 prompt 层精确表达，依赖人工 review。
7. **token 节省**：单 pass 调用，response 长度约等于原章节长度——上下文成本和 Writer 一阶段差不多。如果 token 紧张，按 §何时进入 的借线规则把 score < 88 的章节都跳掉，能省 30-50% 的 polish 调用。
8. **审计纸面分提升幅度有限**：Polisher 不会把 88 分的稿子磨到 95 分——它磨的是"读起来顺不顺"，对 Auditor 的 37 维评分提升通常 0-3 分；不要拿"polish 后再 audit 一次看分数"作为它的成功标准。它的价值是**读者主观体感**，不是数字。
