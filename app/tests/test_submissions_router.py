# app/tests/test_submissions_router.py
"""Storage-layer contract for the address split: /submissions must accept and
persist address / city / state as three separate fields (address = street line
only). The DB write is mocked — we only assert what gets sent to Supabase."""
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.submissions_router as sr

# Mount only the submissions router (not app.main) so this contract test stays
# fast and free of the bi app's heavy ML deps — the route wiring is identical.
_app = FastAPI()
_app.include_router(sr.router)


def _client_and_capture():
    """TestClient with _sb_write mocked; returns (client, mock). The mock stands
    in for the Supabase REST write and returns a row so create_submission can
    read back an id."""
    mock = AsyncMock(return_value=[{"id": "test-id-123"}])
    return TestClient(_app), mock


def test_create_submission_persists_address_city_state_separately():
    client, mock = _client_and_capture()
    with patch.object(sr, "_sb_write", mock):
        r = client.post("/submissions", json={
            "address": "484 Second St",
            "city": "Cambridge",
            "state": "MA",
            "zipcode": "02139",
        })
    assert r.status_code == 200
    assert r.json()["id"] == "test-id-123"
    # Third positional arg to _sb_write is the row body sent to Supabase.
    body = mock.call_args.args[2]
    assert body["address"] == "484 Second St"   # street line only
    assert body["city"] == "Cambridge"
    assert body["state"] == "MA"
    assert body["zipcode"] == "02139"


def test_submission_model_accepts_city_and_state_fields():
    """The Pydantic input model exposes city/state (regression guard: they must
    not be silently dropped as unknown fields)."""
    s = sr.SubmissionIn(address="1 A St", city="Boston", state="MA")
    dumped = s.model_dump(exclude_none=True)
    assert dumped["city"] == "Boston"
    assert dumped["state"] == "MA"
    assert dumped["address"] == "1 A St"


def test_city_and_state_are_migrated_columns():
    """Auto-migration / self-heal must know about the new columns, or they'd be
    dropped by _sb_write's missing-column retry and never persist."""
    cols = dict(sr._TABLE_COLUMNS["wizard_submissions"])
    assert cols.get("city") == "text"
    assert cols.get("state") == "text"


def test_city_state_optional_defaults_to_omitted():
    """Older callers that send only a combined address (no city/state) still work;
    the missing fields are simply not written."""
    client, mock = _client_and_capture()
    with patch.object(sr, "_sb_write", mock):
        r = client.post("/submissions", json={"address": "484 Second St, Cambridge, MA"})
    assert r.status_code == 200
    body = mock.call_args.args[2]
    assert body["address"] == "484 Second St, Cambridge, MA"
    assert "city" not in body   # exclude_none drops unset optionals
    assert "state" not in body
