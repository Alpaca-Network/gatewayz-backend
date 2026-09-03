"""RequestIdentity — single source of truth for "who is this request from".

Three shapes of caller reach the backend today: an API-key holder, a
wallet-linked API-key holder (same shape, `users.auth_method == "wallet"`),
and an anonymous caller with no key at all. Each route currently re-derives
"is this anonymous?" locally (e.g. `chat.py`'s `is_anonymous = api_key is
None`). `RequestIdentity` composes `get_optional_api_key` (the existing
validated-key dependency) with a direct, cached `get_user(api_key)` lookup
and wallet lookups into one object so new code has exactly one place to ask.

This module does **not** replace `get_api_key` / `get_user_id` /
`get_optional_*` — those keep their signatures and behaviour unchanged for
every existing caller. `get_request_identity` is additive: it depends on the
same validated-key primitive and adds nothing to the validation path (see
`get_request_identity`'s docstring for why it does NOT also depend on
`get_optional_user`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Depends, Request

from src.security.deps import get_optional_api_key
from src.services.user_lookup_cache import get_user

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RequestIdentity:
    """Composed view of the caller for one request.

    `is_guest` is broader than `is_anonymous`: every anonymous caller is a
    guest, but so is a wallet-linked account that has never shown a payment
    signal (see `src.services.payment_gate.has_payment_signal` — the same
    signal that gates live API-key issuance). Privy's own "guest account"
    flag is not persisted anywhere today (see spec.md §5), so it cannot be
    read back here; only the wallet-with-no-payment-signal case is detected.
    """

    kind: Literal["api_key", "anonymous"]
    user_id: int | None
    api_key: str | None
    auth_method: str | None
    is_guest: bool
    wallet_addresses: tuple[str, ...]
    user: dict[str, Any] | None = None
    """The full user row from `get_user(api_key)`, already looked up while
    resolving identity. Routes that would otherwise call `get_user(api_key)`
    again (e.g. chat.py's authenticated branch) should read this instead --
    it's the same cached lookup, just already done."""

    @property
    def is_anonymous(self) -> bool:
        return self.kind == "anonymous"

    @property
    def primary_wallet(self) -> str | None:
        return self.wallet_addresses[0] if self.wallet_addresses else None


ANONYMOUS = RequestIdentity(
    kind="anonymous",
    user_id=None,
    api_key=None,
    auth_method=None,
    is_guest=True,
    wallet_addresses=(),
    user=None,
)


def _wallet_addresses_for(user_id: int) -> tuple[str, ...]:
    """Look up linked wallets, tolerating W1's `user_wallets` module not existing yet.

    Imported lazily (and per-call, not at module import time) so this file
    works standalone before `src/db/user_wallets.py` lands, and keeps working
    if that lookup fails at runtime -- "no wallets" beats a 500.
    """
    try:
        from src.db.user_wallets import get_wallets_for_user

        rows = get_wallets_for_user(user_id) or []
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        logger.debug("Wallet lookup unavailable for user %s: %s", user_id, exc)
        return ()

    addresses = [row["wallet_address"] for row in rows if row.get("wallet_address")]
    primary = [row["wallet_address"] for row in rows if row.get("is_primary")]
    ordered = primary + [a for a in addresses if a not in primary]
    return tuple(ordered)


def _is_guest(auth_method: str | None, user: dict[str, Any]) -> bool:
    """Narrower-than-spec guest determination -- see class docstring."""
    if auth_method != "wallet":
        return False
    try:
        from src.services.payment_gate import has_payment_signal

        allowed, _reason = has_payment_signal(user)
        return not allowed
    except Exception as exc:  # noqa: BLE001 - a broken payment check must not 500 chat
        logger.debug("Payment signal check unavailable for guest determination: %s", exc)
        return True


async def get_request_identity(
    request: Request,
    api_key: str | None = Depends(get_optional_api_key),
) -> RequestIdentity:
    """FastAPI dependency: resolve the caller's identity once per request.

    Cached on `request.state.identity` so multiple `Depends(get_request_identity)`
    in one request don't repeat the wallet lookup.

    Deliberately does NOT depend on `get_optional_user`: that function
    re-parses its own `HTTPBearer` credentials and calls `get_api_key(...)`
    again directly (not via `Depends`, so FastAPI's per-request dependency
    cache can't dedupe it), which re-runs `validate_api_key_security()` --
    including its `last_used_at` write to Supabase -- a second time for every
    authenticated request. `api_key` is already validated by
    `get_optional_api_key` above, so the user lookup below goes straight to
    the (cached) `get_user(api_key)` instead of re-validating.
    """
    cached = getattr(request.state, "identity", None) if request is not None else None
    if cached is not None:
        return cached

    if api_key is None:
        # Mirrors get_optional_api_key's own contract exactly (kind derives
        # from the key alone, not from whether a user row was found for it)
        # so chat.py's `identity.is_anonymous` branches identically to the
        # `is_anonymous = api_key is None` it replaces.
        identity = ANONYMOUS
    else:
        # `user` can be None even though api_key is a validly-formatted key
        # (e.g. no matching account) -- keep kind == "api_key" in that case;
        # the existing per-route auth flow (unchanged by this dependency) is
        # still responsible for 404ing on a missing user.
        user = get_user(api_key)
        user_id = user.get("id") if user else None
        auth_method = user.get("auth_method") if user else None
        wallets = _wallet_addresses_for(user_id) if user_id is not None else ()
        identity = RequestIdentity(
            kind="api_key",
            user_id=user_id,
            api_key=api_key,
            auth_method=auth_method,
            is_guest=_is_guest(auth_method, user) if user else False,
            wallet_addresses=wallets,
            user=user,
        )

    logger.debug(
        "identity kind=%s user_id=%s wallets=%d",
        identity.kind,
        identity.user_id,
        len(identity.wallet_addresses),
    )

    if request is not None:
        request.state.identity = identity
    return identity
