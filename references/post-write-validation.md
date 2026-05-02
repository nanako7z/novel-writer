# Post-Write Validation（Writer 输出解析 + 章节确定性体检）

novel-writer 主循环里，"Writer 写完一稿" 与 "Auditor 跑 37 维" 之间多一道**确定性闸门**：
两个零 LLM 成本的脚本兜住格式与机械层雷点，避免把可正则发现的问题塞进 Auditor 的 token 预算。

| 顺序 | 脚本 | 角色 |
|---|---|---|
| 1 | `scripts/writer_parse.py` | 把 Writer 的 raw 输出按 sentinel 拆出 `{title, body, summary, postWriteErrors}` |
| 2 | `scripts/post_write_validate.py` | 对解析出的 body 跑机械层 lint，输出 issue 列表 |

两个脚本都**只读不写**，绝不修改真理文件，绝不修改 `chapters/`。

## 1. `scripts/writer_parse.py`

### CLI

```bash
python scripts/writer_parse.py --file <writer-output.md> [--strict] [--json]
```

- 默认（lenient）：缺失 `CHAPTER_TITLE` 时回退到 H1 / 首行；缺失 `CHAPTER_CONTENT` 时回退到最长散文块；缺失 `CHAPTER_SUMMARY` 时返回 `null`。
- `--strict`：缺失 `CHAPTER_TITLE` 或 `CHAPTER_CONTENT` 任一项即 exit 2。
- `--json`：显式声明输出 JSON（默认就是 JSON，flag 仅为 clarity）。

### Sentinel 契约（与 Writer §14 OUTPUT FORMAT 对齐）

```
=== PRE_WRITE_CHECK ===
=== CHAPTER_TITLE ===          (required)
=== CHAPTER_CONTENT ===        (required)
=== POST_SETTLEMENT ===
=== UPDATED_STATE ===
=== UPDATED_LEDGER ===         (only when numericalSystem)
=== UPDATED_HOOKS ===
=== CHAPTER_SUMMARY ===
=== UPDATED_SUBPLOTS ===
=== UPDATED_EMOTIONAL_ARCS ===
=== UPDATED_CHARACTER_MATRIX ===
=== POST_WRITE_ERRORS ===      (optional — Writer 自报违规)
```

为方便手写测试 fixture，脚本同时识别短别名：`TITLE → CHAPTER_TITLE`、`BODY/CONTENT → CHAPTER_CONTENT`、`SUMMARY → CHAPTER_SUMMARY`、`POSTWRITE_ERRORS → POST_WRITE_ERRORS`。即可以直接喂 Writer 原始整章响应，也可以喂经过抽取的简化形式。

### 输出

成功：

```json
{
  "ok": true,
  "title": "...",
  "body": "...",
  "wordCount": 2547,
  "summary": "...",
  "preWriteCheck": "...",
  "postWriteErrors": ["..."],
  "extras": { "POST_SETTLEMENT": "...", "UPDATED_HOOKS": "..." },
  "raw_sentinels_found": ["PRE_WRITE_CHECK", "CHAPTER_TITLE", "CHAPTER_CONTENT"],
  "missing_required": [],
  "lenient_fallback_used": false
}
```

失败（strict + 缺 sentinel；或空输入）：

```json
{ "ok": false, "error": "missing required sentinel(s): CHAPTER_TITLE",
  "raw_sentinels_found": [], "missing_required": ["CHAPTER_TITLE"] }
```

`lenient_fallback_used: true` 表示这份结果用了 H1 / 首段等启发式补齐，调用方应在日志里留痕。

### 落盘约定（调用方负责）

- Writer raw 整段保留在 `story/raw_writer/<NNNN>.md`（保留所有 `=== BLOCK ===`）。
- `body` 字段单独写到 `story/runtime/chapter-<NNNN>.parsed.md`，作为后续 Auditor / Polisher 的工作 buffer。

## 2. `scripts/post_write_validate.py`

### CLI

