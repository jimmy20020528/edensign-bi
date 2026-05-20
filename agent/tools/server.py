"""
Edensign Agent Tool Service.

Wraps BI (port 8000) and home-report-ai (port 8001) into clean tool endpoints
that Langflow can call. Runs on port 8002.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

BI_BASE = os.getenv("BI_BASE", "http://localhost:8000")
HOME_REPORT_BASE = os.getenv("HOME_REPORT_BASE", "http://localhost:8001")

app = FastAPI(title="Edensign Agent Tools", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "tools": ["analyze_zipcode", "generate_listing", "generate_home_report"]}


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
            params={"zipcode": req.zipcode, "objective": "balanced", "scoring_mode": "hybrid"},
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
    import asyncio

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

    async def call_bi() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            params: dict[str, Any] = {"objective": "balanced", "scoring_mode": "hybrid"}
            if address:
                params["address"] = address
            else:
                params["zipcode"] = zipcode
            r = await client.get(f"{BI_BASE}/analyze/by-zipcode", params=params)
            if r.status_code != 200:
                return {"error": r.text[:300]}
            return r.json()

    async def call_bi_explain() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=90.0) as client:
            payload: dict[str, Any] = {"objective": "balanced", "scoring_mode": "hybrid"}
            if address:
                payload["address"] = address
            else:
                payload["zipcode"] = zipcode
            r = await client.post(f"{BI_BASE}/analyze/explain/by-zipcode", json=payload)
            if r.status_code != 200:
                return {"error": r.text[:300]}
            return r.json()

    # Parallel: home report + BI analysis + BI AI explain
    home_report, bi_analysis, bi_explain = await asyncio.gather(
        call_home_report(), call_bi(), call_bi_explain()
    )

    # Extract style data for listing composition
    top_style_data = None
    if isinstance(bi_analysis, dict) and bi_analysis.get("recommended_styles"):
        top_style_data = bi_analysis["recommended_styles"][0]

    # Compose listing via bi /listing/write (inherits all prompt improvements)
    listing_text = await _compose_listing_via_bi(
        files_payload=files_payload,
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
    )

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


# ============================================================
# Listing composer — delegates to bi /listing/write
# ============================================================
async def _compose_listing_via_bi(
    files_payload: list,
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
) -> str:
    """Route listing generation through the bi listing writer (port 8000).

    Converts raw image bytes to base64 data URLs so the bi endpoint
    can pass them to GPT-4o vision.
    """
    import base64

    style = (top_style_data.get("style") if top_style_data else None) or "Transitional"
    street_address = address or zipcode or ""

    images = []
    for _, (filename, content, content_type) in files_payload:
        ct = content_type or "image/jpeg"
        b64 = base64.b64encode(content).decode()
        images.append(f"data:{ct};base64,{b64}")

    payload: dict = {
        "style": style,
        "street_address": street_address,
        "property_type": property_type,
        "images": images,
    }
    if address:
        payload["address"] = address
    elif zipcode:
        payload["zipcode"] = zipcode
    if bedrooms is not None:   payload["bedrooms"] = bedrooms
    if bathrooms is not None:  payload["bathrooms"] = bathrooms
    if sqft is not None:       payload["sqft"] = sqft
    if listing_price is not None: payload["listing_price"] = listing_price
    if agent_name:             payload["agent_name"] = agent_name
    if agent_contact:          payload["agent_contact"] = agent_contact

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
