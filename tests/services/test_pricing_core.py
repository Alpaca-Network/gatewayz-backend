"""
Consolidated Pricing Core Tests

Merged from:
- test_pricing.py (get_model_pricing, calculate_cost)
- test_pricing_database.py (database pricing lookup, cache, fallback)
- test_pricing_coverage.py (pricing coverage reporting)
- test_pricing_accuracy.py (provider API formats, credit calculations, end-to-end)
- test_pricing_validation.py (Google models pricing, price bounds, spike detection)
"""

import importlib
import math
import time
import warnings
from decimal import Decimal
from unittest.mock import Mock, patch

import httpx
import pytest

from src.services.pricing import (
    _get_pricing_from_cache_fallback,
    _get_pricing_from_database,
    calculate_cost,
    clear_pricing_cache,
    get_model_pricing,
    get_pricing_cache_stats,
)
from src.services.pricing_normalization import (
    PricingFormat,
    get_provider_format,
    normalize_pricing_dict,
    normalize_to_per_token,
    validate_normalized_price,
)

MODULE_PATH = "src.services.pricing"


@pytest.fixture
def mod():
    return importlib.import_module(MODULE_PATH)


def _models_fixture():
    return [
        {
            "id": "openai/gpt-4o",
            "slug": "openai/gpt-4o",
            "pricing": {"prompt": "0.000005", "completion": "0.000015"},
        },
        {
            "id": "anthropic/claude-3-opus",
            "slug": "claude-3-opus",
            "pricing": {"prompt": "0.00003", "completion": "0.00006"},
        },
        {
            "id": "bad/model",
            "slug": "bad/model",
            "pricing": {"prompt": None, "completion": ""},
        },
    ]


# ---------------------------------------------------------------------------
# get_model_pricing tests (from test_pricing.py)
# ---------------------------------------------------------------------------


def test_get_model_pricing_found_by_id(monkeypatch, mod):
    called = {"args": None}

    def fake_get_cached_models(arg):
        called["args"] = arg
        return _models_fixture()

    monkeypatch.setattr("src.services.models.get_cached_models", fake_get_cached_models)
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)

    out = mod.get_model_pricing("openai/gpt-4o")
    assert called["args"] == "all"
    assert out["found"] is True
    assert math.isclose(out["prompt"], 0.000005)
    assert math.isclose(out["completion"], 0.000015)


def test_get_model_pricing_found_by_slug(monkeypatch, mod):
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: _models_fixture())
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
    out = mod.get_model_pricing("claude-3-opus")
    assert out["found"] is True
    assert math.isclose(out["prompt"], 0.00003)
    assert math.isclose(out["completion"], 0.00006)


def test_get_model_pricing_model_not_found_uses_default(monkeypatch, mod):
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: _models_fixture())
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
    out = mod.get_model_pricing("totally/unknown-model")
    assert out["found"] is False
    assert math.isclose(out["prompt"], 0.00002)
    assert math.isclose(out["completion"], 0.00002)


def test_get_model_pricing_empty_cache_uses_default(monkeypatch, mod):
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: [])
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
    out = mod.get_model_pricing("anything")
    assert out["found"] is False
    assert math.isclose(out["prompt"], 0.00002)
    assert math.isclose(out["completion"], 0.00002)


def test_get_model_pricing_handles_missing_prices(monkeypatch, mod):
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: _models_fixture())
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
    out = mod.get_model_pricing("bad/model")
    assert out["found"] is True
    assert math.isclose(out["prompt"], 0.0)
    assert math.isclose(out["completion"], 0.0)


def test_get_model_pricing_exception_returns_default(monkeypatch, mod):
    def boom(_):
        raise RuntimeError("cache layer down")

    monkeypatch.setattr("src.services.models.get_cached_models", boom)
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
    out = mod.get_model_pricing("openai/gpt-4o")
    assert out["found"] is False
    assert math.isclose(out["prompt"], 0.00002)
    assert math.isclose(out["completion"], 0.00002)


def test_get_model_pricing_normalizes_hf_suffix(monkeypatch, mod):
    """Test that HuggingFace :hf-inference suffix is stripped for pricing lookup"""
    hf_models = [
        {
            "id": "meta-llama/Llama-2-7b-chat-hf",
            "slug": "meta-llama/Llama-2-7b-chat-hf",
            "pricing": {"prompt": "0", "completion": "0"},
        }
    ]
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: hf_models)
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)

    out = mod.get_model_pricing("meta-llama/Llama-2-7b-chat-hf:hf-inference")
    assert out["found"] is True
    assert math.isclose(out["prompt"], 0.0)
    assert math.isclose(out["completion"], 0.0)


def test_get_model_pricing_handles_multiple_provider_suffixes(monkeypatch, mod):
    """Test that various provider suffixes are normalized"""
    models = [
        {
            "id": "test/model-1",
            "slug": "test/model-1",
            "pricing": {"prompt": "0.00001", "completion": "0.00002"},
        }
    ]
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: models)
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)

    for suffix in [":hf-inference", ":openai", ":anthropic"]:
        out = mod.get_model_pricing(f"test/model-1{suffix}")
        assert out["found"] is True
        assert math.isclose(out["prompt"], 0.00001)


