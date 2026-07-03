"""Evaluate Task 2 (room instance grouping) on PAIRS dataset.

PAIRS dataset structure:
    multiview/PAIRS dataset/bedroom-214 PAIRS-clean/
        scene_0001_A1.jpg   (angle A, empty)
        scene_0001_A2.jpg   (angle A, furnished)  <- production-like
        scene_0001_B1.jpg   (angle B, empty)
        scene_0001_B2.jpg   (angle B, furnished)
        scene_0002_*.jpg
        ...

For each scene_NNNN, all 4 images SHOULD group together (same room).
We test the production scenario by using only furnished images (A2, B2).

Metric:
  For each scene with 2+ test images, our grouping is "correct" if all
  test images land in the SAME group.
  Accuracy = correct_scenes / total_scenes.

Usage:
  python scripts/evaluate_grouping.py --category bedroom
  python scripts/evaluate_grouping.py --category bedroom --grid-search
  python scripts/evaluate_grouping.py --category bedroom --t-cls 0.85 --t-patch 30
"""
from pathlib import Path
import argparse
import re
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from group_instances import (
    load_dinov2, extract_features, count_mutual_best_matches,
    find_connected_components,
)

MULTIVIEW_ROOT = Path("/Users/jimmy20020528/Desktop/Edensign/multiview/PAIRS dataset")

CATEGORY_FOLDERS = {
    "bedroom":    MULTIVIEW_ROOT / "bedroom-214 PAIRS-clean",
    "livingroom": MULTIVIEW_ROOT / "livingroom-534 PAIRS-clean",
    "kitchen":    MULTIVIEW_ROOT / "kitchen-84 PAIRS",
}

# PAIRS naming: scene_NNNN_(A|B)(1|2).jpg
PAIRS_PATTERN = re.compile(r"^scene_(\d+)_([AB])([12])\.(jpg|jpeg|png)$", re.IGNORECASE)


def parse_pairs_filename(name):
    m = PAIRS_PATTERN.match(name)
    if not m:
        return None
    scene = int(m.group(1))
    angle = m.group(2)
    occ = "empty" if m.group(3) == "1" else "furnished"
    return scene, angle, occ


def gather_pairs(folder, occupancy_filter="furnished"):
    """Collect images, organized by scene.

    Returns: dict {scene_id: [path, path, ...]}, filtered to occupancy_filter.
    """
    scenes = defaultdict(list)
    for p in folder.iterdir():
        info = parse_pairs_filename(p.name)
        if info is None:
            continue
        scene, angle, occ = info
        if occupancy_filter and occ != occupancy_filter:
            continue
        scenes[scene].append(p)
    return scenes


