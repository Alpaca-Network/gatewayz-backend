"""Tests for GET /admin/status (src/routes/admin_status.py, Phase A, A4)."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import src.routes.admin_status as admin_status
import src.services.integrations_health as integrations_health
import src.services.secrets_registry as secrets_registry
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


def _reset_secrets_registry_fallback():
    """secrets_registry's in-process fallback store is module-level state,
    shared across tests in this file same as integrations_health's cache."""
    secrets_registry._fallback_store.clear()


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
        _reset_secrets_registry_fallback()

    def teardown_method(self, _method):
        _clear_override()
        _reset_integrations_cache()
        _reset_secrets_registry_fallback()

    def test_envelope_and_top_level_keys(self):
        response = client.get("/admin/status")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        data = body["data"]
        for key in (
            "generated_at",
            "jobs",
            "integrations",
            "secrets",
            "wayz",
            "provider_budget",
        ):
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
            "holdings_snapshots",
            "holdings_rewards",
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
        for name in secrets_registry.SECRET_NAMES:
            assert name in secrets
            assert set(secrets[name].keys()) == {
                "present",
                "source",
                "first_seen_at",
                "age_days",
                "rotate_due",
                "fingerprint_known",
            }
            assert secrets[name]["source"] == "env"
            assert isinstance(secrets[name]["present"], bool)
            assert isinstance(secrets[name]["rotate_due"], bool)
            assert isinstance(secrets[name]["fingerprint_known"], bool)


class TestSecretsNeverLeakValues:
    def setup_method(self, _method):
        _override_admin()
        _reset_integrations_cache()
        _reset_secrets_registry_fallback()

    def teardown_method(self, _method):
        _clear_override()
        _reset_integrations_cache()
        _reset_secrets_registry_fallback()

    def test_no_secret_value_appears_anywhere_in_response_body(self, monkeypatch):
        fake_values = {
            "ADMIN_API_KEY": "super-secret-admin-value-zzz1",
            "SUPABASE_SERVICE_ROLE_KEY": "super-secret-service-role-zzz2",
            "SUPABASE_KEY": "super-secret-supabase-key-zzz10",
            "RESEND_API_KEY": "super-secret-resend-zzz3",
            "PRIVY_APP_ID": "super-secret-privy-app-zzz4",
            "PRIVY_VERIFICATION_KEY": "super-secret-privy-key-zzz5",
            "WAYZ_FAUCET_MINTER_PRIVATE_KEY": "super-secret-faucet-zzz6",
            "WAYZ_REWARDS_POOL_PRIVATE_KEY": "super-secret-rewards-zzz7",
            "STRIPE_SECRET_KEY": "super-secret-stripe-zzz8",
            "SENTRY_DSN": "super-secret-sentry-zzz9",
            "GATEWAYZ_AUTH_BRIDGE_SECRET": "super-secret-bridge-zzz11",
        }
        for name, value in fake_values.items():
            monkeypatch.setenv(name, value)

        # Actually record fingerprints for these fake values (falls back to
        # the in-process store -- no Redis in this test env) so the
        # assertions below exercise a real fingerprint, not an absent one.
        secrets_registry.record_secret_fingerprints()

        # RESEND_API_KEY being present makes _check_resend call out to
        # Resend for real -- mock that call rather than hitting the live
        # API with a fake token.
        with patch("httpx.get", return_value=MagicMock(status_code=401)):
            response = client.get("/admin/status")
        assert response.status_code == 200
        body_text = json.dumps(response.json())

        for value in fake_values.values():
            assert value not in body_text

        # Never leaks the fingerprint either -- only presence/age may appear.
        for value in fake_values.values():
            assert secrets_registry.fingerprint(value) not in body_text

        secrets = response.json()["data"]["secrets"]
        for name in fake_values:
            assert secrets[name]["present"] is True
            assert "fp" not in secrets[name]
            assert "fingerprint" not in secrets[name]


