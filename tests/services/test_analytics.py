#!/usr/bin/env python3
"""
Tests for analytics service

Tests cover:
- Trial analytics retrieval
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from src.services.analytics import get_trial_analytics


@pytest.fixture
def mock_supabase_analytics():
    """Mock Supabase client for analytics tests"""
    with patch("src.services.analytics.get_supabase_client") as mock_get_client:
        mock_client = Mock()

        # Mock table().select().eq().execute() chain for signups
        signups_result = Mock()
        signups_result.count = 0
        signups_chain = Mock()
        signups_chain.execute.return_value = signups_result

        # Mock table().select().eq().gt().execute() chain for started_trial
        started_result = Mock()
        started_result.count = 0
        started_chain = Mock()
        started_chain.execute.return_value = started_result

        # Mock table().select().in_().is_not().execute() chain for converted
        converted_result = Mock()
        converted_result.count = 0
        converted_chain = Mock()
        converted_chain.execute.return_value = converted_result

        # Set up the mock to return appropriate chains
        def table_mock(table_name):
            table_obj = Mock()

            def select_mock(*args, **kwargs):
                select_obj = Mock()

                def eq_mock(field, value):
                    if field == "subscription_status" and value == "trial":
                        # Check if this is for started_trial (has gt call)
                        eq_obj = Mock()
                        eq_obj.execute.return_value = signups_result
                        eq_obj.gt = Mock(return_value=started_chain)
                        return eq_obj
                    return Mock(execute=Mock(return_value=signups_result))

                def in_mock(field, values):
                    in_obj = Mock()
                    in_obj.is_not = Mock(return_value=converted_chain)
                    return in_obj

                select_obj.eq = eq_mock
                select_obj.in_ = in_mock
                return select_obj

            table_obj.select = select_mock
            return table_obj

        mock_client.table = table_mock
        mock_get_client.return_value = mock_client
        yield mock_client


class TestTrialAnalytics:
    """Test trial analytics function"""

    def test_get_trial_analytics_returns_dict(self, mock_supabase_analytics):
        """Test that get_trial_analytics returns a dictionary"""
        result = get_trial_analytics()
        assert isinstance(result, dict)

    def test_get_trial_analytics_has_required_keys(self, mock_supabase_analytics):
        """Test that result has all required keys"""
        result = get_trial_analytics()

        assert "signups" in result
        assert "started_trial" in result
        assert "converted" in result
        assert "conversion_rate" in result

    def test_get_trial_analytics_default_values(self, mock_supabase_analytics):
        """Test that default values are zero (TODO implementation)"""
        result = get_trial_analytics()

        assert result["signups"] == 0
        assert result["started_trial"] == 0
        assert result["converted"] == 0
        assert result["conversion_rate"] == 0.0

    def test_get_trial_analytics_value_types(self, mock_supabase_analytics):
        """Test that values have correct types"""
        result = get_trial_analytics()

        assert isinstance(result["signups"], int)
        assert isinstance(result["started_trial"], int)
        assert isinstance(result["converted"], int)
        assert isinstance(result["conversion_rate"], float)
