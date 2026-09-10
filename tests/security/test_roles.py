"""Tests for src/security/roles.py -- the shared (role, is_admin) resolver
(gatewayz-backend unified-identity Phase A).

This is the single source of truth /auth, /user/profile, and (per
gatewayz-backend#2307) src.security.deps.require_admin all agree on, so
"is this user an admin" never disagrees between the panel's client-side
gate and the backend's own admin check.
"""

from src.security.roles import resolve_role_fields


class TestResolveRoleFields:
    def test_plain_user_defaults(self):
        role, is_admin = resolve_role_fields({"id": 1})
        assert role == "user"
        assert is_admin is False

    def test_explicit_user_role(self):
        role, is_admin = resolve_role_fields({"role": "user"})
        assert role == "user"
        assert is_admin is False

    def test_admin_role_implies_is_admin(self):
        role, is_admin = resolve_role_fields({"role": "admin"})
        assert role == "admin"
        assert is_admin is True

    def test_superadmin_role_implies_is_admin(self):
        # require_admin (#2307) accepts role in ("admin", "superadmin") --
        # a superadmin row must resolve is_admin=True here too, or /auth and
        # /user/profile would report a superadmin as a non-admin.
        role, is_admin = resolve_role_fields({"role": "superadmin"})
        assert role == "superadmin"
        assert is_admin is True

    def test_is_admin_flag_true_with_user_role(self):
        # Legacy rows may carry is_admin=True without role having caught up.
        role, is_admin = resolve_role_fields({"role": "user", "is_admin": True})
        assert role == "user"
        assert is_admin is True

    def test_empty_or_none_role_defaults_to_user(self):
        role, is_admin = resolve_role_fields({"role": None})
        assert role == "user"
        assert is_admin is False

    def test_unknown_role_is_not_admin(self):
        role, is_admin = resolve_role_fields({"role": "developer"})
        assert role == "developer"
        assert is_admin is False
