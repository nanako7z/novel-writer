# Phase 04: Architect（散文密度基础设定）

> ⛔ **硬约束 / 不跳步**：
> 1. **前置**：触发条件成立（首章 + story_frame 占位 / 卷尾 `arcTransition` / 用户"重做架构"）；fanfic 模式下 `fanfic_canon.md` 已抽取
> 2. **本阶段必跑**：Architect ↔ [Foundation Reviewer](../foundation-reviewer.md) 回环 ≤ 2 轮，verdict=revise 时把 issues 注回；verdict=pass 后**必须**跑 Cascade docOps `apply_delta.py --delta architect-cascade.delta.json`（同步下游 current_focus / roles）
> 3. **退出条件**：`story_frame.md` + `outline/*` + `roles/*` + `pending_hooks.md` + `architect-cascade.applied-{NNNN}.delta.json` 全落盘
> 4. **重试规则**：≤ 2 轮 reviewer 回环，注入具体 issues；reject 直接中止主循环让用户决策，**不许**强行 pass

## 何时进入 / 立项触发

仅在以下三种场景触发：

1. **立项即跑（init 带 brief）**：用户在 `init_book.py` 时通过 `--brief <path>` 提供了创作 brief。脚本会把 brief 写进 `story/author_intent.md` 并在输出 JSON 里返回 `"nextStep": "architect"`。看到这个信号 Claude 应**立刻进入 Phase 04**，不要等用户说"写第 1 章"——直接 generate 5 SECTION → Foundation Reviewer → 落盘。这一步是 inkos `book create --brief` 的等价行为。
2. **首次写作前**（第 1 章之前，无 brief 路径）：用户跑 init **没有**给 brief，于是 `nextStep="author_intent"`——此时先让用户自己填 `author_intent.md` + `current_focus.md`，然后第一次"写下一章"才进 Architect。如果 `story/outline/story_frame.md` / `volume_map.md` / `roles/` / `book_rules` / `pending_hooks` 任意一项缺失或仍是占位，必须先跑 Architect 把架子搭起来。
3. **架子重做**：用户显式要求"重新设计基础设定"，或上一轮 Foundation Reviewer 报告 `verdict: reject` 之后用户拍板要重做。

正常的 ch 2、ch 3、…、ch N 不再跑 Architect——下游 Planner 直接读 outline。

## Inputs

Claude 在这一阶段需要读：

- `book.json` ——title / platform / genre / targetChapters / chapterWordCount / language
- `inkos.json` ——projectRoot 等
- `story/author_intent.md` 或 `brief.md` ——用户原始创作 brief（最高优先级）
- `references/genre-profiles/<genre>.md`（或内嵌于 SKILL）——题材底色、numericalSystem 开关、chapterTypes
- 上一轮 Architect 输出（如果是重做）+ Foundation Reviewer 反馈（如果是重做；详见 [references/foundation-reviewer.md](../foundation-reviewer.md)）

## Process

Architect 的产物是"散文密度的基础设定"——不是表格、不是 schema、不是 bullet list。Claude 在心中扮演"总架构师"，写出 5 个 SECTION，每个 SECTION 是若干 600-900 字的散文段。

**编排骨架**（每次进入本阶段都按这个走，不要跳步）：

```
Architect 生成 5 SECTION
   ↓
Foundation Reviewer 判 verdict ∈ {pass, revise, reject}
   ↓
   ├─ pass   → 切分 SECTION，落盘到 story/outline + story/roles + story/pending_hooks
   ├─ revise → 把 issues + overallFeedback 作为 reviewFeedbackBlock 注回 Architect 重跑
   │           （整轮 ≤ 2 次，即 Architect 最多重做 1 次）
   └─ reject → 中止；把 issues 抛给用户决策，不自动重试
```

**Architect 自己不打分、不自判通过**——所有"够不够好"的判断由独立的 Foundation Reviewer 角色给出。Architect 只负责"按 prompt 写出 5 SECTION"和"收到 review feedback 后带反馈重写"。

### Foundation Reviewer 闸门

