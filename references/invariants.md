# 主循环不变量（编号化单一来源）

每条规则**只在本文件**有完整定义。SKILL.md / 00-orchestration / 各 phase ⛔ 块**仅引用编号**（如「见 invariant #3」），不复述。新增规则统一加在末尾，不补编号。

## #1 — 真理文件只能经 `scripts/apply_delta.py` 修改

直接编辑 `story/state/*.json` / `story/pending_hooks.md` 等真理文件视为脏写——manifest 校验会发现，hookOps 治理会失效。

- 守者：所有阶段（Settler / Architect cascade / user-directive 都走 apply_delta）
- 例外：「作者宪法」四件套（`author_intent.md` / `book_rules` / `fanfic_canon.md` / `parent_canon.md`）允许 LLM 直接 `Edit`，但事后必须 `apply_delta.py log-direct-edit` 补审计日志

## #2 — 章节正文落盘前必须经过 audit-revise 闸门

即便 audit 失败也要标 `status: "audit-failed-best-effort"` 写进 `chapters/index.json`——保证问责可追溯，**禁止**悄悄写盘。

- 守者：主循环 step 7 → step 11

## #3 — 阶段产物先落 `story/runtime/`，最终成果才落 `chapters/` 与 `story/state/`

`story/runtime/chapter-{NNNN}.<phase>.md` 是中间审计证据；`chapters/{NNNN}.md` + delta 才是最终成果。可回溯、可重跑。

- 守者：所有阶段
- 反例：把 runtime sidecar 命名搬进 `chapters/` 会被 `doctor.py` 标 critical

## #4 — LLM 输出解析失败的重试上限

| 阶段 | 上限 |
|---|---|
| Planner | 3 |
| Architect（含 Foundation Reviewer 回环） | 2 |
| audit-revise 整轮 | 3 |
| Settler | 2 |
| Writer post-write retry | 1 |
| Chapter Analyzer | 2 |

到达上限仍失败：写 stub 或标 `audit-failed-best-effort`，**禁止**伪造通过。

## #5 — 重跑必须注入「上次失败原因」

任一阶段 LLM 输出解析 / 校验失败重跑时，prompt 必须显式注入失败的具体原因：schema 错位、缺哪个块、违反哪条规则、命中哪个治理 issue、parser feedback、governance feedback、postWriteFeedback、validationFeedback。**禁止**裸重发原 prompt 让 LLM「再猜一次」。

- 守者：Planner（`MEMO_RETRY_LIMIT`）/ Architect（Foundation Reviewer 回环）/ Writer（`postWriteFeedback`）/ Settler（`parserFeedback` / `governanceFeedback`）/ apply_delta 重跑

## #6 — Writer sentinel + 写后检是强制闸门

Writer 输出走 sentinel 4 块（`=== CHAPTER_TITLE ===` / `=== CHAPTER_CONTENT ===` / `=== CHAPTER_SUMMARY ===` / `=== POSTWRITE_ERRORS ===`）。落盘后**立即**跑 `writer_parse.py --strict` + `post_write_validate.py`，critical 命中允许 Writer 重写一次（详见 #4）。**禁止**跳进 Normalize / Audit。

- 守者：主循环 step 5 → 5b → 5c

## #7 — Settler 主动 5 项铁律

每章 Settler 在产 delta 前必须**逐项审视**周边文件，每项给「是否需更新 + 不更新的理由」：

1. `current_focus.md`
2. `character_matrix.md`
3. `emotional_arcs.md`
4. `subplot_board.md`
5. `story/roles/<slug>.md`

`docOps` 字段必填——哪怕 `{}`，也是「我已显式查过」的承诺。**禁止**把检测责任甩给下章 docops_drift 扫描。

## #8 — Reflector 并入 audit-revise

Reflector **不是**单独阶段；其职责并入 step 7 的 audit-revise loop。inkos README 提到，但实际代码合并。

## #9 — Chapter Analyzer 单向只读

Step 11.05 的 Chapter Analyzer 只读已定稿章节正文与配套 runtime/state，产物只有 `chapter-{NNNN}.analysis.json`。**不**修改 `chapters/*` / `story/state/*` / `pending_hooks.md`。失败也不阻断主循环（写 stub）。区别于 Auditor / Settler 这两个 load-bearing 阶段。

## #10 — Polisher 只在 audit 真正过线时入场

Step 10.5 的 Polisher 单 pass、不开回环、引入新问题即回退。借线（score 85-87）跳过；audit 未过则压根不进 Polisher，由 Reviser 兜底。

## #11 — Step-checkpoint 强制（loop_state.py）

主循环每个 step 入口必须先调 `scripts/loop_state.py require --step <id>`；step 完成必须 `mark --step <id>`。**禁止**跳过 require：跳过会导致下一 step 的 require exit 3。详见 [`loop_state.md`](loop_state.md)。

- 守者：主循环 writeNextChapter；单点指令不强制
