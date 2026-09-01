"""WAYZ testnet faucet — claim testnet WAYZ via a signed wallet-ownership
proof (gatewayz-backend#2245). No wallet-to-account linkage exists yet
(Epic 2) -- wallet control is proven per-request instead of stored.
See docs/superpowers/specs/2026-09-01-wayz-testnet-faucet-design.md.
"""

import logging
import secrets
from typing import Any

from eth_account import Account
from eth_account.messages import encode_defunct
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.config.config import Config
from src.config.redis_config import get_redis_client
from src.db.faucet import (
    create_pending_claim,
    get_existing_claim,
    has_completed_at_least_one_request,
    mark_claim_failed,
    mark_claim_sent,
)
from src.security.deps import get_user_id
from src.services.chain.wayz_token_faucet_client import (
    WayzTokenFaucetClient,
    WayzTokenFaucetClientError,
)
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

_NONCE_TTL_SECONDS = 300
_NONCE_KEY_PREFIX = "faucet_nonce:"

faucet_nonce_rl = create_endpoint_rate_limit("faucet_nonce", max_requests=10, window_seconds=60)
faucet_claim_rl = create_endpoint_rate_limit("faucet_claim", max_requests=5, window_seconds=60)


class FaucetNonceRequest(BaseModel):
    wallet_address: str = Field(..., min_length=42, max_length=42)


class FaucetClaimRequest(BaseModel):
    wallet_address: str = Field(..., min_length=42, max_length=42)
    signature: str = Field(..., min_length=1)


def _nonce_key(user_id: int, wallet_address: str) -> str:
    return f"{_NONCE_KEY_PREFIX}{user_id}:{wallet_address.lower()}"


def _claim_message(user_id: int, nonce: str) -> str:
    return f"Claim testnet WAYZ for Gatewayz account {user_id}. Nonce: {nonce}."


@router.post("/faucet/nonce", tags=["faucet"])
async def get_faucet_nonce(
    body: FaucetNonceRequest,
    user_id: int = Depends(get_user_id),
    _rl: None = Depends(faucet_nonce_rl),
) -> dict[str, Any]:
    """Issue a one-time nonce + the exact message the caller must sign."""
    nonce = secrets.token_hex(16)
    redis_client = get_redis_client()
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Faucet temporarily unavailable")

    redis_client.setex(_nonce_key(user_id, body.wallet_address), _NONCE_TTL_SECONDS, nonce)
    message = _claim_message(user_id, nonce)
    return {"success": True, "data": {"message": message, "expires_in": _NONCE_TTL_SECONDS}}


@router.post("/faucet/claim", tags=["faucet"])
async def claim_faucet(
    body: FaucetClaimRequest,
    user_id: int = Depends(get_user_id),
    _rl: None = Depends(faucet_claim_rl),
) -> dict[str, Any]:
    """Verify wallet ownership, check eligibility, mint testnet WAYZ."""
    redis_client = get_redis_client()
    if redis_client is None:
        raise HTTPException(status_code=503, detail="Faucet temporarily unavailable")

    key = _nonce_key(user_id, body.wallet_address)
    nonce = redis_client.get(key)
    if not nonce:
        raise HTTPException(
            status_code=400, detail="No pending nonce for this wallet — request one first"
        )
    nonce = nonce.decode() if isinstance(nonce, bytes) else nonce

    message = _claim_message(user_id, nonce)
    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=body.signature)
    except Exception as e:
        redis_client.delete(key)
        raise HTTPException(status_code=401, detail="Invalid signature") from e

    redis_client.delete(key)

    if recovered.lower() != body.wallet_address.lower():
        raise HTTPException(status_code=401, detail="Signature does not match wallet_address")

    if not has_completed_at_least_one_request(user_id, Config.WAYZ_FAUCET_MIN_REQUESTS):
        raise HTTPException(status_code=403, detail="Account not eligible for the faucet yet")

    if get_existing_claim(user_id, body.wallet_address) is not None:
        raise HTTPException(status_code=409, detail="Already claimed")

    claim = create_pending_claim(user_id, body.wallet_address, Config.WAYZ_FAUCET_CLAIM_AMOUNT)
    if claim is None:
        raise HTTPException(status_code=409, detail="Already claimed")

    try:
        mint_client = WayzTokenFaucetClient.from_config()
    except WayzTokenFaucetClientError as e:
        mark_claim_failed(claim["id"], str(e))
        raise HTTPException(status_code=503, detail="Faucet not configured") from e

    try:
        tx_hash = await mint_client.mint(body.wallet_address, Config.WAYZ_FAUCET_CLAIM_AMOUNT)
    except Exception as e:
        logger.error(f"Faucet mint failed for claim {claim['id']}: {e}")
        mark_claim_failed(claim["id"], str(e))
        raise HTTPException(status_code=502, detail="Mint failed") from e

    mark_claim_sent(claim["id"], tx_hash)
    return {
        "success": True,
        "tx_hash": tx_hash,
        "amount": str(Config.WAYZ_FAUCET_CLAIM_AMOUNT),
    }
