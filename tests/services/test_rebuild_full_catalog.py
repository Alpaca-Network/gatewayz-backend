"""
Tests for rebuild_full_catalog_from_providers()

Verifies that the full model catalog is correctly assembled from individually-
cached per-provider catalogs, avoiding the single-giant-query timeout that
truncates results.
"""

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Sample data helpers
# ---------------------------------------------------------------------------


def _make_models(provider_slug: str, count: int) -> list[dict]:
    """Generate a list of fake model dicts for a given provider."""
    return [
        {
            "id": f"{provider_slug}/model-{i}",
            "name": f"Model {i}",
            "source_gateway": provider_slug,
            "provider_slug": provider_slug,
            "context_length": 4096,
            "pricing": {"prompt": "0.001", "completion": "0.002"},
            "is_active": True,
        }
        for i in range(count)
    ]


SAMPLE_PROVIDERS_DB = [
    {"slug": "openai"},
    {"slug": "anthropic"},
    {"slug": "openrouter"},
]


def _mock_supabase_response(data):
    """Build a mock supabase client that returns *data* from providers query."""
    client = MagicMock()
    response = MagicMock()
    response.data = data
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
    return client


def _provider_catalog_side_effect(slug):
    """Default side effect: deterministic model counts per provider."""
    counts = {"openai": 3, "anthropic": 2, "openrouter": 5}
    count = counts.get(slug, 0)
    return _make_models(slug, count) if count else []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRebuildFullCatalogFromProviders:
    """Tests for rebuild_full_catalog_from_providers()."""

    def test_assembles_all_providers(self):
        """All provider models are merged into a single catalog."""
        mock_client = _mock_supabase_response(SAMPLE_PROVIDERS_DB)

        with (
            patch("src.config.supabase_config.get_client_for_query", return_value=mock_client),
            patch(
                "src.services.model_catalog_cache.get_cached_provider_catalog",
                side_effect=_provider_catalog_side_effect,
            ),
            patch("src.services.model_catalog_cache.get_model_catalog_cache") as mock_cache_cls,
            patch("src.services.local_memory_cache.set_local_catalog"),
        ):
            mock_cache_cls.return_value = MagicMock()

            from src.services.model_catalog_cache import rebuild_full_catalog_from_providers

            result = rebuild_full_catalog_from_providers()

        # 3 + 2 + 5 = 10 models total
        assert len(result) == 10
        slugs = {m["source_gateway"] for m in result}
        assert slugs == {"openai", "anthropic", "openrouter"}

    def test_caches_result_in_redis_and_local(self):
        """Assembled catalog is cached in both Redis and local memory."""
        mock_client = _mock_supabase_response(SAMPLE_PROVIDERS_DB)
        mock_cache = MagicMock()

        with (
            patch("src.config.supabase_config.get_client_for_query", return_value=mock_client),
            patch(
                "src.services.model_catalog_cache.get_cached_provider_catalog",
                side_effect=_provider_catalog_side_effect,
            ),
            patch(
                "src.services.model_catalog_cache.get_model_catalog_cache", return_value=mock_cache
            ),
            patch("src.services.local_memory_cache.set_local_catalog") as mock_set_local,
        ):

            from src.services.model_catalog_cache import rebuild_full_catalog_from_providers

            result = rebuild_full_catalog_from_providers()

        mock_cache.set_full_catalog.assert_called_once()
        assert len(mock_cache.set_full_catalog.call_args[0][0]) == 10
        mock_set_local.assert_called_once_with("all", result)

    def test_partial_provider_failure(self):
        """If some providers fail, others still contribute to the catalog."""
        mock_client = _mock_supabase_response(SAMPLE_PROVIDERS_DB)

        def _side_effect(slug):
            if slug == "anthropic":
                raise RuntimeError("connection refused")
            counts = {"openai": 3, "openrouter": 5}
            return _make_models(slug, counts.get(slug, 0))

        with (
            patch("src.config.supabase_config.get_client_for_query", return_value=mock_client),
            patch(
                "src.services.model_catalog_cache.get_cached_provider_catalog",
                side_effect=_side_effect,
            ),
            patch(
                "src.services.model_catalog_cache.get_model_catalog_cache", return_value=MagicMock()
            ),
            patch("src.services.local_memory_cache.set_local_catalog"),
        ):

            from src.services.model_catalog_cache import rebuild_full_catalog_from_providers

            result = rebuild_full_catalog_from_providers()

        # anthropic failed, but openai (3) + openrouter (5) = 8
        assert len(result) == 8
        slugs = {m["source_gateway"] for m in result}
        assert "anthropic" not in slugs
        assert "openai" in slugs
        assert "openrouter" in slugs

    def test_all_providers_empty_returns_empty(self):
        """If every provider returns empty, result is empty and nothing is cached."""
        mock_client = _mock_supabase_response(SAMPLE_PROVIDERS_DB)
        mock_cache = MagicMock()

        with (
            patch("src.config.supabase_config.get_client_for_query", return_value=mock_client),
            patch("src.services.model_catalog_cache.get_cached_provider_catalog", return_value=[]),
            patch(
                "src.services.model_catalog_cache.get_model_catalog_cache", return_value=mock_cache
            ),
            patch("src.services.local_memory_cache.set_local_catalog"),
        ):

            from src.services.model_catalog_cache import rebuild_full_catalog_from_providers

            result = rebuild_full_catalog_from_providers()

        assert result == []
        mock_cache.set_full_catalog.assert_not_called()

    def test_empty_provider_list_returns_empty(self):
        """If no provider slugs are discovered, return empty immediately."""
        mock_client = _mock_supabase_response([])  # No providers

        with (
            patch("src.config.supabase_config.get_client_for_query", return_value=mock_client),
            patch(
                "src.services.model_catalog_cache.get_model_catalog_cache", return_value=MagicMock()
            ),
        ):

            from src.services.model_catalog_cache import rebuild_full_catalog_from_providers

            result = rebuild_full_catalog_from_providers()

        assert result == []

    def test_db_discovery_failure_falls_back_to_registry(self):
        """If DB slug query fails, falls back to GATEWAY_REGISTRY keys."""
        mock_registry = {
            "openai": {"name": "OpenAI"},
            "anthropic": {"name": "Anthropic"},
            "openrouter": {"name": "OpenRouter"},
        }

        with (
            patch(
                "src.config.supabase_config.get_client_for_query", side_effect=Exception("DB down")
            ),
            patch("src.routes.catalog.GATEWAY_REGISTRY", mock_registry),
            patch(
                "src.services.model_catalog_cache.get_cached_provider_catalog",
                side_effect=_provider_catalog_side_effect,
            ) as mock_fetch,
            patch(
                "src.services.model_catalog_cache.get_model_catalog_cache", return_value=MagicMock()
            ),
            patch("src.services.local_memory_cache.set_local_catalog"),
        ):

            from src.services.model_catalog_cache import rebuild_full_catalog_from_providers

            result = rebuild_full_catalog_from_providers()

        assert len(result) == 10
        assert mock_fetch.call_count == 3

    def test_providers_with_zero_models_handled_gracefully(self):
        """Providers returning 0 models don't break the assembly."""
        mock_client = _mock_supabase_response(SAMPLE_PROVIDERS_DB)

        def _side_effect(slug):
            if slug == "openai":
                return _make_models("openai", 5)
            return []  # anthropic and openrouter return empty

        with (
            patch("src.config.supabase_config.get_client_for_query", return_value=mock_client),
            patch(
                "src.services.model_catalog_cache.get_cached_provider_catalog",
                side_effect=_side_effect,
            ),
            patch(
                "src.services.model_catalog_cache.get_model_catalog_cache", return_value=MagicMock()
            ),
            patch("src.services.local_memory_cache.set_local_catalog"),
        ):

            from src.services.model_catalog_cache import rebuild_full_catalog_from_providers

            result = rebuild_full_catalog_from_providers()

        assert len(result) == 5
        assert all(m["source_gateway"] == "openai" for m in result)

    def test_does_not_use_provider_slugs_with_overrides(self):
        """Fallback uses GATEWAY_REGISTRY.keys() not PROVIDER_SLUGS
        (which has huggingface->hug override that doesn't match DB slugs)."""
        mock_registry = {"huggingface": {"name": "Hugging Face"}}

        with (
            patch(
                "src.config.supabase_config.get_client_for_query", side_effect=Exception("DB down")
            ),
            patch("src.routes.catalog.GATEWAY_REGISTRY", mock_registry),
            patch(
                "src.services.model_catalog_cache.get_cached_provider_catalog",
                return_value=_make_models("huggingface", 3),
            ) as mock_fetch,
            patch(
                "src.services.model_catalog_cache.get_model_catalog_cache", return_value=MagicMock()
            ),
            patch("src.services.local_memory_cache.set_local_catalog"),
        ):

            from src.services.model_catalog_cache import rebuild_full_catalog_from_providers

            result = rebuild_full_catalog_from_providers()

        # Should call with "huggingface" (DB slug), not "hug" (PROVIDER_SLUGS override)
        mock_fetch.assert_called_once_with("huggingface")
        assert len(result) == 3


