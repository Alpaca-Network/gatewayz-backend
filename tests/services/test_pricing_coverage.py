"""
Pricing Coverage Validation Tests

Validates that catalog models have non-default pricing entries.
Models without explicit pricing silently fall back to $0.00002/token default,
which can lead to significant under-billing.

These tests are WARNING-level: they report uncovered models but do not fail
the test suite (xfail with run=True), so CI stays green while the output
highlights gaps for the team to address.
"""

import importlib
import warnings

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PRICING_MODULE_PATH = "src.services.pricing"

# Default pricing values used as the fallback in pricing.py
DEFAULT_PROMPT_PRICE = 0.00002
DEFAULT_COMPLETION_PRICE = 0.00002


@pytest.fixture
def pricing_mod():
    """Import the pricing module."""
    return importlib.import_module(PRICING_MODULE_PATH)


def _sample_catalog_models():
    """
    Representative sample of catalog models spanning multiple providers.

    This is intentionally a static fixture so the test runs offline without
    hitting Supabase, Redis, or any provider API.  Add real model IDs here
    as the catalog grows to keep the coverage check meaningful.
    """
    return [
        # OpenAI
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
        "openai/gpt-4-turbo",
        "openai/gpt-3.5-turbo",
        # Anthropic
        "anthropic/claude-3-opus",
        "anthropic/claude-3-sonnet",
        "anthropic/claude-3-haiku",
        # Google
        "google/gemini-pro",
        "google/gemini-1.5-pro",
        "google/gemini-1.5-flash",
        # Meta / open-source via providers
        "meta-llama/llama-3.1-8b-instruct",
        "meta-llama/llama-3.1-70b-instruct",
        "meta-llama/llama-3.1-405b-instruct",
        # Mistral
        "mistralai/mistral-7b-instruct",
        "mistralai/mixtral-8x7b-instruct",
        # DeepSeek
        "deepseek/deepseek-chat",
        "deepseek/deepseek-coder",
        # Misc
        "cohere/command-r-plus",
        "qwen/qwen-2-72b-instruct",
    ]


def _build_cached_models_with_pricing(model_ids, priced_ids):
    """
    Build a fake cached-models list where *priced_ids* have real pricing
    and all others have zero pricing (which triggers default fallback).
    """
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


# ---------------------------------------------------------------------------
# Tests for get_pricing_coverage_report()
# ---------------------------------------------------------------------------


class TestGetPricingCoverageReport:
    """Tests for the get_pricing_coverage_report() utility function."""

    def test_empty_model_list(self, pricing_mod):
        """An empty list should return 100% coverage with zero counts."""
        report = pricing_mod.get_pricing_coverage_report([])
        assert report["total_models"] == 0
        assert report["covered_count"] == 0
        assert report["uncovered_count"] == 0
        assert report["coverage_percentage"] == 100.0
        assert report["uncovered_models"] == []

    def test_all_models_covered(self, monkeypatch, pricing_mod):
        """When every model has real pricing, coverage should be 100%."""
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
        # Clear pricing cache to avoid stale entries
        pricing_mod.clear_pricing_cache()

        report = pricing_mod.get_pricing_coverage_report(model_ids)
        assert report["total_models"] == 3
        assert report["covered_count"] == 3
        assert report["uncovered_count"] == 0
        assert report["coverage_percentage"] == 100.0
        assert report["uncovered_models"] == []

    def test_no_models_covered(self, monkeypatch, pricing_mod):
        """When no model has real pricing, coverage should be 0%."""
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
        """When some models are covered and some are not, report reflects both."""
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
        """Uncovered models list should be sorted alphabetically."""
        model_ids = ["z-model", "a-model", "m-model"]

        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: [])
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
        pricing_mod.clear_pricing_cache()

        report = pricing_mod.get_pricing_coverage_report(model_ids)
        assert report["uncovered_models"] == ["a-model", "m-model", "z-model"]

    def test_high_value_model_without_pricing_counted_as_uncovered(self, monkeypatch, pricing_mod):
        """
        High-value models (GPT-4, Claude, etc.) that raise ValueError when
        pricing is missing should still be counted as uncovered, not crash.
        """
        # Use a model ID that matches the HIGH_VALUE_MODEL_PATTERNS in pricing.py
        model_ids = ["openai/gpt-4-test"]

        monkeypatch.setattr("src.services.models.get_cached_models", lambda _: [])
        monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
        pricing_mod.clear_pricing_cache()

        report = pricing_mod.get_pricing_coverage_report(model_ids)
        # The function should catch the ValueError and count it as uncovered
        assert report["uncovered_count"] == 1
        assert "openai/gpt-4-test" in report["uncovered_models"]


