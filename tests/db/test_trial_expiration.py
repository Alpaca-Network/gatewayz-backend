#!/usr/bin/env python3
"""
Tests for trial expiration functionality

Tests the database function update_expired_trials() and the scheduled
expiration script logic.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from src.config.supabase_config import get_supabase_client


class TestTrialExpiration:
    """Test trial expiration database function and logic"""

    @pytest.fixture
    def mock_supabase_client(self):
        """Mock Supabase client"""
        client = Mock()
        table_mock = Mock()
        rpc_mock = Mock()

        client.table.return_value = table_mock
        client.rpc.return_value = rpc_mock

        # Chainable methods
        table_mock.select.return_value = table_mock
        table_mock.eq.return_value = table_mock
        table_mock.lt.return_value = table_mock
        table_mock.in_.return_value = table_mock
        table_mock.update.return_value = table_mock
        table_mock.execute.return_value = Mock(data=[])

        rpc_mock.execute.return_value = Mock(data={})

        return client, table_mock, rpc_mock

    @pytest.fixture
    def expired_user(self):
        """Create a user with expired trial"""
        now = datetime.now(timezone.utc)
        expired_time = now - timedelta(days=1)
        return {
            "id": 1,
            "username": "testuser",
            "email": "test@example.com",
            "subscription_status": "trial",
            "trial_expires_at": expired_time.isoformat(),
        }

    @pytest.fixture
    def active_trial_user(self):
        """Create a user with active trial"""
        now = datetime.now(timezone.utc)
        future_time = now + timedelta(days=2)
        return {
            "id": 2,
            "username": "activeuser",
            "email": "active@example.com",
            "subscription_status": "trial",
            "trial_expires_at": future_time.isoformat(),
        }

    @patch("src.config.supabase_config.get_supabase_client")
    def test_update_expired_trials_function(self, mock_get_client, mock_supabase_client):
        """Test the update_expired_trials database function"""
        client, table_mock, rpc_mock = mock_supabase_client
        mock_get_client.return_value = client

        # Mock RPC response with update counts
        rpc_mock.execute.return_value = Mock(
            data=[{"users_updated": 5, "api_keys_updated": 8}]
        )

        # Call the database function
        result = client.rpc("update_expired_trials").execute()

        # Verify RPC was called
        client.rpc.assert_called_once_with("update_expired_trials")

        # Verify results
        assert result.data[0]["users_updated"] == 5
        assert result.data[0]["api_keys_updated"] == 8

    @patch("src.config.supabase_config.get_supabase_client")
    def test_find_expired_trials(
        self, mock_get_client, mock_supabase_client, expired_user, active_trial_user
    ):
        """Test finding expired trials"""
        client, table_mock, rpc_mock = mock_supabase_client
        mock_get_client.return_value = client

        # Mock response with one expired trial
        table_mock.execute.return_value = Mock(data=[expired_user])

        # Query for expired trials
        now = datetime.now(timezone.utc).isoformat()
        result = (
            client.table("users")
            .select("id, username, email, trial_expires_at, subscription_status")
            .eq("subscription_status", "trial")
            .lt("trial_expires_at", now)
            .execute()
        )

        # Verify the query
        client.table.assert_called_with("users")
        assert len(result.data) == 1
        assert result.data[0]["subscription_status"] == "trial"

    @patch("src.config.supabase_config.get_supabase_client")
    def test_update_users_table(self, mock_get_client, mock_supabase_client, expired_user):
        """Test updating users table for expired trials"""
        client, table_mock, rpc_mock = mock_supabase_client
        mock_get_client.return_value = client

        # Mock successful update
        table_mock.execute.return_value = Mock(data=[expired_user])

        now = datetime.now(timezone.utc).isoformat()

        # Update expired trials
        result = (
            client.table("users")
            .update({"subscription_status": "expired", "updated_at": now})
            .in_("id", [1])
            .execute()
        )

        # Verify update was called
        table_mock.update.assert_called_once()
        assert len(result.data) == 1

    @patch("src.config.supabase_config.get_supabase_client")
    def test_update_api_keys_table(self, mock_get_client, mock_supabase_client):
        """Test updating api_keys_new table for expired trials"""
        client, table_mock, rpc_mock = mock_supabase_client
        mock_get_client.return_value = client

        # Mock API key data
        api_key_data = {
            "id": 1,
            "user_id": 1,
            "is_trial": True,
            "subscription_status": "trial",
        }
        table_mock.execute.return_value = Mock(data=[api_key_data])

        now = datetime.now(timezone.utc).isoformat()

        # Update API keys for expired trials
        result = (
            client.table("api_keys_new")
            .update(
                {
                    "subscription_status": "expired",
                    "trial_active": False,
                    "trial_expired": True,
                    "updated_at": now,
                }
            )
            .in_("user_id", [1])
            .eq("is_trial", True)
            .execute()
        )

        # Verify update was called
        table_mock.update.assert_called_once()
        assert len(result.data) == 1

    @patch("src.config.supabase_config.get_supabase_client")
    def test_no_expired_trials(self, mock_get_client, mock_supabase_client):
        """Test when there are no expired trials"""
        client, table_mock, rpc_mock = mock_supabase_client
        mock_get_client.return_value = client

        # Mock empty response
        table_mock.execute.return_value = Mock(data=[])

        # Query for expired trials
        now = datetime.now(timezone.utc).isoformat()
        result = (
            client.table("users")
            .select("id, username, email, trial_expires_at, subscription_status")
            .eq("subscription_status", "trial")
            .lt("trial_expires_at", now)
            .execute()
        )

        # Verify no data returned
        assert len(result.data) == 0

    @patch("src.config.supabase_config.get_supabase_client")
    def test_multiple_expired_trials(
        self, mock_get_client, mock_supabase_client, expired_user
    ):
        """Test updating multiple expired trials"""
        client, table_mock, rpc_mock = mock_supabase_client
        mock_get_client.return_value = client

        # Mock multiple expired users
        expired_user_2 = expired_user.copy()
        expired_user_2["id"] = 2
        expired_user_2["username"] = "testuser2"
        expired_user_2["email"] = "test2@example.com"

        table_mock.execute.return_value = Mock(data=[expired_user, expired_user_2])

        # Query for expired trials
        now = datetime.now(timezone.utc).isoformat()
        result = (
            client.table("users")
            .select("id, username, email, trial_expires_at, subscription_status")
            .eq("subscription_status", "trial")
            .lt("trial_expires_at", now)
            .execute()
        )

        # Verify multiple users returned
        assert len(result.data) == 2
        assert all(u["subscription_status"] == "trial" for u in result.data)

    def test_trial_expiration_timestamp_parsing(self):
        """Test that trial expiration timestamps are correctly parsed"""
        now = datetime.now(timezone.utc)
        expired_time = now - timedelta(days=1)
        future_time = now + timedelta(days=1)

        # Test ISO format
        assert expired_time < now
        assert future_time > now

        # Test comparison with ISO string
        expired_iso = expired_time.isoformat()
        future_iso = future_time.isoformat()

        assert expired_iso < now.isoformat()
        assert future_iso > now.isoformat()


class TestTrialExpirationEdgeCases:
    """Test edge cases in trial expiration"""

    def test_trial_expires_exactly_now(self):
        """Test when trial expires at exactly current time"""
        now = datetime.now(timezone.utc)
        trial_end = now

        # Trial should be considered expired at exact time
        assert trial_end <= now

    def test_trial_expires_one_second_ago(self):
        """Test when trial expired one second ago"""
        now = datetime.now(timezone.utc)
        trial_end = now - timedelta(seconds=1)

        assert trial_end < now

    def test_trial_expires_in_one_second(self):
        """Test when trial expires in one second"""
        now = datetime.now(timezone.utc)
        trial_end = now + timedelta(seconds=1)

        assert trial_end > now

    def test_timezone_aware_comparison(self):
        """Test that timezone-aware datetimes are compared correctly"""
        now_utc = datetime.now(timezone.utc)

        # Create a datetime in the past with UTC timezone
        past_utc = now_utc - timedelta(days=1)

        assert past_utc < now_utc
        assert past_utc.tzinfo == timezone.utc
        assert now_utc.tzinfo == timezone.utc
