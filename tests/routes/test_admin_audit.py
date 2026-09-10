"""Tests for GET /admin/audit (src/routes/admin_audit.py)."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import require_admin

client = TestClient(app)


def _override_admin(role="admin"):
    app.dependency_overrides[require_admin] = lambda: {
        "id": 1,
        "role": role,
        "is_admin": True,
    }


def _clear_override():
    app.dependency_overrides.pop(require_admin, None)


class TestAuth:
    def test_401_or_403_without_credentials(self):
        response = client.get("/admin/audit")
        assert response.status_code in (401, 403)


class TestGetAuditLog:
    def setup_method(self, _method):
        _override_admin()

    def teardown_method(self, _method):
        _clear_override()

    def test_returns_envelope(self):
        rows = [{"id": 1, "action": "staff.role_changed"}]
        with patch("src.routes.admin_audit.list_audit", return_value=rows) as mock_list:
            response = client.get("/admin/audit")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["entries"] == rows
        mock_list.assert_called_once_with(limit=100, action=None, actor_user_id=None, before=None)

    def test_passes_query_params_through(self):
        with patch("src.routes.admin_audit.list_audit", return_value=[]) as mock_list:
            response = client.get(
                "/admin/audit",
                params={"limit": 5, "action": "staff.role_changed", "actor_user_id": 9},
            )
        assert response.status_code == 200
        mock_list.assert_called_once_with(
            limit=5, action="staff.role_changed", actor_user_id=9, before=None
        )

    def test_superadmin_can_also_read(self):
        _clear_override()
        _override_admin(role="superadmin")
        with patch("src.routes.admin_audit.list_audit", return_value=[]):
            response = client.get("/admin/audit")
        assert response.status_code == 200

    def test_rejects_limit_over_500(self):
        response = client.get("/admin/audit", params={"limit": 501})
        assert response.status_code == 422
