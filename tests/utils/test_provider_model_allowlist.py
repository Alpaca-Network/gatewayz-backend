"""PROVIDER_MODEL_ALLOWLIST: a restricted provider lists and serves only the models it names."""

import json
from unittest.mock import patch

import pytest

from src.utils import provider_filter
from src.utils.provider_filter import (
    filter_chain_by_model_allowlist,
    filter_models_by_allowlist,
    get_model_allowlist,
    is_model_allowed,
)

ALLOWLIST = json.dumps(
    {"deepinfra": ["meta-llama/Llama-3.3-70B-Instruct", "deepseek-ai/DeepSeek-V3.1"]}
)


# Patched by dotted path: other tests reload src.config.config, so a Config
# class captured at import time here can be a stale copy.
@pytest.fixture
def allowlist(monkeypatch):
    monkeypatch.setattr(
        "src.config.config.Config.PROVIDER_MODEL_ALLOWLIST", ALLOWLIST, raising=False
    )
    monkeypatch.setattr(provider_filter, "_allowlist_cache", None)
    yield
    monkeypatch.setattr(provider_filter, "_allowlist_cache", None)


@pytest.fixture
def no_allowlist(monkeypatch):
    monkeypatch.setattr("src.config.config.Config.PROVIDER_MODEL_ALLOWLIST", "", raising=False)
    monkeypatch.setattr(provider_filter, "_allowlist_cache", None)
    yield
    monkeypatch.setattr(provider_filter, "_allowlist_cache", None)


DEEPINFRA_ROWS = [
    {"id": "deepinfra/meta-llama/Llama-3.3-70B-Instruct", "name": "Llama 3.3 70B"},
    {"id": "deepinfra/some-org/Unlisted-Model", "name": "Unlisted"},
    {"provider_model_id": "deepseek-ai/DeepSeek-V3.1", "id": "deepseek-v3.1"},
]


class TestUnset:
    def test_everything_allowed(self, no_allowlist):
        assert get_model_allowlist() is None
        assert is_model_allowed("deepinfra", "anything/at-all")

    def test_catalog_unchanged(self, no_allowlist):
        assert filter_models_by_allowlist("deepinfra", DEEPINFRA_ROWS) is DEEPINFRA_ROWS

    def test_chain_unchanged(self, no_allowlist):
        chain = ["deepinfra", "groq"]
        assert filter_chain_by_model_allowlist("some-org/unlisted-model", chain) == chain


class TestInvalid:
    @pytest.mark.parametrize("raw", ["not json", "[1, 2]", '"a string"', "{}", "   "])
    def test_invalid_or_empty_restricts_nothing(self, monkeypatch, raw):
        monkeypatch.setattr("src.config.config.Config.PROVIDER_MODEL_ALLOWLIST", raw, raising=False)
        monkeypatch.setattr(provider_filter, "_allowlist_cache", None)
        assert get_model_allowlist() is None
        assert is_model_allowed("deepinfra", "anything")

    def test_bad_entry_skipped_good_entry_kept(self, monkeypatch):
        raw = json.dumps({"deepinfra": "not-a-list", "groq": ["llama-3.3-70b-versatile"]})
        monkeypatch.setattr("src.config.config.Config.PROVIDER_MODEL_ALLOWLIST", raw, raising=False)
        monkeypatch.setattr(provider_filter, "_allowlist_cache", None)
        assert is_model_allowed("deepinfra", "anything")
        assert not is_model_allowed("groq", "qwen/qwen3-32b")


class TestMatching:
    def test_case_insensitive(self, allowlist):
        assert is_model_allowed("deepinfra", "META-LLAMA/llama-3.3-70b-instruct")

    def test_provider_prefix_tolerated(self, allowlist):
        assert is_model_allowed("deepinfra", "deepinfra/meta-llama/Llama-3.3-70B-Instruct")
        assert is_model_allowed("DeepInfra", "deepinfra/deepseek-ai/DeepSeek-V3.1")

    def test_unlisted_rejected(self, allowlist):
        assert not is_model_allowed("deepinfra", "some-org/Unlisted-Model")
        assert not is_model_allowed("deepinfra", None)

    def test_other_providers_unaffected(self, allowlist):
        assert is_model_allowed("groq", "some-org/Unlisted-Model")
        assert is_model_allowed("openai", "gpt-4o")


