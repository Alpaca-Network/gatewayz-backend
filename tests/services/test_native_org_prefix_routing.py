"""zai/ and meta/ model ids must route to their native adapters.

Regression: both prefixes fell through to OpenRouter, so every GLM and Muse
request failed once the OpenRouter key expired, even though the Z.AI and Meta
keys were valid. DB-backed caches are stubbed so this runs without Supabase.
"""

from unittest.mock import patch

import pytest

import src.services.model_transformations as mt


@pytest.fixture(autouse=True)
def _empty_db_caches():
    with (
        patch.object(mt, "get_aliases", return_value={}),
        patch.object(mt, "get_routing_rules", return_value={}),
        patch.object(mt, "get_provider_mappings", return_value={}),
        patch.object(mt, "get_provider_native_values", return_value=set()),
    ):
        yield


@pytest.mark.parametrize(
    ("model_id", "expected"),
    [
        ("zai/glm-5.3", "zai"),
        ("zai/glm-4.7", "zai"),
        ("z-ai/glm-4.6", "zai"),
        ("meta/muse-spark-1.3", "meta"),
        ("moonshot/kimi-k3", "moonshot"),
    ],
)
def test_native_org_prefix_routes_to_native_provider(model_id, expected):
    assert mt.detect_provider_from_model_id(model_id) == expected


def test_openrouter_suffix_still_wins_for_glm():
    # :exacto is an OpenRouter-only variant and must keep routing there.
    assert mt.detect_provider_from_model_id("z-ai/glm-4.6:exacto") == "openrouter"


@pytest.mark.parametrize(
    ("slug", "catalog_id", "upstream_id"),
    [
        ("zai", "zai/glm-5.3", "glm-5.3"),
        ("moonshot", "moonshot/kimi-k3", "kimi-k3"),
        ("meta", "meta/muse-spark-1.3", "muse-spark-1.3"),
    ],
)
def test_native_adapter_strips_catalog_prefix(slug, catalog_id, upstream_id):
    # Catalog ids carry the slug prefix; the upstream APIs only know bare ids.
    from src.services.providers.adapter_configs import ADAPTER_CONFIGS
    from src.services.providers.openai_compat import make_adapter

    assert make_adapter(ADAPTER_CONFIGS[slug])._resolve_model(catalog_id) == upstream_id
