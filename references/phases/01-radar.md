# Phase 01: Radar（市场雷达）

> ⛔ **硬约束 / 不跳步**：
> 1. **前置**：用户已给题材 / 平台 / 字数指标等市场信号，或显式问"网文市场什么火"；这是侧流，**不进**主循环、**不**写真理文件
> 2. **本阶段必跑**：Stage A→B→C 三档递进抓榜（std-lib HTTP → Claude WebFetch → 用户贴）+ 5 维 radar 分析（题材热度 / 平台对位 / 字数节奏 / 同题材竞品 / 切入点）
> 3. **退出条件**：答用户，**不写**真理文件（缓存落 `.radar-cache/` 不算）；用户若要立项，转 SKILL.md "项目初始化"小节调 `init_book.py`
> 4. **重试规则**：Stage A 失败不阻断（落 `pendingWebFetch[]`）；Stage B 失败转 Stage C；三档全空且未 `--allow-knowledge-fallback` 时**不让 LLM 凭印象编**，要求用户补料

## 何时进入

- 用户**直接**问"现在玄幻什么火 / 番茄最近哪种开局收藏好 / 看下当前网文市场 / radar"
- 项目初始化时还没确定题材，需要先扫一轮市场
- 用户给的市场信号很模糊，需要一手榜单数据校准
- 入口语句示例：
  - "现在玄幻什么火？"
  - "我想做一本爽文但不知道选什么细分赛道"
  - "番茄最近哪种开局收藏好？"
  - "看下当前网文市场什么火"

## Inputs

- 题材范围（可选）：用户限定"玄幻 / 都市 / 奇幻"等 SKILL 内部 genre id
- 平台（可选）：番茄 / 起点 / 飞卢 / 晋江 / 纵横 / SF 轻小说 / 其他
- 用户主动提供的市场信号（可选）：粘贴的榜单截图 / txt / 链接——若用户已给，跳过 Stage A 直接进 LLM 分析
- workdir：缓存落 `<workdir>/.radar-cache/`，解析规则同 SKILL.md §"工作目录解析"

## Process

### Stage A — std-lib 主动抓（必跑）

```bash
python {SKILL_ROOT}/scripts/radar_fetch.py scan \
    --sites <all|逗号分隔站点> \
    --genre <SKILL genre id 或 all> \
    --top 15 \
    --max-age-hours 6 \
    --workdir <bookDir 或当前工作区>
```

支持的 site id：`fanqie / qidian / feilu / jjwxc / zongheng / sfacg`（详见 [radar-sources.md](../radar-sources.md)）。stdout 输出 JSON，schema：

```json
{
  "fetchedAt": "...",
  "request": {"sites": [...], "genre": "...", "top": 15},
  "rankings": [{"site": "...", "platform": "...", "fetchedVia": "http|cache", "entries": [...]}],
  "failures": [...],
  "pendingWebFetch": [{"site": "...", "url": "...", "reason": "..."}],
  "pendingUserPaste": [],
  "allowKnowledgeFallback": false
}
```

退出码 `0` 代表至少一个站拿到数据**或**有 `pendingWebFetch[]` 待你接力；`2` 代表全空且无兜底——直接进 Stage C。

### Stage B — Claude WebFetch 兜底（按需跑）

对 stdout 的 `pendingWebFetch[]` 逐项处理：

1. 调 `WebFetch` 拉对应 URL，prompt 让模型抽 `1. 书名 / 作者 [类别]` 形态的 top-N 列表（限 10-15 条）
2. 写到 `/tmp/radar-<site>.txt`（或直接走 inline）
3. 灌回主缓存：

```bash
python {SKILL_ROOT}/scripts/radar_fetch.py merge \
    --site <id> \
    --paste @/tmp/radar-<site>.txt \
    --via webfetch \
    --genre <id 或 all> \
    --workdir <同 Stage A>
```

4. 重跑 `scan`（或直接合并到上一份 stdout 内的 `rankings[]`）。WebFetch 也失败的 site 进 Stage C。

### Stage C — 用户贴（最后兜底）

仍空的站，按 inkos 原话术让用户补：

> "请把番茄玄幻榜前 10 名（含书名+作者+收藏数/简介）粘给我。"

拿到后用同一个 `merge` 命令灌回缓存（`--via user-paste`）。

