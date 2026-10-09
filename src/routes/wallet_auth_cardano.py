"""Link a Cardano stake address to a Gatewayz account (CIP-30 signData).

The Cardano counterpart of /auth/wallet/link/nonce + /auth/wallet/link in
src/routes/wallet_auth.py, and built the same way: the server authors the
exact message, stores it in Redis for 5 minutes, and consumes it exactly once
(``GETDEL``) before checking the signature, so a signature obtained anywhere
else -- or replayed -- never links anything. The proof itself is checked in
src/services/delegation/cip8.py: a valid Ed25519 COSE_Sign1 over our message
by the key whose blake2b-224 hash IS the stake credential in the address.

Linking only -- a stake address is never a way to sign in. It exists so
delegated staking (src/services/delegation/) can match pool delegators to
accounts.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from src.config.config import Config
from src.config.redis_config import get_redis_client
from src.db.user_wallets import CARDANO_NAMESPACE, get_wallet, link_wallet
from src.routes.wallet_auth import _pay_pending_delegation_rewards, _wallet_view
from src.security.deps import get_user_id
from src.security.siwe import SIWE_MESSAGE_TTL_SECONDS
from src.services.delegation.cip8 import (
    Cip8VerificationError,
    parse_stake_address,
    verify_stake_signature,
)
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

_NONCE_PREFIX = "cip30_nonce:link:"

cardano_link_nonce_rl = create_endpoint_rate_limit(
    "cardano_wallet_link_nonce", max_requests=10, window_seconds=60
)
cardano_link_rl = create_endpoint_rate_limit(
    "cardano_wallet_link", max_requests=5, window_seconds=60
)


def _nonce_key(user_id: int, stake_address: str) -> str:
    return f"{_NONCE_PREFIX}{user_id}:{stake_address}"


def _validated_stake_address(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("stake_address must be a string")
    lowered = value.strip().lower()
    try:
        parse_stake_address(lowered, allow_testnet=Config.DELEGATION_ALLOW_CARDANO_TESTNET)
    except Cip8VerificationError as e:
        raise ValueError(f"stake_address rejected: {e.code}") from e
    return lowered


class CardanoNonceRequest(BaseModel):
    # stake1 + 53 chars on mainnet (59 total), stake_test1 on testnet (64).
    stake_address: str = Field(..., min_length=50, max_length=80)

    @field_validator("stake_address")
    @classmethod
    def _valid(cls, v: str) -> str:
        return _validated_stake_address(v)


class CardanoLinkRequest(BaseModel):
    stake_address: str = Field(..., min_length=50, max_length=80)
    # COSE_Sign1 hex: headers + our ~250-byte message + a 64-byte signature
    # is well under 1 KB; bounded so an arbitrary blob never reaches the
    # decoder.
    signature: str = Field(..., min_length=2, max_length=4096)
    # COSE_Key hex: ~45 bytes.
    key: str = Field(..., min_length=2, max_length=512)

    @field_validator("stake_address")
    @classmethod
    def _valid(cls, v: str) -> str:
        return _validated_stake_address(v)


def build_cardano_link_message(
    user_id: int, stake_address: str, nonce: str, issued_at: datetime
) -> str:
    """The exact text the wallet signs. Names the domain, the account and
    the address, so a signature is useless anywhere else."""
    expires = issued_at + timedelta(seconds=SIWE_MESSAGE_TTL_SECONDS)
    return (
        f"{Config.SIWE_DOMAIN} wants you to link a Cardano stake address.\n"
        f"\n"
        f"Link this stake address to Gatewayz account {user_id}.\n"
        f"\n"
        f"Stake address: {stake_address}\n"
        f"URI: {Config.SIWE_URI}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued_at.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"Expiration Time: {expires.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    )


def _require_redis():
    redis_client = get_redis_client()
    if redis_client is None:
        raise HTTPException(status_code=503, detail="wallet_auth_unavailable")
    return redis_client


@router.post("/auth/wallet/cardano/nonce", tags=["wallet_auth"])
async def cardano_link_nonce(
    body: CardanoNonceRequest,
    user_id: int = Depends(get_user_id),
    _rl: None = Depends(cardano_link_nonce_rl),
) -> dict[str, Any]:
    """Issue a one-time message for the wallet to sign with CIP-30
    ``signData(stake_address, payload_hex)``."""
    redis_client = _require_redis()
    nonce = secrets.token_hex(16)
    issued_at = datetime.now(UTC).replace(microsecond=0)
    message = build_cardano_link_message(user_id, body.stake_address, nonce, issued_at)
    try:
        redis_client.setex(
            _nonce_key(user_id, body.stake_address), SIWE_MESSAGE_TTL_SECONDS, message
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail="wallet_auth_unavailable") from e
    return {
        "success": True,
        "data": {
            "nonce": nonce,
            "message": message,
            # CIP-30 signData takes the payload as hex bytes.
            "payload_hex": message.encode("utf-8").hex(),
            "expires_at": (issued_at + timedelta(seconds=SIWE_MESSAGE_TTL_SECONDS)).isoformat(),
        },
    }


@router.post("/auth/wallet/cardano/link", tags=["wallet_auth"])
async def cardano_link(
    body: CardanoLinkRequest,
    user_id: int = Depends(get_user_id),
    _rl: None = Depends(cardano_link_rl),
) -> dict[str, Any]:
    """Verify the CIP-30 proof over the issued message and link the stake
    address to the caller's account."""
    redis_client = _require_redis()
    try:
        stored = redis_client.getdel(_nonce_key(user_id, body.stake_address))
    except Exception as e:
        raise HTTPException(status_code=503, detail="wallet_auth_unavailable") from e
    stored = stored.decode() if isinstance(stored, bytes) else stored
    if not stored:
        raise HTTPException(status_code=400, detail="nonce_missing_or_expired")

    try:
        verify_stake_signature(
            body.stake_address,
            body.signature,
            body.key,
            stored,
            allow_testnet=Config.DELEGATION_ALLOW_CARDANO_TESTNET,
        )
    except Cip8VerificationError as e:
        logger.info("cardano link rejected for user %s: %s", user_id, e.code)
        raise HTTPException(status_code=401, detail=e.code) from e

    existing = get_wallet(body.stake_address)
    if existing is not None:
        if existing["user_id"] == user_id:
            return {"success": True, "data": {"wallet": _wallet_view(existing)}}
        raise HTTPException(status_code=409, detail="wallet_linked_to_other_account")

    wallet_row = link_wallet(
        user_id,
        body.stake_address,
        source="cip30",
        # "Primary" is the wallet an account signs in with; a stake address
        # cannot sign in, so it is never primary.
        make_primary=False,
        chain_namespace=CARDANO_NAMESPACE,
    )
    if wallet_row is None:
        after_race = get_wallet(body.stake_address)
        if after_race is not None and after_race["user_id"] == user_id:
            return {"success": True, "data": {"wallet": _wallet_view(after_race)}}
        if after_race is not None:
            raise HTTPException(status_code=409, detail="wallet_linked_to_other_account")
        raise HTTPException(status_code=500, detail="wallet_link_failed")

    _pay_pending_delegation_rewards(body.stake_address, user_id)
    return {"success": True, "data": {"wallet": _wallet_view(wallet_row)}}
