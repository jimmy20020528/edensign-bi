"""
Edensign Agent Tool Service.

Wraps BI (port 8000) and home-report-ai (port 8001) into clean tool endpoints
that Langflow can call. Runs on port 8002.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


def _zipcode_from(address: str | None, zipcode: str | None) -> str | None:
    """Return an explicit zipcode, or extract a 5-digit US zip from address."""
    if zipcode and len(zipcode) == 5 and zipcode.isdigit():
        return zipcode
    if address:
        m = re.search(r"\b(\d{5})\b", address)
        if m:
            return m.group(1)
    return None


async def _geocode_zipcode(address: str) -> str | None:
    """Call Census Bureau geocoder to resolve a free-form address → zipcode."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress",
                params={"address": address, "benchmark": "2020", "format": "json"},
            )
            if r.status_code != 200:
                return None
            matches = r.json().get("result", {}).get("addressMatches", [])
            if not matches:
                return None
            z = str(matches[0].get("addressComponents", {}).get("zip", "")).strip()[:5]
            return z if len(z) == 5 and z.isdigit() else None
    except Exception:
        return None

BI_BASE = os.getenv("BI_BASE", "http://localhost:8000")
HOME_REPORT_BASE = os.getenv("HOME_REPORT_BASE", "http://localhost:8004")
CV_MODELS_BASE = os.getenv("CV_MODELS_BASE", "http://localhost:8003")

app = FastAPI(title="Edensign Agent Tools", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "tools": ["analyze_zipcode", "generate_listing", "generate_home_report", "classify_rooms"]}


# ============================================================
# UPLOAD + STAGING — proxy to BI (port 8000)
# ============================================================
class UploadImageRequest(BaseModel):
    filename: str
    content_type: str = "image/jpeg"
    data: str


@app.post("/upload")
async def upload_image(req: UploadImageRequest) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(f"{BI_BASE}/upload", json=req.model_dump())
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text[:300])
        return r.json()


@app.post("/staging/run")
async def staging_run_proxy(req: dict) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{BI_BASE}/staging/run", json=req)
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text[:300])
        return r.json()


@app.get("/staging/status/{job_id}")
async def staging_status_proxy(job_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{BI_BASE}/staging/status/{job_id}")
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=r.text[:300])
        return r.json()


# ============================================================
# TOOL 1: Analyze a ZIP code's staging style market
# ============================================================
class AnalyzeZipInput(BaseModel):
    zipcode: str = Field(..., description="5-digit US zip code, e.g. '02135'")
    objective: str = Field(
        default="balanced",
        description="Optimization goal: 'fast', 'price', or 'balanced'",
    )


@app.post("/tool/analyze_zipcode")
async def analyze_zipcode(req: AnalyzeZipInput) -> dict[str, Any]:
    """Get FULL BI analysis for a ZIP — same data the Dashboard shows.

    Returns the complete BI API response: top styles with boosters/detractors,
    walk/transit/bike scores, school profile, buyer demographics (HMDA),
    macro indicators (mortgage rate, unemployment), Redfin market stats,
    methodology, and all warnings/confidence info.

    The agent decides what subset to surface based on user's question depth.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(
            f"{BI_BASE}/analyze/by-zipcode",
            params={"zipcode": req.zipcode, "objective": req.objective, "scoring_mode": "hybrid"},
        )
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"BI API error: {r.text[:200]}")
        return r.json()


# ============================================================
# TOOL 2: Generate a listing description
# ============================================================
class GenerateListingInput(BaseModel):
    zipcode: str = Field(..., description="Property ZIP code")
    bedrooms: int = Field(..., description="Number of bedrooms")
    bathrooms: float = Field(..., description="Number of bathrooms")
    sqft: int = Field(..., description="Square footage")
    style: str | None = Field(default=None, description="Staging style (optional)")
    tone: str = Field(default="professional", description="'professional', 'warm', 'luxurious'")


@app.post("/tool/generate_listing")
async def generate_listing(req: GenerateListingInput) -> dict[str, Any]:
    """Generate market context for a listing description.

    Returns the data the agent's LLM needs to compose the description.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(
            f"{BI_BASE}/analyze/by-zipcode",
            params={"zipcode": req.zipcode, "objective": "balanced", "scoring_mode": "heuristic"},
        )
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"BI API error: {r.text[:200]}")
        bi_data = r.json()

    style = req.style
    style_data = None
    if bi_data.get("recommended_styles"):
        if style is None:
            style_data = bi_data["recommended_styles"][0]
            style = style_data["style"]
        else:
            for s in bi_data["recommended_styles"]:
                if s["style"].lower() == style.lower():
                    style_data = s
                    break


    return {
        "property": {
            "zipcode": req.zipcode,
            "bedrooms": req.bedrooms,
            "bathrooms": req.bathrooms,
            "sqft": req.sqft,
        },
        "recommended_style": style,
        "market_context": {
            "median_price_per_sqft_in_zip": style_data.get("median_price_per_sqft") if style_data else None,
            "predicted_price_for_this_style": style_data.get("model_predicted_price") if style_data else None,
            "predicted_days_on_market": style_data.get("model_predicted_days_on_market") if style_data else None,
            "sample_size": style_data.get("n_listings") if style_data else None,
        },
        "tone": req.tone,
        "_instruction_for_agent": (
            "Compose a 2-3 paragraph listing description using these facts. "
            "Mention the style and 1-2 data points naturally. "
            "Do NOT invent features not in the input."
        ),
    }


