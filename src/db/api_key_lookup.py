"""Central API-key lookup by ``key_hash`` (stage 2 of removing plaintext keys).

Every place that needs to find a row for an API key the caller presented goes
through this module instead of filtering on the plaintext ``api_key`` column.

- ``api_keys_new`` rows are found by ``key_hash`` (``sha256_key_hash``).
- ``rate_limit_usage`` rows are written and read by ``key_hash``; the old
  ``api_key`` column only gets a non-secret placeholder.
- New keys are stored as hash + Fernet ciphertext + last4, never plaintext.

Temporary fallback: while ``API_KEY_PLAINTEXT_FALLBACK`` is on (default
"true" for this stage), a lookup that misses by hash is retried against the
plaintext column. Each fallback hit logs a WARNING with row ids only -- never
the key -- so ops can watch it drop to zero before stage 3 nulls the
plaintext columns (supabase/staged-migrations/*_null_plaintext_api_keys.sql).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from src.utils.crypto import decrypt_api_key, encrypt_api_key, last4, sha256_key_hash

logger = logging.getLogger(__name__)

API_KEYS_TABLE = "api_keys_new"
USERS_TABLE = "users"
RATE_LIMIT_USAGE_TABLE = "rate_limit_usage"

FALLBACK_ENV_VAR = "API_KEY_PLAINTEXT_FALLBACK"
# Prefix for the non-secret value written into legacy NOT NULL / UNIQUE
# ``api_key`` columns that now carry a hash reference instead of the key.
HASH_REF_PREFIX = "hash:"
_HASH_REF_LEN = 12

_FALSE_VALUES = {"0", "false", "no", "off"}
_salt_error_logged = False


class _EmptyResult:
    """Stand-in for a PostgREST response when no query could be run."""

    data: list[dict[str, Any]] = []
    count = 0


def plaintext_fallback_enabled() -> bool:
    """True while lookups may still fall back to the plaintext column."""
    return os.getenv(FALLBACK_ENV_VAR, "true").strip().lower() not in _FALSE_VALUES


def key_hash_for(api_key: str) -> str:
    """Return the lookup hash stored in ``key_hash`` for this API key."""
    return sha256_key_hash(api_key)


def hash_ref(key_hash: str) -> str:
    """Non-secret placeholder for legacy ``api_key`` columns (e.g. 'hash:1a2b...')."""
    return f"{HASH_REF_PREFIX}{key_hash[:_HASH_REF_LEN]}"


def _try_key_hash(api_key: str) -> str | None:
    global _salt_error_logged
    try:
        return key_hash_for(api_key)
    except RuntimeError:
        if not _salt_error_logged:
            logger.error("KEY_HASH_SALT is not configured; API key hash lookup is unavailable")
            _salt_error_logged = True
        return None


def _row_ids(rows: Any) -> list[Any]:
    if not isinstance(rows, list):
        return []
    return [row.get("id") for row in rows[:5] if isinstance(row, dict)]


def log_plaintext_fallback(table: str, rows: Any, operation: str = "select") -> None:
    """WARNING for ops: a lookup only matched via the plaintext column.

    Logs table, operation and row ids. Never the key.
    """
    logger.warning(
        "api_key_plaintext_fallback table=%s op=%s row_ids=%s",
        table,
        operation,
        _row_ids(rows),
    )


def query_by_key(
    api_key: str,
    build: Callable[[str, str], Any],
    *,
    table: str = API_KEYS_TABLE,
    operation: str = "select",
) -> Any:
    """Run ``build(column, value).execute()`` by key_hash, then plaintext if allowed.

    ``build`` receives ``("key_hash", <hash>)`` first. Only if that returns no
    rows (or the hash cannot be computed) and the fallback flag is on is it
    called again with ``("api_key", <plaintext>)``.
    """
    if not api_key:
        return _EmptyResult()

    result = None
    key_hash = _try_key_hash(api_key)
    if key_hash is not None:
        result = build("key_hash", key_hash).execute()
        if getattr(result, "data", None):
            return result

    if not plaintext_fallback_enabled():
        return result if result is not None else _EmptyResult()

    fallback = build("api_key", api_key).execute()
    if getattr(fallback, "data", None):
        log_plaintext_fallback(table, fallback.data, operation)
        return fallback
    return result if result is not None else fallback


def _with_id(columns: str) -> str:
    cols = [c.strip() for c in columns.split(",")]
    if columns.strip() == "*" or "id" in cols or any("*" in c for c in cols):
        return columns
    return "id, " + columns


def find_key_rows(
    client,
    api_key: str,
    columns: str = "*",
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """All ``api_keys_new`` rows for this key (normally 0 or 1)."""

    def build(column: str, value: str):
        query = client.table(API_KEYS_TABLE).select(_with_id(columns)).eq(column, value)
        for name, filter_value in (filters or {}).items():
            query = query.eq(name, filter_value)
        return query

    return list(query_by_key(api_key, build).data or [])


def find_key_row(
    client,
    api_key: str,
    columns: str = "*",
    filters: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """The ``api_keys_new`` row for this key, looked up by key_hash."""
    rows = find_key_rows(client, api_key, columns, filters)
    return rows[0] if rows else None


def find_legacy_user_row(client, api_key: str, columns: str = "*") -> dict[str, Any] | None:
    """Look a user up by the legacy ``users.api_key`` column -- fallback only.

    Returns None when the fallback flag is off. Callers try
    ``api_keys_new`` (by hash) first.
    """
    if not api_key or not plaintext_fallback_enabled():
        return None
    result = client.table(USERS_TABLE).select(_with_id(columns)).eq("api_key", api_key).execute()
    rows = getattr(result, "data", None) or []
    if not rows:
        return None
    log_plaintext_fallback(USERS_TABLE, rows)
    return rows[0]


def find_user_row(client, api_key: str, columns: str = "*") -> dict[str, Any] | None:
    """The ``users`` row that owns this key.

    Resolves through ``api_keys_new`` by hash first, then the legacy
    ``users.api_key`` column behind the fallback flag.
    """
    key_row = find_key_row(client, api_key, "id, user_id")
    if key_row and key_row.get("user_id") is not None:
        result = (
            client.table(USERS_TABLE)
            .select(_with_id(columns))
            .eq("id", key_row["user_id"])
            .execute()
        )
        rows = getattr(result, "data", None) or []
        if rows:
            return rows[0]
    return find_legacy_user_row(client, api_key, columns)


def resolve_user_id(client, api_key: str) -> int | None:
    """User id for this key (api_keys_new by hash, then legacy column)."""
    key_row = find_key_row(client, api_key, "id, user_id")
    if key_row and key_row.get("user_id") is not None:
        return key_row["user_id"]
    legacy = find_legacy_user_row(client, api_key, "id")
    return legacy.get("id") if legacy else None


def plaintext_from_row(row: dict[str, Any] | None) -> str | None:
    """Recover the key from an ``api_keys_new`` row for display to its owner.

    Uses the plaintext column while it still exists, otherwise decrypts
    ``encrypted_key``. Returns None if neither is available. Never logs the key.
    """
    if not row:
        return None
    stored = row.get("api_key")
    if stored and not str(stored).startswith(HASH_REF_PREFIX):
        return stored
    token = row.get("encrypted_key")
    if not token:
        return None
    try:
        return decrypt_api_key(token, row.get("key_version"))
    except (RuntimeError, ValueError) as e:
        logger.error(
            "Could not decrypt API key row id=%s: %s",
            row.get("id"),
            type(e).__name__,
        )
        return None


def key_label(row: dict[str, Any] | None) -> str:
    """Non-secret display label for a key row, e.g. '...a1b2'."""
    if not row:
        return "unknown"
    tail = row.get("last4")
    if not tail:
        stored = row.get("api_key")
        if stored and not str(stored).startswith(HASH_REF_PREFIX):
            tail = last4(stored)
    return f"...{tail}" if tail else f"key#{row.get('id', '?')}"


def new_key_storage_fields(api_key: str) -> dict[str, Any]:
    """Columns to store for a new key: hash, ciphertext, last4 -- no plaintext.

    Raises ValueError if hashing or encryption is not configured, because a
    key stored without them could neither authenticate nor be shown again.
    """
    try:
        key_hash = key_hash_for(api_key)
    except RuntimeError as e:
        raise ValueError("API key creation requires KEY_HASH_SALT (16+ characters).") from e
    try:
        encrypted_key, key_version = encrypt_api_key(api_key)
    except RuntimeError as e:
        raise ValueError(
            "API key creation requires encryption (KEY_VERSION and KEYRING_<version>)."
        ) from e
    return {
        "api_key": None,
        "key_hash": key_hash,
        "encrypted_key": encrypted_key,
        "key_version": key_version,
        "last4": last4(api_key),
    }


def rate_limit_key_fields(api_key: str) -> dict[str, str]:
    """``rate_limit_usage`` identity columns for a key: hash, no plaintext.

    ``api_key`` gets a placeholder so the existing
    UNIQUE (api_key, window_type, window_start) constraint keeps working.
    """
    key_hash = key_hash_for(api_key)
    return {"key_hash": key_hash, "api_key": hash_ref(key_hash)}


def safe_db_error(error: Exception) -> str:
    """Describe a DB error without echoing row data (PostgREST ``details``).

    PostgREST errors carry ``details``/``hint`` that can contain the whole
    failing row -- for api_keys_new that included the plaintext key. Only the
    error code and message are kept.
    """
    if _is_postgrest_error(error):
        code = getattr(error, "code", None)
        message = getattr(error, "message", None)
        return f"{type(error).__name__} code={code} message={message}"
    return str(error)


def _is_postgrest_error(error: Exception) -> bool:
    try:
        from postgrest import APIError
    except Exception:  # pragma: no cover - postgrest is a hard dependency
        return False
    return isinstance(error, APIError)
