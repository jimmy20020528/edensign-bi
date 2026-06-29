# Edensign BI — 遇到的问题记录

> 跑 classify_styles / detail_scrape 过程中暴露的坑。短记,不重复 PROGRESS.md。

最后更新: 2026-05-01

---

## P10. Python 3.9 跟 HomeHarvest 不兼容,主 venv 升 3.12

**表现**: `pip install homeharvest` 装上后 `import homeharvest` 报:
```
TypeError: unsupported operand type(s) for |: 'types.GenericAlias' and 'NoneType'
```

**根因**: HomeHarvest 0.8.18 内部用 `list[X] | None` 联合类型语法,Python 3.10+ 才支持。
`eval_type_backport` 只解决 type annotation,不解决函数签名里的运行时联合类型。

**解决**: 主 venv 升级到 Python 3.12:
```bash
brew install python@3.12
rm -rf .venv
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

升级后所有现有包(asyncpg/httpx/pydantic/google-genai 等)都跟 3.12 兼容,无 break。
顺手 statsmodels 0.14.4 → 0.14.6(兼容新 scipy),pandas 2.2 → 2.3。

详见 [requirements.txt](requirements.txt)。

---

## P9. Realtor.com 用 Kasada 反爬,curl/httpx 直跑必 429 — 但 HomeHarvest 能绕

**表现**: `curl https://www.realtor.com/...` 返回 HTTP 429 + Kasada bot challenge cookies(`KP_UIDz`),
HTML body 1800 字节 captcha shell。

**根因**: Realtor.com 用 Kasada Protocol SDK 反爬,需要在浏览器执行 JS challenge 才返 token。
比 Redfin (内部 GIS,正确参数即可) 和 Zillow (Akamai,IP 易封) 都狠。

### 为什么 HomeHarvest 能绕过 Kasada — 多前门架构

Realtor.com 不是"一个站",是**多个 front door,各自反爬等级不同**:

| Endpoint | 防护强度 | 谁用 | 直 curl 结果 |
|---|---|---|---|
| `www.realtor.com/realestateandhomes-search/...` | ⭐⭐⭐⭐ Kasada(JS challenge) | 浏览器用户 + 爬虫 | 429 captcha |
| `api.realtor.com/sites/v1/...` | ⭐⭐ 轻量(rate limit + auth) | 官方 iOS/Android app | 模拟 app headers 即可通 |
| `api.realtor.com/graphql` | ⭐⭐⭐ middle(API key + operation 校验) | web + app 后端共用 | 正确 query name + 凭证可通 |

**关键**:Realtor.com 不能给 mobile / GraphQL 端点上 Kasada,否则**他们自家 app 就废了**。
HomeHarvest 内部抓包了 Realtor.com app 的实际请求,复现 mobile/GraphQL endpoint
+ 模拟 app User-Agent / Apollo Operation Name 等 headers,**钻 mobile 端口的缝**。

### 类比

```
realtor.com 像有多个入口的银行:
  正门(www 公开页 + Kasada)      = 安检森严
  员工通道(mobile API)            = 刷卡进入(只要带"app 身份")
  后门(GraphQL)                   = 内部用

HomeHarvest 拿"我是 Realtor.com 自家 app"的卡刷员工通道。
curl 直接打 www = 从正门硬闯,被 Kasada 拦。
```

### 跟我们 Redfin 逆向的关系

跟 backend 工程师对 Redfin 做的事**完全同套路**:
- Redfin 也有 mobile/internal endpoint (`/stingray/api/gis` 等)
- 防护远弱于 www
- 只要传对参数 + headers 就能拉数据

### 风险

Realtor.com 任何时候可能给 mobile API 加 Kasada,届时 HomeHarvest 废。社区一年遇 1-2 次,
通常 1-2 周内有 patch。备用方案是 RapidAPI 第三方代爬(他们自己维护多个 bypass)。

### 解决

