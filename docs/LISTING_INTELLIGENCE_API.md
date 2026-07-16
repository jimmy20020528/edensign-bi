# Listing Intelligence API — Gateway Reference

Public contract for the six `/v1/listingIntelligence/*` endpoints exposed by the
gateway allowlist. Each public path forwards to one internal service endpoint
(shown per section). The frontend/integrator only ever sees the `/v1/…` surface;
the internal `v2`/path details are an implementation detail.

```
BASE = https://<gateway-host>            # e.g. https://<pod>-80.proxy.runpod.net, or your API domain
```

**Conventions**
- All bodies are JSON (`Content-Type: application/json`) **except** `classify-rooms`, which is `multipart/form-data`.
- No per-endpoint auth is enforced by these handlers today — apply auth at the gateway.
- Everything degrades gracefully: missing upstream data becomes an `error`/`note`/`null` field, not a 5xx.
- Health check: `GET ${BASE}/health` → `{"status":"ok"}`.
- Address handling: every endpoint that takes a location accepts a **single `address` string**
  (e.g. `"484 Second St, Cambridge, MA"`). The street/city/state split introduced on the
  persistence side (`/submissions`) is **internal** and does **not** change these request bodies —
  callers still send one combined `address`. `zipcode` may be sent instead of / in addition to `address`.

**Endpoint → internal mapping**

| Public (gateway) | Internal | Body |
|---|---|---|
| `POST /v1/listingIntelligence/classify-rooms` | `/classify-rooms` | multipart |
| `POST /v1/listingIntelligence/pipeline/run` | `/v2/pipeline/run` | JSON |
| `POST /v1/listingIntelligence/generate-listing` | `/generate-listing` | JSON |
| `POST /v1/listingIntelligence/analyze/comps` | `/analyze/comps` | JSON |
| `POST /v1/listingIntelligence/analyze/neighborhood` | `/analyze/neighborhood` | JSON |
| `POST /v1/listingIntelligence/analyze/buyer-appeal` | `/analyze/buyer-appeal` | JSON |

---

## 1. `POST /v1/listingIntelligence/classify-rooms`  *(multipart)*
Room-type classification + instance grouping + walk-through ordering for a set of photos.
→ internal `/classify-rooms` (proxies to cv-models).

**Request** — `multipart/form-data`

| field | type | required | notes |
|---|---|---|---|
| `files` | file[] | ✅ | 1–60 image files (JPG/PNG). Downscaled files are fine. |

```bash
curl -X POST "$BASE/v1/listingIntelligence/classify-rooms" \
  -F files=@1.jpg -F files=@2.jpg
```

**Response** `200`
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
**Errors** — `400` no files / more than 60 · `503` `classification_unavailable` (cv-models down).

---

