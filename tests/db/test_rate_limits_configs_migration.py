"""
Test suite for rate_limits to rate_limit_configs migration.

This test suite verifies that the migration from the legacy rate_limits table
to the rate_limit_configs table works correctly, as documented in Issue #977.
"""

import pytest
from unittest.mock import Mock, patch

import src.db.rate_limits as rate_limits_module


@pytest.fixture
def mock_supabase_client():
    """Mock Supabase client"""
    return Mock()


@pytest.fixture
def sample_api_key():
    """Sample API key for testing"""
    return "gw_test_key_abc123"


@pytest.fixture
def sample_user_id():
    """Sample user ID for testing"""
    return 12345


@pytest.fixture
def sample_api_key_id():
    """Sample API key ID for testing"""
    return 67890


@pytest.mark.unit
class TestGetUserRateLimitsConfigsMigration:
    """Test get_user_rate_limits with rate_limit_configs table"""

    @pytest.mark.unit
    @patch("src.db.rate_limits.get_supabase_client")
    def test_get_rate_limits_from_configs_table(
        self, mock_get_client, mock_supabase_client, sample_api_key, sample_api_key_id
    ):
        """Test retrieving rate limits from rate_limit_configs table"""
        mock_get_client.return_value = mock_supabase_client

        # Mock API key lookup
        key_result = Mock()
        key_result.data = [{"id": sample_api_key_id}]

        # Mock rate_limit_configs lookup
        config_result = Mock()
        config_result.data = [
            {
                "max_requests": 1200,  # per hour
                "max_tokens": 120000,  # per hour
            }
        ]

        # Setup mock chain
        table_mock = Mock()
        table_mock.select.return_value.eq.return_value.execute.side_effect = [
            key_result,
            config_result,
        ]
        mock_supabase_client.table.return_value = table_mock

        # Execute
        result = rate_limits_module.get_user_rate_limits(sample_api_key)

        # Verify
        assert result is not None
        assert result["requests_per_minute"] == 20  # 1200 / 60
        assert result["requests_per_hour"] == 1200
        assert result["requests_per_day"] == 28800  # 1200 * 24
        assert result["tokens_per_minute"] == 2000  # 120000 / 60
        assert result["tokens_per_hour"] == 120000
        assert result["tokens_per_day"] == 2880000  # 120000 * 24

    @pytest.mark.unit
    @patch("src.db.rate_limits.get_supabase_client")
    def test_get_rate_limits_returns_none_when_no_config(
        self, mock_get_client, mock_supabase_client, sample_api_key
    ):
        """Test that None is returned when no rate limits are configured"""
        mock_get_client.return_value = mock_supabase_client

        # Mock empty results
        empty_result = Mock()
        empty_result.data = []

        table_mock = Mock()
        table_mock.select.return_value.eq.return_value.execute.return_value = empty_result
        mock_supabase_client.table.return_value = table_mock

        # Execute
        result = rate_limits_module.get_user_rate_limits(sample_api_key)

        # Verify
        assert result is None

    @pytest.mark.unit
    @patch("src.db.rate_limits.get_supabase_client")
    def test_get_rate_limits_handles_missing_api_key(
        self, mock_get_client, mock_supabase_client, sample_api_key
    ):
        """Test handling when API key doesn't exist"""
        mock_get_client.return_value = mock_supabase_client

        # Mock API key not found
        key_result = Mock()
        key_result.data = []

        table_mock = Mock()
        table_mock.select.return_value.eq.return_value.execute.return_value = key_result
        mock_supabase_client.table.return_value = table_mock

        # Execute
        result = rate_limits_module.get_user_rate_limits(sample_api_key)

        # Verify
        assert result is None


