# bi/app/submissions_router.py
"""Backend persistence for wizard submissions + staging runs.

Moves what wizard.html used to do client-side (write to Supabase directly) into
the API, so a rebuilt frontend only calls our endpoints and never touches the DB.
Writes go to Supabase via its PostgREST REST API using httpx (no extra dependency,
no SDK). Defaults to the same public publishable key the frontend used, so it works
out of the box; override SUPABASE_URL / SUPABASE_ANON_KEY in .env (a service-role
key works too).

Endpoints:
  POST  /submissions            -> create a row, returns {id}
  PATCH /submissions/{id}       -> partial update (listing_text, listing_style, photo_urls)
  POST  /staging-runs           -> record a staging run
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("bi.submissions")
router = APIRouter(tags=["submissions"])

_URL = os.getenv("SUPABASE_URL", "https://zdmffkfthjpdikjsstzh.supabase.co").rstrip("/")
_KEY = os.getenv("SUPABASE_ANON_KEY", "sb_publishable_deZqRESP5GmT9R7zBgFYeQ_JA8WUN_I")
_HEADERS = {"apikey": _KEY, "Authorization": f"Bearer {_KEY}", "Content-Type": "application/json"}


class SubmissionIn(BaseModel):
    address: str | None = None
    zipcode: str | None = None
    bedrooms: int | None = None
    bathrooms: float | None = None
    sqft: int | None = None
    year_built: int | None = None
    property_type: str | None = None
    listing_price: int | None = None
    agent_name: str | None = None
    agent_contact: str | None = None
    n_photos: int | None = None
    classification_result: Any | None = None
    home_report: Any | None = None
    bi_analysis: Any | None = None
    bi_explain: Any | None = None
    listing_text: str | None = None
    listing_style: str | None = None
    photo_urls: list[str] | None = None


class SubmissionPatch(BaseModel):
    listing_text: str | None = None
    listing_style: str | None = None  # the recommended staging style the listing was generated for
    photo_urls: list[str] | None = None
    neighborhood: Any | None = None     # /analyze/neighborhood result
    comps: Any | None = None            # /analyze/comps result (incl. market positioning in its narrative)
    buyer_appeal: str | None = None     # /analyze/buyer-appeal paragraph


class StagingRunIn(BaseModel):
    submission_id: Any | None = None
    room_type: str | None = None
    style: str | None = None
    remove_furniture: bool | None = None
    image_urls: list[str] | None = None
    output_urls: list[str] | None = None
    job_id: str | None = None


_MISSING_COL = re.compile(r"Could not find the '([^']+)' column")


def _uad_to_10(decimal: Any) -> float | None:
    """UAD 1.0–6.9 decimal -> 1–10 display score (same formula as the frontend's
    uadTo10: lower UAD = better = higher 10-score)."""
    if decimal is None:
        return None
    try:
        d = float(decimal)
    except (TypeError, ValueError):
        return None
    return round(max(1.0, min(10.0, 10 - (d - 1) * 1.2)), 2)


def _enrich_home_report(hr: Any) -> Any:
    """Add 1–10 scores to the home report (overall + per room) alongside the UAD
    fields, so the stored copy carries the same 1–10 the UI shows."""
    if not isinstance(hr, dict):
        return hr
    out = dict(hr)
    if out.get("overall_quality_decimal") is not None:
        out["overall_quality_10"] = _uad_to_10(out["overall_quality_decimal"])
    if out.get("overall_condition_decimal") is not None:
        out["overall_condition_10"] = _uad_to_10(out["overall_condition_decimal"])
    stats = out.get("stats")
    if isinstance(stats, dict):
        stats = dict(stats)
        if stats.get("overall_quality_decimal") is not None:
            stats["overall_quality_10"] = _uad_to_10(stats["overall_quality_decimal"])
        if stats.get("overall_condition_decimal") is not None:
            stats["overall_condition_10"] = _uad_to_10(stats["overall_condition_decimal"])
        out["stats"] = stats
    rooms = out.get("rooms")
    if isinstance(rooms, list):
        new_rooms = []
        for r in rooms:
            if isinstance(r, dict):
                r = dict(r)
                if r.get("quality_decimal") is not None:
                    r["quality_10"] = _uad_to_10(r["quality_decimal"])
                if r.get("condition_decimal") is not None:
                    r["condition_10"] = _uad_to_10(r["condition_decimal"])
            new_rooms.append(r)
        out["rooms"] = new_rooms
    return out


# ── Optional auto-migration ────────────────────────────────────────────────
# If a direct Postgres connection is provided (Supabase → Settings → Database →
# Connection string; the password is resettable there), bi creates the tables and
# any missing columns on startup, so columns never have to be added by hand.
SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")

_TABLE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "wizard_submissions": [
        ("address", "text"), ("zipcode", "text"), ("bedrooms", "int"),
        ("bathrooms", "numeric"), ("sqft", "int"), ("year_built", "int"),
        ("property_type", "text"), ("listing_price", "bigint"),
        ("agent_name", "text"), ("agent_contact", "text"), ("n_photos", "int"),
        ("classification_result", "jsonb"), ("home_report", "jsonb"),
        ("bi_analysis", "jsonb"), ("bi_explain", "jsonb"),
        ("listing_text", "text"), ("listing_style", "text"), ("photo_urls", "jsonb"),
        ("buyer_appeal", "text"), ("neighborhood", "jsonb"), ("comps", "jsonb"),
    ],
    "staging_runs": [
        ("submission_id", "uuid"), ("room_type", "text"), ("style", "text"),
        ("remove_furniture", "boolean"), ("image_urls", "jsonb"),
        ("output_urls", "jsonb"), ("job_id", "text"),
    ],
}


async def run_migration() -> None:
    """Create tables + add any missing columns. No-op unless SUPABASE_DB_URL is set."""
    if not SUPABASE_DB_URL:
        return
    try:
        import asyncpg
        conn = await asyncpg.connect(SUPABASE_DB_URL, timeout=10)
    except Exception as e:  # noqa: BLE001
        logger.warning("auto-migration skipped (cannot connect): %s", e)
        return
    try:
        for table, cols in _TABLE_COLUMNS.items():
            await conn.execute(
                f"create table if not exists {table} "
                "(id uuid primary key default gen_random_uuid(), "
                "created_at timestamptz default now())"
            )
            for name, typ in cols:
                await conn.execute(f"alter table {table} add column if not exists {name} {typ}")
        logger.info("auto-migration ok: tables/columns ensured")
    except Exception as e:  # noqa: BLE001
        logger.warning("auto-migration error: %s", e)
    finally:
        await conn.close()


async def _sb(method: str, path: str, *, json: Any = None, prefer: str | None = None) -> Any:
    headers = dict(_HEADERS)
    if prefer:
        headers["Prefer"] = prefer
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.request(method, f"{_URL}/rest/v1/{path}", headers=headers, json=json)
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Supabase unreachable: {e}") from e
    if r.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"Supabase {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else None


async def _sb_write(method: str, path: str, body: dict[str, Any], *, prefer: str) -> Any:
    """Write that self-heals around columns the table doesn't have yet: on a
    'column not found' error (PGRST204) it drops the named column and retries, so
    core fields always persist and new columns (year_built, listing_style, ...)
    light up automatically once they're added to the table."""
    cur = dict(body)
    for _ in range(8):
        try:
            return await _sb(method, path, json=cur, prefer=prefer)
        except HTTPException as e:
            m = _MISSING_COL.search(str(e.detail))
            if m and m.group(1) in cur:
                logger.warning("column '%s' missing — dropping it and retrying", m.group(1))
                cur.pop(m.group(1), None)
                if not cur:
                    return None
                continue
            raise
    return None


@router.post("/submissions")
async def create_submission(payload: SubmissionIn) -> dict[str, Any]:
    body = payload.model_dump(exclude_none=True)
    if "home_report" in body:
        body["home_report"] = _enrich_home_report(body["home_report"])  # add 1–10 scores
    rows = await _sb_write("POST", "wizard_submissions", body,
                           prefer="return=representation")
    row = rows[0] if isinstance(rows, list) and rows else (rows or {})
    return {"id": (row or {}).get("id")}


@router.patch("/submissions/{submission_id}")
async def update_submission(submission_id: str, payload: SubmissionPatch) -> dict[str, Any]:
    body = payload.model_dump(exclude_none=True)
    if body:
        await _sb_write("PATCH", f"wizard_submissions?id=eq.{submission_id}",
                        body, prefer="return=minimal")
    return {"ok": True}


@router.post("/staging-runs")
async def create_staging_run(payload: StagingRunIn) -> dict[str, Any]:
    await _sb_write("POST", "staging_runs",
                    payload.model_dump(exclude_none=True), prefer="return=minimal")
    return {"ok": True}
