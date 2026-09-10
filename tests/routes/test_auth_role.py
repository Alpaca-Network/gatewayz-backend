"""Tests for role/is_admin on POST /auth and GET /user/profile
(gatewayz-backend unified-identity Phase A, A5)."""

from unittest.mock import MagicMock, patch

from fastapi import BackgroundTasks
from fastapi.testclient import TestClient

import src.routes.auth as auth_module
from src.main import app
from src.schemas import AuthMethod, PrivyAuthRequest, PrivyUserData

client = TestClient(app)

# Unit tests for the shared (role, is_admin) resolver itself live in
# tests/security/test_roles.py -- this file exercises it through the routes.


class TestAuthResponseIncludesRoleForExistingUser:
    """Exercises _handle_existing_user directly (called as a plain function,
    not through the ASGI app, so its queued background tasks never run) --
    the same level other POST /auth tests in this repo settle for once
    Supabase access is involved (see tests/routes/test_auth_privy_token.py)."""

    def _existing_user(self, **overrides):
        base = {
            "id": 42,
            "username": "existinguser",
            "email": "existing@example.com",
            "api_key": "gw_live_existing_1234567890123456789012345678901234567890",
            "subscription_allowance": 0,
            "purchased_credits": 10.0,
            "credits": 0,
            "tier": "basic",
            "subscription_status": "active",
            "trial_expires_at": None,
            "subscription_end_date": None,
            "allowance_reset_date": None,
            "welcome_email_sent": True,
        }
        base.update(overrides)
        return base

    def _request(self):
        return PrivyAuthRequest(
            user=PrivyUserData(id="did:privy:existinguser123", created_at=1700000000),
        )

    def _mock_supabase_client(self):
        client_mock = MagicMock()
        query_chain = (
            client_mock.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.order.return_value
        )
        query_chain.execute.return_value = MagicMock(
            data=[
                {
                    "api_key": "gw_live_existing_1234567890123456789012345678901234567890",
                    "is_primary": True,
                    "created_at": "2026-01-01",
                }
            ]
        )
        return client_mock

    def test_admin_role_surfaces_in_response(self):
        existing_user = self._existing_user(role="admin")
        with patch(
            "src.routes.auth.supabase_config.get_supabase_client",
            return_value=self._mock_supabase_client(),
        ):
            response = auth_module._handle_existing_user(
                existing_user=existing_user,
                request=self._request(),
                background_tasks=BackgroundTasks(),
                auth_method=AuthMethod.EMAIL,
                display_name=None,
                email="existing@example.com",
            )
        assert response.role == "admin"
        assert response.is_admin is True

    def test_default_user_role_surfaces_in_response(self):
        existing_user = self._existing_user()  # no role column at all
        with patch(
            "src.routes.auth.supabase_config.get_supabase_client",
            return_value=self._mock_supabase_client(),
        ):
            response = auth_module._handle_existing_user(
                existing_user=existing_user,
                request=self._request(),
                background_tasks=BackgroundTasks(),
                auth_method=AuthMethod.EMAIL,
                display_name=None,
                email="existing@example.com",
            )
        assert response.role == "user"
        assert response.is_admin is False

    def test_superadmin_role_surfaces_as_admin(self):
        # Regression: require_admin (#2307) accepts role in
        # ("admin", "superadmin") -- the /auth response must agree.
        existing_user = self._existing_user(role="superadmin")
        with patch(
            "src.routes.auth.supabase_config.get_supabase_client",
            return_value=self._mock_supabase_client(),
        ):
            response = auth_module._handle_existing_user(
                existing_user=existing_user,
                request=self._request(),
                background_tasks=BackgroundTasks(),
                auth_method=AuthMethod.EMAIL,
                display_name=None,
                email="existing@example.com",
            )
        assert response.role == "superadmin"
        assert response.is_admin is True


class TestUserProfileIncludesRole:
    """GET /user/profile should surface role/is_admin from the same user
    row without requiring a second lookup."""

    def _mock_user(self, **overrides):
        base = {
            "id": 7,
            "email": "profileuser@example.com",
            "username": "profileuser",
            "api_key": "gw_live_test_key_1234567890",
            "credits": 5.0,
            "subscription_allowance": 0,
            "purchased_credits": 5.0,
            "auth_method": "privy",
            "subscription_status": "active",
            "tier": "basic",
            "trial_expires_at": None,
            "subscription_end_date": None,
            "is_active": True,
            "registration_date": "2026-01-01T00:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "settings": None,
        }
        base.update(overrides)
        return base

    def _mock_profile(self, mock_user, *, include_role: bool):
        """Shape get_user_profile()'s real return value: user_id, not id,
        and no role/is_admin keys unless the caller asks for them (mirrors
        the pre-A5 profile dict, which never carried role at all)."""
        profile = {
            "user_id": mock_user["id"],
            "api_key": mock_user["api_key"],
            "credits": mock_user["credits"],
            "subscription_allowance": mock_user["subscription_allowance"],
            "purchased_credits": mock_user["purchased_credits"],
            "total_credits": mock_user["credits"],
            "allowance_reset_date": None,
            "created_at": mock_user["created_at"],
            "updated_at": mock_user["updated_at"],
            "username": mock_user["username"],
            "email": mock_user["email"],
            "auth_method": mock_user["auth_method"],
            "subscription_status": mock_user["subscription_status"],
            "tier": mock_user["tier"],
            "tier_display_name": None,
            "trial_expires_at": mock_user["trial_expires_at"],
            "subscription_end_date": mock_user["subscription_end_date"],
            "is_active": mock_user["is_active"],
            "registration_date": mock_user["registration_date"],
            "settings": mock_user["settings"],
        }
        if include_role:
            profile["role"] = mock_user.get("role")
            profile["is_admin"] = mock_user.get("is_admin")
        return profile

    def test_admin_role_included_in_profile_response(self):
        mock_user = self._mock_user(role="admin", is_admin=True)
        with (
            patch("src.db.users.get_user", return_value=mock_user),
            patch(
                "src.db.users.get_user_profile",
                return_value=self._mock_profile(mock_user, include_role=False),
            ),
        ):
            response = client.get(
                "/user/profile", headers={"Authorization": f"Bearer {mock_user['api_key']}"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["role"] == "admin"
        assert body["is_admin"] is True

    def test_default_user_role_when_profile_lacks_role(self):
        mock_user = self._mock_user()  # no role key at all
        with (
            patch("src.db.users.get_user", return_value=mock_user),
            patch(
                "src.db.users.get_user_profile",
                return_value=self._mock_profile(mock_user, include_role=False),
            ),
        ):
            response = client.get(
                "/user/profile", headers={"Authorization": f"Bearer {mock_user['api_key']}"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["role"] == "user"
        assert body["is_admin"] is False
