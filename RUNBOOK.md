# Edensign BI — 全流程 Runbook

> 怎么把整个 pipeline 从 0 跑通,以及日常维护怎么做。
> 假设工作目录是 `/Users/jimmy20020528/Desktop/Edensign/bi/`,所有命令在这里跑。

---

## 0. 一次性环境准备

### 0.1 Python venv — **必须 Python 3.12+**

⚠️ **不要用 Python 3.9 / 3.10 / 3.11**。homeharvest 依赖用 `list[X] | None` 联合语法,
   Python 3.10+ 才支持,实测 3.9 会 import error。建议直接 3.12。

```bash
# 装 Python 3.12(没装的话)
brew install python@3.12

cd /Users/jimmy20020528/Desktop/Edensign/bi
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

# 验证
python --version   # 期望 Python 3.12.x
```

### 0.2 .env 文件

`bi/.env` 必须有下列 key(参考 `.env.example`):

```
DB_PASSWORD=edensign_dev
GEMINI_API_KEY=<去 https://aistudio.google.com/app/apikey 申请>
WALKSCORE_API_KEY=<可选,Step 5 location 用>
FRED_API_KEY=<可选,Step 6 market 用>
RENTCAST_API_KEY=<可选,数据扩样备用>
RAPIDAPI_KEY=<可选,只在用 RapidAPI 第三方爬虫备份方案时需要>
```

### 0.3 启动 Docker Postgres

```bash
docker compose up -d
docker ps --filter name=edensign_bi_db
```

健康状态应该显示 `(healthy)`。**这条容器名就是 `edensign_bi_db`**,后面所有 SQL 命令通过它执行。

### 0.4 应用 schema

第一次跑(全新数据库):

```bash
docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < schema.sql
```

应用所有 migrations(已经存在的库):

```bash
for f in scripts/migrations/*.sql; do
  echo ">>> $f"
  docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < "$f"
done
```

---

## 1. 数据采集流水线(从空数据库到完整数据)

按顺序跑。每步可独立重跑,内部都有 ON CONFLICT 去重。

### 1.1 Step 2 — Census ACS 人口数据

```bash
python scripts/census_pull.py
```

输出: 235 个 Suffolk County tract 写入 `census_tracts` 表。**不需要 API key**。一次性,以后基本不用重跑。

### 1.2 Step 3a — Redfin 发现 listing(找 ID)

```bash
# 默认抓 02134+02135 × {sold-1yr, sold-3yr, sold-5yr, include=forsale}
python scripts/redfin_discover.py --all

# 单 ZIP smoke test(测脚本是否在 work)
python scripts/redfin_discover.py --zip 02134 --filter sold-1yr
```

输出: `listings` 表新增占位行(基础字段 + redfin_url),`photo_urls` 暂时为空。

### 1.3 Step 3b — Redfin 详情页抓(补 photos / priceHistory / DOM)

```bash
# 默认: 只抓 photo_urls=[] 的 listing(增量补)
python scripts/redfin_detail_scrape.py

# 只抓过去 1 年 sold + active 的 listing(George 限定的训练范围)
python scripts/redfin_detail_scrape.py --past-year-only

# smoke test
python scripts/redfin_detail_scrape.py --limit 3

# 全部 redfin listing 都重抓(刷新 priceHistory 用,贵)
python scripts/redfin_detail_scrape.py --all
```

并发 5,~2-3s/条,~100 条 listing 大概 1 分钟。

### 1.4 Step 3c — Realtor.com 数据(via HomeHarvest,免费)

补充 Redfin 数据。Redfin 主要走 MLS-PIN(Boston 地区 MLS),Realtor.com 数据源更广
(Realtor.com + 多 MLS + 公共记录),通常多 30-50% 不重叠 listings。

```bash
python scripts/realtor_pull.py --zip 02135
python scripts/realtor_pull.py --zip 02135 02134   # 多个 ZIP
```

⚠️ realtor.com 用 Kasada 反爬(比 Redfin/Zillow 都强),直接 curl 永远 429。
   解决方案: HomeHarvest(GitHub: ZacharyHampton/HomeHarvest)逆向了 realtor 内部 mobile API 钻缝,
   pip 一装就能用。不收费,无 API key。

### 1.5 Step 5 — Location 因子(walk score / 公交 / amenity)

```bash
python scripts/fetch_location_scores.py
```

需要 `WALK_SCORE_API_KEY`。Overpass(OSM)免费。

