"""
Unified Model Transformations Tests

Merged from:
- tests/services/test_model_transformations.py (provider detection, transform, alias)
- tests/services/test_model_transformations_comprehensive.py (comprehensive classes)
- tests/unit/test_model_transformations.py (_MODEL_ID_MAPPINGS structural tests)

Deduplicated: overlapping provider detection, fireworks transforms, groq transforms,
openrouter auto fallbacks, and alias tests.
"""

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("TESTING", "true")

from src.services.model_transformations import (
    _MODEL_ID_MAPPINGS,
    MODEL_PROVIDER_OVERRIDES,
    OPENROUTER_AUTO_FALLBACKS,
    apply_model_alias,
    detect_provider_from_model_id,
    get_model_id_mapping,
    normalize_model_name,
    transform_model_id,
)

# ===========================================================================
# Model Alias Resolution
# ===========================================================================


class TestModelAliasResolution:
    """Test model alias resolution"""

    def test_gpt_5_1_variants(self):
        """Test GPT-5.1 alias variants"""
        assert apply_model_alias("openai/gpt-5-1") == "openai/gpt-5.1"
        assert apply_model_alias("openai/gpt5-1") == "openai/gpt-5.1"
        assert apply_model_alias("gpt-5-1") == "openai/gpt-5.1"
        assert apply_model_alias("gpt5.1") == "openai/gpt-5.1"

    def test_xai_deprecated(self):
        """Test XAI deprecated model aliases"""
        assert apply_model_alias("grok-beta") == "x-ai/grok-3"
        assert apply_model_alias("xai/grok-beta") == "x-ai/grok-3"
        assert apply_model_alias("grok-vision-beta") == "x-ai/grok-3"

    def test_case_insensitive(self):
        assert apply_model_alias("GPT-5-1") == "openai/gpt-5.1"
        assert apply_model_alias("GROK-BETA") == "x-ai/grok-3"

    def test_no_match_passthrough(self):
        assert apply_model_alias("custom-model-xyz") == "custom-model-xyz"
        assert apply_model_alias("some-org/some-model") == "some-org/some-model"

    def test_none_input(self):
        assert apply_model_alias(None) is None

    def test_empty_string(self):
        assert apply_model_alias("") == ""

    def test_bare_openai_model_names(self):
        """Bare OpenAI model names alias to canonical openai/ prefix."""
        test_cases = [
            ("gpt-4", "openai/gpt-4"),
            ("gpt-4o", "openai/gpt-4o"),
            ("gpt-4o-mini", "openai/gpt-4o-mini"),
            ("gpt-4-turbo", "openai/gpt-4-turbo"),
            ("gpt-3.5-turbo", "openai/gpt-3.5-turbo"),
            ("gpt-3.5-turbo-16k", "openai/gpt-3.5-turbo-16k"),
        ]
        for model_id, expected in test_cases:
            result = apply_model_alias(model_id)
            assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"

    def test_bare_anthropic_model_names(self):
        """Bare Anthropic model names alias to canonical anthropic/ prefix."""
        test_cases = [
            ("claude-3-opus", "anthropic/claude-3-opus"),
            ("claude-3-sonnet", "anthropic/claude-3-sonnet"),
            ("claude-3-haiku", "anthropic/claude-3-haiku"),
            ("claude-3.5-sonnet", "anthropic/claude-3.5-sonnet"),
            ("claude-3.5-haiku", "anthropic/claude-3.5-haiku"),
            ("claude-3.7-sonnet", "anthropic/claude-3.7-sonnet"),
            ("claude-sonnet-4", "anthropic/claude-sonnet-4"),
            ("claude-opus-4", "anthropic/claude-opus-4"),
            ("claude-opus-4.5", "anthropic/claude-opus-4.5"),
        ]
        for model_id, expected in test_cases:
            result = apply_model_alias(model_id)
            assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"

    def test_z_ai_glm_47_alias(self):
        """Non-existent z-ai/glm-4.7 maps to z-ai/glm-4-flash"""
        test_cases = [
            ("z-ai/glm-4.7", "z-ai/glm-4-flash"),
            ("z-ai/glm-4-7", "z-ai/glm-4-flash"),
            ("z-ai/glm4.7", "z-ai/glm-4-flash"),
        ]
        for model_id, expected in test_cases:
            result = apply_model_alias(model_id)
            assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"

    def test_aliased_models_canonical_routing(self):
        """Aliased models (gpt-4, claude-3-opus) now have canonical prefixes."""
        assert apply_model_alias("gpt-4") == "openai/gpt-4"
        assert apply_model_alias("claude-3-opus") == "anthropic/claude-3-opus"


