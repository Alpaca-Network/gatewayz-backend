"""Tests for src/routes/admin_staff.py (Phase A2 staff management API)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.main import app
from src.security.deps import get_current_user, require_admin, require_superadmin

client = TestClient(app)

SUPERADMIN = {"id": 1, "email": "root@example.com", "role": "superadmin"}
ADMIN = {"id": 2, "email": "admin@example.com", "role": "admin"}


@pytest.fixture(autouse=True)
def _isolate_dependency_overrides():
    """Snapshot and restore the FULL app.dependency_overrides dict around
    every test in this module. Popping only the keys this file touches is
    not enough under xdist: another module's test (e.g. one that sets an
    override at import time and never restores it) can be interleaved with
    these, and restoring the exact prior dict -- whatever it was -- is what
    actually makes this file's tests independent of run order."""
    saved = dict(app.dependency_overrides)
    yield
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


@pytest.fixture
def superadmin_override():
    """Override require_superadmin for one test. Returns a setter so a test
    can supply a different actor dict."""

    def _set(user=None):
        app.dependency_overrides[require_superadmin] = lambda: user or SUPERADMIN

    _set()
    return _set


@pytest.fixture
def admin_override():
    """Override require_admin for one test. Returns a setter so a test can
    supply a different actor dict."""

    def _set(user=None):
        app.dependency_overrides[require_admin] = lambda: user or ADMIN

    _set()
    return _set


@pytest.fixture
def current_user_override():
    """Override get_current_user for one test. Returns a setter -- no
    default actor, since the caller's identity is what each accept-invite
    test is specifically about."""

    def _set(user):
        app.dependency_overrides[get_current_user] = lambda: user

    return _set


class TestAuth:
    def test_get_staff_requires_admin(self):
        response = client.get("/admin/staff")
        assert response.status_code in (401, 403)

    def test_invite_requires_superadmin(self):
        response = client.post("/admin/staff/invite", json={"email": "a@x.com", "role": "admin"})
        assert response.status_code in (401, 403)

    def test_patch_requires_superadmin(self):
        response = client.patch("/admin/staff/5", json={"role": "admin"})
        assert response.status_code in (401, 403)


class TestGetStaff:
    def test_returns_staff_list(self, admin_override):
        rows = [{"id": 1, "email": "a@x.com", "role": "superadmin"}]
        with patch("src.routes.admin_staff.list_staff", return_value=rows):
            response = client.get("/admin/staff")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["staff"] == rows


