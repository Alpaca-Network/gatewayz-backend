"""One-time codes for moving a signed-in session between Gatewayz sites.

www.gatewayz.ai used to send people to beta.gatewayz.ai with a credential
and user id in the query string. A credential in a URL leaks into browser
history and logs, and a link built with someone else's credential signs the
visitor into that other account. This module replaces it with a short-lived,
single-use code:

1. ``POST /auth/transfer-code`` -- the signed-in site asks for a code. The
   caller proves who it is with its Gatewayz API key (``Authorization:
   Bearer <key>``) or, for the marketing site that only holds a Privy session,
   a verified Privy access token plus ``privy_user_id`` in the body.
2. The browser is sent to the other site with ``?code=<code>`` only.
3. ``POST /auth/transfer-code/redeem`` -- the other site trades the code for
   the API key and basic profile. The code is consumed atomically (Redis
   GETDEL), so a second redeem fails.

Only the SHA-256 of a code is stored (Redis, 60 s TTL) together with the
user id, the API key row id and the creation time -- never the code and never
the key. Unknown, expired and already-used codes all get the same error.
Codes and keys are never logged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

import src.config.supabase_config as supabase_config
from src.config.redis_config import get_redis_client
from src.db.api_key_lookup import API_KEYS_TABLE, plaintext_from_row
from src.db.users import get_user, get_user_by_id, get_user_by_privy_id
from src.security.deps import get_api_key
from src.security.privy_token import PrivyTokenError, verify_privy_access_token
from src.services.auth_rate_limiting import AuthRateLimitType, check_auth_rate_limit, get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter()

CODE_TTL_SECONDS = 60
_CODE_BYTES = 32  # token_urlsafe(32) -> 43 characters
_REDIS_PREFIX = "session_transfer:"
_KEY_COLUMNS = "id, user_id, api_key, encrypted_key, key_version, is_primary, created_at"

# One message for unknown, expired and already-used codes, so a caller
# cannot tell them apart.
INVALID_CODE_DETAIL = "invalid_or_expired_code"
UNAVAILABLE_DETAIL = "session_transfer_unavailable"

_bearer = HTTPBearer(auto_error=False)


class TransferCodeRequest(BaseModel):
    # Set only when the bearer token is a Privy access token, not an API key.
    privy_user_id: str | None = Field(default=None, min_length=1, max_length=255)


class TransferCodeResponse(BaseModel):
    code: str
    expires_in: int


class RedeemRequest(BaseModel):
    code: str = Field(..., min_length=20, max_length=128)


class RedeemResponse(BaseModel):
    api_key: str
    user_id: int
    privy_user_id: str | None = None
    display_name: str | None = None
    email: str | None = None
    auth_method: str | None = None


def code_hash(code: str) -> str:
    """Storage key material for a code: its SHA-256, never the code itself."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _redis_key(code: str) -> str:
    return f"{_REDIS_PREFIX}{code_hash(code)}"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


async def _enforce_rate_limit(identifier: str, limit_type: AuthRateLimitType) -> None:
    result = await check_auth_rate_limit(identifier, limit_type)
    if not result.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "message": f"Too many attempts. Try again in {result.retry_after} seconds.",
                "retry_after": result.retry_after,
            },
            headers={"Retry-After": str(result.retry_after)},
        )


def _require_redis():
    redis_client = get_redis_client()
    if redis_client is None:
        raise HTTPException(status_code=503, detail=UNAVAILABLE_DETAIL)
    return redis_client


async def _issuer_from_privy(token: str | None, privy_user_id: str) -> tuple[int, int | None]:
    """Verify a Privy access token (always, whatever PRIVY_TOKEN_VERIFICATION
    says) and return (user_id, None). The key is chosen at redeem time."""
    try:
        verify_privy_access_token(token, expected_sub=privy_user_id)
    except PrivyTokenError as e:
        raise HTTPException(status_code=401, detail="invalid_privy_token") from e
    user = await asyncio.to_thread(get_user_by_privy_id, privy_user_id)
    if not user or user.get("id") is None:
        raise HTTPException(status_code=404, detail="user_not_found")
    return int(user["id"]), None


async def _issuer_from_api_key(
    credentials: HTTPAuthorizationCredentials | None, request: Request
) -> tuple[int, int | None]:
    """Validate the API key the normal way and return (user_id, key row id)."""
    api_key = await get_api_key(credentials, request)
    user = await asyncio.to_thread(get_user, api_key)
    if not user or user.get("id") is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return int(user["id"]), user.get("key_id")