# ===========================================================================
# Provider Detection
# ===========================================================================


class TestProviderDetection:
    """Test detect_provider_from_model_id for all providers"""

    def test_openai_models(self):
        assert detect_provider_from_model_id("openai/gpt-4") == "openai"

    def test_anthropic_models(self):
        assert detect_provider_from_model_id("anthropic/claude-3-sonnet") == "anthropic"

    def test_bare_openai_models_detect_as_native(self):
        for model_id in ["gpt-4", "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"]:
            result = detect_provider_from_model_id(model_id)
            assert result == "openai", f"Expected 'openai' for {model_id}, got {result}"

    def test_bare_anthropic_models_detect_as_native(self):
        for model_id in [
            "claude-3-opus",
            "claude-3-sonnet",
            "claude-3-haiku",
            "claude-3.5-sonnet",
            "claude-3.5-haiku",
            "claude-3.7-sonnet",
            "claude-sonnet-4",
            "claude-opus-4",
        ]:
            result = detect_provider_from_model_id(model_id)
            assert result == "anthropic", f"Expected 'anthropic' for {model_id}, got {result}"

    def test_fal_ai_models(self):
        assert detect_provider_from_model_id("fal-ai/stable-diffusion-v15") == "fal"

    def test_fal_related_orgs(self):
        test_cases = [
            ("fal/some-model", "fal"),
            ("minimax/video-01", "fal"),
            ("stabilityai/stable-diffusion-xl", "fal"),
            ("hunyuan3d/some-model", "fal"),
            ("meshy/mesh-model", "fal"),
            ("tripo3d/3d-model", "fal"),
        ]
        for model_id, expected in test_cases:
            result = detect_provider_from_model_id(model_id)
            assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"

    def test_groq_models(self):
        test_cases = [
            ("groq/llama-3.3-70b-versatile", "groq"),
            ("groq/llama-3.1-70b-versatile", "groq"),
            ("groq/mixtral-8x7b-32768", "groq"),
            ("groq/gemma2-9b-it", "groq"),
            ("groq/llama-3.1-8b-instant", "groq"),
        ]
        for model_id, expected in test_cases:
            result = detect_provider_from_model_id(model_id)
            assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"

    @patch.dict("os.environ", {"GOOGLE_VERTEX_CREDENTIALS_JSON": '{"type":"service_account"}'})
    def test_google_vertex_models(self):
        test_cases = [
            ("gemini-2.5-flash", "google-vertex"),
            ("gemini-2.0-flash", "google-vertex"),
            ("google/gemini-2.5-flash", "google-vertex"),
            ("google/gemini-2.0-flash", "google-vertex"),
            ("@google/models/gemini-2.5-flash", "google-vertex"),
            ("@google/models/gemini-2.0-flash", "google-vertex"),
        ]
        for model_id, expected in test_cases:
            result = detect_provider_from_model_id(model_id)
            assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"

    def test_at_prefix_models_route_to_openrouter(self):
        test_cases = [
            ("@anthropic/claude-3-sonnet", "openrouter"),
            ("@openai/gpt-4", "openrouter"),
        ]
        for model_id, expected in test_cases:
            result = detect_provider_from_model_id(model_id)
            assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"

    def test_z_ai_prefix_models(self):
        test_cases = [
            ("z-ai/glm-4-flash", "openrouter"),
            ("z-ai/glm-4.5", "openrouter"),
            ("z-ai/glm-4.6", "openrouter"),
            ("z-ai/glm-4.7", "openrouter"),
            ("zai/glm-4-flash", "openrouter"),
        ]
        for model_id, expected in test_cases:
            result = detect_provider_from_model_id(model_id)
            assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"

    def test_z_ai_glm_with_exacto_suffix(self):
        result = detect_provider_from_model_id("z-ai/glm-4.6:exacto")
        assert result == "openrouter"

    def test_openrouter_colon_suffix_variants(self):
        test_cases = [
            ("z-ai/glm-4.6:exacto", "openrouter"),
            ("google/gemini-2.0-flash-exp:free", "google-vertex"),
            ("anthropic/claude-3-opus:extended", "openrouter"),
        ]
        for model_id, expected_provider in test_cases:
            result = detect_provider_from_model_id(model_id)
            assert (
                result == expected_provider
            ), f"Expected '{expected_provider}' for {model_id}, got {result}"

    def test_gpt51_alias_without_org(self):
        assert detect_provider_from_model_id("gpt-5-1") == "openai"

    def test_onerouter_prefixed_models(self):
        test_cases = [
            ("onerouter/claude-3-5-sonnet", "onerouter"),
            ("onerouter/gpt-4", "onerouter"),
            ("onerouter/gpt-4o", "onerouter"),
            ("onerouter/gpt-3.5-turbo", "onerouter"),
            ("onerouter/llama-3.1-70b", "onerouter"),
        ]
        for model_id, expected in test_cases:
            result = detect_provider_from_model_id(model_id)
            assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"

    def test_onerouter_versioned_models(self):
        test_cases = [
            ("claude-3-5-sonnet@20240620", "onerouter"),
            ("gpt-4@latest", "onerouter"),
            ("gpt-4o@latest", "onerouter"),
            ("gpt-3.5-turbo@latest", "onerouter"),
        ]
        for model_id, expected in test_cases:
            result = detect_provider_from_model_id(model_id)
            assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"

    def test_meta_llama_has_no_specific_provider(self):
        result = detect_provider_from_model_id("meta-llama/llama-2-7b")
        assert result is None


