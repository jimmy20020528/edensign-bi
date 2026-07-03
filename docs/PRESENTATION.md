# Edensign BI — Phase 1 进度汇报稿

> 给 George 第二次 meet 时讲。两个版本:30 秒电梯版 + 5-7 分钟正式版。
> **最后更新: 2026-05-01,反映 v2 production model 状态**。

---

## 0. 30 秒电梯版(进会议室前对自己念一遍)

> Phase 1 完成。我把 BI 从"看历史中位数"升级到"模型反事实预测",**v2 已上 production**。
>
> 数据库 **2518 条 Allston/Brighton listings** (5 年内 Redfin sold + active),
> 过去 1 年 478 条带照片 + 风格分类(George 限定的训练范围)。
> 训了两个模型:**单价模型 MAPE 13.15%**(对标 Zillow off-market 7%),
> **DOM 模型 MAPE 63.75%**(从 v1 的 88% 大幅改善,得益于 reclassify + past-year cutoff)。
>
> 现产品形态 — API 三种模式: heuristic / model / hybrid,前端切换演示。
> 模型 mode 带 evidence 解释 + warnings 提示置信度,是工程级输出。
>
> 你给的 Zillow Preview 方向 — 5 个诉求里 4 个数据已经覆盖,
> 缺的只是 buyer click data,Zillow 不开放。下一步可以做 `/buyer-profile`、`/listing-comps`、
> `/predict-price` 三个 endpoint 直接对应 Zillow Preview。
>
> 数据扩样下一步: HomeHarvest 接入 Realtor.com,再加 1500-3000 条数据,MAPE 期望 8-10%。

---

## 1. 正式版讲稿(5-7 分钟,按这个顺序讲)

### 第 1 段:做了什么(Phase 1 成果)

> 上次 demo 之后,我做了 4 件事:
>
> **第一,数据扩样**。从原来的 213 条扩到 **612 条** Allston + Brighton 已售 listing。
> 通过多维度切片(价格 × 房型 × 时间窗)突破 Redfin 单 ZIP 显示上限。
> 全部 612 条都有完整照片 + priceHistory + DOM。
>
> **第二,风格分类升级**。原来只有 21 个风格 + Unclassified,现在加了两个关键类别:
> - **EmptyRoom**(空房,vacant baseline)
> - **Lived-in**(业主自住,非专业 staging)
>
> 加 Lived-in 是关键决策。重新分类后发现 **168 条(28%)是 Lived-in**,
> 这意味着原来的 80 条 Transitional 里至少一半是误标的业主自住。
> 没拆开的话,模型会把"业主自家家具"和"专业 staging"混在一起,严重污染风格效应估计。
>
> **第三,训练 BI 模型**。两个 target:
> - log_psf(单价对数)— **MAPE 13.3%**
> - log_dom(在市天数对数)— MAPE 88%
>
> 用 Lasso 算法,LOO-CV 评估(留一交叉验证,503 条数据每条都被 hold out 测过)。
> 模型自动告诉我们哪些风格相对空房有正向效应:
> - **Modern Minimalist 比空房高 16% 单价**
> - **Scandinavian 比空房高 15%**
> - **Contemporary 比空房高 9%**
>
> 这些都是 p<0.05 显著结果,不是描述性中位数,是控制了 sqft / 区位 / 房型后的纯净风格效应。
>
> **第四,API 升级**。`/analyze/by-zipcode` 现在支持三种 scoring_mode:
> - `heuristic`(默认):老 MVP,看历史中位数,完全向后兼容
> - `model`:模型反事实预测,对每个候选风格计算"装这个能多卖多少"
> - `hybrid`:50/50 加权
>
> 模型模式带 **evidence block**,告诉 API 用户每条推荐背后的 Top 3 driver:
> 比如 "Modern Minimalist 排第一,因为 walk_score 贡献 +8%、
> sqft 贡献 -3%、风格本身贡献 +12%"。这是工程级输出,可以直接挂 dashboard。

### 第 2 段:跟 industry 对标 — 诚实版

