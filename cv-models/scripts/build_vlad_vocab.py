"""Build VLAD vocabulary from DINOv2 patch features of training images.

Samples images from data/train_furnished/ + data/train_empty/, extracts
DINOv2 patch tokens, fits MiniBatchKMeans to get cluster centroids.

Saves:
    artifacts/vlad_vocab.npy  — (K, 768) L2-normalized cluster centroids

Run once (or re-run after adding significant new training data):
    python scripts/build_vlad_vocab.py [--n-clusters 64] [--n-images 300]

VLAD similarity regime (empirically): same-room ~0.55-0.75, different-room
same-type ~0.10-0.35. This is much wider margin than CLS (0.74 vs 0.61).
Thresholds in app/main.py VLAD_THRESHOLDS need calibration on real data.
"""
from pathlib import Path
import argparse
import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel
from sklearn.cluster import MiniBatchKMeans
import random
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = ROOT / "data"
ARTIFACTS = ROOT / "artifacts"

TRAIN_DIRS = ["train_furnished", "train_empty"]


def gather_images(n_images):
    all_imgs = []
    for d in TRAIN_DIRS:
        folder = DATA_ROOT / d
        if not folder.exists():
            continue
        for p in folder.glob("**/*.jpg"):
            all_imgs.append(p)
        for p in folder.glob("**/*.jpeg"):
            all_imgs.append(p)
        for p in folder.glob("**/*.png"):
            all_imgs.append(p)
    random.shuffle(all_imgs)
    return all_imgs[:n_images]


@torch.no_grad()
def extract_patches(image_paths, processor, model, device, batch_size=16):
    """Returns (N*256, 768) patch features, L2-normalized."""
    all_patches = []
    for start in range(0, len(image_paths), batch_size):
        batch = image_paths[start:start + batch_size]
        imgs = []
        for p in batch:
            try:
                imgs.append(Image.open(p).convert("RGB"))
            except Exception:
                imgs.append(Image.new("RGB", (224, 224), (128, 128, 128)))
        inputs = processor(images=imgs, return_tensors="pt").to(device)
        out = model(**inputs)
        patches = out.last_hidden_state[:, 1:]            # (B, 256, 768)
        norms = patches.norm(dim=-1, keepdim=True).clamp(min=1e-6)
        patches = (patches / norms).cpu().numpy()
        all_patches.append(patches.reshape(-1, 768))
        done = min(start + batch_size, len(image_paths))
        print(f"  [{done}/{len(image_paths)}]", end="\r", flush=True)
    print()
    return np.concatenate(all_patches, axis=0).astype(np.float32)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-clusters", type=int, default=64)
    parser.add_argument("--n-images", type=int, default=300,
                        help="Number of training images to sample for vocab")
    args = parser.parse_args()

    print(f"Gathering up to {args.n_images} training images...")
    imgs = gather_images(args.n_images)
    if not imgs:
        print("No images found in data/train_furnished/ or data/train_empty/", file=sys.stderr)
        sys.exit(1)
    print(f"  Using {len(imgs)} images")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Loading DINOv2-base on {device}...")
    processor = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
    model = AutoModel.from_pretrained("facebook/dinov2-base").to(device).eval()

    print(f"Extracting patch features ({len(imgs)} images × 256 patches)...")
    t0 = time.time()
    all_patches = extract_patches(imgs, processor, model, device)
    print(f"  {all_patches.shape[0]} patches extracted in {time.time()-t0:.1f}s")

    print(f"Fitting MiniBatchKMeans (k={args.n_clusters})...")
    t0 = time.time()
    kmeans = MiniBatchKMeans(
        n_clusters=args.n_clusters,
        random_state=42,
        batch_size=8192,
        n_init=3,
        max_iter=300,
        verbose=0,
    )
    kmeans.fit(all_patches)
    centers = kmeans.cluster_centers_.astype(np.float32)
    # L2-normalize centroids
    centers /= np.linalg.norm(centers, axis=1, keepdims=True).clip(1e-6)
    print(f"  Done in {time.time()-t0:.1f}s  inertia={kmeans.inertia_:.2e}")

    out = ARTIFACTS / "vlad_vocab.npy"
    np.save(out, centers)
    print(f"\nSaved {out}  shape={centers.shape}")
    print("Next: run the cv-models service — it will auto-load the vocab on startup.")


if __name__ == "__main__":
    main()
