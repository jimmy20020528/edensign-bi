from __future__ import annotations

import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)  # must run before any src imports read os.environ

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

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

            assessments = await assess_images(tmp_paths)
            summaries = aggregate_rooms(assessments)

            if not summaries:
                raise HTTPException(
                    status_code=422,
                    detail="No usable images after analysis. All images may be too blurry or failed processing.",
                )

            actions = generate_suggestions(summaries)
            ranked = prioritize(actions)
            overall_q, overall_c = compute_property_scores(summaries)
            note = coverage_note(summaries)
            report = await build_report(len(files), summaries, ranked, overall_q, overall_c, note)

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return report