class TestInviteStaff:
    def test_existing_user_gets_role_set_directly(self, superadmin_override):
        existing = {"id": 9, "email": "existing@x.com", "role": "user"}
        updated = {"id": 9, "email": "existing@x.com", "role": "admin"}
        with (
            patch("src.routes.admin_staff.get_user_by_email_ci", return_value=existing),
            patch("src.routes.admin_staff.set_role", return_value=updated) as mock_set_role,
            patch("src.routes.admin_staff.record_audit") as mock_audit,
        ):
            response = client.post(
                "/admin/staff/invite", json={"email": "existing@x.com", "role": "admin"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["invited"] is False
        assert body["data"]["user"] == updated
        mock_set_role.assert_called_once()
        mock_audit.assert_called_once()

    def test_new_email_creates_invite_and_returns_link_when_email_not_sent(
        self, superadmin_override
    ):
        invite_row = {
            "id": "uuid-1",
            "email": "new@x.com",
            "role": "admin",
            "expires_at": "2026-09-14T00:00:00Z",
        }
        with (
            patch("src.routes.admin_staff.get_user_by_email_ci", return_value=None),
            patch(
                "src.routes.admin_staff.create_invite",
                return_value=(invite_row, "raw-token-value"),
            ),
            patch("src.routes.admin_staff.record_audit"),
        ):
            response = client.post(
                "/admin/staff/invite", json={"email": "new@x.com", "role": "admin"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["data"]["invite"]["email"] == "new@x.com"
        # send_staff_invite doesn't exist yet (lands in a different PR) --
        # ImportError must degrade to returning the link, not failing the request.
        assert "invite_link" in body["data"]
        assert "raw-token-value" in body["data"]["invite_link"]

    def test_invalid_role_rejected(self, superadmin_override):
        response = client.post("/admin/staff/invite", json={"email": "a@x.com", "role": "user"})
        assert response.status_code == 422

    def test_invalid_email_rejected(self, superadmin_override):
        response = client.post(
            "/admin/staff/invite", json={"email": "not-an-email", "role": "admin"}
        )
        assert response.status_code == 422


class TestUpdateStaffRole:
    def test_cannot_change_own_role(self, superadmin_override):
        response = client.patch(f"/admin/staff/{SUPERADMIN['id']}", json={"role": "admin"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "cannot_change_own_role"

    def test_404_when_target_missing(self, superadmin_override):
        with patch("src.routes.admin_staff.get_user_by_id", return_value=None):
            response = client.patch("/admin/staff/999", json={"role": "admin"})
        assert response.status_code == 404

    def test_cannot_demote_last_superadmin(self, superadmin_override):
        target = {"id": 5, "role": "superadmin"}
        with (
            patch("src.routes.admin_staff.get_user_by_id", return_value=target),
            patch("src.routes.admin_staff.count_active_superadmins", return_value=1),
        ):
            response = client.patch("/admin/staff/5", json={"role": "admin"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "last_superadmin"

    def test_successful_role_change(self, superadmin_override):
        target = {"id": 5, "role": "admin"}
        updated = {"id": 5, "role": "superadmin"}
        with (
            patch("src.routes.admin_staff.get_user_by_id", return_value=target),
            patch("src.routes.admin_staff.set_role", return_value=updated),
            patch("src.routes.admin_staff.record_audit") as mock_audit,
        ):
            response = client.patch("/admin/staff/5", json={"role": "superadmin"})
        assert response.status_code == 200
        assert response.json()["data"]["user"] == updated
        mock_audit.assert_called_once()


class TestRemoveStaff:
    def test_cannot_remove_self(self, superadmin_override):
        response = client.delete(f"/admin/staff/{SUPERADMIN['id']}")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "cannot_change_own_role"

    def test_cannot_remove_last_superadmin(self, superadmin_override):
        target = {"id": 5, "role": "superadmin"}
        with (
            patch("src.routes.admin_staff.get_user_by_id", return_value=target),
            patch("src.routes.admin_staff.count_active_superadmins", return_value=1),
        ):
            response = client.delete("/admin/staff/5")
        assert response.status_code == 409

    def test_removes_staff_sets_role_user(self, superadmin_override):
        target = {"id": 5, "role": "admin"}
        updated = {"id": 5, "role": "user"}
        with (
            patch("src.routes.admin_staff.get_user_by_id", return_value=target),
            patch("src.routes.admin_staff.set_role", return_value=updated) as mock_set_role,
            patch("src.routes.admin_staff.record_audit"),
        ):
            response = client.delete("/admin/staff/5")
        assert response.status_code == 200
        mock_set_role.assert_called_once_with(5, "user", actor=SUPERADMIN)


class TestRevokeStaffKeys:
    def test_404_when_target_missing(self, superadmin_override):
        with patch("src.routes.admin_staff.get_user_by_id", return_value=None):
            response = client.post("/admin/staff/999/revoke-keys")
        assert response.status_code == 404

    def test_revokes_keys(self, superadmin_override):
        target = {"id": 5, "role": "admin"}
        with (
            patch("src.routes.admin_staff.get_user_by_id", return_value=target),
            patch("src.routes.admin_staff.revoke_user_keys", return_value=3),
            patch("src.routes.admin_staff.record_audit") as mock_audit,
        ):
            response = client.post("/admin/staff/5/revoke-keys")
        assert response.status_code == 200
        assert response.json()["data"]["revoked_count"] == 3
        mock_audit.assert_called_once()


class TestAcceptInvite:
    def test_requires_authentication(self):
        response = client.post("/auth/accept-invite", json={"token": "abc"})
        assert response.status_code in (401, 403, 404)

    def test_success(self, current_user_override):
        user = {"id": 5, "email": "a@x.com"}
        updated = {"id": 5, "role": "admin"}
        current_user_override(user)
        with (
            patch("src.routes.admin_staff.accept_invite", return_value=updated) as mock_accept,
            patch("src.routes.admin_staff.record_audit"),
        ):
            response = client.post("/auth/accept-invite", json={"token": "raw-token"})
        assert response.status_code == 200
        assert response.json()["data"]["user"] == updated
        mock_accept.assert_called_once_with("raw-token", 5, "a@x.com")

    def test_invalid_token_returns_422(self, current_user_override):
        user = {"id": 5, "email": "a@x.com"}
        current_user_override(user)
        with patch("src.routes.admin_staff.accept_invite", return_value=None):
            response = client.post("/auth/accept-invite", json={"token": "bad-token"})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invite_not_claimable"
