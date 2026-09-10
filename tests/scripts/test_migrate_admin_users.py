"""Tests for scripts/migrate_admin_users.py (Phase A6)."""

from unittest.mock import MagicMock, patch

from scripts.migrate_admin_users import (
    apply_matches,
    build_report,
    create_invites_for_unmatched,
)


def _client_for(admin_users_rows, users_rows):
    client = MagicMock()

    def table(name):
        m = MagicMock()
        if name == "admin_users":
            m.select.return_value.execute.return_value.data = admin_users_rows
        elif name == "users":
            m.select.return_value.execute.return_value.data = users_rows
        return m

    client.table.side_effect = table
    return client


class TestBuildReport:
    def test_matches_by_email_case_insensitively(self):
        client = _client_for(
            admin_users_rows=[{"email": "Admin@Example.com", "role": "admin", "status": "active"}],
            users_rows=[{"id": 1, "email": "admin@example.com", "role": "user"}],
        )
        report = build_report(client)

        assert len(report.matched) == 1
        assert report.matched[0]["user_id"] == 1
        assert report.matched[0]["new_role"] == "admin"
        assert not report.unmatched

    def test_superadmin_maps_to_superadmin(self):
        client = _client_for(
            admin_users_rows=[{"email": "root@x.com", "role": "superadmin", "status": "active"}],
            users_rows=[{"id": 2, "email": "root@x.com", "role": "user"}],
        )
        report = build_report(client)

        assert report.matched[0]["new_role"] == "superadmin"

    def test_unmatched_email_reported(self):
        client = _client_for(
            admin_users_rows=[{"email": "staff@x.com", "role": "admin", "status": "active"}],
            users_rows=[],
        )
        report = build_report(client)

        assert not report.matched
        assert report.unmatched == [{"email": "staff@x.com", "admin_role": "admin"}]

    def test_dev_role_reported_separately_and_not_matched(self):
        client = _client_for(
            admin_users_rows=[{"email": "dev@x.com", "role": "dev", "status": "active"}],
            users_rows=[{"id": 3, "email": "dev@x.com", "role": "user"}],
        )
        report = build_report(client)

        assert not report.matched
        assert not report.unmatched
        assert report.dev_role_skipped == [{"email": "dev@x.com"}]

    def test_unrecognized_role_reported(self):
        client = _client_for(
            admin_users_rows=[{"email": "x@x.com", "role": "wat", "status": "active"}],
            users_rows=[],
        )
        report = build_report(client)

        assert report.unrecognized_role == [{"email": "x@x.com", "admin_role": "wat"}]

    def test_rows_missing_email_are_skipped(self):
        client = _client_for(
            admin_users_rows=[{"email": "", "role": "admin", "status": "active"}],
            users_rows=[],
        )
        report = build_report(client)

        assert not report.matched
        assert not report.unmatched


class TestApplyMatches:
    def test_updates_only_when_role_differs(self):
        client = MagicMock()
        report = MagicMock()
        report.matched = [
            {"user_id": 1, "new_role": "admin", "current_role": "user"},
            {"user_id": 2, "new_role": "admin", "current_role": "admin"},  # no-op
        ]

        updated = apply_matches(client, report)

        assert updated == 1
        client.table.return_value.update.assert_called_once_with({"role": "admin"})

    def test_continues_past_a_failed_row(self):
        client = MagicMock()
        client.table.return_value.update.return_value.eq.return_value.execute.side_effect = [
            RuntimeError("boom"),
            None,
        ]
        report = MagicMock()
        report.matched = [
            {"user_id": 1, "new_role": "admin", "current_role": "user"},
            {"user_id": 2, "new_role": "admin", "current_role": "user"},
        ]

        updated = apply_matches(client, report)

        assert updated == 1


class TestCreateInvitesForUnmatched:
    def test_creates_an_invite_per_unmatched_entry(self):
        client = MagicMock()
        report = MagicMock()
        report.unmatched = [{"email": "a@x.com", "admin_role": "admin"}]
        report.invites_created = []

        with patch(
            "src.db.staff.create_invite",
            return_value=({"id": "uuid-1", "email": "a@x.com"}, "raw-token"),
        ) as mock_create:
            create_invites_for_unmatched(client, report)

        mock_create.assert_called_once_with("a@x.com", "admin", invited_by=None)
        assert report.invites_created == [{"email": "a@x.com", "role": "admin"}]

    def test_never_prints_or_stores_the_raw_token(self):
        client = MagicMock()
        report = MagicMock()
        report.unmatched = [{"email": "a@x.com", "admin_role": "admin"}]
        report.invites_created = []

        with patch(
            "src.db.staff.create_invite",
            return_value=({"id": "uuid-1", "email": "a@x.com"}, "super-secret-raw-token"),
        ):
            create_invites_for_unmatched(client, report)

        assert "super-secret-raw-token" not in str(report.invites_created)
