from __future__ import annotations

from pathlib import Path

import yaml

from src.models.schemas import RoomSummary, RoomType, UpgradeAction

_LIBRARY_PATH = Path(__file__).resolve().parents[2] / "src/knowledge/suggestions.yaml"
_library: list[dict] | None = None


def _load_library() -> list[dict]:
    global _library
    if _library is None:
        _library = yaml.safe_load(_LIBRARY_PATH.read_text())
    return _library


def _matches(rule: dict, summary: RoomSummary) -> bool:
    trigger = rule.get("trigger", {})

    # room_types: null means all rooms
    room_types = trigger.get("room_types")
    if room_types is not None:
        if summary.room_type.value not in room_types:
            return False

    # material: detected value must be in the allowed list
    material = trigger.get("material")
    if material:
        field = material["field"]
        values = material["values"]
        detected = getattr(summary.detected_materials, field, None)
        if detected not in values:
            return False

    # quality_worse_than: N → triggers if quality tier >= N+1
    q_threshold = trigger.get("quality_worse_than")
    if q_threshold is not None:
        if int(summary.quality_decimal) <= q_threshold:
            return False

    # condition_worse_than: N → triggers if condition tier >= N+1
    c_threshold = trigger.get("condition_worse_than")
    if c_threshold is not None:
        if int(summary.condition_decimal) <= c_threshold:
            return False

    # notable_feature: fires if the feature string appears (case-insensitive) in any notable feature
    notable_feat = trigger.get("notable_feature")
    if notable_feat is not None:
        needle = notable_feat.lower()
        if not any(needle in f.lower() for f in summary.notable_features):
            return False

    return True


def generate_suggestions(summaries: list[RoomSummary]) -> list[UpgradeAction]:
    """Match each RoomSummary against the upgrade library. One action per rule max."""
    library = _load_library()
    actions: list[UpgradeAction] = []

    for rule in library:
        triggered_by = [s for s in summaries if _matches(rule, s)]
        if not triggered_by:
            continue

        all_image_ids = [img for s in triggered_by for img in s.source_image_ids]
        first_room = triggered_by[0].room_type

        actions.append(UpgradeAction(
            action_id=rule["id"],
            text=rule["text"],
            detail=rule["detail"],
            room_type=RoomType(first_room.value),
            quality_impact=rule.get("quality_impact"),
            cost_tier=rule["cost_tier"],
            roi_tier=rule["roi_tier"],
            visual_impact=rule["visual_impact"],
            is_urgent=rule.get("is_urgent", False),
            estimated_cost_range=rule.get("estimated_cost_range"),
            source_image_ids=list(dict.fromkeys(all_image_ids)),
        ))

    return actions
