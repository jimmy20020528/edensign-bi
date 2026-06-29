from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
import sys
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

_scripts = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))

from db_dsn import get_db_dsn  # noqa: E402
from app.services.gpt_explainer import explain_analysis_with_openai  # noqa: E402
from app.services.zipcode_analyzer import analyze_zipcode  # noqa: E402
from app.services.listing_writer import build_listing_copy  # noqa: E402
from app.services.neighborhood_data import (  # noqa: E402
    analyze_neighborhood,
    generate_narrative_openai,
)
from app.services.redfin_comps import (  # noqa: E402
    analyze_comps,
    generate_comps_narrative_openai,
)
from app.services.positioning import generate_buyer_appeal_openai  # noqa: E402
from app.upload_router import router as upload_router  # noqa: E402
from staging.router import router as staging_router  # noqa: E402
from app.wizard_proxy import router as wizard_proxy_router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        app.state.pool = await asyncpg.create_pool(get_db_dsn(), min_size=1, max_size=4)
        logger.info("Database connected")
    except Exception as e:
        logger.warning("Database unavailable (%s) — running in LLM-only mode", e)
        app.state.pool = None
    try:
        yield
    finally:
        if app.state.pool:
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
    # localhost (any port) + any RunPod proxy origin, so a frontend served from a
    # separate pod (https://<pod>-<port>.proxy.runpod.net) can call this API.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$|https://[a-z0-9-]+\.proxy\.runpod\.net$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


_FRONTEND = Path(__file__).resolve().parent.parent / "frontend"
if _FRONTEND.is_dir():
    app.mount("/ui", StaticFiles(directory=str(_FRONTEND), html=True), name="frontend")