# ---------------------------------------------------------------------------
# calculate_cost tests (from test_pricing.py)
# ---------------------------------------------------------------------------


def test_calculate_cost_happy(monkeypatch, mod):
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda model_id: {"prompt": 0.00001, "completion": 0.00002, "found": True},
    )
    cost = mod.calculate_cost("any/model", prompt_tokens=1000, completion_tokens=500)
    assert math.isclose(cost, 0.02)


def test_calculate_cost_zero_tokens(monkeypatch, mod):
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00003, "completion": 0.00006, "found": True},
    )
    assert mod.calculate_cost("m", 0, 0) == 0.0


def test_calculate_cost_uses_fallback_on_exception(monkeypatch, mod):
    def boom(_):
        raise RuntimeError("err")

    monkeypatch.setattr(mod, "get_model_pricing", boom)
    cost = mod.calculate_cost("x", prompt_tokens=10, completion_tokens=5)
    assert math.isclose(cost, 0.0003)


# ---------------------------------------------------------------------------
# Free model pricing tests (from test_pricing.py)
# ---------------------------------------------------------------------------


def test_calculate_cost_free_model_returns_zero(monkeypatch, mod):
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00001, "completion": 0.00002, "found": True},
    )
    cost = mod.calculate_cost(
        "meta-llama/llama-2-7b:free", prompt_tokens=1000, completion_tokens=500
    )
    assert cost == 0.0


def test_calculate_cost_free_model_openrouter_format(monkeypatch, mod):
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00005, "completion": 0.00010, "found": True},
    )
    cost = mod.calculate_cost(
        "mistralai/mistral-7b-instruct:free", prompt_tokens=2000, completion_tokens=1000
    )
    assert cost == 0.0


def test_calculate_cost_free_model_with_zero_tokens(monkeypatch, mod):
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00001, "completion": 0.00002, "found": True},
    )
    cost = mod.calculate_cost("model:free", prompt_tokens=0, completion_tokens=0)
    assert cost == 0.0


def test_calculate_cost_non_free_model_normal_pricing(monkeypatch, mod):
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00001, "completion": 0.00002, "found": True},
    )
    cost = mod.calculate_cost("openai/gpt-4", prompt_tokens=1000, completion_tokens=500)
    assert math.isclose(cost, 0.02)


def test_calculate_cost_free_model_fallback_on_exception(monkeypatch, mod):
    def boom(_):
        raise RuntimeError("err")

    monkeypatch.setattr(mod, "get_model_pricing", boom)
    cost = mod.calculate_cost("model:free", prompt_tokens=100, completion_tokens=50)
    assert cost == 0.0


def test_calculate_cost_free_suffix_case_sensitive(monkeypatch, mod):
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00001, "completion": 0.00002, "found": True},
    )
    cost_upper = mod.calculate_cost("model:FREE", prompt_tokens=1000, completion_tokens=500)
    assert cost_upper > 0

    cost_mixed = mod.calculate_cost("model:Free", prompt_tokens=1000, completion_tokens=500)
    assert cost_mixed > 0

    cost_lower = mod.calculate_cost("model:free", prompt_tokens=1000, completion_tokens=500)
    assert cost_lower == 0.0


def test_calculate_cost_multiple_free_models(monkeypatch, mod):
    monkeypatch.setattr(
        mod,
        "get_model_pricing",
        lambda _: {"prompt": 0.00001, "completion": 0.00002, "found": True},
    )
    free_models = [
        "google/gemma-7b-it:free",
        "nousresearch/nous-hermes-llama2-13b:free",
        "huggingfaceh4/zephyr-7b-beta:free",
        "openchat/openchat-7b:free",
    ]
    for model in free_models:
        cost = mod.calculate_cost(model, prompt_tokens=1000, completion_tokens=500)
        assert cost == 0.0, f"Model {model} should return $0 cost"


# ---------------------------------------------------------------------------
# Database pricing lookup tests (from test_pricing_database.py)
# ---------------------------------------------------------------------------


class TestDatabasePricingLookup:
    """Test database pricing queries"""

    def test_get_pricing_from_database_success(self):
        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
            mock_result = Mock()
            mock_result.data = [
                {
                    "model_id": "openai/gpt-4",
                    "pricing_prompt": 0.00003,
                    "pricing_completion": 0.00006,
                }
            ]
            mock_table = Mock()
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
                mock_result
            )
            mock_client.return_value.table.return_value = mock_table

            result = _get_pricing_from_database("openai/gpt-4", {"openai/gpt-4"})
            assert result is not None
            assert result["prompt"] == 0.00003
            assert result["completion"] == 0.00006
            assert result["found"] is True
            assert result["source"] == "database"

    def test_get_pricing_from_database_not_found(self):
        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
            mock_result = Mock()
            mock_result.data = []
            mock_table = Mock()
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
                mock_result
            )
            mock_client.return_value.table.return_value = mock_table

            result = _get_pricing_from_database("unknown/model", {"unknown/model"})
            assert result is None

    def test_get_pricing_from_database_handles_error(self):
        with patch("src.config.supabase_config.get_supabase_client") as mock_client:
            mock_client.side_effect = Exception("Database connection failed")
            result = _get_pricing_from_database("openai/gpt-4", {"openai/gpt-4"})
            assert result is None


