# cv-models — CLAUDE.md

Self-hosted CV modules for Edensign. Two tasks, one DINOv2 backbone.

## Why this module exists

`home-report-ai` Stage 1 currently uses a VLM (Claude/GPT-4o) to classify room
types. At scale that's slow and expensive. This module replaces Stage 1 with a
local DINOv2-based classifier and adds room **instance** grouping (which
photos show the same physical room), which Stage 1 cannot do.

Stage 3 of `home-report-ai` (open-vocabulary material/feature description)
**stays on VLM**. Don't replace it.

### Empty vs furnished rooms — both must work

Edensign sees two very different photo distributions:

- **Furnished**: owner-occupied or already-staged. Cabinets, beds, decor — lots
  of visual cues for what a room is.
- **Empty**: post-move-out, builder-new, or pre-staging. Just walls, floors,
  fixed plumbing/electrical. A bathroom and a small kitchen can look nearly
  identical with no furniture.

A single classifier trained on one distribution **fails badly on the other**.
The module is therefore designed with three classifiers (occupancy detector
+ furnished room-type + empty room-type) — see rule 8 below.

## Core Architecture (DO NOT BREAK)

1. **DINOv2 is frozen.** Never fine-tune the backbone. We use `facebook/dinov2-base`
   via HuggingFace `transformers`. Only the linear classifier head is learned.

2. **Two outputs from one forward pass.**
   - CLS token (1 × 768) → image-level classification AND instance candidate matching
   - Patch tokens (256 × 768 at 224×224 input) → instance verification via mutual
     best match
   Don't run two separate forward passes.

3. **Embeddings are always L2-normalized before storage or comparison.**
   Cosine similarity assumes unit-norm vectors. Skipping normalization silently
   poisons the classifier and the grouping.

4. **Class index order is fixed.** `CLASS_NAMES` in `scripts/extract_embeddings.py`
   defines the canonical index → name mapping. Production classifier maps by
   integer index. Changing the list order without re-training breaks every
   downstream consumer.

5. **Training data must be real listing photos, not staging renders.**
   `staging/v5_staging/` and other AI-generated outputs are NOT valid training
   data — they introduce distribution shift between training and production.
   The starter images currently in `data/train/` from `staging/test_images_empty/`
   are real empty-room photos and are OK as a pipeline smoke test, but the
   production model must be trained on real listing photos.

6. **Don't downgrade to DINOv2-small without re-extracting all embeddings.**
   Embeddings from -base and -small are not interchangeable.

7. **No data augmentation in `extract_embeddings.py`.** DINOv2 was trained with
   its own augmentation pipeline; re-augmenting at feature-extraction time
   corrupts the embeddings. If you want augmented training, augment at the
   image step, before DINOv2 sees it, not after.

8. **Occupancy-aware routing — three classifiers, not one.**
   The pipeline runs in this order:
   1. **Occupancy detector** (binary: empty vs furnished, ~98% expected accuracy).
   2. **Room-type classifier**, selected by Step 1's output:
      - `room_type_furnished.pkl` — trained ONLY on furnished photos.
      - `room_type_empty.pkl` — trained ONLY on empty photos.
   The two room-type classifiers share the DINOv2 backbone (single forward pass
   per image), so cost is essentially one classifier's. Do NOT train a single
   merged room-type classifier on mixed empty + furnished data — empirically
   that fails on empty rooms because furniture is the dominant visual cue.
   Cross-contamination of training sets (e.g., empty bathrooms in the furnished
   training folder) silently kills accuracy.

## Directory layout

