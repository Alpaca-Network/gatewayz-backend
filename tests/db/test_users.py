"""Tests for src/db/users.py::get_user_by_email_ci (Phase A code review fix
round 1: case-insensitive existing-user lookup for POST /admin/staff/invite)."""

from unittest.mock import MagicMock, patch

from src.db.users import get_user_by_email_ci


class TestGetUserByEmailCi:
    def test_finds_a_mixed_case_stored_email(self):
        """The invite flow normalizes the caller's email to lowercase before
        looking it up; the stored row may have been created with different
        casing (e.g. "Admin@Example.com") and must still be found."""
        client = MagicMock()
        stored_user = {"id": 9, "email": "Admin@Example.com", "role": "user"}
        client.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [
            stored_user
        ]

        with patch("src.db.users.get_supabase_client", return_value=client):
            result = get_user_by_email_ci("admin@example.com")

        assert result == stored_user
        client.table.return_value.select.return_value.ilike.assert_called_once_with(
            "email", "admin@example.com"
        )

    def test_returns_none_when_no_match(self):
        client = MagicMock()
        client.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = (
            []
        )

        with patch("src.db.users.get_supabase_client", return_value=client):
            assert get_user_by_email_ci("nobody@example.com") is None

    def test_returns_none_on_error(self):
        client = MagicMock()
        client.table.side_effect = RuntimeError("db down")

        with patch("src.db.users.get_supabase_client", return_value=client):
            assert get_user_by_email_ci("a@x.com") is None
