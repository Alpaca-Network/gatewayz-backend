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
from dataclasses import dataclass
from typing import Literal

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


def privy_verification_mode() -> Literal["enforce", "log", "off"]:
    """
    Resolve the effective Privy token verification mode.

    ``Config.PRIVY_TOKEN_VERIFICATION`` wins when set. Otherwise default to
    "enforce" when a verification key is configured (the key being present
    is the signal that the rollout is ready to enforce), else "log" so
    environments without the key don't 401 every login.
    """
    configured = (Config.PRIVY_TOKEN_VERIFICATION or "").strip().lower()
    if configured in {"enforce", "log", "off"}:
        return configured  # type: ignore[return-value]

    return "enforce" if Config.PRIVY_VERIFICATION_KEY else "log"


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

    verification_key = Config.PRIVY_VERIFICATION_KEY
    if not verification_key:
        raise PrivyTokenError("not_configured", "PRIVY_VERIFICATION_KEY is not configured")

    if not Config.PRIVY_APP_ID:
        raise PrivyTokenError("not_configured", "PRIVY_APP_ID is not configured")

    try:
        payload = jwt.decode(
            token,
            key=_normalize_pem(verification_key),
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