### 1.6 Step 6 — Market snapshot(房贷利率)

```bash
python scripts/fetch_market_snapshot.py
```

需要 `FRED_API_KEY`。每周日跑一次足够。

---

## 2. 风格分类(Gemini VLM)

### 2.1 Step 4 — 主分类(只跑没分过的)

```bash
# 默认: 全部没分过的 listing(可能跨多年)
python scripts/classify_styles.py

# 只分过去 1 年(George 限定的 BI 训练范围,推荐)
python scripts/classify_styles.py --past-year-only
```

只跑 `style_classifications` 里**还没记录**的 listing。每条 ~$0.025,~10s/条。
新进 100 条 listing 大概 ~$2.5 + 30 分钟。

加 `--past-year-only` 限制到 sold_date >= CURRENT_DATE - 365 天 OR sold_date IS NULL,
只覆盖训练相关数据,省 80% Gemini 预算。

---

## 3. 分析

### 3.1 Step 7 — OLS 回归 smoke test

```bash
python scripts/run_step7_analysis.py
```

输出 3 个模型(log_price / log_psf / log_dom),控制 sqft/beds/baths/year_built/walk/transit/amenity,看 style dummy 系数显著性。

### 3.2 启动 API

```bash
uvicorn app.main:app --port 8000
```

→ Swagger UI: http://localhost:8000/docs

```bash
# curl 测试
curl -s 'http://localhost:8000/analyze/by-zipcode?zipcode=02135&objective=balanced' | python3 -m json.tool
curl -s 'http://localhost:8000/analyze/by-zipcode?zipcode=02135&objective=fast'     | python3 -m json.tool
curl -s 'http://localhost:8000/analyze/by-zipcode?zipcode=02135&objective=price'    | python3 -m json.tool
curl -s 'http://localhost:8000/analyze/by-zipcode?zipcode=02134&objective=balanced' | python3 -m json.tool
```

Health check:
```bash
curl http://localhost:8000/health
```

---

## 4. 常用诊断 SQL

所有都通过 `docker exec edensign_bi_db psql -U edensign -d edensign_bi -c "SQL"` 执行。
为了简洁,下面只列 SQL,前缀省略。

### 4.1 总数据量

```sql
SELECT
  source,
  COUNT(*) total,
  COUNT(*) FILTER (WHERE jsonb_array_length(photo_urls) > 0) with_photos,
  COUNT(*) FILTER (WHERE list_price IS NOT NULL) with_list,
  COUNT(*) FILTER (WHERE listed_date IS NOT NULL) with_listed,
  COUNT(*) FILTER (WHERE sold_date IS NOT NULL) with_sold,
  COUNT(*) FILTER (WHERE days_on_market IS NOT NULL AND days_on_market > 1) with_real_dom
FROM listings GROUP BY source;
```

### 4.2 风格分布

```sql
SELECT primary_style, COUNT(*)
FROM style_classifications
GROUP BY 1 ORDER BY 2 DESC;
```

### 4.3 ZIP-style 交叉表

```sql
SELECT zipcode, primary_style, COUNT(*) n
FROM listing_full
WHERE primary_style IS NOT NULL
GROUP BY 1,2 ORDER BY 1, 3 DESC;
```

### 4.4 找异常值(rental 漏入 / 价格离谱)

```sql
SELECT listing_id, address, list_price, sold_price, sqft, price_per_sqft
FROM listings
WHERE list_price < 50000 OR list_price > 5000000 OR price_per_sqft < 100
ORDER BY list_price;
```

### 4.5 找跨期数据污染(2024 sold + 2026 listed)

```sql
SELECT listing_id, listed_date, sold_date,
       (sold_date - listed_date) AS day_diff
FROM listings
WHERE listed_date IS NOT NULL AND sold_date IS NOT NULL
  AND listed_date > sold_date;
```

### 4.6 抓得不全的 listing(可能要重抓)

```sql
SELECT listing_id, jsonb_array_length(photo_urls) AS n
FROM listings
WHERE source = 'redfin' AND jsonb_array_length(photo_urls) < 8
ORDER BY n;
```

强制重抓某条 listing:

```sql
UPDATE listings SET photo_urls = '[]'::jsonb WHERE listing_id = 'rf_xxx';
-- 然后跑 python scripts/redfin_detail_scrape.py
```

---

## 5. 完整冷启动顺序(从空库到 demo-ready)

按这个顺序跑,~1.5 小时:

