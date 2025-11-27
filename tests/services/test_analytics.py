#!/usr/bin/env python3
"""
Tests for analytics service

Tests cover:
- Trial analytics retrieval
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from src.services.analytics import get_trial_analytics


class TestTrialAnalytics:
    """Test trial analytics function"""

    @patch('src.services.analytics.get_supabase_client')
    def test_get_trial_analytics_returns_dict(self, mock_supabase):
        """Test that get_trial_analytics returns a dictionary"""
        # Mock Supabase client and query chain
        mock_client = Mock()
        mock_supabase.return_value = mock_client

        # Create mock result with count attribute
        mock_result = Mock()
        mock_result.count = 0
        mock_result.data = []

        # Mock all possible query chains
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.gt.return_value = mock_query
        mock_query.in_.return_value = mock_query
        mock_query.is_not.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.execute.return_value = mock_result

        mock_client.table.return_value = mock_query

        result = get_trial_analytics()
        assert isinstance(result, dict)

    @patch('src.services.analytics.get_supabase_client')
    def test_get_trial_analytics_has_required_keys(self, mock_supabase):
        """Test that result has all required keys"""
        # Mock Supabase client and query chain
        mock_client = Mock()
        mock_supabase.return_value = mock_client

        # Create mock result with count attribute
        mock_result = Mock()
        mock_result.count = 0
        mock_result.data = []

        # Mock all possible query chains
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.gt.return_value = mock_query
        mock_query.in_.return_value = mock_query
        mock_query.is_not.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.execute.return_value = mock_result

        mock_client.table.return_value = mock_query

        result = get_trial_analytics()

        assert 'signups' in result
        assert 'started_trial' in result
        assert 'converted' in result
        assert 'conversion_rate' in result

    @patch('src.services.analytics.get_supabase_client')
    def test_get_trial_analytics_default_values(self, mock_supabase):
        """Test that default values are zero (TODO implementation)"""
        # Mock Supabase client and query chain
        mock_client = Mock()
        mock_supabase.return_value = mock_client

        # Create mock result with count attribute
        mock_result = Mock()
        mock_result.count = 0
        mock_result.data = []

        # Mock all possible query chains
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.gt.return_value = mock_query
        mock_query.in_.return_value = mock_query
        mock_query.is_not.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.execute.return_value = mock_result

        mock_client.table.return_value = mock_query

        result = get_trial_analytics()

        assert result['signups'] == 0
        assert result['started_trial'] == 0
        assert result['converted'] == 0
        assert result['conversion_rate'] == 0.0

    @patch('src.services.analytics.get_supabase_client')
    def test_get_trial_analytics_value_types(self, mock_supabase):
        """Test that values have correct types"""
        # Mock Supabase client and query chain
        mock_client = Mock()
        mock_supabase.return_value = mock_client

        # Create mock result with count attribute
        mock_result = Mock()
        mock_result.count = 0
        mock_result.data = []

        # Mock all possible query chains
        mock_query = MagicMock()
        mock_query.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.gt.return_value = mock_query
        mock_query.in_.return_value = mock_query
        mock_query.is_not.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.execute.return_value = mock_result

        mock_client.table.return_value = mock_query

        result = get_trial_analytics()

        assert isinstance(result['signups'], int)
        assert isinstance(result['started_trial'], int)
        assert isinstance(result['converted'], int)
        assert isinstance(result['conversion_rate'], float)