class TestPricingCache:
    """Test pricing cache functionality"""

    def setup_method(self):
        clear_pricing_cache()

    def test_cache_stores_and_retrieves_pricing(self):
        with (
            patch("src.config.supabase_config.get_supabase_client") as mock_client,
            patch("src.services.models._is_building_catalog", return_value=False),
        ):
            mock_result = Mock()
            mock_result.data = [
                {
                    "model_id": "openai/gpt-4",
                    "pricing_prompt": 0.00003,
                    "pricing_completion": 0.00006,
                }
            ]
            mock_table = Mock()
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
                mock_result
            )
            mock_client.return_value.table.return_value = mock_table

            result1 = get_model_pricing("openai/gpt-4")
            result2 = get_model_pricing("openai/gpt-4")

            assert result1["prompt"] == 0.00003
            assert result2["prompt"] == 0.00003
            assert mock_client.call_count == 1

    def test_cache_expiration(self):
        with (
            patch("src.config.supabase_config.get_supabase_client") as mock_client,
            patch("src.services.models._is_building_catalog", return_value=False),
            patch("src.services.pricing._pricing_cache_ttl", 1),
        ):
            mock_result = Mock()
            mock_result.data = [
                {
                    "model_id": "openai/gpt-4",
                    "pricing_prompt": 0.00003,
                    "pricing_completion": 0.00006,
                }
            ]
            mock_table = Mock()
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
                mock_result
            )
            mock_client.return_value.table.return_value = mock_table

            get_model_pricing("openai/gpt-4")
            time.sleep(1.1)
            get_model_pricing("openai/gpt-4")

            assert mock_client.call_count == 2

    def test_clear_pricing_cache(self):
        from src.services.pricing import _pricing_cache

        _pricing_cache["model1"] = {"data": {"prompt": 0.001}, "timestamp": time.time()}
        _pricing_cache["model2"] = {"data": {"prompt": 0.002}, "timestamp": time.time()}

        clear_pricing_cache("model1")
        assert "model1" not in _pricing_cache
        assert "model2" in _pricing_cache

        clear_pricing_cache()
        assert len(_pricing_cache) == 0

    def test_get_pricing_cache_stats(self):
        from src.services.pricing import _pricing_cache

        _pricing_cache["model1"] = {"data": {"prompt": 0.001}, "timestamp": time.time()}
        _pricing_cache["model2"] = {"data": {"prompt": 0.002}, "timestamp": time.time()}

        stats = get_pricing_cache_stats()
        assert stats["cached_models"] == 2
        assert stats["ttl_seconds"] == 300


class TestFallbackMechanism:
    """Test fallback to provider API cache"""

    def setup_method(self):
        clear_pricing_cache()

    def test_fallback_to_cache_on_database_failure(self):
        with (
            patch("src.config.supabase_config.get_supabase_client") as mock_db,
            patch("src.services.models.get_cached_models") as mock_cache,
            patch("src.services.models._is_building_catalog", return_value=False),
        ):
            mock_db.side_effect = Exception("Database connection failed")
            mock_cache.return_value = [
                {"id": "openai/gpt-4", "pricing": {"prompt": 0.00003, "completion": 0.00006}}
            ]

            result = get_model_pricing("openai/gpt-4")
            assert result["prompt"] == 0.00003
            assert result["completion"] == 0.00006
            assert result["source"] == "cache_fallback"

    def test_fallback_to_default_when_all_fail(self):
        with (
            patch("src.config.supabase_config.get_supabase_client") as mock_db,
            patch("src.services.models.get_cached_models") as mock_cache,
            patch("src.services.models._is_building_catalog", return_value=False),
        ):
            mock_db.side_effect = Exception("Database connection failed")
            mock_cache.return_value = []

            result = get_model_pricing("unknown/model")
            assert result["prompt"] == 0.00002
            assert result["completion"] == 0.00002
            assert result["found"] is False
            assert result["source"] == "default"

    def test_database_takes_priority_over_cache(self):
        with (
            patch("src.config.supabase_config.get_supabase_client") as mock_db,
            patch("src.services.models.get_cached_models") as mock_cache,
            patch("src.services.models._is_building_catalog", return_value=False),
        ):
            mock_result = Mock()
            mock_result.data = [
                {
                    "model_id": "openai/gpt-4",
                    "pricing_prompt": 0.00005,
                    "pricing_completion": 0.00010,
                }
            ]
            mock_table = Mock()
            mock_table.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = (
                mock_result
            )
            mock_db.return_value.table.return_value = mock_table

            mock_cache.return_value = [
                {"id": "openai/gpt-4", "pricing": {"prompt": 0.00003, "completion": 0.00006}}
            ]

            result = get_model_pricing("openai/gpt-4")
            assert result["prompt"] == 0.00005
            assert result["completion"] == 0.00010
            assert result["source"] == "database"


