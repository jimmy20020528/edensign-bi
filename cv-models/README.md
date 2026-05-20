# cv-models

Self-hosted computer vision modules for Edensign.

**Status**: standalone module. **NOT integrated into the live pipeline yet.** When integrated, this will replace home-report-ai's Stage 1 (VLM-based room classification) and add room instance grouping.

## What's here

### Task 1 — Room type classification (three classifiers)

Frozen DINOv2-base + sklearn `LogisticRegression` linear probes.

| Artifact                  | Task             | Classes | Approx. accuracy |
|---------------------------|------------------|---------|------------------|
| `classifier_occupancy.pkl` | empty vs furnished  | 2       | ~95%             |
| `classifier_furnished.pkl` | room type (furnished) | 13      | ~83%             |
| `classifier_empty.pkl`     | room type (empty)     | 13      | ~68%             |

Empty rooms hit lower accuracy because there are fewer visual cues (no furniture).

The 13 room types: `bathroom`, `kitchen`, `bedroom`, `living`, `dining`, `hallway`, `home_office`, `balcony`, `outdoor`, `theatre`, `kidsroom`, `living_bedroom`, `living_dining`.

The `.pkl` files are NOT in the repo (gitignored). Regenerate them from training data using the scripts below.

### Task 2 — Room instance grouping (NOT BUILT)

Future work: identify which photos show the same physical room. Will use **RoMa indoor** (CVPR 2024) dense feature matcher — but RoMa requires GPU, so the serving code lives under `serve_roma/` and targets RunPod. See `serve_roma/README.md`.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## How to train the classifiers

You need labeled training data, organized by subset and class:

```
cv-models/data/
├── train_occupancy/
│   ├── empty/      *.jpg     (~2300 photos, any room type, no furniture)
│   └── furnished/  *.jpg     (~2300 photos, any room type, furnished)
├── train_furnished/<class>/  *.jpg  (~180 per class)
└── train_empty/<class>/      *.jpg  (~180 per class)
```

If you have Edensign's S3 staging dataset:
```bash
./scripts/download_s3.sh
python scripts/reorganize_letian_data.py
```

Then:
```bash
# Verify data layout
python scripts/check_data.py

# Extract DINOv2 CLS embeddings (one pass through DINOv2 per subset)
python scripts/extract_embeddings.py --subset occupancy
python scripts/extract_embeddings.py --subset furnished
python scripts/extract_embeddings.py --subset empty

# Train linear probes
python scripts/train_classifier.py --task occupancy
python scripts/train_classifier.py --task room_type --subset furnished
python scripts/train_classifier.py --task room_type --subset empty

# Test inference on a folder
python scripts/predict.py --folder path/to/test_images
```

## Architecture (DO NOT BREAK)

1. **DINOv2 is frozen.** Never fine-tune the backbone. We use `facebook/dinov2-base` via HuggingFace transformers. Only the linear classifier head is learned.

2. **Embeddings are always L2-normalized before storage or comparison.** Cosine similarity assumes unit-norm vectors. Skipping normalization silently poisons the classifier.

3. **Class index order is fixed.** `CLASS_NAMES` in `scripts/extract_embeddings.py` defines the canonical index → name mapping. Production classifier maps by integer index. Changing the list order without re-training breaks every downstream consumer.

4. **Occupancy-aware routing — three classifiers, not one.** The intended inference pipeline:
   1. Run occupancy detector (binary).
   2. Route to either `classifier_furnished.pkl` or `classifier_empty.pkl`.

   Do NOT train a single merged room-type classifier on mixed empty + furnished data. Empirically that fails on empty rooms because furniture is the dominant visual cue. Cross-contamination (e.g., empty bathrooms in the furnished training folder) silently kills accuracy.

5. **Training data must be real listing photos.** AI-generated renders (staging outputs) introduce distribution shift and shouldn't be used as training data.

## Files

```
cv-models/
├── scripts/
│   ├── check_data.py             Counts per class + min-size warnings
│   ├── download_s3.sh            (Optional) fetch Letian's S3 dataset
│   ├── reorganize_letian_data.py Reshape raw dataset into train_*/ folders
│   ├── extract_embeddings.py     Subset images → artifacts/embeddings_<subset>.npz
│   ├── train_classifier.py       Train one linear probe; --task --subset flags
│   └── predict.py                CLI inference: occupancy → route → room type
├── src/                          (Reserved — production inference module, not built yet)
├── serve_roma/                   RunPod GPU placeholder (RoMa-based grouping)
├── artifacts/                    .pkl + .npz output (gitignored)
├── data/                         Training data (gitignored)
├── requirements.txt
└── README.md
```

## Future integration

When integrated into the live pipeline (`agent /pipeline/run`):

```
photos
  → DINOv2 single forward pass per photo (CLS + patches)
  → Step 0: classifier_occupancy        → per-photo: empty | furnished
  → Step 1: route to classifier_<occ>   → per-photo: room_type
  → Step 2: bucket by (room_type, occupancy)
  → Step 3: Task 2 (RoMa, GPU) instance grouping per bucket
  → Step 4: home-report-ai /report with hints (skip its Stage 1)
```

The hint dict passed to home-report-ai would look like:

```json
{
  "room_groups": {
    "bathroom_1": {"photo_indices": [0, 5], "room_type": "bathroom", "occupancy": "furnished"},
    "bathroom_2": {"photo_indices": [2, 8, 12], "room_type": "bathroom", "occupancy": "empty"}
  }
}
```

This integration is NOT done yet. home-report-ai still does its own VLM-based Stage 1 today.