```bash
# === 0. 准备 ===
docker compose up -d
docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < schema.sql
for f in scripts/migrations/*.sql; do
  docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < "$f"
done
source .venv/bin/activate

# === 1. 数据采集(40 分钟) ===
python scripts/census_pull.py                              # 5 min
python scripts/redfin_scrape.py --zip 02135 02134          # 5 min
python scripts/realtor_pull.py --zip 02135 02134           # 5 min
python scripts/fetch_location_scores.py                    # 15 min(API 较慢)
python scripts/fetch_market_snapshot.py                    # 30s

# === 2. VLM 分类(40 分钟,$5-6) ===
python scripts/classify_styles.py

# === 3. 验证 ===
docker exec edensign_bi_db psql -U edensign -d edensign_bi -c "
SELECT COUNT(*), COUNT(*) FILTER (WHERE jsonb_array_length(photo_urls) > 0) AS with_photos
FROM listings;"

# === 4. 训练模型 ===
python scripts/build_training_dataset.py --min-sold-date 2025-05-01
python scripts/train_baseline_models.py

# === 5. 起服务 ===
uvicorn app.main:app --port 8000               # → http://localhost:8000/ui/
```

---

## 6. 常规增量刷新(每周一次,~10 分钟)

```bash
source .venv/bin/activate

# 1. 抓新 listing
python scripts/redfin_scrape.py --zip 02135 02134
python scripts/realtor_pull.py --zip 02135 02134

# 2. 刷新数据质量 flags（必须）
python scripts/clean_outliers.py

# 3. 给新照片分类(自动跳过已分类)
python scripts/classify_styles.py

# 4. 刷新 market 数据
python scripts/fetch_market_snapshot.py

# 5. 重训模型(有新数据时)
python scripts/build_training_dataset.py --min-sold-date 2025-05-01
python scripts/train_baseline_models.py

# 6. 重启 API(加载新模型)
# uvicorn 不会热加载 .pkl 文件,需要 kill 然后重起
```

API 服务热加载,数据库 commit 后 API 立刻能看到新数据。

---

## 7. 故障排除

### 7.1 Postgres 连不上

```bash
docker ps --filter name=edensign_bi_db   # 应该 (healthy)
docker compose up -d                     # 没起就启动
docker compose logs db | tail -50        # 看错误
```

### 7.2 Gemini 503 / 限流

`classify_styles.py` 已加指数退避重试(503/429/RESOURCE_EXHAUSTED 等 4s→8s→16s)。仍失败的 listing 直接重跑脚本即可,会自动跳过已成功的。

### 7.3 Redfin scraper 拿到 0 条

- HTML 页可能改了 cache key 名,看 `/tmp/redfin_zip_*.html` 落盘 grep 找新 key
- 或者 IP 被 Cloudflare 短期挡,等 30 分钟再试

### 7.4 风格分类 all 'Unclassified'

- 看具体 listing 的 photo_urls 是不是只有 1 张外观图(参考 ISSUES.md P3)
- 或者照片下载失败(网络问题 + Redfin CDN 偶发 403)

### 7.5 把数据库整个清空重来

⚠️ 不可逆,只在你确定要从头开始时用:

```bash
docker compose down -v       # -v 删 volume,数据一并清
docker compose up -d
docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < schema.sql
for f in scripts/migrations/*.sql; do
  docker exec -i edensign_bi_db psql -U edensign -d edensign_bi < "$f"
done
# 然后从 § 1 开始跑
```

---

## 文件索引(快速查)