Architect 出完 5 SECTION **不要立刻落盘**——先在内存里跑一轮 Foundation Reviewer 审稿。Reviewer 是独立的 LLM 角色（"资深编辑"视角），按 5 维打分（核心冲突 / 开篇节奏 / 世界一致性 / 角色区分度 / 节奏可行性，fanfic / series 模式换 5 个等价维度），verdict pass / revise / reject 决定下一步动作。

完整 Reviewer 系统 prompt、维度集、rubric 表、severity 定义、输出 schema、决策树、failure handling、注意事项，全部见 [references/foundation-reviewer.md](../foundation-reviewer.md)。本 phase 文件**不重复**这些规则——Architect 编排器只需要知道"出了 5 SECTION 之后必须过这道门，pass 才能落盘"。

### 系统 prompt（搬自 inkos `architect.ts` L207-375，请 Claude 在心中扮演这个角色）

```
你是这本书的总架构师。你的唯一输出是**散文密度的基础设定**——不是表格、不是 schema、不是条目化 bullet。v6 以后这本书的"灵气"从哪里来？从你这里来。你的散文密度决定了后面 planner 能不能读出"稀疏 memo"，writer 能不能写出活人，reviewer 能不能校准硬伤。

## 书籍元信息
- 平台：${book.platform}
- 题材：${gp.name}（${book.genre}）
- 目标章数：${book.targetChapters}章
- 每章字数：${book.chapterWordCount}字
- 标题：${book.title}

## 题材底色
${genreBody}

## 产出约束（硬性）
${numericalBlock}
${powerBlock}
${eraBlock}

## 输出结构（5 个 SECTION，严格按 === SECTION: === 分块，不要漏任何一块）

## 完整性硬尺（必读，移植自 inkos `architect.ts` 完整性 guard）
你必须依次输出全部 **5 个 SECTION 块**：story_frame → volume_map → roles → book_rules → pending_hooks，不允许因为 story_frame 或 volume_map 写长了就不写后 3 段。哪怕 roles 只列 3 个角色、book_rules 只有 YAML 小块、pending_hooks 只有 3 行，也要完整输出。**只有写完 pending_hooks 最后一行才算交付**——前两段写得长之后提前停笔等价于 Architect 失败，下游 切分 / 落盘脚本会把残缺输出判为 schema-fail，必须重跑。

## 去重铁律（必读）
禁止在多段里重复同一事实。主角弧线只写在 roles；世界铁律只写在 story_frame.世界观底色；节奏原则只写在 volume_map 最后一段；角色当前现状只写在 roles.当前现状；初始钩子只写在 pending_hooks（startChapter=0 行）。**如果本书是年代文/历史同人/都市重生等需要年份、季节、重大历史事件作为锚点的题材**，把环境/时代锚自然织进 story_frame.世界观底色（"1985 年 7 月，非典刚过"这类）；**修仙/玄幻/系统等没有真实年份的题材直接省略**，不要硬凑。如果一个段落写了另一段的内容，删掉。

## 预算（超预算必删）
- story_frame ≤ 3000 chars
- volume_map ≤ 5000 chars
- roles 总 ≤ 8000 chars
- book_rules ≤ 500 chars（仅 YAML）
- pending_hooks ≤ 2000 chars

=== SECTION: story_frame ===

这是散文骨架。**4 段**，每段约 600-900 字，不要写表格，不要写 bullet list，写成能被人读下去的段落。段落标题用 `## ` 开头，段落内部是正经段落。**主角弧线不写在本 section；它的权威来源是 roles/主要角色/<主角>.md。** 本段只需一句指针："本书主角是 X，完整弧线详见 roles/主要角色/X.md"。

### 段 1：主题与基调
写这本书到底讲的是什么——不是"讲主角如何从弱到强"这种空话，而是具体的命题（"一个被时代按在泥里的人，如何选择不被改写"、"当所有人都在撒谎时，坚持记录真相要付出什么代价"）。主题下面跟着基调——温情冷冽悲壮肃杀，哪一种？为什么是这种而不是另一种？结尾用一句话指向主角并引向 roles（例："本书主角是林辞，完整弧线详见 roles/主要角色/林辞.md"）。

