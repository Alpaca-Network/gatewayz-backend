"""
Tests for /admin/users/stats and /admin/users/growth endpoints
These tests ensure the 1,000 user limit bug doesn't regress
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app

# Test client
client = TestClient(app)

# Mock admin user for authentication
MOCK_ADMIN_USER = {
    "id": 1,
    "username": "admin",
    "email": "admin@test.com",
    "role": "admin",
    "is_active": True,
}


class TestAdminUsersStats:
    """Test the fixed /admin/users/stats endpoint"""

    @patch("src.security.deps.require_admin")
    @patch("src.config.supabase_config.get_supabase_client")
    def test_stats_returns_accurate_totals_not_limited(self, mock_client, mock_auth):
        """Test that stats endpoint returns accurate totals beyond 1,000 users"""

        # Setup mock
        mock_auth.return_value = MOCK_ADMIN_USER

        # Mock Supabase client
        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # Mock count queries to return > 1,000 users
        mock_count_result = MagicMock()
        mock_count_result.count = 38936  # Simulate real user count
        mock_supabase.table.return_value.select.return_value.ilike.return_value.eq.return_value.execute.return_value = (
            mock_count_result
        )

        # Mock other queries (role, status, credits, subscription)
        mock_data_result = MagicMock()
        mock_data_result.data = (
            [{"role": "user"} for _ in range(35000)]  # 35k regular users
            + [{"role": "admin"} for _ in range(100)]  # 100 admins
            + [{"role": "developer"} for _ in range(500)]  # 500 developers
            + [{"role": None} for _ in range(3336)]  # 3336 null roles
        )
        mock_supabase.table.return_value.select.return_value.execute.return_value = mock_data_result

        # Make request
        response = client.get("/admin/users/stats", headers={"Authorization": "Bearer mock_token"})

        # Assertions
        assert response.status_code == 200
        data = response.json()

        # Critical test: Should return > 1,000 users
        assert data["total_users"] == 38936
        assert data["status"] == "success"
        assert "statistics" in data
        assert data["statistics"]["regular_users"] == 38336  # 35000 + 3336

        # Verify count queries were used (not data fetching)
        mock_supabase.table.return_value.select.assert_called()
        # Check that count="exact" was used in queries
        select_calls = mock_supabase.table.return_value.select.call_args_list
        assert any("count" in str(call) for call in select_calls)

    @patch("src.security.deps.require_admin")
    @patch("src.config.supabase_config.get_supabase_client")
    def test_stats_with_filters(self, mock_client, mock_auth):
        """Test stats endpoint with email filter applied"""

        # Setup mock
        mock_auth.return_value = MOCK_ADMIN_USER
        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # Mock filtered count (gmail users only)
        mock_count_result = MagicMock()
        mock_count_result.count = 15420  # ~40% are gmail users
        mock_supabase.table.return_value.select.return_value.ilike.return_value.eq.return_value.execute.return_value = (
            mock_count_result
        )

        # Mock filtered data
        mock_data_result = MagicMock()
        mock_data_result.data = [{"role": "user"} for _ in range(15420)]
        mock_supabase.table.return_value.select.return_value.execute.return_value = mock_data_result

        # Make request with email filter
        response = client.get(
            "/admin/users/stats?email=gmail", headers={"Authorization": "Bearer mock_token"}
        )

        # Assertions
        assert response.status_code == 200
        data = response.json()

        assert data["total_users"] == 15420
        assert data["filters_applied"]["email"] == "gmail"
        assert data["filters_applied"]["is_active"] is None

    @patch("src.security.deps.require_admin")
    def test_stats_unauthorized(self, mock_auth):
        """Test that stats endpoint requires admin authentication"""

        mock_auth.side_effect = Exception("Unauthorized")

        response = client.get("/admin/users/stats")
        assert response.status_code in [401, 403]


class TestAdminUsersGrowth:
    """Test the new /admin/users/growth endpoint"""

    @patch("src.security.deps.require_admin")
    @patch("src.config.supabase_config.get_supabase_client")
    def test_growth_returns_correct_data_points(self, mock_client, mock_auth):
        """Test that growth endpoint returns correct number of data points"""

        # Setup mock
        mock_auth.return_value = MOCK_ADMIN_USER
        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # Mock user creation data for 30 days
        mock_user_data = []
        base_date = datetime.now() - timedelta(days=29)

        for i in range(30):
            # Simulate varying user creation rates
            daily_users = 50 + (i * 2)  # Growing from 50 to 108 users per day
            for j in range(daily_users):
                mock_user_data.append(
                    {
                        "created_at": (base_date + timedelta(days=i)).isoformat(),
                        "registration_date": (base_date + timedelta(days=i)).isoformat(),
                    }
                )

        mock_growth_result = MagicMock()
        mock_growth_result.data = mock_user_data
        mock_supabase.table.return_value.select.return_value.gte.return_value.lte.return_value.order.return_value.execute.return_value = (
            mock_growth_result
        )

        # Mock users before start date
        mock_before_result = MagicMock()
        mock_before_result.count = 35000  # 35k users before our 30-day window
        mock_supabase.table.return_value.select.return_value.lt.return_value.execute.return_value = (
            mock_before_result
        )

        # Make request for 30 days
        response = client.get(
            "/admin/users/growth?days=30", headers={"Authorization": "Bearer mock_token"}
        )

        # Assertions
        assert response.status_code == 200
        data = response.json()

        # Should have exactly 30 data points
        assert len(data["data"]) == 30
        assert data["days"] == 30
        assert data["status"] == "success"

        # Should calculate cumulative correctly
        first_day = data["data"][0]
        last_day = data["data"][-1]

        # First day should have 35k + first day's new users
        assert first_day["value"] > 35000
        assert first_day["new_users"] > 0

        # Last day should have all users
        assert last_day["value"] > first_day["value"]

        # Growth rate should be positive
        assert data["growth_rate"] > 0

    @patch("src.security.deps.require_admin")
    @patch("src.config.supabase_config.get_supabase_client")
    def test_growth_different_day_ranges(self, mock_client, mock_auth):
        """Test growth endpoint with different day ranges"""

        # Setup mock
        mock_auth.return_value = MOCK_ADMIN_USER
        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # Mock minimal data
        mock_growth_result = MagicMock()
        mock_growth_result.data = []
        mock_supabase.table.return_value.select.return_value.gte.return_value.lte.return_value.order.return_value.execute.return_value = (
            mock_growth_result
        )

        mock_before_result = MagicMock()
        mock_before_result.count = 1000
        mock_supabase.table.return_value.select.return_value.lt.return_value.execute.return_value = (
            mock_before_result
        )

        # Test different day ranges
        for days in [7, 30, 90, 365]:
            response = client.get(
                f"/admin/users/growth?days={days}", headers={"Authorization": "Bearer mock_token"}
            )

            assert response.status_code == 200
            data = response.json()

            assert data["days"] == days
            assert len(data["data"]) == days

    @patch("src.security.deps.require_admin")
    def test_growth_unauthorized(self, mock_auth):
        """Test that growth endpoint requires admin authentication"""

        mock_auth.side_effect = Exception("Unauthorized")

        response = client.get("/admin/users/growth")
        assert response.status_code in [401, 403]

    @patch("src.security.deps.require_admin")
    @patch("src.config.supabase_config.get_supabase_client")
    def test_growth_invalid_day_range(self, mock_client, mock_auth):
        """Test growth endpoint validation for day ranges"""

        # Setup mock
        mock_auth.return_value = MOCK_ADMIN_USER

        # Test invalid day ranges
        for invalid_days in [0, -1, 366]:
            response = client.get(
                f"/admin/users/growth?days={invalid_days}",
                headers={"Authorization": "Bearer mock_token"},
            )
            assert response.status_code == 422  # Validation error


class TestRegressionPrevention:
    """Tests specifically to prevent the 1,000 user limit regression"""

    @patch("src.security.deps.require_admin")
    @patch("src.config.supabase_config.get_supabase_client")
    def test_no_1000_limit_regression_stats(self, mock_client, mock_auth):
        """Regression test: Ensure stats endpoint never returns exactly 1,000 due to row limits"""

        # Setup mock
        mock_auth.return_value = MOCK_ADMIN_USER
        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # Mock a scenario where there are exactly 2,500 users
        mock_count_result = MagicMock()
        mock_count_result.count = 2500
        mock_supabase.table.return_value.select.return_value.ilike.return_value.eq.return_value.execute.return_value = (
            mock_count_result
        )

        mock_data_result = MagicMock()
        mock_data_result.data = [{"role": "user"} for _ in range(2500)]
        mock_supabase.table.return_value.select.return_value.execute.return_value = mock_data_result

        response = client.get("/admin/users/stats", headers={"Authorization": "Bearer mock_token"})

        assert response.status_code == 200
        data = response.json()

        # Critical: Should NOT be limited to 1,000
        assert data["total_users"] == 2500
        assert data["total_users"] != 1000

    @patch("src.security.deps.require_admin")
    @patch("src.config.supabase_config.get_supabase_client")
    def test_no_1000_limit_regression_growth(self, mock_client, mock_auth):
        """Regression test: Ensure growth endpoint handles large user bases"""

        # Setup mock
        mock_auth.return_value = MOCK_ADMIN_USER
        mock_supabase = MagicMock()
        mock_client.return_value = mock_supabase

        # Mock scenario with 50k users before period
        mock_before_result = MagicMock()
        mock_before_result.count = 50000
        mock_supabase.table.return_value.select.return_value.lt.return_value.execute.return_value = (
            mock_before_result
        )

        mock_growth_result = MagicMock()
        mock_growth_result.data = []
        mock_supabase.table.return_value.select.return_value.gte.return_value.lte.return_value.order.return_value.execute.return_value = (
            mock_growth_result
        )

        response = client.get(
            "/admin/users/growth?days=30", headers={"Authorization": "Bearer mock_token"}
        )

        assert response.status_code == 200
        data = response.json()

        # Should start with 50k users, not be limited
        assert data["total"] == 50000
        assert data["data"][0]["value"] == 50000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
