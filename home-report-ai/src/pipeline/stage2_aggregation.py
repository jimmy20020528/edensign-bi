from __future__ import annotations

from src.models.schemas import (
    ConditionRating,
    DetectedMaterials,
    QualityRating,
    RoomAssessment,
    RoomSummary,
    RoomType,
)

# Kitchen and bathrooms carry more weight in property-level Q/C scoring
_ROOM_WEIGHT = {
    RoomType.KITCHEN: 2.0,
    RoomType.BATHROOM: 1.5,
    RoomType.LIVING_ROOM: 1.5,
    RoomType.EXTERIOR: 1.2,
    RoomType.BEDROOM: 1.0,
    RoomType.DINING: 1.0,
    RoomType.HALLWAY: 0.8,
    RoomType.BALCONY: 0.7,
    RoomType.UNKNOWN: 0.5,
}


def _decimal_to_rating(decimal: float, prefix: str) -> str:
    tier = max(1, min(6, int(decimal)))
    return f"{prefix}{tier}"


def _merge_materials(assessments: list[RoomAssessment]) -> DetectedMaterials:
    """Pick the most specific (non-null, non-unknown) value for each material field."""
    result: dict[str, str | None] = {}
    for field in ("countertop", "flooring", "cabinets", "fixtures", "appliances"):
        candidates = [
            getattr(a.detected_materials, field)
            for a in assessments
            if getattr(a.detected_materials, field) not in (None, "unknown")
        ]
        result[field] = candidates[0] if candidates else None
    return DetectedMaterials(**result)


def _merge_features(assessments: list[RoomAssessment]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for a in assessments:
        for f in a.notable_features:
            key = f.lower()
            if key not in seen:
                seen.add(key)
                out.append(f)
    return out


def aggregate_rooms(assessments: list[RoomAssessment]) -> list[RoomSummary]:
    """Group non-skipped RoomAssessments by room_type and compute average Q/C scores."""
    by_room: dict[RoomType, list[RoomAssessment]] = {}
    for a in assessments:
        if a.skip:
            continue
        by_room.setdefault(a.room_type, []).append(a)

    summaries: list[RoomSummary] = []
    for room_type, group in by_room.items():
        avg_q = sum(a.quality_decimal for a in group) / len(group)
        avg_c = sum(a.condition_decimal for a in group) / len(group)
        best = max(group, key=lambda a: a.room_type_confidence)

        summaries.append(RoomSummary(
            room_type=room_type,
            source_image_ids=[a.image_id for a in group],
            quality_rating=QualityRating(_decimal_to_rating(avg_q, "Q")),
            quality_decimal=round(avg_q, 1),
            quality_rationale=best.quality_rationale,
            condition_rating=ConditionRating(_decimal_to_rating(avg_c, "C")),
            condition_decimal=round(avg_c, 1),
            condition_rationale=best.condition_rationale,
            detected_materials=_merge_materials(group),
            notable_features=_merge_features(group),
        ))
    return summaries


def compute_property_scores(summaries: list[RoomSummary]) -> tuple[float, float]:
    """Weighted-average Q and C decimals across all rooms."""
    if not summaries:
        return 4.0, 3.0
    total_w = sum(_ROOM_WEIGHT.get(s.room_type, 1.0) for s in summaries)
    q = sum(s.quality_decimal * _ROOM_WEIGHT.get(s.room_type, 1.0) for s in summaries) / total_w
    c = sum(s.condition_decimal * _ROOM_WEIGHT.get(s.room_type, 1.0) for s in summaries) / total_w
    return round(q, 1), round(c, 1)


def coverage_note(summaries: list[RoomSummary]) -> str | None:
    common = {RoomType.KITCHEN, RoomType.BATHROOM, RoomType.LIVING_ROOM, RoomType.BEDROOM}
    missing = common - {s.room_type for s in summaries}
    if not missing:
        return None
    names = ", ".join(rt.value.replace("_", " ") for rt in sorted(missing, key=lambda x: x.value))
    return f"No photos provided for: {names}. These areas were not assessed."
