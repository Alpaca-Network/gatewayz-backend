"""Shared role/admin resolution helper (gatewayz-backend unified-identity
Phase A).

Single source of truth for "is this user row an admin" so every caller —
``POST /auth``, ``GET /user/profile``, and ``src.security.deps.require_admin``
(gatewayz-backend#2307, which extends admin access to ``role="superadmin"``)
— agree on the exact same definition. Previously this logic was duplicated
inline in two route modules and only recognized ``role == "admin"``, which
would have silently reported ``is_admin: false`` for a superadmin.
"""

from __future__ import annotations

from typing import Any

# Any role in this set has admin-level access, per require_admin (#2307).
ADMIN_ROLES = frozenset({"admin", "superadmin"})


def resolve_role_fields(user: dict[str, Any]) -> tuple[str, bool]:
    """Resolve ``(role, is_admin)`` from a ``users`` table row.

    ``is_admin`` is true when ``role`` is any admin-level role
    (``admin``/``superadmin``) OR the row carries an explicit legacy
    ``is_admin`` flag. Defaults to ``("user", False)`` for a row with no
    role yet (a brand-new account, or one created before the role
    migration).
    """
    role = user.get("role") or "user"
    is_admin = role in ADMIN_ROLES or bool(user.get("is_admin", False))
    return role, is_admin