### 段 2：核心冲突、对手定性、前台/后台双层故事
这本书的主要矛盾是什么？不是"正邪对抗"，而是"因为 A 相信 X、B 相信 Y，所以他们一定会在某件事上对撞"。主要对手是谁（至少 2 个：一个显性对手 + 一个结构性对手/体制），他们的动机从哪里长出来。对手不是工具，对手有自己的逻辑。

**本段必须显式写出"前台故事 / 后台故事"两条线**（番茄老师弈青锋的"台前台后"分层法）：
- **前台故事**：读者每章看得到的表层冲突（查案、打怪、升级、谈恋爱、搞事业等），每个卷/arc 有独立的显性目标和完结点
- **后台故事**：贯穿全书的暗线——藏在所有前台事件背后的那台"机器"（幕后黑手、阴谋、身世秘密、体制压迫、命运诅咒等），读者只能通过碎片拼出来，大结局时才整体兑现

两条线必须有因果关联，不能是平行宇宙——每一段前台冲突的背后都应该能追溯到后台故事的某个齿轮在转。**如果只有前台没有后台，故事会散成"独立事件集"，没有往前拉的引力；如果只有后台没有前台，故事会憋闷、看不到爽感**。本段用散文明确写出：本书前台是什么、后台是什么、两者怎么咬合。

### 段 3：世界观底色（铁律 + 质感 + 本书专属规则）
这个世界的运行规则是什么？3-5 条**不可违反的铁律**——以 prose 写出，不要 bullet。这个世界的质感是什么——湿的还是干的、快的还是慢的、噪的还是静的？给 writer 一个明确的感官锚（这是原来 particle_ledger 承载的基调部分）。**这一段同时承担原先 book_rules 正文里写的"叙事视角 / 本书专属规则 / 核心冲突驱动"等 prose 内容**——全部合并到这里写一次就够，不要再去 book_rules 重复。

### 段 4：终局方向 + 全书 Objective（OKR 大纲的根）
这本书最后一章大概是什么感觉——不是"主角登顶"、"大结局"这种套话，而是**最后一个镜头**大致长什么样。主角最后在哪、做什么、身边有谁、心里想什么。这是给全书所有后面的规划一个远方靶子。

**本段末尾必须明确写出全书 Objective 一句话**（番茄老师弈青锋的 OKR 递归大纲法）：这本书讲完时，主角必须达成一个**可验证的终局状态**（例："从一个杂役修士成为宗门长老并公开父辈冤案的真相"、"从黑户打工妹成为掌控三家皮草公司的老板娘并亲手送前夫进监狱"）。不要写"变强"、"复仇"这类抽象词，要写**一个能被外部观察者判定"达成 / 未达成"的具体状态**。这个 Objective 是全书 OKR 递归大纲的根——下面 volume_map 的每一卷会分解出这个 O 对应的 Key Results。

=== SECTION: volume_map ===

这是分卷散文地图，**5 段主体 + 1 段节奏原则尾段**。**关键要求：只写到卷级 prose**——写清楚每卷的主题、情绪曲线、卷间钩子、角色阶段目标、卷尾不可逆事件。**禁止指定具体章号任务**（不要写"第 17 章让他回家"这种章级布局）。章级规划是 Phase 3 planner 的职责，架构师只搭骨架、不编章目。

### 段 1：各卷主题与情绪曲线
有几卷？每卷的主题一句话，每卷的情绪曲线一段（哪里压、哪里爽、哪里冷、哪里暖）。不要机械的"第一卷打小怪第二卷打大怪"，写情绪的流动。

### 段 2：卷间钩子与回收承诺（前台/后台双层都要覆盖）
第 1 卷埋什么钩子、在哪一卷回收；第 2 卷埋什么、在哪一卷回收。散文写，不要表格。**只写卷级**（如"第 1 卷埋的身世之谜在第 3 卷回收"），不要写具体章号。

**钩子必须覆盖前台 + 后台两层**（对应 story_frame.段 2 建立的双层故事）：
- 前台钩子：当前卷内 arc 层面的短期钩子（查案谜题、对手身份、资源争夺等），预期在 1-2 卷内回收
- 后台钩子：贯穿全书的主线钩子（幕后真相、身世、体制秘密等），预期在终卷前后回收，核心的 3-7 条属于 core_hook=true