# ===========================================================================
# Fireworks Transformations
# ===========================================================================


class TestFireworksTransformations:
    """Test Fireworks AI model transformations"""

    def test_deepseek_v3(self):
        assert (
            transform_model_id("deepseek-ai/deepseek-v3", "fireworks")
            == "accounts/fireworks/models/deepseek-v3p1"
        )

    def test_deepseek_v3_alt_org(self):
        assert (
            transform_model_id("deepseek/deepseek-v3", "fireworks")
            == "accounts/fireworks/models/deepseek-v3p1"
        )

    def test_deepseek_v3_1(self):
        assert (
            transform_model_id("deepseek/deepseek-v3.1", "fireworks")
            == "accounts/fireworks/models/deepseek-v3p1"
        )

    def test_deepseek_r1(self):
        result = transform_model_id("deepseek-ai/deepseek-r1", "fireworks")
        assert "deepseek-r1" in result

    def test_llama_3_3_70b(self):
        assert (
            transform_model_id("meta-llama/llama-3.3-70b", "fireworks")
            == "accounts/fireworks/models/llama-v3p3-70b-instruct"
        )

    def test_llama_3_1_70b(self):
        assert (
            transform_model_id("meta-llama/llama-3.1-70b", "fireworks")
            == "accounts/fireworks/models/llama-v3p1-70b-instruct"
        )

    def test_qwen_models(self):
        result = transform_model_id("qwen/qwen-2.5-32b", "fireworks")
        assert "qwen" in result.lower()

    def test_already_in_fireworks_format(self):
        model = "accounts/fireworks/models/llama-v3p3-70b-instruct"
        result = transform_model_id(model, "fireworks")
        assert result == model.lower()

    def test_unknown_model_passes_through(self):
        result = transform_model_id("org/unknown-model", "fireworks")
        assert result == "org/unknown-model"

    def test_unknown_model_does_not_construct_invalid_id(self):
        result = transform_model_id("deepseek/deepseek-v3.2-speciale", "fireworks")
        assert not result.startswith("accounts/fireworks/models/")
        assert result == "deepseek/deepseek-v3.2-speciale"

    def test_unknown_model_without_slash_passthrough(self):
        result = transform_model_id("some-unknown-model", "fireworks")
        assert result == "some-unknown-model"

    def test_nonexistent_variant_passthrough(self):
        nonexistent_variants = [
            "deepseek/deepseek-v4",
            "deepseek/deepseek-r2-ultra",
            "meta-llama/llama-5-100b",
            "qwen/qwen-99b-super",
            "fictional-org/fictional-model-v1.2.3",
        ]
        for model_id in nonexistent_variants:
            result = transform_model_id(model_id, "fireworks")
            assert not result.startswith(
                "accounts/fireworks/models/"
            ), f"Unknown model '{model_id}' was naively constructed"
            assert result == model_id

    def test_fuzzy_match_still_works(self):
        result = transform_model_id("DeepSeek-AI/DeepSeek-V3", "fireworks")
        assert result == "accounts/fireworks/models/deepseek-v3p1"

    def test_version_variations(self):
        for model in [
            "deepseek-ai/deepseek-v3",
            "deepseek-ai/deepseek-v3.1",
            "deepseek-ai/deepseek-v3p1",
        ]:
            result = transform_model_id(model, "fireworks")
            assert result is not None and len(result) > 0

    def test_mixed_case(self):
        result = transform_model_id("Meta-Llama/LLAMA-3.3-70B", "fireworks")
        assert "llama" in result.lower()

    def test_instruct_suffix(self):
        result1 = transform_model_id("meta-llama/llama-3.3-70b", "fireworks")
        result2 = transform_model_id("meta-llama/llama-3.3-70b-instruct", "fireworks")
        assert result1 == result2