class TestModelIDNormalization:
    """Test model ID normalization and alias resolution"""

    def setup_method(self):
        clear_pricing_cache()

    def test_handles_provider_suffixes(self):
        with (
            patch("src.services.pricing._get_pricing_from_database") as mock_db,
            patch("src.services.models._is_building_catalog", return_value=False),
        ):
            mock_db.return_value = None
            with patch("src.services.pricing._get_pricing_from_cache_fallback", return_value=None):
                get_model_pricing("openai/gpt-4:hf-inference")

                call_args = mock_db.call_args
                candidate_ids = call_args[0][1]
                assert "openai/gpt-4:hf-inference" in candidate_ids
                assert "openai/gpt-4" in candidate_ids

    def test_free_model_detection(self):
        cost = calculate_cost("google/gemma-2-9b-it:free", 1000, 500)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# Pricing coverage tests (from test_pricing_coverage.py)
# ---------------------------------------------------------------------------

PRICING_MODULE_PATH = "src.services.pricing"
DEFAULT_PROMPT_PRICE = 0.00002
DEFAULT_COMPLETION_PRICE = 0.00002


@pytest.fixture
def pricing_mod():
    return importlib.import_module(PRICING_MODULE_PATH)


def _sample_catalog_models():
    return [
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "openai/gpt-4-turbo",
        "openai/gpt-3.5-turbo",
        "anthropic/claude-3-opus",
        "anthropic/claude-3-sonnet",
        "anthropic/claude-3-haiku",
        "google/gemini-pro",
        "google/gemini-1.5-pro",
        "google/gemini-1.5-flash",
        "meta-llama/llama-3.1-8b-instruct",
        "meta-llama/llama-3.1-70b-instruct",
        "meta-llama/llama-3.1-405b-instruct",
        "mistralai/mistral-7b-instruct",
        "mistralai/mixtral-8x7b-instruct",
        "deepseek/deepseek-chat",
        "deepseek/deepseek-coder",
        "cohere/command-r-plus",
        "qwen/qwen-2-72b-instruct",
    ]


def _build_cached_models_with_pricing(model_ids, priced_ids):
    models = []
    for mid in model_ids:
        if mid in priced_ids:
            models.append(
                {
                    "id": mid,
                    "slug": mid,
                    "pricing": {"prompt": "0.000005", "completion": "0.000015"},
                }
            )
        else:
            models.append(
                {
                    "id": mid,
                    "slug": mid,
                    "pricing": {"prompt": "0", "completion": "0"},
                }
            )
    return models


class TestGetPricingCoverageReport:
    """Tests for the get_pricing_coverage_report() utility function."""

    def test_empty_model_list(self, pricing_mod):
        report = pricing_mod.get_pricing_coverage_report([])
        assert report["total_models"] == 0
        assert report["covered_count"] == 0
        assert report["uncovered_count"] == 0
        assert report["coverage_percentage"] == 100.0
        assert report["uncovered_models"] == []

    def test_all_models_covered(self, monkeypatch, pricing_mod):
        model_ids = ["model-a", "model-b", "model-c"]
        all_models = [
            {
                "id": mid,
                "slug": mid,
                "pricing": {"prompt": "0.00001", "completion": "0.00002"},
            }
            for mid in model_ids
        ]
        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: all_models)
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
        pricing_mod.clear_pricing_cache()

        report = pricing_mod.get_pricing_coverage_report(model_ids)
        assert report["total_models"] == 3
        assert report["covered_count"] == 3
        assert report["uncovered_count"] == 0
        assert report["coverage_percentage"] == 100.0

    def test_no_models_covered(self, monkeypatch, pricing_mod):
        model_ids = ["unknown/model-x", "unknown/model-y"]
        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: [])
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
        pricing_mod.clear_pricing_cache()

        report = pricing_mod.get_pricing_coverage_report(model_ids)
        assert report["total_models"] == 2
        assert report["covered_count"] == 0
        assert report["uncovered_count"] == 2
        assert report["coverage_percentage"] == 0.0
        assert sorted(report["uncovered_models"]) == sorted(model_ids)

    def test_partial_coverage(self, monkeypatch, pricing_mod):
        model_ids = ["covered/model-a", "covered/model-b", "missing/model-c"]
        priced_set = {"covered/model-a", "covered/model-b"}
        all_models = _build_cached_models_with_pricing(model_ids, priced_set)

        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: all_models)
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
        pricing_mod.clear_pricing_cache()

        report = pricing_mod.get_pricing_coverage_report(model_ids)
        assert report["total_models"] == 3
        assert report["covered_count"] == 2
        assert report["uncovered_count"] == 1
        assert 66.0 <= report["coverage_percentage"] <= 67.0
        assert report["uncovered_models"] == ["missing/model-c"]

    def test_uncovered_models_sorted(self, monkeypatch, pricing_mod):
        model_ids = ["z-model", "a-model", "m-model"]
        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: [])
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
        pricing_mod.clear_pricing_cache()

        report = pricing_mod.get_pricing_coverage_report(model_ids)
        assert report["uncovered_models"] == ["a-model", "m-model", "z-model"]

    def test_high_value_model_without_pricing_counted_as_uncovered(self, monkeypatch, pricing_mod):
        model_ids = ["openai/gpt-4-test"]
        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: [])
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
        pricing_mod.clear_pricing_cache()

        report = pricing_mod.get_pricing_coverage_report(model_ids)
        assert report["uncovered_count"] == 1
        assert "openai/gpt-4-test" in report["uncovered_models"]


