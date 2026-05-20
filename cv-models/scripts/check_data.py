"""Sanity-check the data/train/ folder before training.

Reports:
  - Image counts per class
  - Classes below minimum threshold
  - Unreadable or corrupt files
  - Files smaller than 224×224 (will be upscaled, may hurt quality)
"""
from pathlib import Path
from PIL import Image
import sys

DATA_ROOT = Path(__file__).resolve().parent.parent / "data" / "train"
MIN_COUNT = 80  # below this, training will be unbalanced
MIN_SIZE = 224  # DINOv2 input resolution

EXPECTED_CLASSES = [
    "bathroom", "kitchen", "bedroom", "living", "dining",
    "hallway", "home_office", "balcony", "outdoor",
    "theatre", "kidsroom", "living_bedroom", "living_dining",
]


def main():
    if not DATA_ROOT.exists():
        print(f"❌ {DATA_ROOT} not found")
        sys.exit(1)

    print(f"📂 Scanning {DATA_ROOT}\n")

    actual_classes = sorted([p.name for p in DATA_ROOT.iterdir() if p.is_dir()])
    print("=== Class folders found ===")
    for c in actual_classes:
        marker = "✓" if c in EXPECTED_CLASSES else "?"
        print(f"  {marker} {c}")

    missing = set(EXPECTED_CLASSES) - set(actual_classes)
    unexpected = set(actual_classes) - set(EXPECTED_CLASSES)
    if missing:
        print(f"\n⚠ Missing class folders: {sorted(missing)}")
    if unexpected:
        print(f"\n⚠ Unexpected class folders (will be ignored): {sorted(unexpected)}")

    print("\n=== Per-class image counts ===")
    total = 0
    issues = []
    for c in EXPECTED_CLASSES:
        folder = DATA_ROOT / c
        if not folder.exists():
            print(f"  ❌ {c:<20} (folder missing)")
            continue
        imgs = list(folder.glob("*.jpg")) + list(folder.glob("*.jpeg")) + list(folder.glob("*.png")) + list(folder.glob("*.JPG"))
        n = len(imgs)
        total += n
        flag = "✓" if n >= MIN_COUNT else "⚠"
        print(f"  {flag} {c:<20} {n} images" + ("" if n >= MIN_COUNT else f"  (< {MIN_COUNT} target)"))

        # spot-check a few images for read errors and dimensions
        for img_path in imgs[:5]:
            try:
                with Image.open(img_path) as img:
                    w, h = img.size
                    if w < MIN_SIZE or h < MIN_SIZE:
                        issues.append(f"{img_path.name} only {w}×{h}")
            except Exception as e:
                issues.append(f"{img_path.name}: {e}")

    print(f"\n📊 Total images: {total}")

    if issues:
        print("\n⚠ Sample-check issues:")
        for i in issues[:20]:
            print(f"  - {i}")

    print("\n✅ Done. Fix any ⚠ before running extract_embeddings.py.")


if __name__ == "__main__":
    main()
