"""
Conftest for routes tests - mocks Supabase client to provide test data
"""
import pytest
from unittest.mock import MagicMock, Mock


@pytest.fixture(scope="session", autouse=True)
def mock_supabase_client():
    """Mock the Supabase client to return test data"""
    import src.config.supabase_config as supabase_config

    # Create a mock client
    mock_client = MagicMock()

    # Mock the table() method to return a chainable mock
    def mock_table(table_name):
        table_mock = MagicMock()

        # Create a chainable mock for query building
        def create_chain_mock():
            chain = MagicMock()
            chain.select = MagicMock(return_value=chain)
            chain.eq = MagicMock(return_value=chain)
            chain.execute = MagicMock()

            # Default responses based on table name
            if table_name == "api_keys_new":
                chain.execute.return_value = Mock(data=[{
                    "is_trial": False,
                    "trial_end_date": None,
                }])
            elif table_name == "users":
                chain.execute.return_value = Mock(data=[{
                    "id": 1,
                    "credits": 100.0,
                    "environment_tag": "live",
                    "api_key": "test_api_key",
                    "subscription_status": "active"
                }])

            return chain

        return create_chain_mock()

    mock_client.table = mock_table

    # Replace the get_supabase_client function
    original = supabase_config.get_supabase_client
    supabase_config.get_supabase_client = lambda: mock_client

    yield mock_client

    # Restore
    supabase_config.get_supabase_client = original