class TestCatalog:
    def test_restricted_provider_trimmed(self, allowlist):
        kept = filter_models_by_allowlist("deepinfra", DEEPINFRA_ROWS)
        assert [r.get("provider_model_id") or r["id"] for r in kept] == [
            "deepinfra/meta-llama/Llama-3.3-70B-Instruct",
            "deepseek-ai/DeepSeek-V3.1",
        ]

    def test_other_provider_catalog_untouched(self, allowlist):
        rows = [{"id": "groq/whatever"}]
        assert filter_models_by_allowlist("groq", rows) is rows

    def test_get_cached_provider_catalog_applies_allowlist(self, allowlist):
        from src.services.cache import model_catalog_cache

        with patch.object(
            model_catalog_cache,
            "_get_cached_provider_catalog_unfiltered",
            return_value=list(DEEPINFRA_ROWS),
        ):
            got = model_catalog_cache.get_cached_provider_catalog("deepinfra")
        assert len(got) == 2

    def test_get_cached_provider_catalog_none_passthrough(self, allowlist):
        from src.services.cache import model_catalog_cache

        with patch.object(
            model_catalog_cache, "_get_cached_provider_catalog_unfiltered", return_value=None
        ):
            assert model_catalog_cache.get_cached_provider_catalog("deepinfra") is None

    def test_cache_gateway_catalog_writes_trimmed(self, allowlist):
        from src.services.cache import model_catalog_cache

        class _Cache:
            written = None

            def set_gateway_catalog(self, name, catalog, ttl=None):
                _Cache.written = catalog
                return True

        with patch.object(model_catalog_cache, "get_model_catalog_cache", return_value=_Cache()):
            model_catalog_cache.cache_gateway_catalog("deepinfra", list(DEEPINFRA_ROWS))
        assert len(_Cache.written) == 2


class TestRouting:
    def test_unlisted_model_dropped_from_chain(self, allowlist):
        with patch(
            "src.services.model_transformations.transform_model_id",
            side_effect=lambda m, p, *a, **k: m,
        ):
            chain = filter_chain_by_model_allowlist(
                "some-org/unlisted-model", ["deepinfra", "groq", "together"]
            )
        assert chain == ["groq", "together"]

    def test_listed_model_kept(self, allowlist):
        chain = filter_chain_by_model_allowlist(
            "meta-llama/llama-3.3-70b-instruct", ["deepinfra", "groq"]
        )
        assert chain == ["deepinfra", "groq"]

    def test_native_id_after_transform_matches(self, allowlist):
        with patch(
            "src.services.model_transformations.transform_model_id",
            return_value="meta-llama/Llama-3.3-70B-Instruct",
        ):
            chain = filter_chain_by_model_allowlist("llama-3.3-70b", ["deepinfra"])
        assert chain == ["deepinfra"]

    def test_enforce_model_failover_rules_applies_allowlist(self, allowlist):
        from src.services.provider_failover import enforce_model_failover_rules

        with patch(
            "src.services.model_transformations.transform_model_id",
            side_effect=lambda m, p, *a, **k: m,
        ):
            chain = enforce_model_failover_rules(
                "some-org/unlisted-model", ["deepinfra", "together"]
            )
        assert "deepinfra" not in chain
        assert "together" in chain

    def test_enforce_model_failover_rules_unset_unchanged(self, no_allowlist):
        from src.services.provider_failover import enforce_model_failover_rules

        assert enforce_model_failover_rules("some-org/x", ["deepinfra", "together"]) == [
            "deepinfra",
            "together",
        ]
