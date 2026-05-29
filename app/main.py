from __future__ import annotations

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
from app.services.zipcode_analyzer import analyze_zipcode  # noqa: E402
from app.services.listing_writer import build_listing_copy  # noqa: E402


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

    async with app.state.pool.acquire() as conn:
        try:
            analysis = await analyze_zipcode(
                conn,
                zipcode,
                objective=objective,
                scoring_mode=scoring_mode,
            )
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


@app.post("/listing/write")
async def listing_write(payload: ListingWriteRequest) -> dict[str, Any]:
    try:
        return await build_listing_copy(**payload.model_dump())
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
