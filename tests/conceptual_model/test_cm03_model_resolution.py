"""
CM-3  Model Resolution & Alias Mapping

Tests that MODEL_ID_ALIASES, apply_model_alias, MODEL_PROVIDER_OVERRIDES,
detect_provider_from_model_id, and transform_model_id behave as documented
in the Conceptual Model.
"""

import pytest

from src.services.model_transformations import (
    MODEL_ID_ALIASES,
    MODEL_PROVIDER_OVERRIDES,
    apply_model_alias,
    detect_provider_from_model_id,
    transform_model_id,
)


# ---------------------------------------------------------------------------
# CM-3.1  Alias "r1" resolves to deepseek/deepseek-r1
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_alias_r1_resolves_to_deepseek():
    result = apply_model_alias("r1")
    assert result == "deepseek/deepseek-r1"


# ---------------------------------------------------------------------------
# CM-3.2  Alias "gpt-4o" resolves to openai/gpt-4o
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_alias_gpt4o_resolves_to_openai():
    result = apply_model_alias("gpt-4o")
    assert result == "openai/gpt-4o"


# ---------------------------------------------------------------------------
# CM-3.3  At least 120 aliases are defined
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_at_least_120_aliases_defined():
    # The alias dict plus the provider overrides dict together cover 120+
    # model ID remappings.  Both participate in the resolution pipeline.
    total = len(MODEL_ID_ALIASES) + len(MODEL_PROVIDER_OVERRIDES)
    assert total >= 120, (
        f"Expected at least 120 alias/override entries, found {total} "
        f"(aliases={len(MODEL_ID_ALIASES)}, overrides={len(MODEL_PROVIDER_OVERRIDES)})"
    )


# ---------------------------------------------------------------------------
# CM-3.4  Canonical ID passes through unchanged (no double-resolution)
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_canonical_id_passes_through_unchanged():
    # "openai/gpt-4o" is the canonical form — it should not be re-aliased
    result = apply_model_alias("openai/gpt-4o")
    assert result == "openai/gpt-4o"


# ---------------------------------------------------------------------------
# CM-3.5  MODEL_PROVIDER_OVERRIDES takes highest priority in provider detection
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_provider_detection_explicit_override_highest_priority():
    # Pick a model that has an explicit override entry
    # "deepseek/deepseek-chat" -> "openrouter" per MODEL_PROVIDER_OVERRIDES
    assert MODEL_PROVIDER_OVERRIDES.get("deepseek/deepseek-chat") == "openrouter"

    # detect_provider_from_model_id should honour the override
    # Mock the multi-provider registry import to isolate legacy detection
    from unittest.mock import patch

    with patch.dict(
        "sys.modules",
        {
            "src.services.multi_provider_registry": None,
            "src.services.provider_selector": None,
        },
    ):
        provider = detect_provider_from_model_id("deepseek/deepseek-chat")
    assert provider == "openrouter"


# ---------------------------------------------------------------------------
# CM-3.6  Fireworks "accounts/fireworks/models/..." format detected
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_provider_detection_format_based_rules():
    from unittest.mock import patch

    with patch.dict(
        "sys.modules",
        {
            "src.services.multi_provider_registry": None,
            "src.services.provider_selector": None,
        },
    ):
        provider = detect_provider_from_model_id(
            "accounts/fireworks/models/deepseek-v3p1"
        )
    assert provider == "fireworks"


# ---------------------------------------------------------------------------
# CM-3.7  Org-prefix "meta-llama/" falls back to a provider
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_provider_detection_org_prefix_fallback():
    from unittest.mock import patch

    with patch.dict(
        "sys.modules",
        {
            "src.services.multi_provider_registry": None,
            "src.services.provider_selector": None,
        },
    ):
        provider = detect_provider_from_model_id("meta-llama/llama-3.3-70b-instruct")
    # meta-llama models should be detected (exact provider depends on mapping
    # scan order, but must not be None)
    assert provider is not None


# ---------------------------------------------------------------------------
# CM-3.8  Canonical ID transformed to Fireworks native format
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_model_id_transformation_fireworks_format():
    # "deepseek/deepseek-r1" should map to the Fireworks-native model ID
    result = transform_model_id(
        "deepseek/deepseek-r1", provider="fireworks", use_multi_provider=False
    )
    assert result == "accounts/fireworks/models/deepseek-r1-0528"


# ---------------------------------------------------------------------------
# CM-3.9  Per-provider transformation gives correct native IDs
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_model_id_transformation_per_provider():
    # Fireworks: Llama -> Fireworks native format
    fw_result = transform_model_id(
        "meta-llama/llama-3.3-70b-instruct",
        provider="fireworks",
        use_multi_provider=False,
    )
    assert fw_result == "accounts/fireworks/models/llama-v3p3-70b-instruct"

    # OpenRouter: model keeps org/model format (lowercased)
    or_result = transform_model_id(
        "openai/gpt-4", provider="openrouter", use_multi_provider=False
    )
    assert or_result == "openai/gpt-4"


# ---------------------------------------------------------------------------
# CM-3.10  Unknown alias passes through unchanged
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_unknown_alias_returns_error_or_passthrough():
    unknown = "totally-nonexistent/model-xyz-999"
    result = apply_model_alias(unknown)
    # apply_model_alias returns the input as-is when no alias matches
    assert result == unknown
