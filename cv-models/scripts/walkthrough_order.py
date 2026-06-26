"""Photo walk-through ordering.

Order a property's photos so that reading them top-to-bottom feels like a person
physically walking through the home: start at the front, step into the entry,
then move room-to-room where **consecutive photos visually overlap** — the way a
doorway visible in one shot leads into the next. Aerial / outdoor lifestyle shots
are saved for the end, the way a listing closes on the yard and amenities.

Core idea
---------
"Visual overlap" between two photos is exactly what Task-2 instance grouping
already measures: DINOv2 patch correspondences that survive RANSAC geometric
verification (`count_ransac_inliers`). Same room → very high overlap; adjacent
spaces seen through a doorway → medium overlap; unrelated rooms → ~0. So the
walk-through is a path through the overlap graph that keeps each step as
high-overlap as possible:

  1. Build the symmetric overlap matrix O[i][j] (RANSAC inliers), CLS-screened
     so we only pay for RANSAC on plausibly-related pairs.
  2. Greedy traversal: from the current photo, step to the unvisited photo with
     the highest overlap to it (finishes the current room, since same-room pairs
     dominate). When the current photo has no unvisited overlap left, jump to the
     unvisited photo with the highest overlap to ANY visited photo — this models
     walking through a doorway into the next reachable space.

The continuity layer above is training-free and robust. A thin, *optional*
semantic layer on top uses room_type (from the classifier) only to bookend the
tour — one establishing exterior up front, the remaining outdoor/aerial shots as
a finale — and degrades gracefully to pure-overlop ordering when room_type is
absent or unreliable.

Reuses `extract_features` and `count_ransac_inliers` from group_instances.py;
no new model, no new training data, no VLM cost.
"""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys
import time

import numpy as np

from group_instances import (
    count_ransac_inliers,
    extract_features,
    load_dinov2,
)

# Semantic ordering tiers (the accuracy lever). Only the public-before-private
# split is a HARD rule; ambiguous connectors float by visual overlap.
#  - exterior: bookended (front shot opens, aerial/backyard closes)
#  - public:   main living spaces — ALWAYS before private
#  - private:  bedrooms/baths/office — ALWAYS after public, never first
#  - float:    hallway/balcony/theatre — no fixed slot; placed by overlap (a
#              hallway may be an entry foyer OR a staircase/upstairs corridor, so
#              we don't assume it's early).
EXTERIOR_TYPES = {"outdoor"}
PUBLIC_TYPES = {"living", "living_dining", "dining", "kitchen", "living_bedroom"}
PRIVATE_TYPES = {"bedroom", "kidsroom", "bathroom", "home_office"}
FLOAT_TYPES = {"hallway", "balcony", "theatre"}
# Soft sub-order within the private tier (tiebreaker only; overlap still leads).
_PRIVATE_RANK = {"bedroom": 0, "kidsroom": 1, "bathroom": 2, "home_office": 3}


def _cat(room_type):
    if room_type in EXTERIOR_TYPES:
        return "exterior"
    if room_type in PUBLIC_TYPES:
        return "public"
    if room_type in PRIVATE_TYPES:
        return "private"
    return "float"

# CLS cosine below this → skip RANSAC (pair is clearly unrelated, overlap ~0).
# Intentionally loose: we'd rather run a few extra RANSAC checks than miss a
# real doorway overlap. Mirrors group_instances Step-A screening philosophy.
DEFAULT_SCREEN = 0.45


def compute_overlap_matrix(cls_arr, patches_arr, screen=DEFAULT_SCREEN, verbose=False):
    """Symmetric (N, N) overlap matrix of RANSAC inlier counts.

    cls_arr:     (N, 768) L2-normalized CLS features (cheap screen).
    patches_arr: (N, 256, 768) L2-normalized patch features (RANSAC).
    Returns:     (O, cos) where O[i][j] is the geometrically-verified patch
                 overlap and cos is the CLS cosine matrix (kept for tie-breaks).
    """
    n = len(cls_arr)
    cos = cls_arr @ cls_arr.T
    overlap = np.zeros((n, n), dtype=np.float32)
    pairs = 0
    t0 = time.time()
    for i in range(n):
        for j in range(i + 1, n):
            if cos[i, j] < screen:
                continue
            pairs += 1
            r = count_ransac_inliers(patches_arr[i], patches_arr[j])
            overlap[i, j] = overlap[j, i] = r
    if verbose:
        print(f"  overlap matrix: RANSAC on {pairs} screened pairs "
              f"in {time.time() - t0:.1f}s", file=sys.stderr)
    return overlap, cos