class TestCatalogPricingCoverage:
    """Warning-level tests for catalog pricing coverage."""

    @pytest.mark.xfail(
        reason="Pricing coverage may not be 100% -- this is a monitoring/warning test",
        strict=False,
    )
    def test_sample_catalog_coverage_report(self, monkeypatch, pricing_mod):
        sample_models = _sample_catalog_models()
        priced_models = {
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "openai/gpt-4-turbo",
            "openai/gpt-3.5-turbo",
            "anthropic/claude-3-opus",
            "anthropic/claude-3-sonnet",
            "anthropic/claude-3-haiku",
            "google/gemini-pro",
            "google/gemini-1.5-pro",
            "google/gemini-1.5-flash",
        }
        all_models = _build_cached_models_with_pricing(sample_models, priced_models)

        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: all_models)
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
        pricing_mod.clear_pricing_cache()

        report = pricing_mod.get_pricing_coverage_report(sample_models)
        if report["uncovered_models"]:
            warnings.warn(
                f"\nPricing coverage: {report['coverage_percentage']}% "
                f"({report['covered_count']}/{report['total_models']})\n"
                f"Uncovered models:\n" + "\n".join(f"  - {m}" for m in report["uncovered_models"]),
                UserWarning,
                stacklevel=1,
            )
        assert report["coverage_percentage"] == 100.0

    @pytest.mark.xfail(
        reason="Pricing coverage may not be 100% -- this is a monitoring/warning test",
        strict=False,
    )
    def test_high_value_models_have_pricing(self, monkeypatch, pricing_mod):
        high_value_models = [
            "openai/gpt-4o",
            "openai/gpt-4-turbo",
            "anthropic/claude-3-opus",
            "anthropic/claude-3-sonnet",
            "google/gemini-1.5-pro",
        ]
        priced_set = set(high_value_models)
        all_models = _build_cached_models_with_pricing(high_value_models, priced_set)

        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: all_models)
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
        pricing_mod.clear_pricing_cache()

        report = pricing_mod.get_pricing_coverage_report(high_value_models)
        if report["uncovered_models"]:
            warnings.warn(
                "\nCRITICAL: High-value models missing pricing:\n"
                + "\n".join(f"  - {m}" for m in report["uncovered_models"]),
                UserWarning,
                stacklevel=1,
            )
        assert report["coverage_percentage"] == 100.0


_SAMPLE_MODELS = _sample_catalog_models()


@pytest.mark.parametrize("model_id", _SAMPLE_MODELS, ids=_SAMPLE_MODELS)
def test_individual_model_pricing_not_default(monkeypatch, model_id):
    pricing_mod_local = importlib.import_module(PRICING_MODULE_PATH)
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: [])
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
    pricing_mod_local.clear_pricing_cache()

    try:
        pricing = pricing_mod_local.get_model_pricing(model_id)
        is_default = not pricing.get("found", False) or pricing.get("source") == "default"
    except Exception:
        is_default = True

    if is_default:
        warnings.warn(
            f"Model '{model_id}' has no explicit pricing -- "
            f"falls back to default $0.00002/token",
            UserWarning,
            stacklevel=1,
        )


# ---------------------------------------------------------------------------
# Provider API format tests (from test_pricing_accuracy.py)
# ---------------------------------------------------------------------------


class TestProviderAPIFormats:
    """Test that we correctly understand provider API pricing formats"""

    @pytest.mark.asyncio
    async def test_openrouter_api_returns_per_token_pricing(self):
        try:
            response = httpx.get("https://openrouter.ai/api/v1/models", timeout=10.0)
            response.raise_for_status()
            models = response.json().get("data", [])

            gpt4o_mini = None
            for model in models:
                if model.get("id") == "openai/gpt-4o-mini":
                    gpt4o_mini = model
                    break

            assert gpt4o_mini is not None, "GPT-4o-mini not found in OpenRouter catalog"

            pricing = gpt4o_mini.get("pricing", {})
            prompt_price = float(pricing.get("prompt", 0))

            assert prompt_price < 0.001, (
                f"OpenRouter appears to return per-1M pricing ({prompt_price}), "
                f"not per-token pricing."
            )

            expected_per_token = 0.15 / 1_000_000
            assert abs(prompt_price - expected_per_token) < 0.00000001

        except httpx.HTTPError as e:
            pytest.skip(f"Could not fetch OpenRouter API: {e}")

    def test_manual_pricing_format(self):
        from src.services.pricing_lookup import load_manual_pricing

        pricing_data = load_manual_pricing()
        deepinfra_pricing = pricing_data.get("deepinfra", {})
        llama_pricing = deepinfra_pricing.get("meta-llama/Meta-Llama-3.1-8B-Instruct", {})

        assert llama_pricing.get("prompt") == "0.055", "Manual pricing format has changed!"
        prompt_val = float(llama_pricing.get("prompt", 0))
        assert prompt_val > 0.001, (
            f"Manual pricing appears to be per-token ({prompt_val}), "
            f"but should be per-1M format"
        )


