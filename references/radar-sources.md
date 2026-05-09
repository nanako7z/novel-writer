# Radar 站点源参考

`scripts/radar_fetch.py` 抓榜的 6 个适配器：实现路径、URL 形态、题材映射、可靠性档位、失效处置都在这一篇。新增 / 修改适配器先来这里维护，再改 `scripts/radar/<site>.py`。

## 可靠性档位

| 档位 | 含义 | 行为 |
|---|---|---|
| **A** | SDK / JSON API，无 HTML 解析 | 一旦端点存活就稳；失效靠 endpoint 变更 |
| **B** | 静态 HTML + 正则，未深度防爬 | 偶发 503 / 限流，多数时候能跑 |
| **C** | 静态 HTML + 正则，模板易变 | 半年级别可能正则失效；warning 会喊 |
| **D** | 动态渲染 / 强反爬 / 仅 app | 不在 std-lib 范围；只走 user-paste |

## 站点表

| site | platform | 档位 | 入口 URL（默认 `genre=all`） | 编码 | 字段完整度 | 失效兜底 |
|---|---|---|---|---|---|---|
| `fanqie` | 番茄小说 | **A** | `api-lf.fanqiesdk.com/api/novel/channel/homepage/rank/rank_list/v2/?aid=13&side_type=10\|13` | utf-8 (JSON) | title/author/category/book_id ✔ | endpoint 504 → WebFetch web 端 → user-paste |
| `qidian` | 起点中文网 | **D**<sup>2</sup> | `www.qidian.com/rank/`；分类榜 `rank.qidian.com/yuepiao/chn{N}/` | utf-8 | — | std-lib 触发反爬 challenge（202 + probe.js）；adapter 仍保留以记录 URL，实际跑空 → 必走 WebFetch / user-paste |
| `feilu` | 飞卢小说网 | C | `b.faloo.com/y_0_0_0_0_0_5_1.html`；分类 `b.faloo.com/l/{cate}/0/0/0/2/0/1.html` | utf-8 | title/url ✔；author 待补 | 正则 0 命中 → WebFetch → user-paste |
| `jjwxc` | 晋江文学城 | B | `www.jjwxc.net/topten.php?orderstr=7&t={t}` | **gb18030**（响应强制 gzip，需 `_http.py` 解压） | title/novelid ✔（从 `<a title>` + `data-recommendInfo.relationNovelid`） | 编码错或解析空 → WebFetch → user-paste |
| `zongheng` | 纵横中文网 | **D**<sup>3</sup> | `www.zongheng.com/rank/details.html?rt=1&d=2&i={i}` | utf-8 | — | Vue/Nuxt SPA：数据藏在 `window.__NUXT__=(function(a,b,...){return X}(args))` 压缩 IIFE 的位置参数里；schema 不稳，定制 parser 不划算 → 必走 WebFetch / user-paste |
| `sfacg` | SF轻小说 | C | `book.sfacg.com/Rank/?d=7&t={t}` | utf-8 | title/novelId ✔ | 正则 0 命中 → WebFetch → user-paste |

<sup>2</sup> qidian 实测 std-lib 拿到 `HTTP 202` + 209 字节 probe.js 反爬挑战页。adapter 不删，URL/题材 mapping 仍维护——一旦触发 WebFetch 兜底，渲染后的 HTML 仍能让 phase 01 Stage B 回灌缓存。

<sup>3</sup> zongheng 实测能拉到 26KB raw HTML 但其中 `<a href="//book.zongheng.com/book/...">` 形态完全不存在，书名 / bookId 都在 `window.__NUXT__` 函数闭包的位置参数里。adapter 不删，同样保留题材映射给 WebFetch 阶段用。

未实现 / D 档（仅 user-paste 通道；在 phase 01 让用户贴）：

- 七猫小说（app-first）
- QQ 阅读（强反爬）
- 番茄畅读（同 fanqie 不同库）
- 刺猬猫（部分内容需登录）

## 题材映射表

SKILL 内部 genre id 与各站题材 id 的对照。`radar_fetch.py scan --genre xianxia` 时按这张表选 URL；缺映射条目则回退到该站综合榜。