```
bi/
├── docker-compose.yml          # Postgres 容器
├── schema.sql                  # 5 张主表 + style 视图
├── requirements.txt            # Python 依赖(3.12 + scipy/statsmodels 兼容版)
├── .env / .env.example         # API keys
│
├── PROGRESS.md                 # 阶段性进展记录(追加式)
├── ISSUES.md                   # 10+ 个已知坑 + 解决思路
├── DECISION_BI_PLAN.md         # 产品 + 架构愿景
├── FACTORS.md                  # 14 入模 factor + 30 schema 未用 + 4 计划未采集
├── PRESENTATION.md             # 给 George 第二次 demo 的稿子(电梯版 + 5-7 min 正式版)
├── RUNBOOK.md                  # 你正在看的这个
│
├── config/
│   ├── acs_variables.json      # Census 25 个人口学变量
│   └── style_taxonomy.json     # 23 个风格 (含 EmptyRoom + Lived-in) + 9 个子属性
│
├── scripts/
│   ├── db_dsn.py               # 数据库连接工具
│   │
│   ├── census_pull.py          # Step 2 - 人口数据
│   ├── redfin_discover.py      # Step 3a - Redfin GIS 翻页找 listing ID
│   ├── redfin_detail_scrape.py # Step 3b - Redfin 详情页补 photos + priceHistory
│   ├── realtor_pull.py         # Step 3c - Realtor.com 数据 (HomeHarvest 路径)
│   ├── rentcast_pull.py        # 备用扩样数据源 (RentCast,需付费)
│   ├── zillow_pull.py          # Zillow scraper (Akamai 易封,慎用)
│   ├── redfin_scrape.py        # Main Redfin GIS scraper — use --zip to target any US ZIP
│   │
│   ├── classify_styles.py      # Step 4 - Gemini 主分类(支持 --past-year-only)
│   │
│   ├── fetch_location_scores.py # Step 5 - Walk/Transit/amenity
│   ├── fetch_market_snapshot.py # Step 6 - FRED 利率
│   │
│   ├── clean_outliers.py       # Phase 1 Task 0 - 数据质量 flag
│   ├── run_step7_analysis.py   # Step 7 / Task 0.5 - OLS smoke test
│   ├── build_training_dataset.py # Phase 1 Task 1 - 训练集 parquet
│   ├── train_baseline_models.py # Phase 1 Task 2 - Ridge/Lasso/OLS + LOO-CV
│   │
│   └── migrations/
│       ├── 001_add_emptyroom_and_livedin.sql      # primary_style 加 EmptyRoom + Lived-in
│       ├── 002_add_reasoning_column.sql           # style_classifications 加 reasoning
│       ├── 003_add_zillow_url_and_canonical_id.sql # 跨源 dedup 准备
│       └── 004_add_realtor_url.sql                # Realtor 数据 url 列
│
├── data/
│   └── derived/                # build_training_dataset 产物
│       └── training_<时间戳>.parquet
│
├── models/
│   └── baseline/               # train_baseline_models 产物(并存,不覆盖)
│       ├── log_psf_ridge_<时间戳>/
│       │   ├── model.pkl       # Ridge/Lasso/OLS 三模型 + scaler + best_model_name
│       │   ├── eval.json       # MAPE/MAE/RMSE + 系数表
│       │   └── eval.md         # 人读版报告
│       ├── log_dom_ridge_<时间戳>/
│       └── ...
│
└── app/
    ├── main.py                 # FastAPI 入口 + /analyze/by-zipcode
    └── services/
        └── zipcode_analyzer.py # ZIP 推荐评分,scoring_mode=heuristic|model|hybrid
```

## 数据源对照(更新 2026-05-01)

| Source | 字段前缀 | 接入路径 | 反爬等级 | 当前状态 |
|---|---|---|---|---|
| Redfin (MLS-PIN base) | `rf_<id>` | redfin_discover.py + redfin_detail_scrape.py | ⭐ 弱(走内部 /stingray/api/gis) | ✅ 主源,2518 条 |
| Realtor.com | `rt_<id>` | realtor_pull.py via HomeHarvest | ⭐⭐⭐⭐ 强(Kasada,但 HomeHarvest 走 mobile API 钻缝) | ⏳ 待上线 |
| Zillow | `zw_<id>` | zillow_pull.py(直接 HTML) | ⭐⭐⭐ 中(Akamai,IP 易封) | ⚠ 当前 IP 被封,搁置 |
| RentCast | (rentcast 前缀) | rentcast_pull.py(API,$74/月) | n/a | ⏸ 备用,未启用 |

### 关于"反爬等级"和我们怎么绕

每个房产网站有**多个 endpoint**,反爬强度不一:
- 公开 web 页(给浏览器用):反爬通常最强(Kasada / Akamai / Cloudflare)
- Mobile API(给 app 用):反爬较弱,因为放强了自家 app 就废
- 内部 GraphQL(后端共用):中等

**Redfin / Realtor 我们都走 mobile / 内部 API 端口,绕过 web 公开页的反爬**。
Zillow 一致全覆盖反爬,所以 Zillow 没好的开源 scraper。

详细原理 + 类比见 [ISSUES.md P9](ISSUES.md)。

跨源 dedup 用 `listings.canonical_id`(从 mls_id 跨源关联,e.g. Redfin 的 `rf_73473365`
跟 Realtor 的 `rt_xxx` 共享 mls_id 73473365 → 同房,共 canonical_id)。
