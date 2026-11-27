"""What an API key may do beyond inference.

Two kinds of credential reach the API:

* **Session credentials**, which the account's owner holds after signing in: the
  primary key issued at sign-in (``is_primary``) and temporary session keys
  (``gw_temp_``). The web app and the admin panel authenticate with these.
* **Ordinary API keys**, made for inference and often pasted into third-party
  tools and scripts.

An ordinary key can always run inference. It may manage keys (list, create,
rotate, delete, change scopes) only with the explicit ``key_management`` scope,
and reach admin routes only with the explicit ``admin_api`` scope (and then
only on an elevated account). New keys are created inference-only.

The privileged scope names are deliberately new. Keys created before this
change carry ``{"read": ["*"], "write": ["*"], "admin": ["*"]}`` (or ``{}``),
and those wildcards no longer grant key management or admin access: an old
key keeps working for inference, but cannot manage keys or reach admin routes
unless it is the account's primary key or is granted a new scope explicitly.
"""

from __future__ import annotations

from typing import Any

SCOPE_INFERENCE = "inference"
SCOPE_KEY_MANAGEMENT = "key_management"
SCOPE_ADMIN = "admin_api"

PRIVILEGED_SCOPES = (SCOPE_KEY_MANAGEMENT, SCOPE_ADMIN)

TEMP_SESSION_PREFIX = "gw_temp_"


def inference_only_scopes() -> dict[str, list[str]]:
    """Default scopes for a new key (a fresh dict each call)."""
    return {SCOPE_INFERENCE: ["*"]}


def has_scope(scope_permissions: Any, scope: str) -> bool:
    """True only if ``scope`` is granted explicitly.

    Accepts ``{"key_management": ["*"]}``, any non-empty list, ``True`` or a
    non-empty string. Wildcards under other names (``"write": ["*"]``) and an
    empty dict grant nothing here.
    """
    if not isinstance(scope_permissions, dict):
        return False
    value = scope_permissions.get(scope)
    if value is True:
        return True
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list | tuple):
        return any(isinstance(v, str) and v.strip() for v in value)
    return False


def is_session_credential(api_key: str | None, user: dict[str, Any] | None) -> bool:
    """The account's own sign-in credential: a primary key or a gw_temp_ key."""
    if api_key and str(api_key).startswith(TEMP_SESSION_PREFIX):
        return True
    return bool(user) and user.get("is_primary") is True


def can_manage_keys(api_key: str | None, user: dict[str, Any] | None) -> bool:
    """May this credential list, create, rotate or delete keys, or change scopes?"""
    if is_session_credential(api_key, user):
        return True
    return bool(user) and has_scope(user.get("scope_permissions"), SCOPE_KEY_MANAGEMENT)


def has_admin_credential(user: dict[str, Any] | None) -> bool:
    """Is the credential behind this user dict allowed on admin routes?

    The account's elevation (is_admin / role) is checked separately; this is
    about the key: a session credential or an explicitly admin-scoped key.
    ``get_user`` puts the presented key in ``user["api_key"]`` and the key's
    own ``is_primary`` and ``scope_permissions`` on the dict.
    """
    if not user:
        return False
    if is_session_credential(user.get("api_key"), user):
        return True
    return has_scope(user.get("scope_permissions"), SCOPE_ADMIN)


def privileged_scopes_in(scope_permissions: Any) -> list[str]:
    """Privileged scopes a create/update request is trying to grant."""
    return [s for s in PRIVILEGED_SCOPES if has_scope(scope_permissions, s)]