用 [HomeHarvest](https://github.com/ZacharyHampton/HomeHarvest)(GitHub 开源,免费):

```python
from homeharvest import scrape_property
df = scrape_property(location='02135', listing_type='sold', past_days=365)
# 返回 pandas DataFrame,字段含 mls_id 可跟 Redfin 直接 dedup
```

⚠️ 需要 Python 3.10+(库用了 `list[X] | None` 联合类型),见 P10。

不要尝试自己 bypass Kasada — 工程半天 + 维护噩梦,社区已经做得够好了。

---

## P8. Zillow IP 被 Akamai 封(rate limit 24-48h)

**表现**: 早期 curl 从一台机器多次抓 zillow.com 后,所有请求返 403 + "Access denied" 页。
HTML 5832 字节,无 listings 数据,session 期内不解封。

**根因**: Zillow 用 Akamai Bot Manager,检测到同 IP 高频请求(scraper 模式)后封 IP。

**解决**:
- 短期: 等 24-48h IP reputation 自动恢复
- 中期: 用 residential proxy 池(付费)+ Playwright stealth
- 长期: 跳过 Zillow,用 HomeHarvest 拿 Realtor.com 数据(覆盖类似)

**结论**: 我们当前不依赖 Zillow,绕开。

---

## P7. Decision BI API 缓存 training 映射与模型路径（需重启）

**表现**: 更新 `data/derived/training_*.parquet` 或新增 `models/baseline/log_{psf,dom}_ridge_*` 目录后，已运行的 FastAPI 进程仍可能使用旧的 `primary_style → style_g` 映射或旧的「最新」模型目录。

**根因**: `app/services/zipcode_analyzer.py` 中 `_load_training_parquet_path`、`_primary_style_to_style_g`、`_load_model_bundle` 使用 **`functools.lru_cache`**，进程内只解析一次。

**建议**: 部署新 artifact 后 **重启 uvicorn**（或 Phase 2 改为显式版本号 / 启动时加载并去掉无限期缓存）。**本次 standby 不改代码**，仅记录。

---

## P1. Gemini API 间歇性 503 UNAVAILABLE

**表现**: classify_styles 跑 smoke test 时多条 503 失败:

```
✗ Gemini 调用失败: 503 UNAVAILABLE. 'This model is currently experiencing
high demand. Spikes in demand are usually temporary. Please try again later.'
```

**根因**: `gemini-2.5-pro` 在高峰时段限流。当前脚本无重试逻辑,一次失败 = 这条永远失败。

**解决**: 加指数退避重试(3 次,初始 4s,翻倍到 8s/16s)。已修在 [classify_styles.py](scripts/classify_styles.py)。

---

## P2. 只看前 8 张照片 → 分类不代表真实情况

**典型 case**: `rf_73424698` (6 Sutherland Rd #41, Brighton)

- Redfin 详情页有 **30 张照片**
- 前 10 张全是 **empty room**(空房展示空间)
- 11-30 张是 **furnished/staged**
- 我们 `MAX_PHOTOS_PER_LISTING = 8` 只看了前 8 张全空房 → 判 `EmptyRoom (conf=0.99)`

**实际正确分类**: 这条房子很可能是某个具体风格(从后 20 张能看出),`EmptyRoom` 是抽样偏差导致的误判。

**根因**: 顺序取前 N 不能反映完整 listing。地产 listing 常见排版:
- 前几张: hero shot / empty room highlight
- 中间: room-by-room walkthrough
- 末尾: 平面图 / 社区配套

**解决**: 改为**分层抽样**(stratified),从全 30 张里**等间隔取 8-12 张**,覆盖整个序列。已修在 [classify_styles.py](scripts/classify_styles.py)。

**遗留**: 已经分类的 168 条 + 已经迁出 Unclassified 的 1 条 EmptyRoom 都是用"前 8 张"采样规则做的,可能有同类偏差。**Phase 2 全量重分类时可一并修复**(成本 ~$5)。

---

## P3. Public-record only listings 没有 interior 照片

**典型 case**: `rf_114185` (1633 Commonwealth Ave #12)

- Redfin 详情页有 11 张照片,但 URL 全是 `system_files/media/...`(public record 兜底,非 MLS staging 上传)
- `previousListingPhotosCount: 0` — 老 sold 时的 MLS 照片没归档
- 我们数据库存的 1 张 + Redfin 当前的 11 张 都是建筑外观 / 街景 / 公开记录照
- 无 interior → Gemini 分不出风格 → 一直被判 Unclassified

**根因**: Redfin 对老 sold listing 在 MLS 撤下后用 public record 替代,这种 listing 在 BI 里**无 staging 信号可用**。

**解决**: clean_outliers.py 加 `data_quality_flag = 'no_interior_photos'` 标志(URL 里 ≥80% 含 `system_files/media/` 时打)。Step 7 OLS 时排除这类行。

---

## P11. data_quality_flag 扩展为 7 类，避免 clean 混入脏数据

**表现**: 审计发现 `clean` 内混入多类脏数据：
- `cross_period` 漏刷新（抓数后没重跑 clean_outliers）
- `rental_leakage` 漏掉 `sqft IS NULL` 的低价租金行
- 极端 `sqft` 错值（如 1 / 27000）仍留在 clean
- `realtor` 孤儿行（`canonical_id IS NULL AND list_price IS NULL`）仍为 clean

**解决**: `clean_outliers.py` 规则扩容并重排优先级：
1) `no_interior_photos`
2) `bad_sqft`（新增）
3) `rental_leakage`（扩 sold_price<50000）
4) `realtor_orphan`（新增）
5) `cross_period`
6) `active_only`
7) `list_eq_sold`
8) `clean`

