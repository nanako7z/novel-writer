# POV 过滤（POV Filter）

> `scripts/pov_filter.py` 的真理来源。源自 inkos `utils/pov-filter.ts`。
> 用途：装配 Composer 的 context_pkg 时，把 POV 角色不可能知道的真相裁剪掉，避免 Writer 在该章不经意泄露反派密谋 / 他人秘密 / 远端剧情。

---

## 为什么要过滤

Composer 默认把 active hooks、subplot board、character matrix 等真理文件**全部塞进 Writer 上下文**。这在大多数全知或多视角小说里没问题，但只要某一章是单 POV，问题就出现：

- Writer 看到反派 hook 的 expectedPayoff，于是在叙述中"无意"渗透——读者立刻意识到主角并不知道这件事。
- Writer 看到第 8 章 B 对 C 说的密语（POV 为 A），却让 A 在第 12 章引用——明显穿帮。
- 同人 / 多线小说尤其严重，因为 Composer 提供的"全息真相"会污染单一视角。

POV 过滤把 truth 裁成 **"POV 知 / 不知 / 可推"** 三栏，再决定要不要把对应条目放进 Writer 上下文。

---

## 三档可见性

| 标签 | 判定 | 处理 |
|---|---|---|
| `visible` | POV 在 hook 的 `involvedCharacters` 中，**或** POV 在 hook 的 mention chapters 中（`startChapter` / `lastAdvancedChapter` / `mentionChapters` 任一与 POV 出现章重合） | 透传给 Writer |
| `inferred` | hook 的 `notes` / `expectedPayoff` 字符串里出现了 POV 名字（间接证据） | 默认透传；`--strict` 模式下视为 hidden |
| `hidden` | 既不直接涉及 POV、POV 也不在任何 mention 章 | 不传给 Writer，并写入 `pov_blindspots`，提醒 Writer "本章别意外说出来" |

POV 出现章 = `chapter_summaries` 里 characters / events / stateChanges / hookActivity 任一字段含有 POV 名字的章节集合。

---

## 算法步骤

1. **加载真理**：`story/state/hooks.json`（active hooks）+ `story/character_matrix.md`（关系图）+ `story/state/chapter_summaries.json`（fallback：`story/chapter_summaries.md`）+ 可选 `story/subplot_board.md`。
2. **建 POV 章集合**：扫所有 chapter_summaries，把"该章描述里出现 POV 名字"的章号收集成 `povChapters`。
3. **逐 hook 分类**：跳过 status ∈ {resolved, abandoned, completed, closed}；其余按上面三档分类。
4. **逐 subplot 行分类**：subplot_board.md 按 markdown 表格解析，每行检查 POV 是否被提及 + 是否有章号字段命中 POV 章集合。
5. **可选过滤 ContextPackage**：若给了 `--input <context_pkg.json>`，则把 `selectedContext` 中"提到 blindspot id 但没提 POV"的条目剔除；strict 模式下连带剔除所有 hook_debt 类条目（Composer 拼的钩债简报）中不含 POV 名字的行。
6. **输出**：`filtered_hooks` / `filtered_subplots` / `pov_blindspots` / `filtered_context`，外加 summary 一行。

---

## CLI

```bash
python scripts/pov_filter.py \
  --book books/<id> \
  --pov 林川 \
  --current-chapter 12 \
  [--input books/<id>/story/runtime/context_package.json] \
  [--strict] \
  [--json]
```

退出码：**始终 0**。POV 过滤是建议性的——决定权在 Composer 自己（决定要不要重写 context_pkg）。

---

## 输出 schema

```json
{
  "pov": "林川",
  "currentChapter": 12,
  "strict": false,
  "povChapters": [1, 3, 4, 7, 9],
  "relationships": {"赵九": "敌对", "苏雨": "盟友"},
  "filtered_hooks": [
    {"hookId": "H03", "_pov_visibility": "visible", "_pov_reason": "POV in involvedCharacters", ...}
  ],
  "filtered_subplots": [...],
  "pov_blindspots": [
    {"id": "H07", "category": "hook", "reason": "POV not in chapters [5, 6] and not involved",
     "expectedPayoff": "..."}
  ],
  "filtered_context": [...],
  "summary": "pov=林川, povChapters=5, hooks visible=4/9, subplots visible=2, blindspots=5, strict=false"
}
```

`pov_blindspots` 是给 Writer 的反向清单："这些事 POV 不知道，写本章时不要让叙述者带出来"。

---

## strict 模式

`--strict` 把 `inferred` 一档全部当 hidden 处理。何时打开：

- 严肃 / 古典文风的单 POV 章节（红楼/古典推理路线，过度泄露视角等于穿帮）。
- 悬疑 / 推理章节（保留信息缺口是核心爽点）。
- 信息差是核心爽点的桥段（POV 蒙在鼓里时读者一同体验）。

何时**不要** strict：

- 同人 AU / OOC 模式：原作设定本身被改，POV 严格信息边界往往与同人体验冲突。
- 群像章 / 切换 POV 章：本就不是单视角。
- 心声 / 第一人称重内省章：inferred 信息属于 POV 的合理推测，删掉会丢失角色厚度。

---

## 与 Composer / Writer 的衔接

**Composer**（[03-composer.md](phases/03-composer.md)）：

1. 在拼 selectedContext 之后、写 context_package.json 之前，**如果 chapter_memo 声明了单 POV**（`pov: <character>` 或 `视角: <character>`），跑一次 `pov_filter.py --input <draft context_pkg>`。
2. 用脚本输出的 `filtered_context` 覆盖原 selectedContext，然后再把 `pov_blindspots` 作为新增条目写入 `selectedContext`，source = `runtime/pov_blindspots`，excerpt 写成"以下事项 POV 不知，本章不要主动揭示：…"。
3. 把 `pov_chapters` / `relationships` 摘要写入 `chapter_trace.composerInputs.pov`，便于回溯。

**Writer**：上下文里出现 `pov_blindspots` 即等同于硬约束——叙述者不得在本章正文中主动揭示这些 hook / subplot 的内容（角色之间猜测、误读是允许的）。

---

## fanfic 模式的放宽

参 [branches/fanfic.md](branches/fanfic.md)：

- `canon` 模式：严格遵循原作信息边界 → 推荐 `--strict`。
- `au` 模式：世界观已变，原作的"POV 应该不知道"未必成立 → **不要** `--strict`，inferred 全放行。
- `ooc` 模式：角色性格被改 → 信息边界仍按原作处理（OOC 改的是反应不是认知）→ 默认模式即可。
- `cp` 模式：CP 双方关系常含"互相试探"——双方各自的盲区是爽点来源 → 推荐 `--strict`。

Composer 调用本脚本时根据 `book.json#fanficMode` 决定是否传 `--strict`。

---

## 实现注意

- POV 名字匹配按字符串 `in`，不作分词——所以 POV 名字应使用全名（"林川"而非"小川"），否则会大量误命中。
- subplot_board.md 行解析依赖列名包含 `chapter` 或 `章` 才能抽章号；若你的项目用了 hashed id 列，命中率会低，建议给行加 chapter 字段。
- chapter_summaries 缺失时（项目刚建立），`povChapters` 退化成空集 → 几乎所有 hook 都会被分类成 hidden。这是预期行为：项目早期信息少，宁可裁也别污染。
- filtered_hooks 元素都附带 `_pov_visibility` 与 `_pov_reason` 两个调试字段，前缀 `_` 提示 Writer 不要把它们渲进正文。
