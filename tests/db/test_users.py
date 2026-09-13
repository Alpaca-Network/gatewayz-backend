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


class TestGetUserByEmailCiWildcardSafety:
    """Fix round 1 on gatewayz-backend#2316: `.ilike()` sends its pattern
    straight to Postgres's ILIKE operator, where `%`/`_` are wildcards. An
    unescaped lookup value therefore isn't an exact match at all --
    "first_last@x.com" (the `_` is "any one char") would also match
    "firstXlast@x.com", a different account entirely."""

    def test_underscore_is_escaped_before_reaching_ilike(self):
        client = MagicMock()
        client.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = (
            []
        )

        with patch("src.db.users.get_supabase_client", return_value=client):
            get_user_by_email_ci("first_last@x.com")

        client.table.return_value.select.return_value.ilike.assert_called_once_with(
            "email", "first\\_last@x.com"
        )

    def test_percent_is_escaped_before_reaching_ilike(self):
        client = MagicMock()
        client.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = (
            []
        )

        with patch("src.db.users.get_supabase_client", return_value=client):
            get_user_by_email_ci("100%off@x.com")

        client.table.return_value.select.return_value.ilike.assert_called_once_with(
            "email", "100\\%off@x.com"
        )

    def test_wildcard_near_miss_row_is_rejected_by_python_post_filter(self):
        """Belt and braces: even if the DB layer somehow returned a
        pattern-matched near-miss (e.g. escaping regressed), the Python-side
        exact (case-insensitive) equality check must still reject it."""
        client = MagicMock()
        near_miss = {"id": 1, "email": "firstXlast@x.com"}
        client.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [
            near_miss
        ]

        with patch("src.db.users.get_supabase_client", return_value=client):
            assert get_user_by_email_ci("first_last@x.com") is None

    def test_exact_match_different_case_is_still_found_among_other_rows(self):
        """The post-filter must not become so strict it breaks the
        original case-insensitivity contract."""
        client = MagicMock()
        near_miss = {"id": 1, "email": "firstXlast@x.com"}
        real_match = {"id": 2, "email": "First_Last@X.com"}
        client.table.return_value.select.return_value.ilike.return_value.execute.return_value.data = [
            near_miss,
            real_match,
        ]

        with patch("src.db.users.get_supabase_client", return_value=client):
            assert get_user_by_email_ci("first_last@x.com") == real_match
