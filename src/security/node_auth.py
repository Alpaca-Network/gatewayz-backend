"""Node-bearer authentication for community GPU nodes (Milestone 4 W-A1,
gatewayz-backend#2262).

A community node authenticates its own calls (currently just the
heartbeat) with a `gw_node_<32 urlsafe chars>` bearer token minted at
registration and shown exactly once (src/routes/gpu.py). This is a
DIFFERENT credential from a user's `gw_live_*` API key -- `get_node` is
the node-token counterpart to src/security/deps.py's `get_api_key` /
`get_current_user`, not a replacement for them.
"""

import logging

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.db.gpu import get_node_by_token_hash
from src.utils.crypto import sha256_key_hash

logger = logging.getLogger(__name__)

_node_bearer = HTTPBearer(auto_error=False)


async def get_node(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_node_bearer),
) -> dict:
    """Resolve the calling gpu_nodes row from its bearer token.

    Raises HTTPException 401 if the token is missing/malformed/unknown,
    403 if the node has been disabled.
    """
    del request  # kept for symmetry with other FastAPI auth dependencies
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="node_token_required")

    token = credentials.credentials
    if not token.startswith("gw_node_"):
        raise HTTPException(status_code=401, detail="invalid_node_token")

    try:
        token_hash = sha256_key_hash(token)
    except RuntimeError as e:
        # KEY_HASH_SALT not configured -- same fail-closed behavior as
        # every other hash-based lookup in this codebase.
        logger.error("Node auth unavailable: %s", e)
        raise HTTPException(status_code=503, detail="node_auth_unavailable") from e

    node = get_node_by_token_hash(token_hash)
    if node is None:
        raise HTTPException(status_code=401, detail="invalid_node_token")
    if node.get("status") == "disabled":
        raise HTTPException(status_code=403, detail="node_disabled")

    return node
