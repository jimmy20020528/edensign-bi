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


class StagingRunIn(BaseModel):
    submission_id: Any | None = None
    room_type: str | None = None
    style: str | None = None
    remove_furniture: bool | None = None
    image_urls: list[str] | None = None
    output_urls: list[str] | None = None
    job_id: str | None = None


_MISSING_COL = re.compile(r"Could not find the '([^']+)' column")


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
    rows = await _sb_write("POST", "wizard_submissions",
                           payload.model_dump(exclude_none=True),
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
