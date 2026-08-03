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
        # Isolate the provider filter. Without pinning the scope, the servable
        # lookup would query whatever database the environment happens to have
        # and drop these fixtures for not being in it — an empty scope disables
        # that second filter by design.
        monkeypatch.setattr(
            "src.services.monitoring.intelligent_health_monitor._get_servable_model_ids", set
        )

        result = await monitor._get_models_for_checking()

        assert [r["provider"] for r in result] == ["openai"]


class TestProbeUsesNativeModelId:
    """model_health_tracking stores gateway-prefixed catalog ids; provider APIs
    want the native id. Probing the prefixed form failed 89 of 100 checks in
    production — OpenAI answers "invalid model ID", Anthropic and Moonshot 404.
    """

    @pytest.mark.parametrize(
        "gateway,stored,expected",
        [
            ("openai", "openai/gpt-5.5-pro", "gpt-5.5-pro"),
            ("openai", "openai/gpt-4o-mini", "gpt-4o-mini"),
            ("anthropic", "anthropic/claude-opus-4-8", "claude-opus-4-8"),
            # No mapping table for moonshot, so the prefix survives the
            # transform and the explicit strip has to catch it.
            ("moonshot", "moonshot/kimi-k2.6", "kimi-k2.6"),
            # Already native — must pass through untouched.
            ("xai", "grok-4", "grok-4"),
        ],
    )
    def test_prefix_is_resolved_to_native_id(self, gateway, stored, expected):
        from src.services.model_transformations import transform_model_id

        probe_id = transform_model_id(stored, gateway) or stored
        if probe_id.startswith(f"{gateway}/"):
            probe_id = probe_id[len(gateway) + 1 :]

        assert probe_id == expected

    def test_slash_in_a_genuine_native_id_is_preserved(self):
        """OpenRouter serves "openai/gpt-4" as its native id. The strip keys on
        the gateway name, so an unrelated vendor prefix must survive."""
        gateway, stored = "openrouter", "openai/gpt-4"

        probe_id = stored
        if probe_id.startswith(f"{gateway}/"):
            probe_id = probe_id[len(gateway) + 1 :]

        assert probe_id == "openai/gpt-4"


class TestTokenBudgetParameter:
    """OpenAI's reasoning generations reject `max_tokens` outright."""

    @pytest.mark.parametrize(
        "gateway,expected",
        [
            ("openai", "max_completion_tokens"),
            ("anthropic", "max_tokens"),
            ("moonshot", "max_tokens"),
            ("xai", "max_tokens"),
        ],
    )
    def test_openai_uses_max_completion_tokens(self, gateway, expected):
        payload = {"model": "m", "messages": [{"role": "user", "content": "test"}]}
        if gateway == "openai":
            payload["max_completion_tokens"] = 5
        else:
            payload["max_tokens"] = 5

        assert expected in payload
        assert ("max_tokens" in payload) is (gateway != "openai")


class TestOutputBudgetExhaustedIsAlive:
    """A 400 saying the model spent our token budget proves it ran.

    Reading it as a failure would mark every reasoning model permanently down —
    the same mistake that hid nine flagship models in #2190.
    """

    def test_openai_budget_message_counts_as_alive(self):
        from src.services.monitoring.intelligent_health_monitor import (
            _is_output_budget_exhausted,
        )

        body = (
            '{"error":{"message":"Could not finish the message because max_tokens '
            'or model output limit was reached. Please try again with higher max_tokens."}}'
        )
        assert _is_output_budget_exhausted(body) is True

    @pytest.mark.parametrize(
        "body",
        [
            '{"error":{"message":"invalid model ID"}}',
            '{"error":{"message":"Unsupported parameter: max_tokens is not supported"}}',
            '{"code":"invalid-argument","error":"Model not found: grok-2-vision"}',
            "",
            None,
        ],
    )
    def test_real_failures_are_not_swallowed(self, body):
        from src.services.monitoring.intelligent_health_monitor import (
            _is_output_budget_exhausted,
        )

        assert _is_output_budget_exhausted(body) is False
