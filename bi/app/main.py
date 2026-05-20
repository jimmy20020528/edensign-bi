from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

_scripts = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from db_dsn import get_db_dsn  # noqa: E402
from app.services.gpt_explainer import explain_analysis_with_openai  # noqa: E402
from app.services.llm_market_estimator import estimate_market_for_zip  # noqa: E402
from app.services.zipcode_analyzer import analyze_zipcode  # noqa: E402
from app.services.walkscore_data import get_walk_scores  # noqa: E402
from app.services.fred_macro_data import get_macro_indicators  # noqa: E402
from app.services.nces_school_data import get_school_profile  # noqa: E402
from app.services.redfin_market_data import get_zip_market_data  # noqa: E402
from app.services.hmda_buyer_data import get_buyer_profile  # noqa: E402
from app.services.geocoder import geocode_address  # noqa: E402
from app.services.listing_prescription import build_prescription  # noqa: E402
from app.services.listing_writer import build_listing_copy  # noqa: E402


async def _enrich_display_data(
    result: dict[str, Any],
    zipcode: str,
    lat: float | None = None,
    lon: float | None = None,
    address_string: str | None = None,
) -> dict[str, Any]:
    """Fetch all display-only signals and attach them to the analysis result."""
    walk, macro, school, redfin, hmda = await asyncio.gather(
        asyncio.to_thread(get_walk_scores, zipcode, lat, lon, address_string),
        asyncio.to_thread(get_macro_indicators, zipcode),
        asyncio.to_thread(get_school_profile, zipcode),
        asyncio.to_thread(get_zip_market_data, zipcode),
        asyncio.to_thread(get_buyer_profile, zipcode),
        return_exceptions=True,
    )
    if walk and not isinstance(walk, Exception):
        result["walk_score_data"] = walk
    if macro and not isinstance(macro, Exception):
        result["fred_macro"] = macro
    if school and not isinstance(school, Exception):
        result["school_profile"] = school
    # Only fill if not already set by LLM estimator
    if not result.get("redfin_market") and redfin and not isinstance(redfin, Exception):
        result["redfin_market"] = redfin
    if not result.get("hmda_buyer_data") and hmda and not isinstance(hmda, Exception):
        result["hmda_buyer_data"] = hmda
    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(get_db_dsn(), min_size=1, max_size=4)
    try:
        yield
    finally:
        await app.state.pool.close()


app = FastAPI(
    title="Edensign BI API",
    description="ZIP-based data-driven staging style recommendation.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "null",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def _resolve_input(
    zipcode: str | None,
    address: str | None,
) -> tuple[str, float | None, float | None, str | None]:
    """
    Resolve either a ZIP or address to (zipcode, lat, lon, formatted_address).
    Raises HTTPException if neither can be resolved.
    """
    if address:
        geo = await asyncio.to_thread(geocode_address, address)
        if geo is None:
            raise HTTPException(status_code=400, detail=f"Could not geocode address: {address}")
        return geo["zipcode"], geo["lat"], geo["lon"], geo["formatted_address"]
    if zipcode:
        z = zipcode.strip()[:5]
        if len(z) < 5:
            raise HTTPException(status_code=400, detail="Invalid zipcode.")
        return z, None, None, None
    raise HTTPException(status_code=400, detail="Provide either zipcode or address.")


@app.get("/analyze/by-zipcode")
async def analyze_by_zipcode(
    zipcode: str | None = Query(None, description="US ZIP code"),
    address: str | None = Query(None, description="US street address"),
    objective: str = Query("balanced", description="balanced | fast | price"),
    scoring_mode: str = Query("heuristic", description="heuristic | model | hybrid"),
) -> dict:
    if not zipcode and not address:
        raise HTTPException(status_code=400, detail="Provide either zipcode or address.")
    objective = objective.lower().strip()
    if objective not in {"balanced", "fast", "price"}:
        raise HTTPException(status_code=400, detail="Invalid objective.")
    scoring_mode = scoring_mode.lower().strip()
    if scoring_mode not in {"heuristic", "model", "hybrid"}:
        raise HTTPException(status_code=400, detail="Invalid scoring_mode.")

    zipcode, lat, lon, fmt_address = await _resolve_input(zipcode, address)

    async with app.state.pool.acquire() as conn:
        try:
            result = await analyze_zipcode(
                conn, zipcode, objective=objective, scoring_mode=scoring_mode
            )
            if result.get("status") == "insufficient_data":
                result = await estimate_market_for_zip(
                    zipcode, objective=objective, scoring_mode=scoring_mode
                )
            if fmt_address:
                result["input_address"] = fmt_address
            result = await _enrich_display_data(result, zipcode, lat, lon, address_string=address)
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Analyze failed: {e}") from e


class ExplainByZipcodeRequest(BaseModel):
    zipcode: str | None = Field(None, min_length=5, max_length=10)
    address: str | None = None
    objective: str = "balanced"
    scoring_mode: str = "hybrid"
    client_context: dict[str, Any] | None = None
    precomputed_analysis: dict[str, Any] | None = None