```
cv-models/
├── .venv/                            # torch + transformers + sklearn (Python 3.9)
├── data/
│   ├── train_occupancy/              # binary task
│   │   ├── empty/*.jpg               # ~200 photos (any room type, no furniture)
│   │   └── furnished/*.jpg           # ~200 photos (any room type, furnished)
│   ├── train_furnished/<class>/*.jpg # 13-class, FURNISHED room photos only
│   ├── train_empty/<class>/*.jpg     # 13-class, EMPTY room photos only
│   ├── train/<class>/*.jpg           # LEGACY — starter images from staging/test_images_empty/.
│   │                                 # Smoke-test only; will be removed once train_empty/ is populated.
│   ├── val/                          # held-out validation (optional, same subfolder convention)
│   ├── test/                         # held-out test (optional)
│   └── grouping_eval/                # NOT YET — per-property folders for Task 2 eval
├── artifacts/
│   ├── embeddings_occupancy.npz      # cached DINOv2 CLS features, occupancy task
│   ├── embeddings_furnished.npz      # cached DINOv2 CLS features, furnished classifier
│   ├── embeddings_empty.npz          # cached DINOv2 CLS features, empty classifier
│   ├── classifier_occupancy.pkl      # joblib-saved binary LogisticRegression
│   ├── classifier_furnished.pkl      # 13-class LR for furnished photos
│   ├── classifier_empty.pkl          # 13-class LR for empty photos
│   ├── class_names_furnished.json    # {index: name} mapping for furnished classifier
│   ├── class_names_empty.json        # {index: name} mapping for empty classifier
│   ├── class_names_occupancy.json    # {index: name} mapping for occupancy classifier
│   └── training_report.txt           # CV accuracy + confusion matrix per classifier
├── scripts/
│   ├── check_data.py                 # counts per class + min-size warnings
│   ├── extract_embeddings.py         # data/<subset>/ → artifacts/embeddings_<subset>.npz
│   │                                 # accepts --subset {occupancy,furnished,empty,train(legacy)}
│   ├── train_classifier.py           # trains one classifier; --task {occupancy,room_type}
│   │                                 # + --subset {furnished,empty,occupancy}
│   └── predict.py                    # CLI inference: runs occupancy → routes → room-type
├── src/                              # production inference module (TBD)
├── DATA_COLLECTION.md                # data collection guide (must be updated for 3 subsets)
└── CLAUDE.md                         # this file
```

## Commands

All commands run from `cv-models/`. Use the venv at `.venv/bin/python`.

> **NOTE:** Today's scripts (`extract_embeddings.py`, `train_classifier.py`)
> still target the legacy single `data/train/` layout. They need to be
> updated to accept `--subset {occupancy,furnished,empty}` flags before the
> three-classifier flow can be trained. The commands below show the *intended*
> interface — see "Task 1 — Status" for current state.

```bash
# Verify data layout / counts (across all subsets)
.venv/bin/python scripts/check_data.py

# Extract DINOv2 CLS embeddings (~10 min per 2600 images on CPU, ~30s on GPU)
.venv/bin/python scripts/extract_embeddings.py --subset occupancy
.venv/bin/python scripts/extract_embeddings.py --subset furnished
.venv/bin/python scripts/extract_embeddings.py --subset empty

# Train each classifier (~30 sec each)
.venv/bin/python scripts/train_classifier.py --task occupancy
.venv/bin/python scripts/train_classifier.py --task room_type --subset furnished
.venv/bin/python scripts/train_classifier.py --task room_type --subset empty

# Test full inference (occupancy → routed room-type) on a folder
.venv/bin/python scripts/predict.py --folder /path/to/test/images
```

## Task 1: Room Type Classifier — Status

Three classifiers, all DINOv2-base frozen + sklearn `LogisticRegression`
linear probes:

| Artifact                       | Task           | Classes | Target acc | Data target           |
|--------------------------------|----------------|---------|------------|-----------------------|
| `occupancy_classifier.pkl`     | empty vs furn. | 2       | ≥95%       | ~200 + ~200           |
| `room_type_furnished.pkl`      | room type      | 13      | ≥88%       | ~200/class furnished  |
| `room_type_empty.pkl`          | room type      | 13      | ≥85%       | ~100-200/class empty  |

The 13 room-type classes are: bathroom, kitchen, bedroom, living, dining,
hallway, home_office, balcony, outdoor, theatre, kidsroom, living_bedroom,
living_dining.

Empty rooms have a lower accuracy target — fewer visual cues, distinguishing
small empty rooms from each other is genuinely harder. The occupancy classifier
upstream should be very accurate (binary task on a strong visual signal), so
routing errors are not the main risk.

**Current state:**
- Scripts (`extract_embeddings.py`, `train_classifier.py`, `predict.py`) exist
  but only handle the legacy single-classifier flow. They need a `--subset`
  flag and a routing path in `predict.py`.
- A single legacy classifier was trained from 101 starter images
  (`staging/test_images_empty/`, all empty) just to verify the pipeline runs
  end-to-end. **It is not production-usable; do not ship it.**
- Real training data collection is in progress (Jimmy + Lawrence).

