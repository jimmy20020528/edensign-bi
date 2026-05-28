# Edensign BI — 因子清单(Factor Inventory)

> 数据库收集了什么 / 哪些进了 Phase 1 模型 / 哪些预备但未用 / 各自怎么用。
> 最后更新: 2026-05-01

---

## 总览

```
进 Phase 1 训练:    14 个 features + 2 个 target
进 schema 但未用:   30 个 columns(各种原因)
计划但未采集:        4 类(Phase 2 todo)
```

---

## ✅ Phase 1 模型实际用的因子(14 + 2 target)

### 输入 features

| 因子 | 类型 | 数据源 | 怎么用 |
|---|---|---|---|
| **sqft** | int | `listings.sqft`(Redfin) | 连续 feature,StandardScaler 归一化 |
| **bedrooms** | smallint | `listings.bedrooms`(Redfin) | 连续 feature |
| **bathrooms** | real | `listings.bathrooms`(Redfin) | 连续 feature |
| **year_built** | smallint | `listings.year_built`(Redfin) | 连续 feature(房龄信号) |
| **walk_score** | smallint | `location_scores.walk_score`(Walk Score API) | 0-100,连续 feature |
| **transit_score** | smallint | `location_scores.transit_score`(Walk Score API) | 0-100,连续 feature |
| **amenity_count_1km** | smallint | `location_scores.amenity_count_1km`(Overpass/OSM) | 1km 内 amenity 数量,连续 feature |
| **median_income** | int | `census_tracts.median_income`(Census ACS,JOIN by tract_id) | tract 中位收入 $/年,连续 feature。**控制"高收入区房价本来就贵"混淆** |
| **months_since_2022_q1** | derived | 从 `sold_date` 计算 | 时间漂移线性控制项 |
| **months_since_2022_q1_sq** | derived | 上面平方 | 时间漂移二次控制项(捕捉非线性) |
| **dominant_archetype** | categorical(6 类) | `census_tracts.dominant_archetype`(Census 派生) | one-hot 5 个 dummy,baseline=mixed。**控制"年轻专业 vs 高收入 vs 学生区"买家画像差异** |
| **primary_style** | categorical(20+) | `style_classifications.primary_style`(Gemini VLM) | one-hot,baseline=EmptyRoom。**核心信号** |
| (style_g 派生) | categorical(<10) | 把稀有风格(<3 行)合并到 Other | 真实进入回归的风格 dummy |

### Target(被预测的)

| 因子 | 怎么用 |
|---|---|
| **price_per_sqft** | log 后做 `log_psf` target — Stage A 主模型 |
| **days_on_market** | log 后做 `log_dom` target — Stage A 副模型(弱信号) |

---

## 📦 Schema 里收集了但 Phase 1 没用的(30 个)

### Property 维度(没用 4 个)

| 因子 | 数据源 | 为啥没用 |
|---|---|---|
| `lot_size` | Redfin | condo 主市场 lot_size 多为 0 或 NULL,信号弱 |
| `hoa_fee` | Redfin | 大量 NULL(详情页才有),Phase 1 没纳入 |
| `parking` | Redfin | 高度 NULL,Allston 有车位的特征噪声 |
| `property_type` | Redfin | 跟其它特征(sqft/beds)高共线性,先不用避免 multicollinearity |

### Location 维度(没用 6 个)

| 因子 | 数据源 | 为啥没用 |
|---|---|---|
| `bike_score` | Walk Score API | 跟 walk_score 高度相关(~0.85),冗余 |
| `school_rating_avg` | GreatSchools(未接) | **数据未采集**,GreatSchools API key 还没申请 |
| `crime_rate_per_1000` | Boston Police 开放数据(未接) | **数据未采集**,需写专门的 ingestion |
| `nearest_transit_m` | Overpass/OSM | 已采集,但跟 transit_score 高度相关,冗余 |
| `nearest_park_m` | Overpass/OSM | 已采集,信号弱(Allston 公园密度均匀) |
| `noise_level_db` | 噪声 API(未接) | **数据未采集**,Phase 2 |
| `flood_zone` | FEMA NFHL | **API 失效**(layer 28 返 404,记录在 ISSUES P3 备选) |

### Demographics 维度(没用 12 个 census 字段)

`census_tracts` 有 25 个字段,Phase 1 只用了 `median_income` + `dominant_archetype`。其余:

| 因子 | 含义 | 为啥没用 |
|---|---|---|
| `median_age` | tract 中位年龄 | 跟 dominant_archetype 高度相关(young_professional → 低龄) |
| `pct_families_children` | 有未成年子女家庭比例 | 同上,被 archetype 概括 |
| `pct_owner_occupied` | 自住率(vs 租房) | 跟 student_budget archetype 高相关 |
| `pct_bachelors_plus` | 本科+学历比例 | 跟 median_income 强共线 |
| `pct_white/black/asian/hispanic` | 族裔比例 | 跟 archetype 共线;Allston 主流是 mixed,信号弱 |
| `population_density` | 人口密度 | 跟 walk_score 共线 |
| `avg_household_size` | 平均户均 | 信号弱 |
| `median_home_value` | tract 中位房屋估值 | **跟 sold_price 同源,有数据泄漏风险**,绝不能进训练 |
| `pct_transit_commute` | 公交通勤比例 | 跟 transit_score 共线 |
| `pct_bike_walk_commute` | 骑行/步行通勤 | 跟 walk_score 共线 |
| `pct_english_only` | 只英语家庭 | 跟 archetype 共线 |
| `median_rent` | 中位月租 | 跟 median_home_value 共线 |
| `total_population` | tract 总人口 | 信号弱(Allston tract 大小相近) |
| `total_households` | 总户数 | 同上 |