**如果本段只写前台钩子、没有后台钩子暗桩，说明你漏了整本书的引力轴，必须补上。**

### 段 3：各卷 OKR（Objective + Key Results）
用 OKR 递归大纲法分解全书 Objective（story_frame.段 4 末尾定的根 O）：每一卷都必须明确给出：
- **Objective（卷级目标）**：本卷结束时主角必须达成的**可验证状态**，一句话，与全书 Objective 逻辑递进相连（例：全书 O = "成为宗门长老并公开冤案"；卷 1 O = "从杂役转入正式弟子籍并拿到第一份能指向真相的线索"）
- **Key Results（3 条，可量化/可观察）**：支撑该 O 达成的三个关键子成果，每条必须是外部观察者能判定是否完成的状态变更（例 KR1 = "拿下药园执事位置"、KR2 = "与灵安峰结成稳定盟约"、KR3 = "发现父辈案卷的第一半页残片"）。不要写"变强"、"成长"这类模糊 KR

次要角色的阶段性变化也要点到（师父在第 2 卷会死、对手在第 3 卷会黑化等），写在 KR 条目下作为附注。写阶段性，不写完整弧线（完整弧线在 roles）。**每一卷 3 个 KR 是下游 planner 分解章节任务的直接依据——planner 拿到一卷的 3 个 KR 后，按每 3-5 章推进一个 KR 的节奏排章。**

### 段 4：卷尾必须发生的改变
每一卷最后一章必须发生什么不可逆的事——权力结构改变、关系破裂、秘密暴露、主角身份重定位。写散文，一卷一段。**只写"必须发生什么"，不指定是第几章**。

### 段 5：节奏原则（具体化 + 通用）
**这是节奏原则的唯一归宿，不再有独立 rhythm_principles section。** 本段输出 6 条节奏原则。**至少 3 条必须具体化到本书**（例："前 30 章每 5 章一个小爽点"），其余可保留通用原则（例："拒绝机械降神"、"高潮前 3-5 章埋伏笔"）。具体化 + 通用混合是合法的。反面例子："节奏要张弛有度"（废话）。正面例子："前 30 章每 5 章一个小爽点，且小爽点必须落在章末 300 字内"。6 条各写 2-3 句，覆盖（顺序不强制、可替换同权重议题）：
1. 高潮间距——本书大高潮之间最长多少章？（具体化优先）
2. 喘息频率——高压段多长必须插一章喘息？喘息章承担什么任务？
3. 钩子密度——每章章末留钩数量，主钩最多允许悬多少章？
4. 信息释放节奏——主线信息在前 1/3、中段、后 1/3 分别释放多少比例？（可通用）
5. 爽点节奏——爽点间距多少章一个？什么类型为主？（具体化优先）
6. 情感节点递进——情感关系每多少章必须有一次实质推进？

=== SECTION: roles ===

一人一卡 prose。**主角卡是本书角色弧线的唯一权威来源**——story_frame 不再写主角弧线，writer/planner 都从这里读。用以下格式分隔：

---ROLE---
tier: major
name: <角色名>
---CONTENT---
（这里写散文角色卡，下面的小标题必须全部出现，每段至少 3 行正经散文，不要写表格）

## 核心标签
（3-5 个关键词 + 一句话为什么是这些词）

## 反差细节
（1-2 个与核心标签反差的具体细节——"冷酷杀手但会给流浪猫留鱼骨"。反差细节是人物立体化的公式，必须有。）

## 人物小传（过往经历）
（一段散文，说这个人怎么变成现在这样。童年/重大事件/塑造性格的那件事。只写关键过往，简版。）

## 主角弧线（起点 → 终点 → 代价）
**只有主角必须写本段；其他 major 角色如果弧线分量重也可以写，否则略过。**主角从哪里出发（身份、处境、核心缺陷、一开始最想要什么），到哪里落脚（最终变成什么样的人、拿到/失去什么），为了这个落脚他付出了什么不可逆的代价（关系、身体、信念、某段过去）。不要只写"变强"这种平面变化，要写**内在的位移**。本段是之前 story_frame.段 2 迁移过来的权威位置，写足写实。

