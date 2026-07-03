import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.server import _extract_home_report_highlights


def _room(room_type, quality_decimal, quality_rationale, condition_rating="C3"):
    return {
        "room_type": room_type,
        "quality_rating": f"Q{max(1, min(6, int(quality_decimal)))}",
        "quality_decimal": quality_decimal,
        "quality_rationale": quality_rationale,
        "condition_rating": condition_rating,
        "condition_decimal": 3.0,
        "condition_rationale": "Average condition.",
    }


def test_returns_none_when_no_rooms():
    assert _extract_home_report_highlights({}) is None
    assert _extract_home_report_highlights({"rooms": []}) is None


def test_returns_none_when_all_rooms_low_quality():
    report = {"rooms": [
        _room("kitchen", 3.5, "Dated appliances."),
        _room("bathroom", 2.8, "Needs renovation."),
    ]}
    assert _extract_home_report_highlights(report) is None


def test_includes_only_high_quality_rooms():
    report = {"rooms": [
        _room("kitchen", 5.1, "Premium appliances and custom cabinetry."),
        _room("bathroom", 2.8, "Needs renovation."),
        _room("bedroom", 4.2, "Spacious with excellent natural light."),
    ]}
    result = _extract_home_report_highlights(report)
    assert result is not None
    assert "kitchen" in result
    assert "bedroom" in result
    assert "bathroom" not in result


def test_includes_quality_and_rationale():
    report = {"rooms": [
        _room("kitchen", 4.5, "Chef-grade appliances and quartz counters.", "C5"),
    ]}
    result = _extract_home_report_highlights(report)
    assert "Q4" in result or "Q5" in result
    assert "quartz" in result


def test_returns_none_on_malformed_input():
    assert _extract_home_report_highlights(None) is None
    assert _extract_home_report_highlights("bad") is None
    assert _extract_home_report_highlights({"rooms": "not-a-list"}) is None
