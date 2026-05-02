# Foundation Reviewer（Architect 产物质量闸门）

Architect 出完 5 个 SECTION 之后立即跑的一个 LLM 审稿角色——不是脚本、不是确定性 lint。Claude **从架构师的视角切换到资深编辑视角**，对自己刚生成的"基础设定"做一次体检：撑不撑得起这本书？哪一段方向跑偏？需不需要让 Architect 重做一轮？

> 这是 LLM 角色，不是 deterministic check。Claude 在心里扮演 reviewer，跟刚才扮演 Architect 是不同视角，要带"挑刺编辑"的眼睛去看，**不要因为是自己写的就护短**。

## 何时调用

Foundation Reviewer **不是 top-level phase**——它是 [phase 04 Architect](phases/04-architect.md) 的内部子步骤，每次 Architect 出 5 SECTION 之后**自动**触发，**先于落盘**。流程严格如下：

1. Architect 生成 5 个 SECTION（story_frame / volume_map / roles / book_rules / pending_hooks）。
2. **不要**先把 SECTION 切分落盘——先在内存里跑一轮 Foundation Review。
3. Reviewer 给出 `verdict` ∈ {`pass`, `revise`, `reject`}。
4. 决策树（详见下文「决策树」一节）：通过则落盘；revise 则带反馈重跑 Architect；reject 则中止给用户。
5. Architect ↔ Reviewer 整体最多 2 轮（即第 1 轮 Architect + 第 1 轮 Reviewer + 第 2 轮 Architect + 第 2 轮 Reviewer）。第 2 轮还不过则 best-effort 落盘并打 `architectStatus: "review-failed"`。

正常的 ch 2、ch 3、…、ch N 既不跑 Architect 也不跑 Reviewer——下游 Planner 直接读 outline。

## Inputs

- **ArchitectOutput**：刚生成的完整 5 SECTION markdown（没切分前的整体），按 `=== SECTION: <name> ===` 划块
- **mode**：`original | fanfic | series`（决定用哪套维度）
- **language**：`zh | en`
- **genre profile**：`templates/genres/<book.genre>.md`（题材底色，用于判断节奏维度是否合理）
- **book.json**：title / genre / targetChapters / chapterWordCount
- **可选**：`fanfic_canon.md`（fanfic / series 模式必传，前 8000 字）；`style_guide.md`（如有，前 2000 字）

## 系统 prompt（搬自 inkos `foundation-reviewer.ts` L100-128，请 Claude 在心中扮演这个角色）

> 下面这一块是 inkos 源码里的中文版 `buildChineseReviewPrompt` 原文，**整段照用**，不要改写措辞或评分线。

```
你是一位资深小说编辑，正在审核一本新书的基础设定（世界观 + 大纲 + 规则）。

你需要从以下维度逐项打分（0-100），并给出具体意见：

${dimensions.map((dim, i) => `${i + 1}. ${dim}`).join("\n")}

## 评分标准
- 80+ 通过，可以开始写作
- 60-79 有明显问题，需要修改
- <60 方向性错误，需要重新设计

## 输出格式（严格遵守）
=== DIMENSION: 1 ===
分数：{0-100}
意见：{具体反馈}

=== DIMENSION: 2 ===
分数：{0-100}
意见：{具体反馈}

...（每个维度一个 block）

=== OVERALL ===
总分：{加权平均}
通过：{是/否}
总评：{1-2段总结，指出最大的问题和最值得保留的优点}
${canonBlock}${styleBlock}

审核时要严格。不要因为"还行"就给高分。80分意味着"可以直接开写，不需要改"。
```

英文版本走 inkos `buildEnglishReviewPrompt` 等价 prompt，维度名换英文，"分数 / 意见 / 总评" 换 "Score / Feedback / Summary"，threshold 行写 `80+ Pass — ready to write` / `60-79 Needs revision` / `<60 Fundamental direction problem`，结尾换 `Be strict. 80 means "ready to write without changes."`。

## 维度集（按 mode 切换）

**original 模式（5 维）**：

1. 核心冲突——是否有清晰且有足够张力的核心冲突支撑 40 章？
2. 开篇节奏——前 5 章能否形成翻页驱动力？
3. 世界一致性——世界观是否内洽且具体？
4. 角色区分度——主要角色的声音和动机是否各不相同？
5. 节奏可行性——卷纲是否有足够变化（不会连续 10 章同一种节拍）？

