"""Probe construction for the live health monitor.

Every failure here is one a provider would report as a failed model, so the
status page would publish an outage that does not exist. Each case below was
verified against the real provider APIs before being pinned.
"""

from unittest.mock import MagicMock

import pytest

from src.services.monitoring.intelligent_health_monitor import IntelligentHealthMonitor


@pytest.fixture
def monitor():
    return IntelligentHealthMonitor(redis_coordination=False)


@pytest.fixture
def registry(monkeypatch):
    def _set(entries: dict):
        monkeypatch.setattr("src.services.gateway_registry.get_gateway_registry", lambda: entries)

    return _set


class TestEndpointResolution:
    """Only xai ships an explicit chat_completions_endpoint; the rest must be
    derived from base_url or they resolve to None and are never probed."""

    def test_openai_style_derived_from_base_url(self, monitor, registry):
        registry({"openai": {"base_url": "https://api.openai.com/v1"}})
        assert monitor._get_gateway_endpoint("openai") == (
            "https://api.openai.com/v1/chat/completions"
        )

    def test_moonshot_derived_from_base_url(self, monitor, registry):
        registry({"moonshot": {"base_url": "https://api.moonshot.ai/v1"}})
        assert monitor._get_gateway_endpoint("moonshot") == (
            "https://api.moonshot.ai/v1/chat/completions"
        )

    def test_anthropic_uses_messages_not_chat_completions(self, monitor, registry):
        registry({"anthropic": {"base_url": "https://api.anthropic.com"}})
        assert monitor._get_gateway_endpoint("anthropic") == "https://api.anthropic.com/v1/messages"

    def test_anthropic_base_url_already_versioned(self, monitor, registry):
        """Must not produce .../v1/v1/messages."""
        registry({"anthropic": {"base_url": "https://api.anthropic.com/v1"}})
        assert monitor._get_gateway_endpoint("anthropic") == "https://api.anthropic.com/v1/messages"

    def test_explicit_endpoint_still_wins(self, monitor, registry):
        registry(
            {
                "xai": {
                    "base_url": "https://api.x.ai/v1",
                    "chat_completions_endpoint": "https://custom/x/chat",
                }
            }
        )
        assert monitor._get_gateway_endpoint("xai") == "https://custom/x/chat"

    def test_trailing_slash_does_not_double_up(self, monitor, registry):
        registry({"openai": {"base_url": "https://api.openai.com/v1/"}})
        assert monitor._get_gateway_endpoint("openai") == (
            "https://api.openai.com/v1/chat/completions"
        )


class TestAuthHeaders:
    @pytest.mark.asyncio
    async def test_anthropic_uses_x_api_key_and_version(self, monitor, monkeypatch):
        """A Bearer token gets a 401 from Anthropic — indistinguishable from a
        genuinely broken key when read off the status page."""
        monkeypatch.setattr(
            "src.services.gateway_registry.get_provider_api_key", lambda slug: "sk-test"
        )

        headers = await monitor._get_auth_headers("anthropic")

        assert headers["x-api-key"] == "sk-test"
        assert headers["anthropic-version"] == "2023-06-01"
        assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_other_gateways_use_bearer(self, monitor, monkeypatch):
        monkeypatch.setattr(
            "src.services.gateway_registry.get_provider_api_key", lambda slug: "sk-test"
        )

        headers = await monitor._get_auth_headers("openai")

        assert headers["Authorization"] == "Bearer sk-test"
        assert "x-api-key" not in headers


class TestDisabledProvidersAreNotProbed:
    @pytest.mark.asyncio
    async def test_rows_for_disabled_providers_are_dropped(self, monitor, monkeypatch):
        """model_health_tracking outlives the roster and nothing prunes it."""
        rows = [
            {"provider": "openai", "model": "gpt-4o", "gateway": "openai"},
            {"provider": "openrouter", "model": "x/y", "gateway": "openrouter"},
        ]
        q = MagicMock()
        for m in ("select", "eq", "lte", "order", "limit"):
            getattr(q, m).return_value = q
        q.execute.return_value = MagicMock(data=rows)
        client = MagicMock()
        client.table.return_value = q
        monkeypatch.setattr("src.config.supabase_config.supabase", client)
        monkeypatch.setattr(
            "src.utils.provider_filter.is_provider_enabled", lambda slug: slug == "openai"
        )

        result = await monitor._get_models_for_checking()

        assert [r["provider"] for r in result] == ["openai"]