**训练口径**:
- Hard-exclude: `no_interior_photos`, `bad_sqft`, `rental_leakage`, `realtor_orphan`
- Informational: `cross_period`, `active_only`, `list_eq_sold`

**运维要求**: 每次抓数后必须重跑 `python scripts/clean_outliers.py`（已写入 RUNBOOK §6 增量刷新流程）。

---

## P4. Redfin GIS API 大幅退化(2026-04 起)

**表现**:
- `poly` 参数被静默忽略(不同 polygon 返回同样 350 条)
- `page_number` 失效(每页都是同一批)
- `sold_within_days` 也被忽略
- `photos` 字段从 GIS 响应里消失了

**根因**: Redfin 端点限流 / 参数被 deprecate。

**解决**: 不再走 GIS API。改用搜索页 HTML 的 InitialContext 缓存(redfin_discover.py)+ 详情页 HTML 缓存(redfin_detail_scrape.py)抠数据。

---

## P5. Redfin 同一 listing 现在 active 但历史 sold 的字段错位

**表现**: 某些 listing 在 2024 sold,2026-04 又 active 重新挂牌:
- 库里 `sold_date = 2024-XX`(旧 GIS 抓到的)
- 详情页 `propertyHistoryInfo.events` 只有当前周期 Listed (2026-04),不显示 2024 sold
- detail_scrape 拿到的 `list_price / listed_date / DOM` 都是 2026 重新挂牌的,跟 sold_date 跨期

**解决**: detail_scrape 已加保护逻辑 — `WHEN sold_date IS NOT NULL AND new sold_date IS NULL THEN preserve`,跨期数据不污染同一行。clean_outliers 再标 `data_quality_flag = 'cross_period'` 兜底。

---

## P6. list_price === sold_price (Redfin GIS 老 bug)

**表现**: 原 29 条 redfin listing 全部 `ABS(list_price - sold_price) < 1`,sale_to_list_ratio 完全无意义。

**根因**: Redfin GIS API 对 sold listing 不返原始 listPrice 字段,我们 fallback 用了 sold_price。

**解决**:
- 短期: clean_outliers 标 `data_quality_flag = 'list_eq_sold'`,Step 7 排除 sale_to_list_ratio 这条线。
- 长期: detail_scrape 从 priceHistory 抠真 list_price(已实现,但 active 重挂的房抠到的是 2026 的,跨期问题见 P5)
