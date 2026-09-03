"""SIWE wallet sign-in/sign-up + wallet linking (Milestone 2,
gatewayz-backend#2249 #2250 #2251 #2252).

The server always builds and stores the exact SIWE message the client must
sign (src.security.siwe) -- the client never chooses the message contents,
which is what makes this resistant to a signature obtained on another
dapp being replayed here (see the design spec section 7). A "session" is
an expiring API key (Config.WALLET_SESSION_KEY_DAYS), not a JWT -- no new
auth infrastructure is introduced.

See docs/superpowers/specs/2026-09-03-wallet-identity-auth-design.md
sections 4.1-4.3 and 4.5.
"""

import logging
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

import src.config.supabase_config as supabase_config
import src.db.users as users_module
from src.config.config import Config
from src.config.redis_config import get_redis_client
from src.db.api_keys import create_api_key, get_user_api_keys, update_api_key
from src.db.user_wallets import (
    count_wallets,
    get_wallet,
    get_wallets_for_user,
    link_wallet,
    unlink_wallet,
)
from src.routes.auth import _generate_unique_username, _handle_existing_user
from src.schemas import AuthMethod, PrivyAuthRequest, PrivyAuthResponse, PrivyUserData
from src.security.deps import get_user_id
from src.security.siwe import SIWE_MESSAGE_TTL_SECONDS, build_siwe_message
from src.security.wallet_signature import recover_wallet_address
from src.services.auth_rate_limiting import (
    AuthRateLimitType,
    check_auth_rate_limit,
    get_client_ip,
)
from src.services.endpoint_rate_limiter import create_endpoint_rate_limit
from src.services.payment_gate import resolve_key_environment
from src.utils.wallet_address import normalize_wallet_address

logger = logging.getLogger(__name__)

router = APIRouter()

# Avalanche Fuji testnet -- see Config.SIWE_ALLOWED_CHAIN_IDS for the full
# allow-list (spec section 4.1).
_DEFAULT_CHAIN_ID = 43113

_LOGIN_STATEMENT = "Sign in to Gatewayz."

_LOGIN_NONCE_PREFIX = "siwe_nonce:login:"
_LINK_NONCE_PREFIX = "siwe_nonce:link:"

wallet_link_nonce_rl = create_endpoint_rate_limit(
    "wallet_link_nonce", max_requests=10, window_seconds=60
)
wallet_link_rl = create_endpoint_rate_limit("wallet_link", max_requests=5, window_seconds=60)


def _link_statement(user_id: int) -> str:
    return f"Link this wallet to Gatewayz account {user_id}."


def _login_nonce_key(address: str) -> str:
    return f"{_LOGIN_NONCE_PREFIX}{address}"


def _link_nonce_key(user_id: int, address: str) -> str:
    return f"{_LINK_NONCE_PREFIX}{user_id}:{address}"


class WalletNonceRequest(BaseModel):
    wallet_address: str = Field(..., min_length=42, max_length=42)
    chain_id: int | None = Field(default=None, ge=1)

    @field_validator("wallet_address")
    @classmethod
    def validate_wallet_address(cls, v):
        return normalize_wallet_address(v)


class WalletSignatureRequest(BaseModel):
    """Body shape shared by /auth/wallet/verify and /auth/wallet/link --
    both just need the address, the exact message that was signed, and the
    signature."""

    wallet_address: str = Field(..., min_length=42, max_length=42)
    # The built SIWE message is well under this in practice; bounded so an
    # arbitrarily large string never reaches downstream comparison/parsing.
    message: str = Field(..., min_length=1, max_length=1000)
    # A 65-byte ECDSA signature, hex-encoded with a 0x prefix, is 132 chars;
    # 200 leaves slack without letting an arbitrarily large string reach
    # eth_account's signature parser (same bound as the faucet's).
    signature: str = Field(..., min_length=1, max_length=200)

    @field_validator("wallet_address")
    @classmethod
    def validate_wallet_address(cls, v):
        return normalize_wallet_address(v)


async def _enforce_auth_rate_limit(
    raw_request: Request, limit_type: AuthRateLimitType, human_action: str
) -> None:
    """Same 429 shape as src/routes/auth.py's login/register rate limit checks."""
    client_ip = get_client_ip(raw_request)
    result = await check_auth_rate_limit(client_ip, limit_type)
    if not result.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "message": f"Too many {human_action} attempts. Please try again in {result.retry_after} seconds.",
                "retry_after": result.retry_after,
            },
            headers={"Retry-After": str(result.retry_after)},
        )


