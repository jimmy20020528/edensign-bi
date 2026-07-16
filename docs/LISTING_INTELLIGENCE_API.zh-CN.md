# Listing Intelligence API — 网关接口文档（中文版）

网关白名单对外暴露的六个 `/v1/listingIntelligence/*` 端点的对外契约。每个对外路径转发到
一个内部服务端点（各节标注）。调用方只看到 `/v1/…` 这层统一接口，内部的 `v2`/具体路径属于
实现细节。

> 英文版见 `LISTING_INTELLIGENCE_API.md`，两份内容一致。

```
BASE = https://<gateway-host>            # 例如 https://<pod>-80.proxy.runpod.net，或你的 API 域名
```

**通用约定**
- 除 `classify-rooms` 为 `multipart/form-data` 外，其余请求体均为 JSON（`Content-Type: application/json`）。
- 这些 handler 目前不做单端点鉴权——请在网关层加鉴权。
- 全部优雅降级：上游数据缺失时返回 `error`/`note`/`null` 字段，而不是抛 5xx。
- 健康检查：`GET ${BASE}/health` → `{"status":"ok"}`。
- 地址处理：所有涉及位置的端点都接收**一个完整的 `address` 字符串**（如 `"484 Second St, Cambridge, MA"`）。
  持久化层（`/submissions`）新增的 街道/城市/州 拆分是**内部改动**，**不改变**这些请求体——
  调用方仍然传一个合并好的 `address`。也可以用 `zipcode` 代替或补充 `address`。

**端点 → 内部映射**

| 对外（网关） | 内部 | 请求体 |
|---|---|---|
| `POST /v1/listingIntelligence/classify-rooms` | `/classify-rooms` | multipart |
| `POST /v1/listingIntelligence/pipeline/run` | `/v2/pipeline/run` | JSON |
| `POST /v1/listingIntelligence/generate-listing` | `/generate-listing` | JSON |
| `POST /v1/listingIntelligence/analyze/comps` | `/analyze/comps` | JSON |
| `POST /v1/listingIntelligence/analyze/neighborhood` | `/analyze/neighborhood` | JSON |
| `POST /v1/listingIntelligence/analyze/buyer-appeal` | `/analyze/buyer-appeal` | JSON |

---

## 1. `POST /v1/listingIntelligence/classify-rooms`  *(multipart)*
对一组照片做房间类型识别 + 实例分组 + 看房动线排序。
→ 内部 `/classify-rooms`（转发给 cv-models）。

**请求** — `multipart/form-data`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `files` | file[] | ✅ | 1–60 张图片文件（JPG/PNG）。压缩过的文件也可以。 |

```bash
curl -X POST "$BASE/v1/listingIntelligence/classify-rooms" \
  -F files=@1.jpg -F files=@2.jpg
```

**响应** `200`
```jsonc
{
  "photos": [
    { "index": 0, "room_type": "kitchen", "occupancy": "furnished",
      "confidence": 0.91, "group_id": 1 }
  ],
  "groups": [
    { "group_id": 1, "room_type": "kitchen", "occupancy": "furnished",
      "photo_indices": [0, 3] }
  ],
  "walkthrough": { "order": [0, 3, 1], "steps": [null, 0.8], "new_room": [true, false] }
}
```
**错误** — `400` 无文件 / 超过 60 张 · `503` `classification_unavailable`（cv-models 未运行）。

---

## 2. `POST /v1/listingIntelligence/pipeline/run`  *(JSON)*
一次性完整报告：状况报告 + 市场分析 + LLM 解读（传了 `room_groups` 还会有看房动线）。
照片需**先上传**（走 app 自己的图片上传）再以 URL 形式传入。→ 内部 `/v2/pipeline/run`。

**请求**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `image_urls` | string[] | ✅ | 1–60 个已上传的照片 URL |
| `address` **或** `zipcode` | string | ✅（二选一） | `address` 为完整地址串；`zipcode` 为 5 位数字 |
| `bedrooms` | int | – | |
| `bathrooms` | number | – | |
| `sqft` | int | – | |
| `property_type` | string | – | 默认 `"residential"` |
| `listing_price` | int | – | |
| `agent_name` / `agent_contact` | string | – | |
| `room_groups` | string (JSON) | – | 用户确认后的分组 → 启用看房动线，如 `"[{\"index\":0,\"room_type\":\"kitchen\",\"group_id\":1}]"` |

```jsonc
{ "image_urls": ["https://content.edensign.io/a.jpg", "..."],
  "address": "484 Second St, Cambridge, MA",
  "bedrooms": 3, "bathrooms": 2, "sqft": 1500, "property_type": "residential",
  "listing_price": 650000 }
```

**响应** `200`
```jsonc
{
  "zipcode": "02139",            // 未传 zipcode 时由 address 反查得到
  "address": "484 Second St, Cambridge, MA",
  "n_photos": 12,
  "home_report":  { /* 每个房间的质量/状况 + 建议 */ },
  "bi_analysis":  { /* 推荐 staging 风格 + 市场背景 */ },
  "bi_explain":   { "analysis": {...}, "llm": { "summary": "...", "tips": [...], "buyer_profile": "..." } },
  "walkthrough":  { "order": [...], "segments": [...] } /* 或 null */,
  "listing_text": null           // 文案由 generate-listing 按需生成
}
```
**错误** — `400` 无图片 / 超 60 张 / address 和 zipcode 都没有效值。
**耗时** — 扇出到 CV + home-report + 市场 + LLM；最长约 3 分钟（客户端超时设宽松些）。

---

## 3. `POST /v1/listingIntelligence/generate-listing`  *(JSON)*
按**选定的某个风格**按需生成房源文案。→ 内部 `/generate-listing`。

