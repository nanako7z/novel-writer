# 工作目录解析（init_book.py `--workdir` 取值规则）

`init_book.py` 的 `--workdir` 接受任意路径，但**用户没给路径**时不要默认拿当前 cwd——cwd 可能是用户的家目录、Desktop、或别的无关项目根，直接在那儿落 `inkos.json` + `books/` 会污染。

## 规则

1. **用户显式给了路径**（"在 `~/my-novels` 下起一本"、"用 `/tmp/test-book`"）→ 直接用，不质疑。
2. **用户没给路径，但 cwd 已经是写作工作区**（cwd 里有 `inkos.json`，或 cwd 路径名暗示写作意图，如 `novels` / `writing` / `novel-writer-workspace` 类）→ 用 cwd。
3. **用户没给路径，cwd 也不像写作工作区** → **默认在 cwd 下创建 `novel-writer-workspace/` 子目录**当 workdir：
   ```bash
   python {SKILL_ROOT}/scripts/init_book.py --workdir ./novel-writer-workspace ...
   ```
   先告诉用户："没指定目录，我会在当前位置 (`<cwd>`) 下建一个 `novel-writer-workspace/` 来放项目。也可以告诉我你想放哪。" 用户认可或沉默继续 → 创建；用户给了别的路径 → 切到 §1。
4. **绝不静默拿 cwd 顶层落 `inkos.json`**——除非命中规则 2。这是防呆，不是猜用户。