### Stage D — LLM 分析（必跑，prompt 不动）

把 `rankings[].entries` 拼成 markdown 段落，按下面 system prompt 走一遍 LLM。**这段 prompt 与 inkos 同源（`packages/core/src/agents/radar.ts`），改了等于改基线**。

```
你是一个专业的网络小说市场分析师。下面是从各平台实时抓取的排行榜数据，请基于这些真实数据分析市场趋势。

## 实时排行榜数据

{每个 ranking 渲染：### platform / - title (author) [category] extra }

分析维度：
1. 从排行榜数据中识别当前热门题材和标签
2. 分析哪些类型的作品占据榜单高位
3. 发现市场空白和机会点（榜单上缺少但有潜力的方向）
4. 风险提示（榜单上过度扎堆的题材）

输出格式必须为 JSON：
{
  "recommendations": [
    {
      "platform": "tomato | feilu | qidian | jjwxc | zongheng | sfacg | other",
      "genre": "<细分赛道>",
      "concept": "<一句话描述切入点>",
      "confidence": 0.0-1.0,
      "reasoning": "<为什么这个赛道现在能做（引用具体榜单数据）>",
      "benchmarkTitles": ["对标作 1", "对标作 2"]
    }
  ],
  "marketSummary": "整体市场概述（基于真实榜单数据，2-3 句）"
}

推荐数量：3-5 个，按 confidence 降序排列。
```

**对话回复**：用户更想要人话推荐，不要给 JSON。给 3-5 条 → 题材 + 一句话定位 + 为什么现在能做 + 一两本对标。

### Stage E — 立项（可选）

用户说"行，就做这个"→ 把选定项的 `genre` / `platform` 传给 `init_book.py`。详见 SKILL.md §"项目初始化"。

## Output contract

- **不写真理文件**。
- **可写缓存**：`<workdir>/.radar-cache/<site>__<genre>__<rankingType>__{latest,YYYYMMDDHHmm}.json`。这是技术性 dedup 缓存，不是真理文件，不进 manifest，不参与 docOps。
- 输出形式：直接对话回复用户。
- 若用户决定立项，把 `genre` / `platform` / 简短的题材描述（写到 `templates/story/author_intent.md` 的占位里）作为入参传给 `scripts/init_book.py`。

## Failure handling

- **三档全空 + 默认策略**：不让 LLM 凭印象编。回用户："6 个站今天都没拉到数据（具体见 failures），方便贴一份你看到的榜单吗？"
- **三档全空 + `--allow-knowledge-fallback`**：仅当用户显式同意（"我懒得贴，你先按你的知识给"）才在 prompt 里加注 "未能获取到实时排行数据，请基于你的知识分析"，让 LLM 凭训练记忆给推荐。明确告知用户"这一份基于训练数据可能滞后"。
- **数据不一致**（书名收藏数对不上）→ 标出怀疑点，让用户复核，不强行分析。
- **用户给的题材限制太死**（"我只写修仙脑洞"）→ 不必强推榜单趋势，按用户既定方向给 1 个推荐 + 1 个略微偏离的对照即可。
- **adapter crash**（不应发生但兜底）→ 把 site 强塞进 `pendingWebFetch[]`，让 Stage B 接力。

## 注意事项

- **本 phase 不是写作主循环的一部分**。它产出的"题材+定位"是 init 流的输入，不是 plan / write 的输入。
- inkos 的 daemon 模式（`inkos up` 自动每 6 小时跑一次 radar）在 SKILL 形态里通过 `/schedule` 起 routine 复刻：
  ```
  /schedule create --interval 6h --command 'python scripts/radar_fetch.py scan --sites all --top 10 --format markdown'
  ```
  累计的 markdown 报告由用户自己消费，不自动落 `radar/scan-*.json`。
- **抓取礼貌**：单 host 1s 限速（`scripts/radar/_http.py`），UA 池轮换，不带 cookies，不绕 robots。这套阈值是"低频读取公开榜单"形态，不是爬虫。
- **缓存 TTL**：默认 6h；用户说"现抓最新"或 `--no-cache` 强抓。
- **新增 site**：参考 [radar-sources.md](../radar-sources.md) "适配器约束"小节 + `scripts/radar/__init__.py:SOURCES` 注册。
