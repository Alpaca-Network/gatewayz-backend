#!/usr/bin/env python3
"""
Tests for plans database module
"""

from unittest.mock import MagicMock, patch

import pytest

from src.db.plans import get_plan_id_by_tier


class TestGetPlanIdByTier:
    """Test get_plan_id_by_tier tier-code lookup"""

    @pytest.fixture
    def fake_supabase(self):
        """
        Mock Supabase client. Named ``fake_supabase`` so the global
        skip_if_no_database autouse hook treats this module as using the
        in-memory stub instead of requiring a real Supabase connection.
        """
        with patch("src.db.plans.get_supabase_client") as mock:
            client = MagicMock()
            mock.return_value = client
            yield client

    def _set_result(self, client, data):
        client.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = (
            data
        )

    def test_basic_returns_starter_id(self, fake_supabase):
        self._set_result(fake_supabase, [{"id": 3}])

        assert get_plan_id_by_tier("basic") == 3

    def test_pro_returns_professional_id(self, fake_supabase):
        self._set_result(fake_supabase, [{"id": 4}])

        assert get_plan_id_by_tier("pro") == 4

    def test_max_returns_business_id(self, fake_supabase):
        self._set_result(fake_supabase, [{"id": 5}])

        assert get_plan_id_by_tier("max") == 5

    def test_unknown_tier_returns_none(self, fake_supabase):
        self._set_result(fake_supabase, [])

        assert get_plan_id_by_tier("nonexistent") is None

    def test_query_matches_on_tier_column(self, fake_supabase):
        self._set_result(fake_supabase, [{"id": 3}])

        get_plan_id_by_tier("basic")

        fake_supabase.table.return_value.select.return_value.eq.assert_called_with("tier", "basic")
