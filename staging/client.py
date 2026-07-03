# bi/staging/client.py
from __future__ import annotations

import json
import os
import pathlib
import random

import httpx

# ── Key normalization maps ──────────────────────────────────────────────────

STYLE_KEY: dict[str, str] = {
    "Transitional":       "transitional",
    "Modern":             "modern",
    "Scandinavian":       "scandinavian",
    "Industrial":         "industrial",
    "Mid-Century Modern": "midcentury",
    "Luxury":             "luxury",
    "Coastal":            "coastal",
    "Farmhouse":          "farmhouse",
    "Standard":           "standard",
}

ROOM_KEY: dict[str, str] = {
    "living room":    "living",
    "living":         "living",
    "kitchen":        "kitchen",
    "bedroom":        "bedroom",
    "bathroom":       "bathroom",
    "bath":           "bathroom",
    "balcony":        "balcony",
    "dining room":    "dining",
    "dining":         "dining",
    "kids room":      "kids_room",
    "kids_room":      "kids_room",
    "home office":    "home_office",
    "home_office":    "home_office",
    "outdoor":        "outdoor",
    "hallway":        "hallway",
    "living/bedroom": "living_bedroom",
    "living_bedroom": "living_bedroom",
    "living/dining":  "living_dining",
    "living_dining":  "living_dining",
    "theater":        "theater",
    "theatre":        "theater",
}

_PROMPTS: dict = json.loads(
    (pathlib.Path(__file__).parent / "prompts.json").read_text()
)


def pick_prompt(room_type_label: str, style: str) -> str:
    """Return a random sub-style prompt for the given room type and canonical style."""
    room_key = ROOM_KEY.get(room_type_label.strip().lower(), "living")
    style_key = STYLE_KEY.get(style, "standard")
    variants = (
        _PROMPTS.get(room_key, {}).get(style_key)
        or _PROMPTS["living"]["standard"]
    )
    return random.choice(variants)


def _build_payload(
    image_urls: list[str],
    prompt: str,
    remove_furniture: bool,
) -> dict:
    """Build the RunPod request payload. Pure function — no I/O."""
    if len(image_urls) == 1:
        return {
            "input": {
                "config": {
                    "type": "custom_staging",
                    "add_furniture": {"prompt": prompt},
                    "remove_furniture": {"mode": "auto" if remove_furniture else "off"},
                    "enable_hd": True,
                },
                "image_url": image_urls[0],
            }
        }
    return {
        "input": {
            "config": {
                "type": "multiview",
                "input_scaling": "off",
                "add_furniture": {"prompt": prompt},
                "remove_furniture": {
                    "mode": "on" if remove_furniture else "off",
                    "enable_hd": True,
                },
            },
            "image_urls": image_urls,
        }
    }


async def submit_job(
    image_urls: list[str],
    room_type_label: str,
    style: str,
    remove_furniture: bool,
) -> str:
    """Submit a staging job to RunPod. Returns job_id string."""
    api_key = os.environ.get("RUNPOD_API_KEY")
    endpoint = os.environ.get("RUNPOD_STAGING_ENDPOINT")
    if not api_key or not endpoint:
        raise RuntimeError("RUNPOD_API_KEY and RUNPOD_STAGING_ENDPOINT must be set in environment")
    prompt = pick_prompt(room_type_label, style)
    payload = _build_payload(image_urls, prompt, remove_furniture)

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"https://api.runpod.ai/v2/{endpoint}/run",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        r.raise_for_status()
        return r.json()["id"]


async def get_job_status(job_id: str) -> dict:
    """Poll RunPod for job status. Returns raw RunPod response dict."""
    api_key = os.environ.get("RUNPOD_API_KEY")
    endpoint = os.environ.get("RUNPOD_STAGING_ENDPOINT")
    if not api_key or not endpoint:
        raise RuntimeError("RUNPOD_API_KEY and RUNPOD_STAGING_ENDPOINT must be set in environment")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"https://api.runpod.ai/v2/{endpoint}/status/{job_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        r.raise_for_status()
        return r.json()