class TestSubBlockDegradation:
    def setup_method(self, _method):
        _override_admin()
        _reset_integrations_cache()
        _reset_secrets_registry_fallback()

    def teardown_method(self, _method):
        _clear_override()
        _reset_integrations_cache()
        _reset_secrets_registry_fallback()

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


class TestProviderBudgetBlock:
    """The operator half of provider credit exhaustion.

    End users keep getting PROVIDER_CAPACITY_MESSAGE and nothing more -- that masking is
    correct. This block is the signal that did not exist on 2026-09-16, when an unfunded
    Anthropic key took all 11 of its models down and the only reason anyone found out was
    an unrelated tool happening to use the same key.
    """

    KEY_ID = "f001429593544cd92610592c96fee5e341f53e759e3f07aa5089c82159c5ed03"

    ROW = {
        "provider": "anthropic",
        "reason": "credit_balance_low",
        "first_seen_at": "2026-09-16T09:00:00+00:00",
        "last_seen_at": "2026-09-16T11:30:00+00:00",
        "occurrences": 12,
        "sample_model": "claude-sonnet-5",
    }

    def setup_method(self, _method):
        _override_admin()
        _reset_integrations_cache()
        _reset_secrets_registry_fallback()

    def teardown_method(self, _method):
        _clear_override()
        _reset_integrations_cache()
        _reset_secrets_registry_fallback()

    def _get(self, rows):
        with patch("src.db.provider_budget_events.list_recent_budget_events", return_value=rows):
            response = client.get("/admin/status")
        assert response.status_code == 200
        return response.json()["data"]["provider_budget"]

    def test_a_funded_gateway_reads_ok_with_an_empty_list(self):
        block = self._get([])
        assert block == {"status": "ok", "window_hours": 24, "exhausted": []}

    def test_an_exhausted_provider_reads_degraded_with_the_full_entry(self):
        block = self._get([self.ROW])
        assert block["status"] == "degraded"
        assert block["exhausted"] == [
            {
                "provider": "anthropic",
                "reason": "credit_balance_low",
                "first_seen": "2026-09-16T09:00:00+00:00",
                "last_seen": "2026-09-16T11:30:00+00:00",
                "occurrences": 12,
                "sample_model": "claude-sonnet-5",
            }
        ]

    def test_the_block_is_specific_enough_to_act_on(self):
        # "degraded" on its own would repeat the original sin at a different altitude:
        # an operator would know something is wrong and not what to do about it. Each of
        # these answers a question the person paying the bill actually has.
        entry = self._get([self.ROW])["exhausted"][0]
        assert entry["provider"] == "anthropic"  # whose account
        assert entry["reason"] == "credit_balance_low"  # why, from a closed vocabulary
        assert entry["occurrences"] == 12  # how bad
        assert entry["first_seen"] and entry["last_seen"]  # since when, still happening
        assert entry["sample_model"] == "claude-sonnet-5"  # what to retry to confirm

    def test_no_upstream_error_text_reaches_the_response(self):
        # The admin surface may carry more detail than the user-facing message, but not a
        # different *kind* of detail: every field above is one the gateway owns. Upstream
        # budget errors embed a dashboard URL containing the key id, and a stored value
        # that somehow did would be normalized away on read.
        block = self._get(
            [{**self.ROW, "reason": f"credits low, see https://openrouter.ai/keys/{self.KEY_ID}"}]
        )
        body = json.dumps(block)
        assert self.KEY_ID not in body
        assert "openrouter.ai" not in body
        assert "http" not in body
        assert block["exhausted"][0]["reason"] == "unknown"

    def test_a_broken_read_degrades_to_an_error_not_to_ok(self):
        # The failure mode this repo keeps hitting: a broken query that renders as a calm,
        # healthy-looking page. "No provider is out of credit" must not be what you see
        # when the query that would tell you is broken.
        with patch.object(
            admin_status, "provider_budget_status", side_effect=RuntimeError("supabase down")
        ):
            response = client.get("/admin/status")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["provider_budget"] == {"error": "RuntimeError"}
        assert data["provider_budget"].get("status") != "ok"
        # ...and it takes nothing else down with it.
        assert "error" not in data["secrets"]
