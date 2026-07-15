from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import require_admin

client = TestClient(app)
app.dependency_overrides[require_admin] = lambda: {"id": 1, "email": "admin@test.com"}


@patch("src.db.client.get_db")
def test_model_usage_analytics_is_free_filter_applies_eq(mock_get_db):
    mock_client = MagicMock()
    mock_get_db.return_value = mock_client
    query_mock = mock_client.table.return_value.select.return_value
    query_mock.eq.return_value = query_mock
    query_mock.ilike.return_value = query_mock
    query_mock.order.return_value = query_mock
    query_mock.range.return_value = query_mock
    query_mock.execute.return_value.data = []
    query_mock.execute.return_value.count = 0

    response = client.get("/admin/model-usage-analytics?is_free=true")

    assert response.status_code == 200
    query_mock.eq.assert_any_call("is_free", True)
