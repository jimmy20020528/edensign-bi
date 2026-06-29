# Edensign API — Integration Guide

Your frontend talks to **one gateway** — the `bi` service (default port **8000**).
It serves the analysis endpoints itself and proxies the rest (classification,
walk-through, home report, pipeline) to the internal services, which may live on
the same pod or a different one. **You only need this one base URL.**

```
BASE = https://<gateway-pod>-8000.proxy.runpod.net      # http://localhost:8000 in dev
```

- Bodies are JSON unless marked **multipart**. Photo uploads = `multipart/form-data`
  with repeated `files` parts. Max **60** photos/request.
- CORS already allows `*.proxy.runpod.net` and `localhost` — a frontend on another
  pod/domain can call this directly from the browser.
- No auth today — add your own before public launch.
- Everything degrades gracefully (missing data → `error`/`note`, not a crash).
- `GET /health` → `{"status":"ok"}`.

> Classification also runs as its own service and is reachable directly on its pod
> (`https://<classify-pod>-8003.proxy.runpod.net/classify-rooms`) if you prefer to
> call it without the gateway — same request/response as below.

---

## `POST /classify-rooms`  *(multipart)* — room type + grouping + walk-through
```bash
curl -X POST "$BASE/classify-rooms" -F files=@1.jpg -F files=@2.jpg
```
```jsonc
{ "photos": [ { "index":0, "room_type":"kitchen", "occupancy":"furnished",
               "confidence":0.91, "group_id":1 } ],
  "groups": [ { "group_id":1, "room_type":"kitchen", "occupancy":"furnished",
               "photo_indices":[0,4] } ],
  "walkthrough": { "order":[...], "steps":[...], "new_room":[...] } }
```
`index` = position in your upload; `group_id` ties photos of the same room together.

## `POST /walkthrough`  *(multipart)* — re-order photos like a tour
Send photos + the (possibly user-edited) grouping.
```bash
curl -X POST "$BASE/walkthrough" -F files=@1.jpg -F files=@2.jpg \
  -F groups='[{"index":0,"room_type":"kitchen","group_id":1}, ...]'
```
```jsonc
{ "order":[idx...], "segments":[{group_id,room_type,photo_indices}],
  "steps":[...], "new_room":[...] }
```
Rule: public rooms lead, bedrooms/baths never first, outdoor last.

## `POST /report`  *(multipart)* — per-photo quality/condition report
```bash
curl -X POST "$BASE/report" -F files=@1.jpg -F files=@2.jpg
```
Returns per-room quality & condition (UAD-derived, shown 1–10) + suggestions. Use
its output as the `home_report` input for `/analyze/buyer-appeal` and `/listing/write`.

## `GET /analyze/by-zipcode` — recommended staging style + market
```bash
curl "$BASE/analyze/by-zipcode?zipcode=02149&objective=balanced&scoring_mode=heuristic"
```
| query | values | default |
|---|---|---|
| `zipcode` | 5-digit | required |
| `objective` | `balanced` \| `fast` \| `price` | `balanced` |
| `scoring_mode` | `heuristic` \| `model` \| `hybrid` | `heuristic` |

Response: `{ zipcode, recommended_styles:[{style,...}], walk_score_data, hmda_buyer_data, redfin_market, ... }`.

## `POST /analyze/explain/by-zipcode` — LLM executive summary
```jsonc
{ "zipcode":"02149", "objective":"balanced", "scoring_mode":"heuristic" }
```
Response: `{ "analysis": {/* same as above */}, "llm": { summary, tips, buyer_profile } }`.

## `POST /analyze/neighborhood` — amenities + walkability + narrative
```jsonc
{ "address":"42 Tappan St, Everett, MA 02149", "include_narrative":true }   // address preferred
```
Response: `{ "neighborhood": { location, walk_score, amenities:[...] }, "narrative": {...} }`.

## `POST /analyze/comps` — comparable sales / CMA
```jsonc
{ "zipcode":"02149", "address":"42 Tappan St...",
  "bedrooms":3, "bathrooms":2, "sqft":1500, "year_built":1920,
  "listing_price":650000, "property_type":"residential", "include_narrative":true }
```
```jsonc
{ "cma": { "subject":{beds,baths,sqft,ppsf,year_built,listing_price},
           "comps":[{address,beds,baths,sqft,year_built,distance_mi,sold_price,ppsf,status,badges:[...]}],
           "highlights":{best_overall,dimensions}, "suggested_range":{low,high}, "stats":{...} },
  "narrative": {...} }
```
If Redfin returns nothing: `{ "cma":{...}, "narrative":null, "note":"..." }`.

## `POST /analyze/buyer-appeal` — target buyer + positioning paragraph
```jsonc
{ "home_report": {/* from /report or /pipeline/run */},
  "market": {/* bi_analysis, optional */},
  "specs": { "beds":3, "baths":2, "sqft":1500, "year_built":1920 } }
```
Response: `{ "buyer_appeal":"2–4 grounded sentences", "provider":"openai", "model":"..." }`.

## `POST /listing/write` — listing description
```jsonc
{ "style":"audience_first",            // also: word_optimized, luxury, concise, ...
  "street_address":"42 Tappan St, Everett, MA 02149",
  "property_type":"residential",
  "bedrooms":3, "bathrooms":2, "sqft":1500, "listing_price":650000,
  "home_report":{/* grounds the copy */}, "market_data":{/* optional */} }
```
Response: `{ style, template, headline, paragraphs:[...], full_body, why_summary, why_steps }`.
Grounded in the provided photos/market — won't invent features or a location.

## `POST /pipeline/run`  *(multipart)* — one-shot full report  *(optional)*
Bundles condition report + market + neighborhood (+ walk-through if you pass
`room_groups`) in one call.

| field | type | notes |
|---|---|---|
| `files` | file[] | photos |
| `address` *or* `zipcode` | string | |
| `bedrooms`/`bathrooms`/`sqft`/`property_type`/`listing_price` | optional | |
| `room_groups` | string(JSON) | confirmed grouping → enables walk-through |

Response: `{ zipcode, address, n_photos, home_report, bi_analysis, bi_explain, walkthrough, listing_text }`.

---

## Typical frontend flow
1. Upload photos → `POST /classify-rooms` → show rooms; let the user edit groups.
2. (optional) `POST /walkthrough` with the edited groups → ordered photos to download.
3. `POST /report` → per-room quality/condition (keep the result for steps 6–7).
4. User enters address + beds/baths/sqft/year.
5. `GET /analyze/by-zipcode` (+`/explain`), `POST /analyze/neighborhood`, `POST /analyze/comps`.
6. `POST /analyze/buyer-appeal` (pass the `home_report` from step 3).
7. `POST /listing/write` (pass `home_report`; let the user pick a `style` and regenerate).

(Or skip 1/3/5 and call one `POST /pipeline/run` for the whole report.)

## Errors & latency
- 400 bad input · 422 couldn't geocode · 503 classifier unavailable · 500 upstream
  (bodies carry `detail`/`error`).
- LLM/VLM calls take seconds; `/pipeline/run` up to ~3 min — use generous timeouts.