```bash
python scripts/post_write_validate.py --file <chapter.md> [--chapter N] [--book <bookDir>] [--strict] [--json]
```

- `--file` 接收的是 `writer_parse.py` 落出来的 body（可带 frontmatter）。
- `--chapter` 可选；给了就做 frontmatter / 文件名 / 正文章节号交叉校验。
- `--book` 可选；给了就启用 `story/character_matrix.md` 的角色名变体检查。
- `--strict`：把 warning 也升格为 gating（exit 2）。
- `--json`：显式声明 JSON 输出（默认即 JSON）。

退出码：

- `0`：无 critical（warning 允许，除非 `--strict`）
- `2`：出现 critical（或 `--strict` 模式下出现 warning）

apply_delta-style 集成可以用退出码作闸门——主循环看到 2 就回退到 Writer 重写或进 spot-fix Reviser。

### 检查类目

| 类目（category） | severity | 检查内容 | 阈值 / 修复建议 |
|---|---|---|---|
| `title-format` | info / warning | H1 标题缺失 / 多个 H1 | 单 H1，正文不要二次起标题 |
| `chapter-ref` | critical | 文件名 / frontmatter / `--chapter` 数字不一致；正文出现 `第N章` / `Chapter N` | 角色不该知道自己在第几章；改为"那天晚上""仓库出事那次" |
| `paragraph-shape` | warning / critical | 短段 < 35 字数 ≥ 4 且占比 ≥ 60%；连续 ≥ 3 个短段；≥ 2 段 > 300 字；任何段 > 600 字（critical）；任何段 ≥ 1200 字（critical，疑似断段丢失）；全章只有 1 段（critical） | 在动作切换或情绪节点处断开；把动作/观察/反应并入同段 |
| `dialogue` | critical / warning / info | `“`/`”` 不成对（critical）；`「`/`」` 不成对；半角 `"` 紧贴中文 ≥ 2 处；`”` 后多余空格再接"说/道"（info） | 用全角中文引号 `"…"`，引号紧贴 `说/道` |
| `forbidden-pattern` | critical | `不是…而是…` 句式；`——` 破折号 | 改用直述句 / 用逗号或句号断句 |
| `ai-tell` | warning | 转折/惊讶标记词（仿佛/忽然/竟然/猛地/猛然/不禁/宛如）总数 > `len/3000` | 用具体动作或感官描写传递突然性 |
| `report-terms` | critical | "核心动机/信息边界/核心风险/利益最大化/当前处境/沉没成本/认知共鸣"等分析框架术语 | 这些只能用于 PRE_WRITE_CHECK 内部推理；正文用口语化表达 |
| `meta-narration` | warning | 编剧旁白（"接下来就是…/到这里算是…/读者可能…"） | 删除元叙事，让剧情自然展开 |
| `sermon` | warning | "显然/毋庸置疑/不言而喻/众所周知/不难看出" | 让读者从情节自己判断 |
| `collective-shock` | warning | "全场震惊/众人哗然/一片寂静" | 改写成 1-2 个具体角色的身体反应 |
| `rhythm` | warning | 连续 ≥ 6 句包含"了" | 保留最有力的一个，其余改为无"了"句式 |
| `annotation-leak` | critical | 残留 `[作者按]` / `[TODO]` / `<TODO>` / HTML 注释 / 【作者…】 / Writer 输出 sentinel `=== TAG ===` | 删除批注；如果是 sentinel 残留说明 writer_parse 切片错位，重跑 |
| `length` | critical / warning | 全文 < 200 字（critical，疑似空稿/截断）；> 20000 字（warning） | 触发 critical 时直接打回 Writer 重写 |
| `character-consistency` | warning | 出现 character_matrix 没收录的疑似变体（角色名 + 儿/哥/姐/爷/君/公子；或主角 stem 单独出现） | 确认是否同一角色，统一命名 |

### 输出

