"""Debug: call Gemini on one image and print raw output + validation result."""
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(override=True)

from src.vlm.client import call_vlm
from src.vlm.prompts import ASSESSMENT_PROMPT
from src.vlm.validators import parse_and_validate


async def main(path: str) -> None:
    p = Path(path)
    print(f"Testing: {p.name}\n")
    raw = await call_vlm(p, ASSESSMENT_PROMPT)
    print("=== RAW OUTPUT ===")
    print(raw)
    print("==================\n")
    result = parse_and_validate(raw, p.stem)
    if result:
        print(f"Validation OK:")
        print(f"  room_type     : {result.room_type.value}")
        print(f"  quality       : {result.quality_rating.value} ({result.quality_decimal:.1f})  — {result.quality_rationale}")
        print(f"  condition     : {result.condition_rating.value} ({result.condition_decimal:.1f})  — {result.condition_rationale}")
        mats = result.detected_materials
        for field in ("countertop", "flooring", "cabinets", "fixtures", "appliances"):
            val = getattr(mats, field)
            if val:
                print(f"  {field:12s}: {val}")
        if result.notable_features:
            print(f"  features      : {', '.join(result.notable_features)}")
    else:
        print("Validation FAILED — check ERROR logs above")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
