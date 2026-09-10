"""Tests for GET /admin/status (src/routes/admin_status.py, Phase A, A4)."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import src.routes.admin_status as admin_status
import src.services.integrations_health as integrations_health
from src.main import app
from src.security.deps import require_admin_or_env_key

client = TestClient(app)


@pytest.fixture(autouse=True)
def _no_real_backend_calls():
    """The integrations block's Supabase and Redis checks would otherwise
    reach out for real on every test in this file: Supabase to the fake
    placeholder URL tests/conftest.py sets by default, and Redis to
    whatever REDIS_URL/localhost resolves to, both slow and pointless in a
    unit test. Fail both fast and deterministically instead."""
    with (
        patch(
            "src.config.supabase_config.get_supabase_client",
            side_effect=RuntimeError("no database in this test"),
        ),
        patch(
            "src.config.redis_config.get_redis_client",
            side_effect=RuntimeError("no redis in this test"),
        ),
    ):
        yield


def _reset_integrations_cache():
    """The integrations_health module caches check_all() results for 30s
    in-process. Reset between tests so one test's env/mocks can't leak
    into another's assertions via a stale cache entry."""
    integrations_health._cache = None
    integrations_health._cache_computed_at = 0.0


def _override_admin():
    app.dependency_overrides[require_admin_or_env_key] = lambda: {
        "role": "admin",
        "auth": "env_key",
        "is_admin": True,
    }


def _clear_override():
    app.dependency_overrides.pop(require_admin_or_env_key, None)


class TestAuth:
    def test_401_or_403_without_credentials(self):
        response = client.get("/admin/status")
        assert response.status_code in (401, 403)

    def test_200_with_env_admin_key(self, monkeypatch):
        monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key-for-status")
        response = client.get(
            "/admin/status",
            headers={"Authorization": "Bearer test-admin-key-for-status"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert "data" in body


class TestResponseShape:
    def setup_method(self, _method):
        _override_admin()
        _reset_integrations_cache()

    def teardown_method(self, _method):
        _clear_override()
        _reset_integrations_cache()

    def test_envelope_and_top_level_keys(self):
        response = client.get("/admin/status")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        for key in ("generated_at", "jobs", "integrations", "secrets", "wayz"):
            assert key in data

    def test_wayz_block_matches_admin_wayz_shape_minus_jobs(self):
        response = client.get("/admin/status")
        wayz = response.json()["data"]["wayz"]
        for key in ("config", "staking", "faucet", "wallets", "gpu", "pending_approvals"):
            assert key in wayz
        assert "jobs" not in wayz

    def test_jobs_block_lists_every_wrapped_job(self):
        response = client.get("/admin/status")
        jobs = response.json()["data"]["jobs"]
        for name in (
            "model_sync",
            "price_refresh",
            "ledger_reconciliation",
            "retention_cleanup",
            "wayz_staking_sync",
            "gpu_spot_check",
            "gpu_settlement",
            "pricing_drift",
            "gpu_liveness_sweep",
            "gpu_rollup",
        ):
            assert name in jobs

    def test_integrations_block_lists_every_integration(self):
        response = client.get("/admin/status")
        integrations = response.json()["data"]["integrations"]
        for name in ("resend", "privy", "fuji_rpc", "supabase", "redis", "stripe", "sentry"):
            assert name in integrations
            assert "status" in integrations[name]

    def test_secrets_block_lists_the_fixed_allowlist(self):
        response = client.get("/admin/status")
        secrets = response.json()["data"]["secrets"]
        for name in (
            "ADMIN_API_KEY",
            "SUPABASE_SERVICE_ROLE_KEY",
            "RESEND_API_KEY",
            "PRIVY_APP_ID",
            "PRIVY_VERIFICATION_KEY",
            "WAYZ_FAUCET_MINTER_PRIVATE_KEY",
            "WAYZ_REWARDS_POOL_PRIVATE_KEY",
            "STRIPE_SECRET_KEY",
            "SENTRY_DSN",
        ):
            assert name in secrets
            assert set(secrets[name].keys()) == {"present", "source"}
            assert secrets[name]["source"] == "env"
            assert isinstance(secrets[name]["present"], bool)


class TestSecretsNeverLeakValues:
    def setup_method(self, _method):
        _override_admin()
        _reset_integrations_cache()

    def teardown_method(self, _method):
        _clear_override()
        _reset_integrations_cache()

    def test_no_secret_value_appears_anywhere_in_response_body(self, monkeypatch):
        fake_values = {
            "ADMIN_API_KEY": "super-secret-admin-value-zzz1",
            "SUPABASE_SERVICE_ROLE_KEY": "super-secret-service-role-zzz2",
            "RESEND_API_KEY": "super-secret-resend-zzz3",
            "PRIVY_APP_ID": "super-secret-privy-app-zzz4",
            "PRIVY_VERIFICATION_KEY": "super-secret-privy-key-zzz5",
            "WAYZ_FAUCET_MINTER_PRIVATE_KEY": "super-secret-faucet-zzz6",
            "WAYZ_REWARDS_POOL_PRIVATE_KEY": "super-secret-rewards-zzz7",
            "STRIPE_SECRET_KEY": "super-secret-stripe-zzz8",
            "SENTRY_DSN": "super-secret-sentry-zzz9",
        }
        for name, value in fake_values.items():
            monkeypatch.setenv(name, value)

        # RESEND_API_KEY being present makes _check_resend call out to
        # Resend for real -- mock that call rather than hitting the live
        # API with a fake token.
        with patch("httpx.get", return_value=MagicMock(status_code=401)):
            response = client.get("/admin/status")
        assert response.status_code == 200
        body_text = json.dumps(response.json())

        for value in fake_values.values():
            assert value not in body_text

        secrets = response.json()["data"]["secrets"]
        for name in fake_values:
            assert secrets[name]["present"] is True


class TestSubBlockDegradation:
    def setup_method(self, _method):
        _override_admin()
        _reset_integrations_cache()

    def teardown_method(self, _method):
        _clear_override()
        _reset_integrations_cache()

    def test_jobs_block_degrades_independently_and_never_500s(self):
        with patch("src.routes.admin_wayz.get_job_runs", side_effect=RuntimeError("boom")):
            response = client.get("/admin/status")
        assert response.status_code == 200
        assert response.json()["data"]["jobs"] == {"error": "RuntimeError"}
        assert "error" not in response.json()["data"]["secrets"]

    def test_integrations_block_degrades_independently(self):
        with patch.object(
            admin_status, "check_all", side_effect=RuntimeError("integrations broke")
        ):
            response = client.get("/admin/status")
        assert response.status_code == 200
        assert response.json()["data"]["integrations"] == {"error": "RuntimeError"}
        # Everything else is still populated.
        assert "error" not in response.json()["data"]["jobs"]

    def test_wayz_staking_sub_block_degrades_without_breaking_status(self):
        with patch(
            "src.routes.admin_wayz.get_stake_totals",
            side_effect=RuntimeError("db exploded"),
        ):
            response = client.get("/admin/status")
        assert response.status_code == 200
        wayz = response.json()["data"]["wayz"]
        assert wayz["staking"] == {"error": "RuntimeError"}
        assert "error" not in wayz["config"]
