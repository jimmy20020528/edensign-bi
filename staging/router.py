# bi/staging/router.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from staging.client import get_job_status, submit_job

router = APIRouter(prefix="/staging", tags=["staging"])


class RunRequest(BaseModel):
    image_urls: list[str]
    room_type_label: str
    style: str
    remove_furniture: bool = True


class RunResponse(BaseModel):
    job_id: str


class StatusResponse(BaseModel):
    status: str
    output_urls: list[str] = []
    error: str | None = None


@router.post("/run", response_model=RunResponse)
async def staging_run(req: RunRequest) -> RunResponse:
    if not req.image_urls:
        raise HTTPException(status_code=400, detail="image_urls must not be empty")
    if not all(url.startswith(("http://", "https://")) for url in req.image_urls):
        raise HTTPException(status_code=400, detail="All image_urls must be http(s) URLs")
    try:
        job_id = await submit_job(
            image_urls=req.image_urls,
            room_type_label=req.room_type_label,
            style=req.style,
            remove_furniture=req.remove_furniture,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RunPod submit failed: {e}") from e
    return RunResponse(job_id=job_id)


@router.get("/status/{job_id}", response_model=StatusResponse)
async def staging_status(job_id: str) -> StatusResponse:
    try:
        data = await get_job_status(job_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"RunPod poll failed: {e}") from e

    status = data.get("status", "UNKNOWN")
    output_url: str | None = None

    output_urls: list[str] = []
    if status == "COMPLETED":
        out = data.get("output", {}) if isinstance(data.get("output"), dict) else {}
        result = out.get("result")
        if isinstance(result, list):
            output_urls = [r["url"] for r in result if isinstance(r, dict) and r.get("url")]
        elif isinstance(result, dict) and result.get("url"):
            output_urls = [result["url"]]

    return StatusResponse(
        status=status,
        output_urls=output_urls,
        error=data.get("error"),
    )
