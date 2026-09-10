from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import require_admin

client = TestClient(app)


@pytest.fixture(autouse=True)
def _admin_override():
    """Override require_admin for the duration of each test in this module,
    then restore whatever app.dependency_overrides held before.

    This used to be a bare module-level `app.dependency_overrides[...] = ...`
    executed once at import time and never undone -- under pytest-xdist,
    whichever worker imported this module leaked a fake admin into every
    other test in that worker's process for the rest of the run (e.g.
    tests/routes/test_admin_audit.py::test_401_or_403_without_credentials
    got 200 instead of 401/403, order-dependent on which worker imported
    which file first). Snapshotting and restoring the full dict around each
    test -- not just this key -- makes this file's tests order-independent
    with respect to whatever any other module's tests are doing.
    """
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[require_admin] = lambda: {"id": 1, "email": "admin@test.com"}
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


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
