# bi/app/upload_router.py
from __future__ import annotations

import asyncio
import base64
import os
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# Don't let boto3 fall back to the EC2 instance-metadata service for creds/region —
# on non-AWS hosts (e.g. a RunPod pod) that hangs for a long time. Fail fast instead.
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
_S3_CONFIG = Config(connect_timeout=5, read_timeout=20, retries={"max_attempts": 2})

router = APIRouter(tags=["upload"])

_BUCKET = "edensign-content"
_KEY_PREFIX = "images"
_CDN = "https://content.edensign.io/images"


class UploadRequest(BaseModel):
    filename: str
    content_type: str = "image/jpeg"
    data: str  # base64-encoded file content


class UploadResponse(BaseModel):
    url: str


@router.post("/upload", response_model=UploadResponse)
async def upload_image(req: UploadRequest) -> UploadResponse:
    ext = os.path.splitext(req.filename)[1].lower() or ".jpg"
    key = f"{_KEY_PREFIX}/{uuid.uuid4()}{ext}"
    raw = base64.b64decode(req.data)

    def _upload() -> None:
        s3 = boto3.client("s3", config=_S3_CONFIG)
        s3.put_object(Bucket=_BUCKET, Key=key, Body=raw, ContentType=req.content_type)

    try:
        await asyncio.to_thread(_upload)
    except (BotoCoreError, ClientError) as e:
        raise HTTPException(status_code=502, detail=f"S3 upload failed: {e}") from e

    return UploadResponse(url=f"{_CDN}/{key.split('/', 1)[1]}")
