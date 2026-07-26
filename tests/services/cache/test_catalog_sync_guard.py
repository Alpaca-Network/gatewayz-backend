"""Catalog cache writes must not persist a half-written database.

Regression cover for the incident where enabling the hourly sync served a wrong
catalog for 30 minutes: a rebuild ran while ``sync_all_providers`` was mid-write
and the mid-flight snapshot (openai 84 of 104 rows, anthropic 1 of 5) was cached
for the full provider TTL.
"""

from unittest.mock import MagicMock

import pytest

from src.services.cache import catalog_sync_guard as guard_mod
from src.services.cache import model_catalog_cache


@pytest.fixture
def redis(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(guard_mod, "SYNC_GUARD_TTL_SECONDS", 900)
    monkeypatch.setattr("src.config.redis_config.get_redis_client", lambda: client)
    monkeypatch.setattr("src.config.redis_config.is_redis_available", lambda: True)
    return client


class TestSyncGuardMarker:
    def test_mark_started_writes_marker_with_ttl(self, redis):
        assert guard_mod.mark_sync_started() is True
        redis.setex.assert_called_once_with(guard_mod.SYNC_GUARD_KEY, 900, "1")

    def test_mark_finished_clears_marker(self, redis):
        assert guard_mod.mark_sync_finished() is True
        redis.delete.assert_called_once_with(guard_mod.SYNC_GUARD_KEY)

    def test_is_sync_in_progress_reflects_marker(self, redis):
        redis.exists.return_value = 1
        assert guard_mod.is_sync_in_progress() is True
        redis.exists.return_value = 0
        assert guard_mod.is_sync_in_progress() is False

    def test_context_manager_clears_marker_even_on_error(self, redis):
        with pytest.raises(RuntimeError):
            with guard_mod.catalog_sync_guard():
                raise RuntimeError("sync exploded")

        redis.delete.assert_called_once_with(guard_mod.SYNC_GUARD_KEY)

    def test_fails_open_without_redis(self, monkeypatch):
        """A Redis outage must not disable caching — degrade to old behaviour."""
        monkeypatch.setattr("src.config.redis_config.get_redis_client", lambda: None)

        assert guard_mod.mark_sync_started() is False
        assert guard_mod.is_sync_in_progress() is False


class TestCatalogWritesRespectGuard:
    @pytest.fixture
    def cache(self, monkeypatch):
        client = MagicMock()
        monkeypatch.setattr(model_catalog_cache, "get_redis_client", lambda: client)
        monkeypatch.setattr(model_catalog_cache, "is_redis_available", lambda: True)
        return model_catalog_cache.ModelCatalogCache(), client

    @pytest.fixture
    def sync_running(self, monkeypatch):
        def _set(running: bool):
            monkeypatch.setattr(
                "src.services.cache.catalog_sync_guard.is_sync_in_progress", lambda: running
            )

        return _set

    def test_full_catalog_write_skipped_during_sync(self, cache, sync_running):
        catalog_cache, client = cache
        sync_running(True)

        assert catalog_cache.set_full_catalog([{"id": "openai/gpt-4"}]) is False
        client.setex.assert_not_called()

    def test_provider_catalog_write_skipped_during_sync(self, cache, sync_running):
        catalog_cache, client = cache
        sync_running(True)

        assert catalog_cache.set_provider_catalog("openai", [{"id": "openai/gpt-4"}]) is False
        client.setex.assert_not_called()

    def test_unique_models_write_skipped_during_sync(self, cache, sync_running):
        catalog_cache, client = cache
        sync_running(True)

        assert catalog_cache.set_unique_models([{"id": "gpt-4"}]) is False
        client.setex.assert_not_called()

    def test_writes_proceed_when_no_sync_running(self, cache, sync_running):
        catalog_cache, client = cache
        sync_running(False)

        assert catalog_cache.set_full_catalog([{"id": "openai/gpt-4"}]) is True
        client.setex.assert_called_once()

    @pytest.mark.parametrize(
        "method,args",
        [
            ("set_full_catalog", ()),
            ("set_provider_catalog", ("openai",)),
            ("set_unique_models", ()),
        ],
    )
    def test_empty_payload_is_never_cached(self, cache, sync_running, method, args):
        """An empty catalog is how a failing query hides — never persist one."""
        catalog_cache, client = cache
        sync_running(False)

        assert getattr(catalog_cache, method)(*args, []) is False
        client.setex.assert_not_called()