# ===========================================================================
# OpenRouter Transformations
# ===========================================================================


class TestOpenRouterTransformations:
    """Test OpenRouter model transformations"""

    def test_prefix_stripped(self):
        result = transform_model_id("openrouter/openai/gpt-4", "openrouter")
        assert result == "openai/gpt-4"

    def test_auto_preserved(self):
        assert transform_model_id("openrouter/auto", "openrouter") == "openrouter/auto"

    def test_bodybuilder_preserved(self):
        assert (
            transform_model_id("openrouter/bodybuilder", "openrouter") == "openrouter/bodybuilder"
        )

    def test_meta_models_preserved(self):
        for model in ["openrouter/auto", "openrouter/bodybuilder"]:
            assert transform_model_id(model, "openrouter") == model

    def test_gpt51_hyphen_alias(self):
        result = transform_model_id("openai/gpt-5-1", "openrouter")
        assert result == "openai/gpt-5.1"

    def test_claude_sonnet_variants(self):
        result = transform_model_id("anthropic/claude-sonnet-4.5", "openrouter")
        assert "claude" in result.lower()

    def test_cerebras_to_openrouter(self):
        result = transform_model_id("cerebras/llama-3.3-70b", "openrouter")
        assert "llama" in result.lower()

    def test_z_ai_glm_passthrough(self):
        transformed = transform_model_id("z-ai/glm-4.6:exacto", "openrouter")
        assert transformed == "z-ai/glm-4.6:exacto"

    def test_normalizes_to_lowercase(self):
        result = transform_model_id("OpenAI/GPT-4", "openrouter")
        assert result == result.lower()


# ===========================================================================
# OpenRouter Auto Fallbacks
# ===========================================================================


class TestOpenRouterAutoFallbacks:
    """Test openrouter/auto fallback behavior"""

    def test_fallback_to_cerebras(self):
        assert transform_model_id("openrouter/auto", "cerebras") == "llama-3.3-70b"

    def test_fallback_to_huggingface(self):
        result = transform_model_id("openrouter/auto", "huggingface")
        assert "llama" in result.lower() or "Llama" in result

    def test_fallback_to_google_vertex(self):
        result = transform_model_id("openrouter/auto", "google-vertex")
        assert "gemini" in result.lower()

    def test_fallback_to_alibaba(self):
        result = transform_model_id("openrouter/auto", "alibaba-cloud")
        assert "qwen" in result.lower()

    def test_fallback_providers_defined(self):
        assert "cerebras" in OPENROUTER_AUTO_FALLBACKS
        assert "huggingface" in OPENROUTER_AUTO_FALLBACKS
        assert "google-vertex" in OPENROUTER_AUTO_FALLBACKS

    def test_fallback_models_valid(self):
        for provider, model in OPENROUTER_AUTO_FALLBACKS.items():
            assert model is not None and len(model) > 0


