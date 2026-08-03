from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


@patch("src.db.client.get_db")
def test_chat_requests_status_filter_applies_eq(mock_get_db):
    mock_client = MagicMock()
    mock_get_db.return_value = mock_client
    query_mock = mock_client.table.return_value.select.return_value
    query_mock.eq.return_value = query_mock
    query_mock.ilike.return_value = query_mock
    query_mock.gte.return_value = query_mock
    query_mock.lte.return_value = query_mock
    query_mock.order.return_value = query_mock
    query_mock.range.return_value = query_mock
    query_mock.execute.return_value.data = []
    mock_client.table.return_value.select.return_value.execute.return_value.count = 0

    response = client.get("/api/monitoring/chat-requests?status=failed")

    assert response.status_code == 200
    query_mock.eq.assert_any_call("status", "failed")