def evaluate(category, t_cls, t_patch, occupancy="furnished",
             max_scenes=None, processor=None, model=None, device=None,
             cached_features=None):
    """Run grouping on the category, report accuracy.

    Returns: (accuracy, n_correct, n_total, breakdown)
    """
    folder = CATEGORY_FOLDERS[category]
    if not folder.exists():
        print(f"ERROR: {folder} not found")
        return None

    scenes = gather_pairs(folder, occupancy_filter=occupancy)
    if max_scenes:
        scenes = dict(list(scenes.items())[:max_scenes])

    # Flatten to single list of paths, but keep mapping back to scenes
    all_paths = []
    path_to_scene = []
    for scene_id, paths in scenes.items():
        for p in paths:
            all_paths.append(p)
            path_to_scene.append(scene_id)

    if not all_paths:
        print(f"No {occupancy} images found in {folder}")
        return None

    # Extract features (or use cached)
    if cached_features is None:
        if processor is None:
            processor, model, device = load_dinov2()
        print(f"  Extracting features for {len(all_paths)} images...")
        t0 = time.time()
        cls_arr, patches_arr = extract_features(all_paths, processor, model, device)
        print(f"  Features extracted in {time.time() - t0:.1f}s")
    else:
        cls_arr, patches_arr = cached_features

    # Now run grouping with given thresholds — but we already have features
    N = len(all_paths)
    cls_sim = cls_arr @ cls_arr.T

    candidate_pairs = []
    for i in range(N):
        for j in range(i + 1, N):
            if cls_sim[i, j] > t_cls:
                candidate_pairs.append((i, j))

    confirmed_edges = []
    for i, j in candidate_pairs:
        if count_mutual_best_matches(patches_arr[i], patches_arr[j]) >= t_patch:
            confirmed_edges.append((i, j))

    groups = find_connected_components(N, confirmed_edges)

    # Evaluate: for each scene with 2+ images, are all in same group?
    # Build path_idx -> group_id
    idx_to_group = {}
    for gi, group in enumerate(groups):
        for idx in group:
            idx_to_group[idx] = gi

    # Group test paths by their scene
    scene_to_indices = defaultdict(list)
    for idx, scene_id in enumerate(path_to_scene):
        scene_to_indices[scene_id].append(idx)

    # Stats
    n_total = 0
    n_correct = 0
    breakdown = {"single_photo_scenes": 0, "multi_photo_scenes": 0,
                 "correct_groupings": 0, "wrong_groupings": 0}

    for scene_id, indices in scene_to_indices.items():
        if len(indices) < 2:
            breakdown["single_photo_scenes"] += 1
            continue
        breakdown["multi_photo_scenes"] += 1
        # All indices should be in the same group
        groups_of_indices = set(idx_to_group[idx] for idx in indices)
        if len(groups_of_indices) == 1:
            n_correct += 1
            breakdown["correct_groupings"] += 1
        else:
            breakdown["wrong_groupings"] += 1
        n_total += 1

    accuracy = n_correct / n_total if n_total > 0 else 0.0
    return accuracy, n_correct, n_total, breakdown, (cls_arr, patches_arr)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", choices=list(CATEGORY_FOLDERS.keys()),
                        default="bedroom")
    parser.add_argument("--t-cls", type=float, default=0.85)
    parser.add_argument("--t-patch", type=int, default=30)
    parser.add_argument("--occupancy", choices=["empty", "furnished"], default="furnished")
    parser.add_argument("--max-scenes", type=int, default=None,
                        help="Limit number of scenes (for quick tests)")
    parser.add_argument("--grid-search", action="store_true",
                        help="Sweep T_cls and T_patch")
    args = parser.parse_args()

    processor, model, device = load_dinov2()
    print(f"DINOv2 loaded on {device}")

    if not args.grid_search:
        # Single evaluation
        result = evaluate(args.category, args.t_cls, args.t_patch,
                          occupancy=args.occupancy, max_scenes=args.max_scenes,
                          processor=processor, model=model, device=device)
        if result is None:
            return
        acc, n_corr, n_tot, breakdown, _ = result
        print(f"\n=== {args.category} / {args.occupancy} ===")
        print(f"T_cls = {args.t_cls}, T_patch = {args.t_patch}")
        print(f"Accuracy: {acc:.3f} ({n_corr}/{n_tot} scenes correctly grouped)")
        print(f"Breakdown: {breakdown}")
    else:
        # Grid search — extract features ONCE, then try all threshold combos
        print(f"\nGrid search for {args.category} / {args.occupancy}")
        print("Extracting features once (this takes a few minutes)...")
        first_result = evaluate(args.category, 0.85, 30,
                                occupancy=args.occupancy, max_scenes=args.max_scenes,
                                processor=processor, model=model, device=device)
        if first_result is None:
            return
        _, _, _, _, cached_features = first_result

        # Now sweep thresholds with cached features
        t_cls_values = [0.70, 0.75, 0.80, 0.85, 0.88, 0.90, 0.92]
        t_patch_values = [5, 10, 15, 20, 25, 30, 40, 50]

        print(f"\n{'T_cls':>6} {'T_patch':>8} {'Acc':>6} {'N_correct':>10} {'N_total':>8}")
        print("-" * 50)
        best = (0, 0, 0)
        for t_cls in t_cls_values:
            for t_patch in t_patch_values:
                result = evaluate(args.category, t_cls, t_patch,
                                  occupancy=args.occupancy, max_scenes=args.max_scenes,
                                  cached_features=cached_features)
                if result is None:
                    continue
                acc, n_corr, n_tot, _, _ = result
                print(f"{t_cls:>6.2f} {t_patch:>8d} {acc:>6.3f} {n_corr:>10d} {n_tot:>8d}")
                if acc > best[0]:
                    best = (acc, t_cls, t_patch)

        print(f"\nBest: T_cls={best[1]}, T_patch={best[2]}, accuracy={best[0]:.3f}")


if __name__ == "__main__":
    main()
