# 角色关系矩阵（Character Matrix）

> 每章 Settler 阶段如有 `characterMatrixOps`（apply_delta 内部转译为 `docOps.characterMatrix`），
> apply_delta 会按 `(charA, charB)` upsert 行；user-directive 也可直接走 `docOps.characterMatrix`。
> intimacy 取 -10 到 +10；正值越大越亲近，负值越大越敌对。

| charA | charB | relationship | intimacy | lastInteraction | notes |
|---|---|---|---|---|---|
