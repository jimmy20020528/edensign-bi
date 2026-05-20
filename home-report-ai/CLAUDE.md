# Home Report AI — CLAUDE.md

## Core Architecture (DO NOT BREAK)

**VLM only describes. Rules make judgments. LLM only polishes.**

1. VLM outputs only objective facts — no suggestions, no "should", no value judgments.
2. Every suggestion must come from `src/knowledge/suggestions.yaml`. No other path generates suggestions.
3. Every suggestion must carry an evidence chain: `triggered_by_observations` → `source_image_ids`.
4. Stages 2, 3, 4 call NO LLMs. Pure Python computation only.
5. Safety-critical items (is_safety_critical=true) always go to must_do, ignore scoring.
6. No structural recommendations (load-bearing walls, moving plumbing/electrical, changing layout).

## Push Back Checklist

Before implementing any change, ask:
1. Does this make VLM output a judgment ("should") instead of a fact ("is")? → **REJECT**
2. Does this add a suggestion outside suggestions.yaml? → **REJECT**
3. Does this break the evidence chain (suggestion → observation → image_id)? → **REJECT**
4. Is this a structural/load-bearing/plumbing/electrical layout change? → **REJECT**
5. Does Stage 2, 3, or 4 now call an LLM? → **REJECT**

## Data Flow (fixed)

```
images → [Stage1: VLM describe] → ImageAnalysis
       → [Stage2: aggregate, pure Python] → RoomFactSheet
       → [Stage3: rule match yaml] → Suggestion (with evidence)
       → [Stage4: score + bucket, pure Python] → PrioritizedList
       → [Stage5: template + LLM polish] → FinalReport
```

## General Coding Guidelines

- Minimum code that solves the problem. No speculative features.
- Only change what the task requires.
- No error handling for impossible scenarios.
- No abstractions for single-use code.
- State assumptions before implementing. Ask when unclear.

## Current Progress

**Completed (2026-05-14):**
- [x] `src/models/schemas.py` — all Pydantic models
- [x] `src/vlm/client.py` — Claude + OpenAI, retry, timeout, never raises
- [x] `src/vlm/prompts.py` — PERCEPTION_PROMPT, POLISH_PROMPT
- [x] `src/vlm/validators.py` — parse + validate VLM JSON output
- [x] `src/pipeline/stage1_perception.py` — parallel image analysis
- [x] `src/pipeline/stage2_aggregation.py` — room grouping, dedup, contradiction resolution
- [x] `src/knowledge/suggestions.yaml` — ~30 seed suggestions
- [x] `src/pipeline/stage3_suggestions.py` — rule engine, evidence chain
- [x] `src/pipeline/stage4_prioritize.py` — scoring formula, bucket assignment
- [x] `src/pipeline/stage5_report.py` — template + LLM polish
- [x] `src/api/main.py` — FastAPI endpoint `/report`
- [x] Tests: stage2, stage3, stage4, validators

**Next:**
- [ ] Expand suggestions.yaml to 50-100 entries
- [ ] Accept `room_type` and `room_groups` hints (skip Stage 1 when cv-models is integrated)
- [ ] More integration tests (real VLM call against fixture images)