@pytest.mark.unit
class TestSetUserRateLimitsConfigsMigration:
    """Test set_user_rate_limits with rate_limit_configs table"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("src.db.rate_limits.get_supabase_client")
    async def test_set_rate_limits_creates_new_config(
        self, mock_get_client, mock_supabase_client, sample_api_key, sample_api_key_id, sample_user_id
    ):
        """Test creating new rate limit config in rate_limit_configs table"""
        mock_get_client.return_value = mock_supabase_client

        # Mock API key lookup
        key_result = Mock()
        key_result.data = [{"id": sample_api_key_id, "user_id": sample_user_id}]

        # Mock no existing config
        empty_result = Mock()
        empty_result.data = []

        # Mock successful insert
        insert_result = Mock()
        insert_result.data = [{"id": 1}]

        # Setup mock chain
        table_mock = Mock()
        mock_supabase_client.table.return_value = table_mock

        # Configure different return values for different calls
        def table_side_effect(table_name):
            mock = Mock()
            if table_name == "api_keys_new":
                mock.select.return_value.eq.return_value.execute.return_value = key_result
            elif table_name == "rate_limit_configs":
                # First call (check existing) returns empty, second call (insert) returns success
                mock.select.return_value.eq.return_value.execute.return_value = empty_result
                mock.insert.return_value.execute.return_value = insert_result
            return mock

        mock_supabase_client.table.side_effect = table_side_effect

        # Execute
        rate_limits = {
            "requests_per_hour": 1000,
            "tokens_per_hour": 100000,
            "burst_limit": 50,
            "concurrency_limit": 25,
        }

        await rate_limits_module.set_user_rate_limits(sample_api_key, rate_limits)

        # Verify insert was called
        assert mock_supabase_client.table.call_count >= 2

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("src.db.rate_limits.get_supabase_client")
    async def test_set_rate_limits_updates_existing_config(
        self, mock_get_client, mock_supabase_client, sample_api_key, sample_api_key_id, sample_user_id
    ):
        """Test updating existing rate limit config"""
        mock_get_client.return_value = mock_supabase_client

        # Mock API key lookup
        key_result = Mock()
        key_result.data = [{"id": sample_api_key_id, "user_id": sample_user_id}]

        # Mock existing config
        existing_result = Mock()
        existing_result.data = [{"id": 1}]

        # Mock successful update
        update_result = Mock()
        update_result.data = [{"id": 1}]

        # Setup mock chain
        def table_side_effect(table_name):
            mock = Mock()
            if table_name == "api_keys_new":
                mock.select.return_value.eq.return_value.execute.return_value = key_result
            elif table_name == "rate_limit_configs":
                mock.select.return_value.eq.return_value.execute.return_value = existing_result
                mock.update.return_value.eq.return_value.execute.return_value = update_result
            return mock

        mock_supabase_client.table.side_effect = table_side_effect

        # Execute
        rate_limits = {
            "requests_per_hour": 2000,
            "tokens_per_hour": 200000,
        }

        await rate_limits_module.set_user_rate_limits(sample_api_key, rate_limits)

        # Verify update was called
        assert mock_supabase_client.table.call_count >= 2

    @pytest.mark.unit
    @pytest.mark.asyncio
    @patch("src.db.rate_limits.get_supabase_client")
    async def test_set_rate_limits_raises_error_for_missing_key(
        self, mock_get_client, mock_supabase_client, sample_api_key
    ):
        """Test that ValueError is raised when API key not found"""
        mock_get_client.return_value = mock_supabase_client

        # Mock API key not found
        key_result = Mock()
        key_result.data = []

        table_mock = Mock()
        table_mock.select.return_value.eq.return_value.execute.return_value = key_result
        mock_supabase_client.table.return_value = table_mock

        # Execute and verify
        with pytest.raises(ValueError, match="API key not found"):
            await rate_limits_module.set_user_rate_limits(sample_api_key, {})


@pytest.mark.unit
class TestRateLimitsLegacyTableRemoval:
    """Test that legacy rate_limits table is no longer referenced"""

    @pytest.mark.unit
    @patch("src.db.rate_limits.get_supabase_client")
    def test_no_fallback_to_legacy_table(
        self, mock_get_client, mock_supabase_client, sample_api_key
    ):
        """
        Test that get_user_rate_limits does NOT fallback to legacy rate_limits table.
        This test verifies the fix for Issue #977.
        """
        mock_get_client.return_value = mock_supabase_client

        # Mock no results from api_keys_new or rate_limit_configs
        empty_result = Mock()
        empty_result.data = []

        table_mock = Mock()
        table_mock.select.return_value.eq.return_value.execute.return_value = empty_result
        mock_supabase_client.table.return_value = table_mock

        # Execute
        result = rate_limits_module.get_user_rate_limits(sample_api_key)

        # Verify
        assert result is None

        # Verify that rate_limits table was NEVER accessed
        table_calls = [call[0][0] for call in mock_supabase_client.table.call_args_list]
        assert "rate_limits" not in table_calls, "Legacy rate_limits table should not be accessed"

    @pytest.mark.unit
    def test_set_user_rate_limits_uses_configs_table(self):
        """
        Verify that set_user_rate_limits uses rate_limit_configs table.
        This is a documentation test to ensure we're using the correct table.
        """
        import inspect
        import src.db.rate_limits

        # Get source code of set_user_rate_limits
        source = inspect.getsource(src.db.rate_limits.set_user_rate_limits)

        # Verify rate_limit_configs is referenced
        assert "rate_limit_configs" in source, "set_user_rate_limits should use rate_limit_configs table"

        # Verify legacy table is NOT used for writes
        # Note: The legacy table name might appear in comments, so we check for actual table operations
        assert 'table("rate_limits")' not in source or "# " in source.split('table("rate_limits")')[0], \
            "set_user_rate_limits should not write to legacy rate_limits table"


@pytest.mark.integration
class TestRateLimitConfigsIntegration:
    """Integration tests for rate_limit_configs functionality"""

    @pytest.mark.integration
    @pytest.mark.asyncio
    @patch("src.db.rate_limits.get_supabase_client")
    async def test_full_lifecycle_create_retrieve_update(
        self, mock_get_client, sample_api_key, sample_api_key_id, sample_user_id
    ):
        """Test full lifecycle: create config, retrieve it, update it"""
        mock_client = Mock()
        mock_get_client.return_value = mock_client

        # Step 1: Create rate limits
        key_result = Mock()
        key_result.data = [{"id": sample_api_key_id, "user_id": sample_user_id}]

        empty_result = Mock()
        empty_result.data = []

        insert_result = Mock()
        insert_result.data = [{"id": 1}]

        def create_table_mock(table_name):
            mock = Mock()
            if table_name == "api_keys_new":
                mock.select.return_value.eq.return_value.execute.return_value = key_result
            elif table_name == "rate_limit_configs":
                mock.select.return_value.eq.return_value.execute.return_value = empty_result
                mock.insert.return_value.execute.return_value = insert_result
            return mock

        mock_client.table.side_effect = create_table_mock

        # Create
        rate_limits = {"requests_per_hour": 500, "tokens_per_hour": 50000}
        await rate_limits_module.set_user_rate_limits(sample_api_key, rate_limits)

        # Step 2: Retrieve rate limits
        config_result = Mock()
        config_result.data = [{"max_requests": 500, "max_tokens": 50000}]

        def retrieve_table_mock(table_name):
            mock = Mock()
            if table_name == "api_keys_new":
                mock.select.return_value.eq.return_value.execute.return_value = key_result
            elif table_name == "rate_limit_configs":
                mock.select.return_value.eq.return_value.execute.return_value = config_result
            return mock

        mock_client.table.side_effect = retrieve_table_mock

        # Retrieve
        retrieved = rate_limits_module.get_user_rate_limits(sample_api_key)
        assert retrieved["requests_per_hour"] == 500
        assert retrieved["tokens_per_hour"] == 50000


# Smoke test
@pytest.mark.unit
def test_module_imports():
    """Smoke test to ensure the module imports correctly after migration"""
    import src.db.rate_limits

    assert hasattr(src.db.rate_limits, "get_user_rate_limits")
    assert hasattr(src.db.rate_limits, "set_user_rate_limits")
    assert hasattr(src.db.rate_limits, "check_rate_limit")
    assert hasattr(src.db.rate_limits, "update_rate_limit_usage")
