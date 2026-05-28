from __future__ import annotations

from src.models.schemas import RoomType, UpgradeAction
from src.pipeline.stage4_prioritize import prioritize


def _action(aid="test", cost_tier="low", roi_tier="high",
            visual_impact=4, is_urgent=False) -> UpgradeAction:
    return UpgradeAction(
        action_id=aid,
        text="Test action",
        detail="Test detail",
        room_type=RoomType.KITCHEN,
        cost_tier=cost_tier,
        roi_tier=roi_tier,
        visual_impact=visual_impact,
        is_urgent=is_urgent,
    )


def test_urgent_always_must_do():
    a = _action(is_urgent=True, roi_tier="low", visual_impact=1)
    result = prioritize([a])
    assert result[0].priority_bucket == "must_do"


def test_urgent_first_in_sorted_order():
    normal = _action("normal", roi_tier="high", visual_impact=5)
    urgent = _action("urgent", is_urgent=True, roi_tier="low", visual_impact=1)
    result = prioritize([normal, urgent])
    assert result[0].action_id == "urgent"


def test_high_roi_high_visual_goes_recommended():
    a = _action(roi_tier="high", visual_impact=5, cost_tier="low")
    result = prioritize([a])
    assert result[0].priority_bucket == "recommended"


def test_low_roi_low_visual_high_cost_goes_optional():
    a = _action(roi_tier="low", visual_impact=1, cost_tier="high")
    result = prioritize([a])
    assert result[0].priority_bucket == "optional"


def test_scores_assigned():
    a = _action()
    result = prioritize([a])
    assert result[0].priority_score is not None
    assert result[0].priority_score > 0


def test_sorted_by_score_within_bucket():
    low_score = _action("low", roi_tier="medium", visual_impact=2, cost_tier="high")
    high_score = _action("high", roi_tier="high", visual_impact=5, cost_tier="low")
    result = prioritize([low_score, high_score])
    # Both recommended; high_score should come first
    assert result[0].action_id == "high"