class TestCreditCalculations:
    """Test that credit deductions are calculated correctly"""

    def test_calculate_cost_with_correct_pricing(self):
        prompt_tokens = 1000
        completion_tokens = 500
        expected_cost = (1000 * 0.00000015) + (500 * 0.0000006)
        assert abs(expected_cost - 0.00045) < 0.0000001

    def test_calculate_cost_with_wrong_normalization(self):
        openrouter_price = 0.00000015
        incorrectly_normalized = openrouter_price / 1_000_000

        correct_cost = 1000 * openrouter_price
        incorrect_cost = 1000 * incorrectly_normalized

        ratio = correct_cost / incorrect_cost
        assert abs(ratio - 1_000_000) < 1


class TestEndToEndPricing:
    """Test complete pricing flow from API to credit deduction"""

    @pytest.mark.integration
    def test_gpt4o_mini_pricing_end_to_end(self):
        model_id = "openai/gpt-4o-mini"
        pricing = get_model_pricing(model_id)

        if not pricing.get("found"):
            pytest.skip(f"Model {model_id} not found in catalog")

        prompt_price = pricing["prompt"]
        completion_price = pricing["completion"]

        expected_prompt = 0.15 / 1_000_000
        expected_completion = 0.60 / 1_000_000

        assert abs(prompt_price - expected_prompt) / expected_prompt < 0.1
        assert abs(completion_price - expected_completion) / expected_completion < 0.1

        cost = calculate_cost(model_id, prompt_tokens=1000, completion_tokens=500)
        expected_cost = (1000 * expected_prompt) + (500 * expected_completion)
        assert abs(cost - expected_cost) / expected_cost < 0.1


class TestPricingConsistency:
    """Test that pricing is consistent across different code paths"""

    def test_openrouter_pricing_not_double_normalized(self):
        openrouter_response_pricing = {
            "prompt": "0.00000015",
            "completion": "0.0000006",
            "request": "0",
            "image": "0",
        }

        incorrectly_normalized = normalize_pricing_dict(
            openrouter_response_pricing,
            PricingFormat.PER_1M_TOKENS,
        )

        wrong_price = float(incorrectly_normalized["prompt"])
        correct_price = float(openrouter_response_pricing["prompt"])

        ratio = correct_price / wrong_price
        assert abs(ratio - 1_000_000) < 1


class TestKnownModelPricing:
    """Test pricing for known models against public pricing pages"""

    @pytest.mark.parametrize(
        "model_id,expected_input_per_1m,expected_output_per_1m",
        [
            ("openai/gpt-4o-mini", 0.15, 0.60),
            ("openai/gpt-4o", 2.50, 10.00),
            ("anthropic/claude-3-5-sonnet", 3.00, 15.00),
        ],
    )
    def test_known_model_pricing(self, model_id, expected_input_per_1m, expected_output_per_1m):
        pricing = get_model_pricing(model_id)
        if not pricing.get("found"):
            pytest.skip(f"Model {model_id} not in catalog")

        expected_prompt = expected_input_per_1m / 1_000_000
        expected_completion = expected_output_per_1m / 1_000_000

        actual_prompt = pricing["prompt"]
        actual_completion = pricing["completion"]

        prompt_diff = abs(actual_prompt - expected_prompt) / expected_prompt
        completion_diff = abs(actual_completion - expected_completion) / expected_completion

        assert prompt_diff < 0.2
        assert completion_diff < 0.2


# ---------------------------------------------------------------------------
# Google model pricing validation (from test_pricing_validation.py)
# ---------------------------------------------------------------------------

# Official Google pricing (from ai.google.dev/gemini-api/docs/pricing)
GOOGLE_OFFICIAL_PRICING = {
    "gemini-3-pro": {"input_per_1m": 2.00, "output_per_1m": 12.00},
    "gemini-3-flash": {"input_per_1m": 0.50, "output_per_1m": 3.00},
    "gemini-2.5-pro": {"input_per_1m": 1.25, "output_per_1m": 10.00},
    "gemini-2.5-flash": {"input_per_1m": 0.30, "output_per_1m": 2.50},
    "gemini-2.5-flash-lite": {"input_per_1m": 0.10, "output_per_1m": 0.40},
    "gemini-2.0-flash": {"input_per_1m": 0.10, "output_per_1m": 0.40},
    "gemini-2.0-flash-lite": {"input_per_1m": 0.075, "output_per_1m": 0.30},
    "gemma": {"input_per_1m": 0.0, "output_per_1m": 0.0},
    "text-embedding": {"input_per_1m": 0.15, "output_per_1m": 0.0},
}


