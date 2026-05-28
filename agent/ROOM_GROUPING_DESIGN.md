# Edensign Self-Hosted CV Modules — Design Doc v2

**Owner**: Jimmy  
**Status**: Planned for implementation week of 2026-05-25  
**Last updated**: 2026-05-18

---

## Why this exists

Edensign's `home-report-ai` currently uses a VLM (Claude/GPT-4o) for Stage 1
(room type classification) and Stage 3 (detail description / Q/C rating).
Long-term, calling VLMs on every photo every time is:

1. **Expensive** — ~$0.30–1.00 per 30-photo property; thousands of dollars/month at scale.
2. **Slow** — ~6-8s per image; 30 images = several minutes.
3. **Externally dependent** — model versions drift, prices change, rate limits apply,
   data flows out of Edensign.

We replace **Stage 1** with a self-hosted DINOv2-based classifier and add a new
**Room Instance Grouping** module — both use the same DINOv2 backbone (one model,
two heads). **Stage 3 (detail description) keeps the VLM** because open-vocabulary
material/feature description is genuinely VLM-class work; replacing it would
require training a multi-label detector with thousands of labels — out of scope.

**Net effect**: VLM calls drop ~90% (per-image → per-room), key classification
no longer depends on external API, and the same backbone unlocks instance grouping
(prerequisite for multi-view staging).

---

## Task 1: Room Type Classifier

### Goal
Replace the VLM call in home-report-ai Stage 1. Input: 1 photo. Output: one of
~13 room types (`bathroom`, `kitchen`, `bedroom`, `living`, `dining`, `hallway`,
`home_office`, `balcony`, `outdoor`, `theatre`, `kidsroom`, `living_bedroom`,
`living_dining`).

### Implementation: two-phase

#### Phase 1A — Prototype matching (zero-shot, ~1 day)

```
Once (offline):
  For each room type, collect N reference images (we already have these).
  Encode each with DINOv2 → CLS embedding (768-d).
  Average embeddings per class → one prototype vector per class.
  Save prototypes to disk.

Per request:
  New image → DINOv2 → CLS embedding (normalized).
  Cosine similarity vs each of K prototypes.
  argmax → predicted class.
  Top-2 margin used as confidence; low confidence flag for VLM fallback.
```

**Training data: already available.** `staging/test_images_empty/` and
`staging/v5_staging/` are each pre-organized into 13 class folders with 7–9
images per class (see Appendix). That's enough for prototype computation.

**Expected accuracy**: 75–85% on production photos. Acceptable for MVP;
ambiguous classes (`living_bedroom`, `living_dining`) will be the soft spots.

**Cost at inference**: ~50ms per image on CPU (DINOv2-base, 224×224).
Effectively free vs. VLM call.

#### Phase 1B — Linear probe (if 1A < 85% accuracy on real listings)

```
Offline:
  Expand training set to 50–200 images per class (mix v5_staging + scraped MLS
  photos + label corrections from 1A misclassifications).
  Encode all with DINOv2 → 768-d feature matrix.
  Fit sklearn LogisticRegression (multinomial, L2-regularized).
  Save the fitted classifier (~5 KB).

Per request:
  DINOv2 CLS embedding → linear classifier → softmax over K classes.
```

**Expected accuracy**: 88–94% (linear probes on DINOv2 in the original paper hit
85%+ on ImageNet; on a 13-class domain task with same-distribution data they
typically do better).

**Effort**: ~2 days, mostly data expansion + labeling 1A's failure cases.

**Decision rule**: ship 1A. If on the first 50 real properties' photos
accuracy < 85%, escalate to 1B. Don't pre-build 1B — it'll waste data prep
effort if 1A is already good enough.

### VLM fallback

When prototype-matching confidence is low (top-2 margin < 0.05, say), call
GPT-4o-mini on that single image. Budget cap ensures this stays at < 5% of
photos in steady state.

---

## Task 2: Room Instance Grouping

