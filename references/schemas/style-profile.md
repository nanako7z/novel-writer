# StyleProfile（风格指纹 schema）

`scripts/style_analyze.py` 的输出契约。来源：`models/style-profile.ts`（TS interface）+ `agents/style-analyzer.ts` L82-92（产出形状）。

落盘路径：`books/<bookId>/story/style_profile.json`，配合可选的 `story/style_guide.md`（定性 LLM 分析）由 05-writer 同时注入。

---

## 1. JSON 完整示例

```json
{
  "avgSentenceLength": 18.4,
  "sentenceLengthStdDev": 6.2,
  "avgParagraphLength": 96,
  "paragraphLengthRange": {
    "min": 12,
    "max": 312
  },
  "vocabularyDiversity": 0.412,
  "topPatterns": [
    "他便...(28次)",
    "一道...(19次)",
    "那是...(11次)",
    "只见...(9次)",
    "原来...(5次)"
  ],
  "rhetoricalFeatures": [
    "比喻(像/如/仿佛)(34处)",
    "排比(12处)",
    "短句节奏(82处)"
  ],
  "sourceName": "金庸-笑傲江湖.txt",
  "analyzedAt": "2026-05-01T08:42:13.117Z"
}
```

---

## 2. 字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `avgSentenceLength` | number（保留 1 位小数） | 是 | 句子平均字符长度。句子按 `[。！？\n]` 切分后 trim 过滤空串 |
| `sentenceLengthStdDev` | number（保留 1 位小数） | 是 | 句长总体标准差（除以 N，不是 N-1） |
| `avgParagraphLength` | int | 是 | 段落平均字符长度，四舍五入到整数。段落按 `\n\s*\n` 切分 |
| `paragraphLengthRange` | object | 是 | `{ min: int, max: int }`，文本中最短/最长段落字符数 |
| `paragraphLengthRange.min` | int | 是 | 最短段落字符数（无段落时为 0） |
| `paragraphLengthRange.max` | int | 是 | 最长段落字符数（无段落时为 0） |
| `vocabularyDiversity` | number（保留 3 位小数） | 是 | 字符级 TTR：先用 `replace(/[\s\n\r，。！？、：；""''（）【】《》\d]/g, "")` 过滤标点空白数字，再算 `unique chars / total chars`。范围 0-1，越高越多样 |
| `topPatterns` | array<string> | 是 | 最高频的句首 2 字符模式。取每句首 2 字符计数，按计数降序，取前 5，**只保留计数 ≥ 3** 的项；格式 `<两字>...(<count>次)` |
| `rhetoricalFeatures` | array<string> | 是 | 修辞特征命中（命中数 ≥ 2 才入列）；格式 `<名称>(<n>处)` |
| `sourceName` | string | 否 | 参考文本来源名（文件名 / 自定义标签） |
| `analyzedAt` | string | 否 | ISO8601 时间戳，由分析脚本填写 |

> `readonly` 在 TS 中是结构修饰，序列化为 JSON 后不体现；apply 时不允许部分覆盖（要重新跑一次完整分析）。

---

## 3. 修辞特征定义（生成 `rhetoricalFeatures` 的来源）

来源：`agents/style-analyzer.ts` L9-16。`scripts/style_analyze.py` 必须复刻这 6 条正则，命中数 ≥ 2 才入数组：

| name | regex |
|------|-------|
| `比喻(像/如/仿佛)` | `/[像如仿佛似](?:是\|同\|一般\|一样)/g` |
| `排比` | `/[，。；]([^，。；]{2,6})[，。；]\1/g` |
| `反问` | `/难道\|怎么可能\|岂不是\|何尝不/g` |
| `夸张` | `/天崩地裂\|惊天动地\|翻天覆地\|震耳欲聋/g` |
| `拟人` | `/[风雨雪月花树草石](?:在\|像\|仿佛).*?(?:笑\|哭\|叹\|呻\|吟\|怒\|舞)/g` |
| `短句节奏` | `/[。！？][^。！？]{1,8}[。！？]/g` |

> 这些正则在 TS 端是 `g`（全局）模式；Python 实现用 `re.findall` 即可。

---

## 4. 生成流程（脚本侧）

`scripts/style_analyze.py <ref_file> --book <bookId> [--stats-only]` 的执行步骤：

1. 读 `<ref_file>` 为 UTF-8 文本，校验长度 ≥ 500 字符（< 500 抛错退出）
2. 按 §2 算法计算 7 项统计字段
3. 写 `books/<bookId>/story/style_profile.json`（JSON 缩进 2，UTF-8）
4. 退出码 0 表示成功；同时把 JSON 打印到 stdout 方便 Claude 立即引用
5. 失败时非零退出 + stderr 错误说明（不写 partial JSON）

`--stats-only`：本字段只是声明性的——脚本任何时候都只产 JSON，定性的 `style_guide.md` 由 Claude 在对话中跑（见 `references/branches/style.md` §4）。`--stats-only` 旗标的存在让用户能显式表达"不要再额外跑 LLM 分析"。

---

## 5. 存储位置 / 落盘约定

| 路径 | 内容 |
|------|------|
| `books/<bookId>/story/style_profile.json` | 本 schema 的 JSON 实例 |
| `books/<bookId>/story/style_guide.md` | 可选的定性 LLM 分析结果（见 `references/branches/style.md` §4） |

写入策略：

- 先写 `style_profile.json.tmp` → `os.rename` 原子替换
- 同名文件存在时直接覆盖（风格指纹是无状态的，每次跑都重算）
- 不需要 schema 版本号——结构稳定，字段新增时向下兼容

---

## 6. 与 Writer 注入的对应关系

来源：`agents/writer-prompts.ts` L690-697。Writer system prompt 中风格指纹块由 `buildStyleFingerprint(fingerprint)` 拼装：

```
## 文风指纹（模仿目标）

以下是从参考文本中提取的写作风格特征。你的输出必须尽量贴合这些特征：

<fingerprint 文本>
```

`<fingerprint 文本>` 是把本 JSON 渲染成可读文本的结果。建议格式（每行一项）：

```
平均句长：18.4 字
句长标准差：6.2
平均段落长度：96 字
段落长度范围：12-312 字
词汇多样性（TTR）：0.412
高频句首：他便..., 一道..., 那是..., 只见..., 原来...
修辞特征：比喻(34处), 排比(12处), 短句节奏(82处)
来源：金庸-笑傲江湖.txt
```

转换可由脚本（apply_delta 同伴脚本）或 Claude 现场完成，不强制脚本化。