| SKILL genre | fanqie<sup>1</sup> | qidian chn | feilu cate | jjwxc t | zongheng i | sfacg t |
|---|---|---|---|---|---|---|
| `xianxia` | n/a | 1 | 2 | 6 | 4 | — |
| `xuanhuan` | n/a | 21 | 1 | 6 | 2 | 13 |
| `cultivation` | n/a | 1 | 2 | 6 | 4 | — |
| `urban` | n/a | 4 | 3 | 2 | 5 | — |
| `sci-fi` | n/a | 9 | 5 | — | 7 | 16 |
| `litrpg` | n/a | 8 | — | — | — | 22 |
| `isekai` | n/a | 22 | 6 | 6 | 3 | 11 |
| `horror` | n/a | 7 | — | 6 | — | 19 |
| `romantasy` | n/a | — | — | 2 | — | — |
| `cozy` | n/a | — | — | 2 | — | — |
| `progression` | n/a | — | — | — | — | — |
| `tower-climber` | n/a | — | — | — | — | — |
| `dungeon-core` | n/a | — | — | — | — | — |
| `system-apocalypse` | n/a | — | — | — | — | — |
| `other` | n/a | — | — | — | — | — |

<sup>1</sup> 番茄 SDK API 没有 per-genre 端点（只能拉 sideType=10/13 综合榜），LLM 在 phase 01 prompt 阶段从 `category` 字段二次过滤。

`—` 表示该站对应题材不显眼；适配器会回退到综合榜并 warn。

## URL 维护清单

每半年抽查一次（建议跟卷尾压缩同周期触发）：

```bash
python scripts/radar_fetch.py scan --sites all --no-cache --top 5 --format markdown
```

预期：6 个站 ≥ 4 个有 entries；有任一站 0 entries 且非网络问题，按下表操作：

- 番茄 SDK 404 / 5xx → 多半 endpoint 变了。打开 fanqie app 抓包替换 `api-lf.fanqiesdk.com` 路径，更新 `scripts/radar/fanqie.py:API_BASE`。
- 飞卢正则 0 命中 → 打开榜单页，看 `<a href="//b.faloo.com/<id>.html">` 是否变成新形态，改 `BOOK_LINK_RE`。
- 晋江解析空 → 三种可能：编码（GB18030 → UTF-8 切换）；gzip 解压（_http.py 已自动处理）；rank 页 markup 从 `data-recommendInfo` JSON 改成别的。先打开 raw HTML 看 200 个 `<a title="...">` 还在不在。
- SF 轻小说 0 命中 → 看 `<a href="http://book.sfacg.com/Novel/N/">` 形态有没有变。
- qidian 持续 D 档 → 目前没有 std-lib 出路，phase 01 Stage B 自动 WebFetch 接力；只有当 qidian 出 SDK / 公开 API 才有可能升档。
- zongheng 持续 D 档 → 若哪天纵横从 SPA 退回 SSR markup（`<a href="//book.zongheng.com/book/N.html">title</a>`），可加一条简单 regex 升档至 C；现状只能走 WebFetch。

## 适配器约束（写新 site 也照此办理）

1. 模块名 = site id，文件 `scripts/radar/<site>.py`，导出 `SOURCE = fetch`
2. `fetch(genre, top) -> PlatformRankings` 永远不 raise；网络/解析错落 `failures[]`
3. 暴露一个 pure `parse_html(...)`（或同等 parser），让 `radar_fetch.py self-test` 用 fixture 验
4. `_http.get` 默认走 UA 池 + 单 host 1s 限速，不要绕
5. 不携带 cookies / 不绕 robots / 不冒充搜索引擎爬虫
6. 在 `scripts/radar/__init__.py:SOURCES` 注册

## 与 inkos 的差异（设计回执）

| 方面 | inkos | SKILL |
|---|---|---|
| 内置站点数 | 2（fanqie + qidian） | 6（+ feilu / jjwxc / zongheng / sfacg） |
| 调度器 | `Promise.all` 并发 | 串行 + 1s 限速（更礼貌） |
| 空数据策略 | prompt 注 "用知识分析"，让 LLM 编 | 默认禁；`--allow-knowledge-fallback` 才开 |
| 结果落盘 | `<root>/radar/scan-<ts>.json` 永久累积 | `<workdir>/.radar-cache/...` 仅缓存（6h TTL） |
| 兜底通道 | 无（数据空就让 LLM 编） | 3 档递进：std-lib → WebFetch → user-paste |
| Adapter 注册 | TS class array | Python dict (`SOURCES`) |
