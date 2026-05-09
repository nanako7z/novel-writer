# 用户指令式调整设定（白名单 / 作者宪法两条路径）

当作者在主对话里说"调整 X 设定"/"把主角性格改成 Y"/"修一下风格指引"时，按目标文件分流。

## 路径 A：白名单指导 md

**适用文件**：`current_focus.md` / `style_guide.md` / `character_matrix.md` / `emotional_arcs.md` / `subplot_board.md` / `outline/*` / `roles/*`

**不直接 `Edit`**——走 docOps user-directive 通道，保证 anchor 解析 / 表结构 / `.bak` 备份 / 可回滚。

流程：

1. 先 `Read` 对应 md 看清结构（H2 节名 / 表格列名）
2. 写 `story/runtime/user-directive.delta.json`：

```json
{
  "chapter": "<manifest.lastAppliedChapter, 缺省 0>",
  "docOps": {
    "currentFocus": [{
      "op": "replace_section",
      "anchor": "## Active Focus",
      "newContent": "...",
      "reason": "用户：把焦点从 X 切到 Y",
      "sourcePhase": "user-directive",
      "sourceChapter": "<lastAppliedChapter>"
    }]
  }
}
```

`reason` ≤ 200 chars，引作者原话。

3. 调 apply_delta：

```bash
python scripts/apply_delta.py --book <bookDir> \
  --delta story/runtime/user-directive.delta.json \
  --skip-hook-governance --skip-commitment-ledger --skip-book-metadata
```

4. 把 `docOpsApplied` 回执作者。

## 路径 B：作者宪法

**适用文件**：`author_intent.md` / `book_rules`（=`book.json#bookRules` 子树）/ `fanfic_canon.md` / `parent_canon.md`

这四个文件**自动通道永远只读**（schema 阶段就拒），但作者明示指令时 LLM 可直接 `Edit`——作者主权例外。

流程：

1. LLM 直接 `Edit` 目标文件
2. 调 helper 补审计日志（必走，写入 `story/runtime/doc_changes.log`，`opId` 自动 SHA8）：
   ```bash
   python scripts/apply_delta.py --book <bookDir> log-direct-edit \
       --file story/author_intent.md --reason "用户：把核心命题改成 X"
   ```
3. 不走 `.bak`——宪法变更频次低，靠 git 或手动备份兜底。`revert-doc-op` 对 direct_edit 不支持；回滚走 `git checkout`
4. 把 helper 回执的 `opId` 告诉作者

## LLM 自己起意改任何 md → 必须先问作者

如果 LLM 觉得"这条焦点该推进了"但作者没明示，**先问作者**再决定走 A 还是 B（防止隐性设定漂移）。Settler / Architect 通过自动 docOps 改白名单 md 是合法的——因为有本章正文驱动；跳出主循环的"主动改"必须由作者授权。
