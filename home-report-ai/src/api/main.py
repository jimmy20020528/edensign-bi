from __future__ import annotations

import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)  # must run before any src imports read os.environ

import logging
import time

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

_log = logging.getLogger("timing")

from src.models.schemas import FinalReport
from src.pipeline.stage1_perception import assess_images
from src.pipeline.stage2_aggregation import aggregate_rooms, compute_property_scores, coverage_note
from src.pipeline.stage3_suggestions import generate_suggestions
from src.pipeline.stage4_prioritize import prioritize
from src.pipeline.stage5_report import build_report

app = FastAPI(title="Home Report AI", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/report", response_model=FinalReport)
async def generate_report(files: list[UploadFile] = File(...)) -> FinalReport:
    if not files:
        raise HTTPException(status_code=400, detail="No images provided.")
    if len(files) > 30:
        raise HTTPException(status_code=400, detail="Maximum 30 images per request.")

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_paths: list[Path] = []
            for i, upload in enumerate(files):
                suffix = Path(upload.filename or "").suffix.lower() or ".jpg"
                if suffix not in _ALLOWED_SUFFIXES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unsupported file type: {suffix}",
                    )
                dest = Path(tmpdir) / f"img_{i:03d}{suffix}"
                dest.write_bytes(await upload.read())
                tmp_paths.append(dest)

            t0 = time.perf_counter()
            assessments = await assess_images(tmp_paths)
            t1 = time.perf_counter()
            summaries = aggregate_rooms(assessments)
            t2 = time.perf_counter()

            if not summaries:
                raise HTTPException(
                    status_code=422,
                    detail="No usable images after analysis. All images may be too blurry or failed processing.",
                )

            actions = generate_suggestions(summaries)
            t3 = time.perf_counter()
            ranked = prioritize(actions)
            t4 = time.perf_counter()
            overall_q, overall_c = compute_property_scores(summaries)
            note = coverage_note(summaries)
            report = await build_report(len(files), summaries, ranked, overall_q, overall_c, note)
            t5 = time.perf_counter()

            _log.warning(
                "TIMING n=%d | stage1_vlm=%.1fs | stage2_agg=%.2fs | "
                "stage3_rules=%.2fs | stage4_rank=%.2fs | stage5_polish=%.1fs | total=%.1fs",
                len(files), t1-t0, t2-t1, t3-t2, t4-t3, t5-t4, t5-t0,
            )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return report
