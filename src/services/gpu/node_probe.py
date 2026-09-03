"""Community GPU node reachability probe (Milestone 4 W-A1,
gatewayz-backend#2262).

Called at node registration and whenever the endpoint URL/key changes
(src/routes/gpu.py): confirms the operator's vLLM (or OpenAI-compatible)
server is actually reachable at the declared endpoint and actually serves
the models the operator claims, before Gatewayz ever routes traffic to it.
Kept as a standalone function (not inlined in the route) so tests can
patch it instead of standing up a real HTTP server.

SSRF hardening (review fix round 1): `endpoint_url` is attacker-controlled
input from a community operator, and this function makes Gatewayz's own
server fetch it -- see src/utils/ssrf_guard.py's module docstring. The
hostname is resolved and public-address-checked via
`assert_public_https_url`, and the actual HTTP connection is pinned to
that resolved IP (SNI/Host still set to the original hostname) so a
DNS-rebind between the check and the connection can't retarget the
request. Redirects are never followed (a 3xx to an internal address must
not be silently chased), and the response body is capped so a malicious
or misbehaving server can't exhaust memory.
"""

import json
import logging
from urllib.parse import urlsplit, urlunsplit

import httpx

from src.utils.ssrf_guard import SSRFBlockedError, assert_public_https_url

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 5.0
_MAX_PROBE_RESPONSE_BYTES = 1_000_000  # 1 MB -- a /v1/models listing is tiny


class NodeProbeError(Exception):
    """Raised when a node's declared endpoint can't be verified. `reason`
    is a snake_case code the route maps directly to its `detail` (spec
    section 3: 400 `endpoint_unreachable`/`models_mismatch`)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _pinned_url(endpoint_url: str, resolved_ip: str, path: str) -> tuple[str, str]:
    """Rebuild `endpoint_url` + `path` with the host replaced by the
    resolved (and already public-address-checked) IP, preserving scheme
    and any explicit port. Returns (pinned_url, original_hostname) -- the
    hostname is still needed for the SNI/Host override so TLS and any
    name-based vhost routing on the operator's server keep working.
    """
    parts = urlsplit(endpoint_url)
    netloc = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    base_path = parts.path.rstrip("/")
    pinned = urlunsplit((parts.scheme, netloc, base_path + path, "", ""))
    return pinned, parts.hostname


def probe_node_models(endpoint_url: str, endpoint_api_key: str) -> set[str]:
    """GET {endpoint_url}/v1/models and return the set of model ids it
    reports. Raises NodeProbeError("endpoint_unreachable") on an SSRF
    block, any connection failure, timeout, redirect, oversized response,
    or non-200 response.
    """
    try:
        resolved_ip = assert_public_https_url(endpoint_url)
    except SSRFBlockedError as e:
        logger.info("Node probe blocked by SSRF guard for %s: %s", endpoint_url, e.reason)
        raise NodeProbeError("endpoint_unreachable") from e

    url, hostname = _pinned_url(endpoint_url, resolved_ip, "/v1/models")
    headers = {"Host": hostname}
    if endpoint_api_key:
        headers["Authorization"] = f"Bearer {endpoint_api_key}"

    try:
        with httpx.stream(
            "GET",
            url,
            headers=headers,
            timeout=_PROBE_TIMEOUT_SECONDS,
            follow_redirects=False,
            extensions={"sni_hostname": hostname},
        ) as response:
            if response.status_code != 200:
                # Covers non-2xx AND 3xx -- a redirect is never followed,
                # so it lands here as a plain non-200 response.
                logger.info("Node probe got status %s from %s", response.status_code, url)
                raise NodeProbeError("endpoint_unreachable")

            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > _MAX_PROBE_RESPONSE_BYTES:
                    logger.info("Node probe response from %s exceeded size cap", url)
                    raise NodeProbeError("endpoint_unreachable")
    except httpx.HTTPError as e:
        logger.info("Node probe failed to reach %s: %s", url, e)
        raise NodeProbeError("endpoint_unreachable") from e

    try:
        payload = json.loads(bytes(body))
        return {item["id"] for item in payload.get("data", []) if "id" in item}
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        logger.info("Node probe got unparseable response from %s: %s", url, e)
        raise NodeProbeError("endpoint_unreachable") from e