class TestRebuildIntegrationWithGetCachedFullCatalog:
    """Test that get_cached_full_catalog() correctly calls rebuild on cache miss."""

    def test_cache_hit_does_not_trigger_rebuild(self):
        """Redis cache hit returns immediately without rebuilding."""
        mock_cache = MagicMock()
        mock_cache.get_full_catalog.return_value = [{"id": "cached-model"}]

        with (
            patch(
                "src.services.model_catalog_cache.get_model_catalog_cache", return_value=mock_cache
            ),
            patch("src.services.local_memory_cache.set_local_catalog"),
            patch("src.services.local_memory_cache.get_local_catalog", return_value=(None, False)),
        ):

            from src.services.model_catalog_cache import get_cached_full_catalog

            result = get_cached_full_catalog()

        assert result == [{"id": "cached-model"}]

    def test_cache_miss_triggers_rebuild(self):
        """Cache miss triggers rebuild_full_catalog_from_providers."""
        mock_cache = MagicMock()
        mock_cache.get_full_catalog.return_value = None  # Cache miss

        with (
            patch(
                "src.services.model_catalog_cache.get_model_catalog_cache", return_value=mock_cache
            ),
            patch("src.services.local_memory_cache.get_local_catalog", return_value=(None, False)),
            patch("src.services.local_memory_cache.set_local_catalog"),
            patch(
                "src.services.model_catalog_cache.rebuild_full_catalog_from_providers",
                return_value=_make_models("openai", 5),
            ) as mock_rebuild,
        ):

            from src.services.model_catalog_cache import get_cached_full_catalog

            result = get_cached_full_catalog()

        mock_rebuild.assert_called_once()
        assert len(result) == 5
