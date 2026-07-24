from __future__ import annotations

"""Optional outbound proxy for the key-free public data sources (OSM Nominatim,
Overpass, Walk Score, NCES, Redfin). These providers rate-limit or outright
block datacenter IPs, so on a hosted pod set PUBLIC_DATA_PROXY (e.g.
http://user:pass@host:port) to route ONLY these calls through a relay.

Deliberately NOT a global HTTP(S)_PROXY: OpenAI/Gemini and internal
service-to-service calls must never go through the relay.
"""

import os


def public_data_proxy() -> str | None:
    return os.environ.get("PUBLIC_DATA_PROXY") or None
