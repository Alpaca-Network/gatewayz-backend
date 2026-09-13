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

import src.config.supabase_config as supabase_config
import src.db.users as users_module
from src.config import Config
from src.db.audit import record_audit
from src.utils.security_validators import escape_ilike_pattern

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


def _is_explicitly_unverified(account: dict[str, Any]) -> bool:
    """True only when the account payload itself carries an explicit
    unverified marker (`verified: false` or `verified_at: null` present as a
    key). Privy's public API docs don't document either field on `email`/
    `google_oauth`/`apple_oauth` accounts today (see `_extract_verified_email`
    docstring for why those types are trustworthy by construction), but this
    is a defense-in-depth check in case a future API response ever adds one:
    if the field is present and says "not verified", believe it -- don't
    rely solely on account type. Absence of the field is not a signal either
    way (most accounts here won't carry it at all)."""
    if "verified" in account and account.get("verified") is False:
        return True
    return "verified_at" in account and account.get("verified_at") is None


def _extract_verified_email(privy_user: dict[str, Any]) -> str | None:
    """
    Pull a trustworthy email out of a Privy `/users/{did}` response.

    Only two account types can vouch for an email here:
    - `email` -- Privy only creates this linked-account entry after the
      user completes OTP (or magic-link) verification for that address;
      there is no "pending"/unverified `email` account type in Privy's
      model, so its mere presence in `linked_accounts` already means it was
      verified by the time it landed there.
    - `google_oauth` / `apple_oauth` -- Privy only creates these after the
      identity provider's own sign-in flow, which itself only hands back an
      email Google/Apple have already verified account-side. Privy does
      not re-expose a separate verification flag for these because the
      provider is the source of truth.

    Anything else (wallet-only, phone, unlinked) yields no adoptable email,
    which is an expected outcome (see docs/PRIVY_MIGRATION.md's Risks
    section: wallet-only logins cannot be adopted), not an error.

    Defense in depth: if a linked account of either trusted type ever DOES
    carry an explicit unverified marker (`verified: false` or
    `verified_at: null`), it's skipped anyway -- see
    `_is_explicitly_unverified`.
    """
    linked_accounts = privy_user.get("linked_accounts")
    if not isinstance(linked_accounts, list):
        return None

    for account in linked_accounts:
        if not isinstance(account, dict):
            continue
        if _is_explicitly_unverified(account):
            continue
        account_type = account.get("type")
        if account_type == "email":
            address = account.get("address") or account.get("email")
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
    """ALL legacy (pre-migration) rows whose email matches, EXACTLY
    (case-insensitively) -- same semantics as `lower(email) = lower(E)`.

    Deliberately does NOT use `db.users.get_user_by_email[_ci]` -- those
    return only the first match, which would silently hide the "more than
    one row shares this email" case this function exists to detect (the
    spec's "ambiguous -> do NOT adopt" rule).

    SECURITY (PR #2316 review, fix round 1): `.ilike()` sends its pattern
    straight to Postgres's ILIKE operator, where `%`/`_` are wildcards -- an
    unescaped `email` here is a pattern search, not an exact match:
    "first_last@x.com" (`_` = "any one char") would also match
    "firstXlast@x.com", a DIFFERENT account, and that account would then be
    silently adopted into. `escape_ilike_pattern` neutralizes the wildcards,
    and the exact-match filter below is a second, independent guard on the
    returned rows -- belt and braces, same pattern as
    `db.users.get_user_by_email_ci`.
    """
    legacy_app_ids = Config.PRIVY_LEGACY_APP_IDS
    if not legacy_app_ids:
        return []

    try:
        client = supabase_config.get_supabase_client()
        result = (
            client.table("users")
            .select("*")
            .ilike("email", escape_ilike_pattern(email))
            .in_("privy_app_id", list(legacy_app_ids))
            .execute()
        )
        rows = result.data or []
    except Exception as e:  # noqa: BLE001 - a lookup failure must not break login
        logger.warning("privy_migration_lookup_failed reason=db_error(%s)", type(e).__name__)
        return []

    target = email.strip().lower()
    return [
        row
        for row in rows
        if row.get("is_active") is not False and (row.get("email") or "").strip().lower() == target
    ]


def adopt_legacy_account(
    *,
    user: dict[str, Any],
    new_did: str,
    request: Any = None,
) -> dict[str, Any] | None:
    """Re-link `user`'s row to `new_did` under the current Privy app.

    Everything else on the row (credits, keys, history) is left untouched --
    only the two Privy identity columns change. Writes an audit_log row
    (never raises on audit failure -- see record_audit's own contract).

    SECURITY/CORRECTNESS (PR #2316 review, fix round 1): the UPDATE is
    conditioned on `privy_user_id` still equaling the DID this row had when
    `_find_legacy_candidates` read it (`.eq("privy_user_id", old_did)`), not
    just `id` -- otherwise two concurrent logins racing to adopt the same
    legacy row (or a row that changed underneath us for any other reason
    between the read and this write) could both "succeed" against a stale
    read, corrupting which DID owns the account. If zero rows match (we lost
    the race), this returns whatever `get_user_by_privy_id(new_did)` finds
    now: another request may have already completed this exact adoption
    (return that row, no error), or nothing has (return None so the caller
    falls through to ordinary new-account creation) -- either way, this
    never blindly returns the stale, no-longer-true `user` dict it was
    called with.
    """
    old_did = user.get("privy_user_id")
    old_app_id = user.get("privy_app_id")
    current_app_id = Config.PRIVY_APP_ID

    client = supabase_config.get_supabase_client()
    update_fields = {"privy_user_id": new_did, "privy_app_id": current_app_id}
    query = client.table("users").update(update_fields).eq("id", user["id"])
    query = (
        query.is_("privy_user_id", "null")
        if old_did is None
        else query.eq("privy_user_id", old_did)
    )
    result = query.execute()

    if not result.data:
        logger.warning(
            "privy_migration_adopt_race_lost user_id=%s",
            user.get("id"),
        )
        return users_module.get_user_by_privy_id(new_did)

    updated_user = result.data[0]

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


def migration_counts() -> dict[str, int]:
    """{'legacy_users': n, 'migrated_users': n} -- counts of `users` rows by
    `privy_app_id`, for `GET /admin/status`'s `migration` block (see
    src/routes/admin_status.py). 0 for either count on any lookup error --
    never raises, matching every other admin/status block builder.
    """
    counts = {"legacy_users": 0, "migrated_users": 0}
    legacy_app_ids = list(Config.PRIVY_LEGACY_APP_IDS)

    try:
        client = supabase_config.get_supabase_client()
        if legacy_app_ids:
            legacy_result = (
                client.table("users")
                .select("id", count="exact")
                .in_("privy_app_id", legacy_app_ids)
                .execute()
            )
            counts["legacy_users"] = legacy_result.count or 0
        if Config.PRIVY_APP_ID:
            migrated_result = (
                client.table("users")
                .select("id", count="exact")
                .eq("privy_app_id", Config.PRIVY_APP_ID)
                .execute()
            )
            counts["migrated_users"] = migrated_result.count or 0
    except Exception as e:
        logger.warning("privy_migration_counts_failed: %s", type(e).__name__)

    return counts
