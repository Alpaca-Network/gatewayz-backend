"""Secret fingerprint + age registry (gatewayz-backend Phase D, D1).

``GET /admin/status``'s ``secrets`` block (``src/routes/admin_status.py``)
reports more than presence for a fixed allow-list of secret env vars: it
also reports how long a secret has held its current value, so a stale
credential shows up as a rotation reminder instead of silently aging
forever. This module never sees, stores, logs, or exposes a secret's
value -- only a one-way sha256 fingerprint of it, truncated to 12 hex
chars, used purely to detect "did this change" across process restarts.

Redis-backed (hash key ``ops:secret:{NAME}`` -> ``{fp, first_seen_at}``)
with an in-process dict fallback when Redis is unavailable, mirroring the
try/except + safe-default convention ``src/services/ops/job_runs.py``
already uses for the same jobs/status surface. ``record_secret_fingerprints``
must NEVER raise -- it runs once at startup (``src/services/startup.py``)
and a failure here must never block the app from accepting traffic.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import UTC, datetime
from typing import Any

from src.config.redis_config import get_redis_client

logger = logging.getLogger(__name__)

_KEY_PREFIX = "ops:secret:"

# Fixed allow-list of secret env vars this registry fingerprints and ages.
# Shared with src/routes/admin_status.py's `secrets` block -- import this
# list from here rather than duplicating it. Never add a name here without
# also confirming nothing downstream can ever report more than
# {present, first_seen_at, age_days, rotate_due, fingerprint_known} for it --
# never the value, never the fingerprint itself.
SECRET_NAMES: list[str] = [
    "ADMIN_API_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_KEY",
    "RESEND_API_KEY",
    "PRIVY_APP_ID",
    "PRIVY_VERIFICATION_KEY",
    "WAYZ_FAUCET_MINTER_PRIVATE_KEY",
    "WAYZ_REWARDS_POOL_PRIVATE_KEY",
    "STRIPE_SECRET_KEY",
    "SENTRY_DSN",
    "GATEWAYZ_AUTH_BRIDGE_SECRET",
    "UPSTREAM_ABUSE_PSEUDONYM",
]

# Default salt mixed into the fingerprint below. Overridable via the
# SECRET_FP_SALT env var. Changing the salt (deliberately, or by an
# environment losing the override) changes every fingerprint at once, so
# the very next record_secret_fingerprints() call treats every currently
# -present secret as freshly rotated and resets its first_seen_at -- that
# is an accepted, documented side effect of rotating the salt itself, not a
# bug.
_DEFAULT_SALT = "gatewayz-secrets-registry-v1"

_DEFAULT_ROTATION_DAYS = 90

# In-process fallback store, used whenever Redis is unavailable (e.g. local
# dev with no REDIS_URL configured). Not shared across processes/instances --
# same limitation as every other in-memory fallback in this codebase (see
# src/config/redis_config.py, src/services/ops/job_runs.py).
_fallback_store: dict[str, dict[str, str]] = {}


def _salt() -> str:
    return os.environ.get("SECRET_FP_SALT", _DEFAULT_SALT)


def _rotation_days() -> int:
    try:
        return int(os.environ.get("SECRET_ROTATION_DAYS", str(_DEFAULT_ROTATION_DAYS)))
    except ValueError:
        return _DEFAULT_ROTATION_DAYS


def fingerprint(value: str) -> str:
    """One-way sha256(salt + value), truncated to 12 hex chars.

    Stable across restarts for the same value (so the same secret always
    produces the same fingerprint) but never reversible to the original
    value -- this is the only representation of a secret this module ever
    stores, logs, or returns, and even it is never exposed outside this
    module (never returned by secret_ages()).
    """
    digest = hashlib.sha256((_salt() + value).encode()).hexdigest()
    return digest[:12]


def _redis_key(name: str) -> str:
    return f"{_KEY_PREFIX}{name}"


def _read_record(client: Any, name: str) -> dict[str, str] | None:
    """{fp, first_seen_at} for `name`, from Redis if available else the
    in-process fallback. Never raises."""
    if client is not None:
        try:
            raw = client.hgetall(_redis_key(name))
            if raw:
                return {"fp": raw.get("fp"), "first_seen_at": raw.get("first_seen_at")}
        except Exception as e:
            logger.warning(f"secrets_registry: Redis read failed for '{name}': {e}")
    return _fallback_store.get(name)


def _write_record(client: Any, name: str, fp: str, first_seen_at: str) -> None:
    """Persist {fp, first_seen_at} for `name` to Redis, falling back to the
    in-process store on any failure. Never raises."""
    record = {"fp": fp, "first_seen_at": first_seen_at}
    if client is not None:
        try:
            client.hset(_redis_key(name), mapping=record)
            return
        except Exception as e:
            logger.warning(
                f"secrets_registry: Redis write failed for '{name}', "
                f"falling back to in-process store: {e}"
            )
    _fallback_store[name] = record


def record_secret_fingerprints() -> None:
    """Fingerprint every present secret in SECRET_NAMES and persist it.

    Called once at startup. For each secret that is actually set: if its
    fingerprint matches what's already stored, first_seen_at is left
    untouched (this is the normal "same secret as last restart" case). If it
    differs -- including the very first time a secret is ever recorded --
    first_seen_at is reset to now; if something WAS already stored (a real
    rotation, not first-ever recording), an info line "secret rotated: NAME"
    is logged -- the name only, never the value or the fingerprint. Absent
    secrets are skipped entirely (no record, so secret_ages() reports
    fingerprint_known=False for them). Never raises.
    """
    try:
        client = None
        try:
            client = get_redis_client()
        except Exception as e:
            logger.debug(f"secrets_registry: Redis client unavailable: {e}")

        now = datetime.now(UTC).isoformat()
        for name in SECRET_NAMES:
            value = os.environ.get(name)
            if not value:
                continue
            fp = fingerprint(value)
            existing = _read_record(client, name)
            if existing is not None and existing.get("fp") == fp:
                continue  # unchanged -- preserve first_seen_at
            _write_record(client, name, fp, now)
            if existing is not None:
                logger.info(f"secret rotated: {name}")
    except Exception as e:
        logger.warning(f"secrets_registry: record_secret_fingerprints failed: {e}")


def secret_ages() -> dict[str, dict[str, Any]]:
    """{NAME: {present, first_seen_at, age_days, rotate_due, fingerprint_known}}
    for every name in SECRET_NAMES.

    ``present`` is independent of whether a fingerprint was ever recorded --
    a secret can be present today but fingerprint_known=False if
    record_secret_fingerprints() hasn't run yet in this process (or ever, in
    this environment). Never exposes a fingerprint or value. Never raises.
    """
    client = None
    try:
        client = get_redis_client()
    except Exception as e:
        logger.debug(f"secrets_registry: Redis client unavailable: {e}")

    rotation_days = _rotation_days()
    now = datetime.now(UTC)
    result: dict[str, dict[str, Any]] = {}
    for name in SECRET_NAMES:
        present = bool(os.environ.get(name))
        record = _read_record(client, name)

        first_seen_at = record.get("first_seen_at") if record else None
        age_days: int | None = None
        if first_seen_at:
            try:
                first_seen = datetime.fromisoformat(first_seen_at)
                age_days = (now - first_seen).days
            except ValueError:
                age_days = None

        result[name] = {
            "present": present,
            "first_seen_at": first_seen_at,
            "age_days": age_days,
            "rotate_due": age_days is not None and age_days >= rotation_days,
            "fingerprint_known": record is not None,
        }
    return result