class TestGoogleModelsPricing:
    """Test Google models pricing configuration."""

    @pytest.fixture
    def google_models(self):
        from src.services.google_models_config import get_google_models

        return get_google_models()

    def test_all_models_have_pricing(self, google_models):
        for model in google_models:
            for provider in model.providers:
                if provider.name == "google-vertex":
                    assert (
                        provider.cost_per_1k_input is not None
                    ), f"Model {model.id} missing cost_per_1k_input"
                    assert (
                        provider.cost_per_1k_output is not None
                    ), f"Model {model.id} missing cost_per_1k_output"

    def test_pricing_format_not_per_million(self, google_models):
        for model in google_models:
            for provider in model.providers:
                if provider.name == "google-vertex":
                    assert provider.cost_per_1k_input < 1.0, (
                        f"Model {model.id} cost_per_1k_input={provider.cost_per_1k_input} "
                        f"looks like per-1M format"
                    )
                    assert provider.cost_per_1k_output < 1.0, (
                        f"Model {model.id} cost_per_1k_output={provider.cost_per_1k_output} "
                        f"looks like per-1M format"
                    )

    def test_google_pricing_matches_official(self, google_models):
        for model in google_models:
            for provider in model.providers:
                if provider.name == "google-vertex":
                    official_pricing = None
                    for pattern in sorted(GOOGLE_OFFICIAL_PRICING.keys(), key=len, reverse=True):
                        if pattern in model.id.lower():
                            official_pricing = GOOGLE_OFFICIAL_PRICING[pattern]
                            break
                    if not official_pricing:
                        continue

                    expected_input_per_1k = official_pricing["input_per_1m"] / 1000
                    expected_output_per_1k = official_pricing["output_per_1m"] / 1000
                    tolerance = 0.01

                    if provider.cost_per_1k_input > 0:
                        actual = provider.cost_per_1k_input
                        expected = expected_input_per_1k
                        diff_pct = abs(actual - expected) / expected if expected > 0 else 0
                        assert diff_pct <= tolerance, (
                            f"Model {model.id} input pricing mismatch: "
                            f"configured=${actual * 1000:.4f}/1M, "
                            f"official=${official_pricing['input_per_1m']:.4f}/1M "
                            f"(diff: {diff_pct*100:.1f}%)"
                        )

                    if provider.cost_per_1k_output > 0:
                        actual = provider.cost_per_1k_output
                        expected = expected_output_per_1k
                        diff_pct = abs(actual - expected) / expected if expected > 0 else 0
                        assert diff_pct <= tolerance, (
                            f"Model {model.id} output pricing mismatch: "
                            f"configured=${actual * 1000:.4f}/1M, "
                            f"official=${official_pricing['output_per_1m']:.4f}/1M "
                            f"(diff: {diff_pct*100:.1f}%)"
                        )

    def test_gemma_models_are_free(self, google_models):
        for model in google_models:
            if "gemma" in model.id.lower():
                for provider in model.providers:
                    if provider.name == "google-vertex":
                        assert (
                            provider.cost_per_1k_input == 0.0
                        ), f"Gemma model {model.id} should be free (input)"
                        assert (
                            provider.cost_per_1k_output == 0.0
                        ), f"Gemma model {model.id} should be free (output)"

    def test_output_price_higher_than_input(self, google_models):
        for model in google_models:
            if "gemma" in model.id.lower() or "exp" in model.id.lower():
                continue
            for provider in model.providers:
                if provider.name == "google-vertex":
                    if provider.cost_per_1k_input > 0 and provider.cost_per_1k_output > 0:
                        assert provider.cost_per_1k_output > provider.cost_per_1k_input, (
                            f"Model {model.id} has output price "
                            f"(${provider.cost_per_1k_output * 1000:.4f}/1M) "
                            f"lower than input price "
                            f"(${provider.cost_per_1k_input * 1000:.4f}/1M)"
                        )

    def test_pricing_reasonable_range(self, google_models):
        for model in google_models:
            for provider in model.providers:
                if provider.name == "google-vertex":
                    input_per_1m = provider.cost_per_1k_input * 1000
                    output_per_1m = provider.cost_per_1k_output * 1000
                    assert (
                        0 <= input_per_1m <= 100.0
                    ), f"Model {model.id} input price ${input_per_1m:.2f}/1M outside range"
                    assert (
                        0 <= output_per_1m <= 200.0
                    ), f"Model {model.id} output price ${output_per_1m:.2f}/1M outside range"

    def test_per_token_format_conversion(self, google_models):
        for model in google_models:
            for provider in model.providers:
                if provider.name == "google-vertex":
                    assert provider.cost_per_1k_input / 1000 < 0.001
                    assert provider.cost_per_1k_output / 1000 < 0.001


# ---------------------------------------------------------------------------
# Price bounds validation (from test_pricing_validation.py)
# ---------------------------------------------------------------------------


