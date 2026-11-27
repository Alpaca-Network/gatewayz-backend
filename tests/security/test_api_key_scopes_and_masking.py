"""Key display, key-management scope and admin credential hardening.

1. A key's full value is returned once (create / rotate); every read is masked.
2. New keys are inference-only; managing keys needs a session credential
   (primary or gw_temp_ key) or the explicit key_management scope.
3. Admin routes need a session credential or an admin_api-scoped key, not
   just any key of an elevated account.
4. Keys created before this change (legacy wildcard scopes) keep inference
   access but lose key management and admin access.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import HTTPException

from src.db import api_key_lookup
from src.routes import api_keys as routes
from src.schemas import CreateApiKeyRequest, DeleteApiKeyRequest, UpdateApiKeyRequest
from src.security import deps
from src.services import key_scopes

LEGACY_SCOPES = {"read": ["*"], "write": ["*"], "admin": ["*"]}
FULL_KEY = "gw_live_" + "x" * 39 + "a1b2"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _user(**overrides):
    user = {
        "id": 5,
        "api_key": "gw_live_caller",
        "is_primary": False,
        "scope_permissions": LEGACY_SCOPES,
        "role": "user",
        "is_admin": False,
    }
    user.update(overrides)
    return user


# --------------------------------------------------------------------------
# Masking helpers
# --------------------------------------------------------------------------


class TestMasking:
    def test_mask_in_memory_key(self):
        assert api_key_lookup.mask_api_key(FULL_KEY) == "gw_live_...a1b2"
        assert api_key_lookup.mask_api_key("gw_test_abcdef9z") == "gw_test_...ef9z"
        assert api_key_lookup.mask_api_key(None) is None

    def test_mask_from_row_uses_last4_and_never_decrypts(self):
        row = {
            "id": 7,
            "api_key": None,
            "encrypted_key": "ciphertext",
            "last4": "a1b2",
            "environment_tag": "live",
        }
        with patch.object(api_key_lookup, "decrypt_api_key") as dec:
            assert api_key_lookup.masked_key_from_row(row) == "gw_live_...a1b2"
        dec.assert_not_called()

    def test_mask_from_row_with_legacy_plaintext_column(self):
        assert api_key_lookup.masked_key_from_row({"api_key": FULL_KEY}) == "gw_live_...a1b2"

    def test_mask_from_row_without_last4(self):
        assert api_key_lookup.masked_key_from_row({"id": 9, "api_key": "hash:abc"}) == "key#9"


def _db_row(**overrides):
    row = {
        "id": 22,
        "key_name": "ci",
        "api_key": None,
        "encrypted_key": "ciphertext",
        "key_version": 1,
        "last4": "a1b2",
        "environment_tag": "live",
        "scope_permissions": {"inference": ["*"]},
        "is_active": True,
        "is_primary": False,
        "requests_used": 0,
        "ip_allowlist": [],
        "domain_referrers": [],
    }
    row.update(overrides)
    return row


class TestDbReadsAreMasked:
    def _client(self, rows):
        client = MagicMock()
        query = client.table.return_value.select.return_value
        query.eq.return_value = query
        query.order.return_value = query
        query.execute.return_value = SimpleNamespace(data=rows)
        return client

    def test_list_returns_masked_keys(self):
        from src.db import api_keys as db

        client = self._client([_db_row(), _db_row(id=23, api_key=FULL_KEY, last4=None)])
        with (
            patch.object(db, "get_supabase_client", return_value=client),
            patch.object(api_key_lookup, "decrypt_api_key", return_value=FULL_KEY) as dec,
        ):
            keys = db.get_user_api_keys(5)
        assert [k["api_key"] for k in keys] == ["gw_live_...a1b2", "gw_live_...a1b2"]
        assert all(FULL_KEY not in str(k) for k in keys)
        dec.assert_not_called()

    def test_get_by_id_is_masked(self):
        from src.db import api_keys as db

        with (
            patch.object(db, "get_supabase_client", return_value=self._client([_db_row()])),
            patch.object(api_key_lookup, "decrypt_api_key", return_value=FULL_KEY),
        ):
            key = db.get_api_key_by_id(22, 5)
        assert key["api_key"] == "gw_live_...a1b2"
        assert FULL_KEY not in str(key)

    def test_secret_by_id_is_internal_and_decrypts(self):
        from src.db import api_keys as db

        with (
            patch.object(db, "get_supabase_client", return_value=self._client([_db_row()])),
            patch.object(api_key_lookup, "decrypt_api_key", return_value=FULL_KEY),
        ):
            assert db.get_api_key_secret_by_id(22, 5) == FULL_KEY

    def test_new_key_default_scope_is_inference_only(self):
        from src.db import api_keys as db

        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value = SimpleNamespace(
            data=[{"id": 1}]
        )
        with (
            patch.object(db, "get_supabase_client", return_value=client),
            patch.object(db, "check_key_name_uniqueness", return_value=True),
            patch.object(db, "check_plan_entitlements", return_value={}),
            patch.object(
                db,
                "new_key_storage_fields",
                return_value={"api_key": None, "key_hash": "h", "last4": "zzzz"},
            ),
            patch.object(db, "_insert_api_key_row") as insert,
        ):
            insert.return_value = SimpleNamespace(data=[{"id": 1}])
            db.create_api_key(user_id=5, key_name="k")
        payload = insert.call_args.args[1]
        assert payload["scope_permissions"] == {"inference": ["*"]}


# --------------------------------------------------------------------------
# Scope rules
# --------------------------------------------------------------------------


class TestScopeRules:
    def test_legacy_wildcards_do_not_grant_privileged_scopes(self):
        assert not key_scopes.has_scope(LEGACY_SCOPES, key_scopes.SCOPE_KEY_MANAGEMENT)
        assert not key_scopes.has_scope(LEGACY_SCOPES, key_scopes.SCOPE_ADMIN)
        assert not key_scopes.has_scope({}, key_scopes.SCOPE_KEY_MANAGEMENT)
        assert not key_scopes.has_scope(None, key_scopes.SCOPE_ADMIN)

    def test_explicit_scope(self):
        assert key_scopes.has_scope({"key_management": ["*"]}, "key_management")
        assert not key_scopes.has_scope({"key_management": []}, "key_management")

    @pytest.mark.parametrize(
        "api_key,user,expected",
        [
            ("gw_temp_session", _user(), True),
            ("gw_live_primary", _user(is_primary=True), True),
            ("gw_live_scoped", _user(scope_permissions={"key_management": ["*"]}), True),
            ("gw_live_legacy", _user(), False),
            ("gw_live_new", _user(scope_permissions={"inference": ["*"]}), False),
            ("gw_live_job", _user(scope_permissions={"read": ["inference"]}), False),
        ],
    )
    def test_can_manage_keys(self, api_key, user, expected):
        assert key_scopes.can_manage_keys(api_key, user) is expected


# --------------------------------------------------------------------------
# /user/api-keys routes
# --------------------------------------------------------------------------


@pytest.fixture
def route_env():
    create = Mock(return_value=(FULL_KEY, 22))
    with (
        patch.object(
            routes, "check_auth_rate_limit", AsyncMock(return_value=SimpleNamespace(allowed=True))
        ),
        patch.object(routes, "create_api_key", create),
        patch("src.services.payment_gate.check_live_key_allowed", return_value=(True, None)),
    ):
        yield create


def _create(api_key, user, **body):
    req = CreateApiKeyRequest(key_name="ci", environment_tag="live", **body)
    with patch.object(routes, "get_user", return_value=user):
        return _run(routes.create_user_api_key(req, api_key=api_key))


class TestKeyManagementGate:
    def test_ordinary_key_cannot_create(self, route_env):
        with pytest.raises(HTTPException) as exc:
            _create("gw_live_legacy", _user())
        assert exc.value.status_code == 403
        assert exc.value.detail["error"]["code"] == "key_management_scope_required"
        route_env.assert_not_called()

    def test_ordinary_key_cannot_list(self):
        with (
            patch.object(routes, "get_user", return_value=_user()),
            patch.object(routes, "get_user_api_keys") as listing,
        ):
            with pytest.raises(HTTPException) as exc:
                _run(routes.list_user_api_keys(api_key="gw_live_legacy"))
        assert exc.value.status_code == 403
        listing.assert_not_called()

    def test_ordinary_key_cannot_delete(self):
        with (
            patch.object(routes, "get_user", return_value=_user()),
            patch.object(routes, "delete_api_key") as delete,
        ):
            with pytest.raises(HTTPException) as exc:
                _run(
                    routes.delete_user_api_key(
                        22, DeleteApiKeyRequest(confirmation="DELETE_KEY"), api_key="gw_live_x"
                    )
                )
        assert exc.value.status_code == 403
        delete.assert_not_called()

    def test_ordinary_key_cannot_rotate_or_change_scopes(self):
        for body in ({"action": "rotate"}, {"scope_permissions": {"inference": ["*"]}}):
            with patch.object(routes, "get_user", return_value=_user()):
                with pytest.raises(HTTPException) as exc:
                    _run(
                        routes.update_user_api_key_endpoint(
                            22, UpdateApiKeyRequest(**body), api_key="gw_live_x"
                        )
                    )
            assert exc.value.status_code == 403

    def test_session_key_creates_inference_only_key_and_sees_it_once(self, route_env):
        out = _create("gw_live_primary", _user(is_primary=True))
        assert out["api_key"] == FULL_KEY
        # No scopes requested -> create_api_key applies the inference-only default.
        assert route_env.call_args.kwargs["scope_permissions"] is None

    def test_temp_session_key_can_create(self, route_env):
        assert _create("gw_temp_abc", _user())["api_key"] == FULL_KEY

    def test_key_management_scope_can_create_inference_key(self, route_env):
        user = _user(scope_permissions={"key_management": ["*"]})
        assert _create("gw_live_mgr", user)["api_key"] == FULL_KEY

    def test_key_management_key_cannot_grant_privileged_scopes(self, route_env):
        user = _user(scope_permissions={"key_management": ["*"]})
        with pytest.raises(HTTPException) as exc:
            _create("gw_live_mgr", user, scope_permissions={"key_management": ["*"]})
        assert exc.value.detail["error"]["code"] == "session_required_to_grant_scope"
        route_env.assert_not_called()

    def test_admin_scope_needs_elevated_account(self, route_env):
        with pytest.raises(HTTPException) as exc:
            _create("gw_live_p", _user(is_primary=True), scope_permissions={"admin_api": ["*"]})
        assert exc.value.detail["error"]["code"] == "admin_scope_not_allowed"
        out = _create(
            "gw_live_p",
            _user(is_primary=True, role="admin"),
            scope_permissions={"admin_api": ["*"]},
        )
        assert out["status"] == "success"

    def test_scoped_key_cannot_delete_primary_key(self):
        user = _user(scope_permissions={"key_management": ["*"]})
        with (
            patch.object(routes, "get_user", return_value=user),
            patch.object(routes, "get_api_key_by_id", return_value={"id": 1, "is_primary": True}),
            patch.object(routes, "delete_api_key") as delete,
        ):
            with pytest.raises(HTTPException) as exc:
                _run(
                    routes.delete_user_api_key(
                        1, DeleteApiKeyRequest(confirmation="DELETE_KEY"), api_key="gw_live_m"
                    )
                )
        assert exc.value.detail["error"]["code"] == "session_required_for_primary_key"
        delete.assert_not_called()

    def test_delete_uses_internal_secret_not_response_value(self):
        with (
            patch.object(routes, "get_user", return_value=_user(is_primary=True)),
            patch.object(
                routes,
                "get_api_key_by_id",
                return_value={"id": 22, "is_primary": False, "api_key": "gw_live_...a1b2"},
            ),
            patch.object(routes, "get_api_key_secret_by_id", return_value=FULL_KEY),
            patch.object(routes, "delete_api_key", return_value=True) as delete,
        ):
            out = _run(
                routes.delete_user_api_key(
                    22, DeleteApiKeyRequest(confirmation="DELETE_KEY"), api_key="gw_live_p"
                )
            )
        assert out["status"] == "success"
        assert delete.call_args.args[0] == FULL_KEY


def _key_dict(**overrides):
    key = {
        "id": 22,
        "key_name": "ci",
        "api_key": "gw_live_...a1b2",
        "last4": "a1b2",
        "environment_tag": "live",
        "scope_permissions": {"inference": ["*"]},
        "is_active": True,
        "is_primary": False,
        "requests_used": 0,
        "ip_allowlist": [],
        "domain_referrers": [],
    }
    key.update(overrides)
    return key


class TestRotate:
    def test_rotate_returns_new_key_once_and_masked_details(self):
        new_key = "gw_live_" + "n" * 39 + "9z9z"
        with (
            patch.object(routes, "get_user", return_value=_user(is_primary=True)),
            patch.object(routes, "get_api_key_by_id", return_value=_key_dict()),
            patch("src.db_security.rotate_api_key", return_value=new_key) as rotate,
        ):
            out = _run(
                routes.update_user_api_key_endpoint(
                    22, UpdateApiKeyRequest(action="rotate"), api_key="gw_live_p"
                )
            )
        rotate.assert_called_once_with(22, 5)
        assert out.new_api_key == new_key
        assert out.updated_key.api_key == "gw_live_...a1b2"

    def test_regular_update_has_no_new_key(self):
        with (
            patch.object(routes, "get_user", return_value=_user(is_primary=True)),
            patch.object(routes, "get_api_key_by_id", return_value=_key_dict()),
            patch.object(routes, "get_api_key_secret_by_id", return_value=FULL_KEY),
            patch.object(routes, "update_api_key", return_value=True) as update,
        ):
            out = _run(
                routes.update_user_api_key_endpoint(
                    22, UpdateApiKeyRequest(key_name="renamed"), api_key="gw_live_p"
                )
            )
        assert out.new_api_key is None
        assert update.call_args.args[0] == FULL_KEY
        assert FULL_KEY not in out.model_dump_json()


class TestRateLimitListIsMasked:
    def test_rate_limit_configs_response_masks_keys(self):
        from src.routes import rate_limits

        stats = {"total_requests": 0, "total_tokens": 0}
        with (
            patch.object(rate_limits, "get_user", return_value=_user(is_primary=True)),
            patch.object(
                rate_limits,
                "get_user_rate_limit_configs",
                return_value=[{"api_key": FULL_KEY, "key_name": "ci", "rate_limit_config": {}}],
            ),
            patch.object(rate_limits, "get_rate_limit_usage_stats", return_value=stats),
        ):
            out = rate_limits._fetch_user_rate_limits_sync("gw_live_p")
        assert out["configs"][0]["api_key"] == "gw_live_...a1b2"
        assert FULL_KEY not in str(out["configs"])


# --------------------------------------------------------------------------
# Admin credential
# --------------------------------------------------------------------------


def _admin(**overrides):
    return _user(role="admin", is_admin=True, **overrides)


class TestAdminCredential:
    def test_inference_key_of_admin_account_is_refused(self):
        with pytest.raises(HTTPException) as exc:
            _run(deps.require_admin(user=_admin()))
        assert exc.value.status_code == 403
        assert exc.value.detail["error"]["code"] == "admin_credential_required"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"is_primary": True},
            {"api_key": "gw_temp_session"},
            {"scope_permissions": {"admin_api": ["*"]}},
        ],
    )
    def test_session_or_admin_scoped_key_is_accepted(self, overrides):
        user = _admin(**overrides)
        assert _run(deps.require_admin(user=user)) is user

    def test_non_admin_account_with_admin_scope_is_still_refused(self):
        with pytest.raises(HTTPException) as exc:
            _run(deps.require_admin(user=_user(scope_permissions={"admin_api": ["*"]})))
        assert exc.value.detail == "Administrator privileges required"

    def test_superadmin_needs_admin_credential(self):
        with pytest.raises(HTTPException):
            _run(deps.require_superadmin(user=_user(role="superadmin")))
        user = _user(role="superadmin", is_primary=True)
        assert _run(deps.require_superadmin(user=user)) is user

    def test_admin_or_env_key_refuses_ordinary_admin_key(self):
        creds = SimpleNamespace(credentials="gw_live_inference")
        with (
            patch.dict("os.environ", {"ADMIN_API_KEY": "env-admin-key-placeholder"}),
            patch.object(deps, "get_api_key", AsyncMock(return_value="gw_live_inference")),
            patch.object(deps, "get_current_user", AsyncMock(return_value=_admin())),
        ):
            with pytest.raises(HTTPException) as exc:
                _run(deps.require_admin_or_env_key(credentials=creds))
        assert exc.value.status_code == 403

    def test_admin_or_env_key_still_accepts_env_key(self):
        creds = SimpleNamespace(credentials="env-admin-key-placeholder")
        with patch.dict("os.environ", {"ADMIN_API_KEY": "env-admin-key-placeholder"}):
            out = _run(deps.require_admin_or_env_key(credentials=creds))
        assert out["auth"] == "env_key"

    def test_ip_whitelist_admin_dependency(self):
        from src.routes import ip_whitelist

        with pytest.raises(HTTPException):
            ip_whitelist.require_admin(current_user=_admin())
        assert ip_whitelist.require_admin(current_user=_admin(is_primary=True))

    def test_rate_limit_clamp_exemption_needs_admin_credential(self):
        from src.routes import rate_limits

        huge = {"requests_per_minute": 10**9}
        assert rate_limits._clamp_rate_limit_config(huge, _admin(is_primary=True)) == huge
        clamped = rate_limits._clamp_rate_limit_config(huge, _admin())
        assert clamped["requests_per_minute"] < 10**9

    def test_refund_route_needs_admin_credential(self):
        from src.routes import payments

        with patch.object(payments, "stripe_service") as stripe:
            with pytest.raises(HTTPException) as exc:
                _run(payments.create_refund(MagicMock(), current_user=_admin()))
        assert exc.value.status_code == 403
        stripe.create_refund.assert_not_called()
