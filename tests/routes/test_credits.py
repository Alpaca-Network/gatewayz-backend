"""
Tests for Credits Route Endpoints

Covers:
- Credit addition
- Credit adjustment
- Bulk credit addition
- Credit refunds
- Credits summary
- Credit transactions listing

Uses FastAPI dependency override mechanism for testing.
"""

import os
import sys
from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Set test environment
os.environ["APP_ENV"] = "testing"
os.environ["TESTING"] = "true"
os.environ["SUPABASE_URL"] = "https://test.supabase.co"
os.environ["SUPABASE_KEY"] = "test-key"
os.environ["ADMIN_API_KEY"] = "test-admin-key-12345"
os.environ["ENCRYPTION_KEY"] = "test-encryption-key-32-bytes-long!"
os.environ["API_GATEWAY_SALT"] = "test-salt-for-hashing-keys-minimum-16-chars"

from src.main import app
from src.security.deps import require_admin

# Skip tests on Python 3.10 due to compatibility issues
pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Credits route tests have Python 3.10 compatibility issues"
)


@pytest.fixture
def client():
    """FastAPI test client"""
    app.dependency_overrides = {}
    yield TestClient(app)
    app.dependency_overrides = {}


@pytest.fixture
def admin_user():
    """Mock admin user"""
    return {
        "id": 1,
        "user_id": 1,
        "email": "admin@gatewayz.ai",
        "username": "admin",
        "credits": 1000.0,
        "api_key": "test_mock_key_for_testing_only",  # nosec - not a real key
        "is_active": True,
        "is_admin": True,
        "role": "admin",
    }


@pytest.fixture
def auth_headers():
    """Authentication headers for admin"""
    return {
        "Authorization": "Bearer test_mock_key_for_testing_only",  # nosec - not a real key
        "Content-Type": "application/json",
    }


def mock_require_admin():
    """Mock admin authentication dependency"""
    return {
        "id": 1,
        "username": "admin",
        "is_admin": True,
        "role": "admin",
    }