### Goal
Given N photos of the same property, group photos that show the *same physical
room*. Output: groups like `[{id: "bathroom_1", photos: [001, 006]},
{id: "bathroom_2", photos: [003, 005, 007]}, ...]`.

This is hard because:
- Real estate photographers shoot multiple angles per room (large viewpoint changes).
- Builder-grade homes have **near-identical twin bathrooms / bedrooms** (same
  tile, same fixtures). Pure embedding similarity will merge these falsely.
- Mirrors in bathrooms reflect the same scene with geometric inconsistencies.

### Implementation: two-step (DINOv2 only, no second model)

DINOv2 outputs two kinds of features from the same forward pass:

- **CLS token** (1 × 768): a global descriptor of the whole image.
- **Patch tokens** (256 × 768 at 224×224 input): one descriptor per 14×14
  image patch.

We use both. CLS for fast candidate generation, patch features for geometric
verification.

#### Step A — CLS-embedding candidate generation (~5 minutes implementation)

```
For each photo: extract DINOv2 CLS embedding (normalized).
Pairwise cosine similarity matrix (N × N).
Threshold = 0.85 → candidate same-room pairs.
Optional: also constrain to same predicted room_type from Task 1.
```

This is fast (one forward pass per image, ~50ms) and catches the obvious cases.
But it'll falsely merge twin rooms with identical finishes — that's what Step B
fixes.

#### Step B — Patch feature mutual best match verification (~15 minutes implementation)

For each Step-A candidate pair (A, B):

```
1. Extract patch features:
   A_patches: 256 × 768 (one vector per spatial location in A)
   B_patches: 256 × 768

2. Compute 256 × 256 cosine similarity matrix.

3. For each patch in A, find the best-matching patch in B.
4. For each patch in B, find the best-matching patch in A.
5. Count "mutual best matches" — pairs (i, j) where:
       i's best match in B is j, AND
       j's best match in A is i.
6. If mutual_match_count > T (threshold, ~30 expected on a same-room pair),
   the pair is confirmed same-room.
```

