"""
Tests for API key creation graceful degradation when tables are missing.

This tests the fixes for handling missing rate_limit_configs and api_key_audit_logs tables.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.db.api_keys import create_api_key, delete_api_key, update_api_key


class TestAPIKeyGracefulDegradation:
    """Test graceful degradation when database tables are missing."""

    @pytest.mark.asyncio
    @patch("src.db.api_keys.get_supabase_client")
    @patch("secrets.token_urlsafe")
    @patch("src.utils.crypto.sha256_key_hash")
    @patch("src.utils.crypto.encrypt_api_key")
    async def test_create_api_key_missing_rate_limit_configs_table(
        self, mock_encrypt, mock_hash, mock_token, mock_supabase
    ):
        """Test that API key creation succeeds even when rate_limit_configs table is missing."""
        # Mock the API key generation
        mock_token.return_value = "test_key_123"
        mock_hash.return_value = "hashed_key"
        mock_encrypt.return_value = ("encrypted_key", 1)

        # Mock successful API key creation
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        # API key insert succeeds
        mock_client.table.return_value.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": 1, "key_hash": "hashed_key"}]
        )

        # Define the side effect function that simulates table-specific behavior
        def table_side_effect(table_name):
            mock_table = MagicMock()
            if table_name == "rate_limit_configs":
                # Simulate PGRST205 error for missing table
                mock_table.insert.return_value.execute.side_effect = Exception(
                    "{'code': 'PGRST205', 'message': \"Could not find the table 'public.rate_limit_configs'\"}"
                )
            elif table_name == "api_key_audit_logs":
                # audit logs succeed
                mock_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": 1}])
            else:
                # api_keys_new succeeds
                mock_table.insert.return_value.execute.return_value = MagicMock(
                    data=[{"id": 1, "key_hash": "hashed_key"}]
                )
            return mock_table

        mock_client.table.side_effect = table_side_effect

        # Call create_api_key - should NOT raise exception despite missing table
        api_key, key_id = create_api_key(
            user_id=123, key_name="Test Key", environment_tag="production"
        )

        # Verify API key was returned successfully
        assert api_key == "gw_test_key_123"  # prefix 'gw_' + mock token
        assert key_id == 1

    @pytest.mark.asyncio
    @patch("src.db.api_keys.get_supabase_client")
    @patch("secrets.token_urlsafe")
    @patch("src.utils.crypto.sha256_key_hash")
    @patch("src.utils.crypto.encrypt_api_key")
    async def test_create_api_key_missing_audit_logs_table(
        self, mock_encrypt, mock_hash, mock_token, mock_supabase
    ):
        """Test that API key creation succeeds even when api_key_audit_logs table is missing."""
        mock_token.return_value = "test_key_456"
        mock_hash.return_value = "hashed_key"
        mock_encrypt.return_value = ("encrypted_key", 1)

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        # Define the side effect for missing audit logs table
        def table_side_effect(table_name):
            mock_table = MagicMock()
            if table_name == "api_key_audit_logs":
                # Simulate PGRST205 error
                mock_table.insert.return_value.execute.side_effect = Exception(
                    "{'code': 'PGRST205', 'hint': \"Perhaps you meant the table 'public.api_keys_new'\"}"
                )
            elif table_name == "rate_limit_configs":
                # rate limits succeed
                mock_table.insert.return_value.execute.return_value = MagicMock(data=[{"id": 1}])
            else:
                # api_keys_new succeeds
                mock_table.insert.return_value.execute.return_value = MagicMock(
                    data=[{"id": 2, "key_hash": "hashed_key"}]
                )
            return mock_table

        mock_client.table.side_effect = table_side_effect

        # Should succeed despite missing audit logs table
        api_key, key_id = create_api_key(
            user_id=456, key_name="Test Key 2", environment_tag="staging"
        )

        assert api_key == "gw_test_key_456"
        assert key_id == 2

    @pytest.mark.asyncio
    @patch("src.db.api_keys.get_supabase_client")
    @patch("secrets.token_urlsafe")
    @patch("src.utils.crypto.sha256_key_hash")
    @patch("src.utils.crypto.encrypt_api_key")
    async def test_create_api_key_both_tables_missing(
        self, mock_encrypt, mock_hash, mock_token, mock_supabase
    ):
        """Test that API key creation succeeds even when BOTH auxiliary tables are missing."""
        mock_token.return_value = "test_key_789"
        mock_hash.return_value = "hashed_key"
        mock_encrypt.return_value = ("encrypted_key", 1)

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        # Both auxiliary tables missing
        def table_side_effect(table_name):
            mock_table = MagicMock()
            if table_name in ("api_key_audit_logs", "rate_limit_configs"):
                mock_table.insert.return_value.execute.side_effect = Exception(
                    f"Could not find the table 'public.{table_name}' in the schema cache"
                )
            else:
                # api_keys_new succeeds
                mock_table.insert.return_value.execute.return_value = MagicMock(
                    data=[{"id": 3, "key_hash": "hashed_key"}]
                )
            return mock_table

        mock_client.table.side_effect = table_side_effect

        # Should succeed with both tables missing
        api_key, key_id = create_api_key(
            user_id=789, key_name="Test Key 3", environment_tag="development"
        )

        assert api_key == "gw_test_key_789"
        assert key_id == 3

    @pytest.mark.asyncio
    @patch("src.db.api_keys.get_supabase_client")
    async def test_delete_api_key_missing_tables(self, mock_supabase):
        """Test that API key deletion succeeds when auxiliary tables are missing."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        # Mock successful deletion but auxiliary operations fail
        def table_side_effect(table_name):
            mock_table = MagicMock()
            if table_name == "rate_limit_configs":
                mock_table.delete.return_value.eq.return_value.execute.side_effect = Exception(
                    "PGRST205: Could not find the table"
                )
            elif table_name == "api_key_audit_logs":
                mock_table.insert.return_value.execute.side_effect = Exception("PGRST205")
            else:
                # api_keys_new deletion succeeds (two .eq() calls for api_key and user_id)
                mock_table.delete.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
                    data=[{"id": 1, "key_name": "Deleted Key", "environment_tag": "production"}]
                )
            return mock_table

        mock_client.table.side_effect = table_side_effect

        # Should succeed despite missing tables
        result = delete_api_key(user_id=123, api_key="gw_test_to_delete")

        assert result is True

    @pytest.mark.asyncio
    @patch("src.db.api_keys.get_supabase_client")
    async def test_update_api_key_missing_tables(self, mock_supabase):
        """Test that API key update succeeds when auxiliary tables are missing."""
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        # Mock the get operation to return existing key data
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[
                {"id": 1, "key_name": "Old Name", "environment_tag": "production", "user_id": 123}
            ]
        )

        # Mock successful update but auxiliary operations fail
        def table_side_effect_update(table_name):
            mock_table = MagicMock()
            if table_name == "rate_limit_configs":
                mock_table.update.return_value.eq.return_value.execute.side_effect = Exception(
                    "Could not find the table 'public.rate_limit_configs'"
                )
            elif table_name == "api_key_audit_logs":
                mock_table.insert.return_value.execute.side_effect = Exception("PGRST205")
            else:
                # api_keys_new update succeeds
                if hasattr(mock_table, "update"):
                    mock_table.update.return_value.eq.return_value.execute.return_value = MagicMock(
                        data=[{"id": 1, "key_name": "Updated Name"}]
                    )
                # select also succeeds
                mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
                    data=[
                        {
                            "id": 1,
                            "key_name": "Old Name",
                            "environment_tag": "production",
                            "user_id": 123,
                        }
                    ]
                )
            return mock_table

        mock_client.table.side_effect = table_side_effect_update

        # Should succeed despite missing tables
        result = update_api_key(
            api_key="gw_test_key",
            user_id=123,
            updates={"key_name": "Updated Name", "max_requests": 2000},
        )

        assert result is True

    @pytest.mark.asyncio
    @patch("src.db.api_keys.get_supabase_client")
    @patch("secrets.token_urlsafe")
    @patch("src.utils.crypto.sha256_key_hash")
    @patch("src.utils.crypto.encrypt_api_key")
    async def test_create_api_key_real_error_still_logged(
        self, mock_encrypt, mock_hash, mock_token, mock_supabase
    ):
        """Test that real errors (non-missing-table) are still logged properly."""
        mock_token.return_value = "test_key_error"
        mock_encrypt.return_value = ("encrypted_key", 1)
        mock_hash.return_value = "hashed_key"

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        # Simulate a REAL error (not a missing table)
        def table_side_effect(table_name):
            mock_table = MagicMock()
            if table_name == "rate_limit_configs":
                # Real database error (not PGRST205)
                mock_table.insert.return_value.execute.side_effect = Exception(
                    "Database connection timeout"
                )
            else:
                mock_table.insert.return_value.execute.return_value = MagicMock(
                    data=[{"id": 99, "key_hash": "hashed_key"}]
                )
            return mock_table

        mock_client.table.side_effect = table_side_effect

        # Should still create the key (graceful degradation)
        # but the real error should be logged (not suppressed)
        with patch("src.db.api_keys.logger") as mock_logger:
            api_key, key_id = create_api_key(
                user_id=999, key_name="Error Test Key", environment_tag="test"
            )

            # Verify real error WAS logged (not suppressed)
            mock_logger.warning.assert_called()
            warning_call_args = str(mock_logger.warning.call_args)
            assert "Database connection timeout" in warning_call_args

        assert api_key == "gw_test_key_error"
        assert key_id == 99