@app.post("/analyze/explain/by-zipcode")
async def analyze_and_explain_by_zipcode(payload: ExplainByZipcodeRequest) -> dict[str, Any]:
    objective = payload.objective.lower().strip()
    if objective not in {"balanced", "fast", "price"}:
        raise HTTPException(status_code=400, detail="Invalid objective.")
    scoring_mode = payload.scoring_mode.lower().strip()
    if scoring_mode not in {"heuristic", "model", "hybrid"}:
        raise HTTPException(status_code=400, detail="Invalid scoring_mode.")

    if not payload.zipcode and not payload.address and not payload.precomputed_analysis:
        raise HTTPException(status_code=400, detail="Provide zipcode, address, or precomputed_analysis.")

    zipcode_r, lat, lon, fmt_address = await _resolve_input(payload.zipcode, payload.address) \
        if (payload.zipcode or payload.address) else (None, None, None, None)

    async with app.state.pool.acquire() as conn:
        try:
            if payload.precomputed_analysis:
                analysis = payload.precomputed_analysis
            else:
                analysis = await analyze_zipcode(
                    conn,
                    zipcode_r,
                    objective=objective,
                    scoring_mode=scoring_mode,
                )
                if analysis.get("status") == "insufficient_data":
                    analysis = await estimate_market_for_zip(
                        zipcode_r, objective=objective, scoring_mode=scoring_mode
                    )
                if fmt_address:
                    analysis["input_address"] = fmt_address
                analysis = await _enrich_display_data(analysis, zipcode_r, lat, lon, address_string=payload.address)
            llm = await explain_analysis_with_openai(
                analysis=analysis,
                client_context=payload.client_context,
            )
            return {
                "analysis": analysis,
                "llm": llm,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Explain failed: {e}") from e


class PrescriptionRequest(BaseModel):
    zipcode: str | None = Field(None, min_length=5, max_length=10)
    address: str | None = None
    objective: str = "balanced"
    scoring_mode: str = "hybrid"
    precomputed_analysis: dict[str, Any] | None = None


@app.post("/listing/prescription")
async def listing_prescription(payload: PrescriptionRequest) -> dict[str, Any]:
    """
    Generate a rule-based staging prescription and listing copy guidance.
    Pass either precomputed_analysis (from /analyze/by-zipcode) or a zipcode/address.
    """
    objective = payload.objective.lower().strip()
    if objective not in {"balanced", "fast", "price"}:
        raise HTTPException(status_code=400, detail="Invalid objective.")
    scoring_mode = payload.scoring_mode.lower().strip()
    if scoring_mode not in {"heuristic", "model", "hybrid"}:
        raise HTTPException(status_code=400, detail="Invalid scoring_mode.")

    if not payload.zipcode and not payload.address and not payload.precomputed_analysis:
        raise HTTPException(status_code=400, detail="Provide zipcode, address, or precomputed_analysis.")

    if payload.precomputed_analysis:
        analysis = payload.precomputed_analysis
    else:
        zipcode_r, lat, lon, fmt_address = await _resolve_input(payload.zipcode, payload.address)
        async with app.state.pool.acquire() as conn:
            try:
                analysis = await analyze_zipcode(
                    conn, zipcode_r, objective=objective, scoring_mode=scoring_mode
                )
                if analysis.get("status") == "insufficient_data":
                    analysis = await estimate_market_for_zip(
                        zipcode_r, objective=objective, scoring_mode=scoring_mode
                    )
                if fmt_address:
                    analysis["input_address"] = fmt_address
                analysis = await _enrich_display_data(
                    analysis, zipcode_r, lat, lon, address_string=payload.address
                )
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Analysis failed: {e}") from e

    try:
        prescription = build_prescription(analysis)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prescription failed: {e}") from e

    return prescription


_VALID_STYLES = {
    "Transitional", "Contemporary", "Modern", "Modern Minimalist",
    "Scandinavian", "Bohemian", "Industrial", "Coastal", "Coastal Modern",
    "Farmhouse", "Traditional",
}


class ListingWriteRequest(BaseModel):
    style: str
    street_address: str
    bedrooms: int | None = None
    bathrooms: float | None = None
    sqft: int | None = None
    property_type: str = "residential"
    agent_name: str | None = None
    agent_contact: str | None = None
    listing_price: int | None = None
    additional_requirements: str | None = None
    zipcode: str | None = None
    address: str | None = None
    images: list[str] | None = None  # base64 data URLs


@app.post("/listing/write")
async def listing_write(payload: ListingWriteRequest) -> dict[str, Any]:
    """
    Generate GPT-powered listing copy informed by real market data.
    Pass zipcode or address to enrich with local market context.
    """
    if payload.style not in _VALID_STYLES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown style. Valid options: {sorted(_VALID_STYLES)}",
        )

    market_data: dict[str, Any] | None = None
    if payload.zipcode or payload.address:
        try:
            zipcode_r, lat, lon, fmt_address = await _resolve_input(payload.zipcode, payload.address)
            async with app.state.pool.acquire() as conn:
                analysis = await analyze_zipcode(conn, zipcode_r, objective="balanced", scoring_mode="hybrid")
                if analysis.get("status") == "insufficient_data":
                    analysis = await estimate_market_for_zip(zipcode_r, objective="balanced", scoring_mode="hybrid")
            analysis = await _enrich_display_data(
                analysis, zipcode_r, lat, lon,
                address_string=payload.address or payload.zipcode,
            )
            market_data = analysis
        except Exception:
            pass  # market context is best-effort, don't fail the whole request

    return await build_listing_copy(
        style=payload.style,
        street_address=payload.street_address,
        bedrooms=payload.bedrooms,
        bathrooms=payload.bathrooms,
        sqft=payload.sqft,
        property_type=payload.property_type,
        agent_name=payload.agent_name,
        agent_contact=payload.agent_contact,
        listing_price=payload.listing_price,
        additional_requirements=payload.additional_requirements,
        market_data=market_data,
        images=payload.images,
    )