def _require_redis():
    redis_client = get_redis_client()
    if redis_client is None:
        raise HTTPException(status_code=503, detail="wallet_auth_unavailable")
    return redis_client


def _build_and_store_nonce(
    redis_client, key: str, address: str, chain_id: int, statement: str
) -> str:
    if chain_id not in Config.SIWE_ALLOWED_CHAIN_IDS:
        raise HTTPException(status_code=422, detail="chain_id_not_allowed")

    nonce = secrets.token_hex(16)
    message = build_siwe_message(
        address=address,
        nonce=nonce,
        chain_id=chain_id,
        statement=statement,
        issued_at=datetime.now(UTC),
    )
    try:
        redis_client.setex(key, SIWE_MESSAGE_TTL_SECONDS, message)
    except Exception as e:
        raise HTTPException(status_code=503, detail="wallet_auth_unavailable") from e
    return message


def _consume_and_verify(
    redis_client, key: str, address: str, submitted_message: str, signature: str
) -> None:
    """Consume the stored nonce message and verify the signature over it.
    Raises HTTPException on any failure; returns None on success."""
    try:
        stored = redis_client.getdel(key)
    except Exception as e:
        raise HTTPException(status_code=503, detail="wallet_auth_unavailable") from e
    stored = stored.decode() if isinstance(stored, bytes) else stored

    # The server-authored message must match byte-for-byte -- this is what
    # stops a client from signing (and submitting) an arbitrary message.
    if not stored or stored != submitted_message:
        raise HTTPException(status_code=400, detail="nonce_missing_or_expired")

    recovered = recover_wallet_address(submitted_message, signature)
    if recovered is None:
        raise HTTPException(status_code=401, detail="invalid_signature")
    if recovered.lower() != address:
        raise HTTPException(status_code=401, detail="signature_address_mismatch")


@router.post("/auth/wallet/nonce", tags=["wallet_auth"])
async def wallet_nonce(body: WalletNonceRequest, raw_request: Request) -> dict[str, Any]:
    """Issue a one-time SIWE nonce + the exact message to sign. Response is
    identical whether or not the wallet is already registered (no
    enumeration)."""
    await _enforce_auth_rate_limit(raw_request, AuthRateLimitType.WALLET_NONCE, "wallet nonce")

    redis_client = _require_redis()
    message = _build_and_store_nonce(
        redis_client,
        _login_nonce_key(body.wallet_address),
        body.wallet_address,
        body.chain_id or _DEFAULT_CHAIN_ID,
        _LOGIN_STATEMENT,
    )
    return {"success": True, "data": {"message": message, "expires_in": SIWE_MESSAGE_TTL_SECONDS}}


def _key_is_expired(expiration_date: str | None) -> bool:
    if not expiration_date:
        return False
    try:
        exp_str = expiration_date
        if "Z" in exp_str:
            exp_str = exp_str.replace("Z", "+00:00")
        elif not exp_str.endswith("+00:00"):
            exp_str = exp_str + "+00:00"
        expiration = datetime.fromisoformat(exp_str)
        return expiration < datetime.now(UTC).replace(tzinfo=expiration.tzinfo)
    except Exception:
        return False


def _maybe_replace_expired_primary_key(user: dict[str, Any]) -> None:
    """A wallet 'session' is an expiring API key (spec section 4.2). The
    key-auth path checks expiration_date but never flips is_active on
    expiry, so without this a stale expired key would be returned forever
    by _handle_existing_user. Deactivate it and mint a fresh one BEFORE
    _handle_existing_user re-fetches the user's active keys."""
    user_id = user["id"]
    keys = get_user_api_keys(user_id)
    primary = next((k for k in keys if k.get("is_primary") and k.get("is_active")), None)
    if primary is None or not _key_is_expired(primary.get("expiration_date")):
        return

    try:
        update_api_key(primary["api_key"], user_id, {"is_active": False})
    except Exception as e:
        logger.warning("Failed to deactivate expired wallet key for user %s: %s", user_id, e)
        return

    env_tag, _downgraded = resolve_key_environment(user, "live")
    try:
        create_api_key(
            user_id=user_id,
            # "Wallet Sign-In" is already taken by the (now inactive) old
            # key -- check_key_name_uniqueness checks ALL rows regardless
            # of is_active, so the replacement needs a distinct name.
            key_name=f"Wallet Sign-In ({secrets.token_hex(3)})",
            environment_tag=env_tag,
            expiration_days=Config.WALLET_SESSION_KEY_DAYS,
            is_primary=True,
        )
    except Exception as e:
        logger.error("Failed to mint replacement wallet key for user %s: %s", user_id, e)