> 单价模型 MAPE 13% 跟 Zillow / Redfin 对比:
>
> | 服务 | MAPE | 数据规模 |
> |---|---|---|
> | Zillow on-market | 1.7-1.9% | 1.6 亿条 |
> | Zillow off-market | 7-7.2% | 1.6 亿条 |
> | Redfin off-market | 6-9% | 几千万条 |
> | **我们** | **13.3%** | **503 条** |
>
> Zillow on-market 那 1.7% 是因为模型能看到 list_price,基本是抄作业。
> off-market 才是公平比对 — Zillow 也只有 7%,但用了 1.6 亿条数据 + 15 年迭代。
>
> **我们 503 条数据,做到 13%,差大概 2 倍**。这是现实差距,不夸大。
>
> 但**我们有一个 Zillow 没有的 feature: staging style**。
> Zillow 看不到房子里面装修风格,他能告诉你"这套房值多少",
> 但不能告诉你"装哪个风格能多卖多少"。
> 我们这一层是 Edensign 真正的护城河。

### 第 3 段:你给的 Zillow Preview 方向 — 我们能做什么

> 你给我那篇 Zillow Preview 文章我读了。Zillow 在做的事是把 pre-listing 阶段
> 从 opinion-based 变 data-based — 让 seller 在挂牌前就看到 view / save / tour request 信号。
>
> 你列的 5 个具体诉求,我们数据已经覆盖 4 个:
>
> | 你的诉求 | Zillow 怎么做 | 我们能做什么 |
> |---|---|---|
> | Pre-listing 定价 | 用 view/save 信号 | **Stage A model 反事实预测**(已训好) |
> | Buyer 是什么样的 | Zillow user behavior | **Census ACS dominant_archetype**(已有) |
> | Seller 自己 access | Seller dashboard | **API 输出 listing performance + comps**(待加 endpoint) |
> | MLS 不一定准 | Zillow 多源 | **我们用 Redfin + 详情页 + Census,可加公共记录** |
> | Buyer CTR 验证 staging | Zillow click 数据 | ❌ Zillow 不开放,我们也没有,需长期方案 |
>
> 唯一短板是 buyer click 数据。短期可以用 DOM + PPSF 当代理("卖得快+贵"="buyer 兴趣强"),
> 长期想做真 CTR 因果分析,需要 Edensign 上自己的 listing 平台埋 pixel。

### 第 4 段:接下来的具体计划

> 三个新 endpoint 一周内能做完:
>
> **`/predict-price?address=`**: pre-listing 阶段 AVM。客户输入地址,自动从 Census Geocoder 拿坐标,
> 从 listings 表拿这套房历史属性,模型预测 sold_price 区间。**前端只要 1 个输入框**。
>
> **`/buyer-profile/{zipcode}`**: 输出 ZIP 主流买家画像。
> 比如 02135 → "young_professional + young_family 主导,中位收入 $85k,大学+学历占 78%"。
>
> **`/listing-comps?address=&radius_km=`**: 给 seller 看附近 N 个相似 sold listings,
> PostGIS 半径搜索 + 风格 / 价格 / 户型筛选。这就是 seller dashboard 后端要的数据。
>
> 这三个 endpoint 加起来直接对应 Zillow Preview 80% 功能,
> Edensign 的差异化是"我们能告诉你装什么风格",这条 Zillow 给不了。

### 第 5 段:已知缺点 — 提前讲免得被问到尴尬

> 必须要诚实承认的几点:
>
> **1. log_dom 模型弱(MAPE 88%)**。DOM 在行业内本就难预测,
> 受 agent 营销、定价策略、季节、买家心理影响,这些我们不掌握。
> Zillow / Redfin 公开 estimate 也都不报 DOM 数字。
> 我们 model mode 输出 DOM 时永远带 `model_dom_low_confidence` warning。
>
> **2. 数据时间跨度大**。我们 612 条 sold 横跨 1989-2025,
> 时间漂移 36 年。当前用线性 + 二次时间控制,但不够精确。
> 解法是后续切到 Boston Case-Shiller 月度指数做归一化。
>
> **3. cross_period 数据污染**。167 条 listing 是 2024 sold + 2026 重新挂牌,
> 我们的 sold_date 和 listed_date 跨期,DOM 字段可能反映 2026 active 不是 2024 sold。
> log_dom 训练已经排除这 167 条,但 log_psf 仍包含(因为 sold_price 真实)。
>
> **4. data_quality_flag 显示 330 条 clean / 167 cross_period / 81 active_only / ... **。
> 仅 54% 是完全干净的。但因为我们设计了 informational vs hard exclude,
> 只有 rental_leakage(3 条)和 no_interior_photos(3 条)是真排除,
> 其余都纳入训练。
>
> **5. ZIP 集中在 Allston/Brighton**。02134 只有 53 条,02135 有 160。
> 02134 单独建模不靠谱,confidence 自动 medium。
> 扩到 Boston 全市需要再花一周抓 5-10 个 ZIP。