# ===========================================================================
# Groq Transformations
# ===========================================================================


class TestGroqTransformations:
    """Test Groq model transformations"""

    def test_prefix_stripped(self):
        test_cases = [
            ("groq/llama-3.3-70b-versatile", "llama-3.3-70b-versatile"),
            ("groq/mixtral-8x7b-32768", "mixtral-8x7b-32768"),
            ("groq/gemma2-9b-it", "gemma2-9b-it"),
        ]
        for model_id, expected in test_cases:
            assert transform_model_id(model_id, "groq") == expected

    def test_without_prefix_passthrough(self):
        test_cases = [
            ("llama-3.3-70b-versatile", "llama-3.3-70b-versatile"),
            ("mixtral-8x7b-32768", "mixtral-8x7b-32768"),
            ("llama3-70b-8192", "llama3-70b-8192"),
        ]
        for model_id, expected in test_cases:
            assert transform_model_id(model_id, "groq") == expected


# ===========================================================================
# Google Vertex Transformations
# ===========================================================================


class TestGoogleVertexTransformations:
    """Test Google Vertex AI model transformations"""

    def test_gemini_2_5_flash(self):
        result = transform_model_id("gemini-2.5-flash", "google-vertex")
        assert "gemini-2.5-flash" in result

    def test_with_google_prefix(self):
        result = transform_model_id("google/gemini-2.5-flash", "google-vertex")
        assert "gemini" in result

    def test_with_at_prefix(self):
        result = transform_model_id("@google/models/gemini-2.5-flash", "google-vertex")
        assert "gemini" in result

    def test_gemini_1_5_pro(self):
        result = transform_model_id("gemini-1.5-pro", "google-vertex")
        assert "gemini-1.5-pro" in result

    def test_gemini_2_0_flash(self):
        result = transform_model_id("gemini-2.0-flash", "google-vertex")
        assert "gemini-2.0-flash" in result


# ===========================================================================
# HuggingFace Transformations
# ===========================================================================


class TestHuggingFaceTransformations:
    """Test HuggingFace model transformations"""

    def test_llama_models(self):
        result = transform_model_id("meta-llama/llama-3.3-70b", "huggingface")
        assert "Llama-3.3-70B" in result or "llama-3.3-70b" in result.lower()

    def test_deepseek_models(self):
        result = transform_model_id("deepseek-ai/deepseek-v3", "huggingface")
        assert "DeepSeek" in result or "deepseek" in result.lower()

    def test_qwen_models(self):
        result = transform_model_id("qwen/qwen-2.5-72b", "huggingface")
        assert "qwen" in result.lower()

    def test_mistral_models(self):
        result = transform_model_id("mistralai/mistral-7b", "huggingface")
        assert "mistral" in result.lower()

    def test_hug_alias_same_as_huggingface(self):
        result_hug = transform_model_id("meta-llama/llama-3.3-70b", "hug")
        result_hf = transform_model_id("meta-llama/llama-3.3-70b", "huggingface")
        assert result_hug == result_hf


# ===========================================================================
# OneRouter Transformations
# ===========================================================================


