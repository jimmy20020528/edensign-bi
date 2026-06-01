# bi/app/upload_router.py
from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(tags=["upload"])

_BUCKET = "s3://edensign-content/images"
_CDN    = "https://content.edensign.io/images"


class UploadRequest(BaseModel):
    filename: str
    content_type: str = "image/jpeg"
    data: str  # base64-encoded file content


class UploadResponse(BaseModel):
    url: str


@router.post("/upload", response_model=UploadResponse)
async def upload_image(req: UploadRequest) -> UploadResponse:
    ext = os.path.splitext(req.filename)[1].lower() or ".jpg"
    key = f"{uuid.uuid4()}{ext}"

    raw = base64.b64decode(req.data)

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name

    try:
        proc = await asyncio.create_subprocess_exec(
            "aws", "s3", "cp", tmp_path, f"{_BUCKET}/{key}",
            "--content-type", req.content_type,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise HTTPException(status_code=502, detail=f"S3 upload failed: {stderr.decode()}")
    finally:
        os.unlink(tmp_path)

    return UploadResponse(url=f"{_CDN}/{key}")
