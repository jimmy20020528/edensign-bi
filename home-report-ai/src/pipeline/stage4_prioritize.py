from __future__ import annotations

from src.models.schemas import UpgradeAction

_ROI_WEIGHT = {"high": 3, "medium": 2, "low": 1}
_COST_PENALTY = {"low": 0, "medium": 1, "high": 2}


def _score(a: UpgradeAction) -> float:
    return (
        _ROI_WEIGHT[a.roi_tier] * 2.0
        + a.visual_impact * 1.5
        - _COST_PENALTY[a.cost_tier] * 0.5
    )


def _bucket(a: UpgradeAction) -> str:
    if a.is_urgent:
        return "must_do"
    score = a.priority_score or 0.0
    if score >= 9.0:
        return "recommended"
    if score >= 6.0:
        return "recommended"
    return "optional"


def prioritize(actions: list[UpgradeAction]) -> list[UpgradeAction]:
    """Score, bucket, and sort upgrade actions. Returns a new list."""
    scored = []
    for a in actions:
        s = a.model_copy(update={"priority_score": _score(a)})
        s = s.model_copy(update={"priority_bucket": _bucket(s)})
        scored.append(s)

    def sort_key(a: UpgradeAction) -> tuple[int, float]:
        return (0 if a.is_urgent else 1, -(a.priority_score or 0.0))

    return sorted(scored, key=sort_key)
