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
