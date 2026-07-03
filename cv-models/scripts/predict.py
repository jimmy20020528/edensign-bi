"""Predict room type for one or more images using the three-classifier pipeline.

Pipeline: occupancy (binary) → route to furnished or empty room-type classifier.
Matches the inference logic in app/main.py.

Usage:
  python scripts/predict.py <image_path> [<image_path> ...]
  python scripts/predict.py --folder <folder>
"""
import sys
import argparse
import json
from pathlib import Path

import numpy as np
import joblib
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"


def load_models():
    print("Loading DINOv2 + classifiers...", file=sys.stderr)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoImageProcessor.from_pretrained("facebook/dinov2-base")
    backbone = AutoModel.from_pretrained("facebook/dinov2-base").to(device).eval()

    for name in ("classifier_occupancy", "classifier_furnished", "classifier_empty"):
        p = ARTIFACTS / f"{name}.pkl"
        if not p.exists():
            print(f"  Missing {p}. Run train_classifier.py --subset <subset> first.", file=sys.stderr)
            sys.exit(1)

    occ_clf = joblib.load(ARTIFACTS / "classifier_occupancy.pkl")
    clf_furnished = joblib.load(ARTIFACTS / "classifier_furnished.pkl")
    clf_empty = joblib.load(ARTIFACTS / "classifier_empty.pkl")
    names_occ = json.loads((ARTIFACTS / "class_names_occupancy.json").read_text())
    names_furnished = json.loads((ARTIFACTS / "class_names_furnished.json").read_text())
    names_empty = json.loads((ARTIFACTS / "class_names_empty.json").read_text())

    return processor, backbone, device, occ_clf, clf_furnished, clf_empty, names_occ, names_furnished, names_empty


def embed(processor, backbone, device, img_path):
    try:
        img = Image.open(img_path).convert("RGB")
    except Exception as e:
        print(f"  WARN: cannot open {img_path}: {e}", file=sys.stderr)
        img = Image.new("RGB", (224, 224), color=(128, 128, 128))
    inputs = processor(images=img, return_tensors="pt").to(device)
    with torch.no_grad():
        cls = backbone(**inputs).last_hidden_state[:, 0]
        cls = cls / cls.norm(dim=1, keepdim=True).clamp(min=1e-6)
    return cls.cpu().numpy()[0]


def predict_one(emb, occ_clf, clf_furnished, clf_empty, names_occ, names_furnished, names_empty):
    occ_idx = int(occ_clf.predict([emb])[0])
    occupancy = names_occ[str(occ_idx)]

    if occupancy == "furnished":
        clf, names = clf_furnished, names_furnished
    else:
        clf, names = clf_empty, names_empty

    probs = clf.predict_proba([emb])[0]
    top3_idx = np.argsort(probs)[::-1][:3]
    top3 = [(names.get(str(clf.classes_[i]), f"class_{clf.classes_[i]}"), float(probs[i]))
             for i in top3_idx]
    return occupancy, top3


def main():
    p = argparse.ArgumentParser()
    p.add_argument("paths", nargs="*")
    p.add_argument("--folder", type=str)
    args = p.parse_args()

    img_paths = []
    if args.folder:
        folder = Path(args.folder)
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.JPG"):
            img_paths.extend(sorted(folder.glob(ext)))
    img_paths.extend([Path(x) for x in args.paths])
    if not img_paths:
        print("Usage: predict.py <img> [<img> ...] OR --folder <folder>")
        sys.exit(1)

    processor, backbone, device, occ_clf, clf_furnished, clf_empty, names_occ, names_furnished, names_empty = load_models()

    print()
    for img_path in img_paths:
        if not img_path.exists():
            print(f"  {img_path}: not found")
            continue
        emb = embed(processor, backbone, device, img_path)
        occupancy, top3 = predict_one(emb, occ_clf, clf_furnished, clf_empty,
                                      names_occ, names_furnished, names_empty)
        print(f"📸 {img_path.name}  [{occupancy}]")
        for name, prob in top3:
            bar = "█" * int(prob * 40)
            print(f"   {name:<18} {prob*100:5.1f}%  {bar}")
        print()


if __name__ == "__main__":
    main()
