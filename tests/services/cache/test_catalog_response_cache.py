import json
from unittest.mock import MagicMock, call

import pytest

from src.services.cache import catalog_response_cache, model_catalog_cache


@pytest.mark.asyncio
async def test_catalog_cache_hit_keeps_original_expiry(monkeypatch):
    redis = MagicMock()
    redis.get.return_value = json.dumps({"data": [{"id": "openai/gpt-4o-mini"}]})
    monkeypatch.setattr(catalog_response_cache, "get_redis_client", lambda: redis)

    result = await catalog_response_cache.get_cached_catalog_response(
        "all", {"limit": 100, "offset": 0}
    )

    assert result == {"data": [{"id": "openai/gpt-4o-mini"}]}
    redis.expire.assert_not_called()
    redis.zadd.assert_called_once()


def test_catalog_cache_uses_fresh_v3_namespace():
    key = catalog_response_cache.get_catalog_cache_key("all", {"limit": 100, "offset": 0})

    assert key.startswith("gw:catalog:v3:all:")


def test_full_catalog_cache_uses_fresh_namespace_and_fixed_expiry(monkeypatch):
    redis = MagicMock()
    redis.get.return_value = json.dumps({"data": [{"id": "openai/gpt-4o-mini"}]})
    monkeypatch.setattr(model_catalog_cache, "get_redis_client", lambda: redis)
    monkeypatch.setattr(model_catalog_cache, "is_redis_available", lambda: True)
    cache = model_catalog_cache.ModelCatalogCache()

    result = cache.get_full_catalog()

    assert result == {"data": [{"id": "openai/gpt-4o-mini"}]}
    redis.get.assert_called_once_with("gw:models:catalog:v2:full")
    redis.expire.assert_not_called()


def test_provider_invalidation_also_clears_aggregated_variants(monkeypatch):
    redis = MagicMock()
    redis.scan.side_effect = [
        (0, [b"gw:catalog:v3:openai:provider-key"]),
        (0, [b"gw:catalog:v3:all:aggregate-key"]),
    ]
    monkeypatch.setattr(catalog_response_cache, "get_redis_client", lambda: redis)

    deleted = catalog_response_cache.invalidate_catalog_cache("openai")

    assert deleted == 2
    assert redis.scan.call_args_list == [
        call(0, match="gw:catalog:v3:openai:*", count=100),
        call(0, match="gw:catalog:v3:all:*", count=100),
    ]
    assert redis.delete.call_args_list == [
        call(b"gw:catalog:v3:openai:provider-key"),
        call(b"gw:catalog:v3:all:aggregate-key"),
    ]


def test_all_invalidation_scans_once(monkeypatch):
    redis = MagicMock()
    redis.scan.return_value = (0, [b"gw:catalog:v3:all:key"])
    monkeypatch.setattr(catalog_response_cache, "get_redis_client", lambda: redis)

    deleted = catalog_response_cache.invalidate_catalog_cache("all")

    assert deleted == 1
    redis.scan.assert_called_once_with(0, match="gw:catalog:v3:all:*", count=100)