# ============================================================
# TOOL 3: Generate a home report from photos
# ============================================================
@app.post("/tool/generate_home_report")
async def generate_home_report(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    """Upload property photos, get a UAD-standard home assessment report."""
    if not files:
        raise HTTPException(status_code=400, detail="No images provided.")
    if len(files) > 30:
        raise HTTPException(status_code=400, detail="Maximum 30 images per request.")

    files_payload = []
    for f in files:
        content = await f.read()
        files_payload.append(("files", (f.filename, content, f.content_type or "image/jpeg")))

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{HOME_REPORT_BASE}/report", files=files_payload)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"home-report-ai error: {r.text[:300]}")
        return r.json()


# ============================================================
# CLASSIFY ROOMS — proxy to cv-models service
# ============================================================
@app.post("/classify-rooms")
async def classify_rooms(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    """Proxy room classification + instance grouping to cv-models (port 8003).

    Returns: {photos: [{index, room_type, occupancy, confidence, group_id}],
              groups: [{group_id, room_type, occupancy, photo_indices}]}
    Returns HTTP 503 if cv-models is not running (wizard handles gracefully).
    """
    if not files:
        raise HTTPException(status_code=400, detail="No images provided.")
    if len(files) > 30:
        raise HTTPException(status_code=400, detail="Maximum 30 images per request.")

    files_payload = []
    for f in files:
        content = await f.read()
        files_payload.append(("files", (f.filename, content, f.content_type or "image/jpeg")))

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            r = await client.post(f"{CV_MODELS_BASE}/classify-rooms", files=files_payload)
            if r.status_code != 200:
                raise HTTPException(status_code=503, detail="classification_unavailable")
            return r.json()
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="classification_unavailable")


# ============================================================
# WIZARD PIPELINE — one-shot endpoint
# ============================================================
@app.post("/pipeline/run")
async def pipeline_run(
    files: list[UploadFile] = File(...),
    zipcode: str | None = Form(None),
    address: str | None = Form(None),
    bedrooms: int | None = Form(None),
    bathrooms: float | None = Form(None),
    sqft: int | None = Form(None),
    property_type: str = Form("residential"),
    listing_price: int | None = Form(None),
    agent_name: str | None = Form(None),
    agent_contact: str | None = Form(None),
) -> dict[str, Any]:
    """Photos + zipcode_or_address -> full Edensign report.

    Runs home-report-ai and BI in parallel, then composes a listing
    description. Returns a unified JSON the frontend can render.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No images provided.")
    if len(files) > 30:
        raise HTTPException(status_code=400, detail="Maximum 30 images.")
    if not address and not (zipcode and len(zipcode) == 5 and zipcode.isdigit()):
        raise HTTPException(status_code=400, detail="Provide either address or 5-digit zipcode.")

    # Read all photos into memory (so we can replay multipart payload)
    files_payload = []
    for f in files:
        content = await f.read()
        files_payload.append(("files", (f.filename, content, f.content_type or "image/jpeg")))

    async def call_home_report() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(f"{HOME_REPORT_BASE}/report", files=files_payload)
            if r.status_code != 200:
                return {"error": r.text[:300]}
            return r.json()

    resolved_zip = _zipcode_from(address, zipcode)
    if not resolved_zip and address:
        resolved_zip = await _geocode_zipcode(address)
        if resolved_zip:
            logger.info("Geocoded '%s' → %s", address, resolved_zip)

    async def call_bi() -> dict[str, Any]:
        if not resolved_zip:
            return {"error": "cannot determine zipcode from address"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            params: dict[str, Any] = {"zipcode": resolved_zip, "objective": "balanced", "scoring_mode": "heuristic"}
            r = await client.get(f"{BI_BASE}/analyze/by-zipcode", params=params)
            if r.status_code != 200:
                return {"error": r.text[:300]}
            return r.json()

    async def call_bi_explain() -> dict[str, Any]:
        if not resolved_zip:
            return {"error": "cannot determine zipcode from address"}
        async with httpx.AsyncClient(timeout=90.0) as client:
            payload: dict[str, Any] = {"zipcode": resolved_zip, "objective": "balanced", "scoring_mode": "heuristic"}
            r = await client.post(f"{BI_BASE}/analyze/explain/by-zipcode", json=payload)
            if r.status_code != 200:
                return {"error": r.text[:300]}
            return r.json()

    # bi_explain is independent — start it immediately so it runs while home_report runs
    bi_explain_task = asyncio.create_task(call_bi_explain())

    try:
        # home report + BI analysis in parallel (listing needs both)
        home_report, bi_analysis = await asyncio.gather(
            call_home_report(), call_bi()
        )

        # Extract style data and home report highlights for listing
        top_style_data = None
        if isinstance(bi_analysis, dict) and bi_analysis.get("recommended_styles"):
            top_style_data = bi_analysis["recommended_styles"][0]
        home_report_highlights = _extract_home_report_highlights(home_report)
        if home_report_highlights is None:
            logger.warning("No room highlights above Q4 threshold — listing will be generic")

        # Listing starts as soon as home_report + bi finish; bi_explain may already be done
        listing_text = await _compose_listing_via_bi(
            address=address,
            zipcode=zipcode,
            top_style_data=top_style_data,
            bedrooms=bedrooms,
            bathrooms=bathrooms,
            sqft=sqft,
            property_type=property_type,
            listing_price=listing_price,
            agent_name=agent_name,
            agent_contact=agent_contact,
            additional_requirements=home_report_highlights,
            market_data=bi_analysis if isinstance(bi_analysis, dict) and "error" not in bi_analysis else None,
        )
    finally:
        bi_explain = await bi_explain_task

    # Resolved zipcode from BI (if user supplied address, BI returns the geocoded zip)
    resolved_zipcode = zipcode
    if isinstance(bi_analysis, dict) and bi_analysis.get("zipcode"):
        resolved_zipcode = bi_analysis["zipcode"]

    return {
        "zipcode": resolved_zipcode,
        "address": address,
        "n_photos": len(files),
        "home_report": home_report,
        "bi_analysis": bi_analysis,
        "bi_explain": bi_explain,
        "listing_text": listing_text,
    }


def _extract_home_report_highlights(home_report: Any) -> str | None:
    if not isinstance(home_report, dict):
        return None
    rooms = home_report.get("rooms")
    if not isinstance(rooms, list):
        return None

    highlights = []
    for room in rooms:
        if not isinstance(room, dict):
            continue
        q = room.get("quality_decimal", 0)
        if q < 4.0:
            continue
        room_type = room.get("room_type", "room")
        q_rating = room.get("quality_rating", f"Q{int(q)}")
        c_rating = room.get("condition_rating", "")
        rationale = room.get("quality_rationale", "")
        rating_str = f"{q_rating}/{c_rating}" if c_rating else q_rating
        highlights.append(f"- {room_type} ({rating_str}): {rationale}")

    if not highlights:
        return None

    return (
        "Property highlights from professional room assessment "
        "(use these specific details naturally in the listing — do not mention ratings or scores, "
        "do not reference low-scoring rooms):\n"
        + "\n".join(highlights)
    )


# ============================================================
# Listing composer — delegates to bi /listing/write
# ============================================================
async def _compose_listing_via_bi(
    address: str | None,
    zipcode: str | None,
    top_style_data: dict | None,
    bedrooms: int | None = None,
    bathrooms: float | None = None,
    sqft: int | None = None,
    property_type: str = "residential",
    listing_price: int | None = None,
    agent_name: str | None = None,
    agent_contact: str | None = None,
    additional_requirements: str | None = None,
    market_data: dict | None = None,
) -> str:
    style = (top_style_data.get("style") if top_style_data else None) or "Transitional"
    street_address = address or zipcode or ""
    payload: dict = {
        "style": style,
        "street_address": street_address,
        "property_type": property_type,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "sqft": sqft,
        "listing_price": listing_price,
        "agent_name": agent_name,
        "agent_contact": agent_contact,
        "additional_requirements": additional_requirements,
        "market_data": market_data,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            r = await client.post(f"{BI_BASE}/listing/write", json=payload)
            if r.status_code != 200:
                return f"[listing_error: {r.text[:200]}]"
            data = r.json()
            return data.get("full_body") or "\n\n".join(data.get("paragraphs", []))
        except Exception as e:
            return f"[listing_exception: {str(e)[:200]}]"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="info")
