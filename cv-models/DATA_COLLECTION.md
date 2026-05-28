# Room Type Classifier — Data Collection Guide

## Target: ~2,600 labeled images across 13 classes

### Per-class targets

| Class folder name      | Target | Min | Why                                |
|------------------------|--------|-----|------------------------------------|
| bathroom               | 250    | 200 | High variance (master/powder/half) |
| kitchen                | 250    | 200 | High variance (layout types)       |
| bedroom                | 250    | 200 | Most common, varied                |
| living                 | 250    | 200 | Most common, varied                |
| dining                 | 200    | 150 | Distinct enough                    |
| hallway                | 150    | 100 | Smaller variety                    |
| home_office            | 150    | 100 | Modern listings                    |
| balcony                | 150    | 100 | Outdoor element                    |
| outdoor                | 200    | 150 | Yard/exterior, very varied         |
| theatre                | 100    | 80  | Luxury only; rare                  |
| kidsroom               | 150    | 100 | Bedroom subtype                    |
| living_bedroom         | 100    | 80  | Studio layouts                     |
| living_dining          | 150    | 100 | Open floor plans                   |
| **Total**              | **~2,350** | **~1,750** |                                |

### Folder structure (put images here)

```
/Users/jimmy20020528/Desktop/Edensign/cv-models/data/train/
├── bathroom/
│   ├── 0001.jpg
│   ├── 0002.jpg
│   └── ...
├── kitchen/
├── bedroom/
├── living/
├── dining/
├── hallway/
├── home_office/
├── balcony/
├── outdoor/
├── theatre/
├── kidsroom/
├── living_bedroom/
└── living_dining/
```

### What makes a good training image

1. **Real listing photos** — not staging renders, not stock photos
2. **Variety within each class**:
   - Different angles (wide shot, corner shot, detail shot)
   - Different lighting (bright, dim, mixed)
   - Different staging styles (modern, traditional, empty)
   - Different finishes (white tile, marble, wood, vinyl)
3. **Recognizable as the class** — if a human takes 1 second to look,
   they should say the same label
4. **Resolution**: any size ≥ 224×224 (DINOv2 will resize to 224)
5. **Format**: JPG or PNG

### What to avoid

- ❌ Pure staging renders (those are too clean/synthetic)
- ❌ Floor plans / blueprints / aerial drone shots
- ❌ Heavy text overlays / watermarks / agent contact info burned in
- ❌ Photos where the main subject isn't the room itself (e.g., a close-up of a chair)
- ❌ Same room repeated 5+ times (helps less than 5 different rooms)

### Good sources

1. **Zillow / Redfin / Realtor.com** (manual or scraped) — best variety, real photos
2. **MLS scrape** (if you have BI's scraper working) — already labeled by listing agents
3. **Your existing `staging/v5_staging/`** — confirm class folder names match,
   then copy these in too (small starter batch)
4. **Public datasets**:
   - SUN397 (some room classes)
   - Places365 (broader scene categories)
   - LSUN bedroom/living (huge, but Places365 better for our setup)

### Quality check before training

Run this to spot-check class distributions and find obviously bad images:

```bash
cd /Users/jimmy20020528/Desktop/Edensign/cv-models
.venv/bin/python scripts/check_data.py
```

(Script will be created — flags: too few per class, suspect filenames,
unreadable files, dimensions < 224)

### When you're done

Tell me. I'll run the training pipeline:
```
.venv/bin/python scripts/extract_embeddings.py   # ~1 min (2600 images × 50ms)
.venv/bin/python scripts/train_classifier.py    # ~30 sec
.venv/bin/python scripts/evaluate.py            # ~10 sec, prints confusion matrix
```
