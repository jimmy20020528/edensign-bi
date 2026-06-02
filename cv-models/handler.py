# cv-models/handler.py
"""RunPod serverless handler for room classification + instance grouping."""
from __future__ import annotations

import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import app.main as cv_main
from app.main import _load_artifacts, _state


def handler(job: dict) -> dict:
    """Receive image URLs, download and classify, return classify-rooms response."""
    images = job.get("input", {}).get("images", [])
    if not images:
        return {"error": "No images provided"}
    if len(images) > 30:
        return {"error": "Maximum 30 images"}
    if not _state.get("ready"):
        return {"error": "Model not loaded"}

    tmp_paths: list[Path] = []
    try:
        for img in images:
            url = img["url"]
            suffix = Path(img.get("filename", "img.jpg")).suffix or ".jpg"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                with urllib.request.urlopen(url) as resp:
                    tmp.write(resp.read())
                tmp_paths.append(Path(tmp.name))
        return cv_main._classify_and_group(tmp_paths)
    except Exception as e:
        return {"error": str(e)}
    finally:
        for p in tmp_paths:
            p.unlink(missing_ok=True)


if __name__ == "__main__":
    import runpod
    print("[handler] Loading artifacts...", flush=True)
    _state.update(_load_artifacts())
    print("[handler] Ready.", flush=True)
    runpod.serverless.start({"handler": handler})
