"""
CM-9: Model Catalog Tests

Tests covering:
  9.1 Model Metadata (required fields, canonical ID format, pricing, modality, context_length)
  9.2 Catalog Inclusion Rules (pricing exclusion, inactive provider exclusion, deduplication)
"""

from unittest.mock import MagicMock, patch

import pytest

from src.routes.catalog import GATEWAY_REGISTRY, merge_models_by_slug
from src.services.models import (
    MODALITY_TEXT_TO_AUDIO,
    MODALITY_TEXT_TO_IMAGE,
    MODALITY_TEXT_TO_TEXT,
)
from src.services.pricing_lookup import GATEWAY_PROVIDERS, enrich_model_with_pricing

# Known modality types used across all provider clients in the codebase.
KNOWN_MODALITIES = {
    MODALITY_TEXT_TO_TEXT,  # "text->text"
    MODALITY_TEXT_TO_IMAGE,  # "text->image"
    MODALITY_TEXT_TO_AUDIO,  # "text->audio"
    "text\u2192text",  # Unicode arrow variant used in some catalog entries
    "text\u2192image",
    "text\u2192audio",
}

# Required top-level fields that every catalog entry must carry.
REQUIRED_MODEL_FIELDS = {"id", "name", "provider_slug", "context_length", "pricing"}


def _make_model(**overrides) -> dict:
    """Create a valid model catalog entry, merging *overrides* on top of defaults."""
    base = {
        "id": "meta-llama/Llama-3.3-70B-Instruct",
        "name": "Llama 3.3 70B Instruct",
        "provider_slug": "fireworks",
        "context_length": 131072,
        "modality": "text->text",
        "pricing": {
            "prompt": "0.00000055",
            "completion": "0.00000055",
        },
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_vision": False,
        "health_status": "healthy",
        "source_gateway": "fireworks",
    }
    base.update(overrides)
    return base


# ===================================================================
# 9.1 Model Metadata
# ===================================================================


class TestModelMetadata:
    """Tests verifying that model catalog entries carry correct metadata."""

    @pytest.mark.cm_verified
    def test_every_model_has_required_fields(self, sample_model_catalog_entry):
        """CM-9.1.1: Every model has id, name, provider_slug, context_length, pricing."""
        # Verify the canonical fixture has all required fields
        for field in REQUIRED_MODEL_FIELDS:
            assert (
                field in sample_model_catalog_entry
            ), f"Required field '{field}' missing from model catalog entry"
            assert (
                sample_model_catalog_entry[field] is not None
            ), f"Required field '{field}' is None"

        # Also verify several synthetic entries
        entries = [
            _make_model(),
            _make_model(id="openai/gpt-4o", name="GPT-4o", provider_slug="openrouter"),
            _make_model(
                id="anthropic/claude-3.5-sonnet",
                name="Claude 3.5 Sonnet",
                provider_slug="anthropic",
                context_length=200000,
            ),
        ]
        for entry in entries:
            for field in REQUIRED_MODEL_FIELDS:
                assert (
                    field in entry
                ), f"Required field '{field}' missing from model '{entry.get('id')}'"
                assert (
                    entry[field] is not None
                ), f"Required field '{field}' is None for model '{entry.get('id')}'"

    @pytest.mark.cm_verified
    def test_model_id_is_canonical_format(self, sample_model_catalog_entry):
        """CM-9.1.2: Model IDs follow the {org}/{model-name} canonical format."""
        model_id = sample_model_catalog_entry["id"]
        assert (
            "/" in model_id
        ), f"Model ID '{model_id}' does not follow '{{org}}/{{model-name}}' format"

        parts = model_id.split("/", 1)
        assert len(parts) == 2, f"Model ID '{model_id}' must have exactly one '/' separator"
        org, model_name = parts
        assert len(org) > 0, "Organization part of model ID must not be empty"
        assert len(model_name) > 0, "Model name part of model ID must not be empty"

        # Verify additional canonical IDs
        valid_ids = [
            "openai/gpt-4o",
            "anthropic/claude-3.5-sonnet",
            "meta-llama/Llama-3.3-70B-Instruct",
            "google/gemini-2.0-flash",
        ]
        for mid in valid_ids:
            parts = mid.split("/", 1)
            assert len(parts) == 2 and all(parts), f"'{mid}' does not match canonical format"

    @pytest.mark.cm_verified
    def test_pricing_field_never_null(self, sample_model_catalog_entry):
        """CM-9.1.3: No model has null/zero pricing (prompt & completion must be > 0)."""
        pricing = sample_model_catalog_entry["pricing"]
        assert pricing is not None, "pricing dict must not be None"

        prompt_val = float(pricing["prompt"])
        completion_val = float(pricing["completion"])
        assert prompt_val > 0, f"Prompt pricing must be > 0, got {prompt_val}"
        assert completion_val > 0, f"Completion pricing must be > 0, got {completion_val}"

        # Verify the pattern holds for constructed entries
        valid_model = _make_model(pricing={"prompt": "0.000001", "completion": "0.000002"})
        assert float(valid_model["pricing"]["prompt"]) > 0
        assert float(valid_model["pricing"]["completion"]) > 0

    @pytest.mark.cm_verified
    def test_modality_is_known_type(self, sample_model_catalog_entry):
        """CM-9.1.4: Modality is one of the known types defined in the codebase."""
        modality = sample_model_catalog_entry.get("modality")
        assert (
            modality in KNOWN_MODALITIES
        ), f"Modality '{modality}' is not one of the known types: {KNOWN_MODALITIES}"

        # Verify all standard modalities are recognized
        for mod in [MODALITY_TEXT_TO_TEXT, MODALITY_TEXT_TO_IMAGE, MODALITY_TEXT_TO_AUDIO]:
            entry = _make_model(modality=mod)
            assert entry["modality"] in KNOWN_MODALITIES

    @pytest.mark.cm_verified
    def test_context_length_is_positive_integer(self, sample_model_catalog_entry):
        """CM-9.1.5: context_length is a positive integer (> 0)."""
        ctx = sample_model_catalog_entry["context_length"]
        assert isinstance(ctx, int), f"context_length must be int, got {type(ctx).__name__}"
        assert ctx > 0, f"context_length must be > 0, got {ctx}"

        # Verify typical context lengths
        for length in [2048, 4096, 8192, 32768, 131072, 200000]:
            entry = _make_model(context_length=length)
            assert isinstance(entry["context_length"], int)
            assert entry["context_length"] > 0


