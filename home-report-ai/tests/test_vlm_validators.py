from __future__ import annotations

import json

from src.vlm.validators import parse_and_validate


def _valid(**overrides) -> str:
    base = {
        "image_id": "img1",
        "room_type": "kitchen",
        "room_type_confidence": 0.95,
        "quality_rating": "Q3",
        "quality_decimal": 3.4,
        "quality_rationale": "Granite countertops and semi-custom cabinetry consistent with Q3.",
        "condition_rating": "C3",
        "condition_decimal": 3.2,
        "condition_rationale": "Normal wear commensurate with age.",
        "detected_materials": {
            "countertop": "granite",
            "flooring": "solid_hardwood",
            "cabinets": "semi_custom",
            "fixtures": "upgraded",
            "appliances": "upgraded_stainless",
        },
        "notable_features": ["island"],
        "image_quality": "clear",
        "skip": False,
        "skip_reason": None,
    }
    base.update(overrides)
    return json.dumps(base)


def test_valid_json_parses():
    result = parse_and_validate(_valid(), "img1")
    assert result is not None
    assert result.room_type.value == "kitchen"
    assert result.quality_rating.value == "Q3"
    assert result.condition_rating.value == "C3"
    assert result.detected_materials.countertop == "granite"


def test_image_id_overridden_by_caller():
    result = parse_and_validate(_valid(image_id="wrong"), "correct_id")
    assert result is not None
    assert result.image_id == "correct_id"


def test_invalid_json_returns_none():
    result = parse_and_validate("not json {{{", "img1")
    assert result is None


def test_missing_required_field_returns_none():
    data = json.loads(_valid())
    del data["quality_rating"]
    result = parse_and_validate(json.dumps(data), "img1")
    assert result is None


def test_markdown_fences_stripped():
    raw = "```json\n" + _valid() + "\n```"
    result = parse_and_validate(raw, "img1")
    assert result is not None


def test_living_room_space_normalized():
    result = parse_and_validate(_valid(room_type="living room"), "img1")
    assert result is not None
    assert result.room_type.value == "living_room"


def test_quality_rating_corrected_to_match_decimal():
    # decimal says Q4 territory but rating claims Q3 — validator corrects it
    result = parse_and_validate(_valid(quality_decimal=4.2, quality_rating="Q3"), "img1")
    assert result is not None
    assert result.quality_rating.value == "Q4"


def test_decimal_clamped_to_max():
    result = parse_and_validate(_valid(quality_decimal=99.0), "img1")
    assert result is not None
    assert result.quality_decimal <= 6.9


# ── UAD 3.6 new fields ────────────────────────────────────────────────────────

def _valid_with_mats(**mat_overrides) -> str:
    base_mats = {
        "countertop": "granite",
        "flooring": "solid_hardwood",
        "cabinets": "semi_custom",
        "fixtures": "upgraded",
        "appliances": "upgraded_stainless",
    }
    base_mats.update(mat_overrides)
    import json as _json
    base = _json.loads(_valid())
    base["detected_materials"] = base_mats
    return _json.dumps(base)


def test_kitchen_update_status_normalized():
    result = parse_and_validate(_valid_with_mats(kitchen_update_status="not updated"), "img1")
    assert result is not None
    assert result.detected_materials.kitchen_update_status == "not_updated"


def test_bathroom_update_status_remodeled():
    result = parse_and_validate(_valid_with_mats(bathroom_update_status="renovated"), "img1")
    assert result is not None
    assert result.detected_materials.bathroom_update_status == "remodeled"


def test_ceiling_height_cathedral_normalized():
    result = parse_and_validate(_valid_with_mats(ceiling_height="cathedral"), "img1")
    assert result is not None
    assert result.detected_materials.ceiling_height == "vaulted"


def test_ceiling_height_high():
    result = parse_and_validate(_valid_with_mats(ceiling_height="high"), "img1")
    assert result is not None
    assert result.detected_materials.ceiling_height == "high"


def test_unknown_update_status_becomes_none():
    result = parse_and_validate(_valid_with_mats(kitchen_update_status="unknown_val"), "img1")
    assert result is not None
    assert result.detected_materials.kitchen_update_status is None
