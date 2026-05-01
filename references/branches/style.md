# 风格分支（style branch）

本分支是 inkos `style analyze` / `style import` 在 SKILL 形态下的还原。当用户要求"模仿 XX 风格写"或显式跑了 `style_analyze.py` 之后，主流程在 05-writer 阶段额外注入风格指纹（统计指标）和风格指南（定性 LLM 分析）。

两件产物是独立的：

- `story/style_profile.json`——纯文本统计指纹（5 类指标），由 `scripts/style_analyze.py` 产出，**不调用 LLM**
- `story/style_guide.md`——定性叙事声音分析，由 Claude 跑本文件第 4 节的 LLM prompt 产出，可选

`--stats-only` 模式下只产 JSON，不跑 LLM。

---

## 1. 总流程（SKILL 调度顺序）

```
用户提供参考文本 ref.txt
        │
        ├─ scripts/style_analyze.py ref.txt --book <bookId>
        │       └─ 产出 story/style_profile.json（确定性）
        │
        └─ （可选）Claude 用本文件 §4 LLM prompt 跑定性分析
                └─ 产出 story/style_guide.md
        ↓
05-writer 阶段：
  - 始终注入 buildStyleFingerprint(profile_json) → "## 文风指纹（模仿目标）"块
  - 若 style_guide.md 存在且非占位符，注入 buildStyleGuide(text) → "## 文风指南"块
```

参考文本最低长度（来自 runner.ts L2004）：500 字符；推荐 ≥ 2000 字符以保证统计稳定。

---

## 2. 纯文本统计分析（5 项指标）

来源：`agents/style-analyzer.ts` L22-93。`scripts/style_analyze.py` 必须复刻以下算法。

### 2.1 句子切分

```regex
[。！？\n]
```

切分后 trim、过滤空串，得到 `sentences[]`。

### 2.2 段落切分

```regex
\n\s*\n
```

切分后 trim、过滤空串，得到 `paragraphs[]`。

### 2.3 5 项指标

| 指标 | 算法 |
|------|------|
| `avgSentenceLength` | `sentences` 长度均值，保留 1 位小数 |
| `sentenceLengthStdDev` | `sentences` 长度总体标准差（除以 N，不是 N-1），保留 1 位小数 |
| `avgParagraphLength` | `paragraphs` 长度均值，四舍五入到整数 |
| `paragraphLengthRange` | `{ min, max }` 段落长度，整数 |
| `vocabularyDiversity` (TTR) | 字符级 Type-Token Ratio：先把 `text` 做 `replace(/[\s\n\r，。！？、：；""''（）【】《》\d]/g, "")` 过滤标点空白数字，再计算 `unique chars / total chars`，保留 3 位小数 |
| `topPatterns` | 取每句首 2 字符作 key 计数 → 按计数降序取前 5 → **过滤计数 ≥ 3 的项**，格式 `<两字>...(<count>次)` |
| `rhetoricalFeatures` | 见下表，命中数 ≥ 2 的才进数组，格式 `<名称>(<n>处)` |

### 2.4 修辞特征正则表（`RHETORICAL_PATTERNS`，逐字搬）

```javascript
{ name: "比喻(像/如/仿佛)", regex: /[像如仿佛似](?:是|同|一般|一样)/g },
{ name: "排比",             regex: /[，。；]([^，。；]{2,6})[，。；]\1/g },
{ name: "反问",             regex: /难道|怎么可能|岂不是|何尝不/g },
{ name: "夸张",             regex: /天崩地裂|惊天动地|翻天覆地|震耳欲聋/g },
{ name: "拟人",             regex: /[风雨雪月花树草石](?:在|像|仿佛).*?(?:笑|哭|叹|呻|吟|怒|舞)/g },
{ name: "短句节奏",         regex: /[。！？][^。！？]{1,8}[。！？]/g },
```

### 2.5 输出 JSON 形状

参见 `references/schemas/style-profile.md`。

---

## 3. SKILL 调用入口

```
python scripts/style_analyze.py <ref.txt> --book <bookId> [--stats-only]
```

- 不传 `--stats-only`：脚本只跑统计写 JSON；定性分析由 SKILL 在对话里跑。
- 传 `--stats-only`：等价上面，但显式声明跳过 LLM 阶段。
- 失败：参考文本 < 500 字符、文件不存在 → 非零退出 + stderr 报错。

定性分析（生成 `style_guide.md`）由 Claude 在主流程里调，**不写在脚本里**。

---

