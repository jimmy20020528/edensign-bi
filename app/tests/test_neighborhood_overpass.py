# app/tests/test_neighborhood_overpass.py
"""Overpass reliability for fetch_pois().

Root cause of a live bug: the Neighborhood section rendered only a generic
narrative with no amenities. fetch_pois() hit a single Overpass endpoint
(overpass-api.de) with no retry/fallback; Overpass is a public, frequently
overloaded API, so a transient timeout returned {} → empty amenities → the
narrative had nothing specific to say. These tests pin the fix: try multiple
mirrors so a transient failure on one is recovered from another.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import httpx
from app.services import neighborhood_data as nb

# One named restaurant node near the query point — enough to prove a mirror's
# response was parsed into buckets.
_OVERPASS_OK = {
    "elements": [
        {"type": "node", "lat": 42.402, "lon": -71.053,
         "tags": {"name": "Test Cafe", "amenity": "cafe"}},
    ]
}


def _resp_ok():
    r = MagicMock()
    r.status_code = 200
    r.raise_for_status = MagicMock()
    r.json.return_value = _OVERPASS_OK
    return r


def test_fetch_pois_falls_back_to_second_mirror_when_first_fails():
    """First mirror times out → fetch_pois must try the next mirror and succeed."""
    calls = []

    def fake_post(url, *args, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise httpx.TimeoutException("primary mirror slow")
        return _resp_ok()

    mock_client = MagicMock()
    mock_client.__enter__.return_value.post = fake_post
    with patch.object(nb.httpx, "Client", return_value=mock_client):
        result = nb.fetch_pois(42.402, -71.053)

    assert len(calls) >= 2, "should have tried a second mirror after the first failed"
    assert result.get("_total", 0) >= 1
    assert any(p["name"] == "Test Cafe" for cat in result.values()
               if isinstance(cat, list) for p in cat)


def test_fetch_pois_returns_empty_when_all_mirrors_fail():
    """Every mirror fails → graceful {} (never raises), as before."""
    def fake_post(url, *args, **kwargs):
        raise httpx.ConnectError("down")

    mock_client = MagicMock()
    mock_client.__enter__.return_value.post = fake_post
    with patch.object(nb.httpx, "Client", return_value=mock_client):
        result = nb.fetch_pois(42.402, -71.053)

    assert result == {}


def test_fetch_pois_tries_more_than_one_mirror_available():
    """There must be >1 mirror configured, otherwise fallback is meaningless."""
    assert len(nb.OVERPASS_MIRRORS) >= 2


def test_fetch_pois_stops_after_total_time_budget():
    """Slow mirrors must not stack into a minute-long wait: once the overall
    time budget is spent, stop trying more mirrors even if some remain."""
    calls = []

    def fake_post(url, *args, **kwargs):
        calls.append(url)
        raise httpx.TimeoutException("slow")

    mock_client = MagicMock()
    mock_client.__enter__.return_value.post = fake_post

    # monotonic(): 1st call sets the deadline (t=0); subsequent calls jump past
    # the budget so the loop breaks before exhausting all mirrors.
    times = iter([0.0] + [nb._OVERPASS_TOTAL_BUDGET + 1] * 20)
    with patch.object(nb.httpx, "Client", return_value=mock_client), \
         patch.object(nb.time, "monotonic", side_effect=lambda: next(times)):
        result = nb.fetch_pois(42.402, -71.053)

    assert result == {}
    # Budget blown right after the first attempt → must NOT try all mirrors.
    assert len(calls) < len(nb.OVERPASS_MIRRORS)
