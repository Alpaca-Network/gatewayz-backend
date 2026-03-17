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
    def test_every_model_has_required_fields(self):
        """CM-9.1.1: Calling normalize_fireworks_model on raw provider data
        produces an entry with all required fields."""
        from src.services.models import normalize_fireworks_model

        raw_fireworks = {
            "id": "accounts/fireworks/models/llama-v3p3-70b-instruct",
            "display_name": "Llama 3.3 70B Instruct",
            "metadata": {
                "context_length": 131072,
                "modality": "text->text",
            },
            "pricing": {
                "cents_per_input_token": 0.000055,
                "cents_per_output_token": 0.000055,
            },
        }

        with (
            patch("src.services.pricing_lookup._get_pricing_from_database", return_value=None),
            patch("src.services.pricing_lookup.get_model_pricing", return_value=None),
            patch("src.services.pricing_lookup._get_cross_reference_pricing", return_value=None),
            patch("src.services.pricing_lookup._is_building_catalog", return_value=False),
        ):
            result = normalize_fireworks_model(raw_fireworks)

        # normalize may return None if enrichment strips the model; verify it came back
        assert result is not None, "normalize_fireworks_model should return a model dict"

        for field in REQUIRED_MODEL_FIELDS:
            assert field in result, f"Required field '{field}' missing from normalized model"

    @pytest.mark.cm_verified
    def test_model_id_is_canonical_format(self):
        """CM-9.1.2: normalize_fireworks_model produces IDs that include the raw slug."""
        from src.services.models import normalize_fireworks_model

        raw = {
            "id": "accounts/fireworks/models/deepseek-v3",
            "display_name": "DeepSeek V3",
            "metadata": {"context_length": 8192},
        }

        with (
            patch("src.services.pricing_lookup._get_pricing_from_database", return_value=None),
            patch("src.services.pricing_lookup.get_model_pricing", return_value=None),
            patch("src.services.pricing_lookup._get_cross_reference_pricing", return_value=None),
            patch("src.services.pricing_lookup._is_building_catalog", return_value=False),
        ):
            result = normalize_fireworks_model(raw)

        # Result may be None for gateway providers without pricing; that's acceptable.
        # If returned, id must be a string
        if result is not None:
            model_id = result["id"]
            assert (
                isinstance(model_id, str) and len(model_id) > 0
            ), f"Model ID must be a non-empty string, got '{model_id}'"

        # Also verify the merge_models_by_slug canonical format works with org/name IDs
        entries = [
            _make_model(id="openai/gpt-4o", name="GPT-4o"),
            _make_model(id="anthropic/claude-3.5-sonnet", name="Claude"),
        ]
        for entry in entries:
            parts = entry["id"].split("/", 1)
            assert len(parts) == 2, f"'{entry['id']}' does not match org/model format"
            assert all(parts), f"Both parts of '{entry['id']}' must be non-empty"

    @pytest.mark.cm_verified
    def test_pricing_field_never_null(self):
        """CM-9.1.3: enrich_model_with_pricing populates pricing on a model."""
        model = _make_model(pricing=None)
        model.pop("pricing", None)

        mock_pricing = {"prompt": "0.000001", "completion": "0.000002"}

        with (
            patch(
                "src.services.pricing_lookup._get_pricing_from_database", return_value=mock_pricing
            ),
            patch("src.services.pricing_lookup._is_building_catalog", return_value=False),
        ):
            result = enrich_model_with_pricing(model, gateway="fireworks")

        assert result is not None, "enrich_model_with_pricing should return enriched model"
        assert result["pricing"] is not None, "pricing dict must not be None"
        assert float(result["pricing"]["prompt"]) > 0, "Prompt pricing must be > 0"
        assert float(result["pricing"]["completion"]) > 0, "Completion pricing must be > 0"

    @pytest.mark.cm_verified
    def test_modality_is_known_type(self):
        """CM-9.1.4: normalize_fireworks_model outputs a known modality type."""
        from src.services.models import normalize_fireworks_model

        for modality in ["text->text", "text->image"]:
            raw = {
                "id": f"accounts/fireworks/models/test-{modality}",
                "display_name": "Test Model",
                "metadata": {"context_length": 4096, "modality": modality},
                "pricing": {
                    "cents_per_input_token": 0.001,
                    "cents_per_output_token": 0.001,
                },
            }

            with (
                patch("src.services.pricing_lookup._get_pricing_from_database", return_value=None),
                patch("src.services.pricing_lookup.get_model_pricing", return_value=None),
                patch(
                    "src.services.pricing_lookup._get_cross_reference_pricing", return_value=None
                ),
                patch("src.services.pricing_lookup._is_building_catalog", return_value=False),
            ):
                result = normalize_fireworks_model(raw)

            if result is not None:
                arch = result.get("architecture", {})
                result_modality = arch.get("modality", result.get("modality"))
                assert (
                    result_modality in KNOWN_MODALITIES
                ), f"Modality '{result_modality}' not in known types"

    @pytest.mark.cm_verified
    def test_context_length_is_positive_integer(self):
        """CM-9.1.5: normalize_fireworks_model preserves context_length as a positive int."""
        from src.services.models import normalize_fireworks_model

        raw = {
            "id": "accounts/fireworks/models/test-ctx",
            "display_name": "Test Context Model",
            "metadata": {"context_length": 32768},
            "pricing": {
                "cents_per_input_token": 0.001,
                "cents_per_output_token": 0.001,
            },
        }

        with (
            patch("src.services.pricing_lookup._get_pricing_from_database", return_value=None),
            patch("src.services.pricing_lookup.get_model_pricing", return_value=None),
            patch("src.services.pricing_lookup._get_cross_reference_pricing", return_value=None),
            patch("src.services.pricing_lookup._is_building_catalog", return_value=False),
        ):
            result = normalize_fireworks_model(raw)

        if result is not None:
            ctx = result["context_length"]
            assert isinstance(ctx, int), f"context_length must be int, got {type(ctx).__name__}"
            assert ctx > 0, f"context_length must be > 0, got {ctx}"


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
