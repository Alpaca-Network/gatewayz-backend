"""Tests for the provider credit balance monitor (balance APIs + 402 tracking)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.monitoring import provider_credit_monitor as pcm


def _mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _patch_get(payload: dict):
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=_mock_response(payload))
    return patch("httpx.AsyncClient", return_value=client), client


@pytest.fixture(autouse=True)
def _clear_cache():
    pcm._credit_balance_cache.clear()
    yield
    pcm._credit_balance_cache.clear()


class TestParsers:
    def test_deepseek_parses_total_balance(self):
        data = {"balance_infos": [{"currency": "USD", "total_balance": "12.34"}]}
        assert pcm._parse_deepseek_balance(data) == 12.34

    def test_deepseek_empty_infos_returns_none(self):
        assert pcm._parse_deepseek_balance({"balance_infos": []}) is None

    def test_moonshot_parses_available_balance(self):
        assert pcm._parse_moonshot_balance({"data": {"available_balance": 50}}) == 50.0

    def test_novita_parses_credit_balance(self):
        assert pcm._parse_novita_balance({"credit_balance": 7}) == 7.0

    def test_openrouter_parses_limit_remaining(self):
        assert pcm._parse_openrouter_balance({"data": {"limit_remaining": 99.5}}) == 99.5


class TestCheckProviderBalance:
    @pytest.mark.asyncio
    async def test_deepseek_balance_flows_through(self):
        payload = {"balance_infos": [{"currency": "USD", "total_balance": "123.45"}]}
        p, client = _patch_get(payload)
        with p, patch.object(pcm.Config, "DEEPSEEK_API_KEY", "sk-test", create=True):
            result = await pcm.check_provider_balance("deepseek")
        assert result["balance"] == 123.45
        assert result["currency"] == "USD"
        assert result["status"] == "healthy"
        auth = client.get.call_args.kwargs["headers"]["Authorization"]
        assert auth == "Bearer sk-test"
        assert client.get.call_args.args[0] == "https://api.deepseek.com/user/balance"

    @pytest.mark.asyncio
    async def test_zero_balance_is_critical(self):
        payload = {"data": {"available_balance": 0}}
        p, _ = _patch_get(payload)
        with p, patch.object(pcm.Config, "MOONSHOT_API_KEY", "sk-test", create=True):
            result = await pcm.check_provider_balance("moonshot")
        assert result["balance"] == 0.0
        assert result["status"] == "critical"

    @pytest.mark.asyncio
    async def test_missing_key_returns_unknown(self):
        with patch.object(pcm.Config, "NOVITA_API_KEY", None, create=True):
            result = await pcm.check_provider_balance("novita")
        assert result["status"] == "unknown"
        assert result["error"] == "API key not configured"

    @pytest.mark.asyncio
    async def test_unconfigured_provider_returns_unknown(self):
        result = await pcm.check_provider_balance("groq")
        assert result["status"] == "unknown"
        assert "No balance API" in result["error"]

    @pytest.mark.asyncio
    async def test_result_is_cached(self):
        payload = {"balance_infos": [{"total_balance": "60.0"}]}
        p, client = _patch_get(payload)
        with p, patch.object(pcm.Config, "DEEPSEEK_API_KEY", "sk-test", create=True):
            first = await pcm.check_provider_balance("deepseek")
            second = await pcm.check_provider_balance("deepseek")
        assert first["cached"] is False
        assert second["cached"] is True
        assert client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_openrouter_legacy_wrapper_delegates(self):
        payload = {"data": {"limit_remaining": 42.0}}
        p, _ = _patch_get(payload)
        with p, patch.object(pcm.Config, "OPENROUTER_API_KEY", "sk-or", create=True):
            result = await pcm.check_openrouter_credits()
        assert result["provider"] == "openrouter"
        assert result["balance"] == 42.0


class TestCheckAll:
    @pytest.mark.asyncio
    async def test_covers_balance_apis_and_402_providers(self):
        with patch.object(
            pcm, "check_provider_balance", new=AsyncMock(side_effect=lambda p: {"provider": p})
        ):
            results = await pcm.check_all_provider_credits()
        for provider in pcm.BALANCE_APIS:
            assert provider in results
        for provider in pcm._get_monitored_402_providers():
            assert provider in results

    def test_minimax_tracked_via_402(self):
        assert "minimax" in pcm._FALLBACK_402_PROVIDERS
