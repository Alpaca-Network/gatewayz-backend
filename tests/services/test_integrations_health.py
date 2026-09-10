"""Tests for src/services/integrations_health.py (Phase A, A4)."""

from unittest.mock import MagicMock, patch

import pytest

import src.services.integrations_health as integrations_health
from src.services.integrations_health import check_all


@pytest.fixture(autouse=True)
def skip_if_no_database():
    """Shadow the repo-wide ``skip_if_no_database`` autouse fixture
    (tests/conftest.py) for this module.

    That fixture's path heuristic treats any test file whose path contains
    "integration" as a database test and skips it when no real Supabase
    project is reachable. "test_integrations_health.py" matches on the
    substring alone, even though every check here mocks its dependency
    (Supabase, Redis, Resend, web3) directly -- none of these tests touch a
    real database or network.
    """
    return


def _reset_cache():
    integrations_health._cache = None
    integrations_health._cache_computed_at = 0.0


class TestResendCheck:
    def setup_method(self, _m):
        _reset_cache()

    def test_not_configured_when_no_key(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        result = integrations_health._check_resend(3)
        assert result == {"status": "not_configured", "latency_ms": None, "detail": None}

    def test_ok_on_200(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        mock_response = MagicMock(status_code=200)
        with patch("httpx.get", return_value=mock_response):
            result = integrations_health._check_resend(3)
        assert result["status"] == "ok"
        assert result["latency_ms"] is not None

    def test_suspended_key_degrades_explicitly(self, monkeypatch):
        """The exact scenario from the spec: a suspended Resend key must
        show up as degraded, not silently look healthy or fully down."""
        monkeypatch.setenv("RESEND_API_KEY", "re_suspended")
        mock_response = MagicMock(status_code=401)
        with patch("httpx.get", return_value=mock_response):
            result = integrations_health._check_resend(3)
        assert result["status"] == "degraded"
        assert result["detail"] == "suspended_or_invalid_key"

    def test_other_error_status_is_down(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        mock_response = MagicMock(status_code=500)
        with patch("httpx.get", return_value=mock_response):
            result = integrations_health._check_resend(3)
        assert result["status"] == "down"

    def test_network_error_is_down_never_raises(self, monkeypatch):
        monkeypatch.setenv("RESEND_API_KEY", "re_test")
        with patch("httpx.get", side_effect=RuntimeError("boom")):
            result = integrations_health._check_resend(3)
        assert result["status"] == "down"
        assert result["detail"] == "RuntimeError"


class TestPrivyCheck:
    def setup_method(self, _m):
        _reset_cache()

    def test_not_configured_when_missing_app_id_or_key(self):
        with patch.object(integrations_health.Config, "PRIVY_APP_ID", None):
            result = integrations_health._check_privy()
        assert result["status"] == "not_configured"

    def test_ok_when_key_parses(self):
        # A real EC public key PEM (test fixture, not a secret).
        pem = (
            "-----BEGIN PUBLIC KEY-----\n"
            "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEuDosNCok+QdivoVKje+9bd3wCAS8\n"
            "u+E2KVsJbw47VWACvzVuFDV9kUq7AemGzfMjYK4R0bIcPIV1BfbKwvx8AA==\n"
            "-----END PUBLIC KEY-----"
        )
        with (
            patch.object(integrations_health.Config, "PRIVY_APP_ID", "app_123"),
            patch.object(integrations_health.Config, "PRIVY_VERIFICATION_KEY", pem),
        ):
            result = integrations_health._check_privy()
        assert result["status"] == "ok"
        assert result["detail"] in {"enforce", "log", "off"}

    def test_down_when_key_malformed(self):
        with (
            patch.object(integrations_health.Config, "PRIVY_APP_ID", "app_123"),
            patch.object(integrations_health.Config, "PRIVY_VERIFICATION_KEY", "not-a-pem"),
        ):
            result = integrations_health._check_privy()
        assert result["status"] == "down"


class TestFujiRpcCheck:
    def setup_method(self, _m):
        _reset_cache()

    def test_not_configured_when_contract_unset(self):
        with patch.object(integrations_health.Config, "WAYZ_STAKING_CONTRACT_ADDRESS", None):
            result = integrations_health._check_fuji_rpc(2)
        assert result["status"] == "not_configured"

    def test_ok_when_rpc_responds(self):
        mock_w3_instance = MagicMock()
        mock_w3_instance.eth.block_number = 555
        with (
            patch.object(integrations_health.Config, "WAYZ_STAKING_CONTRACT_ADDRESS", "0xStaking"),
            patch("web3.Web3") as MockWeb3,
        ):
            MockWeb3.return_value = mock_w3_instance
            result = integrations_health._check_fuji_rpc(2)
        assert result["status"] == "ok"
        assert result["detail"] == "block=555"

    def test_down_on_rpc_failure(self):
        with (
            patch.object(integrations_health.Config, "WAYZ_STAKING_CONTRACT_ADDRESS", "0xStaking"),
            patch("web3.Web3", side_effect=TimeoutError("rpc timeout")),
        ):
            result = integrations_health._check_fuji_rpc(2)
        assert result["status"] == "down"


class TestSupabaseCheck:
    def setup_method(self, _m):
        _reset_cache()

    def test_not_configured_when_unset(self):
        with (
            patch.object(integrations_health.Config, "SUPABASE_URL", None),
            patch.object(integrations_health.Config, "SUPABASE_KEY", None),
        ):
            result = integrations_health._check_supabase(3)
        assert result["status"] == "not_configured"

    def test_ok_when_query_succeeds(self):
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.limit.return_value.execute.return_value = (
            MagicMock()
        )
        with (
            patch.object(integrations_health.Config, "SUPABASE_URL", "https://x.supabase.co"),
            patch.object(integrations_health.Config, "SUPABASE_KEY", "key"),
            patch(
                "src.config.supabase_config.get_supabase_client",
                return_value=mock_client,
            ),
        ):
            result = integrations_health._check_supabase(3)
        assert result["status"] == "ok"

    def test_down_when_query_raises(self):
        with (
            patch.object(integrations_health.Config, "SUPABASE_URL", "https://x.supabase.co"),
            patch.object(integrations_health.Config, "SUPABASE_KEY", "key"),
            patch(
                "src.config.supabase_config.get_supabase_client",
                side_effect=RuntimeError("db down"),
            ),
        ):
            result = integrations_health._check_supabase(3)
        assert result["status"] == "down"


class TestRedisCheck:
    def setup_method(self, _m):
        _reset_cache()

    def test_not_configured_when_disabled(self):
        with patch.object(integrations_health.Config, "REDIS_ENABLED", False):
            result = integrations_health._check_redis(3)
        assert result["status"] == "not_configured"

    def test_ok_when_ping_succeeds(self):
        mock_client = MagicMock()
        with (
            patch.object(integrations_health.Config, "REDIS_ENABLED", True),
            patch("src.config.redis_config.get_redis_client", return_value=mock_client),
        ):
            result = integrations_health._check_redis(3)
        assert result["status"] == "ok"

    def test_down_when_client_unavailable(self):
        with (
            patch.object(integrations_health.Config, "REDIS_ENABLED", True),
            patch("src.config.redis_config.get_redis_client", return_value=None),
        ):
            result = integrations_health._check_redis(3)
        assert result["status"] == "down"

    def test_down_when_ping_raises(self):
        mock_client = MagicMock()
        mock_client.ping.side_effect = RuntimeError("no connection")
        with (
            patch.object(integrations_health.Config, "REDIS_ENABLED", True),
            patch("src.config.redis_config.get_redis_client", return_value=mock_client),
        ):
            result = integrations_health._check_redis(3)
        assert result["status"] == "down"


class TestStripeAndSentryChecks:
    def test_stripe_not_configured(self, monkeypatch):
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        assert integrations_health._check_stripe()["status"] == "not_configured"

    def test_stripe_ok_when_key_present(self, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
        assert integrations_health._check_stripe()["status"] == "ok"

    def test_sentry_not_configured(self):
        with patch.object(integrations_health.Config, "SENTRY_DSN", None):
            assert integrations_health._check_sentry()["status"] == "not_configured"

    def test_sentry_ok_when_dsn_present(self):
        with patch.object(integrations_health.Config, "SENTRY_DSN", "https://sentry.example/1"):
            assert integrations_health._check_sentry()["status"] == "ok"


class TestCheckAllCaching:
    def setup_method(self, _m):
        _reset_cache()

    def test_check_all_returns_every_integration(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
        with (
            patch.object(integrations_health.Config, "PRIVY_APP_ID", None),
            patch.object(integrations_health.Config, "WAYZ_STAKING_CONTRACT_ADDRESS", None),
            patch.object(integrations_health.Config, "SUPABASE_URL", None),
            patch.object(integrations_health.Config, "REDIS_ENABLED", False),
            patch.object(integrations_health.Config, "SENTRY_DSN", None),
        ):
            result = check_all()
        for name in ("resend", "privy", "fuji_rpc", "supabase", "redis", "stripe", "sentry"):
            assert name in result
            assert result[name]["status"] == "not_configured"

    def test_result_is_cached_within_ttl(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        with patch.object(integrations_health, "_check_privy") as mock_privy:
            mock_privy.return_value = {
                "status": "not_configured",
                "latency_ms": None,
                "detail": None,
            }
            check_all()
            check_all()
        # Second call within the cache TTL must not recompute.
        assert mock_privy.call_count == 1

    def test_one_check_raising_does_not_break_the_others(self, monkeypatch):
        monkeypatch.delenv("RESEND_API_KEY", raising=False)
        with patch.object(integrations_health, "_check_privy", side_effect=RuntimeError("boom")):
            result = check_all()
        assert result["privy"]["status"] == "down"
        assert result["privy"]["detail"] == "RuntimeError"
        assert "resend" in result
