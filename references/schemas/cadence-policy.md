# Cadence Policy Schema（结构化节奏政策）

> 本 schema 把题材 profile 里那条散文式 `pacingRule:` 升级为**可被 `cadence_check.py` 直接读取的结构化节拍配置**。它仍然兼容旧 profile（缺失时由 `pacingRule` 启发式推断），但显式写出来后 Planner 拿到的 `recommendedNext` 与 `fatigueAlerts` 会更稳定。
>
> 单一字段：放在题材 frontmatter 的 `cadence:` 子对象内（与 `pacingRule` 同级；`pacingRule` 仍保留给人类阅读）。

## 1. 字段总览

```yaml
cadence:
  satisfactionWindow: 5            # 任意两次满足之间允许的最大间隔章数；超出 → satisfactionEmergency
  satisfactionSequence:             # 滚动窗口内偏好的满足类型分布（按权重）
    - {type: "爽点",  weight: 3}
    - {type: "揭密",  weight: 2}
    - {type: "转折",  weight: 2}
    - {type: "情感",  weight: 1}
  volumeBeatDistribution:           # 卷内位置 → 偏好章节类型 + 满足节拍
    early:    {chapterTypes: ["建立", "铺垫"], satisfactionPerN: 8}
    middle:   {chapterTypes: ["冲突", "推进"], satisfactionPerN: 5}
    late:     {chapterTypes: ["高潮", "收束"], satisfactionPerN: 3}
  fatigueGuards:                    # 触发器列表；命中后 cadence_check.fatigueAlerts 输出
    - {pattern: "连续 3 章同 satisfactionType",  action: "force-switch-type"}
    - {pattern: "连续 5 章无爽点",                action: "satisfactionEmergency"}
```

### 1.1 字段语义

| 字段 | 类型 | 说明 |
|---|---|---|
| `satisfactionWindow` | int (1-20) | 任意两个 satisfaction 事件之间的最大允许间隔。`gap >= window` → `satisfactionPressure="high"`；`gap >= ceil(window*0.6)` → `medium`；否则 `low` |
| `satisfactionSequence[]` | list | 偏好的满足类型分布（用于 Planner 选择下一个 satisfactionType）。`type` 应是题材 `satisfactionTypes` 的子集；`weight` 越高越优先 |
| `volumeBeatDistribution.early` | object | 进度 < 0.33 时的偏好章节类型 + 每 N 章一个满足节拍 |
| `volumeBeatDistribution.middle` | object | 进度 0.33-0.66 |
| `volumeBeatDistribution.late` | object | 进度 > 0.66（含 climax / 收束） |
| `*.chapterTypes` | list[str] | 该 band 推荐的章节类型；应是题材 `chapterTypes` 的子集 |
| `*.satisfactionPerN` | int | 该 band 内每 N 章应该发生一次满足事件（早期可疏，后期密） |
| `fatigueGuards[]` | list | 命中模式时输出 `fatigueAlerts`；`pattern` 是诊断字段（人类可读），`action` 是脚本动作枚举 |

### 1.2 `fatigueGuards.action` 枚举

- `force-switch-type` — 下一章必须切换 chapterType / satisfactionType
- `satisfactionEmergency` — 下一章必须直接对应一个 satisfactionType（即 high pressure）
- `lower-tension` — 连续高紧张后强制安排一个降压节拍（过渡 / 日常）
- `vary-title-token` — 标题去重（与 `analyzeTitlePressure` 联动；现 cadence_check 不消费但保留扩展位）

## 2. 缺省 / 推断路径

如果题材 frontmatter 没有 `cadence:` 子对象，`cadence_check.py` 会按以下顺序推断默认值：

1. **`satisfactionWindow`** —— 从 `references/cadence-policy.md` 的"题材默认 cadence"表查 `genre id` 对应的"高压阈值"列；找不到 → 5
2. **`satisfactionSequence`** —— 取 profile 的 `satisfactionTypes` 列表，每条权重为 1（均匀分布）
3. **`volumeBeatDistribution`** —— 内置兜底：
   ```yaml
   early:  {chapterTypes: <profile.chapterTypes[:2]>,  satisfactionPerN: window*2}
   middle: {chapterTypes: <profile.chapterTypes[1:3]>, satisfactionPerN: window}
   late:   {chapterTypes: <profile.chapterTypes[-2:]>, satisfactionPerN: max(2, window-2)}
   ```
4. **`fatigueGuards`** —— 内置兜底两条：
   - `连续 3 章同 satisfactionType → force-switch-type`
   - `连续 N 章无爽点 → satisfactionEmergency`（N = `satisfactionWindow`）

## 3. cadence_check.py 输出新增字段

在原有 `satisfactionPressure / chaptersSinceSatisfaction / volumeBeatStatus / recommendedChapterTypes / pacingNotes` 之上：

```json
{
  "recommendedNext": {
    "chapterType": "战斗章",
    "satisfactionType": "斗法碾压",
    "reasoning": "middle band, 距离上次满足 4 章 (window=5) → 高压；按 satisfactionSequence 权重 + 最近未用 satisfactionTypes 优先"
  },
  "fatigueAlerts": [
    {"pattern": "连续 3 章 chapterType='战斗章'", "action": "force-switch-type"},
    {"pattern": "连续 5 章无爽点", "action": "satisfactionEmergency"}
  ],
  "cadencePolicy": {
    "satisfactionWindow": 5,
    "source": "embedded" // or "inferred"
  }
}
```

`recommendedNext.reasoning` 是给 Planner 看的——一句话说明为什么挑了这个组合。

## 4. 何时该显式写 `cadence:`

- 想精调高压阈值（默认按 `pacingRule` 抽出的全局表，可能跟你实际节奏不符）
- 题材有强卷段差异（如 cozy 早期慢、后期收束密；不写就用均匀兜底）
- 想引入"连续 X 章无 satisfactionType=具体值"这类细粒度疲劳触发器
- 自定义题材（非 builtin 15 个之一）—— builtin 表查不到默认值

不写也能用——`pacingRule` 启发式 + 内置兜底足以覆盖 80% 场景。

## 5. 与 `references/cadence-policy.md` 的关系

- `references/cadence-policy.md` —— **政策层**：4 层节拍模型（chapter/volume/book/arc）、人类可读的题材默认 cadence 表、Planner 怎么消费
- 本文（`schemas/cadence-policy.md`）—— **schema 层**：YAML 字段格式、推断规则、cadence_check 输出契约

两者交叉引用：`cadence-policy.md` 的"扩展点"指向本 schema；本 schema 的 §2 推断默认值指回 `cadence-policy.md` 的题材默认表。