class TestOneRouterTransformations:
    """Test OneRouter model transformations"""

    def test_strips_prefix(self):
        test_cases = [
            ("onerouter/claude-3-5-sonnet", "claude-3-5-sonnet@20240620"),
            ("onerouter/gpt-4", "gpt-4@latest"),
            ("onerouter/gpt-4o", "gpt-4o@latest"),
            ("onerouter/gpt-3.5-turbo", "gpt-3.5-turbo@latest"),
        ]
        for model_id, expected in test_cases:
            assert transform_model_id(model_id, "onerouter") == expected

    def test_passthrough_versioned(self):
        test_cases = [
            ("claude-3-5-sonnet@20240620", "claude-3-5-sonnet@20240620"),
            ("gpt-4@latest", "gpt-4@latest"),
            ("gpt-4o@latest", "gpt-4o@latest"),
        ]
        for model_id, expected in test_cases:
            assert transform_model_id(model_id, "onerouter") == expected

    def test_simple_names_get_version_suffix(self):
        test_cases = [
            ("claude-3-5-sonnet", "claude-3-5-sonnet@20240620"),
            ("gpt-4", "gpt-4@latest"),
            ("gpt-4o", "gpt-4o@latest"),
            ("gpt-3.5-turbo", "gpt-3.5-turbo@latest"),
        ]
        for model_id, expected in test_cases:
            assert transform_model_id(model_id, "onerouter") == expected

    def test_unknown_model_passthrough(self):
        assert transform_model_id("some-unknown-model", "onerouter") == "some-unknown-model"
        assert transform_model_id("org/custom-model", "onerouter") == "org/custom-model"

    def test_model_id_mapping_exists(self):
        mapping = get_model_id_mapping("onerouter")
        assert mapping is not None
        assert len(mapping) > 0
        assert "onerouter/claude-3-5-sonnet" in mapping
        assert "gpt-4" in mapping


# ===========================================================================
# Other Provider Transformations
# ===========================================================================


class TestOtherProviderTransformations:
    """Test Near, AIMO, Morpheus transformations"""

    def test_near_prefix_stripped(self):
        result = transform_model_id("near/some-model", "near")
        assert not result.startswith("near/")

    def test_aimo_prefix_stripped(self):
        result = transform_model_id("aimo/some-model", "aimo")
        assert not result.startswith("aimo/")

    def test_morpheus_prefix_stripped(self):
        result = transform_model_id("morpheus/some-model", "morpheus")
        assert not result.startswith("morpheus/")


# ===========================================================================
# Model ID Normalization
# ===========================================================================


class TestModelIdNormalization:
    """Test model ID normalization"""

    def test_empty_string(self):
        assert transform_model_id("", "openrouter") == ""

    def test_none_input(self):
        assert transform_model_id(None, "openrouter") is None

    def test_preserves_special_characters(self):
        result = transform_model_id("openrouter/auto", "openrouter")
        assert "/" in result


class TestNormalizeModelName:
    """Test model name normalization function"""

    def test_normalize_basic(self):
        result = normalize_model_name("Meta-Llama/Llama-3.3-70B")
        assert result == result.lower()

    def test_normalize_handles_version_separators(self):
        n1 = normalize_model_name("llama-3.1-70b")
        n2 = normalize_model_name("llama-3p1-70b")
        assert "llama" in n1
        assert "llama" in n2


# ===========================================================================
# Model Provider Overrides
# ===========================================================================


class TestModelProviderOverrides:
    """Test model provider overrides"""

    def test_katanemo_override(self):
        assert "katanemo/arch-router-1.5b" in MODEL_PROVIDER_OVERRIDES
        assert MODEL_PROVIDER_OVERRIDES["katanemo/arch-router-1.5b"] == "huggingface"

    def test_zai_override(self):
        assert "zai-org/glm-4.6-fp8" in MODEL_PROVIDER_OVERRIDES
        assert MODEL_PROVIDER_OVERRIDES["zai-org/glm-4.6-fp8"] == "near"


# ===========================================================================
# get_model_id_mapping
# ===========================================================================


class TestGetModelIdMapping:
    """Test model ID mapping retrieval"""

    def test_fireworks_mapping(self):
        mapping = get_model_id_mapping("fireworks")
        assert isinstance(mapping, dict)
        assert len(mapping) > 0
        assert any("deepseek" in key for key in mapping.keys())

    def test_openrouter_mapping(self):
        mapping = get_model_id_mapping("openrouter")
        assert isinstance(mapping, dict)
        assert len(mapping) > 0

    def test_groq_mapping(self):
        mapping = get_model_id_mapping("groq")
        assert isinstance(mapping, dict)
        assert "llama-3.3-70b-versatile" in mapping

    def test_google_vertex_mapping(self):
        mapping = get_model_id_mapping("google-vertex")
        assert isinstance(mapping, dict)
        assert any("gemini" in key for key in mapping.keys())

    def test_cloudflare_mapping(self):
        mapping = get_model_id_mapping("cloudflare-workers-ai")
        assert isinstance(mapping, dict)
        assert len(mapping) > 0

    def test_unknown_provider_mapping(self):
        assert get_model_id_mapping("unknown-provider") == {}
        assert get_model_id_mapping("nonexistent-provider-xyz") == {}

    def test_return_value_matches_direct_lookup(self):
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
        for provider in _KNOWN_PROVIDERS:
            assert get_model_id_mapping(provider) == _MODEL_ID_MAPPINGS.get(provider, {})


