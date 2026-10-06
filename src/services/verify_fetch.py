"""Fetch a spec or deliverable the way a GenLayer validator will, from our side first.

Gatewayz fetches caller-supplied URLs, so every fetch goes through the SSRF fence
(src/utils/ssrf_guard.PinnedPublicIPTransport: https only, public IPs only, DNS
pinned per request). Fetching before submission turns a wrong hash or a dead URL
into a free 422 instead of a paid GenLayer case that fails.
"""

from __future__ import annotations

import hashlib

import httpx

from src.utils.ssrf_guard import PinnedPublicIPTransport, SSRFBlockedError

MAX_BYTES = 1_000_000
TIMEOUT_S = 15.0


class FetchFailed(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def fetch_hash(url: str) -> str:
    """sha256 (0x-hex) of the exact bytes served at url right now."""
    if not url.startswith("https://") or len(url) > 512:
        raise FetchFailed("invalid_uri", "URIs must be https and at most 512 characters")
    try:
        with httpx.Client(
            transport=PinnedPublicIPTransport(), timeout=TIMEOUT_S, follow_redirects=False
        ) as c:
            with c.stream("GET", url, headers={"User-Agent": "gatewayz-verify"}) as r:
                if r.status_code != 200:
                    raise FetchFailed("uri_not_fetchable", f"{url} returned HTTP {r.status_code}")
                h = hashlib.sha256()
                size = 0
                for chunk in r.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise FetchFailed(
                            "uri_too_large", f"{url} is larger than {MAX_BYTES} bytes"
                        )
                    h.update(chunk)
                return "0x" + h.hexdigest()
    except SSRFBlockedError as e:
        raise FetchFailed(
            "uri_not_allowed", f"{url} is not a public https address ({e.reason})"
        ) from e
    except httpx.HTTPError as e:
        raise FetchFailed(
            "uri_not_fetchable", f"{url} could not be fetched: {type(e).__name__}"
        ) from e
