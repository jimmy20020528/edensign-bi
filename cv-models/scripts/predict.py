"""Predict room type for one or more images.

Usage:
  python scripts/predict.py <image_path> [<image_path> ...]
  python scripts/predict.py --folder <folder>

Loads artifacts/classifier.pkl + DINOv2, prints predictions + top-3 probs.
"""
import sys
import argparse
from pathlib import Path
import numpy as np
import joblib
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
CLF_PATH = ARTIFACTS / "classifier.pkl"


def load_models():
    print("Loading DINOv2 + classifier...")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
    backbone = AutoModel.from_pretrained("facebook/dinov2-base").to(device).eval()
    clf = joblib.load(CLF_PATH)
    return processor, backbone, clf, device


def embed(processor, backbone, device, img_path):
    img = Image.open(img_path).convert("RGB")
    inputs = processor(images=img, return_tensors="pt").to(device)
    with torch.no_grad():
        out = backbone(**inputs)
        cls = out.last_hidden_state[:, 0]
        cls = cls / cls.norm(dim=1, keepdim=True)
    return cls.cpu().numpy()[0]


def predict_one(processor, backbone, clf, device, img_path):
    emb = embed(processor, backbone, device, img_path)
    probs = clf.predict_proba([emb])[0]
    classes = clf.classes_
    class_names = list(np.load(ARTIFACTS / "embeddings.npz", allow_pickle=True)["class_names"])
    top3_idx = np.argsort(probs)[::-1][:3]
    top3 = [(class_names[classes[i]], float(probs[i])) for i in top3_idx]
    return top3


def main():
    p = argparse.ArgumentParser()
    p.add_argument("paths", nargs="*")
    p.add_argument("--folder", type=str, help="Predict every image in a folder")
    args = p.parse_args()

    if not CLF_PATH.exists():
        print(f"❌ {CLF_PATH} not found. Run train_classifier.py first.")
        sys.exit(1)

    img_paths = []
    if args.folder:
        folder = Path(args.folder)
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.JPG"):
            img_paths.extend(folder.glob(ext))
    img_paths.extend([Path(p) for p in args.paths])
    if not img_paths:
        print("Usage: predict.py <img> [<img> ...] OR --folder <folder>")
        sys.exit(1)

    processor, backbone, clf, device = load_models()

    print()
    for img_path in img_paths:
        if not img_path.exists():
            print(f"⚠ {img_path} not found")
            continue
        top3 = predict_one(processor, backbone, clf, device, img_path)
        print(f"📸 {img_path.name}")
        for name, prob in top3:
            bar = "█" * int(prob * 40)
            print(f"   {name:<18} {prob*100:5.1f}%  {bar}")
        print()


if __name__ == "__main__":
    main()
