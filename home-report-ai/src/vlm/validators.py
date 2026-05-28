from __future__ import annotations

import json
import logging

from src.models.schemas import RoomAssessment

logger = logging.getLogger(__name__)

_QUALITY_MAP = {
    "q1": "Q1", "q2": "Q2", "q3": "Q3", "q4": "Q4", "q5": "Q5", "q6": "Q6",
    "1": "Q1", "2": "Q2", "3": "Q3", "4": "Q4", "5": "Q5", "6": "Q6",
}
_CONDITION_MAP = {
    "c1": "C1", "c2": "C2", "c3": "C3", "c4": "C4", "c5": "C5", "c6": "C6",
    "1": "C1", "2": "C2", "3": "C3", "4": "C4", "5": "C5", "6": "C6",
}
_ROOM_TYPE_MAP = {
    "living room": "living_room",
    "dining room": "dining",
    "dining area": "dining",
    "master bedroom": "bedroom",
    "guest bedroom": "bedroom",
    "half bath": "bathroom",
    "full bath": "bathroom",
    "foyer": "hallway",
    "entry": "hallway",
    "laundry": "hallway",
    "garage": "exterior",
    "yard": "exterior",
    "patio": "balcony",
    "terrace": "balcony",
}
_QUALITY_LABEL_MAP = {
    "high": "Q2", "high-end": "Q2", "high end": "Q2",
    "above average": "Q3", "above-average": "Q3",
    "average": "Q4", "standard": "Q4", "builder": "Q4", "builder grade": "Q4",
    "economy": "Q5", "low": "Q5",
}
_CONDITION_LABEL_MAP = {
    "excellent": "C2", "like new": "C2", "like-new": "C2",
    "good": "C3", "fair": "C4",
    "poor": "C5", "bad": "C5",
    "critical": "C6",
}


def _strip_markdown(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def _normalize(data: dict) -> dict:
    # room_type
    if "room_type" in data:
        rt = str(data["room_type"]).lower().strip()
        rt = _ROOM_TYPE_MAP.get(rt, rt.replace(" ", "_"))
        data["room_type"] = rt

    # quality_rating
    if "quality_rating" in data:
        qr = str(data["quality_rating"]).lower().strip()
        mapped = _QUALITY_MAP.get(qr) or _QUALITY_LABEL_MAP.get(qr)
        if mapped:
            data["quality_rating"] = mapped

    # condition_rating
    if "condition_rating" in data:
        cr = str(data["condition_rating"]).lower().strip()
        mapped = _CONDITION_MAP.get(cr) or _CONDITION_LABEL_MAP.get(cr)
        if mapped:
            data["condition_rating"] = mapped

    # Clamp decimals to valid ranges
    for field, lo, hi, fallback in (
        ("quality_decimal", 1.0, 6.9, 4.0),
        ("condition_decimal", 1.0, 6.9, 3.0),
        ("room_type_confidence", 0.0, 1.0, 0.5),
    ):
        if field in data:
            try:
                data[field] = max(lo, min(hi, float(data[field])))
            except (TypeError, ValueError):
                data[field] = fallback

    # Ensure quality_rating matches quality_decimal tier
    if "quality_decimal" in data and "quality_rating" in data:
        tier = max(1, min(6, int(data["quality_decimal"])))
        expected = f"Q{tier}"
        if data["quality_rating"] != expected:
            data["quality_rating"] = expected

    # Ensure condition_rating matches condition_decimal tier
    if "condition_decimal" in data and "condition_rating" in data:
        tier = max(1, min(6, int(data["condition_decimal"])))
        expected = f"C{tier}"
        if data["condition_rating"] != expected:
            data["condition_rating"] = expected

    # image_quality normalization
    if "image_quality" in data:
        q = str(data["image_quality"]).lower().strip()
        data["image_quality"] = {
            "sharp": "clear", "high": "clear", "good": "clear", "clear": "clear",
            "blurry": "blurry", "blur": "blurry", "low": "blurry", "dark": "blurry",
            "partial": "partial", "obscured": "partial", "cropped": "partial",
        }.get(q, "clear")

    # detected_materials: ensure dict, then normalize sub-fields
    if not isinstance(data.get("detected_materials"), dict):
        data["detected_materials"] = {}

    mats = data["detected_materials"]

    # normalize kitchen/bathroom update_status
    _UPDATE_STATUS_MAP = {
        "not updated": "not_updated", "not-updated": "not_updated", "original": "not_updated",
        "updated": "updated", "update": "updated",
        "remodeled": "remodeled", "renovated": "remodeled", "remodelled": "remodeled",
    }
    for key in ("kitchen_update_status", "bathroom_update_status"):
        if key in mats and mats[key] is not None:
            val = str(mats[key]).lower().strip()
            mats[key] = _UPDATE_STATUS_MAP.get(val, val)
            if mats[key] not in ("not_updated", "updated", "remodeled"):
                mats[key] = None

    # normalize ceiling_height
    if "ceiling_height" in mats and mats["ceiling_height"] is not None:
        val = str(mats["ceiling_height"]).lower().strip()
        _CH_MAP = {
            "low": "low", "short": "low", "below 8": "low",
            "standard": "standard", "normal": "standard", "typical": "standard",
            "vaulted": "vaulted", "cathedral": "vaulted", "angled": "vaulted", "sloped": "vaulted",
            "high": "high", "tall": "high", "10 ft": "high", "10ft": "high",
        }
        mats["ceiling_height"] = _CH_MAP.get(val, val)
        if mats["ceiling_height"] not in ("low", "standard", "vaulted", "high"):
            mats["ceiling_height"] = None

    # notable_features: ensure list
    if not isinstance(data.get("notable_features"), list):
        data["notable_features"] = []

    return data


def parse_and_validate_batch(raw: str, image_ids: list[str]) -> list[RoomAssessment] | None:
    """Parse a JSON array response from a batch VLM call."""
    stripped = _strip_markdown(raw)
    try:
        data_list = json.loads(stripped)
        if not isinstance(data_list, list):
            return None
    except json.JSONDecodeError as e:
        logger.error("Batch JSON parse failed: %s\nRAW (first 500):\n%s", e, raw[:500])
        return None

    results = []
    for i, image_id in enumerate(image_ids):
        data = data_list[i] if i < len(data_list) else {}
        if not isinstance(data, dict):
            data = {}
        data["image_id"] = image_id
        data = _normalize(data)
        try:
            results.append(RoomAssessment.model_validate(data))
        except Exception as e:
            logger.error("Batch pydantic failed for %s: %s", image_id, e)
            results.append(None)
    return results


def parse_and_validate(raw: str, image_id: str) -> RoomAssessment | None:
    stripped = _strip_markdown(raw)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed for %s: %s\nRAW (first 500):\n%s", image_id, e, raw[:500])
        return None

    data["image_id"] = image_id
    data = _normalize(data)

    try:
        return RoomAssessment.model_validate(data)
    except Exception as e:
        logger.error("Pydantic validation failed for %s: %s\nDATA: %s", image_id, e, data)
        return None
