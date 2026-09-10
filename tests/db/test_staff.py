"""Tests for src/db/staff.py (Phase A2 staff management)."""

import hashlib
from unittest.mock import MagicMock, patch

from src.db.staff import (
    accept_invite,
    count_active_superadmins,
    create_invite,
    get_invite_by_token,
    list_staff,
    revoke_user_keys,
    set_role,
)


class TestListStaff:
    def test_returns_rows_and_derives_privy_link_flag(self):
        client = MagicMock()
        client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
            {"id": 1, "email": "a@x.com", "role": "admin", "privy_user_id": "did:privy:abc"},
            {"id": 2, "email": "b@x.com", "role": "superadmin", "privy_user_id": None},
        ]

        with patch("src.db.staff.get_supabase_client", return_value=client):
            rows = list_staff()

        assert rows[0]["has_privy_link"] is True
        assert "privy_user_id" not in rows[0]
        assert rows[1]["has_privy_link"] is False

    def test_returns_empty_list_on_error(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("db down")

        with patch("src.db.staff.get_supabase_client", return_value=client):
            assert list_staff() == []


class TestCountActiveSuperadmins:
    def test_returns_count(self):
        client = MagicMock()
        query = client.table.return_value.select.return_value.eq.return_value.eq.return_value
        query.execute.return_value.count = 2
        query.execute.return_value.data = [{"id": 1}, {"id": 2}]

        with patch("src.db.staff.get_supabase_client", return_value=client):
            assert count_active_superadmins() == 2

    def test_fails_closed_to_zero_on_error(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("db down")

        with patch("src.db.staff.get_supabase_client", return_value=client):
            assert count_active_superadmins() == 0


class TestSetRole:
    def test_updates_role_and_invalidates_cache(self):
        client = MagicMock()
        client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": 5, "role": "admin"}
        ]
        client.table.return_value.insert.return_value.execute.return_value = MagicMock()

        with (
            patch("src.db.staff.get_supabase_client", return_value=client),
            patch("src.db.users.invalidate_user_cache_by_id") as mock_invalidate,
        ):
            updated = set_role(5, "admin", actor={"id": 1})

        assert updated == {"id": 5, "role": "admin"}
        mock_invalidate.assert_called_once_with(5)

    def test_returns_none_when_user_missing(self):
        client = MagicMock()
        client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = []

        with patch("src.db.staff.get_supabase_client", return_value=client):
            assert set_role(999, "admin", actor={"id": 1}) is None


class TestCreateInvite:
    def test_creates_row_and_returns_raw_token_once(self):
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value.data = [
            {
                "id": "uuid-1",
                "email": "new@x.com",
                "role": "admin",
                "expires_at": "2026-09-14T00:00:00Z",
            }
        ]

        with patch("src.db.staff.get_supabase_client", return_value=client):
            row, raw_token = create_invite("New@X.com", "admin", invited_by=1)

        assert row["email"] == "new@x.com"
        assert raw_token is not None and len(raw_token) > 20

        inserted = client.table.return_value.insert.call_args[0][0]
        assert inserted["email"] == "new@x.com"
        assert inserted["token_hash"] == hashlib.sha256(raw_token.encode()).hexdigest()
        # The raw token itself must never be stored.
        assert raw_token not in inserted.values()

    def test_returns_none_tuple_on_failure(self):
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value.data = []

        with patch("src.db.staff.get_supabase_client", return_value=client):
            row, raw_token = create_invite("x@y.com", "admin", invited_by=1)

        assert row is None
        assert raw_token is None


class TestGetInviteByToken:
    def test_returns_invite_when_unexpired(self):
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.is_.return_value.execute.return_value.data = [
            {
                "id": "uuid-1",
                "email": "a@x.com",
                "role": "admin",
                "expires_at": "2099-01-01T00:00:00+00:00",
            }
        ]

        with patch("src.db.staff.get_supabase_client", return_value=client):
            invite = get_invite_by_token("raw-token")

        assert invite["id"] == "uuid-1"

    def test_returns_none_when_expired(self):
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.is_.return_value.execute.return_value.data = [
            {
                "id": "uuid-1",
                "email": "a@x.com",
                "role": "admin",
                "expires_at": "2020-01-01T00:00:00+00:00",
            }
        ]

        with patch("src.db.staff.get_supabase_client", return_value=client):
            assert get_invite_by_token("raw-token") is None

    def test_returns_none_when_not_found(self):
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.is_.return_value.execute.return_value.data = (
            []
        )

        with patch("src.db.staff.get_supabase_client", return_value=client):
            assert get_invite_by_token("raw-token") is None


class TestAcceptInvite:
    def test_accepts_when_email_matches(self):
        invite = {
            "id": "uuid-1",
            "email": "match@x.com",
            "role": "admin",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
        client = MagicMock()

        with (
            patch("src.db.staff.get_invite_by_token", return_value=invite),
            patch(
                "src.db.staff.set_role", return_value={"id": 5, "role": "admin"}
            ) as mock_set_role,
            patch("src.db.staff.get_supabase_client", return_value=client),
        ):
            updated = accept_invite("raw-token", user_id=5, user_email="Match@X.com")

        assert updated == {"id": 5, "role": "admin"}
        mock_set_role.assert_called_once_with(5, "admin", actor=None)

    def test_rejects_when_email_does_not_match(self):
        invite = {
            "id": "uuid-1",
            "email": "match@x.com",
            "role": "admin",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
        with patch("src.db.staff.get_invite_by_token", return_value=invite):
            updated = accept_invite("raw-token", user_id=5, user_email="different@x.com")

        assert updated is None

    def test_rejects_when_invite_missing(self):
        with patch("src.db.staff.get_invite_by_token", return_value=None):
            updated = accept_invite("raw-token", user_id=5, user_email="a@x.com")
        assert updated is None


class TestRevokeUserKeys:
    def test_deactivates_active_keys_and_invalidates_cache(self):
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = [
            {"api_key": "gw_live_a"},
            {"api_key": "gw_live_b"},
        ]

        with (
            patch("src.db.staff.get_supabase_client", return_value=client),
            patch("src.db.users.invalidate_user_cache") as mock_invalidate,
        ):
            count = revoke_user_keys(5)

        assert count == 2
        assert mock_invalidate.call_count == 2

    def test_returns_zero_when_no_active_keys(self):
        client = MagicMock()
        client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = (
            []
        )

        with patch("src.db.staff.get_supabase_client", return_value=client):
            assert revoke_user_keys(5) == 0
