# Frontend Integration — calling each backend feature

How a (rebuilt) frontend calls every Edensign feature. Raw request/response shapes
are in `API.md`; this is the practical "what to call, in what order" with JS `fetch`.

## Setup — two base URLs

```js
const API   = "https://<gateway-pod>-80.proxy.runpod.net";   // bi gateway: everything except classify
const CLASSIFY = "https://<classify-pod>-8003.proxy.runpod.net"; // the classification service
```

- CORS already allows `*.proxy.runpod.net` and `localhost` — call both from the browser.
- All bodies are JSON unless marked multipart. No auth today (add your own gateway/key for prod).
- Health: `GET ${API}/health` → `{status:"ok"}`.

---

## 1. Upload photos → S3 URLs  `POST ${API}/upload`

Each photo is base64'd and uploaded; you get back a public `content.edensign.io` URL.
Classification (step 2) needs URLs, and you persist `photo_urls`.

```js
async function uploadPhoto(file) {
  const b64 = await new Promise(r => {
    const fr = new FileReader();
    fr.onload = () => r(fr.result.split(",")[1]);
    fr.readAsDataURL(file);
  });
  const res = await fetch(`${API}/upload`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, content_type: file.type || "image/jpeg", data: b64 }),
  });
  const { url } = await res.json();        // https://content.edensign.io/images/<uuid>.jpg
  return url;
}
const photoUrls = await Promise.all(files.map(uploadPhoto));
```

## 2. Classification (room type + grouping)  `POST ${CLASSIFY}/classify-rooms`

Send the **URLs** from step 1 (this service is URL-based, on its own pod).

```js
const res = await fetch(`${CLASSIFY}/classify-rooms`, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ image_urls: photoUrls }),   // 1–30 URLs
});
const { groups } = await res.json();
// groups: [{ id, room_type, occupancy, photos: [{ url, room_type, occupancy, confidence }] }]
```

Use `groups` to show rooms / let the user regroup before the rest.

## 3. Market analysis + recommended staging style  `GET ${API}/analyze/by-zipcode`

```js
const a = await (await fetch(
  `${API}/analyze/by-zipcode?zipcode=02149&objective=balanced&scoring_mode=heuristic`
)).json();
// a.recommended_styles: [{ style, median_days_on_market, median_price_per_sqft,
//                          estimated_psf_premium_pct, style_score, market_fit, explain }]
// a.walk_score_data, a.hmda_buyer_data, a.redfin_market, a.confidence
```
Any ZIP works (DB where seeded, LLM estimate elsewhere). `a` is the `bi_analysis` object.

## 4. Executive summary  `POST ${API}/analyze/explain/by-zipcode`

```js
const e = await (await fetch(`${API}/analyze/explain/by-zipcode`, {
  method:"POST", headers:{ "Content-Type":"application/json" },
  body: JSON.stringify({ zipcode:"02149", objective:"balanced", scoring_mode:"heuristic" }),
})).json();
// e.analysis (same as #3), e.llm: { summary, tips, buyer_profile }
```

## 5. Neighborhood (amenities + walkability)  `POST ${API}/analyze/neighborhood`

```js
const n = await (await fetch(`${API}/analyze/neighborhood`, {
  method:"POST", headers:{ "Content-Type":"application/json" },
  body: JSON.stringify({ address: "42 Tappan St, Everett, MA 02149", include_narrative:true }),
})).json();
// n.neighborhood.walk_score, n.neighborhood.amenities {dining, grocery, schools,
//   recreation, transit, ...} each [{ name, kind, distance_mi }], n.narrative
```

## 6. Comparable Sales (CMA) + market positioning  `POST ${API}/analyze/comps`

```js
const c = await (await fetch(`${API}/analyze/comps`, {
  method:"POST", headers:{ "Content-Type":"application/json" },
  body: JSON.stringify({ zipcode:"02149", address, bedrooms:3, bathrooms:2,
                         sqft:1500, year_built:1920, listing_price:650000,
                         property_type:"residential", include_narrative:true }),
})).json();
// c.cma.comps [{ address, beds, baths, sqft, year_built, distance_mi, sold_price, ppsf, status, badges }]
// c.cma.subject, c.cma.highlights (best match), c.cma.suggested_range, c.cma.price_position ← market positioning
// c.narrative
```

## 7. Buyer Appeal  `POST ${API}/analyze/buyer-appeal`

