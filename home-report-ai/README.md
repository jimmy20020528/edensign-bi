# home-report-ai

FastAPI service on **port 8001** that takes property photos and produces a UAD-standard home assessment report — per-room Q (quality) and C (condition) ratings, plus prioritized suggestions.

## Pipeline (5 stages)

```
images
  → [Stage 1: VLM perception]      Gemini/OpenAI describes each image; no value judgments
  → [Stage 2: aggregate]           Pure Python — group images by room, dedup, resolve contradictions
  → [Stage 3: rule-based suggestions]   Pure Python — match RoomFactSheet against suggestions.yaml
  → [Stage 4: prioritize]          Pure Python — score + bucket (must_do / recommended / optional)
  → [Stage 5: report]              Template + LLM polish for final rationale
```

**Core invariant**: VLM only describes. Rules make judgments. LLM only polishes. Stages 2, 3, 4 call NO LLMs.

## Endpoints

| Method | Path      | Purpose                                              |
|--------|-----------|------------------------------------------------------|
| GET    | `/health` | Liveness check                                        |
| POST   | `/report` | Multipart photo upload (1–30 images) → FinalReport   |

## Running locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# fill in:
#   GEMINI_API_KEY=...
#   VLM_PROVIDER=gemini  (or openai)
#   GEMINI_MODEL=gemini-2.5-pro  (or gemini-2.5-flash)
#   LOG_LEVEL=INFO

uvicorn src.api.main:app --port 8001
```

Health:
```bash
curl localhost:8001/health
```

Quick test from CLI:
```bash
python test_run.py path/to/image1.jpg path/to/image2.jpg
```

Single-image VLM debug (raw model output + validation result):
```bash
python debug_vlm.py path/to/image.jpg
```

## Files

```
home-report-ai/
├── src/
│   ├── api/main.py             FastAPI app
│   ├── models/schemas.py       Pydantic models (FinalReport, RoomFactSheet, ...)
│   ├── vlm/
│   │   ├── client.py           VLM provider abstraction (Gemini + OpenAI)
│   │   ├── prompts.py          ASSESSMENT_PROMPT, POLISH_PROMPT
│   │   └── validators.py       Parse + validate VLM JSON output
│   ├── knowledge/
│   │   └── suggestions.yaml    All possible suggestions with evidence triggers
│   └── pipeline/
│       ├── stage1_perception.py
│       ├── stage2_aggregation.py
│       ├── stage3_suggestions.py
│       ├── stage4_prioritize.py
│       └── stage5_report.py
├── tests/                       pytest suite (stage2/3/4 + validators)
├── prompts/                     (reserved for future prompt versions)
├── debug_vlm.py                 CLI debugger for a single image
├── test_run.py                  CLI smoke test against running service
├── pytest.ini
├── requirements.txt
└── .env.example
```

## DO NOT BREAK

1. **Stages 2, 3, 4 are LLM-free.** Adding an LLM call here breaks the pipeline contract.
2. **Every suggestion has an evidence chain.** A suggestion in the final report must trace to a triggering observation, which must trace to a source `image_id`.
3. **Safety-critical items (`is_safety_critical=true`) always land in `must_do`.** Don't let scoring override that.
4. **No structural recommendations.** Load-bearing walls, plumbing/electrical layout changes — not in scope.
5. **VLM output must be facts, not judgments.** A description says "the countertop is laminate"; a judgment says "the countertop should be replaced." The latter is forbidden in VLM output and must come from rules.

## Status

| Component                                         | Status      |
|---------------------------------------------------|-------------|
| Stage 1 perception (Gemini + OpenAI clients)      | Done        |
| Stage 2 aggregation                               | Done        |
| Stage 3 rule engine + suggestions.yaml (~30 seed) | Done        |
| Stage 4 scoring + bucketing                       | Done        |
| Stage 5 template + LLM polish                     | Done        |
| `/report` endpoint                                | Done        |
| Tests (stage2/3/4 + validators)                   | Done        |
| Expand suggestions.yaml to 50–100 entries         | Next        |
| Accept `room_type` hints from cv-models           | Not started |

## Notes

- Max 30 images per `/report` call.
- Allowed file types: jpg, jpeg, png, webp.
- 422 if no images yield usable analysis (all blurry / VLM failures).