**fanfic / series 模式（5 维）**：

1. 原作 DNA 保留——是否尊重原作的世界规则、角色性格、已确立事实？
2. 新叙事空间——是否有明确的分岔点或新领域，让故事有原创空间，而非复述原作？
3. 核心冲突——新故事的核心冲突是否有足够张力且区别于原作？
4. 开篇节奏——前 5 章能否形成翻页驱动力，不需要 3 章铺垫？
5. 节奏可行性——卷纲是否避免了重走原作剧情节拍的陷阱？

## 评审 rubric（5 SECTION ↔ 维度映射 + severity 定义）

下表说明每个 SECTION 应该被哪一维度盯死，以及 issue severity 的判定标准。

| SECTION | 主要落点维度 | 体检要点 | 该项失分典型证据 |
|---|---|---|---|
| story_frame | 核心冲突 / 世界一致性 | 主题与基调是否具体；主要冲突是否有"两个相信不同事实的人"对撞结构；世界铁律 3-5 条且 prose；终局镜头是否具体 | 主题写成"主角变强"；冲突空喊"正邪对抗"；铁律退化成 bullet 或不存在；终局只写"打败大反派" |
| volume_map | 节奏可行性 / 开篇节奏 | 5+1 段散文；卷间钩子有回收承诺；卷尾改变是不可逆的；节奏原则 6 条且 ≥3 条具体化 | 节奏原则全是"张弛有度"废话；只有 3 段；指定到具体章号（"第 17 章"）；卷尾改变可逆 |
| roles | 角色区分度 | 主角卡 8 个 ## 子标题齐；至少 3 主（主角 + 主对手 + 主协作者）；反差细节存在；motivation 与 story_frame 段 2 的对手定性对得上 | 缺主角弧线段；多个角色声音同质；motivation 与冲突结构不一致；只有 1 个主要角色 |
| book_rules | 世界一致性 | 仅 YAML，零散文；prohibitions 3-5 条且具体；numericalSystemOverrides 与题材 profile 一致 | 写成散文；prohibitions 写成"不能 OOC" 这种空话；数值系统题材没填 hardCap |
| pending_hooks | 节奏可行性 | 表格 12 列齐；core_hook=true 在 3-7 条之间；依赖链 depends_on 不成环；回收节奏覆盖立即 / 近期 / 中程 / 慢烧 / 终局；主线承重伏笔与 volume_map 卷间钩子对得上 | core_hook 灌水到 10+；depends_on 自引用或成环；全部 hook 都是"立即"节奏；与 volume_map 钩子矛盾 |

**severity 定义**：

| severity | 含义 | 触发判定 |
|---|---|---|
| `critical` | 方向性错误，不重做就开不了书 | 任一维度分数 < 50；或 SECTION 缺失 / 严重残缺 |
| `major` | 明显短板，影响下游 Planner / Writer，但方向没崩 | 维度分数 50-69；或多条体检要点不达标 |
| `minor` | 单点瑕疵，可作为 Architect 第二轮的提示，不必 block | 维度分数 70-79；或单条预算超 / 单条格式瑕疵 |

## 输出 schema（解析后）

```json
{
  "verdict": "pass" | "revise" | "reject",
  "score": 0-100,
  "issues": [
    {
      "section": "story_frame" | "volume_map" | "roles" | "book_rules" | "pending_hooks",
      "severity": "critical" | "major" | "minor",
      "description": "具体问题（指向哪一段哪一句）",
      "suggestion": "怎么改（建议而非命令）"
    }
  ],
  "overallFeedback": "原始 === OVERALL === 块的总评字符串"
}
```

verdict 判定：

- `pass`：`score >= 80` **且** 任一维度分数都 ≥ 60。issues 可为空也可只含 minor。
- `revise`：`50 <= score < 80`，或某一维度 < 60 但总分 ≥ 50。值得让 Architect 带反馈重做。
- `reject`：`score < 50`，或多个维度同时 < 50。设定方向性崩塌，**不自动重试**，抛回用户决策。