## 2. `POST /v1/listingIntelligence/pipeline/run`  *(JSON)*
One-shot full report: condition report + market analysis + LLM explain (+ walk-through if
`room_groups` given). Photos must be **uploaded first** (via the app's own image upload) and
passed as URLs. → internal `/v2/pipeline/run`.

**Request**

| field | type | required | notes |
|---|---|---|---|
| `image_urls` | string[] | ✅ | 1–60 already-uploaded photo URLs. |
| `address` **or** `zipcode` | string | ✅ (one of) | `address` = full string; `zipcode` = 5 digits. |
| `bedrooms` | int | – | |
| `bathrooms` | number | – | |
| `sqft` | int | – | |
| `property_type` | string | – | default `"residential"` |
| `listing_price` | int | – | |
| `agent_name` / `agent_contact` | string | – | |
| `room_groups` | string (JSON) | – | confirmed grouping → enables walk-through, e.g. `"[{\"index\":0,\"room_type\":\"kitchen\",\"group_id\":1}]"` |

```jsonc
{ "image_urls": ["https://content.edensign.io/a.jpg", "..."],
  "address": "484 Second St, Cambridge, MA",
  "bedrooms": 3, "bathrooms": 2, "sqft": 1500, "property_type": "residential",
  "listing_price": 650000 }
```

**Response** `200`
```jsonc
{
  "zipcode": "02139",            // geocoded from address if not supplied
  "address": "484 Second St, Cambridge, MA",
  "n_photos": 12,
  "home_report":  { /* per-room quality/condition + suggestions */ },
  "bi_analysis":  { /* recommended staging styles + market context */ },
  "bi_explain":   { "analysis": {...}, "llm": { "summary": "...", "tips": [...], "buyer_profile": "..." } },
  "walkthrough":  { "order": [...], "segments": [...] } /* or null */,
  "listing_text": null           // listing is generated on demand via generate-listing
}
```
**Errors** — `400` no images / >60 / neither address nor valid zipcode.
**Latency** — fans out to CV + home-report + market + LLM; allow up to ~3 min (use a generous timeout).

---

## 3. `POST /v1/listingIntelligence/generate-listing`  *(JSON)*
Generate the listing description for **one chosen style**, on demand. → internal `/generate-listing`.

**Request**

| field | type | required | notes |
|---|---|---|---|
| `style` | string | ✅ | the staging style the copy is written for |
| `template` | string | – | default `"word_optimized"`; also `audience_first`, `concise`, `aida`, `story` |
| `home_report` | object | – | from `pipeline/run` — grounds the copy in real features |
| `market_data` | object | – | `bi_analysis` from `pipeline/run` (optional) |
| `address` / `zipcode` | string | – | |
| `bedrooms`/`bathrooms`/`sqft`/`property_type`/`listing_price` | – | – | |
| `agent_name` / `agent_contact` | string | – | |

```jsonc
{ "style": "Modern Farmhouse", "template": "audience_first",
  "home_report": { /* from pipeline/run */ }, "market_data": { /* optional */ },
  "address": "484 Second St, Cambridge, MA", "bedrooms": 3, "bathrooms": 2, "sqft": 1500 }
```

**Response** `200`
```jsonc
{ "listing_text": "Two-to-three paragraph description…",
  "style": "Modern Farmhouse",
  "template": "audience_first",
  "why_summary": "One-line rationale for the copy choices",
  "why_steps": { "style": "...", "audience": "..." } }
```
**Errors** — `400` `style` missing · `502` listing composition failed upstream.

---

## 4. `POST /v1/listingIntelligence/analyze/comps`  *(JSON)*
Comparable-sales analysis (CMA) from Redfin sold comps. → internal `/analyze/comps`.

**Request**

| field | type | required | notes |
|---|---|---|---|
| `zipcode` | string | ✅ | 5-digit |
| `address` | string | – | improves comp matching |
| `bedrooms`/`bathrooms`/`sqft`/`year_built`/`listing_price` | number | – | subject-home filters |
| `property_type` | string | – | |
| `include_narrative` | bool | – | default `true` (LLM narrative) |

```jsonc
{ "zipcode": "02139", "address": "484 Second St, Cambridge, MA",
  "bedrooms": 3, "bathrooms": 2, "sqft": 1500, "year_built": 1920,
  "listing_price": 650000, "property_type": "residential", "include_narrative": true }
```

**Response** `200`
```jsonc
{ "cma": {
    "subject": { "beds": 3, "baths": 2, "sqft": 1500, "ppsf": 433, "year_built": 1920, "listing_price": 650000 },
    "comps": [ { "address": "...", "beds": 3, "baths": 2, "sqft": 1480, "year_built": 1921,
                 "distance_mi": 0.3, "sold_price": 640000, "ppsf": 432, "status": "sold", "badges": ["best-match"] } ],
    "highlights": { "best_overall": {...}, "dimensions": {...} },
    "suggested_range": { "low": 620000, "high": 675000 },
    "stats": { /* $/SF stats etc. */ } },
  "narrative": { /* grounded summary */ } }
```
If Redfin returns nothing: `{ "cma": {...}, "narrative": null, "note": "No comparable sales available for this ZIP right now." }`
**Errors** — `400` zipcode shorter than 5 digits.

---

## 5. `POST /v1/listingIntelligence/analyze/neighborhood`  *(JSON)*
Nearby amenities + walkability + grounded narrative (key-free OSM + Walk Score).
→ internal `/analyze/neighborhood`.

**Request**

| field | type | required | notes |
|---|---|---|---|
| `address` **or** `zipcode` | string | ✅ (one of) | `address` preferred (more precise) |
| `include_narrative` | bool | – | default `true` |
| `market_context` | object | – | optional context passed to the narrative |

```jsonc
{ "address": "484 Second St, Cambridge, MA", "include_narrative": true }
```

**Response** `200`
```jsonc
{ "neighborhood": { "location": {...}, "walk_score": {...}, "amenities": [ {...} ] },
  "narrative": { /* grounded paragraph(s) */ } }   // narrative null/{"error":...} if it fails
```
**Errors** — `400` neither address nor 5-digit zipcode · `422` could not geocode the address/zip.

---

## 6. `POST /v1/listingIntelligence/analyze/buyer-appeal`  *(JSON)*
Target-buyer + positioning paragraph, grounded in the home report's real features + specs.
→ internal `/analyze/buyer-appeal`.

**Request**

| field | type | required | notes |
|---|---|---|---|
| `home_report` | object | – | from `pipeline/run` (or the condition report) |
| `market` | object | – | `bi_analysis` (optional) |
| `specs` | object | – | e.g. `{ "beds": 3, "baths": 2, "sqft": 1500, "year_built": 1920 }` |

```jsonc
{ "home_report": { /* from pipeline/run */ },
  "market": { /* optional */ },
  "specs": { "beds": 3, "baths": 2, "sqft": 1500, "year_built": 1920 } }
```

**Response** `200`
```jsonc
{ "buyer_appeal": "2–4 grounded sentences about the likely buyer and what wins them over.",
  "provider": "openai", "model": "gpt-4o-mini" }
```

---

## Errors & latency (all endpoints)
- `400` bad input · `422` couldn't geocode · `502`/`503` upstream unavailable · `500` upstream error.
  Error bodies carry `detail` (FastAPI) or an `error`/`note` field.
- LLM/VLM-backed endpoints take seconds; `pipeline/run` up to ~3 min. Use generous client timeouts.

## Typical flow
1. Upload photos (app image upload) → `classify-rooms` → show rooms, let the user edit groups.
2. `pipeline/run` with the uploaded URLs (+ edited `room_groups`) → full report.
3. Render market/style from `bi_analysis` + `bi_explain`.
4. `analyze/neighborhood`, `analyze/comps`, `analyze/buyer-appeal` for the detail sections.
5. `generate-listing` per chosen style (regenerate as the user switches styles).
