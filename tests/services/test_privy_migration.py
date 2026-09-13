"""
Tests for the Privy app-migration adoption service
(src/services/privy_migration.py, docs/PRIVY_MIGRATION.md).

Route-level wiring (is /auth calling this at the right time, with the right
arguments, never the client-supplied body) is covered separately in
tests/routes/test_auth_privy_migration.py.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services import privy_migration as pm

OLD_APP_ID = "cmg8fkib300g3l40dbs6autqe"
NEW_APP_ID = "cmtxc6wsn00yn0dle1k5a9bzq"
NEW_DID = "did:privy:newappuser123"


def _configure(
    monkeypatch, *, mode="adopt", legacy=(OLD_APP_ID,), app_id=NEW_APP_ID, secret="s3cr3t"
):
    monkeypatch.setattr(pm.Config, "PRIVY_MIGRATION_MODE", mode)
    monkeypatch.setattr(pm.Config, "PRIVY_LEGACY_APP_IDS", frozenset(legacy))
    monkeypatch.setattr(pm.Config, "PRIVY_APP_ID", app_id)
    monkeypatch.setattr(pm.Config, "PRIVY_APP_SECRET", secret)


def _privy_user(*, email="user@example.com", verified_kind="email"):
    if verified_kind == "email":
        linked = [{"type": "email", "address": email}]
    elif verified_kind == "google":
        linked = [{"type": "google_oauth", "email": email}]
    elif verified_kind == "wallet_only":
        linked = [{"type": "wallet", "address": "0xabc"}]
    else:
        linked = []
    return {"id": NEW_DID, "linked_accounts": linked}


def _mock_httpx_response(payload, *, status_ok=True):
    response = MagicMock()
    if status_ok:
        response.raise_for_status = MagicMock()
    else:
        response.raise_for_status = MagicMock(side_effect=Exception("boom"))
    response.json.return_value = payload
    return response


class TestExtractVerifiedEmail:
    def test_email_account(self):
        assert pm._extract_verified_email(_privy_user(verified_kind="email")) == "user@example.com"

    def test_google_oauth_account(self):
        assert pm._extract_verified_email(_privy_user(verified_kind="google")) == "user@example.com"

    def test_wallet_only_has_no_adoptable_email(self):
        assert pm._extract_verified_email(_privy_user(verified_kind="wallet_only")) is None

    def test_malformed_linked_accounts_is_safe(self):
        assert pm._extract_verified_email({"linked_accounts": "not-a-list"}) is None


class TestAttemptAdoptionGating:
    """Preconditions that must block adoption before any network/DB call."""

    @pytest.mark.asyncio
    async def test_mode_off_never_calls_privy(self, monkeypatch):
        _configure(monkeypatch, mode="off")
        with patch("httpx.AsyncClient") as mock_client:
            result = await pm.attempt_adoption(new_did=NEW_DID, token_verified=True)
        assert result is None
        mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_unverified_token_never_calls_privy(self, monkeypatch):
        _configure(monkeypatch, mode="adopt")
        with patch("httpx.AsyncClient") as mock_client:
            result = await pm.attempt_adoption(new_did=NEW_DID, token_verified=False)
        assert result is None
        mock_client.assert_not_called()


class TestAttemptAdoptionHappyPath:
    @pytest.mark.asyncio
    async def test_single_legacy_match_is_adopted(self, monkeypatch):
        _configure(monkeypatch)
        legacy_user = {
            "id": 42,
            "email": "user@example.com",
            "privy_user_id": "did:privy:oldappuser",
            "privy_app_id": OLD_APP_ID,
            "is_active": True,
        }

        mock_response = _mock_httpx_response(_privy_user())
        updated_row = {**legacy_user, "privy_user_id": NEW_DID, "privy_app_id": NEW_APP_ID}

        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.ilike.return_value.in_.return_value.execute.return_value.data = [
            legacy_user
        ]
        mock_supabase.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            updated_row
        ]

        with (
            patch("httpx.AsyncClient") as mock_client,
            patch.object(pm.supabase_config, "get_supabase_client", return_value=mock_supabase),
            patch.object(pm, "record_audit") as mock_audit,
        ):
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            result = await pm.attempt_adoption(new_did=NEW_DID, token_verified=True)

        assert result == updated_row
        mock_audit.assert_called_once()
        call = mock_audit.call_args.kwargs
        assert call["action"] == "auth.privy_migrated"
        assert call["target_type"] == "user"
        assert call["target_id"] == 42
        assert call["metadata"]["app_from"] == OLD_APP_ID
        assert call["metadata"]["app_to"] == NEW_APP_ID
        assert "did:privy" not in str(call["metadata"])  # DIDs are hashed, never logged raw

    @pytest.mark.asyncio
    async def test_no_legacy_match_falls_through(self, monkeypatch):
        _configure(monkeypatch)
        mock_response = _mock_httpx_response(_privy_user())
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.ilike.return_value.in_.return_value.execute.return_value.data = (
            []
        )

        with (
            patch("httpx.AsyncClient") as mock_client,
            patch.object(pm.supabase_config, "get_supabase_client", return_value=mock_supabase),
        ):
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            result = await pm.attempt_adoption(new_did=NEW_DID, token_verified=True)

        assert result is None

    @pytest.mark.asyncio
    async def test_ambiguous_match_does_not_adopt(self, monkeypatch, caplog):
        _configure(monkeypatch)
        mock_response = _mock_httpx_response(_privy_user())
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.ilike.return_value.in_.return_value.execute.return_value.data = [
            {"id": 1, "privy_app_id": OLD_APP_ID, "is_active": True},
            {"id": 2, "privy_app_id": OLD_APP_ID, "is_active": True},
        ]

        with (
            patch("httpx.AsyncClient") as mock_client,
            patch.object(pm.supabase_config, "get_supabase_client", return_value=mock_supabase),
            caplog.at_level("WARNING", logger="src.services.privy_migration"),
        ):
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            result = await pm.attempt_adoption(new_did=NEW_DID, token_verified=True)

        assert result is None
        assert "privy_migration_ambiguous_match" in caplog.text

    def test_already_migrated_row_is_excluded_by_legacy_filter(self, monkeypatch):
        """A row whose privy_app_id already equals the current app is never a
        legacy candidate -- it's the normal existing-user path (matched by
        privy_user_id already, upstream of adoption ever running)."""
        _configure(monkeypatch)
        mock_supabase = MagicMock()

        # The query must filter to PRIVY_LEGACY_APP_IDS only -- NEW_APP_ID
        # (the current app) must never appear in that filter.
        with patch.object(pm.supabase_config, "get_supabase_client", return_value=mock_supabase):
            pm._find_legacy_candidates("user@example.com")
        in_call = mock_supabase.table.return_value.select.return_value.ilike.return_value.in_
        filtered_ids = in_call.call_args.args[1]
        assert NEW_APP_ID not in filtered_ids
        assert OLD_APP_ID in filtered_ids

    @pytest.mark.asyncio
    async def test_unverified_email_wallet_only_does_not_adopt(self, monkeypatch):
        _configure(monkeypatch)
        mock_response = _mock_httpx_response(_privy_user(verified_kind="wallet_only"))

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            result = await pm.attempt_adoption(new_did=NEW_DID, token_verified=True)

        assert result is None

    @pytest.mark.asyncio
    async def test_privy_lookup_failure_falls_through(self, monkeypatch, caplog):
        _configure(monkeypatch)

        with (
            patch("httpx.AsyncClient") as mock_client,
            caplog.at_level("WARNING", logger="src.services.privy_migration"),
        ):
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=TimeoutError("boom")
            )
            result = await pm.attempt_adoption(new_did=NEW_DID, token_verified=True)

        assert result is None
        assert "privy_migration_lookup_failed" in caplog.text

    @pytest.mark.asyncio
    async def test_missing_credentials_falls_through(self, monkeypatch):
        _configure(monkeypatch, secret=None)
        with patch("httpx.AsyncClient") as mock_client:
            result = await pm.attempt_adoption(new_did=NEW_DID, token_verified=True)
        assert result is None
        mock_client.assert_not_called()


class TestMigrationCounts:
    def test_counts_query_both_sides(self, monkeypatch):
        _configure(monkeypatch)
        mock_supabase = MagicMock()
        mock_supabase.table.return_value.select.return_value.in_.return_value.execute.return_value.count = (
            5
        )
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.count = (
            3
        )

        with patch.object(pm.supabase_config, "get_supabase_client", return_value=mock_supabase):
            counts = pm.migration_counts()

        assert counts == {"legacy_users": 5, "migrated_users": 3}

    def test_db_error_returns_zeros(self, monkeypatch):
        _configure(monkeypatch)
        with patch.object(
            pm.supabase_config, "get_supabase_client", side_effect=RuntimeError("down")
        ):
            counts = pm.migration_counts()
        assert counts == {"legacy_users": 0, "migrated_users": 0}