**核心规则**: dominant_archetype 已经是这 12 个字段的"语义压缩",**直接用 archetype 优于把 12 个全塞进去导致共线性**。Phase 2 可考虑用 PCA / FactorAnalysis 做人口因子降维。

### Market 维度(没用 8 个,只采了 2 个有效)

| 因子 | 数据源 | 状态 |
|---|---|---|
| `mortgage_rate_30yr` | FRED MORTGAGE30US | ✓ 已采(每周日刷新),Phase 1 没纳入(全国统一,跨 ZIP 无差异) |
| `avg_close_days` | listings 全量聚合 | ✓ 已计算(5.4 天,Boston 火爆市场),作 metadata 不入回归 |
| `price_reduction_pct` | 派生 | **0%,数据废**(因 list_eq_sold bug,记 ISSUES.md P6) |
| `active_inventory` | Redfin | 未采(需 active listing 总数估算) |
| `months_of_supply` | 派生 | 未采(=inventory / 月成交) |
| `absorption_rate` | 派生 | 未采 |
| `yoy_price_change` | 派生 | 未采(需用同 ZIP 历史均价对比) |
| `seasonality_index` | 派生 | 未采 |

**Phase 1 不用 Market 因子的核心原因**:Allston/Brighton 单 ZIP 训练,市场状况对所有 listing 一致,跨 listing 无变异,**进回归会被吸收到截距里**,等于浪费 dummy。Phase 2 扩到多 ZIP / 跨时间训练才有意义。

### Style 子属性维度(没用 8 个 sub-attributes)

`style_classifications` 表里 Gemini 输出了 8 个子属性,Phase 1 只用 primary_style:

| 子属性 | 取值 | 为啥没用 |
|---|---|---|
| `color_tone` | warm / cool / neutral | Phase 1 简化,只用 primary_style 一个风格信号 |
| `price_feel` | budget / mid / luxury | **跟 sqft + median_income 强共线**(贵区贵房自然 luxury feel) |
| `furniture_density` | sparse / moderate / dense | 跟 EmptyRoom / Lived-in / pro-staged 强共线 |
| `natural_light` | low / medium / high | 噪声多(照片光线≠真实采光) |
| `renovation_level` | original / partial / full | **可能有信号**,Phase 2 可以加 |
| `floor_plan` | open / semi-open / closed | 信号有限,跟 year_built 部分共线(新房多 open) |
| `kitchen_style` | traditional / transitional / modern | 跟 primary_style 部分共线 |
| `bathroom_finish` | basic / updated / luxury | 跟 price_feel 强共线 |

**Phase 2 推荐加进来的**: `renovation_level` 是 "翻新程度" 信号,跟 year_built 互补(老房翻新过 vs 老房没翻新),可能贡献独立信号。

---

## ⏳ 计划但未采集的因子(Phase 2 todo)

| 因子 | 数据源 | 为啥重要 |
|---|---|---|
| **GreatSchools 学区评分** | greatschools.org API | 美国房产价格头部驱动因子之一,我们没接 |
| **Boston Police 犯罪率** | data.boston.gov 开放数据 | 区位安全度,跟 PSF 强相关 |
| **FEMA Flood Zone** | NFHL layer | 海岸/河边特别敏感,Allston 影响小但扩到滨海要 |
| **Buyer click data (CTR)** | 自家 listing 平台埋 pixel | George 想要的"证明 staging 真有效"的 ground truth,Zillow 不开放,需自建 |

---

## 🔥 排除 / 禁用的因子

| 因子 | 排除理由 |
|---|---|
| `sale_to_list_ratio` | list_price === sold_price 老 GIS bug 导致全部 1.0,无信号(ISSUES.md P6) |
| `listed_date` 派生的 DOM | 大量 cross_period 污染(2024 sold + 2026 active 重挂),改用 `days_on_market` 字段(detail_scrape 从 priceHistory 抠) |
| `median_home_value` (Census) | **跟 target 同源,会数据泄漏**,绝不能进训练 |
| `price_reduction_pct` | 全 0,无信号 |

---

## 📊 因子在 BI 输出里怎么对应

```
API 输出 evidence.top_drivers 字段:
  [
    {feature: "walk_score",       contribution_pct: +8.2},
    {feature: "sqft",             contribution_pct: -3.1},
    {feature: "style_Modern Min", contribution_pct: +12.5}
  ]

每个 feature 的 contribution = coefficient × feature_value
排序按 |contribution|,取 Top 3
"+%": 该因子在这 ZIP 让 PSF 高 X%
"-%": 该因子让 PSF 低 X%
```

---

## 🎯 给 George demo 时的 talking points

```
"Phase 1 模型用了 14 个 features,涵盖 5 个维度:
   - 房屋本身 4 (sqft, beds, baths, year_built)
   - 区位 3 (walk, transit, amenity)
   - 人口 2 (median_income, dominant_archetype)
   - 时间漂移控制 2 (months_since_2022 + 二次)
   - staging 风格 1(20+ 类 dummy)
   
 Schema 里还有 30+ 字段没用,因为多重共线 / 数据未采 /  数据质量问题。
 Phase 2 重点采:学区评分、犯罪率、买家点击数据。
 Schema 里**已经留了字段**,采集到就能直接进模型,不用改架构。"
```