class TestCreditsAdd:
    """Test credit addition endpoint"""

    @patch("src.routes.credits.get_supabase_client")
    @patch("src.routes.credits.log_credit_transaction")
    def test_add_credits_success(
        self, mock_log_transaction, mock_supabase, client, admin_user, auth_headers
    ):
        """Successfully add credits to a user"""
        # Override admin dependency
        app.dependency_overrides[require_admin] = mock_require_admin

        # Mock Supabase client
        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        # Mock user lookup
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 2, "credits": 100.0}
        ]
        # Mock update
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": 2, "credits": 200.0}
        ]

        # Mock transaction logging
        mock_log_transaction.return_value = {"id": 1}

        response = client.post(
            "/credits/add",
            json={"user_id": 2, "amount": 100.0, "description": "Test credit addition"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["user_id"] == 2
        assert data["previous_balance"] == 100.0
        assert data["new_balance"] == 200.0
        assert data["amount_changed"] == 100.0

    @patch("src.routes.credits.get_supabase_client")
    def test_add_credits_user_not_found(self, mock_supabase, client, auth_headers):
        """Return 404 when user not found"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = (
            []
        )

        response = client.post(
            "/credits/add", json={"user_id": 999, "amount": 100.0}, headers=auth_headers
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_add_credits_negative_amount(self, client, auth_headers):
        """Reject negative credit amounts"""
        app.dependency_overrides[require_admin] = mock_require_admin

        response = client.post(
            "/credits/add", json={"user_id": 2, "amount": -50.0}, headers=auth_headers
        )

        assert response.status_code == 422  # Validation error


class TestCreditsAdjust:
    """Test credit adjustment endpoint"""

    @patch("src.routes.credits.get_supabase_client")
    @patch("src.routes.credits.log_credit_transaction")
    def test_adjust_credits_positive(
        self, mock_log_transaction, mock_supabase, client, auth_headers
    ):
        """Successfully add credits via adjustment"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 2, "credits": 100.0}
        ]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": 2, "credits": 150.0}
        ]
        mock_log_transaction.return_value = {"id": 1}

        response = client.post(
            "/credits/adjust",
            json={
                "user_id": 2,
                "amount": 50.0,
                "description": "Adjustment",
                "reason": "Compensation",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["new_balance"] == 150.0

    @patch("src.routes.credits.get_supabase_client")
    def test_adjust_credits_negative_prevents_overdraft(self, mock_supabase, client, auth_headers):
        """Prevent adjustment that would result in negative balance"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 2, "credits": 50.0}
        ]

        response = client.post(
            "/credits/adjust",
            json={"user_id": 2, "amount": -100.0, "reason": "Deduction"},
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert "negative balance" in response.json()["detail"].lower()


class TestCreditsBulkAdd:
    """Test bulk credit addition endpoint"""

    @patch("src.routes.credits.get_supabase_client")
    @patch("src.routes.credits.log_credit_transaction")
    def test_bulk_add_credits_success(
        self, mock_log_transaction, mock_supabase, client, auth_headers
    ):
        """Successfully add credits to multiple users with different balances"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        # Mock batch user lookup with different users (using .in_() query)
        mock_client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
            {"id": 2, "credits": 100.0, "username": "user1"},
            {"id": 3, "credits": 250.0, "username": "user2"},
        ]
        # Mock update for each user
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": 2, "credits": 200.0}
        ]
        mock_log_transaction.return_value = {"id": 1}

        response = client.post(
            "/credits/bulk-add",
            json={"user_ids": [2, 3], "amount": 100.0, "description": "Bulk addition"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_users"] == 2
        assert data["successful"] == 2
        assert data["failed"] == 0
        assert data["amount_per_user"] == 100.0
        assert data["total_credits_added"] == 200.0
        # Verify both users are in results with correct data
        assert len(data["results"]) == 2
        user_ids_in_results = {r["user_id"] for r in data["results"]}
        assert user_ids_in_results == {2, 3}

    @patch("src.routes.credits.get_supabase_client")
    @patch("src.routes.credits.log_credit_transaction")
    def test_bulk_add_deduplicates_user_ids(
        self, mock_log_transaction, mock_supabase, client, auth_headers
    ):
        """Duplicate user IDs are deduplicated to prevent incorrect balance tracking"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        # Mock batch user lookup - only one user since duplicates are removed
        mock_client.table.return_value.select.return_value.in_.return_value.execute.return_value.data = [
            {"id": 2, "credits": 100.0, "username": "user1"},
        ]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": 2, "credits": 200.0}
        ]
        mock_log_transaction.return_value = {"id": 1}

        # Send duplicate user IDs
        response = client.post(
            "/credits/bulk-add",
            json={
                "user_ids": [2, 2, 2],  # Same user ID repeated
                "amount": 100.0,
                "description": "Bulk addition with duplicates",
            },
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        # Should only process unique users
        assert data["total_users"] == 1
        assert data["successful"] == 1
        assert data["total_credits_added"] == 100.0  # Only added once
        assert len(data["results"]) == 1

    def test_bulk_add_empty_list(self, client, auth_headers):
        """Reject empty user list"""
        app.dependency_overrides[require_admin] = mock_require_admin

        response = client.post(
            "/credits/bulk-add", json={"user_ids": [], "amount": 100.0}, headers=auth_headers
        )

        assert response.status_code == 422  # Validation error


class TestCreditsRefund:
    """Test credit refund endpoint"""

    @patch("src.routes.credits.get_supabase_client")
    @patch("src.routes.credits.log_credit_transaction")
    def test_refund_credits_success(
        self, mock_log_transaction, mock_supabase, client, auth_headers
    ):
        """Successfully refund credits to a user"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 2, "credits": 50.0}
        ]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": 2, "credits": 100.0}
        ]
        mock_log_transaction.return_value = {"id": 1}

        response = client.post(
            "/credits/refund",
            json={"user_id": 2, "amount": 50.0, "reason": "Service issue refund"},
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["new_balance"] == 100.0


class TestCreditsSummary:
    """Test credits summary endpoint"""

    @patch("src.routes.credits.get_supabase_client")
    @patch("src.routes.credits.get_transaction_summary")
    def test_get_summary_for_user(self, mock_get_summary, mock_supabase, client, auth_headers):
        """Get credit summary for specific user"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 2, "username": "testuser", "credits": 500.0}
        ]

        mock_get_summary.return_value = {
            "total_transactions": 10,
            "total_credits_added": 1000.0,
            "total_credits_used": 500.0,
            "net_change": 500.0,
        }

        response = client.get("/credits/summary?user_id=2", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["user_id"] == 2
        assert data["current_balance"] == 500.0

    @patch("src.routes.credits.get_supabase_client")
    def test_get_system_summary(self, mock_supabase, client, auth_headers):
        """Get system-wide credit summary"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_client = MagicMock()
        mock_supabase.return_value = mock_client

        # Mock users query
        mock_client.table.return_value.select.return_value.execute.return_value.data = [
            {"id": 1, "credits": 100.0},
            {"id": 2, "credits": 200.0},
        ]

        response = client.get("/credits/summary", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "system_summary" in data


class TestCreditsTransactions:
    """Test credit transactions listing endpoint"""

    @patch("src.routes.credits.get_all_transactions")
    def test_get_transactions_default(self, mock_get_transactions, client, auth_headers):
        """Get transactions with default parameters"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_get_transactions.return_value = [
            {
                "id": 1,
                "user_id": 2,
                "amount": 100.0,
                "transaction_type": "admin_credit",
                "description": "Test",
                "balance_before": 0.0,
                "balance_after": 100.0,
                "created_at": datetime.now(UTC).isoformat(),
                "payment_id": None,
                "metadata": {},
                "created_by": "admin:1",
            }
        ]

        response = client.get("/credits/transactions", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "transactions" in data
        assert len(data["transactions"]) == 1

    @patch("src.routes.credits.get_all_transactions")
    def test_get_transactions_with_filters(self, mock_get_transactions, client, auth_headers):
        """Get transactions with filters applied"""
        app.dependency_overrides[require_admin] = mock_require_admin

        mock_get_transactions.return_value = []

        response = client.get(
            "/credits/transactions?user_id=2&transaction_type=refund&limit=10", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["filters_applied"]["user_id"] == 2
        assert data["filters_applied"]["transaction_type"] == "refund"

    def test_get_transactions_invalid_direction(self, client, auth_headers):
        """Reject invalid direction filter"""
        app.dependency_overrides[require_admin] = mock_require_admin

        response = client.get("/credits/transactions?direction=invalid", headers=auth_headers)

        assert response.status_code == 400
        assert "direction" in response.json()["detail"].lower()


class TestCreditsAuthorization:
    """Test authorization for credits endpoints"""

    def test_add_credits_requires_admin(self, client):
        """Credits endpoints require admin authentication"""
        # Don't override the admin dependency
        app.dependency_overrides = {}

        response = client.post(
            "/credits/add",
            json={"user_id": 2, "amount": 100.0},
            headers={"Authorization": "Bearer regular_user_key"},
        )

        # Should fail with authentication error
        assert response.status_code in [401, 403]