def _synthetic_privy_request(user_id: int) -> PrivyAuthRequest:
    """_handle_existing_user needs a PrivyAuthRequest-shaped object purely
    for logging and its (overwritten below) privy_user_id response field --
    wallet sign-in has no real Privy identity. This marker id is never
    persisted or treated as a real Privy DID anywhere downstream."""
    return PrivyAuthRequest(
        user=PrivyUserData(id=f"wallet:{user_id}", created_at=int(time.time())),
        auto_create_api_key=True,
    )


def _wallet_login_existing_user(
    wallet_row: dict[str, Any], background_tasks: BackgroundTasks
) -> PrivyAuthResponse:
    user_id = wallet_row["user_id"]
    user = users_module.get_user_by_id(user_id)
    if user is None:
        # user_wallets.user_id is FK ON DELETE CASCADE, so this should be
        # unreachable -- but fail loudly rather than silently 500ing later.
        logger.error("user_wallets row references missing user %s", user_id)
        raise HTTPException(status_code=500, detail="wallet_account_missing")

    _maybe_replace_expired_primary_key(user)

    response = _handle_existing_user(
        existing_user=user,
        request=_synthetic_privy_request(user_id),
        background_tasks=background_tasks,
        auth_method=AuthMethod.WALLET,
        display_name=user.get("username"),
        email=user.get("email"),
    )
    # No real Privy identity for a wallet-first account -- don't leak the
    # synthetic marker id constructed above.
    response.privy_user_id = None
    return response


def _wallet_signup_new_user(
    address: str, background_tasks: BackgroundTasks  # noqa: ARG001 -- kept for signature symmetry
) -> PrivyAuthResponse:
    client = supabase_config.get_supabase_client()
    username = _generate_unique_username(client, f"wallet_{address[2:8]}")
    email = f"wallet+{address}@wallet.placeholder"

    user_payload = {
        "username": username,
        "email": email,
        "purchased_credits": 0.0,
        "subscription_allowance": 0.0,
        "auth_method": AuthMethod.WALLET.value,
        "created_at": datetime.now(UTC).isoformat(),
        "welcome_email_sent": False,
        "subscription_status": "inactive",
        "tier": "basic",
    }
    try:
        result = client.table("users").insert(user_payload).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="wallet_signup_failed")
        created_user = result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Wallet signup insert failed for %s: %s", address, e)
        raise HTTPException(status_code=500, detail="wallet_signup_failed") from e

    user_id = created_user["id"]
    env_tag, _downgraded = resolve_key_environment(created_user, "live")
    try:
        api_key, _key_id = create_api_key(
            user_id=user_id,
            key_name="Wallet Sign-In",
            environment_tag=env_tag,
            expiration_days=Config.WALLET_SESSION_KEY_DAYS,
            is_primary=True,
        )
    except Exception as e:
        logger.critical("Wallet signup: failed to create API key for user %s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="wallet_signup_failed") from e

    # If linking fails here, the user + key already exist and signup still
    # succeeds -- the wallet link can be retried (re-verifying the SAME
    # address hits this same new-user branch again since get_wallet() would
    # still return None; that's an accepted rare edge, not rolled back).
    wallet_row = link_wallet(user_id, address, source="siwe", make_primary=True)
    if wallet_row is None:
        logger.error(
            "Wallet signup: user %s created but linking wallet %s failed", user_id, address
        )

    return PrivyAuthResponse(
        success=True,
        message="Account created successfully",
        user_id=user_id,
        api_key=api_key,
        auth_method=AuthMethod.WALLET,
        privy_user_id=None,
        is_new_user=True,
        display_name=username,
        email=None,
        credits=0.0,
        timestamp=datetime.now(UTC),
        subscription_status="inactive",
        tier="basic",
        tier_display_name="Basic",
        subscription_allowance=0.0,
        purchased_credits=0.0,
        total_credits=0.0,
    )


@router.post("/auth/wallet/verify", response_model=PrivyAuthResponse, tags=["wallet_auth"])
async def wallet_verify(
    body: WalletSignatureRequest,
    background_tasks: BackgroundTasks,
    raw_request: Request,
) -> PrivyAuthResponse:
    """Verify a signed SIWE message and sign in (existing wallet) or sign
    up (new wallet). Returns the same shape as POST /auth."""
    await _enforce_auth_rate_limit(raw_request, AuthRateLimitType.LOGIN, "wallet sign-in")

    redis_client = _require_redis()
    _consume_and_verify(
        redis_client,
        _login_nonce_key(body.wallet_address),
        body.wallet_address,
        body.message,
        body.signature,
    )

    existing = get_wallet(body.wallet_address)
    if existing is not None:
        return _wallet_login_existing_user(existing, background_tasks)

    await _enforce_auth_rate_limit(raw_request, AuthRateLimitType.REGISTER, "wallet sign-up")
    return _wallet_signup_new_user(body.wallet_address, background_tasks)


@router.post("/auth/wallet/link/nonce", tags=["wallet_auth"])
async def wallet_link_nonce(
    body: WalletNonceRequest,
    user_id: int = Depends(get_user_id),
    _rl: None = Depends(wallet_link_nonce_rl),
) -> dict[str, Any]:
    redis_client = _require_redis()
    message = _build_and_store_nonce(
        redis_client,
        _link_nonce_key(user_id, body.wallet_address),
        body.wallet_address,
        body.chain_id or _DEFAULT_CHAIN_ID,
        _link_statement(user_id),
    )
    return {"success": True, "data": {"message": message, "expires_in": SIWE_MESSAGE_TTL_SECONDS}}


def _wallet_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "wallet_address": row.get("wallet_address"),
        "source": row.get("source"),
        "wallet_client_type": row.get("wallet_client_type"),
        "is_primary": row.get("is_primary", False),
        "verified_at": row.get("verified_at"),
    }