Pass the home report (#9) + specs.

```js
const b = await (await fetch(`${API}/analyze/buyer-appeal`, {
  method:"POST", headers:{ "Content-Type":"application/json" },
  body: JSON.stringify({ home_report, market: bi_analysis,
                         specs:{ beds:3, baths:2, sqft:1500, year_built:1920 } }),
})).json();
// b.buyer_appeal  (2–4 grounded sentences)
```

## 8. Home Report — per-room condition (1–10)  `POST ${API}/report`  *(multipart)*

```js
const fd = new FormData();
files.forEach(f => fd.append("files", f));
const home_report = await (await fetch(`${API}/report`, { method:"POST", body: fd })).json();
// home_report.overall_quality_10 / overall_condition_10 (1–10), rooms:[{ room_type,
//   quality_10, condition_10, notable_features, quality_rationale, ... }], overall_narrative
```
Quality/condition are on a **1–10** scale (UAD fields kept too). Use `home_report` as
input for #7 and #10.

## 9. Listing description  `POST ${API}/listing/write`

```js
const l = await (await fetch(`${API}/listing/write`, {
  method:"POST", headers:{ "Content-Type":"application/json" },
  body: JSON.stringify({ style:"audience_first",       // writing style; see list below
                         street_address: address, property_type:"residential",
                         bedrooms:3, bathrooms:2, sqft:1500, listing_price:650000,
                         home_report, market_data: bi_analysis }),
})).json();
// l.headline, l.paragraphs[], l.full_body, l.why_summary, l.why_steps
```
**Writing styles** (the user picks one; regenerate to switch): `audience_first`,
`word_optimized`, `luxury`, `concise`, `aida`. Grounded in the photos/market — won't
invent features or a location.

## 10. Photo walk-through (optional)  `POST ${API}/walkthrough`  *(multipart)*

Re-orders photos like a real tour. Served on the gateway (`:80`) when the backend was
started with `WALKTHROUGH=1` (see DEPLOY.md); otherwise it's unavailable. Send the
photo files + the confirmed grouping.

```js
const fd = new FormData();
files.forEach(f => fd.append("files", f));
fd.append("groups", JSON.stringify(groups));      // from #2 (possibly user-edited)
const w = await (await fetch(`${API}/walkthrough`, { method:"POST", body: fd })).json();
// w.order [photoIndex...], w.segments [{ room_type, photo_indices }]
```

## 11. One-shot (optional)  `POST ${API}/v2/pipeline/run`  *(JSON)*

Bundles home report + market + neighborhood (+ walk-through if `room_groups` sent) in
one call — instead of #3/#5/#8 individually.

**Prefer the JSON `/v2/pipeline/run`** (below): upload each photo via `/upload` first,
then send the URLs. The old multipart `/pipeline/run` puts every photo's raw bytes in
one request body, which exceeds the production proxy's 6MB limit once a listing has
more than a handful of full-res photos (times out). v2 keeps the request tiny and the
backend downloads the photos itself. Same response shape.

```js
// image_urls come from #12's /upload (or your own upload) — not raw File objects.
const body = { image_urls: photoUrls, address, bedrooms: 3, sqft: 1500 };
if (roomGroups) body.room_groups = JSON.stringify(roomGroups);  // optional, enables walk-through
const p = await (await fetch(`${API}/v2/pipeline/run`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
})).json();
// p.home_report, p.bi_analysis, p.bi_explain, p.walkthrough, p.zipcode, p.n_photos
```

The legacy multipart form is still available at `POST ${API}/pipeline/run` (same fields
as a FormData with `files`), but avoid it for real listings for the size reason above.

## 12. Persistence — save the submission to the DB

The backend writes to Supabase for you (no DB creds in the frontend).

```js
// after you have the results: create the row first (fast → id), then patch the rest
const { id } = await (await fetch(`${API}/submissions`, {
  method:"POST", headers:{ "Content-Type":"application/json" },
  body: JSON.stringify({ address, zipcode, bedrooms, bathrooms, sqft, year_built,
    property_type, listing_price, agent_name, agent_contact, n_photos: files.length,
    classification_result: groups, home_report, bi_analysis, bi_explain }),
})).json();

await fetch(`${API}/submissions/${id}`, { method:"PATCH",
  headers:{ "Content-Type":"application/json" },
  body: JSON.stringify({ photo_urls: photoUrls }) });           // after upload

await fetch(`${API}/submissions/${id}`, { method:"PATCH",       // after each (re)generate
  headers:{ "Content-Type":"application/json" },
  body: JSON.stringify({ listing_text: l.full_body, listing_style: "audience_first" }) });

// also persist the section results once they load:
await fetch(`${API}/submissions/${id}`, { method:"PATCH",
  headers:{ "Content-Type":"application/json" },
  body: JSON.stringify({ buyer_appeal: b.buyer_appeal, neighborhood: n, comps: c }) });
```
`listing_style` = the **writing** style (audience_first…), not the staging style.

## 13. Virtual staging (optional)  `POST ${API}/staging/run` → poll `GET ${API}/staging/status/{job_id}`

```js
const { job_id } = await (await fetch(`${API}/staging/run`, {
  method:"POST", headers:{ "Content-Type":"application/json" },
  body: JSON.stringify({ image_urls:[...], room_type:"living_room",
                         style:"Modern", remove_furniture:true }),
})).json();
// poll until status COMPLETED → { output_urls: [...] }
const s = await (await fetch(`${API}/staging/status/${job_id}`)).json();
```
Record it: `POST ${API}/staging-runs { submission_id, room_type, style, remove_furniture, image_urls, output_urls, job_id }`.

---

## Recommended flow

1. Upload photos → URLs (#1)
2. Classify (#2) → show rooms, let user edit groups
3. Get home report (#8), market (#3 +#4), neighborhood (#5), comps (#6), buyer appeal (#7)
4. Generate listing (#9); walk-through order (#10) if available
5. Create submission (#12), patch photo_urls / listing / section results as they land
6. (optional) virtual staging (#13)

Errors: 400 bad input · 422 couldn't geocode · 503 classifier/LLM unavailable · 500
upstream. Bodies carry `detail`/`error`. Each endpoint degrades gracefully.