def _greedy_walk(nodes, overlap, cos, start):
    """Greedy continuity walk over `nodes` (a list of int indices) on `overlap`.

    From the current node, step to the unvisited node with the highest overlap to
    it; when that is exhausted (no positive-overlap unvisited neighbor), jump to
    the unvisited node with the highest overlap to ANY already-visited node — a
    doorway into the next reachable space. CLS cosine breaks ties so transitions
    stay visually similar even when geometric overlap is zero.

    Returns: (order, steps) where steps[k] is the overlap between order[k-1] and
             order[k] (steps[0] is None), and a bool flag marks "new room" jumps.
    """
    node_set = set(nodes)
    visited = [start]
    vset = {start}

    def best_from(sources):
        """Highest (overlap, cos) edge from any source to an unvisited node."""
        best = None  # (overlap, cos, target)
        for s in sources:
            for t in node_set:
                if t in vset:
                    continue
                key = (overlap[s, t], cos[s, t], -t)
                if best is None or key > best[0]:
                    best = (key, t, overlap[s, t])
        return best  # (sortkey, target, overlap_value) or None

    steps = [None]
    new_room = [True]
    while len(visited) < len(nodes):
        cur = visited[-1]
        local = best_from([cur])
        if local is not None and local[2] > 0:
            _, nxt, ov = local
            jumped = False
        else:
            # current room exhausted — step through a doorway to the next space
            frontier = best_from(visited)
            if frontier is None:
                break
            _, nxt, ov = frontier
            jumped = True
        visited.append(nxt)
        vset.add(nxt)
        steps.append(float(ov))
        new_room.append(jumped)
    return visited, steps, new_room


def order_from_features(
    cls_arr,
    patches_arr,
    room_types=None,
    *,
    screen=DEFAULT_SCREEN,
    bookend_exteriors=True,
    verbose=False,
):
    """Walk-through ordering from already-extracted DINOv2 features.

    This is the entry point for the cv-models service: `/classify-rooms` already
    runs one DINOv2 forward pass (CLS + patches), so it passes those arrays here
    rather than paying for a second extraction.

    cls_arr:     (N, 768) L2-normalized CLS features.
    patches_arr: (N, 256, 768) L2-normalized patch features.
    room_types:  optional per-photo room_type labels (len N) used only to bookend
                 outdoor/aerial shots. None → pure overlap continuity.
    bookend_exteriors: if True and room_types given, lead with one establishing
                 exterior and move the rest of the outdoor/aerial shots to the end.

    Returns a dict:
      {
        "order":   [orig_index, ...]          # the walk-through sequence
        "steps":   [None, overlap1, ...]      # overlap with previous photo
        "new_room":[True, False, ...]         # True where the walk crossed into
                                              #   a new space (doorway jump)
        "overlap": O.tolist()                 # full overlap matrix (debug)
      }
    """
    n = len(cls_arr)
    if n == 0:
        return {"order": [], "steps": [], "new_room": [], "overlap": []}
    if n == 1:
        return {"order": [0], "steps": [None], "new_room": [True], "overlap": [[0.0]]}

    overlap, cos = compute_overlap_matrix(cls_arr, patches_arr, screen, verbose)

    is_ext = [False] * n
    if room_types and bookend_exteriors:
        is_ext = [(rt in EXTERIOR_TYPES) for rt in room_types]
    interior = [i for i in range(n) if not is_ext[i]]
    exterior = [i for i in range(n) if is_ext[i]]

    # All exterior (or no room_types): just walk everything by overlap.
    if not interior:
        interior, exterior = list(range(n)), []

    # Opener: the establishing exterior most visually connected to the interior
    # (the front that leads inside); fall back to the first exterior.
    opener = None
    if exterior:
        opener = max(
            exterior,
            key=lambda e: (max((overlap[e, i] for i in interior), default=0.0), -e),
        )
        exterior.remove(opener)

    # Interior seed: the entry — interior photo most connected to the opener if we
    # have one, else the most "peripheral" room (lowest total overlap), which
    # tends to be a tour endpoint rather than a central hub.
    if opener is not None and any(overlap[opener, i] > 0 for i in interior):
        seed = max(interior, key=lambda i: (overlap[opener, i], cos[opener, i], -i))
    else:
        seed = min(interior, key=lambda i: (overlap[i].sum(), i))

    interior_order, interior_steps, interior_new = _greedy_walk(
        interior, overlap, cos, seed
    )

    # Finale: remaining outdoor/aerial lifestyle shots, walked among themselves.
    exterior_order, exterior_steps, exterior_new = [], [], []
    if exterior:
        ext_seed = max(exterior, key=lambda e: (overlap[e].sum(), -e))
        exterior_order, exterior_steps, exterior_new = _greedy_walk(
            exterior, overlap, cos, ext_seed
        )

    order, steps, new_room = [], [], []
    if opener is not None:
        order.append(opener)
        steps.append(None)
        new_room.append(True)
    for k, idx in enumerate(interior_order):
        order.append(idx)
        if k == 0 and opener is not None:
            steps.append(float(overlap[opener, idx]))
            new_room.append(overlap[opener, idx] <= 0)
        else:
            steps.append(interior_steps[k])
            new_room.append(interior_new[k])
    for k, idx in enumerate(exterior_order):
        order.append(idx)
        steps.append(exterior_steps[k] if k > 0 else None)
        new_room.append(True if k == 0 else exterior_new[k])

    return {
        "order": [int(i) for i in order],
        "steps": [None if s is None else float(s) for s in steps],
        "new_room": [bool(x) for x in new_room],
        "overlap": overlap.tolist(),
    }


