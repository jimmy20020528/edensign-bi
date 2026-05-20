from __future__ import annotations

from src.models.schemas import (
    ConditionRating,
    DetectedMaterials,
    QualityRating,
    RoomSummary,
    RoomType,
)
from src.pipeline.stage3_suggestions import generate_suggestions


def _summary(room_type=RoomType.KITCHEN, q: float = 4.0, c: float = 3.0,
             notable_features=None, **mat_kwargs) -> RoomSummary:
    tier_q = max(1, min(6, int(q)))
    tier_c = max(1, min(6, int(c)))
    return RoomSummary(
        room_type=room_type,
        source_image_ids=["img1"],
        quality_rating=QualityRating(f"Q{tier_q}"),
        quality_decimal=q,
        quality_rationale="Test.",
        condition_rating=ConditionRating(f"C{tier_c}"),
        condition_decimal=c,
        condition_rationale="Test.",
        detected_materials=DetectedMaterials(**mat_kwargs),
        notable_features=notable_features or [],
    )


def test_no_triggers_produces_no_actions():
    # Q3 kitchen with high-end materials — nothing should trigger
    s = _summary(q=3.0, countertop="quartz", cabinets="custom_inset",
                 flooring="solid_hardwood", fixtures="designer")
    assert generate_suggestions([s]) == []


def test_laminate_countertop_triggers_upgrade():
    s = _summary(room_type=RoomType.KITCHEN, countertop="laminate")
    actions = generate_suggestions([s])
    ids = [a.action_id for a in actions]
    assert "kitchen_countertop_laminate" in ids


def test_stock_cabinets_trigger_upgrade():
    s = _summary(room_type=RoomType.KITCHEN, cabinets="stock_flat_panel")
    actions = generate_suggestions([s])
    ids = [a.action_id for a in actions]
    assert "kitchen_cabinets_stock" in ids


def test_c4_condition_triggers_repaint():
    s = _summary(room_type=RoomType.BEDROOM, q=3.0, c=4.2)
    actions = generate_suggestions([s])
    ids = [a.action_id for a in actions]
    assert "condition_c4_repaint" in ids


def test_c5_condition_triggers_urgent():
    s = _summary(room_type=RoomType.LIVING_ROOM, q=3.0, c=5.1)
    actions = generate_suggestions([s])
    urgent = [a for a in actions if a.is_urgent]
    assert len(urgent) >= 1


def test_c3_does_not_trigger_c4_rule():
    # C3 (decimal 3.5) should NOT trigger condition_worse_than: 3 rule
    s = _summary(room_type=RoomType.BEDROOM, q=3.0, c=3.5)
    actions = generate_suggestions([s])
    ids = [a.action_id for a in actions]
    assert "condition_c4_repaint" not in ids


def test_source_image_ids_populated():
    s = _summary(room_type=RoomType.KITCHEN, countertop="laminate")
    s = s.model_copy(update={"source_image_ids": ["img1", "img2"]})
    actions = generate_suggestions([s])
    laminate_action = next(a for a in actions if a.action_id == "kitchen_countertop_laminate")
    assert "img1" in laminate_action.source_image_ids
    assert "img2" in laminate_action.source_image_ids


# ── UAD 3.6 update_status rules ──────────────────────────────────────────────

def test_kitchen_not_updated_triggers():
    s = _summary(room_type=RoomType.KITCHEN, kitchen_update_status="not_updated")
    ids = [a.action_id for a in generate_suggestions([s])]
    assert "kitchen_not_updated" in ids


def test_bathroom_not_updated_triggers():
    s = _summary(room_type=RoomType.BATHROOM, bathroom_update_status="not_updated")
    ids = [a.action_id for a in generate_suggestions([s])]
    assert "bathroom_not_updated" in ids


def test_kitchen_updated_status_does_not_fire_not_updated_rule():
    s = _summary(room_type=RoomType.KITCHEN, kitchen_update_status="updated")
    ids = [a.action_id for a in generate_suggestions([s])]
    assert "kitchen_not_updated" not in ids


# ── notable_feature rules ─────────────────────────────────────────────────────

def test_water_stain_triggers_urgent():
    s = _summary(notable_features=["water stain on ceiling"])
    actions = generate_suggestions([s])
    ids = [a.action_id for a in actions]
    assert "water_stain_urgent" in ids
    urgent = [a for a in actions if a.action_id == "water_stain_urgent"]
    assert urgent[0].is_urgent is True


def test_mold_triggers_urgent():
    s = _summary(notable_features=["mold visible in corner"])
    actions = generate_suggestions([s])
    ids = [a.action_id for a in actions]
    assert "mold_urgent" in ids


def test_peeling_triggers_urgent():
    s = _summary(notable_features=["peeling paint on walls"])
    actions = generate_suggestions([s])
    ids = [a.action_id for a in actions]
    assert "peeling_paint" in ids


def test_no_notable_feature_no_water_stain():
    s = _summary(notable_features=[])
    ids = [a.action_id for a in generate_suggestions([s])]
    assert "water_stain_urgent" not in ids
