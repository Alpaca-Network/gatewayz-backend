"""Community GPU node reachability probe (Milestone 4 W-A1,
gatewayz-backend#2262).

Called at node registration and whenever the endpoint URL/key changes
(src/routes/gpu.py): confirms the operator's vLLM (or OpenAI-compatible)
server is actually reachable at the declared endpoint and actually serves
the models the operator claims, before Gatewayz ever routes traffic to it.
Kept as a standalone function (not inlined in the route) so tests can
patch it instead of standing up a real HTTP server.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT_SECONDS = 5.0


class NodeProbeError(Exception):
    """Raised when a node's declared endpoint can't be verified. `reason`
    is a snake_case code the route maps directly to its `detail` (spec
    section 3: 400 `endpoint_unreachable`/`models_mismatch`)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def probe_node_models(endpoint_url: str, endpoint_api_key: str) -> set[str]:
    """GET {endpoint_url}/v1/models and return the set of model ids it
    reports. Raises NodeProbeError("endpoint_unreachable") on any
    connection failure, timeout, or non-200 response.
    """
    url = endpoint_url.rstrip("/") + "/v1/models"
    headers = {"Authorization": f"Bearer {endpoint_api_key}"} if endpoint_api_key else {}
    try:
        response = httpx.get(url, headers=headers, timeout=_PROBE_TIMEOUT_SECONDS)
    except httpx.HTTPError as e:
        logger.info("Node probe failed to reach %s: %s", url, e)
        raise NodeProbeError("endpoint_unreachable") from e

    if response.status_code != 200:
        logger.info("Node probe got status %s from %s", response.status_code, url)
        raise NodeProbeError("endpoint_unreachable")

    try:
        payload = response.json()
        return {item["id"] for item in payload.get("data", []) if "id" in item}
    except (ValueError, TypeError, KeyError) as e:
        logger.info("Node probe got unparseable response from %s: %s", url, e)
        raise NodeProbeError("endpoint_unreachable") from e
