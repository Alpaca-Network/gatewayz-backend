"""
Privy Access Token Verification (gatewayz-backend#2248, #2254 prerequisite)

`POST /auth` historically trusted the client-supplied Privy user object
(`request.user.id`) with no proof the caller actually holds that Privy
session. This module verifies the Privy *access token* server-side so a
request can only act as the Privy DID it presents a valid, unexpired,
correctly-signed token for.

Privy issues access tokens as ES256-signed JWTs (`iss=privy.io`,
`aud=<app id>`, `sub=<privy DID>`). The verification key is the app's
public key from the Privy dashboard (PEM, `-----BEGIN PUBLIC KEY-----`).
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Literal

import httpx
import jwt

from src.config import Config

logger = logging.getLogger(__name__)

PrivyTokenErrorReason = Literal[
    "missing",
    "expired",
    "bad_signature",
    "sub_mismatch",
    "malformed",
    "not_configured",
]

# Clock skew tolerance for exp/iat comparisons.
_LEEWAY_SECONDS = 60


@dataclass(frozen=True)
class PrivyTokenClaims:
    """Claims extracted from a verified Privy access token."""

    sub: str
    sid: str | None
    exp: int
    iat: int | None


class PrivyTokenError(Exception):
    """Raised when a Privy access token fails verification."""

    def __init__(self, reason: PrivyTokenErrorReason, message: str | None = None):
        self.reason = reason
        super().__init__(message or reason)


def _normalize_pem(key: str) -> str:
    """
    Normalize a PEM public key sourced from an env var.

    Railway (and similar dashboards) often store multi-line PEM values with
    literal ``\\n`` escapes rather than real newlines. Normalize those back
    to newlines before handing the key to PyJWT/cryptography.
    """
    return key.replace("\\n", "\n").strip()


# ---------------------------------------------------------------------------
# JWKS (the dashboard no longer shows a PEM "verification key"; Privy serves
# the app's ES256 public keys at a public JWKS URL, and rotates them — so a
# single PEM would reject tokens signed by the other kid). We fetch the JWKS
# for PRIVY_APP_ID, cache it in-process, and pick the key by the token's
# ``kid``. PRIVY_VERIFICATION_KEY (PEM) remains a manual override/fallback.
# ---------------------------------------------------------------------------

_JWKS_URL_TEMPLATE = "https://auth.privy.io/api/v1/apps/{app_id}/jwks.json"
_JWKS_TTL_SECONDS = 6 * 60 * 60
_JWKS_FETCH_TIMEOUT = 5.0
_jwks_cache: dict[str, tuple[float, dict[str, jwt.PyJWK]]] = {}
_jwks_lock = threading.Lock()


def _jwks_url(app_id: str) -> str:
    return (Config.PRIVY_JWKS_URL or "").strip() or _JWKS_URL_TEMPLATE.format(app_id=app_id)


def _fetch_jwks(app_id: str) -> dict[str, jwt.PyJWK]:
    resp = httpx.get(_jwks_url(app_id), timeout=_JWKS_FETCH_TIMEOUT)
    resp.raise_for_status()
    keys: dict[str, jwt.PyJWK] = {}
    for entry in resp.json().get("keys", []):
        kid = entry.get("kid")
        if not kid:
            continue
        try:
            keys[kid] = jwt.PyJWK.from_dict(entry)
        except Exception as e:  # pragma: no cover - defensive; malformed key entries
            logger.warning("Skipping unparseable Privy JWK %s: %s", kid, type(e).__name__)
    if not keys:
        raise PrivyTokenError("not_configured", "Privy JWKS returned no usable keys")
    return keys


def _get_jwks(app_id: str, *, force_refresh: bool = False) -> dict[str, jwt.PyJWK]:
    now = time.monotonic()
    with _jwks_lock:
        cached = _jwks_cache.get(app_id)
        if cached and not force_refresh and now - cached[0] < _JWKS_TTL_SECONDS:
            return cached[1]
    try:
        keys = _fetch_jwks(app_id)
    except PrivyTokenError:
        raise
    except Exception as e:
        # Network/HTTP failure: serve a stale cache if we have one, else fail closed.
        with _jwks_lock:
            cached = _jwks_cache.get(app_id)
        if cached:
            logger.warning("Privy JWKS refresh failed (%s); using cached keys", type(e).__name__)
            return cached[1]
        raise PrivyTokenError("jwks_unavailable", "Could not fetch Privy JWKS") from e
    with _jwks_lock:
        _jwks_cache[app_id] = (now, keys)
    return keys


def _resolve_signing_key(token: str, app_id: str):
    """Return the key object to verify ``token`` with, from PEM env or JWKS."""
    if Config.PRIVY_VERIFICATION_KEY:
        return _normalize_pem(Config.PRIVY_VERIFICATION_KEY)
    try:
        header = jwt.get_unverified_header(token)
    except jwt.DecodeError as e:
        raise PrivyTokenError("malformed", "Privy access token is malformed") from e
    kid = header.get("kid")
    if not kid:
        raise PrivyTokenError("bad_signature", "Privy access token has no kid header")
    keys = _get_jwks(app_id)
    if kid not in keys:
        # Unknown kid: Privy may have rotated since we cached — refresh once.
        keys = _get_jwks(app_id, force_refresh=True)
    key = keys.get(kid)
    if key is None:
        raise PrivyTokenError("bad_signature", "Privy access token signed by an unknown key")
    return key.key


def clear_jwks_cache() -> None:
    """Test/ops helper."""
    with _jwks_lock:
        _jwks_cache.clear()


def privy_verification_configured() -> bool:
    """True when tokens can be verified: PEM key set, or an app id (JWKS)."""
    return bool(Config.PRIVY_VERIFICATION_KEY) or bool(Config.PRIVY_APP_ID)


def privy_verification_mode() -> Literal["enforce", "log", "off"]:
    """
    Resolve the effective Privy token verification mode.

    ``Config.PRIVY_TOKEN_VERIFICATION`` wins when set. Otherwise default to
    "enforce" whenever tokens CAN be verified -- a PEM key or a PRIVY_APP_ID
    (JWKS) -- else "log" so environments with neither don't 401 every login.

    JWKS-only setups used to default to "log". The Privy dashboard no longer
    exposes a PEM, so production silently fell back to "log" and POST /auth
    accepted forged tokens: a known DID was enough to get that account's API
    key. "log" is a rollout aid you opt into, never a default.
    """
    configured = (Config.PRIVY_TOKEN_VERIFICATION or "").strip().lower()
    if configured in {"enforce", "log", "off"}:
        return configured  # type: ignore[return-value]

    return "enforce" if privy_verification_configured() else "log"


def verify_privy_access_token(token: str | None, expected_sub: str) -> PrivyTokenClaims:
    """
    Verify a Privy access token and return its claims.

    Args:
        token: The Privy access token (JWT) presented by the client.
        expected_sub: The Privy DID the caller claims to be
            (``request.user.id``); must match the token's ``sub`` claim.

    Returns:
        The verified token's claims.

    Raises:
        PrivyTokenError: with a machine-readable ``reason`` describing why
            verification failed.
    """
    if not token:
        raise PrivyTokenError("missing", "Privy access token was not provided")

    if not Config.PRIVY_APP_ID:
        raise PrivyTokenError("not_configured", "PRIVY_APP_ID is not configured")

    if not privy_verification_configured():
        raise PrivyTokenError("not_configured", "PRIVY_VERIFICATION_KEY is not configured")

    signing_key = _resolve_signing_key(token, Config.PRIVY_APP_ID)

    try:
        payload = jwt.decode(
            token,
            key=signing_key,
            algorithms=["ES256"],
            audience=Config.PRIVY_APP_ID,
            issuer="privy.io",
            leeway=_LEEWAY_SECONDS,
        )
    except jwt.ExpiredSignatureError as e:
        raise PrivyTokenError("expired", "Privy access token has expired") from e
    except (jwt.InvalidSignatureError, jwt.InvalidAudienceError, jwt.InvalidIssuerError) as e:
        raise PrivyTokenError("bad_signature", "Privy access token failed verification") from e
    except jwt.DecodeError as e:
        raise PrivyTokenError("malformed", "Privy access token is malformed") from e
    except jwt.InvalidTokenError as e:
        # Catch-all for other PyJWT validation failures (e.g. immature token).
        raise PrivyTokenError("bad_signature", "Privy access token failed verification") from e

    sub = payload.get("sub")
    if not sub or sub != expected_sub:
        raise PrivyTokenError(
            "sub_mismatch",
            "Token subject does not match the requesting user",
        )

    return PrivyTokenClaims(
        sub=sub,
        sid=payload.get("sid"),
        exp=payload["exp"],
        iat=payload.get("iat"),
    )