def order_walkthrough(
    image_paths,
    room_types=None,
    *,
    processor=None,
    model=None,
    device=None,
    screen=DEFAULT_SCREEN,
    bookend_exteriors=True,
    verbose=False,
):
    """Path-based convenience wrapper: extract DINOv2 features, then order.

    For callers that already have features (the cv-models service), use
    `order_from_features` directly to avoid a redundant forward pass.
    """
    if not image_paths:
        return {"order": [], "steps": [], "new_room": [], "overlap": []}
    if processor is None:
        processor, model, device = load_dinov2()
    cls_arr, patches_arr = extract_features(image_paths, processor, model, device)
    return order_from_features(
        cls_arr, patches_arr, room_types,
        screen=screen, bookend_exteriors=bookend_exteriors, verbose=verbose,
    )


def _constrained_walk(interior, adj, grt, seed):
    """Overlap-driven room walk with ONE hard constraint: no private room (bedroom/
    bathroom/office) is visited while any public room (living/dining/kitchen) is still
    unvisited. Float rooms (hallway/balcony/theatre) are unconstrained, so they slot
    in next to whatever they visually connect to — an entry hallway lands early, a
    staircase/upstairs hallway lands by the bedrooms. Private sub-order (bedroom →
    bath → office) is only a tiebreak; overlap leads."""
    if not interior:
        return []
    visited = [seed]
    remaining = [g for g in interior if g != seed]
    while remaining:
        cur = visited[-1]
        public_left = any(_cat(grt[g]) == "public" for g in remaining)
        allowed = [g for g in remaining if _cat(grt[g]) != "private"] if public_left else list(remaining)
        if not allowed:
            allowed = list(remaining)

        def rank(g, ov):
            pr = _PRIVATE_RANK.get(grt[g], 9) if _cat(grt[g]) == "private" else -1
            return (ov, -pr, -g)

        nxt = max(allowed, key=lambda g: rank(g, adj[cur][g]))
        if adj[cur][nxt] <= 0:  # current room exhausted → step to nearest reachable
            nxt = max(allowed, key=lambda g: rank(g, max(adj[v][g] for v in visited)))
        visited.append(nxt)
        remaining.remove(nxt)
    return visited


def _order_photos_in_group(idxs, overlap):
    """Within one room, lead with the establishing shot (most intra-room overlap)
    then chain by overlap so consecutive photos stay continuous."""
    idxs = list(idxs)
    if len(idxs) <= 1:
        return idxs
    seed = max(idxs, key=lambda i: sum(overlap[i][j] for j in idxs if j != i))
    out = [seed]
    rem = [i for i in idxs if i != seed]
    while rem:
        cur = out[-1]
        nxt = max(rem, key=lambda j: (overlap[cur][j], -j))
        out.append(nxt)
        rem.remove(nxt)
    return out


def _greedy_group_walk(group_list, adj, start):
    """Greedy continuity walk over groups (room-to-room). adj[a][b] = how visually
    adjacent two rooms are (max cross-room overlap). When the current room has no
    positive-overlap neighbour left, jump to the room most adjacent to ANY visited
    room — i.e. step through the next reachable doorway."""
    if not group_list:
        return []
    if start is None:
        start = group_list[0]
    visited = [start]
    remaining = [g for g in group_list if g != start]
    while remaining:
        cur = visited[-1]
        nxt = max(remaining, key=lambda g: adj[cur][g])
        if adj[cur][nxt] <= 0:
            nxt = max(remaining, key=lambda g: max(adj[v][g] for v in visited))
        visited.append(nxt)
        remaining.remove(nxt)
    return visited