@router.post(
    "/auth/transfer-code",
    response_model=TransferCodeResponse,
    tags=["authentication"],
)
async def issue_transfer_code(
    request: Request,
    response: Response,
    body: TransferCodeRequest | None = None,
) -> TransferCodeResponse:
    """Issue a 60-second, single-use code for the caller's own account."""
    _no_store(response)
    await _enforce_rate_limit(get_client_ip(request), AuthRateLimitType.SESSION_TRANSFER_ISSUE)

    credentials = await _bearer(request)
    if body is not None and body.privy_user_id:
        token = credentials.credentials if credentials else None
        user_id, key_id = await _issuer_from_privy(token, body.privy_user_id)
    else:
        user_id, key_id = await _issuer_from_api_key(credentials, request)

    redis_client = _require_redis()
    code = secrets.token_urlsafe(_CODE_BYTES)
    record = json.dumps(
        {"user_id": user_id, "key_id": key_id, "created_at": time.time(), "used_at": None}
    )
    try:
        await asyncio.to_thread(redis_client.setex, _redis_key(code), CODE_TTL_SECONDS, record)
    except Exception as e:
        logger.error("session_transfer issue: store failed (%s)", type(e).__name__)
        raise HTTPException(status_code=503, detail=UNAVAILABLE_DETAIL) from e

    logger.info("session_transfer code issued user_id=%s", user_id)
    return TransferCodeResponse(code=code, expires_in=CODE_TTL_SECONDS)


def _consume(redis_client, code: str) -> dict[str, Any] | None:
    """Atomically take the record for this code (GETDEL). None if absent."""
    raw = redis_client.getdel(_redis_key(code))
    if not raw:
        return None
    raw = raw.decode() if isinstance(raw, bytes) else raw
    try:
        record = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    created_at = record.get("created_at")
    # Belt and braces in case a TTL was ever lost.
    if not isinstance(created_at, (int, float)):
        return None
    if time.time() - created_at > CODE_TTL_SECONDS:
        return None
    return record


def _load_key_row(user_id: int, key_id: int | None) -> dict[str, Any] | None:
    """The active key to hand back: the one that issued the code, else the
    user's primary (same order as login: primary first, then oldest)."""
    client = supabase_config.get_supabase_client()
    query = (
        client.table(API_KEYS_TABLE)
        .select(_KEY_COLUMNS)
        .eq("user_id", user_id)
        .eq("is_active", True)
    )
    if key_id is not None:
        query = query.eq("id", key_id)
    rows = list(query.execute().data or [])
    if not rows:
        return None
    rows.sort(key=lambda r: (not r.get("is_primary", False), str(r.get("created_at") or "")))
    return rows[0]


@router.post(
    "/auth/transfer-code/redeem",
    response_model=RedeemResponse,
    tags=["authentication"],
)
async def redeem_transfer_code(
    body: RedeemRequest, request: Request, response: Response
) -> RedeemResponse:
    """Trade a transfer code for the API key and basic profile. Works once."""
    _no_store(response)
    await _enforce_rate_limit(get_client_ip(request), AuthRateLimitType.SESSION_TRANSFER_REDEEM)

    redis_client = _require_redis()
    try:
        record = await asyncio.to_thread(_consume, redis_client, body.code)
    except Exception as e:
        logger.error("session_transfer redeem: store failed (%s)", type(e).__name__)
        raise HTTPException(status_code=503, detail=UNAVAILABLE_DETAIL) from e

    if record is None or record.get("user_id") is None:
        raise HTTPException(status_code=400, detail=INVALID_CODE_DETAIL)

    user_id = int(record["user_id"])
    key_id = record.get("key_id")
    try:
        key_row = await asyncio.to_thread(_load_key_row, user_id, key_id)
    except Exception as e:
        logger.error("session_transfer redeem: key lookup failed (%s)", type(e).__name__)
        raise HTTPException(status_code=503, detail=UNAVAILABLE_DETAIL) from e

    api_key = plaintext_from_row(key_row)
    if not api_key:
        # Key revoked or undecryptable since the code was issued.
        logger.warning("session_transfer redeem: no usable key user_id=%s", user_id)
        raise HTTPException(status_code=400, detail=INVALID_CODE_DETAIL)

    user = await asyncio.to_thread(get_user_by_id, user_id) or {}
    logger.info("session_transfer code redeemed user_id=%s", user_id)
    return RedeemResponse(
        api_key=api_key,
        user_id=user_id,
        privy_user_id=user.get("privy_user_id"),
        display_name=user.get("username") or user.get("display_name"),
        email=user.get("email"),
        auth_method=user.get("auth_method"),
    )
