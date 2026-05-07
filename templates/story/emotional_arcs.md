# 情感弧线（Emotional Arcs）

> 每章 Settler 阶段如有 `emotionalArcOps`（apply_delta 内部会转译成 `docOps.emotionalArcs`），
> apply_delta 会向本表 upsert 行；user-directive 也可直接走 `docOps.emotionalArcs`。
> 强度 1-10；arcDirection 取 rising/falling/stable/turning。

| character | chapter | emotionalState | triggerEvent | intensity | arcDirection |
|---|---|---|---|---|---|
