"""Dispute content vault: opt-in, encrypted, 72-hour retention of content bundles.

Without the vault, Gatewayz stores no prompt or output text and a user who
contests a v2 usage line must keep and reveal the content bundle themselves.
With it, a request on a job key that sends ``X-Gatewayz-Dispute-Vault: on`` has
its bundle -- the same bytes ``job_usage_v2.content_hash`` fingerprints -- kept
encrypted until the dispute window closes (72 h after the job closes; see
migration 20261028120000). Contesting is then one call:

    POST /v1/jobs/{job_id}/usage/{i}/reveal   -> {content_uri, content_hash, expires_at}

and the GenLayer validators fetch ``content_uri`` and hash what they get back.

Encryption is AES-256-GCM with a key from DISPUTE_VAULT_KEYS; the database
holds ciphertext only. The associated data binds each ciphertext to its
request_id and content_hash, so a row copied onto another request cannot be
decrypted. Every read re-checks sha256(plaintext) == content_hash before serving.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from src.services import job_usage_v2

logger = logging.getLogger(__name__)

HEADER = "X-Gatewayz-Dispute-Vault"
# The CostDispute contract refuses content bundles larger than this.
MAX_BUNDLE_BYTES = 256_000
REVEAL_TTL = timedelta(hours=48)


class VaultConfigError(RuntimeError):
    pass


def enabled() -> bool:
    """On only with v2 usage lines on, the flag on, and a key configured."""
    from src.config.config import Config

    if not getattr(Config, "DISPUTE_VAULT_ENABLED", False) or not job_usage_v2.enabled():
        return False
    try:
        _keys()
    except VaultConfigError as e:
        logger.error("dispute vault enabled but misconfigured: %s", e)
        return False
    return True


def opted_in(request: Any) -> bool:
    """True when the client asked for this request's content to be kept for disputes."""
    headers = getattr(request, "headers", None)
    value = headers.get(HEADER) if headers is not None else None
    return str(value or "").strip().lower() in {"on", "1", "true", "yes"}


def _keys() -> list[tuple[str, bytes]]:
    """[(key_id, 32-byte key)], newest first. DISPUTE_VAULT_KEYS = "k2:<b64>,k1:<b64>".

    The first key encrypts; all of them decrypt, so a key can be rotated without
    losing bundles still inside their window.
    """
    from src.config.config import Config

    raw = getattr(Config, "DISPUTE_VAULT_KEYS", None) or ""
    out = []
    for part in [p.strip() for p in raw.split(",") if p.strip()]:
        kid, _, b64 = part.partition(":")
        try:
            key = base64.b64decode(b64, validate=True)
        except Exception as e:
            raise VaultConfigError(f"key {kid!r} is not base64") from e
        if not kid or len(kid) > 32 or len(key) != 32:
            raise VaultConfigError(f"key {kid!r} must be '<id>:<base64 of 32 bytes>'")
        out.append((kid, key))
    if not out:
        raise VaultConfigError("DISPUTE_VAULT_KEYS is empty")
    return out


def _aad(request_id: str, content_hash: str) -> bytes:
    return f"gatewayz-dispute-vault-v1|{request_id}|{content_hash}".encode()


def encrypt(request_id: str, content_hash: str, plaintext: bytes) -> tuple[str, bytes, bytes]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    kid, key = _keys()[0]
    nonce = os.urandom(12)
    return kid, nonce, AESGCM(key).encrypt(nonce, plaintext, _aad(request_id, content_hash))


def decrypt(
    request_id: str, content_hash: str, key_id: str, nonce: bytes, ciphertext: bytes
) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = dict(_keys()).get(key_id)
    if key is None:
        raise VaultConfigError(f"no key {key_id!r} to decrypt with")
    return AESGCM(key).decrypt(nonce, ciphertext, _aad(request_id, content_hash))


def store(
    job_id: str, request_id: str | None, messages: Any, output: str, content_hash: str
) -> bool:
    """Keep one request's bundle, encrypted. Never raises into the request path.

    Skipped (and logged) when the bundle does not hash to ``content_hash``, is
    larger than the contract accepts, or cannot be encoded: the line is then
    disputable only with content the user kept themselves.
    """
    if not request_id or content_hash == job_usage_v2.ZERO_HASH:
        return False
    try:
        bundle = job_usage_v2.canonical_json({"messages": messages, "output": output or ""})
        if job_usage_v2.sha256_hex(bundle) != content_hash:
            logger.error("dispute vault: bundle for %s does not match its content_hash", request_id)
            return False
        if len(bundle) > MAX_BUNDLE_BYTES:
            logger.info(
                "dispute vault: %s is %d bytes, over the contract limit; not kept",
                request_id,
                len(bundle),
            )
            return False
        kid, nonce, ct = encrypt(request_id, content_hash, bundle)
        from src.db import dispute_vault as db

        db.insert_content(request_id, job_id, content_hash, kid, nonce, ct)
        return True
    except Exception as e:  # report, never break the request
        logger.error("dispute vault: content for %s NOT kept: %s", request_id, e)
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def reveal(job_id: str, request_id: str) -> dict | None:
    """A short-lived public link to one kept bundle, or None if none is kept.

    The link expires after REVEAL_TTL or when the dispute window closes,
    whichever is first. Only the token's sha256 is stored.
    """
    from src.config.config import Config
    from src.db import dispute_vault as db

    row = db.get_content(request_id)
    if row is None or row["job_id"] != job_id:
        return None
    window_end = db.content_expiry(job_id)
    now = datetime.now(UTC)
    if window_end is None or window_end <= now:
        return None
    expires_at = min(now + REVEAL_TTL, window_end)
    token = secrets.token_urlsafe(32)
    db.insert_reveal(_token_hash(token), request_id, expires_at)
    base = (getattr(Config, "DISPUTE_VAULT_PUBLIC_BASE", None) or "https://api.gatewayz.ai").rstrip(
        "/"
    )
    return {
        "request_id": request_id,
        "content_uri": f"{base}/v1/dispute-content/{token}",
        "content_hash": row["content_hash"],
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def open_reveal(token: str) -> bytes | None:
    """The exact bundle bytes behind a live reveal link, or None.

    Raises only if the stored plaintext no longer matches its content_hash, which
    must never be served quietly: a validator would refuse it anyway.
    """
    from src.db import dispute_vault as db

    if not token or len(token) > 128:
        return None
    rev = db.get_reveal(_token_hash(token))
    if rev is None:
        return None
    expires = datetime.fromisoformat(str(rev["expires_at"]).replace("Z", "+00:00"))
    if expires <= datetime.now(UTC):
        return None
    row = db.get_content(rev["request_id"])
    if row is None:
        return None
    window_end = db.content_expiry(row["job_id"])
    if window_end is None or window_end <= datetime.now(UTC):
        return None
    plain = decrypt(
        row["request_id"], row["content_hash"], row["key_id"], row["nonce"], row["ciphertext"]
    )
    if job_usage_v2.sha256_hex(plain) != row["content_hash"]:
        raise RuntimeError(f"dispute vault: {row['request_id']} does not hash to its content_hash")
    return plain
