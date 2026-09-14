"""New Privy-authenticated accounts must be stamped with the Privy app that
issued their DID (docs/PRIVY_MIGRATION.md): `privy_app_id` is what the lazy
adoption path keys on (`PRIVY_LEGACY_APP_IDS`) and what `/admin/status`'s
migration counters read. Until 2026-09-14 every post-cutover account was
created with `privy_app_id = NULL`, so a future app migration could not tell
those rows apart from pre-Privy accounts.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.db.users import create_enhanced_user


@pytest.fixture
def sb():
    return None


def _client_capturing_user_insert():
    inserted: dict = {}

    def make_query(name):
        query = MagicMock()
        query.select.return_value = query
        query.eq.return_value = query
        query.limit.return_value = query

        def _insert(payload):
            if name == "users":
                inserted.update(payload)
            return query

        query.insert.side_effect = _insert
        query.execute.return_value = MagicMock(data=[{"id": 7, **inserted}])
        return query

    client = MagicMock()
    client.table.side_effect = make_query
    return client, inserted


class TestCreateEnhancedUserStampsPrivyAppId:
    def test_privy_user_gets_current_app_id(self, sb):
        client, inserted = _client_capturing_user_insert()
        with (
            patch("src.db.users.get_supabase_client", return_value=client),
            patch("src.db.users.create_api_key", return_value=("gw_live_x", {"id": 1})),
            patch(
                "src.services.payment_gate.resolve_key_environment",
                return_value=("test", "no_payment_signal"),
            ),
            patch("src.db.users.Config.PRIVY_APP_ID", "app_current"),
        ):
            create_enhanced_user(
                username="u",
                email="u@example.com",
                auth_method="privy",
                privy_user_id="did:privy:abc",
            )
        assert inserted["privy_user_id"] == "did:privy:abc"
        assert inserted["privy_app_id"] == "app_current"

    def test_non_privy_user_is_not_stamped(self, sb):
        client, inserted = _client_capturing_user_insert()
        with (
            patch("src.db.users.get_supabase_client", return_value=client),
            patch("src.db.users.create_api_key", return_value=("gw_live_x", {"id": 1})),
            patch(
                "src.services.payment_gate.resolve_key_environment",
                return_value=("test", "no_payment_signal"),
            ),
            patch("src.db.users.Config.PRIVY_APP_ID", "app_current"),
        ):
            create_enhanced_user(username="u", email="u@example.com", auth_method="email")
        assert "privy_user_id" not in inserted
        assert "privy_app_id" not in inserted

    def test_no_app_id_configured_leaves_column_unset(self, sb):
        client, inserted = _client_capturing_user_insert()
        with (
            patch("src.db.users.get_supabase_client", return_value=client),
            patch("src.db.users.create_api_key", return_value=("gw_live_x", {"id": 1})),
            patch(
                "src.services.payment_gate.resolve_key_environment",
                return_value=("test", "no_payment_signal"),
            ),
            patch("src.db.users.Config.PRIVY_APP_ID", None),
        ):
            create_enhanced_user(
                username="u",
                email="u@example.com",
                auth_method="privy",
                privy_user_id="did:privy:abc",
            )
        assert "privy_app_id" not in inserted