**Why this works for twin rooms**: Two literally identical bathrooms still have
*different camera poses*. A patch showing the upper-left corner of one room
cannot mutual-match a patch in the other room's lower-right — because in the
other photo, that physical position was captured by a *different* patch (or
isn't visible at all). Mutual best matches require **co-visibility**, which
twin rooms lack.

**Why this works for big viewpoint changes within the same room**: DINOv2
patch features are pose-robust (the original paper Figure 10 demonstrates
cross-pose, cross-domain matching). A sink shot from the left will have patch
features that semantically match the same sink shot from the right.

#### Step C — Clustering

After steps A and B produce a "confirmed same-room" graph (edges between photos),
run a connected-components algorithm to collapse into final groups. No HDBSCAN
needed — graph traversal is sufficient and deterministic.

### Evaluation

Build ground-truth dataset:
- 30 real property photo sets (Lawrence to source from MLS scrape).
- Hand-label expected groupings.
- Metric: clustering F1 (precision and recall of pair-grouping decisions).

Tune threshold T from Step B on this dataset. Initial guess: T = 25-40 mutual
matches out of max 256.

### Performance budget

For a 30-photo property:
- Step A: 30 forward passes × 50ms = 1.5s
- Step B: ~30-60 candidate pairs × ~5ms each = 0.3s
- Total: < 2 seconds, well within pipeline budget.

---

## Architecture

```
agent/
├── tools/
│   ├── server.py                 # existing port-8002 tool service
│   ├── dinov2_backbone.py        # NEW — single DINOv2 forward, returns (CLS, patches)
│   ├── room_classifier.py        # NEW — Task 1: prototypes + (optional) linear probe
│   └── room_grouping.py          # NEW — Task 2: pairwise A+B + clustering
├── prototypes/
│   ├── prototypes.npy            # NEW — K × 768 (built offline from staging/)
│   └── class_names.json          # NEW — index → class_name
└── models/
    └── (DINOv2 cached via torch.hub on first run, ~330 MB)
```

### Service integration

Modify `tools/server.py` (port 8002) `/pipeline/run`:

```
Before:
  files → home-report-ai (does its own VLM Stage 1 + 2 + 3) → report

After:
  files → room_classifier (Task 1, our own service) → per-photo room_type
       → room_grouping (Task 2, our own service) → per-instance groups
       → home-report-ai with hints (use these labels + groupings, skip your Stage 1)
       → report
```

`home-report-ai` Stage 1 becomes optional — controlled by a query param
(`skip_stage1=true`) so we can A/B compare during rollout.

---

## Deployment

- DINOv2 model: load once at service start, hold in memory (~600 MB RAM with
  base; ~330 MB for small). One-process service is fine for MVP; if QPS rises,
  Gunicorn with 2 workers.
- CPU is sufficient for current load (one property at a time, < 30 photos).
  If batching needed: switch to CUDA on RunPod (same code, just `model.cuda()`).
- Prototypes: precomputed once, committed to repo (~40 KB).
- VLM fallback: same OPENAI_API_KEY already used in tool service.

---

## Rollout plan

| Day | Task |
|-----|------|
| 1 | DINOv2 backbone module; compute prototypes from `staging/v5_staging/` |
| 1 | Phase 1A classifier; eval on `test_images_empty/` held-out (target ≥ 80%) |
| 2 | Task 2 Step A (CLS clustering); manual sanity on test sets |
| 2 | Task 2 Step B (patch matching); same sanity tests |
| 3 | Wire into `/pipeline/run`; update home-report-ai to accept `skip_stage1` |
| 3 | Frontend: render per-instance cards (master bath vs powder room) |
| 4 | Build 30-property ground-truth dataset (Lawrence) |
| 5 | Threshold tuning; ship to demo |

If 1A < 85% on day 1 eval → Phase 1B added in week 2.

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| Phase 1A accuracy too low for production | Phase 1B linear probe, ~2 days extra |
| Twin rooms still false-merge after Step B | Add GPT-4o "are these the same room?" fallback for ambiguous pairs (low budget) |
| Photographer shoots two rooms from the same doorway (overlap photo) | Confidence flag; user can manually re-group in UI (Day-2 feature) |
| DINOv2 patch features too coarse (14px) for tile-pattern discrimination | Up-sample input to 448×448 → 32×32 patches; cost doubles but still fast |
| Class imbalance in training (only 7-9 images per class right now) | Phase 1A doesn't need much; for 1B, augment with scraped MLS data |

---

## Open questions for George

1. Do we want a UI to let users manually re-group photos when our auto-grouping
   makes a mistake? (Adds trust; could be Day-2.)
2. Long-term: should the classifier handle commercial property types (office,
   retail), or stay residential-only?
3. Acceptable VLM fallback rate? (We can dial threshold to trade accuracy
   vs. VLM cost.)

---

## Appendix: Available labeled training data

Pre-organized under `staging/` — **immediately usable for prototype computation
and Phase 1A evaluation:**

`test_images_empty/`: 13 classes × 7-9 images each (~103 total)
`staging/v5_staging/`: 13 classes × 7-9 images each (~103 total)
`staging/v3_staging/`: Bedroom (9), Living (8) only — legacy

Total: ~210 hand-organized images across 13 classes for Phase 1A.
For Phase 1B, plan to scrape MLS for additional ~50/class.

## Decision log

- **2026-05-18 (v1)**: Picked DINOv2 over CLIP/SIFT/COLMAP after discussion.
- **2026-05-18 (v2)**: Scoped to TWO modules (classifier + grouping), not one
  combined service. Stage 1 of home-report-ai becomes self-hosted; Stage 3 keeps
  VLM. Same DINOv2 backbone serves both modules (CLS for classification,
  patches for grouping verification).
