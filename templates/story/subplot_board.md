# 支线板（Subplot Board）

> 每章 Settler 阶段如有 `subplotOps`（apply_delta 内部转译为 `docOps.subplotBoard`），
> apply_delta 会按 `subplotId` upsert 行；user-directive 也可直接走 `docOps.subplotBoard`。
> status：active / dormant / resolved / abandoned。

| subplotId | name | status | lastAdvancedChapter | characters | notes |
|---|---|---|---|---|---|
