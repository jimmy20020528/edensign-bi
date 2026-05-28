from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.models.schemas import ConditionRating, DetectedMaterials, QualityRating, RoomAssessment, RoomType
from src.vlm.client import call_vlm, call_vlm_batch
from src.vlm.prompts import ASSESSMENT_PROMPT, batch_assessment_prompt
from src.vlm.validators import parse_and_validate, parse_and_validate_batch

logger = logging.getLogger(__name__)


async def assess_image(image_path: Path) -> RoomAssessment:
    """Assess a single image with UAD Q/C scoring. Retries once on validation failure."""
    image_id = image_path.stem

    for attempt in range(1, 3):
        raw = await call_vlm(image_path, ASSESSMENT_PROMPT)
        result = parse_and_validate(raw, image_id)
        if result is not None:
            return result
        logger.warning("Stage1: validation failed for %s (attempt %d)", image_id, attempt)

    return RoomAssessment(
        image_id=image_id,
        room_type=RoomType.UNKNOWN,
        room_type_confidence=0.0,
        quality_rating=QualityRating.Q4,
        quality_decimal=4.0,
        quality_rationale="Unable to assess — image could not be analyzed.",
        condition_rating=ConditionRating.C3,
        condition_decimal=3.0,
        condition_rationale="Unable to assess — image could not be analyzed.",
        detected_materials=DetectedMaterials(),
        notable_features=[],
        image_quality="blurry",
        skip=True,
        skip_reason="validation_failed_after_retry",
    )


_SEM = asyncio.Semaphore(5)


async def _assess_with_sem(path: Path) -> RoomAssessment:
    async with _SEM:
        return await assess_image(path)


async def assess_images(image_paths: list[Path]) -> list[RoomAssessment]:
    """Assess all images in a single batch VLM call. Falls back to per-image on failure."""
    if not image_paths:
        return []
    if len(image_paths) == 1:
        return [await assess_image(image_paths[0])]

    image_ids = [p.stem for p in image_paths]
    try:
        raw = await call_vlm_batch(image_paths, batch_assessment_prompt(image_ids))
        results = parse_and_validate_batch(raw, image_ids)
        if results and all(r is not None for r in results):
            return results
        logger.warning("Batch VLM returned incomplete results, falling back to per-image")
    except Exception as exc:
        logger.warning("Batch VLM failed (%s), falling back to per-image", exc)

    return list(await asyncio.gather(*[_assess_with_sem(p) for p in image_paths]))
