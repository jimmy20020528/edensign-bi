"""Reorganize Letian's classifier_dataset/ into the train_{occupancy}/{class}/ layout
that our training scripts expect.

Source:
    classifier_dataset/
    ├── empty/         <RoomType>_<Style>_<NNN>.jpg   (and OD####.jpg)
    └── furnished/     <RoomType>_<Style>_<NNN>.jpg   (+ .txt prompts, ignored)
                       OD####.jpg

Destination:
    data/
    ├── train_occupancy/
    │   ├── empty/      (all empty/ images, flat — for binary task)
    │   └── furnished/  (all furnished/*.jpg, flat)
    ├── train_furnished/
    │   ├── bathroom/, kitchen/, ... (13 folders)
    └── train_empty/
        ├── bathroom/, kitchen/, ... (13 folders)

Default mode: copy (preserves source). Use --move to move instead.
"""
from pathlib import Path
from typing import Optional
import re
import shutil
import argparse
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = ROOT / "classifier_dataset"
DST_DATA = ROOT / "data"

PREFIX_TO_CLASS = {
    "Bathroom": "bathroom",
    "Kitchen": "kitchen",
    "Bedroom": "bedroom",
    "Living-Room": "living",
    "Dining-Room": "dining",
    "Hallway": "hallway",
    "Home-Office": "home_office",
    "Balcony": "balcony",
    "Theatre": "theatre",
    "Kidroom": "kidsroom",
    "Living+dining": "living_dining",
    "Living+bedroom": "living_bedroom",
    "OD": "outdoor",
}


def classify_filename(name: str) -> Optional[str]:
    if re.match(r"^OD\d+\.", name):
        return "outdoor"
    for prefix, cls in PREFIX_TO_CLASS.items():
        if name.startswith(prefix + "_"):
            return cls
    return None


def reorganize(move: bool = False, dry_run: bool = False):
    op = "MOVE" if move else "COPY"
    if dry_run:
        op = f"DRY-RUN ({op})"
    print(f"Mode: {op}")
    print(f"Source: {SRC_ROOT}")
    print(f"Dest:   {DST_DATA}\n")

    if not SRC_ROOT.exists():
        print(f"Source not found: {SRC_ROOT}")
        return

    stats = {"empty": Counter(), "furnished": Counter(), "unrecognized": []}

    for occupancy in ("empty", "furnished"):
        src_dir = SRC_ROOT / occupancy
        if not src_dir.exists():
            print(f"Missing: {src_dir}")
            continue

        occ_dst = DST_DATA / "train_occupancy" / occupancy
        if not dry_run:
            occ_dst.mkdir(parents=True, exist_ok=True)

        for f in src_dir.iterdir():
            if not f.is_file():
                continue
            if f.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue

            cls = classify_filename(f.name)
            if cls is None:
                stats["unrecognized"].append(f.name)
                continue

            occ_target = occ_dst / f.name
            cls_dst_dir = DST_DATA / f"train_{occupancy}" / cls
            if not dry_run:
                cls_dst_dir.mkdir(parents=True, exist_ok=True)
            cls_target = cls_dst_dir / f.name

            if not dry_run:
                if move:
                    shutil.copy2(str(f), str(occ_target))
                    shutil.move(str(f), str(cls_target))
                else:
                    shutil.copy2(str(f), str(occ_target))
                    shutil.copy2(str(f), str(cls_target))

            stats[occupancy][cls] += 1

    print("=== Reorganization summary ===")
    for occupancy in ("empty", "furnished"):
        total = sum(stats[occupancy].values())
        print(f"\n{occupancy}/  ({total} images)")
        for cls, n in sorted(stats[occupancy].items(), key=lambda kv: -kv[1]):
            print(f"  {cls:<20} {n}")

    if stats["unrecognized"]:
        print(f"\nUnrecognized files ({len(stats['unrecognized'])}):")
        for n in stats["unrecognized"][:20]:
            print(f"  - {n}")
        if len(stats["unrecognized"]) > 20:
            print(f"  ... and {len(stats['unrecognized']) - 20} more")

    print("\nDone." if not dry_run else "\n(dry run - no files changed)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--move", action="store_true",
                        help="Move source files instead of copying (saves 14 GB)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without doing it")
    args = parser.parse_args()
    reorganize(move=args.move, dry_run=args.dry_run)