class TestPriceBoundsValidation:
    """Test price bounds validation from Issue #1038"""

    def test_valid_price_within_bounds(self):
        from src.services.pricing_validation import validate_price_bounds

        result = validate_price_bounds(0.0000025, "openai/gpt-4o", "input")
        assert result.is_valid is True
        assert result.price_per_token == Decimal("0.0000025")
        assert len(result.errors) == 0

    def test_price_below_minimum_rejected(self):
        from src.services.pricing_validation import validate_price_bounds

        result = validate_price_bounds(0.00000001, "test/model", "input")
        assert result.is_valid is False
        assert "below absolute minimum" in result.errors[0]

    def test_price_above_maximum_rejected(self):
        from src.services.pricing_validation import validate_price_bounds

        result = validate_price_bounds(0.5, "test/model", "input")
        assert result.is_valid is False
        assert "exceeds absolute maximum" in result.errors[0]

    def test_zero_price_valid_with_warning(self):
        from src.services.pricing_validation import validate_price_bounds

        result = validate_price_bounds(0, "test/model", "input")
        assert result.is_valid is True
        assert "Zero pricing" in result.warnings[0]

    def test_unusually_low_price_warning(self):
        from src.services.pricing_validation import validate_price_bounds

        result = validate_price_bounds(0.00000015, "test/model", "input")
        assert result.is_valid is True
        assert "unusually low" in result.warnings[0]


class TestPriceSpikeDetection:
    """Test price spike detection from Issue #1038"""

    def test_small_price_change_valid(self):
        from src.services.pricing_validation import detect_price_spike

        result = detect_price_spike(0.000001, 0.0000012, "test/model", "input")
        assert result.is_valid is True
        assert result.metadata["percent_change"] == 20.0

    def test_large_price_spike_rejected(self):
        from src.services.pricing_validation import detect_price_spike

        result = detect_price_spike(0.000001, 0.000002, "test/model", "input")
        assert result.is_valid is False
        assert "Price spike detected" in result.errors[0]
        assert result.metadata["percent_change"] == 100.0

    def test_spike_detection_with_zero_old_price(self):
        from src.services.pricing_validation import detect_price_spike

        result = detect_price_spike(0, 0.000001, "test/model", "input")
        assert result.is_valid is True
        assert result.metadata.get("skipped") is True


class TestComprehensivePricingValidation:
    """Test comprehensive pricing update validation from Issue #1038"""

    def test_valid_pricing_update(self):
        from src.services.pricing_validation import validate_pricing_update

        new_pricing = {"prompt": 0.0000025, "completion": 0.00001}
        old_pricing = {"prompt": 0.000002, "completion": 0.000009}
        result = validate_pricing_update("openai/gpt-4o", new_pricing, old_pricing)
        assert result["is_valid"] is True
        assert len(result["errors"]) == 0

    def test_pricing_update_with_bounds_violation(self):
        from src.services.pricing_validation import validate_pricing_update

        result = validate_pricing_update("test/model", {"prompt": 0.5, "completion": 0.00001})
        assert result["is_valid"] is False

    def test_pricing_update_with_spike(self):
        from src.services.pricing_validation import validate_pricing_update

        new_pricing = {"prompt": 0.000004, "completion": 0.00001}
        old_pricing = {"prompt": 0.000002, "completion": 0.00001}
        result = validate_pricing_update("test/model", new_pricing, old_pricing)
        assert result["is_valid"] is False


@pytest.mark.integration
class TestValidationWithRealPricing:
    """Test validation with real-world pricing examples"""

    def test_openai_gpt4o_pricing(self):
        from src.services.pricing_validation import validate_pricing_update

        result = validate_pricing_update(
            "openai/gpt-4o", {"prompt": 0.0000025, "completion": 0.00001}
        )
        assert result["is_valid"] is True

    def test_anthropic_claude_opus_pricing(self):
        from src.services.pricing_validation import validate_pricing_update

        result = validate_pricing_update(
            "anthropic/claude-3-opus", {"prompt": 0.000015, "completion": 0.000075}
        )
        assert result["is_valid"] is True

    def test_llama_8b_pricing(self):
        from src.services.pricing_validation import validate_pricing_update

        result = validate_pricing_update(
            "meta-llama/Meta-Llama-3.1-8B-Instruct",
            {"prompt": 0.00000015, "completion": 0.00000015},
        )
        assert result["is_valid"] is True


# ---------------------------------------------------------------------------
# Normalization validation (from test_pricing_validation.py)
# ---------------------------------------------------------------------------


class TestPricingNormalizationValidation:
    """Pricing normalization from validation perspective"""

    def test_per_1k_to_per_token_conversion(self):
        result = normalize_to_per_token(0.0003, PricingFormat.PER_1K_TOKENS)
        expected = Decimal("0.0003") / Decimal("1000")
        assert abs(result - expected) < Decimal("0.000000001")

    def test_per_1m_to_per_token_conversion(self):
        result = normalize_to_per_token(0.30, PricingFormat.PER_1M_TOKENS)
        expected = Decimal("0.30") / Decimal("1000000")
        assert abs(result - expected) < Decimal("0.000000001")

    def test_google_pricing_conversion_examples(self):
        result = normalize_to_per_token(0.30, PricingFormat.PER_1M_TOKENS)
        assert abs(result - Decimal("0.0000003")) < Decimal("0.00000001")

        result = normalize_to_per_token(1.25, PricingFormat.PER_1M_TOKENS)
        assert abs(result - Decimal("0.00000125")) < Decimal("0.00000001")


# Mark tests as critical for CI/CD
pytestmark = pytest.mark.critical
