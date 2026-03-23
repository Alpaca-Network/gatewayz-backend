"""
Unit tests for _MODEL_ID_MAPPINGS in src.services.model_transformations.

Covers ~1000 entries across 23 providers:
- Structural validity of every mapping dict
- Type correctness of all keys and values
- Absence of no-op self-mappings on providers where every entry is a real
  transformation (fireworks, featherless, together, huggingface, near,
  clarifai, simplismart)
- Correct behaviour of get_model_id_mapping() for known providers
"""

import pytest

from src.services.model_transformations import (
    _MODEL_ID_MAPPINGS,
    get_model_id_mapping,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Providers whose mappings are expected to contain ONLY real transformations
# (i.e. no entry where key == value).  Providers that intentionally expose
# pass-through entries (e.g. openrouter, groq, google-vertex) are excluded.
_TRANSFORM_ONLY_PROVIDERS = {
    "fireworks",
    "featherless",
    "together",
    "huggingface",
    "near",
    "clarifai",
    "simplismart",
}

# A sample of well-known providers that must be present in the registry.
_KNOWN_PROVIDERS = [
    "fireworks",
    "openrouter",
    "featherless",
    "together",
    "huggingface",
    "groq",
    "google-vertex",
    "cerebras",
    "cloudflare-workers-ai",
    "xai",
    "alibaba-cloud",
    "clarifai",
    "simplismart",
    "near",
    "morpheus",
    "onerouter",
    "alpaca-network",
]


# ---------------------------------------------------------------------------
# 1.  _MODEL_ID_MAPPINGS is a non-empty dict
# ---------------------------------------------------------------------------


class TestModelIdMappingsTopLevel:
    def test_mappings_is_dict(self):
        assert isinstance(_MODEL_ID_MAPPINGS, dict)

    def test_mappings_is_non_empty(self):
        assert len(_MODEL_ID_MAPPINGS) > 0, "_MODEL_ID_MAPPINGS must not be empty"

    def test_expected_provider_count(self):
        # There should be at least 20 providers registered.
        assert (
            len(_MODEL_ID_MAPPINGS) >= 20
        ), f"Expected at least 20 providers, found {len(_MODEL_ID_MAPPINGS)}"

    def test_all_known_providers_present(self):
        for provider in _KNOWN_PROVIDERS:
            assert (
                provider in _MODEL_ID_MAPPINGS
            ), f"Provider '{provider}' missing from _MODEL_ID_MAPPINGS"


# ---------------------------------------------------------------------------
# 2.  Every per-provider mapping is a dict (possibly empty)
# ---------------------------------------------------------------------------


class TestPerProviderMappingType:
    @pytest.mark.parametrize("provider", list(_MODEL_ID_MAPPINGS.keys()))
    def test_provider_mapping_is_dict(self, provider):
        mapping = _MODEL_ID_MAPPINGS[provider]
        assert isinstance(
            mapping, dict
        ), f"Mapping for provider '{provider}' must be a dict, got {type(mapping)}"


# ---------------------------------------------------------------------------
# 3.  All keys and values are non-empty strings
# ---------------------------------------------------------------------------


class TestMappingKeyValueTypes:
    @pytest.mark.parametrize("provider", list(_MODEL_ID_MAPPINGS.keys()))
    def test_all_keys_are_non_empty_strings(self, provider):
        mapping = _MODEL_ID_MAPPINGS[provider]
        for key in mapping:
            assert (
                isinstance(key, str) and key
            ), f"Provider '{provider}': key {key!r} is not a non-empty string"

    @pytest.mark.parametrize("provider", list(_MODEL_ID_MAPPINGS.keys()))
    def test_all_values_are_non_empty_strings(self, provider):
        mapping = _MODEL_ID_MAPPINGS[provider]
        for key, value in mapping.items():
            assert isinstance(value, str) and value, (
                f"Provider '{provider}': value {value!r} for key {key!r} "
                "is not a non-empty string"
            )


# ---------------------------------------------------------------------------
# 4.  No self-mappings (key == value) on transform-only providers
# ---------------------------------------------------------------------------


class TestNoSelfMappings:
    @pytest.mark.parametrize(
        "provider",
        sorted(_TRANSFORM_ONLY_PROVIDERS & _MODEL_ID_MAPPINGS.keys()),
    )
    def test_no_self_mapping_entries(self, provider):
        """
        For providers that only do real transformations, every entry must
        change the model ID.  A mapping where key == value is a no-op and
        likely a mistake.
        """
        mapping = _MODEL_ID_MAPPINGS[provider]
        self_maps = [k for k, v in mapping.items() if k == v]
        assert not self_maps, (
            f"Provider '{provider}' has {len(self_maps)} no-op self-mapping(s): " f"{self_maps[:5]}"
        )


# ---------------------------------------------------------------------------
# 5.  get_model_id_mapping() returns correct types and contents
# ---------------------------------------------------------------------------


class TestGetModelIdMapping:
    def test_returns_dict_for_known_provider(self):
        result = get_model_id_mapping("fireworks")
        assert isinstance(result, dict)

    def test_returns_non_empty_dict_for_fireworks(self):
        result = get_model_id_mapping("fireworks")
        assert len(result) > 0

    def test_returns_non_empty_dict_for_openrouter(self):
        result = get_model_id_mapping("openrouter")
        assert len(result) > 0

    def test_returns_non_empty_dict_for_google_vertex(self):
        result = get_model_id_mapping("google-vertex")
        assert len(result) > 0

    def test_returns_non_empty_dict_for_cloudflare(self):
        result = get_model_id_mapping("cloudflare-workers-ai")
        assert len(result) > 0

    def test_returns_empty_dict_for_unknown_provider(self):
        result = get_model_id_mapping("nonexistent-provider-xyz")
        assert result == {}

    def test_return_value_matches_direct_lookup(self):
        for provider in _KNOWN_PROVIDERS:
            assert get_model_id_mapping(provider) == _MODEL_ID_MAPPINGS.get(
                provider, {}
            ), f"get_model_id_mapping('{provider}') does not match direct dict lookup"

    @pytest.mark.parametrize("provider", _KNOWN_PROVIDERS)
    def test_result_keys_are_strings(self, provider):
        for key in get_model_id_mapping(provider):
            assert isinstance(key, str) and key, (
                f"get_model_id_mapping('{provider}'): key {key!r} is not a " "non-empty string"
            )

    @pytest.mark.parametrize("provider", _KNOWN_PROVIDERS)
    def test_result_values_are_strings(self, provider):
        for key, value in get_model_id_mapping(provider).items():
            assert isinstance(value, str) and value, (
                f"get_model_id_mapping('{provider}'): value {value!r} for key "
                f"{key!r} is not a non-empty string"
            )