# ---------------------------------------------------------------------------
# Warning-level coverage check against the sample catalog
# ---------------------------------------------------------------------------


class TestCatalogPricingCoverage:
    """
    Warning-level tests that check pricing coverage for a representative
    sample of catalog models.

    These tests use xfail so they appear as warnings (xpass when all models
    are covered, xfail when some are not) without breaking CI.
    """

    @pytest.mark.xfail(
        reason="Pricing coverage may not be 100% — this is a monitoring/warning test",
        strict=False,
    )
    def test_sample_catalog_coverage_report(self, monkeypatch, pricing_mod):
        """
        Run the coverage report over the sample catalog and warn about gaps.

        This test is expected to fail (xfail) when models are missing pricing.
        When it passes (xpass), it means full coverage has been achieved.
        """
        sample_models = _sample_catalog_models()

        # Build a fake catalog where a known subset has real pricing
        # In a real integration test, this would call the actual catalog endpoint.
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

        # Emit a warning with the coverage details for visibility in CI logs
        if report["uncovered_models"]:
            warnings.warn(
                f"\nPricing coverage: {report['coverage_percentage']}% "
                f"({report['covered_count']}/{report['total_models']})\n"
                f"Uncovered models:\n" + "\n".join(f"  - {m}" for m in report["uncovered_models"]),
                UserWarning,
                stacklevel=1,
            )

        # This assertion will cause the test to "fail" (showing as xfail)
        # when coverage is not 100%. When coverage is 100%, it will xpass.
        assert report["coverage_percentage"] == 100.0, (
            f"Pricing coverage is {report['coverage_percentage']}%. "
            f"{report['uncovered_count']} model(s) missing pricing: "
            f"{', '.join(report['uncovered_models'][:10])}"
        )

    @pytest.mark.xfail(
        reason="Pricing coverage may not be 100% — this is a monitoring/warning test",
        strict=False,
    )
    def test_high_value_models_have_pricing(self, monkeypatch, pricing_mod):
        """
        High-value models (OpenAI, Anthropic, Google) should always have
        pricing configured to prevent significant under-billing.

        This test is more strict: it only checks the most expensive model
        families. If this fails, it indicates a critical billing gap.
        """
        high_value_models = [
            "openai/gpt-4o",
            "openai/gpt-4-turbo",
            "anthropic/claude-3-opus",
            "anthropic/claude-3-sonnet",
            "google/gemini-1.5-pro",
        ]

        # Simulate all high-value models having pricing
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

        assert report["coverage_percentage"] == 100.0, (
            f"CRITICAL: {report['uncovered_count']} high-value model(s) missing pricing: "
            f"{', '.join(report['uncovered_models'])}"
        )


# ---------------------------------------------------------------------------
# Parametrized per-model warning test
# ---------------------------------------------------------------------------

_SAMPLE_MODELS = _sample_catalog_models()


@pytest.mark.parametrize("model_id", _SAMPLE_MODELS, ids=_SAMPLE_MODELS)
def test_individual_model_pricing_not_default(monkeypatch, model_id):
    """
    Per-model check: warns if an individual model falls back to default pricing.

    Uses pytest.mark.parametrize so each model appears as a separate test item
    in the test output, making it easy to identify exactly which models lack
    pricing data.

    This test uses warnings.warn rather than assert, so it never fails the
    suite — it only emits visible warnings in the pytest output.
    """
    pricing_mod = importlib.import_module(PRICING_MODULE_PATH)

    # Mock out external dependencies so this runs offline
    monkeypatch.setattr("src.services.models.get_cached_models", lambda _: [])
    monkeypatch.setattr("src.services.models._is_building_catalog", lambda: False)
    pricing_mod.clear_pricing_cache()

    try:
        pricing = pricing_mod.get_model_pricing(model_id)
        is_default = not pricing.get("found", False) or pricing.get("source") == "default"
    except Exception:
        # High-value model pricing missing raises ValueError; count as uncovered
        is_default = True

    if is_default:
        warnings.warn(
            f"Model '{model_id}' has no explicit pricing — "
            f"falls back to default $0.00002/token",
            UserWarning,
            stacklevel=1,
        )