app.include_router(upload_router)
app.include_router(staging_router)
app.include_router(wizard_proxy_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/analyze/by-zipcode")
async def analyze_by_zipcode(
    zipcode: str = Query(..., min_length=5, max_length=10, description="US ZIP code"),
    objective: str = Query(
        "balanced",
        description="Optimization objective: balanced | fast | price",
    ),
    scoring_mode: str = Query(
        "heuristic",
        description="heuristic | model | hybrid",
    ),
) -> dict:
    zipcode = zipcode.strip()
    if len(zipcode) < 5:
        raise HTTPException(status_code=400, detail="Invalid zipcode.")
    objective = objective.lower().strip()
    if objective not in {"balanced", "fast", "price"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid objective. Use one of: balanced, fast, price.",
        )
    scoring_mode = scoring_mode.lower().strip()
    if scoring_mode not in {"heuristic", "model", "hybrid"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid scoring_mode. Use one of: heuristic, model, hybrid.",
        )

    if app.state.pool is None:
        try:
            from app.services.llm_market_estimator import estimate_market_for_zip
            return await estimate_market_for_zip(zipcode, objective, scoring_mode)
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"LLM fallback failed: {e}") from e

    async with app.state.pool.acquire() as conn:
        try:
            return await analyze_zipcode(
                conn, zipcode, objective=objective, scoring_mode=scoring_mode
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Analyze failed: {e}") from e


class ExplainByZipcodeRequest(BaseModel):
    zipcode: str = Field(..., min_length=5, max_length=10)
    objective: str = "balanced"
    scoring_mode: str = "hybrid"
    client_context: dict[str, Any] | None = None


@app.post("/analyze/explain/by-zipcode")
async def analyze_and_explain_by_zipcode(payload: ExplainByZipcodeRequest) -> dict[str, Any]:
    zipcode = payload.zipcode.strip()
    if len(zipcode) < 5:
        raise HTTPException(status_code=400, detail="Invalid zipcode.")

    objective = payload.objective.lower().strip()
    if objective not in {"balanced", "fast", "price"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid objective. Use one of: balanced, fast, price.",
        )
    scoring_mode = payload.scoring_mode.lower().strip()
    if scoring_mode not in {"heuristic", "model", "hybrid"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid scoring_mode. Use one of: heuristic, model, hybrid.",
        )

    async def _get_analysis(conn):
        return await analyze_zipcode(
            conn, zipcode, objective=objective, scoring_mode=scoring_mode
        )

    try:
        if app.state.pool is None:
            from app.services.llm_market_estimator import estimate_market_for_zip
            analysis = await estimate_market_for_zip(zipcode, objective, scoring_mode)
        else:
            async with app.state.pool.acquire() as conn:
                analysis = await _get_analysis(conn)
        llm = await explain_analysis_with_openai(
            analysis=analysis,
            client_context=payload.client_context,
        )
        return {"analysis": analysis, "llm": llm}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Explain failed: {e}") from e


class ListingWriteRequest(BaseModel):
    style: str
    street_address: str
    property_type: str = "residential"
    bedrooms: int | None = None
    bathrooms: float | None = None
    sqft: int | None = None
    listing_price: int | None = None
    agent_name: str | None = None
    agent_contact: str | None = None
    additional_requirements: str | None = None
    market_data: dict[str, Any] | None = None
    template: str = "word_optimized"
    home_report: dict[str, Any] | None = None


@app.post("/listing/write")
async def listing_write(payload: ListingWriteRequest) -> dict[str, Any]:
    try:
        return await build_listing_copy(**payload.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


class NeighborhoodRequest(BaseModel):
    address: str | None = None
    zipcode: str | None = None
    include_narrative: bool = True
    market_context: dict[str, Any] | None = None


@app.post("/analyze/neighborhood")
async def analyze_neighborhood_endpoint(payload: NeighborhoodRequest) -> dict[str, Any]:
    """Buyer-facing neighborhood analysis: nearby amenities + walkability + a
    grounded narrative. Sources are key-free OSM + Walk Score; every source
    degrades gracefully. See app/services/neighborhood_data.py."""
    addr = (payload.address or "").strip() or None
    zc = (payload.zipcode or "").strip() or None
    if not addr and not (zc and len(zc) >= 5):
        raise HTTPException(status_code=400, detail="Provide address or 5-digit zipcode.")

    try:
        data = analyze_neighborhood(address=addr, zipcode=zc)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Neighborhood lookup failed: {e}") from e
    if not data.get("location"):
        raise HTTPException(status_code=422, detail="Could not geocode the address/zip.")

    narrative: dict[str, Any] | None = None
    if payload.include_narrative:
        try:
            narrative = await generate_narrative_openai(data, payload.market_context)
        except Exception as e:
            logger.warning("Neighborhood narrative failed: %s", e)
            narrative = {"error": str(e)[:200]}

    return {"neighborhood": data, "narrative": narrative}


class CompsRequest(BaseModel):
    zipcode: str
    address: str | None = None
    bedrooms: float | None = None
    bathrooms: float | None = None
    sqft: float | None = None
    listing_price: float | None = None
    property_type: str | None = None
    year_built: float | None = None
    include_narrative: bool = True


@app.post("/analyze/comps")
async def analyze_comps_endpoint(payload: CompsRequest) -> dict[str, Any]:
    """Comparable-sales analysis (CMA) from Redfin sold comps: comp set, $/SF
    stats, suggested list range/anchor, and a grounded narrative. Redfin is an
    unofficial source and degrades gracefully. See app/services/redfin_comps.py."""
    zc = (payload.zipcode or "").strip()
    if len(zc) < 5:
        raise HTTPException(status_code=400, detail="Provide a 5-digit zipcode.")
    try:
        cma = analyze_comps(
            zc, address=(payload.address or "").strip() or None,
            beds=payload.bedrooms, baths=payload.bathrooms,
            sqft=payload.sqft, listing_price=payload.listing_price,
            property_type=payload.property_type, year_built=payload.year_built,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Comps lookup failed: {e}") from e
    if not cma.get("comps"):
        # no comps (Redfin blocked / region unresolved / empty) — report, don't crash
        return {"cma": cma, "narrative": None,
                "note": "No comparable sales available for this ZIP right now."}

    narrative: dict[str, Any] | None = None
    if payload.include_narrative:
        try:
            narrative = await generate_comps_narrative_openai(cma)
        except Exception as e:
            logger.warning("Comps narrative failed: %s", e)
            narrative = {"error": str(e)[:200]}

    return {"cma": cma, "narrative": narrative}


class BuyerAppealRequest(BaseModel):
    home_report: dict[str, Any] | None = None
    market: dict[str, Any] | None = None
    specs: dict[str, Any] | None = None


@app.post("/analyze/buyer-appeal")
async def analyze_buyer_appeal_endpoint(payload: BuyerAppealRequest) -> dict[str, Any]:
    """The listing review's 'Buyer Appeal' paragraph — target buyer + the features
    that drive interest, grounded in the home report's real features + specs."""
    try:
        out = await generate_buyer_appeal_openai(
            home_report=payload.home_report, market=payload.market, specs=payload.specs
        )
        return out
    except Exception as e:
        logger.warning("Buyer appeal failed: %s", e)
        return {"buyer_appeal": "", "error": str(e)[:200]}
