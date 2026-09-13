"""
Privy app migration: lazy, server-verified adoption of legacy accounts
(docs/PRIVY_MIGRATION.md, 2026-09-13).

Gatewayz moved its Privy project from an old app id to a new one. Privy DIDs
(and embedded wallets) are scoped per app, so a returning user who logs in
against the new app presents a DID that has never been seen before -- without
this module, `/auth` would (correctly, by the M2 anti-takeover rule) treat
that as a brand-new account and the user would lose their credits, API keys,
and history.

This module closes that gap the *safe* way: it never trusts anything the
client sends. It asks Privy's own server API which email the new DID belongs
to, and only re-links an existing account when that server-confirmed email
matches exactly one legacy row. Every failure mode (Privy API down, ambiguous
match, no match, unverified email) falls through to the ordinary new-account
path -- adoption is a bonus, never a requirement for login to work.

Called from `src/routes/auth.py`'s verified-token path, only when
`Config.PRIVY_MIGRATION_MODE == "adopt"` and no row already matches the new
DID.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Any

import httpx

from src.config import Config
from src.config.supabase_config import get_supabase_client
from src.db.audit import record_audit

logger = logging.getLogger(__name__)

_PRIVY_USER_URL_TEMPLATE = "https://auth.privy.io/api/v1/users/{did}"
_LOOKUP_TIMEOUT_SECONDS = 5.0

AUDIT_ACTION = "auth.privy_migrated"


def migration_mode_is_adopt() -> bool:
    """True when the adoption path should run at all."""
    return Config.PRIVY_MIGRATION_MODE == "adopt"


def _did_hash(did: str) -> str:
    """Short, non-reversible fingerprint for logs/audit metadata -- never
    the DID itself (it's a stable per-person identifier we'd otherwise be
    writing to logs in the clear)."""
    return hashlib.sha256(did.encode()).hexdigest()[:12]


def _email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:12]


def _extract_verified_email(privy_user: dict[str, Any]) -> str | None:
    """
    Pull a trustworthy email out of a Privy `/users/{did}` response.

    Only two account types can vouch for an email here: a verified `email`
    linked account, or a `google_oauth`/`apple_oauth` account (Privy only
    creates those after the provider's own email verification -- there is no
    unverified variant). Anything else (wallet-only, phone, unlinked) yields
    no adoptable email, which is an expected outcome (see spec §"Risks":
    wallet-only logins cannot be adopted), not an error.
    """
    linked_accounts = privy_user.get("linked_accounts")
    if not isinstance(linked_accounts, list):
        return None

    for account in linked_accounts:
        if not isinstance(account, dict):
            continue
        account_type = account.get("type")
        if account_type == "email":
            address = account.get("address") or account.get("email")
            # Privy's own /users API does not surface a separate
            # "verified" flag for the primary email account -- an email
            # account only exists once its OTP/link has been verified.
            if address:
                return str(address).strip()
        elif account_type in ("google_oauth", "apple_oauth"):
            address = account.get("email")
            if address:
                return str(address).strip()

    return None


async def _fetch_privy_user(did: str) -> dict[str, Any] | None:
    """
    GET https://auth.privy.io/api/v1/users/{did} using the current app's
    server credentials.

    Returns the parsed user object, or None on ANY failure (network error,
    timeout, non-2xx, malformed body, missing credentials) -- callers must
    treat None as "cannot adopt right now", never as "no such user".
    """
    if not Config.PRIVY_APP_ID or not Config.PRIVY_APP_SECRET:
        logger.warning("privy_migration_lookup_failed reason=not_configured")
        return None

    basic = base64.b64encode(f"{Config.PRIVY_APP_ID}:{Config.PRIVY_APP_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {basic}",
        "privy-app-id": Config.PRIVY_APP_ID,
    }
    url = _PRIVY_USER_URL_TEMPLATE.format(did=did)

    try:
        async with httpx.AsyncClient(timeout=_LOOKUP_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            logger.warning("privy_migration_lookup_failed reason=malformed_response")
            return None
        return data
    except Exception as e:  # noqa: BLE001 - any failure here must fall through, never break login
        logger.warning(
            "privy_migration_lookup_failed reason=%s did_hash=%s",
            type(e).__name__,
            _did_hash(did),
        )
        return None


def _find_legacy_candidates(email: str) -> list[dict[str, Any]]:
    """ALL legacy (pre-migration) rows whose email matches, case-insensitively.

    Deliberately does NOT use `db.users.get_user_by_email[_ci]` -- those
    return only the first match, which would silently hide the "more than
    one row shares this email" case this function exists to detect (the
    spec's "ambiguous -> do NOT adopt" rule). `.ilike()` with no wildcards is
    an exact, case-insensitive match, same semantics as `lower(email) =
    lower(E)`.
    """
    legacy_app_ids = Config.PRIVY_LEGACY_APP_IDS
    if not legacy_app_ids:
        return []

    try:
        client = get_supabase_client()
        result = (
            client.table("users")
            .select("*")
            .ilike("email", email)
            .in_("privy_app_id", list(legacy_app_ids))
            .execute()
        )
        rows = result.data or []
    except Exception as e:  # noqa: BLE001 - a lookup failure must not break login
        logger.warning("privy_migration_lookup_failed reason=db_error(%s)", type(e).__name__)
        return []

    return [row for row in rows if row.get("is_active") is not False]


def adopt_legacy_account(
    *,
    user: dict[str, Any],
    new_did: str,
    request: Any = None,
) -> dict[str, Any]:
    """Re-link `user`'s row to `new_did` under the current Privy app.

    Everything else on the row (credits, keys, history) is left untouched --
    only the two Privy identity columns change. Writes an audit_log row
    (never raises on audit failure -- see record_audit's own contract).
    """
    old_did = user.get("privy_user_id")
    old_app_id = user.get("privy_app_id")
    current_app_id = Config.PRIVY_APP_ID

    client = get_supabase_client()
    update_fields = {"privy_user_id": new_did, "privy_app_id": current_app_id}
    result = client.table("users").update(update_fields).eq("id", user["id"]).execute()
    updated_user = result.data[0] if result.data else {**user, **update_fields}

    record_audit(
        actor=None,  # system-initiated, not an admin action
        action=AUDIT_ACTION,
        target_type="user",
        target_id=user.get("id"),
        request=request,
        metadata={
            "old_did_hash": _did_hash(old_did) if old_did else None,
            "new_did_hash": _did_hash(new_did),
            "app_from": old_app_id,
            "app_to": current_app_id,
        },
    )

    logger.info(
        "privy_migration_adopted user_id=%s app_from=%s app_to=%s",
        user.get("id"),
        old_app_id,
        current_app_id,
    )
    return updated_user


async def attempt_adoption(
    *,
    new_did: str,
    token_verified: bool,
    request: Any = None,
) -> dict[str, Any] | None:
    """
    Try to adopt an existing legacy account for a brand-new Privy DID.

    Returns the adopted (updated) user row, or None if adoption did not
    happen for any reason -- callers must treat None exactly like "this is a
    new account" and proceed with normal new-account creation.

    Preconditions enforced here (in addition to whatever the caller already
    checked): migration mode is "adopt", and the caller's token was actually
    verified server-side. `new_did` having no existing row is assumed to
    already be true by the time this is called (checked by the caller,
    src.routes.auth.privy_auth, right before invoking this).
    """
    if not migration_mode_is_adopt():
        return None

    if not token_verified:
        # Adoption is proof-of-identity re-linking; it must never run for a
        # DID we haven't cryptographically verified belongs to the caller.
        logger.debug("privy_migration_skipped reason=token_unverified")
        return None

    privy_user = await _fetch_privy_user(new_did)
    if privy_user is None:
        # _fetch_privy_user already logged the specific failure reason.
        return None

    email = _extract_verified_email(privy_user)
    if not email:
        logger.info(
            "privy_migration_no_adoptable_email did_hash=%s",
            _did_hash(new_did),
        )
        return None

    candidates = _find_legacy_candidates(email)

    if not candidates:
        logger.info(
            "privy_migration_no_legacy_match email_hash=%s did_hash=%s",
            _email_hash(email),
            _did_hash(new_did),
        )
        return None

    if len(candidates) > 1:
        logger.warning(
            "privy_migration_ambiguous_match email_hash=%s did_hash=%s candidate_count=%d",
            _email_hash(email),
            _did_hash(new_did),
            len(candidates),
        )
        return None

    return adopt_legacy_account(user=candidates[0], new_did=new_did, request=request)
