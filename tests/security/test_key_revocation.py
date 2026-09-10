"""Key revocation must be real: a deactivated or deleted API key must stop
authenticating immediately, not up to _user_cache_ttl (60s) later.

Regression coverage for the gap flagged in
docs/superpowers/specs/2026-09-10-unified-admin-identity-design.md §5:
"Backend get_user() ignores is_active on api_keys_new (found 2026-09-09) --
fix in A1 so key revocation is real."

Two layers are covered:
  1. src/db/users.py::_get_user_uncached (and therefore get_user) now
     rejects an inactive/expired key itself, so ANY caller of get_user() --
     not just the request path through validate_api_key_security -- is
     protected.
  2. src/db/api_keys.py::delete_api_key / update_api_key invalidate the
     in-memory _user_cache immediately on revocation, so a cached user
     object can't outlive the key that authenticated it.
  3. An end-to-end TestClient check that a real request with an inactive
     key is rejected with 401 through the full dependency chain.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import src.db.users as users_mod


@pytest.fixture(autouse=True)
def _clear_user_cache():
    """Every test starts and ends with an empty in-memory user cache --
    it's process-global module state shared across tests."""
    users_mod.clear_user_cache()
    yield
    users_mod.clear_user_cache()


def _key_row(**overrides):
    row = {
        "id": 1,
        "user_id": 42,
        "key_name": "test-key",
        "environment_tag": "live",
        "scope_permissions": None,
        "is_primary": False,
        "is_active": True,
        "expiration_date": None,
    }
    row.update(overrides)
    return row


def _user_row():
    return {"id": 42, "email": "user@example.com", "role": "user"}


def _mock_client_for(key_row, user_row):
    """A MagicMock Supabase client: .table("api_keys_new")... -> key_row,
    .table("users")... -> user_row."""
    client = MagicMock()

    def table(name):
        m = MagicMock()
        if name == "api_keys_new":
            m.select.return_value.eq.return_value.execute.return_value.data = (
                [key_row] if key_row else []
            )
        elif name == "users":
            m.select.return_value.eq.return_value.execute.return_value.data = (
                [user_row] if user_row else []
            )
        else:
            m.select.return_value.eq.return_value.execute.return_value.data = []
        return m

    client.table.side_effect = table
    return client


class TestGetUserUncachedHonoursIsActive:
    def test_active_key_returns_user(self):
        client = _mock_client_for(_key_row(is_active=True), _user_row())
        with patch("src.db.users.get_supabase_client", return_value=client):
            user = users_mod._get_user_uncached("gw_live_test")
        assert user is not None
        assert user["id"] == 42

    def test_inactive_key_returns_none(self):
        client = _mock_client_for(_key_row(is_active=False), _user_row())
        with patch("src.db.users.get_supabase_client", return_value=client):
            user = users_mod._get_user_uncached("gw_live_test")
        assert user is None

    def test_expired_key_returns_none(self):
        client = _mock_client_for(
            _key_row(is_active=True, expiration_date="2020-01-01T00:00:00+00:00"), _user_row()
        )
        with patch("src.db.users.get_supabase_client", return_value=client):
            user = users_mod._get_user_uncached("gw_live_test")
        assert user is None

    def test_unexpired_key_returns_user(self):
        client = _mock_client_for(
            _key_row(is_active=True, expiration_date="2099-01-01T00:00:00+00:00"), _user_row()
        )
        with patch("src.db.users.get_supabase_client", return_value=client):
            user = users_mod._get_user_uncached("gw_live_test")
        assert user is not None

    def test_legacy_fallback_honours_is_active(self):
        """The legacy users.api_key column is a second, independent
        authentication path -- an inactive user must not authenticate
        through it either, even when api_keys_new has no matching row."""
        client = _mock_client_for(key_row=None, user_row={**_user_row(), "is_active": False})
        with patch("src.db.users.get_supabase_client", return_value=client):
            user = users_mod._get_user_uncached("gw_legacy_test")
        assert user is None

    def test_legacy_fallback_allows_active_user(self):
        client = _mock_client_for(key_row=None, user_row={**_user_row(), "is_active": True})
        with patch("src.db.users.get_supabase_client", return_value=client):
            user = users_mod._get_user_uncached("gw_legacy_test")
        assert user is not None


class TestGetUserCachesOnlyLiveResults:
    def test_get_user_does_not_cache_a_revoked_key(self):
        client = _mock_client_for(_key_row(is_active=False), _user_row())
        with patch("src.db.users.get_supabase_client", return_value=client):
            assert users_mod.get_user("gw_live_test") is None
        assert "gw_live_test" not in users_mod._user_cache


class TestDeleteApiKeyInvalidatesCache:
    def test_delete_api_key_clears_cached_user(self):
        api_key = "gw_live_delete_me"
        users_mod._user_cache[api_key] = {
            "user": _user_row(),
            "timestamp": __import__("time").time(),
        }

        deleted_row = {"id": 1, "key_name": "test-key", "environment_tag": "live"}
        client = MagicMock()
        client.table.return_value.delete.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            deleted_row
        ]
        # rate_limit_configs / api_key_audit_logs deletes/inserts are best-effort
        client.table.return_value.delete.return_value.eq.return_value.execute.return_value = (
            MagicMock()
        )
        client.table.return_value.insert.return_value.execute.return_value = MagicMock()

        with patch("src.db.api_keys.get_supabase_client", return_value=client):
            from src.db.api_keys import delete_api_key

            result = delete_api_key(api_key, user_id=42)

        assert result is True
        assert api_key not in users_mod._user_cache


class TestUpdateApiKeyInvalidatesCacheOnDeactivation:
    def test_update_api_key_is_active_false_clears_cache(self):
        api_key = "gw_live_deactivate_me"
        users_mod._user_cache[api_key] = {
            "user": _user_row(),
            "timestamp": __import__("time").time(),
        }

        key_row = _key_row(id=1)
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            key_row
        ]
        client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            key_row
        ]
        client.table.return_value.insert.return_value.execute.return_value = MagicMock()

        with (
            patch("src.db.api_keys.get_supabase_client", return_value=client),
            patch("src.db.api_keys.check_key_name_uniqueness", return_value=True),
        ):
            from src.db.api_keys import update_api_key

            result = update_api_key(api_key, user_id=42, updates={"is_active": False})

        assert result is True
        assert api_key not in users_mod._user_cache


class TestEndToEndInactiveKeyRejected:
    def test_inactive_key_rejected_with_401_through_test_client(self, monkeypatch):
        """Disable the TESTING short-circuit in validate_api_key_security so
        the full production auth chain (get_api_key -> validate_api_key_security
        -> _validate_key_constraints) runs against a mocked, inactive key row."""
        from src.config.config import Config
        from src.security.deps import get_current_user

        monkeypatch.setattr(Config, "IS_TESTING", False)
        monkeypatch.delenv("TESTING", raising=False)

        client = _mock_client_for(_key_row(is_active=False), _user_row())

        app = FastAPI()

        @app.get("/whoami")
        async def whoami(user: dict = Depends(get_current_user)):
            return {"id": user["id"]}

        with (
            patch("src.config.supabase_config.get_supabase_client", return_value=client),
            patch("src.db.users.get_supabase_client", return_value=client),
        ):
            test_client = TestClient(app)
            resp = test_client.get(
                "/whoami", headers={"Authorization": "Bearer gw_live_inactive_key"}
            )

        assert resp.status_code == 401
