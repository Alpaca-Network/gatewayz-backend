"""Tests for src.routes.gpu_earnings (gatewayz-backend#2265, #2266)."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import get_user_id

client = TestClient(app)
app.dependency_overrides[get_user_id] = lambda: 42


@patch("src.routes.gpu_earnings.list_settlements_for_provider")
@patch("src.routes.gpu_earnings.list_recent_work_for_provider")
@patch("src.routes.gpu_earnings.earnings_totals")
@patch("src.routes.gpu_earnings.get_provider_for_user")
def test_earnings_returns_totals_work_and_settlements(
    mock_get_provider, mock_totals, mock_work, mock_settlements
):
    mock_get_provider.return_value = {"id": 5, "user_id": 42}
    mock_totals.return_value = {"accrued": 1000, "settled": 2000, "void": 300}
    mock_work.return_value = [
        {
            "billing_ref": "br-1",
            "model": "community/llama-3.1-8b-instruct",
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "verification": "verified",
            "created_at": "2026-09-01T00:00:00Z",
            "prompt_hash": "should-not-appear",
        }
    ]
    mock_settlements.return_value = [
        {
            "id": 1,
            "period_start": "2026-09-01T00:00:00Z",
            "period_end": "2026-09-02T00:00:00Z",
            "amount_wei": "2000",
            "status": "sent",
            "tx_hash": "0xabc123",
            "error": None,
            "created_at": "2026-09-02T00:00:00Z",
        }
    ]

    response = client.get("/gpu/providers/me/earnings")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["totals"] == {"accrued_wei": "1000", "settled_wei": "2000", "void_wei": "300"}
    assert data["work"][0]["billing_ref"] == "br-1"
    assert "prompt_hash" not in data["work"][0]
    assert data["settlements"][0]["tx_url"] == "https://testnet.snowtrace.io/tx/0xabc123"
    mock_get_provider.assert_called_once_with(42)
    mock_totals.assert_called_once_with(5)
    mock_work.assert_called_once_with(5, limit=50)
    mock_settlements.assert_called_once_with(5)


@patch("src.routes.gpu_earnings.get_provider_for_user")
def test_earnings_returns_404_when_caller_has_no_provider(mock_get_provider):
    mock_get_provider.return_value = None
    response = client.get("/gpu/providers/me/earnings")
    assert response.status_code == 404


@patch("src.routes.gpu_earnings.list_settlements_for_provider")
@patch("src.routes.gpu_earnings.list_recent_work_for_provider")
@patch("src.routes.gpu_earnings.earnings_totals")
@patch("src.routes.gpu_earnings.get_provider_for_user")
def test_earnings_settlement_without_tx_hash_has_no_tx_url(
    mock_get_provider, mock_totals, mock_work, mock_settlements
):
    mock_get_provider.return_value = {"id": 5, "user_id": 42}
    mock_totals.return_value = {"accrued": 0, "settled": 0, "void": 0}
    mock_work.return_value = []
    mock_settlements.return_value = [
        {
            "id": 2,
            "period_start": "2026-09-01T00:00:00Z",
            "period_end": "2026-09-02T00:00:00Z",
            "amount_wei": "500",
            "status": "pending",
            "tx_hash": None,
            "error": None,
            "created_at": "2026-09-02T00:00:00Z",
        }
    ]

    response = client.get("/gpu/providers/me/earnings")

    assert response.status_code == 200
    assert response.json()["data"]["settlements"][0]["tx_url"] is None


def test_earnings_requires_auth():
    app.dependency_overrides.pop(get_user_id, None)
    try:
        response = client.get("/gpu/providers/me/earnings")
        assert response.status_code == 401
    finally:
        app.dependency_overrides[get_user_id] = lambda: 42


@patch("src.routes.gpu_earnings.list_settlements_for_provider")
@patch("src.routes.gpu_earnings.list_recent_work_for_provider")
@patch("src.routes.gpu_earnings.earnings_totals")
@patch("src.routes.gpu_earnings.get_provider_for_user")
def test_earnings_scopes_strictly_to_the_caller_via_user_id(
    mock_get_provider, mock_totals, mock_work, mock_settlements
):
    """Regression-style guard: the route must always resolve the provider
    via get_provider_for_user(caller's user_id) -- never accept a
    provider_id from the request -- so there's no IDOR path to another
    user's earnings/settlements."""
    mock_get_provider.return_value = {"id": 999, "user_id": 42}
    mock_totals.return_value = {"accrued": 0, "settled": 0, "void": 0}
    mock_work.return_value = []
    mock_settlements.return_value = []

    response = client.get("/gpu/providers/me/earnings?provider_id=1")

    assert response.status_code == 200
    mock_get_provider.assert_called_once_with(42)
    mock_totals.assert_called_once_with(999)