**请求**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `style` | string | ✅ | 文案面向的 staging 风格 |
| `template` | string | – | 默认 `"word_optimized"`；另有 `audience_first`、`concise`、`aida`、`story` |
| `home_report` | object | – | 来自 `pipeline/run` —— 让文案基于真实特征、不臆造 |
| `market_data` | object | – | `pipeline/run` 里的 `bi_analysis`（可选） |
| `address` / `zipcode` | string | – | |
| `bedrooms`/`bathrooms`/`sqft`/`property_type`/`listing_price` | – | – | |
| `agent_name` / `agent_contact` | string | – | |

```jsonc
{ "style": "Modern Farmhouse", "template": "audience_first",
  "home_report": { /* 来自 pipeline/run */ }, "market_data": { /* 可选 */ },
  "address": "484 Second St, Cambridge, MA", "bedrooms": 3, "bathrooms": 2, "sqft": 1500 }
```

**响应** `200`
```jsonc
{ "listing_text": "两到三段的房源描述…",
  "style": "Modern Farmhouse",
  "template": "audience_first",
  "why_summary": "文案取舍的一句话理由",
  "why_steps": { "style": "...", "audience": "..." } }
```
**错误** — `400` 缺 `style` · `502` 上游文案合成失败。

---

## 4. `POST /v1/listingIntelligence/analyze/comps`  *(JSON)*
基于 Redfin 成交数据的可比销售分析（CMA）。→ 内部 `/analyze/comps`。

**请求**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `zipcode` | string | ✅ | 5 位数字 |
| `address` | string | – | 提升可比房匹配精度 |
| `bedrooms`/`bathrooms`/`sqft`/`year_built`/`listing_price` | number | – | 目标房屋筛选条件 |
| `property_type` | string | – | |
| `include_narrative` | bool | – | 默认 `true`（LLM 叙述） |

```jsonc
{ "zipcode": "02139", "address": "484 Second St, Cambridge, MA",
  "bedrooms": 3, "bathrooms": 2, "sqft": 1500, "year_built": 1920,
  "listing_price": 650000, "property_type": "residential", "include_narrative": true }
```

**响应** `200`
```jsonc
{ "cma": {
    "subject": { "beds": 3, "baths": 2, "sqft": 1500, "ppsf": 433, "year_built": 1920, "listing_price": 650000 },
    "comps": [ { "address": "...", "beds": 3, "baths": 2, "sqft": 1480, "year_built": 1921,
                 "distance_mi": 0.3, "sold_price": 640000, "ppsf": 432, "status": "sold", "badges": ["best-match"] } ],
    "highlights": { "best_overall": {...}, "dimensions": {...} },
    "suggested_range": { "low": 620000, "high": 675000 },
    "stats": { /* $/SF 等统计 */ } },
  "narrative": { /* 有依据的总结 */ } }
```
Redfin 无返回时：`{ "cma": {...}, "narrative": null, "note": "No comparable sales available for this ZIP right now." }`
**错误** — `400` zipcode 不足 5 位。

---

## 5. `POST /v1/listingIntelligence/analyze/neighborhood`  *(JSON)*
周边配套 + 步行便利度 + 有依据的叙述（免 key 的 OSM + Walk Score）。
→ 内部 `/analyze/neighborhood`。

**请求**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `address` **或** `zipcode` | string | ✅（二选一） | 优先用 `address`（更精确） |
| `include_narrative` | bool | – | 默认 `true` |
| `market_context` | object | – | 传给叙述生成的可选上下文 |

```jsonc
{ "address": "484 Second St, Cambridge, MA", "include_narrative": true }
```

**响应** `200`
```jsonc
{ "neighborhood": { "location": {...}, "walk_score": {...}, "amenities": [ {...} ] },
  "narrative": { /* 有依据的段落 */ } }   // 失败时 narrative 为 null 或 {"error":...}
```
**错误** — `400` address 和 5 位 zipcode 都没有 · `422` 地址/邮编无法地理编码。

---

## 6. `POST /v1/listingIntelligence/analyze/buyer-appeal`  *(JSON)*
目标买家 + 定位段落，基于 home report 的真实特征与房屋参数。→ 内部 `/analyze/buyer-appeal`。

**请求**

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `home_report` | object | – | 来自 `pipeline/run`（或状况报告） |
| `market` | object | – | `bi_analysis`（可选） |
| `specs` | object | – | 如 `{ "beds": 3, "baths": 2, "sqft": 1500, "year_built": 1920 }` |

```jsonc
{ "home_report": { /* 来自 pipeline/run */ },
  "market": { /* 可选 */ },
  "specs": { "beds": 3, "baths": 2, "sqft": 1500, "year_built": 1920 } }
```

**响应** `200`
```jsonc
{ "buyer_appeal": "2–4 句有依据的话，描述目标买家以及打动他们的点。",
  "provider": "openai", "model": "gpt-4o-mini" }
```

---

## 错误与耗时（所有端点通用）
- `400` 入参有误 · `422` 无法地理编码 · `502`/`503` 上游不可用 · `500` 上游报错。
  错误体带 `detail`（FastAPI）或 `error`/`note` 字段。
- 依赖 LLM/VLM 的端点耗时数秒；`pipeline/run` 最长约 3 分钟。客户端超时设宽松。

## 典型调用流程
1. 上传照片（app 图片上传）→ `classify-rooms` → 展示房间，让用户调整分组。
2. 用上传得到的 URL（+ 调整后的 `room_groups`）调 `pipeline/run` → 完整报告。
3. 用 `bi_analysis` + `bi_explain` 渲染市场/风格。
4. `analyze/neighborhood`、`analyze/comps`、`analyze/buyer-appeal` 出各详情模块。
5. 每选一个风格调一次 `generate-listing`（用户切换风格就重新生成）。
