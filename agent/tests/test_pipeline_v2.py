# agent/tests/test_pipeline_v2.py
"""Tests for POST /v2/pipeline/run — JSON image_urls variant of /pipeline/run.

Root cause this fixes: in production, /pipeline/run's multipart body (raw
photo bytes for every uploaded photo) passes through an AWS Lambda-fronted
proxy that caps request bodies at 6MB. A real listing shoot (20-30 full-res
photos) blows past that, so the request times out / errors before it reaches
this service. This endpoint instead takes S3/edensign image URLs (the
frontend already has an `/upload` step that returns them) and downloads the
bytes itself, so the client → backend request body stays tiny JSON.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

TEST_URLS = [
    "https://content.edensign.io/images/kitchen.jpg",
    "https://content.edensign.io/images/bath.jpg",
]

HOME_REPORT_FIXTURE = {"rooms": []}
BI_ANALYSIS_FIXTURE = {"zipcode": "02135", "recommended_styles": [{"style": "Modern"}]}
BI_EXPLAIN_FIXTURE = {"analysis": {}, "llm": {}}


def _client():
    import importlib
    import tools.server as srv
    importlib.reload(srv)
    from fastapi.testclient import TestClient
    return srv, TestClient(srv.app)


def test_pipeline_v2_empty_urls_returns_400():
    srv, client = _client()
    r = client.post("/v2/pipeline/run", json={"image_urls": [], "zipcode": "02135"})
    assert r.status_code == 400


def test_pipeline_v2_requires_address_or_zipcode():
    srv, client = _client()
    r = client.post("/v2/pipeline/run", json={"image_urls": TEST_URLS})
    assert r.status_code == 400


def test_pipeline_v2_downloads_urls_and_forwards_multipart():
    srv, client = _client()
    downloaded_urls = []
    home_report_files = []

    async def fake_get(self, url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if url in TEST_URLS:
            downloaded_urls.append(url)
            resp.content = f"bytes-for-{url}".encode()
        elif url.endswith("/analyze/by-zipcode"):
            resp.json.return_value = BI_ANALYSIS_FIXTURE
        else:
            raise AssertionError(f"unexpected GET {url}")
        return resp

    async def fake_post(self, url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if url.endswith("/report"):
            home_report_files.extend(kwargs.get("files", []))
            resp.json.return_value = HOME_REPORT_FIXTURE
        elif url.endswith("/analyze/explain/by-zipcode"):
            resp.json.return_value = BI_EXPLAIN_FIXTURE
        else:
            raise AssertionError(f"unexpected POST {url}")
        return resp

    with patch.object(srv.httpx.AsyncClient, "get", new=fake_get), \
         patch.object(srv.httpx.AsyncClient, "post", new=fake_post):
        r = client.post(
            "/v2/pipeline/run",
            json={"image_urls": TEST_URLS, "zipcode": "02135"},
        )

    assert r.status_code == 200
    data = r.json()
    assert data["n_photos"] == len(TEST_URLS)
    assert data["home_report"] == HOME_REPORT_FIXTURE
    assert data["bi_analysis"] == BI_ANALYSIS_FIXTURE
    assert data["bi_explain"] == BI_EXPLAIN_FIXTURE
    # Every URL was downloaded server-side and its bytes forwarded as multipart.
    assert sorted(downloaded_urls) == sorted(TEST_URLS)
    forwarded_bytes = {f[1][1] for f in home_report_files}
    assert forwarded_bytes == {f"bytes-for-{u}".encode() for u in TEST_URLS}


def test_pipeline_v2_download_client_follows_redirects():
    """The download client must follow redirects: real image URLs (S3 presigned
    → CDN, picsum, etc.) commonly 302-redirect. Without follow_redirects the
    download gets an empty body and raise_for_status blows up into a 500."""
    srv, _ = _client()
    captured_kwargs = {}

    real_async_client = srv.httpx.AsyncClient

    def spy_client(*args, **kwargs):
        # Only capture the download client (the one used inside pipeline_run_v2).
        # It's the client created without a base_url, timeout=60.
        captured_kwargs.update(kwargs)
        return real_async_client(*args, **kwargs)

    async def fake_get(self, url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        if url in TEST_URLS:
            resp.content = b"img"
        elif url.endswith("/analyze/by-zipcode"):
            resp.json.return_value = BI_ANALYSIS_FIXTURE
        else:
            raise AssertionError(f"unexpected GET {url}")
        return resp

    async def fake_post(self, url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if url.endswith("/report"):
            resp.json.return_value = HOME_REPORT_FIXTURE
        elif url.endswith("/analyze/explain/by-zipcode"):
            resp.json.return_value = BI_EXPLAIN_FIXTURE
        else:
            raise AssertionError(f"unexpected POST {url}")
        return resp

    with patch.object(srv.httpx, "AsyncClient", side_effect=spy_client) as _, \
         patch.object(real_async_client, "get", new=fake_get), \
         patch.object(real_async_client, "post", new=fake_post):
        r = client_post_v2(srv)

    assert r.status_code == 200
    assert captured_kwargs.get("follow_redirects") is True


def client_post_v2(srv):
    from fastapi.testclient import TestClient
    return TestClient(srv.app).post(
        "/v2/pipeline/run", json={"image_urls": TEST_URLS, "zipcode": "02135"})


def test_pipeline_v2_bad_url_returns_clean_error_not_500():
    """A URL that fails to download (4xx/5xx/network) should surface as a clear
    502, not an opaque 500 from an unhandled exception."""
    srv, client = _client()

    async def fake_get(self, url, **kwargs):
        raise srv.httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404))

    with patch.object(srv.httpx.AsyncClient, "get", new=fake_get):
        r = client.post(
            "/v2/pipeline/run",
            json={"image_urls": ["https://content.edensign.io/missing.jpg"], "zipcode": "02135"},
        )

    assert r.status_code == 502
    assert "image" in r.json()["detail"].lower()
