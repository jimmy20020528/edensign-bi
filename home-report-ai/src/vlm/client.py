from __future__ import annotations

import asyncio
import base64
import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_PROVIDER = os.environ.get("VLM_PROVIDER", "gemini").lower()
_TIMEOUT = 90.0
_MAX_RETRIES = 3


def _encode_image(path: Path) -> tuple[str, str]:
    """Return (base64_data, media_type)."""
    suffix = path.suffix.lower()
    media_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "image/jpeg")
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return data, media_type


async def _call_claude(image_path: Path, prompt: str) -> str:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    b64, media_type = _encode_image(image_path)
    body = {
        "model": "claude-opus-4-5",
        "max_tokens": 2048,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64",
                                              "media_type": media_type,
                                              "data": b64}},
                {"type": "text", "text": prompt},
            ],
        }],
    }
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01",
                "content-type": "application/json"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post("https://api.anthropic.com/v1/messages",
                                  headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]


async def _call_openai(image_path: Path, prompt: str) -> str:
    api_key = os.environ["OPENAI_API_KEY"]
    b64, media_type = _encode_image(image_path)
    data_url = f"data:{media_type};base64,{b64}"
    body = {
        "model": os.environ.get("OPENAI_VLM_MODEL", "gpt-4o"),
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": 2048,
    }
    headers = {"Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post("https://api.openai.com/v1/chat/completions",
                                  headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def _call_gemini(image_path: Path, prompt: str) -> str:
    api_key = os.environ["GEMINI_API_KEY"]
    b64, media_type = _encode_image(image_path)
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")  # 2.0-flash retired (404 on generateContent)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    body = {
        "contents": [{
            "parts": [
                {"inline_data": {"mime_type": media_type, "data": b64}},
                {"text": prompt},
            ]
        }],
        "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.1},
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(url, json=body)
        resp.raise_for_status()
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]


async def call_vlm_batch(image_paths: list[Path], prompt: str) -> str:
    """Send all images in one GPT-4o call. Only supported for openai provider."""
    api_key = os.environ["OPENAI_API_KEY"]
    model = os.environ.get("OPENAI_VLM_MODEL", "gpt-4o")
    content: list[dict] = [{"type": "text", "text": prompt}]
    for path in image_paths:
        b64, media_type = _encode_image(path)
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{b64}", "detail": "low"},
        })
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 4096 * min(len(image_paths), 4),
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post("https://api.openai.com/v1/chat/completions",
                                  headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


async def call_vlm(image_path: Path, prompt: str) -> str:
    """
    Call the configured VLM with exponential backoff retry.
    Never raises — returns a skip-sentinel JSON string on total failure.
    """
    _call = {"claude": _call_claude, "openai": _call_openai, "gemini": _call_gemini}.get(
        _PROVIDER, _call_gemini
    )
    delay = 2.0
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return await _call(image_path, prompt)
        except Exception as exc:
            logger.warning("VLM attempt %d/%d failed: %s", attempt, _MAX_RETRIES, exc)
            if attempt < _MAX_RETRIES:
                await asyncio.sleep(delay)
                delay *= 2
    return '{"skip": true, "skip_reason": "vlm_error"}'
