"""Quick test script. Usage: python test_run.py img1.jpg img2.jpg ..."""
import asyncio
import json
import sys
from pathlib import Path

import httpx

API = "http://127.0.0.1:8001"


async def main(paths: list[str]) -> None:
    files = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            print(f"File not found: {p}")
            sys.exit(1)
        suffix = path.suffix.lower()
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "webp": "image/webp"}.get(suffix.lstrip("."), "image/jpeg")
        files.append(("files", (path.name, path.read_bytes(), mime)))

    print(f"Sending {len(files)} image(s) to {API}/report ...")
    async with httpx.AsyncClient(timeout=600) as client:
        resp = await client.post(f"{API}/report", files=files)

    if resp.status_code != 200:
        print(f"Error {resp.status_code}: {resp.text}")
        sys.exit(1)

    report = resp.json()
    print(json.dumps(report, indent=2, ensure_ascii=False))

    print("\n── Property Assessment ───────────────────────────────")
    print(report["overall_narrative"])

    print(f"\n── Overall Scores ────────────────────────────────────")
    print(f"Quality:   {report['overall_quality_rating']}  ({report['overall_quality_decimal']:.1f} / 6.0  — lower is better)")
    print(f"Condition: {report['overall_condition_rating']}  ({report['overall_condition_decimal']:.1f} / 6.0)")

    print(f"\n── Rooms ─────────────────────────────────────────────")
    for room in report["rooms"]:
        mats = room["detected_materials"]
        mat_str = ", ".join(f"{k}: {v.replace('_',' ')}" for k, v in mats.items() if v and v != "unknown")
        features = ", ".join(room.get("notable_features", [])[:3])
        print(f"  {room['room_type'].replace('_',' ').title():15s} "
              f"Q={room['quality_rating']} ({room['quality_decimal']:.1f})  "
              f"C={room['condition_rating']} ({room['condition_decimal']:.1f})")
        if mat_str:
            print(f"    Materials: {mat_str}")
        if features:
            print(f"    Features:  {features}")

    print(f"\n── Upgrade Actions ───────────────────────────────────")
    print(f"Urgent: {len(report['must_do'])} | Recommended: {len(report['recommended'])} | Optional: {len(report['optional'])}")

    if report["must_do"]:
        print("\n必做 (Urgent):")
        for a in report["must_do"]:
            cost = a.get("estimated_cost_range", "TBD")
            print(f"  [{a['action_id']}] {a['text']}")
            print(f"    Cost: {cost}")

    if report["recommended"]:
        print("\n建议 (Recommended):")
        for a in report["recommended"]:
            qi = f"  →  {a['quality_impact']}" if a.get("quality_impact") else ""
            cost = a.get("estimated_cost_range", "TBD")
            print(f"  [{a['action_id']}] {a['text']}{qi}")
            print(f"    Cost: {cost}  ROI: {a['roi_tier']}")

    if report["optional"]:
        print("\n可选 (Optional):")
        for a in report["optional"]:
            print(f"  [{a['action_id']}] {a['text']}")

    if report.get("coverage_note"):
        print(f"\nNote: {report['coverage_note']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_run.py <img1> [img2] ...")
        sys.exit(1)
    asyncio.run(main(sys.argv[1:]))
