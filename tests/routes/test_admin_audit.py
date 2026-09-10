"""Tests for GET /admin/audit (src/routes/admin_audit.py)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import require_admin

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_dependency_overrides():
    """Snapshot and restore the FULL app.dependency_overrides dict around
    every test in this module -- not just pop the key this file happens to
    touch. Under xdist, another module's test can run interleaved with
    these (e.g. one that sets an override at import time and never
    restores it); restoring the exact prior dict, rather than clearing one
    key, is what actually makes this file's tests order-independent."""
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def admin_override():
    """Override require_admin for the duration of one test. Returns a
    setter so a test can pick the role (default 'admin')."""

    def _set(role="admin"):
        app.dependency_overrides[require_admin] = lambda: {
            "id": 1,
            "role": role,
            "is_admin": True,
        }

    _set()
    return _set


class TestAuth:
    def test_401_or_403_without_credentials(self):
        response = client.get("/admin/audit")
        assert response.status_code in (401, 403)


class TestGetAuditLog:
    def test_returns_envelope(self, admin_override):
        rows = [{"id": 1, "action": "staff.role_changed"}]
        with patch("src.routes.admin_audit.list_audit", return_value=rows) as mock_list:
            response = client.get("/admin/audit")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["entries"] == rows
        mock_list.assert_called_once_with(limit=100, action=None, actor_user_id=None, before=None)

    def test_passes_query_params_through(self, admin_override):
        with patch("src.routes.admin_audit.list_audit", return_value=[]) as mock_list:
            response = client.get(
                "/admin/audit",
                params={"limit": 5, "action": "staff.role_changed", "actor_user_id": 9},
            )
        assert response.status_code == 200
        mock_list.assert_called_once_with(
            limit=5, action="staff.role_changed", actor_user_id=9, before=None
        )

    def test_superadmin_can_also_read(self, admin_override):
        admin_override("superadmin")
        with patch("src.routes.admin_audit.list_audit", return_value=[]):
            response = client.get("/admin/audit")
        assert response.status_code == 200

    def test_rejects_limit_over_500(self, admin_override):
        response = client.get("/admin/audit", params={"limit": 501})
        assert response.status_code == 422
