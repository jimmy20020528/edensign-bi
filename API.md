# Edensign API — Integration Guide

Two base URLs (see DEPLOY.md / FRONTEND.md):

```
BASE     = https://<gateway-pod>-80.proxy.runpod.net      # bi gateway: everything below
CLASSIFY = https://<classify-pod>-8003.proxy.runpod.net   # classification (its own pod)
```
(In local dev the gateway defaults to `http://localhost:8000`.)

- Bodies are JSON unless marked **multipart**.
- CORS allows `*.proxy.runpod.net` and `localhost` — call from the browser directly.
- No auth today — add your own before public launch.
- Everything degrades gracefully (missing data → `error`/`note`, not a crash).
- `GET ${BASE}/health` → `{"status":"ok"}`.

---

## `POST ${CLASSIFY}/classify-rooms` — room type + grouping  *(JSON, URL-based)*

The deployed classifier takes **image URLs** (upload first — see `/upload`) and
downloads them server-side. (Different from the gateway's internal multipart variant.)

```bash
curl -X POST "$CLASSIFY/classify-rooms" -H "Content-Type: application/json" \
  -d '{"image_urls":["https://content.edensign.io/images/a.jpg", "..."]}'   # 1–30 URLs
```
```jsonc
{ "groups": [
    { "id": 1, "room_type": "kitchen", "occupancy": "furnished",
      "photos": [ { "url": "https://…/a.jpg", "room_type": "kitchen",
                    "occupancy": "furnished", "confidence": 0.91 } ] } ] }
```

## `POST ${BASE}/walkthrough` — re-order photos like a tour  *(multipart)*

Only served when the gateway was started with `WALKTHROUGH=1` (see DEPLOY.md);
otherwise unavailable. Send photo files + the confirmed grouping.
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

**Prefer `POST /v2/pipeline/run` for real listings.** Same response shape, but
JSON with `image_urls` (from `/upload`) instead of multipart file bytes — the
multipart body above scales with total photo size and can exceed the
production proxy's request-size limit once a listing has more than a
handful of full-resolution photos, causing a timeout.

```jsonc
{ "image_urls": ["https://.../a.jpg", "..."],   // upload each via POST /upload first
  "address": "42 Tappan St, Everett, MA 02149", // or "zipcode"
  "bedrooms": 3, "bathrooms": 2, "sqft": 1500, "property_type": "residential",
  "listing_price": 650000, "agent_name": "...", "agent_contact": "...",
  "room_groups": "[{...}]"                       // optional, same as /pipeline/run
}
```

---

## Persistence (optional) — save a submission to the DB

The backend persists submissions/runs for you (it writes to Supabase server-side),
so the frontend never needs DB credentials.

- `POST /submissions` — create a row, returns `{ "id": "..." }`. Body: any of
  `address, zipcode, bedrooms, bathrooms, sqft, property_type, listing_price,
  agent_name, agent_contact, n_photos, classification_result, home_report,
  bi_analysis, bi_explain, listing_text, photo_urls`.
- `PATCH /submissions/{id}` — partial update; send `{ "listing_text": "..." }` after
  the listing is (re)generated, and/or `{ "photo_urls": [...] }` once photos are uploaded.
  (Regenerating overwrites `listing_text` — the latest wins.)
- `POST /staging-runs` — `{ submission_id, room_type, style, remove_furniture,
  image_urls, output_urls, job_id }`.

Recommended order: create the submission right after `/pipeline/run` (get the `id`),
then PATCH `photo_urls` after upload and `listing_text` after each generate.

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