```json
{
  "ok": false,
  "issues": [
    {"severity": "critical", "category": "chapter-ref",
     "description": "正文中出现章节号指称：「第3章」",
     "line": 42, "evidence": "…他想起在第3章曾经…"}
  ],
  "summary": "chars=2547 paragraphs=42 critical=1 warning=2"
}
```

`ok` 仅在没有 `severity=critical` 时为 `true`。退出码：0 = 无 critical；2 = 有 critical（或 `--strict` 模式下出现 warning）。

## 3. 在主循环中的调用位置

集成点：phase 05 Writer 与 phase 08 Normalizer 之间，编号为 step 5b（writer_parse）+ step 5c（post_write_validate），见 `references/phases/00-orchestration.md`。

```
05 Writer  ────────────────►  story/raw_writer/<NNNN>.md
                              │
                              ▼  step 5b
                  scripts/writer_parse.py --file <raw> --strict
                              │ ok? -> body 写到 story/runtime/chapter-<NNNN>.parsed.md
                              │ fail-> 让 Writer 重新输出仅缺失部分（≤2 次）；最终回退到默认 lenient 模式
                              ▼  step 5c
            scripts/post_write_validate.py --file <parsed> --chapter N [--book <bookDir>]
                              │ exit 2 (critical)? ──► 把 issues 反馈给 Writer 重写一次；
                              │                        仍 critical → 报错给用户（不进 Normalizer/Auditor）
                              │ exit 0 但有 warning ──► warning 进 Auditor 的合并 issues 列表
                              ▼
                       08 Normalizer（如长度漂出 soft）
                              ▼
                       09 Auditor（输入是 parsed body；post_write warning 合并到 issues）
```

### 与 Writer 的反馈回环

post_write_validate 的 `issues[].description` + `evidence` 是给 Writer 重写的 prompt 输入。重写一次仍 critical → 主循环 fail 并 surface 给用户，不要硬落盘。

### 与 Reviser 的关系（备选路径）

如果用户不想走 Writer 重写，也可以把 critical 直接喂 Reviser **spot-fix** 模式当 patch instruction——把"对话引号没闭合""出现破折号"这类纯机械错误成本压到最低。warning / info 则不阻塞，与 LLM-audit 的发现并表，由 Reviser polish 模式统一处理。

### 与 Auditor（phase 09）的协作

- Auditor 的 user prompt 输入用的是 `writer_parse.py` 解析后的 `body` 字段，
  **不是** raw Writer 输出（避免把 sentinel 头当作正文评分）。
- post_write 的 issue 列表合并进 Auditor `auditResult.issues`，
  但只有 `severity=critical` 的 post_write 项强制把 `passed` 拉成 false。
- post_write 必须先于 Auditor 跑完——这是顺序闸门；详见 phase 09 Inputs 段。

## 4. 失败处理

- `writer_parse.py` 严格模式失败：让 Writer 重输出缺失 sentinel 区块（最多 2 次），仍失败再开 `--lenient`。
- `writer_parse.py` lenient 仍失败：把 raw 输出当章末 best-effort 写入 `story/raw_writer/`，
  在 chapter manifest 标 `parser-failed-best-effort`，不阻塞下一章 Plan。
- `post_write_validate.py` 永不阻塞主循环；它只是建议。SKILL 决策点是"看到 critical 就走 spot-fix"。

## 5. 注意事项

- 这两个脚本是 **port** of inkos `agents/writer-parser.ts` + `agents/post-write-validator.ts`，
  改动检查阈值前请先回看那两个 TS 源文件，保持行为兼容。
- 角色名一致性检查用的是 `story/character_matrix.md` 的 `## 角色名` 头作为 canonical 名单；
  如果一本书没启 `bookRules.enableFullCastTracking`，矩阵可能稀疏，检查会自动降级（不报 false positive）。
- 不要在这里加任何"文笔层"判断（句式美感、节奏舒适度等）——文笔归 Polisher（phase 11）和 Auditor（dim 24-27），
  这两个脚本只管"机械层是否成立"。