# ===========================================================================
# _MODEL_ID_MAPPINGS structural tests (from tests/unit/test_model_transformations.py)
# ===========================================================================

_TRANSFORM_ONLY_PROVIDERS = {
    "fireworks",
    "featherless",
    "together",
    "huggingface",
    "near",
    "clarifai",
    "simplismart",
}

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


class TestModelIdMappingsTopLevel:
    def test_mappings_is_dict(self):
        assert isinstance(_MODEL_ID_MAPPINGS, dict)

    def test_mappings_is_non_empty(self):
        assert len(_MODEL_ID_MAPPINGS) > 0

    def test_expected_provider_count(self):
        assert len(_MODEL_ID_MAPPINGS) >= 20

    def test_all_known_providers_present(self):
        for provider in _KNOWN_PROVIDERS:
            assert provider in _MODEL_ID_MAPPINGS, f"Provider '{provider}' missing"


class TestPerProviderMappingType:
    @pytest.mark.parametrize("provider", list(_MODEL_ID_MAPPINGS.keys()))
    def test_provider_mapping_is_dict(self, provider):
        assert isinstance(_MODEL_ID_MAPPINGS[provider], dict)


class TestMappingKeyValueTypes:
    @pytest.mark.parametrize("provider", list(_MODEL_ID_MAPPINGS.keys()))
    def test_all_keys_are_non_empty_strings(self, provider):
        for key in _MODEL_ID_MAPPINGS[provider]:
            assert isinstance(key, str) and key, f"Provider '{provider}': key {key!r} invalid"

    @pytest.mark.parametrize("provider", list(_MODEL_ID_MAPPINGS.keys()))
    def test_all_values_are_non_empty_strings(self, provider):
        for key, value in _MODEL_ID_MAPPINGS[provider].items():
            assert (
                isinstance(value, str) and value
            ), f"Provider '{provider}': value {value!r} for key {key!r} invalid"


class TestNoSelfMappings:
    @pytest.mark.parametrize(
        "provider",
        sorted(_TRANSFORM_ONLY_PROVIDERS & _MODEL_ID_MAPPINGS.keys()),
    )
    def test_no_self_mapping_entries(self, provider):
        mapping = _MODEL_ID_MAPPINGS[provider]
        self_maps = [k for k, v in mapping.items() if k == v]
        assert not self_maps, f"Provider '{provider}' has self-mapping(s): {self_maps[:5]}"


class TestGetModelIdMappingAPI:
    """Test get_model_id_mapping() returns correct types"""

    def test_returns_dict_for_known_provider(self):
        assert isinstance(get_model_id_mapping("fireworks"), dict)

    def test_returns_non_empty_for_known_providers(self):
        for provider in ["fireworks", "openrouter", "google-vertex", "cloudflare-workers-ai"]:
            assert len(get_model_id_mapping(provider)) > 0

    def test_returns_empty_for_unknown(self):
        assert get_model_id_mapping("nonexistent-provider-xyz") == {}

    @pytest.mark.parametrize("provider", _KNOWN_PROVIDERS)
    def test_result_keys_are_strings(self, provider):
        for key in get_model_id_mapping(provider):
            assert isinstance(key, str) and key

    @pytest.mark.parametrize("provider", _KNOWN_PROVIDERS)
    def test_result_values_are_strings(self, provider):
        for key, value in get_model_id_mapping(provider).items():
            assert isinstance(value, str) and value