@router.post("/auth/wallet/link", tags=["wallet_auth"])
async def wallet_link(
    body: WalletSignatureRequest,
    user_id: int = Depends(get_user_id),
    _rl: None = Depends(wallet_link_rl),
) -> dict[str, Any]:
    redis_client = _require_redis()
    _consume_and_verify(
        redis_client,
        _link_nonce_key(user_id, body.wallet_address),
        body.wallet_address,
        body.message,
        body.signature,
    )

    existing = get_wallet(body.wallet_address)
    if existing is not None:
        if existing["user_id"] == user_id:
            return {"success": True, "data": {"wallet": _wallet_view(existing)}}
        # Never reveal whose account it is -- just that it's taken.
        raise HTTPException(status_code=409, detail="wallet_linked_to_other_account")

    make_primary = count_wallets(user_id) == 0
    wallet_row = link_wallet(user_id, body.wallet_address, source="siwe", make_primary=make_primary)
    if wallet_row is None:
        # Insert raced with a concurrent link of the same address (or a
        # transient DB error) -- re-check rather than assume either.
        existing_after_race = get_wallet(body.wallet_address)
        if existing_after_race is not None and existing_after_race["user_id"] == user_id:
            return {"success": True, "data": {"wallet": _wallet_view(existing_after_race)}}
        if existing_after_race is not None:
            raise HTTPException(status_code=409, detail="wallet_linked_to_other_account")
        raise HTTPException(status_code=500, detail="wallet_link_failed")

    return {"success": True, "data": {"wallet": _wallet_view(wallet_row)}}


@router.get("/auth/wallets", tags=["wallet_auth"])
async def list_wallets(user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    wallets = get_wallets_for_user(user_id)
    return {"success": True, "data": {"wallets": [_wallet_view(w) for w in wallets]}}


@router.delete("/auth/wallets/{wallet_address}", tags=["wallet_auth"])
async def unlink_wallet_route(
    wallet_address: str, user_id: int = Depends(get_user_id)
) -> dict[str, Any]:
    try:
        address = normalize_wallet_address(wallet_address)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    existing = get_wallet(address)
    if existing is None or existing["user_id"] != user_id:
        raise HTTPException(status_code=404, detail="wallet_not_linked")

    # get_user_by_id returns None on ANY lookup error (safe-default
    # convention, src/db/users.py) as well as on a genuinely missing user.
    # This guard exists specifically to prevent a destructive action
    # (locking the owner out of their only auth method), so it must fail
    # CLOSED on "can't tell" -- a transient DB hiccup must never be read as
    # "not their only auth method, proceed." Same rule count_wallets'
    # own docstring already states for its callers.
    user = users_module.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=503, detail="temporarily_unavailable")
    if user.get("auth_method") == AuthMethod.WALLET.value and count_wallets(user_id) <= 1:
        # This is the only wallet AND the only way to authenticate this
        # account -- unlinking it would lock the owner out.
        raise HTTPException(status_code=400, detail="last_auth_method")

    if not unlink_wallet(user_id, address):
        raise HTTPException(status_code=500, detail="wallet_unlink_failed")

    return {"success": True}
