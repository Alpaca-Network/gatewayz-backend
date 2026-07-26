"""A Redis delete cannot reach a per-process cache; an epoch can.

The local catalog cache holds entries for 15 minutes fresh plus an hour of stale
grace. Invalidation deletes a Redis key, which leaves every worker's in-process
copy untouched — so a corrected catalog could stay invisible for up to 75
minutes. Every catalog fix made today needed a container restart before it
showed, including a model priced a million-fold too low.

Entries now carry the epoch they were written under, and invalidation bumps a
shared counter.
"""

from unittest.mock import MagicMock

import pytest

from src.services.cache import local_memory_cache as lmc


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch):
    lmc.get_local_cache().clear()
    monkeypatch.setattr(lmc, "_epoch_value", 0)
    monkeypatch.setattr(lmc, "_epoch_checked_at", 0.0)
    yield
    lmc.get_local_cache().clear()


@pytest.fixture
def redis(monkeypatch):
    client = MagicMock()
    client.get.return_value = b"0"
    monkeypatch.setattr("src.config.redis_config.get_redis_client", lambda: client)
    monkeypatch.setattr("src.config.redis_config.is_redis_available", lambda: True)
    return client


class TestEpochInvalidation:
    def test_entry_survives_while_the_epoch_is_unchanged(self, redis):
        lmc.set_local_catalog("openai", [{"id": "a"}])

        catalog, _ = lmc.get_local_catalog("openai")

        assert catalog == [{"id": "a"}]

    def test_entry_is_dropped_once_the_epoch_moves(self, redis):
        """The case that mattered: another worker invalidated, this one must notice."""
        lmc.set_local_catalog("openai", [{"id": "stale"}])

        # A different process bumped the shared counter.
        redis.get.return_value = b"1"
        lmc.get_catalog_epoch(force=True)

        catalog, _ = lmc.get_local_catalog("openai")

        assert catalog is None, "a superseded catalog must not be served"

    def test_bump_increments_and_refreshes_locally(self, redis):
        redis.incr.return_value = 7
        redis.get.return_value = b"7"

        assert lmc.bump_catalog_epoch() == 7
        redis.incr.assert_called_once_with(lmc.CATALOG_EPOCH_KEY)
        assert lmc.get_catalog_epoch() == 7

    def test_write_after_a_bump_is_readable(self, redis):
        """Invalidate, then repopulate — the fresh entry must stick."""
        redis.incr.return_value = 3
        redis.get.return_value = b"3"
        lmc.bump_catalog_epoch()

        lmc.set_local_catalog("openai", [{"id": "fresh"}])
        catalog, _ = lmc.get_local_catalog("openai")

        assert catalog == [{"id": "fresh"}]


class TestDegradation:
    def test_epoch_read_is_memoised(self, redis, monkeypatch):
        """The common path must stay in-process, not a Redis round-trip per read."""
        lmc.get_catalog_epoch(force=True)
        redis.get.reset_mock()

        for _ in range(20):
            lmc.get_catalog_epoch()

        redis.get.assert_not_called()

    def test_redis_outage_keeps_serving_the_last_known_epoch(self, monkeypatch):
        """Fail closed: an outage must not throw away every worker's cache."""
        monkeypatch.setattr(lmc, "_epoch_value", 4)
        monkeypatch.setattr("src.config.redis_config.get_redis_client", lambda: None)

        assert lmc.get_catalog_epoch(force=True) == 4

    def test_bump_without_redis_does_not_raise(self, monkeypatch):
        monkeypatch.setattr("src.config.redis_config.get_redis_client", lambda: None)

        assert lmc.bump_catalog_epoch() == lmc._epoch_value


class TestEpochNeverRegresses:
    """A stale read must not un-invalidate a catalog we just superseded.

    get_catalog_epoch releases the lock to do its Redis read, so a slow reader —
    or a lagging replica — can return a number older than one another thread
    already recorded. Overwriting with it would silently resurrect a superseded
    catalog, defeating the entire mechanism.
    """

    def test_a_lagging_read_cannot_lower_the_epoch(self, redis, monkeypatch):
        monkeypatch.setattr(lmc, "_epoch_value", 9)
        redis.get.return_value = b"4"  # replica behind, or a raced read

        assert lmc.get_catalog_epoch(force=True) == 9

    def test_a_newer_read_does_advance_it(self, redis, monkeypatch):
        monkeypatch.setattr(lmc, "_epoch_value", 2)
        redis.get.return_value = b"11"

        assert lmc.get_catalog_epoch(force=True) == 11

    def test_bump_uses_the_incr_result_without_a_second_read(self, redis):
        """INCR already returns the authoritative value; re-reading can lag."""
        redis.incr.return_value = 5
        redis.get.reset_mock()

        assert lmc.bump_catalog_epoch() == 5
        redis.get.assert_not_called()

    def test_bump_cannot_lower_the_epoch_either(self, redis, monkeypatch):
        monkeypatch.setattr(lmc, "_epoch_value", 8)
        redis.incr.return_value = 3

        assert lmc.bump_catalog_epoch() == 8


class TestConcurrentRefresh:
    def test_only_one_thread_fires_the_redis_read_per_interval(self, redis):
        """The refresh window is claimed under the lock, so a lapsed interval
        does not produce a burst of GETs from every concurrent reader."""
        import threading

        lmc.get_catalog_epoch(force=True)
        redis.get.reset_mock()
        # Expire the memo window.
        lmc._epoch_checked_at = 0.0

        threads = [threading.Thread(target=lmc.get_catalog_epoch) for _ in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert redis.get.call_count == 1