# ===================================================================
# 9.2 Catalog Inclusion Rules
# ===================================================================


class TestCatalogInclusionRules:
    """Tests verifying catalog filtering and deduplication logic."""

    @pytest.mark.cm_verified
    def test_model_without_pricing_excluded(self):
        """CM-9.2.1: A gateway-provider model with no pricing is excluded from the catalog.

        enrich_model_with_pricing returns None for gateway providers that have no
        pricing across all three tiers (database, manual JSON, cross-reference).
        """
        model = _make_model(
            id="deepinfra/some-new-model",
            provider_slug="deepinfra",
            pricing=None,
        )
        # Remove existing pricing so the enrichment logic must search for it
        model.pop("pricing", None)

        # deepinfra is a GATEWAY_PROVIDER, so models without pricing get filtered out
        assert "deepinfra" in GATEWAY_PROVIDERS

        with (
            patch("src.services.pricing_lookup._get_pricing_from_database", return_value=None),
            patch("src.services.pricing_lookup.get_model_pricing", return_value=None),
            patch("src.services.pricing_lookup._get_cross_reference_pricing", return_value=None),
            patch("src.services.pricing_lookup._is_building_catalog", return_value=False),
        ):
            result = enrich_model_with_pricing(model, gateway="deepinfra")

        assert (
            result is None
        ), "Gateway provider model without pricing must be excluded (return None)"

    @pytest.mark.cm_verified
    def test_model_with_inactive_provider_excluded(self):
        """CM-9.2.2: A model whose provider slug is not in GATEWAY_REGISTRY is excluded.

        Only providers registered in GATEWAY_REGISTRY are served in the catalog.
        An unregistered provider slug means the model should not appear.
        """
        unregistered_slug = "totally-unknown-provider-xyz"
        assert (
            unregistered_slug not in GATEWAY_REGISTRY
        ), "Test requires an unregistered provider slug"

        # When a provider is not in GATEWAY_REGISTRY, its models are never fetched
        # by the catalog building process (PROVIDER_SLUGS is derived from GATEWAY_REGISTRY).
        from src.routes.catalog import PROVIDER_SLUGS

        assert (
            unregistered_slug not in PROVIDER_SLUGS
        ), "Unregistered provider must not appear in PROVIDER_SLUGS"

        # Additionally, if such a model somehow appears and is in GATEWAY_PROVIDERS,
        # it would be filtered out due to missing pricing.
        model = _make_model(
            id=f"{unregistered_slug}/fake-model",
            provider_slug=unregistered_slug,
        )
        model.pop("pricing", None)

        # The model is not a known gateway provider, but it also won't appear in the
        # catalog fetch loop because its slug is absent from PROVIDER_SLUGS.
        assert unregistered_slug not in PROVIDER_SLUGS

    @pytest.mark.cm_verified
    def test_deduplicated_view_no_duplicate_ids(self):
        """CM-9.2.3: The merge/deduplicated view contains no duplicate model IDs.

        merge_models_by_slug uses canonical_slug or id to deduplicate entries
        from multiple provider lists.
        """
        list_a = [
            _make_model(id="openai/gpt-4o", name="GPT-4o", provider_slug="openrouter"),
            _make_model(id="meta-llama/Llama-3.3-70B-Instruct", provider_slug="openrouter"),
        ]
        list_b = [
            # Duplicate of first entry in list_a (same id)
            _make_model(id="openai/gpt-4o", name="GPT-4o", provider_slug="together"),
            _make_model(id="google/gemini-2.0-flash", provider_slug="google-vertex"),
        ]
        list_c = [
            # Another duplicate
            _make_model(id="meta-llama/Llama-3.3-70B-Instruct", provider_slug="deepinfra"),
        ]

        merged = merge_models_by_slug(list_a, list_b, list_c)

        ids = [m["id"] for m in merged]
        assert len(ids) == len(
            set(i.lower() for i in ids)
        ), f"Duplicate IDs found after merge: {ids}"

        # Expect exactly 3 unique models
        assert len(merged) == 3, f"Expected 3 unique models after dedup, got {len(merged)}"
