"""
The combined catalog must not silently drop a provider.

Prod, 2026-10-19/20: after a deploy, /v1/models (gateway=all) lost every groq
row while ?gateway=groq still had 11, and earlier zai showed 8 of 11. The
per-provider copy feeding the combined rebuild was empty or half-written (read
during a DB error or a sync) and got pinned in local memory, so every rebuild
in that process kept the gap. These tests pin the repair: the rebuild checks
each provider against its active row count, reloads short ones straight from
the database, and never keeps a combined catalog that is still missing one.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

import src.services.cache.model_catalog_cache as mcc


def _models(slug: str, count: int) -> list[dict]:
    return [{"id": f"{slug}/m-{i}", "source_gateway": slug} for i in range(count)]


def _providers_client(slugs):
    client = MagicMock()
    response = MagicMock()
    response.data = [{"slug": s} for s in slugs]
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
    return client


@pytest.fixture(autouse=True)
def _hermetic():
    with (
        patch("src.utils.provider_filter.is_provider_enabled", return_value=True),
        patch("src.services.cache.catalog_sync_guard.is_sync_in_progress", return_value=False),
    ):
        yield
    mcc._last_rebuild_complete = True


def _rebuild(cached, counts, reloaded):
    """Run a rebuild with per-provider cached copies, DB counts and DB reloads."""
    cache = MagicMock()
    with (
        patch(
            "src.config.supabase_config.get_client_for_query",
            return_value=_providers_client(list(cached)),
        ),
        patch.object(mcc, "get_cached_provider_catalog", side_effect=lambda s: cached[s]),
        patch.object(mcc, "_count_active_provider_models", side_effect=lambda s: counts.get(s)),
        patch.object(
            mcc, "_reload_provider_from_db", side_effect=lambda s: reloaded.get(s, [])
        ) as reload,
        patch.object(mcc, "get_model_catalog_cache", return_value=cache),
        patch("src.services.cache.local_memory_cache.set_local_catalog") as set_local,
    ):
        result = mcc.rebuild_full_catalog_from_providers()
    return result, cache, set_local, reload


def _by_gateway(models):
    out: dict[str, int] = {}
    for m in models:
        out[m["source_gateway"]] = out.get(m["source_gateway"], 0) + 1
    return out


class TestRepair:
    def test_missing_provider_cache_is_reloaded_from_db(self):
        result, cache, set_local, _ = _rebuild(
            cached={"openai": _models("openai", 3), "groq": []},
            counts={"openai": 3, "groq": 11},
            reloaded={"groq": _models("groq", 11)},
        )

        assert _by_gateway(result) == {"openai": 3, "groq": 11}
        cache.set_provider_catalog.assert_called_once()
        assert cache.set_provider_catalog.call_args[0][0] == "groq"
        assert cache.set_full_catalog.call_args[1]["ttl"] == mcc.ModelCatalogCache.TTL_FULL_CATALOG
        set_local.assert_any_call("all", result)
        assert mcc.last_full_catalog_rebuild_complete() is True

    def test_partial_provider_cache_is_completed(self):
        result, _, _, reload = _rebuild(
            cached={"zai": _models("zai", 8)},
            counts={"zai": 11},
            reloaded={"zai": _models("zai", 11)},
        )

        assert _by_gateway(result) == {"zai": 11}
        reload.assert_called_once_with("zai")

    def test_still_missing_is_cached_briefly_and_flagged(self):
        result, cache, set_local, _ = _rebuild(
            cached={"openai": _models("openai", 3), "groq": []},
            counts={"openai": 3, "groq": 11},
            reloaded={"groq": []},
        )

        assert _by_gateway(result) == {"openai": 3}
        assert cache.set_full_catalog.call_args[1]["ttl"] == mcc.TTL_INCOMPLETE_FULL_CATALOG
        assert all(c.args[0] != "all" for c in set_local.call_args_list)
        assert mcc.last_full_catalog_rebuild_complete() is False

    def test_provider_with_no_active_rows_is_not_reloaded(self):
        result, _, _, reload = _rebuild(
            cached={"openai": _models("openai", 3), "xai": []},
            counts={"openai": 3, "xai": 0},
            reloaded={},
        )

        assert _by_gateway(result) == {"openai": 3}
        reload.assert_not_called()
        assert mcc.last_full_catalog_rebuild_complete() is True

    def test_unknown_count_leaves_cached_copy_alone(self):
        result, _, _, reload = _rebuild(
            cached={"openai": _models("openai", 3)},
            counts={},
            reloaded={},
        )

        assert len(result) == 3
        reload.assert_not_called()

    def test_no_counts_when_database_is_unreachable(self):
        """Registry fallback means the DB is down; counting would only add timeouts."""
        with (
            patch(
                "src.config.supabase_config.get_client_for_query",
                side_effect=Exception("DB down"),
            ),
            patch(
                "src.services.gateway_registry.get_gateway_registry",
                return_value={"openai": {}},
            ),
            patch.object(mcc, "get_cached_provider_catalog", return_value=_models("openai", 2)),
            patch.object(mcc, "_count_active_provider_models") as count,
            patch.object(mcc, "get_model_catalog_cache", return_value=MagicMock()),
            patch("src.services.cache.local_memory_cache.set_local_catalog"),
        ):
            result = mcc.rebuild_full_catalog_from_providers()

        assert len(result) == 2
        count.assert_not_called()

    def test_order_is_stable(self):
        result, _, _, _ = _rebuild(
            cached={"b": _models("b", 1), "a": _models("a", 1)},
            counts={},
            reloaded={},
        )

        assert [m["source_gateway"] for m in result] == ["b", "a"]


class TestLocalCacheGuard:
    def test_empty_list_is_cached_only_briefly(self):
        with patch("src.services.cache.local_memory_cache.set_local_catalog") as set_local:
            mcc._set_local_catalog_if_sound("groq", [])
        set_local.assert_called_once_with(
            "groq", [], ttl=mcc.SUSPECT_LOCAL_CATALOG_TTL, stale_ttl=0.0
        )

    def test_list_read_during_a_sync_is_cached_only_briefly(self):
        partial = _models("zai", 8)
        with (
            patch("src.services.cache.catalog_sync_guard.is_sync_in_progress", return_value=True),
            patch("src.services.cache.local_memory_cache.set_local_catalog") as set_local,
        ):
            mcc._set_local_catalog_if_sound("zai", partial)
        set_local.assert_called_once_with(
            "zai", partial, ttl=mcc.SUSPECT_LOCAL_CATALOG_TTL, stale_ttl=0.0
        )

    def test_sound_list_keeps_the_normal_lifetime(self):
        full = _models("zai", 11)
        with patch("src.services.cache.local_memory_cache.set_local_catalog") as set_local:
            mcc._set_local_catalog_if_sound("zai", full)
        set_local.assert_called_once_with("zai", full)

    def test_db_miss_returning_empty_is_not_pinned_locally(self):
        cache = MagicMock()
        cache.get_provider_catalog.return_value = None
        with (
            patch.object(mcc, "get_model_catalog_cache", return_value=cache),
            patch(
                "src.services.cache.local_memory_cache.get_local_catalog",
                return_value=(None, False),
            ),
            patch("src.services.cache.local_memory_cache.set_local_catalog") as set_local,
            patch("src.db.models_catalog_db.get_models_by_gateway_for_catalog", return_value=[]),
            patch("src.db.models_catalog_db.transform_db_models_batch", return_value=[]),
        ):
            assert mcc.get_cached_provider_catalog("groq") == []
        set_local.assert_called_once_with(
            "groq", [], ttl=mcc.SUSPECT_LOCAL_CATALOG_TTL, stale_ttl=0.0
        )


class TestResponseCacheGuard:
    def _cache(self, gateway):
        from src.services.cache import catalog_response_cache as crc

        redis = MagicMock()
        with (
            patch.object(crc, "get_redis_client", return_value=redis),
            patch.object(crc, "_evict_lru_if_needed", return_value=0),
            patch.object(crc, "_update_cache_metadata"),
        ):
            ok = asyncio.run(crc.cache_catalog_response(gateway, {"limit": None}, {"data": []}))
        return ok, redis.pipeline.return_value

    def test_incomplete_combined_response_is_not_cached(self):
        mcc._last_rebuild_complete = False
        ok, redis = self._cache("all")
        assert ok is False
        redis.setex.assert_not_called()

    def test_single_gateway_response_is_still_cached(self):
        mcc._last_rebuild_complete = False
        _, redis = self._cache("groq")
        redis.setex.assert_called_once()


class TestAllowlistedProvider:
    """PROVIDER_MODEL_ALLOWLIST trims a provider's cached copy (deepinfra: 15 of
    ~230 active rows), so the repair must count only the allowlisted rows.
    Counting every active row would mark the provider partial on every rebuild."""

    LISTED = [f"org/model-{i}" for i in range(15)]

    @staticmethod
    def _rows(total):
        return [{"provider_model_id": f"org/model-{i}"} for i in range(total)]

    @staticmethod
    def _catalog(count):
        return [
            {"id": f"deepinfra/org/model-{i}", "source_gateway": "deepinfra"} for i in range(count)
        ]

    def _client(self, slugs, db_rows):
        client = _providers_client(slugs)
        rows = MagicMock()
        rows.data = db_rows
        chain = client.table.return_value.select.return_value.eq.return_value.eq.return_value
        chain.range.return_value.execute.return_value = rows
        # The unfiltered head count sees every active row.
        chain.execute.return_value.count = len(db_rows)
        return client

    def _allowlist(self):
        import json

        return patch(
            "src.config.config.Config.PROVIDER_MODEL_ALLOWLIST",
            json.dumps({"deepinfra": self.LISTED}),
            create=True,
        )

    def _rebuild(self, cached, reloaded):
        cache = MagicMock()
        with (
            self._allowlist(),
            patch(
                "src.config.supabase_config.get_client_for_query",
                return_value=self._client(["deepinfra"], self._rows(230)),
            ),
            patch.object(mcc, "get_cached_provider_catalog", return_value=cached),
            patch.object(mcc, "_reload_provider_from_db", return_value=reloaded) as reload,
            patch.object(mcc, "get_model_catalog_cache", return_value=cache),
            patch("src.services.cache.local_memory_cache.set_local_catalog"),
        ):
            result = mcc.rebuild_full_catalog_from_providers()
        return result, cache, reload

    def test_count_includes_only_allowlisted_rows(self):
        with (
            self._allowlist(),
            patch(
                "src.config.supabase_config.get_client_for_query",
                return_value=self._client(["deepinfra"], self._rows(230)),
            ),
        ):
            assert mcc._count_active_provider_models("deepinfra") == 15

    def test_15_of_230_is_complete_without_a_reload(self):
        result, cache, reload = self._rebuild(self._catalog(15), [])

        assert len(result) == 15
        reload.assert_not_called()
        assert cache.set_full_catalog.call_args[1]["ttl"] == mcc.ModelCatalogCache.TTL_FULL_CATALOG
        assert mcc.last_full_catalog_rebuild_complete() is True

    def test_missing_listed_models_are_still_repaired(self):
        result, _, reload = self._rebuild(self._catalog(9), self._catalog(15))

        reload.assert_called_once_with("deepinfra")
        assert len(result) == 15
        assert mcc.last_full_catalog_rebuild_complete() is True

    def test_db_reload_applies_the_allowlist(self):
        unfiltered = [
            {"id": f"deepinfra/org/model-{i}", "provider_model_id": f"org/model-{i}"}
            for i in range(230)
        ]
        with (
            self._allowlist(),
            patch.object(mcc, "_fetch_provider_models_from_db", return_value=unfiltered),
            patch("src.services.models._set_building_catalog"),
        ):
            assert len(mcc._reload_provider_from_db("deepinfra")) == 15
