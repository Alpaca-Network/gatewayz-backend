"""BYOK (bring-your-own-key) storage — Phase 5 of the direct-supply pivot.

Stores a customer's own upstream provider API key, encrypted at rest with the
Fernet keyring. The plaintext key is recovered ONLY server-side to make the
upstream provider call (via ``get_decrypted_provider_key``); it is never
returned to any client. See docs/BUSINESS_PIVOT_DIRECT_SUPPLY.md.
"""

import logging
from typing import Any

from src.config.supabase_config import get_supabase_client
from src.utils.crypto import decrypt_api_key, encrypt_api_key, last4

logger = logging.getLogger(__name__)

_TABLE = "user_provider_keys"


def upsert_provider_key(user_id: int, provider_slug: str, plaintext_key: str) -> dict[str, Any]:
    """Encrypt and store (or replace) a user's BYOK key for a provider.

    Returns a safe record (no plaintext, no ciphertext) suitable for API
    responses. Raises RuntimeError if encryption is not configured (we never
    store a provider key in plaintext).
    """
    encrypted_key, key_version = encrypt_api_key(plaintext_key)
    row = {
        "user_id": user_id,
        "provider_slug": provider_slug,
        "encrypted_key": encrypted_key,
        "key_version": key_version,
        "key_last4": last4(plaintext_key),
        "is_active": True,
    }
    client = get_supabase_client()
    client.table(_TABLE).upsert(row, on_conflict="user_id,provider_slug").execute()
    return {
        "user_id": user_id,
        "provider_slug": provider_slug,
        "key_last4": row["key_last4"],
        "is_active": True,
    }


def list_provider_keys(user_id: int) -> list[dict[str, Any]]:
    """List a user's BYOK keys — masked (last4 only), never the secret."""
    client = get_supabase_client()
    result = (
        client.table(_TABLE)
        .select("provider_slug,key_last4,is_active,created_at,updated_at")
        .eq("user_id", user_id)
        .eq("is_active", True)
        .execute()
    )
    return result.data or []


def delete_provider_key(user_id: int, provider_slug: str) -> bool:
    """Hard-delete a user's BYOK key for a provider. Returns True if removed."""
    client = get_supabase_client()
    result = (
        client.table(_TABLE)
        .delete()
        .eq("user_id", user_id)
        .eq("provider_slug", provider_slug)
        .execute()
    )
    return bool(result.data)


def get_decrypted_provider_key(user_id: int, provider_slug: str) -> str | None:
    """Return the plaintext BYOK key for (user, provider), or None.

    SERVER-SIDE ONLY — the result is used to authenticate the upstream provider
    call and must never be returned to a client. Returns None when the user has
    no active BYOK key for the provider or decryption fails.
    """
    try:
        client = get_supabase_client()
        result = (
            client.table(_TABLE)
            .select("encrypted_key,key_version")
            .eq("user_id", user_id)
            .eq("provider_slug", provider_slug)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )
        if not result.data:
            return None
        row = result.data[0]
        return decrypt_api_key(row["encrypted_key"], row.get("key_version"))
    except Exception as e:
        logger.warning("BYOK key lookup failed for provider %s: %s", provider_slug, e)
        return None