### 第 6 段:开放讨论的事项

> 想跟你对齐的 3 件事:
>
> **A. CTR 数据策略**。短期用 DOM 当 proxy 行不行?长期要不要 Edensign 上自己 listing 平台埋 pixel?
> 这是产品决策不是技术决策。
>
> **B. 是否扩到 Boston 全市**。再花 1 周抓 5000 条数据,模型 MAPE 能从 13% 降到 8-10%,
> 跟 Zillow off-market 同档。但需要给我一周纯抓数据 + 重训模型的时间。
>
> **C. 输入字段**。我们方向是"输入只要 ZIP/地址,其余属性自动获取"。
> 当前 ZIP 模式已支持。地址模式需要接 Census Geocoder + RentCast/Redfin 详情页 fetch,
> 多 1-2 天工作。要不要现在做,看你产品形态优先级。

---

## 2. 重要数字速记表(meet 时被问随便答)— **v2 production 状态**

```
=== 数据规模(2026-05-01)===
2518   总 listings (Redfin source,5 年)
478    过去 1 年 sold + active 已分类 (George 限定训练范围)
380    log_psf 训练样本 (v2)
327    log_dom 训练样本 (排除 cross_period)

=== v2 production 模型表现 ===
13.15% log_psf MAPE  (v1: 13.31%)
63.75% log_dom MAPE  (v1: 88.01%, 大幅改善 28%)

0.70   log_psf R² (Version B,EmptyRoom baseline)
0.10   log_dom R² (仍弱,但 MAPE 视角看明显改善)

=== 风格分布(过去 1 年)===
172    Lived-in (36%)  ← Allston 业主自住主力
84     Transitional (18%)  ← 真 pro-staged(从 v1 的 80+ 净化后)
85     EmptyRoom (18%)  ← vacant baseline
44     Contemporary (9%)
32     Scandinavian (7%)
16+16  Modern Min / Mid-Century Modern
21     Unclassified (4%)

=== API 设计 ===
3      scoring_mode 选项 (heuristic / model / hybrid)
4      warnings 类型 (small_zip / model_dom / low_support / data_quality)

=== 风格 lift (v2,相对 EmptyRoom baseline)===
Modern Minimalist:  +16%
Scandinavian:       +15%
Contemporary:       +9%
Lived-in:           -3.8% (p≈0.10,方向稳)
```

---

## 3. demo 现场操作(George 想看就跑)

```bash
cd /Users/jimmy20020528/Desktop/Edensign/bi
source .venv/bin/activate
uvicorn app.main:app --port 8765

# 浏览器打开 http://localhost:8000/docs 给老板点交互界面

# 或者命令行 4 连击:
curl 'http://localhost:8000/analyze/by-zipcode?zipcode=02135&scoring_mode=heuristic' | python3 -m json.tool
curl 'http://localhost:8000/analyze/by-zipcode?zipcode=02135&scoring_mode=model'      | python3 -m json.tool
curl 'http://localhost:8000/analyze/by-zipcode?zipcode=02135&scoring_mode=hybrid'     | python3 -m json.tool
curl 'http://localhost:8000/analyze/by-zipcode?zipcode=02134&scoring_mode=model'      | python3 -m json.tool
```

讲第 1 个 vs 第 2 个 vs 第 3 个的输出差异:
- **heuristic**: 看历史中位数,Bohemian 因为 6 条样本恰好快卖进 Top 3
- **model**: 控制混淆后,Bohemian 的"快卖"被识别为偶然,踢出 Top 3,Contemporary 上来
- **hybrid**: 两者折中

讲 02134 模型模式:
- confidence.overall = "medium"(因为 n=49 < 80,自动降级)
- warnings 包含 small_zip_low_support
- 个别风格因为 n<5 也带 low_support warning

---

## 4. 一句话收尾

> 我们 Phase 1 已经把 staging recommendation 做到工业 AVM 起步水平。
> Phase 2 看你方向 — 主推 Zillow Preview 对齐做 3 个新 endpoint,
> 还是先扩样到 Boston 全市把 MAPE 压到 8-10%。两个我都能做,需要你给优先级。