def order_grouped_from_features(cls_arr, patches_arr, room_types, group_ids,
                                screen=DEFAULT_SCREEN, verbose=False):
    """Room-aware walk-through ordering from confirmed groups.

    Rooms are atomic (a group's photos stay together). Rooms are ordered by visual
    adjacency from the front door; the tour never opens in a bedroom/bathroom and
    saves outdoor/aerial lifestyle shots for the end.

    room_types, group_ids: per-photo lists (index-aligned to cls_arr/patches_arr),
    from the user-confirmed grouping. Returns {order, steps, new_room, segments}.
    """
    n = len(cls_arr)
    if n == 0:
        return {"order": [], "steps": [], "new_room": [], "segments": []}
    overlap, cos = compute_overlap_matrix(cls_arr, patches_arr, screen, verbose)

    # group photos by confirmed group_id, with a majority room_type per group
    groups: dict = {}
    for i in range(n):
        groups.setdefault(group_ids[i], []).append(i)
    gids = list(groups.keys())

    def group_rt(g):
        cnt: dict = {}
        for i in groups[g]:
            cnt[room_types[i]] = cnt.get(room_types[i], 0) + 1
        return max(cnt.items(), key=lambda kv: kv[1])[0]

    grt = {g: group_rt(g) for g in gids}

    # room-to-room adjacency = strongest photo overlap across the two rooms
    adj = {a: {} for a in gids}
    for a in gids:
        for b in gids:
            adj[a][b] = 0.0 if a == b else max(
                (overlap[i][j] for i in groups[a] for j in groups[b]), default=0.0
            )

    # Exterior bookend: the front shot opens, the rest (aerial/backyard) close.
    exterior = [g for g in gids if _cat(grt[g]) == "exterior"]
    interior = [g for g in gids if _cat(grt[g]) != "exterior"]

    opener = None
    if exterior:
        # front door = the exterior shot most connected to the interior
        opener = max(exterior, key=lambda e: max((adj[e][i] for i in interior), default=0.0))
        exterior = [e for e in exterior if e != opener]

    interior_seq = []
    if interior:
        # Seed the interior walk at a public (else float) room — never a private one.
        pool = ([g for g in interior if _cat(grt[g]) == "public"]
                or [g for g in interior if _cat(grt[g]) == "float"]
                or interior)
        if opener is not None and any(adj[opener][g] > 0 for g in pool):
            seed = max(pool, key=lambda g: (adj[opener][g], -g))
        else:
            seed = min(pool, key=lambda g: sum(adj[g].values()))  # peripheral = a tour start
        interior_seq = _constrained_walk(interior, adj, grt, seed)

    finale = []
    if exterior:
        ext_seed = max(exterior, key=lambda e: sum(adj[e].values()))
        finale = _greedy_group_walk(exterior, adj, ext_seed)

    group_order = ([opener] if opener is not None else []) + interior_seq + finale

    # Safety net for the all-private edge case (no public/float/exterior at all).
    if group_order and _cat(grt[group_order[0]]) == "private":
        for k in range(1, len(group_order)):
            if _cat(grt[group_order[k]]) != "private":
                group_order.insert(0, group_order.pop(k))
                break

    order, steps, new_room, segments = [], [], [], []
    for g in group_order:
        photo_order = _order_photos_in_group(groups[g], overlap)
        segments.append({"group_id": int(g), "room_type": grt[g],
                         "photo_indices": [int(x) for x in photo_order]})
        for pi, idx in enumerate(photo_order):
            steps.append(None if not order else float(overlap[order[-1]][idx]))
            new_room.append(pi == 0)
            order.append(int(idx))

    return {"order": order, "steps": steps, "new_room": [bool(x) for x in new_room],
            "segments": segments}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=str, help="Folder of images")
    parser.add_argument("--files", nargs="+", help="Specific image paths")
    parser.add_argument("--screen", type=float, default=DEFAULT_SCREEN)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.folder:
        folder = Path(args.folder)
        image_paths = sorted(p for p in folder.iterdir()
                             if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    elif args.files:
        image_paths = [Path(p) for p in args.files]
    else:
        print("Usage: --folder <path> OR --files <files>...", file=sys.stderr)
        return

    result = order_walkthrough(image_paths, verbose=not args.json)
    if args.json:
        print(json.dumps({
            "order": [str(image_paths[i]) for i in result["order"]],
            "steps": result["steps"],
            "new_room": result["new_room"],
        }, indent=2))
    else:
        print(f"\n=== walk-through order ({len(image_paths)} photos) ===")
        for k, i in enumerate(result["order"]):
            ov = result["steps"][k]
            tag = "  (new space)" if result["new_room"][k] else ""
            ov_s = "" if ov is None else f"  overlap={ov:.0f}"
            print(f"  {k:2d}. {image_paths[i].name}{ov_s}{tag}")


if __name__ == "__main__":
    main()