**Migration plan** (when ready to train the three real classifiers):
1. Populate `data/train_occupancy/{empty,furnished}/` (~200 each).
2. Populate `data/train_furnished/<class>/` (~200/class).
3. Populate `data/train_empty/<class>/` (~100-200/class).
4. Update `extract_embeddings.py` and `train_classifier.py` to accept
   `--subset` arg writing to `artifacts/embeddings_<subset>.npz`.
5. Update `predict.py` to load all three artifacts and route.
6. The legacy `data/train/` and single `classifier.pkl` are deleted after
   migration; nothing depends on them.

## Task 2: Room Instance Grouping — Status

Plan documented in `agent/ROOM_GROUPING_DESIGN.md`. Code not yet written.

Two-step design:
- **Step A:** DINOv2 CLS pairwise cosine similarity → candidate same-room pairs
  (threshold ≈ 0.85, fast).
- **Step B:** DINOv2 patch features mutual best matches → geometric verification
  (rejects twin rooms with identical finishes; threshold ≈ 30 mutual matches
  out of 256 patches).
- Connected components on the verified-edge graph → final groups.

No training. Only the DINOv2 backbone + numpy.

**Occupancy affects thresholds.** Empty rooms share many low-info patches
(blank walls, plain floors) which inflates CLS similarity and produces fewer
distinct mutual matches. Plan to tune two threshold profiles using the
upstream occupancy detector's output:

- `furnished` profile: T_cls ≈ 0.85, T_patch ≈ 30 (the default).
- `empty` profile: T_cls ≈ 0.90 (stricter on CLS), T_patch ≈ 15 (looser on
  patch counts because fewer distinctive patches exist).

Twin-bathroom false-merges are mostly an empty-room failure mode. Calibrate
both profiles on the eventual `data/grouping_eval/` benchmark, not on
intuition.

## Pipeline contract

When all three classifiers + Task 2 are implemented and integrated, the
end-to-end flow becomes:

```
upload photos
  → DINOv2 single forward pass per photo: CLS + patches
  → Step 0: occupancy_classifier         → per-photo: empty | furnished
  → Step 1: route to room_type_<occ>     → per-photo: room_type
  → Step 2: bucket by (room_type, occupancy)
              → {(bathroom, furnished): [...], (bathroom, empty): [...], ...}
  → Step 3: Task 2 grouping (per-bucket, threshold profile by occupancy)
              → {bathroom_1: [...], bathroom_2: [...], ...}
  → Step 4: home-report-ai /report (per instance group, with hints:
              room_type + occupancy; skips its own Stage 1)
  → Step 5: aggregate per-instance reports → final response
```

This module's outputs (occupancy, room_type, instance groupings) are consumed by
`agent/tools/server.py` `/pipeline/run`. The hint dict passed to home-report-ai
looks like:

```json
{
  "room_groups": {
    "bathroom_1": {"photo_indices": [0, 5], "room_type": "bathroom", "occupancy": "furnished"},
    "bathroom_2": {"photo_indices": [2, 8, 12], "room_type": "bathroom", "occupancy": "empty"},
    ...
  }
}
```

Don't change this schema without updating the consumer.

## Push back checklist

If asked to do any of the following, stop and confirm:

- "Fine-tune DINOv2" — that's Phase 2, requires GPU, may not improve results.
  Confirm linear probe accuracy first.
- "Train on staging/v5_staging" — those are AI-generated renders, not real
  photos. Discuss alternative data sources first.
- "Switch to CLIP / SigLIP / OpenCLIP" — DINOv2 was chosen for instance-level
  discrimination. Switching changes Task 2's quality too. Confirm reason.
- "Add a 14th class" — fine, but every consumer mapping (frontend, agent,
  home-report-ai integration) must update class indices simultaneously.
- "Move to RunPod / GPU" — the code already detects CUDA. No code change
  needed, only deployment config. Confirm whether QPS or batch latency
  actually requires GPU before doing this.
- "Skip the L2-normalize step" — never. See rule 3.
- "Just train one room-type classifier on empty + furnished mixed together"
  — explicitly rejected. See rule 8 and Task 1 status. Empty rooms need a
  dedicated classifier; furniture cues otherwise dominate training.
- "Drop the occupancy detector and assume furnished" — only acceptable if
  product confirms that production traffic is 100% furnished. Today's
  assumption: unknown distribution, must handle both.
- "Mix empty photos into `train_furnished/` because we have extra" —
  contaminates the furnished classifier's distribution. Empty photos go in
  `train_empty/` only. (Photos with very minimal furniture — e.g., a single
  rug — are a judgment call; default to "furnished" if there is any decor at
  all.)
