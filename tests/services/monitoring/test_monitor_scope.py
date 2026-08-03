"""Probe only what we actually sell.

model_health_tracking outlives the catalog. 79 of 110 tracked rows were models
long delisted — Claude 3 Haiku, GPT-4 and friends — and each one fails every
probe by design. That dragged the PUBLIC status page to 6% success while every
served model was healthy, which is a worse lie than the 0% it replaced.
"""

from unittest.mock import MagicMock

import pytest

from src.services.monitoring import intelligent_health_monitor as ihm
from src.services.monitoring.intelligent_health_monitor import IntelligentHealthMonitor


@pytest.fixture
def monitor():
    return IntelligentHealthMonitor(redis_coordination=False)


def _tracking_client(rows):
    q = MagicMock()
    for m in ("select", "eq", "lte", "order", "limit"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=rows)
    client = MagicMock()
    client.table.return_value = q
    return client


class TestScopeFilter:
    @pytest.mark.asyncio
    async def test_delisted_models_are_not_probed(self, monitor, monkeypatch):
        rows = [
            {"provider": "anthropic", "model": "anthropic/claude-sonnet-5", "gateway": "anthropic"},
            {"provider": "anthropic", "model": "anthropic/claude-3-haiku", "gateway": "anthropic"},
        ]
        monkeypatch.setattr("src.config.supabase_config.supabase", _tracking_client(rows))
        monkeypatch.setattr("src.utils.provider_filter.is_provider_enabled", lambda s: True)
        monkeypatch.setattr(ihm, "_get_servable_model_ids", lambda: {"anthropic/claude-sonnet-5"})

        result = await monitor._get_models_for_checking()

        assert [m["model"] for m in result] == ["anthropic/claude-sonnet-5"]

    @pytest.mark.asyncio
    async def test_an_empty_scope_disables_the_filter(self, monitor, monkeypatch):
        """A failed lookup must not stop monitoring altogether.

        Probing too much is recoverable; silently monitoring nothing is not.
        """
        rows = [{"provider": "anthropic", "model": "anthropic/anything", "gateway": "anthropic"}]
        monkeypatch.setattr("src.config.supabase_config.supabase", _tracking_client(rows))
        monkeypatch.setattr("src.utils.provider_filter.is_provider_enabled", lambda s: True)
        monkeypatch.setattr(ihm, "_get_servable_model_ids", set)

        result = await monitor._get_models_for_checking()

        assert [m["model"] for m in result] == ["anthropic/anything"]


class TestServableLookup:
    def test_collects_active_models_for_enabled_providers(self, monkeypatch):
        def _client(**_k):
            providers = MagicMock()
            providers.select.return_value = providers
            providers.eq.return_value = providers
            providers.execute.return_value = MagicMock(
                data=[{"id": 1, "slug": "anthropic"}, {"id": 2, "slug": "openrouter"}]
            )

            models = MagicMock()
            models.select.return_value = models
            models.eq.return_value = models
            models.execute.return_value = MagicMock(
                data=[{"provider_model_id": "anthropic/claude-sonnet-5"}]
            )

            c = MagicMock()
            c.table.side_effect = lambda name: providers if name == "providers" else models
            return c

        monkeypatch.setattr("src.config.supabase_config.get_client_for_query", _client)
        monkeypatch.setattr(
            "src.utils.provider_filter.is_provider_enabled", lambda s: s == "anthropic"
        )

        assert ihm._get_servable_model_ids() == {"anthropic/claude-sonnet-5"}

    def test_returns_empty_on_failure_rather_than_raising(self, monkeypatch):
        def _boom(**_k):
            raise RuntimeError("db down")

        monkeypatch.setattr("src.config.supabase_config.get_client_for_query", _boom)

        assert ihm._get_servable_model_ids() == set()

    def test_no_enabled_providers_yields_an_empty_scope(self, monkeypatch):
        def _client(**_k):
            providers = MagicMock()
            providers.select.return_value = providers
            providers.eq.return_value = providers
            providers.execute.return_value = MagicMock(data=[{"id": 1, "slug": "openrouter"}])
            c = MagicMock()
            c.table.return_value = providers
            return c

        monkeypatch.setattr("src.config.supabase_config.get_client_for_query", _client)
        monkeypatch.setattr("src.utils.provider_filter.is_provider_enabled", lambda s: False)

        assert ihm._get_servable_model_ids() == set()