`score` 取 5 个维度的平均分（四舍五入整数），与 inkos `parseReviewResult` 算法等价。

## 决策树

Reviewer 出完 verdict 后，Architect 编排器按下表行动：

| verdict | 动作 |
|---|---|
| `pass` | Architect 输出**立刻切分落盘**到 `story/outline/`、`story/roles/`、`story/pending_hooks.md`；流程进入下一步（Planner / 主循环）。 |
| `revise` | **不落盘**。把完整 issues 列表 + overallFeedback 拼成 `reviewFeedbackBlock`，附到 Architect user message 重跑。重跑后再次进 Reviewer。整体最多 2 轮（即一次重做机会）；第 2 轮 Reviewer 仍非 pass → 见下文 Failure handling。 |
| `reject` | **不落盘**，**不自动重试**。把 issues + overallFeedback 直接抛给用户，附一句"基础设定方向上有问题，建议你看一下后再决定是改 brief 还是改题材"。等用户拍板后才能重跑 Architect。 |

整轮预算口径与 [phase 04](phases/04-architect.md) 的"Architect 重试 ≤ 2"对齐：第 1 轮 Architect + 第 2 轮 Architect 共占 2 次预算，每轮各跑一次 Reviewer。

## Failure handling

- **Reviewer 解析失败**（输出里找不到 `=== DIMENSION: i ===` 或 `=== OVERALL ===` 块、SECTION 列表抓不全、Architect 输出本身就缺 SECTION）：Reviewer **不应崩溃**，而是返回 `verdict: "revise"` + 一条 `severity: major, section: "(meta)", description: "Architect 输出结构格式不合规，无法按 SECTION 解析"` 的 structural-format issue，把信号送回 Architect 让它把格式重写正确。这样比直接 reject 更友好——通常是 SECTION 头标错了或漏了一块，重做一次就能修。
- **维度分数缺失**（部分 dimension block 解析不出）：按 inkos 默认填 50 + feedback="(parse failed)"，并在 issues 里加一条 `severity: major, description: "维度 N 解析失败"` 提示这次审稿不够可靠；verdict 按补完后的 score 走判定。
- **第 2 轮仍非 pass**：落盘 best-effort 版本，写 `architectStatus: "review-failed"` 到 `story/runtime/architect-review.json`，把 issues 列表附在文件里。下游 Planner 在 chapter_memo 里可以选择性补救（例如补主线承重 hook、补主角反差细节），但**不要**让它假装基础设定没问题。
- **Reviewer 自身 LLM 抽风**（如返回完全跑题的内容）：与解析失败同样处理——返回 revise + structural-format issue，但**不消耗** Architect 的重做预算（这是 reviewer 崩了不是设定崩了）。

## 注意事项

- **Reviewer 不创作，只判**：它可以在 `suggestion` 字段里写"建议把段 2 的对手动机改成与体制冲突而非个人恩怨"，**但不要**自己改写一段散文塞回去。改写是 Architect 第二轮的活，Reviewer 越权改写会让重做反馈失真。
- **是 LLM 角色不是 lint**：维度打分依赖语义判断，不要试图用脚本替代——脚本只能查字符预算和 SECTION 是否齐全（这部分体检要点可以让脚本辅助，但 verdict 必须 LLM 出）。
- **视角切换是关键**：刚才扮演的是"总架构师"，现在扮演"资深编辑"。同一段文字从两个视角看是不一样的，要刻意压住"自己写的总觉得不错"的偏向。
- **80 分门槛是硬的**：inkos 把 PASS_THRESHOLD 设为 80 + DIMENSION_FLOOR 60，意思是任意一维 < 60 即使总分 90 也算不 pass（降级为 revise）。复刻这个规则，不要软化。
- **temperature 提示**：inkos 在调 chat 时给的是 0.3，Claude 在心里跑这一段时也要带"克制、稳定、严格"的语气，不要发散写。
- **重做反馈要带 issue 列表**：第二轮 Architect prompt 里要把 issues + overallFeedback 完整附进去——盲重试基本等于浪费一次预算。
- **不写真理文件**：本阶段任何输出都还在内存里，**verdict === pass 之后**才允许 Architect 切分 SECTION 落盘到 `story/outline/`、`story/roles/`、`story/pending_hooks.md`。
