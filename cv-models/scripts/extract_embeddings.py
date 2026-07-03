"""Extract DINOv2 CLS embeddings for one training subset.

Subsets:
    occupancy   → data/train_occupancy/{empty,furnished}/*.jpg          (2 classes)
    furnished   → data/train_furnished/<room_type>/*.jpg                (13 classes)
    empty       → data/train_empty/<room_type>/*.jpg                    (13 classes)

Output: artifacts/embeddings_<subset>.npz with keys:
    X            (N, 768) float32, L2-normalized
    y            (N,) int (class index)
    paths        (N,) str (source file path)
    class_names  (K,) list of class names (index -> name)
"""
from pathlib import Path
import argparse
import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel
import time

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = ROOT / "data"
ARTIFACTS = ROOT / "artifacts"
ARTIFACTS.mkdir(exist_ok=True)

# Canonical class lists per subset
SUBSET_CONFIG = {
    "occupancy": {
        "data_dir": "train_occupancy",
        "class_names": ["empty", "furnished"],
    },
    "furnished": {
        "data_dir": "train_furnished",
        "class_names": [
            "bathroom", "kitchen", "bedroom", "living", "dining",
            "hallway", "home_office", "balcony", "outdoor",
            "theatre", "kidsroom", "living_bedroom", "living_dining",
        ],
    },
    "empty": {
        "data_dir": "train_empty",
        "class_names": [
            "bathroom", "kitchen", "bedroom", "living", "dining",
            "hallway", "home_office", "balcony", "outdoor",
            "theatre", "kidsroom", "living_bedroom", "living_dining",
        ],
    },
}

BATCH_SIZE = 16


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subset", required=True,
                        choices=list(SUBSET_CONFIG.keys()))
    args = parser.parse_args()

    cfg = SUBSET_CONFIG[args.subset]
    src_root = DATA_ROOT / cfg["data_dir"]
    class_names = cfg["class_names"]
    out_path = ARTIFACTS / f"embeddings_{args.subset}.npz"

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Loading DINOv2-base on {device}...")
    processor = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
    model = AutoModel.from_pretrained("facebook/dinov2-base").to(device).eval()

    # Gather samples
    samples = []  # (path, class_idx)
    for ci, cname in enumerate(class_names):
        folder = src_root / cname
        if not folder.exists():
            print(f"  WARN  {cname}: folder missing")
            continue
        imgs = sorted(list(folder.glob("*.jpg")) + list(folder.glob("*.jpeg"))
                      + list(folder.glob("*.png")) + list(folder.glob("*.JPG")))
        for p in imgs:
            samples.append((p, ci))
        print(f"  OK    {cname:<20} {len(imgs)} images")

    if not samples:
        print(f"No images found under {src_root}")
        return

    print(f"\nTotal: {len(samples)} images. Extracting embeddings...\n")

    X = np.zeros((len(samples), 768), dtype=np.float32)
    y = np.zeros(len(samples), dtype=np.int64)
    paths = np.array([str(p) for p, _ in samples])

    t0 = time.time()
    for batch_start in range(0, len(samples), BATCH_SIZE):
        batch = samples[batch_start:batch_start + BATCH_SIZE]
        batch_imgs = []
        for p, _ in batch:
            try:
                img = Image.open(p).convert("RGB")
                batch_imgs.append(img)
            except Exception as e:
                print(f"  skipping {p}: {e}")
                batch_imgs.append(Image.new("RGB", (224, 224), color=(128, 128, 128)))

        inputs = processor(images=batch_imgs, return_tensors="pt").to(device)
        with torch.no_grad():
            out = model(**inputs)
            cls = out.last_hidden_state[:, 0]
            cls = cls / cls.norm(dim=1, keepdim=True).clamp(min=1e-6)
            cls = cls.cpu().numpy()

        for i, (_, ci) in enumerate(batch):
            X[batch_start + i] = cls[i]
            y[batch_start + i] = ci

        done = batch_start + len(batch)
        elapsed = time.time() - t0
        eta = elapsed / done * (len(samples) - done)
        print(f"  [{done}/{len(samples)}] {elapsed:.1f}s elapsed, {eta:.1f}s eta", end="\r")

    print(f"\n\nExtracted in {time.time() - t0:.1f}s")
    np.savez(out_path, X=X, y=y, paths=paths, class_names=np.array(class_names))
    print(f"Saved {out_path}")
    print(f"   X shape: {X.shape}")
    print(f"   y shape: {y.shape}")
    print(f"   Class distribution: {np.bincount(y, minlength=len(class_names))}")


if __name__ == "__main__":
    main()