## 当前现状（第 0 章初始状态）
（第 0 章时他在哪、做什么、处境如何、最近最烦心的事。**只写角色个人处境**——初始钩子写在 pending_hooks 的 startChapter=0 行；环境/时代锚（如果是需要年份的题材）织进 story_frame.世界观底色。不再有独立的 current_state section。）

## 关系网络
（与主角、与其他重要角色的关系——一句话一条，关系不是标签是动态。）

## 内在驱动
（他想要什么、为什么想要、愿意付出什么代价。）

## 成长弧光
（他在这本书里会经历什么内在位移——变好变坏变复杂，落在哪里。非主角可短可长。）

---ROLE---
tier: major
name: <下一个主要角色>
---CONTENT---
...

（主要角色至少 3 个：主角 + 主要对手 + 主要协作者。建议 2-3 主 + 2-3 辅，不要灌水。质量 > 数量。）

---ROLE---
tier: minor
name: <次要角色名>
---CONTENT---
（次要角色简化版，只需要 4 个小标题：核心标签 / 反差细节 / 当前现状 / 与主角关系，每段 1-2 行即可）

（次要角色 3-5 个，按出场密度给。）

=== SECTION: book_rules ===

**只输出 YAML frontmatter 一块——零散文。** 所有的"叙事视角 / 本书专属规则 / 核心冲突驱动"等散文已经合并到 story_frame.世界观底色，不要在这里重复写。
```
---
version: "1.0"
protagonist:
  name: (主角名)
  personalityLock: [(3-5个性格关键词)]
  behavioralConstraints: [(3-5条行为约束)]
genreLock:
  primary: ${book.genre}
  forbidden: [(2-3种禁止混入的文风)]
numericalSystemOverrides:    # 仅当 gp.numericalSystem === true
  hardCap: (根据设定确定)
  resourceTypes: [(核心资源类型列表)]
prohibitions:
  - (3-5条本书禁忌)
chapterTypesOverride: []
fatigueWordsOverride: []
additionalAuditDimensions: []
enableFullCastTracking: false
---
```

=== SECTION: pending_hooks ===

初始伏笔池（Markdown 表格），Phase 7 扩展列：
| hook_id | 起始章节 | 类型 | 状态 | 最近推进 | 预期回收 | 回收节奏 | 上游依赖 | 回收卷 | 核心 | 半衰期 | 备注 |

伏笔表规则：
- 第 5 列必须是纯数字章节号，不能写自然语言描述
- 建书阶段所有伏笔都还没正式推进，所以第 5 列统一填 0
- 第 7 列必须填写：立即 / 近期 / 中程 / 慢烧 / 终局 之一
- 第 8 列「上游依赖」：列出必须在本伏笔之前种下/回收的上游 hook_id，格式如 [H003, H007]；若无依赖填「无」
- 第 9 列「回收卷」：用自然语言写该伏笔计划在哪一卷哪一段回收（例："第 2 卷中段"、"终卷终章前"）。不强制解析为章号
- 第 10 列「核心」：是否主线承重伏笔 true / false。主线承重伏笔一本书最多 3-7 条（主谜团、身世、核心承诺），其余次要伏笔填 false
- 第 11 列「半衰期」：可选，整数章数。若不填自动按回收节奏推导（立即/近期 = 10、中程 = 30、慢烧/终局 = 80）
- 初始线索放备注列，不放第 5 列
- **初始世界状态 / 初始敌我关系** 如果有关键信息（例如"主角身上带着父亲的笔记本"、"体制已经开始监视码头"），可以作为 startChapter=0 的种子行录入，备注列说明其"初始状态"属性。

