from unittest.mock import MagicMock, patch

import pytest

from src.db.chat_completion_requests import save_chat_completion_request_with_cost


@pytest.fixture
def sb(monkeypatch):
    """In-memory Supabase stub to prevent database connection"""
    return MagicMock()


@patch("src.db.chat_completion_requests.get_model_id_by_name", return_value=None)
@patch("src.db.chat_completion_requests.get_supabase_client")
def test_failed_request_persisted_even_when_model_unresolved(
    mock_get_client, mock_get_model_id, sb
):
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_client.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": 1, "request_id": "req-123", "model_id": None, "status": "failed"}
    ]

    result = save_chat_completion_request_with_cost(
        request_id="req-123",
        model_name="some/unresolvable-model",
        input_tokens=10,
        output_tokens=0,
        processing_time_ms=50,
        cost_usd=0.0,
        input_cost_usd=0.0,
        output_cost_usd=0.0,
        pricing_source="error",
        status="failed",
        error_message="401 User not found",
        provider_name="openrouter",
    )

    assert result is not None
    assert result["request_id"] == "req-123"
    inserted_payload = mock_client.table.return_value.insert.call_args[0][0]
    assert inserted_payload["model_id"] is None
    assert inserted_payload["metadata"]["unresolved_model_name"] == "some/unresolvable-model"
    assert inserted_payload["status"] == "failed"


@patch("src.db.chat_completion_requests.get_model_id_by_name", return_value=None)
@patch("src.db.chat_completion_requests.get_supabase_client")
def test_non_failed_request_skipped_when_model_unresolved(mock_get_client, mock_get_model_id, sb):
    """Verify that non-failed requests still return None (unchanged behavior)"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    result = save_chat_completion_request_with_cost(
        request_id="req-456",
        model_name="some/unresolvable-model",
        input_tokens=10,
        output_tokens=5,
        processing_time_ms=100,
        cost_usd=0.01,
        input_cost_usd=0.005,
        output_cost_usd=0.005,
        pricing_source="calculated",
        status="completed",
        provider_name="openrouter",
    )

    assert result is None
    mock_client.table.return_value.insert.assert_not_called()
