# Writing Methodology（写作方法论参考全本）

> 移植自 inkos `utils/writing-methodology.ts`。提供一份**全书通用、与题材无关**的写作方法论参考长文，由 `scripts/writing_methodology.py` 一次性发出。它**不是 Writer 系统 prompt 的实时拼装段**——Writer 系统 prompt 里已经有"写作铁律 / 创作宪法 / 沉浸感六支柱"等精简版（见 [phase 05 §4-§6](phases/05-writer.md)）。本文是那些精简版的**长版参考材料**，用法在 §3。

## 内容（六章）

| section ID | 中文标题 | 英文标题 |
|---|---|---|
| `sense` | 一、去 AI 味：正反例对照 | 1. Anti-AI Pattern Guide |
| `psych` | 二、六步走人物心理分析 | 2. Six-Step Character Psychology |
| `support` | 三、配角设计方法论 | 3. Supporting Character Design |
| `pillars` | 四、代入感六大支柱 | 4. Immersion Pillars |
| `escalate` | 五、强情绪升级法（避免流水账） | 5. Emotional Escalation |
| `checklist` | 六、写前自检清单 | 6. Pre-Write Checklist |

正文 verbatim 搬自 inkos 源码，不要改写。改写等于改风格基线。

## 用法

```bash
python {SKILL_ROOT}/scripts/writing_methodology.py \
  [--lang zh|en] \
  [--sections all|sense,psych,support,pillars,escalate,checklist] \
  [--json|--markdown] \
  [--out <path>]
```

- 默认：`--lang zh --sections all --markdown`，输出到 stdout
- `--sections` 接受 ID 或常用别名（`anti-ai`/`psychology`/`immersion`/`scene`/`pace`/`info` 等）
- `--json` 输出 `{language, sections: [{id, name, content}]}`

## 何时注入

inkos 的策略是 **"initBook / generateStyleGuide 时一次性注入到 `style_guide.md`"**——也就是说：

- 这是**每本书共用**的方法论（与 `references/genre-profile.md` 的题材规则正交）
- 一旦写入 `books/<id>/story/style_guide.md`，Writer 在每章开写时通过 §9.B 文风指南段读到它
- 因此**不需要**每章动态重新拼装；写一次就行

实务上 Composer 不会在每章跑 `writing_methodology.py`。两个真正会用的场景：

1. **`init_book.py` 之后的初始化**：把方法论灌进 `style_guide.md` 顶部（在 init 脚本里没有这一步时手动调，作者级别决策——是否要这套方法论上墙）
2. **风格模仿分支**：Style 分支生成 `style_guide.md` 时，如果作者选了"我要去 AI 味的现代华语风格"这类预设，把对应 sections 拼到 style_guide 顶。否则作者要的就是某种特定流派指纹，不要硬塞通用方法论破坏指纹

## 与 Writer / style_guide 的关系

- **Writer 系统 prompt 的 §4 写作铁律**：14 条精简、写作时实时调用的判据
- **Writer 系统 prompt 的 §5 创作宪法**：14 条散文版原则，"在两个都说得通的下一句之间做选择"
- **Writer 系统 prompt 的 §6 沉浸感六支柱**：场景前几页静默立柱
- **本方法论**：**长版参考**，含正反例与表格——给作者读、给 Auditor 评分时回查、给 Reviser 在 polish/anti-detect 模式下兜底

三者**不冲突**：精简版是"动笔时的工作记忆"，方法论长版是"参考手册"。Writer 不会同时读两份，避免 prompt 膨胀。

## 与 genre-profile 的关系

| 维度 | 来源 | 范围 |
|---|---|---|
| 通用写作工艺（去 AI、心理、配角设计、沉浸、升级、自检）| **本文 / writing-methodology** | 全书共用，与题材无关 |
| 题材专属（疲劳词、节奏规则、章节类型、爽点类型）| `templates/genres/<genre>.md` + `references/genre-profile.md` | 仅当前题材 |
| 本书铁律（主角设定锁、本书禁忌）| `book_rules.md` | 仅当前书 |

层级见 [rule-stack.md](rule-stack.md)：本方法论挂在 L2（全书）层；与 L1 题材相加，不会替代。

## 不要把它当 audit 检查点

Auditor (phase 09) 评分时**不要**逐条对照本文。本文是建议而非硬约束；硬约束在 §12 去 AI 味铁律 + Auditor 37 维。Reviser 在 anti-detect / polish 模式下可以引用本文里的"反例→正例对照"作为重写灵感，但不要把"是否照本文办"当扣分依据。