## 最后强调
- 符合${book.platform}平台口味、${gp.name}题材特征
- 主角人设鲜明、行为边界清晰
- 伏笔前后呼应、配角有独立动机不是工具人
- **story_frame / volume_map / roles 必须是散文密度，不要退化成 bullet**
- **book_rules 只留 YAML，不要写散文**
- **不要输出 rhythm_principles 或 current_state 独立 section**——节奏原则合并进 volume_map 尾段；角色初始状态写在 roles.当前现状，初始钩子写在 pending_hooks（startChapter=0 行），环境/时代锚（仅历史/年代/都市重生等需要年份的题材）织进 story_frame.世界观底色，不要硬凑
- **pending_hooks 表必须包含 Phase 7 扩展列——depends_on 标出因果链、pays_off_in_arc 锁定回收大致位置、core_hook 标记主线承重伏笔（3-7 条）、half_life 仅给重点伏笔设置**
```

### 工作步骤

1. **读 brief 与题材底色**：brief 是用户最高优先级，题材底色（genre body）从 `references/genre-profiles/<genre>.md` 取（修仙、玄幻、年代、都市重生、历史同人……）。
2. **判定附加 block**：
   - `numericalBlock`：题材有数值/资源体系（如修仙、系统、游戏）→ 写硬上限和资源类型；否则告诉自己"无数值系统，留空 numericalSystemOverrides"。
   - `powerBlock`：能力体系是哪一种（修炼境界 / 系统数值 / 异能 / 无能力体系）。
   - `eraBlock`：是否需要年份/季节/历史事件锚（年代文 / 历史同人 / 都市重生 → 写；修仙 / 玄幻 / 末世 → 跳过）。
3. **生成 5 个 SECTION**：严格用 `=== SECTION: <name> ===` 分隔，不要漏一块也不要多一块。
4. **预算自检**：每个 SECTION 写完后心算字符数，超预算的部分必须自删。
5. **去重自检**：主角弧线只能在 roles，世界铁律只能在 story_frame.段 3，节奏原则只能在 volume_map.段 5——任何其他位置出现立刻删。
6. **5 SECTION 完整性自检**：story_frame / volume_map / roles / book_rules / pending_hooks 缺任何一段都视为 Architect 失败，必须补全后再交付（移植自 inkos `architect.ts` 完整性 guard）。
7. **OKR 自检（番茄老师弈青锋）**：story_frame.段 4 末尾必须有"全书 Objective 一句话 + 可被外部观察者判定的终局状态"；volume_map.段 3 必须每卷给出 `Objective` + `Key Results`（3 条）。缺即不通过。

### 切分写入

把模型输出按 SECTION 切分，分别落地到：

| SECTION         | 文件                                                |
|-----------------|----------------------------------------------------|
| story_frame     | `story/outline/story_frame.md`                     |
| volume_map      | `story/outline/volume_map.md`                      |
| roles           | `story/roles/<tier>/<name>.md`（一人一文件，按 `---ROLE---` 切） |
| book_rules      | `story/outline/story_frame.md` 的 YAML frontmatter（合并到顶部）或独立 `story/book_rules.md`（兼容老路径） |
| pending_hooks   | `story/pending_hooks.md`                           |

## Output contract

### 权威文件（authoritative）—— Phase 5+ 主路径

- `story/outline/story_frame.md` ——**顶部必须带 YAML frontmatter**（嵌入 book_rules 完整字段；移植自 inkos `buildBookRulesFromStoryFrameFrontmatter`），紧接 4 段散文（主题 / 冲突 / 世界 / 终局），≤ 3000 chars。frontmatter 是 book_rules 的**唯一权威来源**。
- `story/outline/volume_map.md` ——5+1 段散文（每卷主题情绪 / 卷间钩子 / 阶段目标 / 卷尾改变 / 节奏原则），≤ 5000 chars
- `story/roles/主要角色/<name>.md` 与 `story/roles/次要角色/<name>.md` ——一人一卡 prose，主角卡含完整 8 个 ## 子标题。**roles/ 是角色弧线的唯一权威来源**——`character_matrix.md` 不是。
- `story/pending_hooks.md` ——12 列 Markdown 表（hook_id / 起始章节 / 类型 / 状态 / 最近推进 / 预期回收 / 回收节奏 / 上游依赖 / 回收卷 / 核心 / 半衰期 / 备注），≤ 2000 chars

### Compat shim（双轨，下游读取顺序 frontmatter → flat）

- `story/book_rules.md` ← shim，与 story_frame frontmatter 同步；下游先读 frontmatter，缺/失败 fallback 到本文件
- `story/character_matrix.md` ← shim，从 roles/ 聚合生成；下游先读 roles/，缺/失败 fallback 到本文件

**双轨理由**：Phase 5+ 工具链按 frontmatter-first 读，外部工具直 cat 按 flat 读，Architect 同写两份保一致。重构只动权威文件，跑一次 Architect revise 重生成 shim。

### Cascade docOps（Architect 重做大纲时刷新下游）

Architect 重写 outline 后，下游指导 md 与新 outline 失同步——current_focus 引用已删钩子、某 role 的"当前现状"被 outline 改变。

5 SECTION + Foundation Reviewer pass **之后**额外产一份 docOps，写到 `story/runtime/architect-cascade.delta.json`：

```json
{
  "chapter": <manifest.lastAppliedChapter>,
  "docOps": {
    "currentFocus": [{ "op": "replace_section", "anchor": "## Active Focus",
      "newContent": "...", "reason": "Architect 重做：旧焦点引用的钩子已不在",
      "sourcePhase": "architect", "sourceChapter": <n> }],
    "roles": [{ "op": "patch_role_section", "slug": "lin-qiu",
      "anchor": "## 当前现状", "newContent": "...",
      "reason": "Architect 重做：主角弧线起点改变",
      "sourcePhase": "architect", "sourceChapter": <n> }]
  }
}
```

主循环 step 4a 自动调 `apply_delta.py --skip-hook-governance --skip-commitment-ledger --skip-book-metadata` 落盘。

**只 cascade 白名单 md**（current_focus / character_matrix / emotional_arcs / subplot_board / roles/*）；outline 自身（story_frame / volume_map）已整文件覆写，不再 docOps 二改。**绝不**动作者宪法（author_intent / book_rules / fanfic_canon / parent_canon）。

## Failure handling

Architect ≤ 2 轮（第 1 轮 + 必要时第 2 轮，每轮跟一次 Foundation Reviewer），按 verdict 分流：

- **`pass`** → 切分 SECTION 落盘
- **`revise`**（含 Reviewer 解析失败降级的 structural issue）→ `reviewFeedbackBlock`（issues + overallFeedback）注回 Architect 跑第 2 轮 + 再审；第 2 轮仍 ≠ pass 则 best-effort 落盘 + `architectStatus: "review-failed"` 写到 `story/runtime/architect-review.json`，**不再**第 3 轮
- **`reject`**（方向性崩塌，score < 50 或多维度 < 50）→ **不落盘、不自动重试**，issues 抛给用户决策（改 brief / 题材）

预算超限：自删后重写本 SECTION，不算重试。完整 Reviewer 决策见 [foundation-reviewer.md](../foundation-reviewer.md#failure-handling)。

## 注意事项

- **散文密度是核心**：bullet list 是失败信号；表格只在 pending_hooks / book_rules YAML 出现。
- **去重铁律**：写完每一段问自己"这段事实有没有在另一段出现过"，有就删。
- **不要写章号**：volume_map 只到卷级，禁止"第 17 章让他回家"这种章级布局——那是 Planner 的活。
- **主角卡是权威**：story_frame 段 1 末尾必须以指针形式指向 `roles/主要角色/<主角>.md`，不要重复写主角弧线。
- **eraBlock 是题材判定**：修仙 / 玄幻 / 系统流不要硬塞年份；年代 / 历史 / 重生流必须织入年份与重大历史事件。
- **English book**：用 inkos 的 `buildEnglishFoundationPrompt` 等价英文 prompt，所有 SECTION 名保持不变（`=== SECTION: story_frame ===` 等），段落用英文写。英文路径同样必须 emit 全部 5 SECTION 完整、story_frame.段 4 末尾必须含一句 Book Objective（参 inkos `architect.ts` 中 `You MUST emit all 5 SECTION blocks in order` 的英文段）。
- **重做时**：附加 contextBlock（"这是第 N 次架构，前次产物存在于…"）和 reviewFeedbackBlock（"上次的问题…"），让 Claude 看到历史。
