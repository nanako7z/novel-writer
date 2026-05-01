# 敏感词词表（中文网文三级分类）

> 移植自 inkos `packages/core/src/agents/sensitive-words.ts`。本文件是确定性词表 + 处置策略的真理来源；脚本 `scripts/sensitive_scan.py` 必须照此实现，Auditor / Writer 必须按此处置。

---

## 分级总览

| 等级 | 类别 | severity | Auditor 严重度 | 处置 |
|---|---|---|---|---|
| L1 | 政治类 | `block` | `critical` | 必须删除或替换才能发布；`sensitive_scan.py` 命中即非零退出码 |
| L2 | 性内容类 | `warn` | `warning` | 平台审核风险；建议替换或弱化 |
| L3 | 极端暴力类 | `warn` | `warning` | 平台审核风险；建议替换或弱化 |

自定义词表（`book.json#sensitiveWords` 或 `--custom-words`）默认 `warn`，并入 L2/L3 同等处置。

---

## L1 政治类（severity: block）—— 全表

```
习近平、习主席、习总书记、共产党、中国共产党、共青团、
六四、天安门事件、天安门广场事件、法轮功、法轮大法、
台独、藏独、疆独、港独、
新疆集中营、再教育营、
维吾尔、达赖喇嘛、达赖、
刘晓波、艾未未、赵紫阳、
文化大革命、文革、大跃进、
反右运动、镇压、六四屠杀、
中南海、政治局常委、
翻墙、防火长城
```

命中处置（Auditor `critical`）：

> 检测到政治敏感词：「<word>」×<count>。必须删除或替换政治敏感词，否则无法发布。

Writer 任何时候都不应主动写入这些词；如剧情需要历史 / 政治背景，用泛化表达（"那场运动""上面的人""高墙之外"）替代具名。

---

## L2 性内容类（severity: warn）—— 全表

```
性交、做爱、口交、肛交、自慰、手淫、
阴茎、阴道、阴蒂、乳房、乳头、
射精、高潮、潮吹、
淫荡、淫乱、荡妇、婊子、
强奸、轮奸
```

命中处置（Auditor `warning`）：

> 检测到色情敏感词：「<word>」×<count>。建议替换或弱化色情敏感词，避免平台审核问题。

Writer 处理性场景时优先用"以景代情""以动作代直白"——拥抱、呼吸、衣物褶皱、汗水反光等具象细节替代器官名词与生理动作直陈。涉及性暴力主题，用"侵犯""伤害"等泛化动词替代。

---

## L3 极端暴力类（severity: warn）—— 全表

```
肢解、碎尸、挖眼、剥皮、开膛破肚、
虐杀、凌迟、活剥、活埋、烹煮活人
```

命中处置（Auditor `warning`）：

> 检测到极端暴力词：「<word>」×<count>。建议替换或弱化极端暴力词，避免平台审核问题。

Writer 处理高烈度暴力时，优先视角侧切——写旁观者的反应、声音、气味、事后痕迹，而不是过程的肢体细节；保留戏剧张力，去除可操作的肉体破坏描写。

---

## 处置策略（按 severity）

- **block（critical）**——硬门：
  - `sensitive_scan.py` 在政治词命中时返回非零退出码，Audit 流程立即标记为 `critical`，Reviser 必须以 `anti-detect` 或 `spot-fix` 模式修正后才允许进入下一阶段。
  - 不论上下文（包括引用、批判、转述），一律删除或替换。
  - SKILL 层不再做"是否合理"的语义判断——只要命中就拦。

- **warn（warning）**——软门：
  - `sensitive_scan.py` 命中后以零退出码返回，但 issue 列表里给出每个命中词与计数。
  - Auditor 在最终报告中累加为 `warning`；如果 `warning` 总数超出本书阈值（默认 3 条），自动触发 `polish` 或 `anti-detect`。
  - 用户可通过 `book.json#sensitiveOverride` 单独豁免某词（例如医学题材豁免器官名词），脚本读取后跳过。

- **自定义词（warn）**：
  - `book.json#sensitiveWords` 是用户为本书追加的"私有禁用词"（设定冲突、人物原名、未公开剧透等）。
  - 处置等同 L2/L3 warn，但 Auditor 提示文案换成"项目自定义敏感词"。

---

## `scripts/sensitive_scan.py` 接口契约

脚本职责（与 inkos `analyzeSensitiveWords` 行为一一对应）：

1. 读取章节正文（参数：`--text <path>` 或 stdin），可选读取 `book.json` 取出 `sensitiveWords` 自定义词表。
2. 对三类内置词表 + 自定义词表分别扫描，使用 `re.escape()` 等价的转义后逐词 `findall`。
3. 输出 JSON 到 stdout，结构：
   ```json
   {
     "found": [
       {"word": "...", "count": 3, "severity": "block"},
       {"word": "...", "count": 1, "severity": "warn"}
     ],
     "issues": [
       {
         "severity": "critical",
         "category": "敏感词",
         "description": "检测到政治敏感词：\"...\"×3",
         "suggestion": "必须删除或替换政治敏感词，否则无法发布"
       }
     ]
   }
   ```
4. 退出码：
   - `0`：无命中或仅有 warn 级
   - `1`：至少一条 `block` 级命中（强制阻断后续阶段）
   - `2`：脚本自身错误（参数缺失、文件读取失败等），错误信息打到 stderr。
5. 不修改正文，不做语义判断，不调用 LLM。修复由 Reviser 阶段（Claude）完成。

---

## 与 Auditor / Reviser 的衔接

- **Auditor**（09-auditor.md）在评分前先调用 `sensitive_scan.py`：发现 `block` 命中 → 总分上限 60、issues 中追加该 critical；发现 `warn` 命中 ≥ 阈值 → 总分上限 75。
- **Reviser**（10-reviser.md）选择修改模式时：仅有敏感词问题 → `anti-detect` 模式；混合问题 → `auto`，但敏感词永远在第一遍处理。
- **Writer**（05-writer.md）的"硬性禁令"段落已经包含"政治敏感词不得出现在正文"的提醒，但 Writer 不能依赖自身记忆——`sensitive_scan.py` 是最后一道确定性闸门。
