from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


class RoomType(str, Enum):
    KITCHEN = "kitchen"
    BEDROOM = "bedroom"
    BATHROOM = "bathroom"
    LIVING_ROOM = "living_room"
    DINING = "dining"
    HALLWAY = "hallway"
    BALCONY = "balcony"
    EXTERIOR = "exterior"
    UNKNOWN = "unknown"


class QualityRating(str, Enum):
    Q1 = "Q1"  # Architect-designed, unique
    Q2 = "Q2"  # High-end custom
    Q3 = "Q3"  # Above builder-grade
    Q4 = "Q4"  # Standard builder-grade
    Q5 = "Q5"  # Economy
    Q6 = "Q6"  # Below minimum standards


class ConditionRating(str, Enum):
    C1 = "C1"  # New/never occupied
    C2 = "C2"  # Like new / fully renovated
    C3 = "C3"  # Normal wear, well maintained
    C4 = "C4"  # Minor deferred maintenance
    C5 = "C5"  # Obvious deterioration
    C6 = "C6"  # Significant damage


class DetectedMaterials(BaseModel):
    countertop: Optional[str] = None
    flooring: Optional[str] = None
    cabinets: Optional[str] = None
    fixtures: Optional[str] = None
    appliances: Optional[str] = None
    # UAD 3.6 update-status fields (per kitchen/bathroom)
    kitchen_update_status: Optional[Literal["not_updated", "updated", "remodeled"]] = None
    bathroom_update_status: Optional[Literal["not_updated", "updated", "remodeled"]] = None
    # Ceiling height — visual quality indicator
    ceiling_height: Optional[Literal["low", "standard", "vaulted", "high"]] = None


class RoomAssessment(BaseModel):
    """Stage 1 output: UAD-calibrated assessment of one image."""
    image_id: str
    room_type: RoomType
    room_type_confidence: float = Field(..., ge=0, le=1)

    quality_rating: QualityRating
    quality_decimal: float = Field(..., ge=1.0, le=6.9)
    quality_rationale: str

    condition_rating: ConditionRating
    condition_decimal: float = Field(..., ge=1.0, le=6.9)
    condition_rationale: str

    detected_materials: DetectedMaterials = Field(default_factory=DetectedMaterials)
    notable_features: list[str] = Field(default_factory=list)

    image_quality: Literal["clear", "blurry", "partial"]
    skip: bool = False
    skip_reason: Optional[str] = None


class RoomSummary(BaseModel):
    """Stage 2 output: aggregated Q/C assessment for one room type."""
    room_type: RoomType
    source_image_ids: list[str]

    quality_rating: QualityRating
    quality_decimal: float
    quality_rationale: str

    condition_rating: ConditionRating
    condition_decimal: float
    condition_rationale: str

    detected_materials: DetectedMaterials
    notable_features: list[str]


class UpgradeAction(BaseModel):
    """Stage 3-4 output: one actionable upgrade recommendation."""
    action_id: str
    text: str
    detail: str
    room_type: Optional[RoomType] = None
    quality_impact: Optional[str] = None      # e.g., "Q4 → Q3"
    cost_tier: Literal["low", "medium", "high"]
    roi_tier: Literal["low", "medium", "high"]
    visual_impact: int = Field(..., ge=1, le=5)
    is_urgent: bool = False
    estimated_cost_range: Optional[str] = None
    source_image_ids: list[str] = Field(default_factory=list)

    # Set after Stage 4
    priority_score: Optional[float] = None
    priority_bucket: Optional[Literal["must_do", "recommended", "optional"]] = None


class FinalReport(BaseModel):
    """Stage 5 output: complete UAD-calibrated property assessment report."""
    overall_quality_rating: QualityRating
    overall_quality_decimal: float
    overall_condition_rating: ConditionRating
    overall_condition_decimal: float
    overall_narrative: str

    rooms: list[RoomSummary]

    must_do: list[UpgradeAction]
    recommended: list[UpgradeAction]
    optional: list[UpgradeAction]

    stats: dict
    coverage_note: Optional[str] = None
