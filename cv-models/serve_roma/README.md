# serve_roma — RoMa indoor matching service for RunPod GPU

**Status: placeholder. No `serve.py` yet — the service code will be written here when we actually deploy on RunPod.**

## What goes here

A FastAPI service that wraps RoMa indoor for **room instance grouping** — taking a set of property photos and grouping the ones that show the same physical room.

### Why GPU is required

RoMa indoor is a dense feature matcher built on DINOv2-Large + a Gaussian Process match decoder. The GP decoder uses `torch.cholesky_solve`, which:
- Runs in **~30 ms/pair** on an NVIDIA RTX 4090 (CUDA)
- Runs in **~110 seconds/pair** on Apple Silicon (MPS) due to MPS not supporting `cholesky_solve` (falls back to CPU)

That ~3,000× slowdown makes Mac development impossible. RunPod GPU is the deployment target.

## How to set up on RunPod

1. **Choose a pod template** with PyTorch + CUDA preinstalled. Recommended:
   - `runpod/pytorch:2.4.0-py3.11-cuda12.4.1` (or newer)
   - RTX 4090 (sufficient) or A100 (faster but more expensive)

2. **Clone this repo and cd into this folder:**

```bash
git clone <repo-url>
cd edensign-repo/cv-models/serve_roma
```

3. **Install dependencies:**

```bash
pip install -r requirements.txt
```

This installs `romatch` from PyPI (which pulls RoMa's code + downloads DINOv2-Large weights on first use, ~1.13 GB).

4. **Quick smoke test** to confirm RoMa works on this GPU:

```python
import torch
from romatch import roma_indoor

device = torch.device("cuda")
model = roma_indoor(device=device)

warp, certainty = model.match("img_A.jpg", "img_B.jpg", device=device)
matches, cert = model.sample(warp, certainty)
print(f"Got {matches.shape[0]} matches, mean certainty {cert.mean():.3f}")
```

Expected runtime per pair on RTX 4090: ~150 ms (first call slower due to warmup).

5. **Then write `serve.py`** — a FastAPI app that exposes `POST /group` taking a list of image URLs or multipart uploads, runs pairwise RoMa matching, applies a threshold + Union-Find, and returns groupings:

```json
{
  "groups": [
    {"id": "bathroom_1", "photo_indices": [0, 5]},
    {"id": "bathroom_2", "photo_indices": [2, 8, 12]}
  ]
}
```

## Performance budget (target)

For a 30-photo property bucketed into ~5 rooms (avg 6 photos per room):
- Pairwise pairs per room: C(6,2) = 15
- Total pairs across rooms: ~50
- RoMa runtime on RTX 4090: 50 × 150 ms = **~7.5 seconds**
- Plus Union-Find: <0.1 s
- Plus HTTP overhead: ~1 s

Fits within the wizard's ~30 s total budget.

## Why this isn't built yet

Because debugging RoMa on a Mac is a non-starter (110 seconds per pair), the iteration loop only makes sense on the actual deployment target. The plan is:

1. Spin up a RunPod pod with PyTorch base
2. Install `romatch`
3. Smoke-test it on Edensign's PAIRS evaluation dataset (~830 ground-truth scenes)
4. Calibrate the matching threshold separately for furnished and empty pairs
5. Write `serve.py` once we know the API contract that fits

Until then, this folder contains just `requirements.txt` and this README.