## 4. 定性 LLM 分析 system prompt（逐字搬运）

来源：`pipeline/runner.ts` L2003-2063 中 `generateStyleGuide` 内联的 system prompt。

```
你是一位文学风格分析专家。分析参考文本的写作风格，提取可供模仿的定性特征。

输出格式（Markdown）：
## 叙事声音与语气
（冷峻/热烈/讽刺/温情/...，附1-2个原文例句）

## 对话风格
（角色说话的共性特征：句子长短、口头禅倾向、方言痕迹、对话节奏）

## 场景描写特征
（五感偏好、意象选择、描写密度、环境与情绪的关联方式）

## 转折与衔接手法
（场景如何切换、时间跳跃的处理方式、段落间的过渡特征）

## 节奏特征
（长短句分布、段落长度偏好、高潮/舒缓的交替方式）

## 词汇偏好
（高频特色用词、比喻/修辞倾向、口语化程度）

## 情绪表达方式
（直白抒情 vs 动作外化、内心独白的频率和风格）

## 独特习惯
（任何值得模仿的个人写作习惯）

分析必须基于原文实际特征，不要泛泛而谈。每个部分用1-2个原文例句佐证。
```

User message（同样逐字）：

```
分析以下参考文本的写作风格：

${referenceText.slice(0, 20000)}
```

调用参数：`temperature: 0.3`。

**输入截断**：参考文本超过 20000 字符时只取前 20000（runner.ts 中的 `slice(0, 20000)`）。

**输出后处理**：原版会在 LLM 回复尾部追加 `craftMethodology`（来自 `buildWritingMethodologySection(lang)`）；SKILL 移植时不强制——可以让 Claude 直接把 LLM 回复落到 `style_guide.md`，方法论段落由 05-writer 自己的"创作宪法"段提供。

---

## 5. Writer 注入方式（逐字搬运 wrapper）

来源：`agents/writer-prompts.ts` L681-697。

### 5.1 风格指南（定性）注入

`buildStyleGuide(styleGuide)`：

```
## 文风指南

<style_guide.md 全文>
```

跳过条件：`styleGuide` 为空字符串或字面量 `(文件尚未创建)`。

### 5.2 风格指纹（统计）注入

`buildStyleFingerprint(fingerprint)`：

```
## 文风指纹（模仿目标）

以下是从参考文本中提取的写作风格特征。你的输出必须尽量贴合这些特征：

<fingerprint 文本>
```

`fingerprint` 是把 `style_profile.json` 渲染成人读字符串的结果（建议格式：每行一项，例：`平均句长：18.4 字`、`句长标准差：6.2`、`段落长度范围：12-180 字`、`高频句首：他便..., 一道..., 那是...`、`修辞特征：比喻(34处), 排比(12处)`）。SKILL 实施时由脚本或 Claude 现场把 JSON 转成可读字符串。

跳过条件：`fingerprint` 为空字符串。

### 5.3 注入位置

两块都在 Writer system prompt 的"## 本书专属规则"之后、"## 动笔前必须自问"之前。如果同时存在，先注入风格指南（定性），再注入风格指纹（指标）。

---

## 6. 输出契约

| 文件 | 何时产生 | 产生方式 |
|------|----------|----------|
| `story/style_profile.json` | 用户提供参考文本，跑 style_analyze.py 时**总是**生成 | 脚本（确定性） |
| `story/style_guide.md` | 同上场景，**未传 `--stats-only` 时** | Claude 跑 §4 LLM prompt |

落盘后两个文件由 05-writer 在拼装 system prompt 时按 §5 引入。

字数检查：

- 参考文本 < 500 字符 → 抛错（runner.ts L2004 阈值）
- 推荐 ≥ 2000 字符以让 §2 的 5 项指标稳定
- LLM 输入截断到 20000 字符

---

## 7. 与 fanfic 分支的协作

同人项目（`book.json#fanficMode` 非空）也可以加载风格分支。两者注入的是不同 prompt 段、不冲突：

- fanfic 分支注入"## 同人正典参照"+"## 角色语音参照"（Writer system prompt）
- style 分支注入"## 文风指南"+"## 文风指纹（模仿目标）"

如果原作素材就是参考文本，建议把它跑两次：一次走 `fanfic-canon-importer` 抽 5 段正典（见 `references/branches/fanfic.md` §5），一次走 `style_analyze.py` + §4 LLM prompt 抽风格特征。两组产物各自落盘到 `fanfic_canon.md` / `style_profile.json` + `style_guide.md`。
