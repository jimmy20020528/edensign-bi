from __future__ import annotations

from src.models.schemas import (
    ConditionRating,
    DetectedMaterials,
    QualityRating,
    RoomAssessment,
    RoomType,
)
from src.pipeline.stage2_aggregation import aggregate_rooms, compute_property_scores, coverage_note


def _assessment(image_id: str, room_type: RoomType, q: float = 3.0, c: float = 3.0,
                skip: bool = False, **mat_kwargs) -> RoomAssessment:
    tier_q = max(1, min(6, int(q)))
    tier_c = max(1, min(6, int(c)))
    return RoomAssessment(
        image_id=image_id,
        room_type=room_type,
        room_type_confidence=0.9,
        quality_rating=QualityRating(f"Q{tier_q}"),
        quality_decimal=q,
        quality_rationale="Test rationale.",
        condition_rating=ConditionRating(f"C{tier_c}"),
        condition_decimal=c,
        condition_rationale="Test rationale.",
        detected_materials=DetectedMaterials(**mat_kwargs),
        notable_features=[],
        image_quality="clear",
        skip=skip,
    )


def test_same_room_multiple_images_produces_one_summary():
    assessments = [
        _assessment("img1", RoomType.KITCHEN),
        _assessment("img2", RoomType.KITCHEN),
        _assessment("img3", RoomType.KITCHEN),
    ]
    summaries = aggregate_rooms(assessments)
    assert len(summaries) == 1
    assert summaries[0].room_type == RoomType.KITCHEN
    assert set(summaries[0].source_image_ids) == {"img1", "img2", "img3"}


def test_skipped_images_excluded():
    assessments = [
        _assessment("img1", RoomType.KITCHEN, skip=True),
        _assessment("img2", RoomType.KITCHEN),
    ]
    summaries = aggregate_rooms(assessments)
    assert len(summaries) == 1
    assert "img1" not in summaries[0].source_image_ids


def test_quality_decimals_averaged():
    assessments = [
        _assessment("img1", RoomType.KITCHEN, q=3.0),
        _assessment("img2", RoomType.KITCHEN, q=4.0),
    ]
    summaries = aggregate_rooms(assessments)
    assert summaries[0].quality_decimal == 3.5
    assert summaries[0].quality_rating.value == "Q3"


def test_coverage_note_missing_rooms():
    summaries = aggregate_rooms([_assessment("img1", RoomType.KITCHEN)])
    note = coverage_note(summaries)
    assert note is not None
    assert "bathroom" in note.lower() or "bedroom" in note.lower()


def test_coverage_note_all_present():
    assessments = [
        _assessment("img1", RoomType.KITCHEN),
        _assessment("img2", RoomType.BATHROOM),
        _assessment("img3", RoomType.LIVING_ROOM),
        _assessment("img4", RoomType.BEDROOM),
    ]
    summaries = aggregate_rooms(assessments)
    assert coverage_note(summaries) is None


def test_empty_input():
    assert aggregate_rooms([]) == []


def test_property_scores_weighted_kitchen_higher():
    # Kitchen (weight 2.0) vs bedroom (weight 1.0)
    assessments = [
        _assessment("k", RoomType.KITCHEN, q=4.0),
        _assessment("b", RoomType.BEDROOM, q=2.0),
    ]
    summaries = aggregate_rooms(assessments)
    overall_q, _ = compute_property_scores(summaries)
    # Weighted: (4.0*2 + 2.0*1) / 3 = 10/3 ≈ 3.3
    assert 3.0 < overall_q < 4.0
