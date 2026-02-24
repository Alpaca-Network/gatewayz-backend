"""
Comprehensive tests for Model Transformations

Tests cover:
- Model ID transformation for all providers
- Alias resolution
- Provider detection from model ID
- Edge cases and error handling
"""

import os
from unittest.mock import patch

os.environ["APP_ENV"] = "testing"
os.environ["TESTING"] = "true"

from src.services.model_transformations import (
    MODEL_PROVIDER_OVERRIDES,
    OPENROUTER_AUTO_FALLBACKS,
    apply_model_alias,
    detect_provider_from_model_id,
    get_model_id_mapping,
    normalize_model_name,
    transform_model_id,
)


class TestModelAliasResolution:
    """Test model alias resolution"""

    def test_apply_model_alias_gpt_variants(self):
        """Test GPT-5.1 alias variants"""
        assert apply_model_alias("openai/gpt-5-1") == "openai/gpt-5.1"
        assert apply_model_alias("openai/gpt5-1") == "openai/gpt-5.1"
        assert apply_model_alias("gpt-5-1") == "openai/gpt-5.1"
        assert apply_model_alias("gpt5.1") == "openai/gpt-5.1"

    def test_apply_model_alias_xai_deprecated(self):
        """Test XAI deprecated model aliases - now mapped to canonical x-ai/ prefix"""
        # grok-beta and grok-vision-beta are deprecated, map to x-ai/grok-3
        assert apply_model_alias("grok-beta") == "x-ai/grok-3"
        assert apply_model_alias("xai/grok-beta") == "x-ai/grok-3"
        assert apply_model_alias("grok-vision-beta") == "x-ai/grok-3"

    def test_apply_model_alias_case_insensitive(self):
        """Test that alias lookup is case insensitive"""
        assert apply_model_alias("GPT-5-1") == "openai/gpt-5.1"
        assert apply_model_alias("GROK-BETA") == "x-ai/grok-3"

    def test_apply_model_alias_no_match(self):
        """Test non-aliased models pass through

        Note: Many common models like gpt-4 and claude-3-opus now have aliases
        to map them to canonical prefixed versions (openai/gpt-4, anthropic/claude-3-opus).
        This ensures proper provider routing and failover behavior.
        """
        # gpt-4 is now aliased to openai/gpt-4 for proper routing
        assert apply_model_alias("gpt-4") == "openai/gpt-4"
        # claude-3-opus is now aliased to anthropic/claude-3-opus for proper routing
        assert apply_model_alias("claude-3-opus") == "anthropic/claude-3-opus"
        # Test a truly non-aliased model
        assert apply_model_alias("custom-model-xyz") == "custom-model-xyz"
        assert apply_model_alias("some-org/some-model") == "some-org/some-model"

    def test_apply_model_alias_none_input(self):
        """Test None input returns None"""
        assert apply_model_alias(None) is None

    def test_apply_model_alias_empty_string(self):
        """Test empty string returns empty string"""
        assert apply_model_alias("") == ""


class TestFireworksTransformations:
    """Test Fireworks AI model transformations"""

    def test_transform_deepseek_v3(self):
        """Test DeepSeek-V3 transformation"""
        result = transform_model_id("deepseek-ai/deepseek-v3", "fireworks")
        assert result == "accounts/fireworks/models/deepseek-v3p1"

    def test_transform_deepseek_v3_alt_org(self):
        """Test DeepSeek-V3 with alternative org prefix"""
        result = transform_model_id("deepseek/deepseek-v3", "fireworks")
        assert result == "accounts/fireworks/models/deepseek-v3p1"

    def test_transform_llama_3_3_70b(self):
        """Test Llama 3.3 70B transformation"""
        result = transform_model_id("meta-llama/llama-3.3-70b", "fireworks")
        assert result == "accounts/fireworks/models/llama-v3p3-70b-instruct"

    def test_transform_llama_3_1_70b(self):
        """Test Llama 3.1 70B transformation"""
        result = transform_model_id("meta-llama/llama-3.1-70b", "fireworks")
        assert result == "accounts/fireworks/models/llama-v3p1-70b-instruct"

    def test_transform_qwen_models(self):
        """Test Qwen model transformations"""
        result = transform_model_id("qwen/qwen-2.5-32b", "fireworks")
        assert "qwen" in result.lower()

    def test_transform_already_in_fireworks_format(self):
        """Test model already in Fireworks format passes through"""
        model = "accounts/fireworks/models/llama-v3p3-70b-instruct"
        result = transform_model_id(model, "fireworks")
        assert result == model.lower()

    def test_transform_unknown_model_passes_through(self):
        """Test unknown model passes through as-is (Fireworks API will reject if invalid)"""
        result = transform_model_id("org/unknown-model", "fireworks")
        # Unknown models now pass through as-is rather than constructing a Fireworks path
        assert result == "org/unknown-model"

    def test_transform_deepseek_r1(self):
        """Test DeepSeek-R1 transformation"""
        result = transform_model_id("deepseek-ai/deepseek-r1", "fireworks")
        assert "deepseek-r1" in result


class TestOpenRouterTransformations:
    """Test OpenRouter model transformations"""

    def test_transform_openrouter_prefix_stripped(self):
        """Test openrouter/ prefix is stripped"""
        result = transform_model_id("openrouter/openai/gpt-4", "openrouter")
        assert result == "openai/gpt-4"

    def test_transform_openrouter_auto_preserved(self):
        """Test openrouter/auto is preserved"""
        result = transform_model_id("openrouter/auto", "openrouter")
        assert result == "openrouter/auto"

    def test_transform_openrouter_bodybuilder_preserved(self):
        """Test openrouter/bodybuilder is preserved"""
        result = transform_model_id("openrouter/bodybuilder", "openrouter")
        assert result == "openrouter/bodybuilder"

    def test_transform_openrouter_meta_models_preserved(self):
        """Test all OpenRouter meta-models are preserved"""
        meta_models = ["openrouter/auto", "openrouter/bodybuilder"]
        for model in meta_models:
            result = transform_model_id(model, "openrouter")
            assert result == model, f"Expected {model} to be preserved but got {result}"

    def test_transform_claude_sonnet_variants(self):
        """Test Claude Sonnet 4.5 transformation variants"""
        result = transform_model_id("anthropic/claude-sonnet-4.5", "openrouter")
        assert "claude" in result.lower()

    def test_transform_cerebras_to_openrouter(self):
        """Test Cerebras model routed through OpenRouter"""
        result = transform_model_id("cerebras/llama-3.3-70b", "openrouter")
        assert "llama" in result.lower()


class TestOpenRouterAutoFallbacks:
    """Test OpenRouter auto fallback behavior"""

    def test_openrouter_auto_fallback_to_cerebras(self):
        """Test openrouter/auto fallback to Cerebras"""
        result = transform_model_id("openrouter/auto", "cerebras")
        assert result == "llama-3.3-70b"

    def test_openrouter_auto_fallback_to_huggingface(self):
        """Test openrouter/auto fallback to HuggingFace"""
        result = transform_model_id("openrouter/auto", "huggingface")
        assert "llama" in result.lower()

    def test_openrouter_auto_fallback_to_google_vertex(self):
        """Test openrouter/auto fallback to Google Vertex"""
        result = transform_model_id("openrouter/auto", "google-vertex")
        assert "gemini" in result.lower()

    def test_openrouter_auto_fallback_to_alibaba(self):
        """Test openrouter/auto fallback to Alibaba Cloud"""
        result = transform_model_id("openrouter/auto", "alibaba-cloud")
        assert "qwen" in result.lower()


class TestGroqTransformations:
    """Test Groq model transformations"""

    def test_transform_groq_prefix_stripped(self):
        """Test groq/ prefix is stripped"""
        result = transform_model_id("groq/llama-3.3-70b-versatile", "groq")
        assert result == "llama-3.3-70b-versatile"

    def test_transform_groq_mixtral(self):
        """Test Groq Mixtral transformation"""
        result = transform_model_id("groq/mixtral-8x7b-32768", "groq")
        assert result == "mixtral-8x7b-32768"

    def test_transform_groq_gemma(self):
        """Test Groq Gemma transformation"""
        result = transform_model_id("groq/gemma2-9b-it", "groq")
        assert result == "gemma2-9b-it"

    def test_transform_groq_without_prefix(self):
        """Test Groq model without prefix"""
        result = transform_model_id("llama-3.3-70b-versatile", "groq")
        assert result == "llama-3.3-70b-versatile"


class TestGoogleVertexTransformations:
    """Test Google Vertex AI model transformations"""

    def test_transform_gemini_2_5_flash(self):
        """Test Gemini 2.5 Flash transformation"""
        result = transform_model_id("gemini-2.5-flash", "google-vertex")
        assert "gemini-2.5-flash" in result

    def test_transform_gemini_with_google_prefix(self):
        """Test Gemini with google/ prefix"""
        result = transform_model_id("google/gemini-2.5-flash", "google-vertex")
        assert "gemini" in result

    def test_transform_gemini_with_at_prefix(self):
        """Test Gemini with @google/models/ prefix"""
        result = transform_model_id("@google/models/gemini-2.5-flash", "google-vertex")
        assert "gemini" in result

    def test_transform_gemini_1_5_pro(self):
        """Test Gemini 1.5 Pro transformation"""
        result = transform_model_id("gemini-1.5-pro", "google-vertex")
        assert "gemini-1.5-pro" in result

    def test_transform_gemini_2_0_flash(self):
        """Test Gemini 2.0 Flash transformation"""
        result = transform_model_id("gemini-2.0-flash", "google-vertex")
        assert "gemini-2.0-flash" in result


class TestHuggingFaceTransformations:
    """Test HuggingFace model transformations"""

    def test_transform_llama_models(self):
        """Test Llama model transformations"""
        result = transform_model_id("meta-llama/llama-3.3-70b", "huggingface")
        assert "Llama-3.3-70B" in result or "llama-3.3-70b" in result.lower()

    def test_transform_deepseek_models(self):
        """Test DeepSeek model transformations"""
        result = transform_model_id("deepseek-ai/deepseek-v3", "huggingface")
        assert "DeepSeek" in result or "deepseek" in result.lower()

    def test_transform_qwen_models(self):
        """Test Qwen model transformations"""
        result = transform_model_id("qwen/qwen-2.5-72b", "huggingface")
        assert "qwen" in result.lower()

    def test_transform_mistral_models(self):
        """Test Mistral model transformations"""
        result = transform_model_id("mistralai/mistral-7b", "huggingface")
        assert "mistral" in result.lower()

    def test_hug_alias_same_as_huggingface(self):
        """Test 'hug' alias behaves same as 'huggingface'"""
        result_hug = transform_model_id("meta-llama/llama-3.3-70b", "hug")
        result_hf = transform_model_id("meta-llama/llama-3.3-70b", "huggingface")
        assert result_hug == result_hf


class TestNearTransformations:
    """Test Near AI model transformations"""

    def test_transform_near_prefix_stripped(self):
        """Test near/ prefix is stripped"""
        result = transform_model_id("near/some-model", "near")
        assert not result.startswith("near/")


class TestAIMOTransformations:
    """Test AIMO model transformations"""

    def test_transform_aimo_prefix_stripped(self):
        """Test aimo/ prefix is stripped"""
        result = transform_model_id("aimo/some-model", "aimo")
        assert not result.startswith("aimo/")


class TestMorpheusTransformations:
    """Test Morpheus model transformations"""

    def test_transform_morpheus_prefix_stripped(self):
        """Test morpheus/ prefix is stripped"""
        result = transform_model_id("morpheus/some-model", "morpheus")
        assert not result.startswith("morpheus/")


class TestProviderDetection:
    """Test provider detection from model ID"""

    def test_detect_fal_ai_provider(self):
        """Test Fal.ai provider detection"""
        result = detect_provider_from_model_id("fal-ai/stable-diffusion-v15")
        assert result == "fal"

    def test_detect_fal_related_orgs(self):
        """Test Fal-related org detection"""
        test_cases = [
            ("fal/some-model", "fal"),
            ("minimax/video-01", "fal"),
            ("stabilityai/stable-diffusion-xl", "fal"),
        ]

        for model_id, expected in test_cases:
            result = detect_provider_from_model_id(model_id)
            assert result == expected, f"Expected '{expected}' for {model_id}"

    def test_detect_openrouter_colon_suffix(self):
        """Test OpenRouter models with colon suffix"""
        result = detect_provider_from_model_id("z-ai/glm-4.6:exacto")
        assert result == "openrouter"

    def test_detect_groq_models(self):
        """Test Groq model detection"""
        result = detect_provider_from_model_id("groq/llama-3.3-70b-versatile")
        assert result == "groq"

    @patch.dict(os.environ, {"GOOGLE_VERTEX_CREDENTIALS_JSON": '{"type":"service_account"}'})
    def test_detect_google_vertex_models(self):
        """Test Google Vertex AI model detection"""
        result = detect_provider_from_model_id("gemini-2.5-flash")
        assert result == "google-vertex"

    def test_detect_at_prefix_models(self):
        """Test @ prefix model detection"""
        result = detect_provider_from_model_id("@anthropic/claude-3-sonnet")
        assert result == "openrouter"


class TestModelIdNormalization:
    """Test model ID normalization"""

    def test_transform_normalizes_to_lowercase(self):
        """Test that output is normalized to lowercase"""
        result = transform_model_id("OpenAI/GPT-4", "openrouter")
        assert result == result.lower()

    def test_transform_empty_string(self):
        """Test empty string input"""
        result = transform_model_id("", "openrouter")
        assert result == ""

    def test_transform_none_input(self):
        """Test None input"""
        result = transform_model_id(None, "openrouter")
        assert result is None


class TestGetModelIdMapping:
    """Test model ID mapping retrieval"""

    def test_get_fireworks_mapping(self):
        """Test getting Fireworks mapping"""
        mapping = get_model_id_mapping("fireworks")
        assert isinstance(mapping, dict)
        assert len(mapping) > 0
        # Check some expected keys
        assert any("deepseek" in key for key in mapping.keys())

    def test_get_openrouter_mapping(self):
        """Test getting OpenRouter mapping"""
        mapping = get_model_id_mapping("openrouter")
        assert isinstance(mapping, dict)

    def test_get_groq_mapping(self):
        """Test getting Groq mapping"""
        mapping = get_model_id_mapping("groq")
        assert isinstance(mapping, dict)
        assert "llama-3.3-70b-versatile" in mapping

    def test_get_google_vertex_mapping(self):
        """Test getting Google Vertex AI mapping"""
        mapping = get_model_id_mapping("google-vertex")
        assert isinstance(mapping, dict)
        assert any("gemini" in key for key in mapping.keys())

    def test_get_unknown_provider_mapping(self):
        """Test getting mapping for unknown provider returns empty dict"""
        mapping = get_model_id_mapping("unknown-provider")
        assert isinstance(mapping, dict)
        assert len(mapping) == 0


class TestModelProviderOverrides:
    """Test model provider overrides"""

    def test_katanemo_override(self):
        """Test Katanemo model override"""
        assert "katanemo/arch-router-1.5b" in MODEL_PROVIDER_OVERRIDES
        assert MODEL_PROVIDER_OVERRIDES["katanemo/arch-router-1.5b"] == "huggingface"

    def test_zai_override(self):
        """Test Z-AI model override"""
        assert "zai-org/glm-4.6-fp8" in MODEL_PROVIDER_OVERRIDES
        assert MODEL_PROVIDER_OVERRIDES["zai-org/glm-4.6-fp8"] == "near"


class TestOpenRouterAutoFallbackConfig:
    """Test OpenRouter auto fallback configuration"""

    def test_fallback_providers_defined(self):
        """Test fallback providers are defined"""
        assert "cerebras" in OPENROUTER_AUTO_FALLBACKS
        assert "huggingface" in OPENROUTER_AUTO_FALLBACKS
        assert "google-vertex" in OPENROUTER_AUTO_FALLBACKS

    def test_fallback_models_valid(self):
        """Test fallback models are valid model IDs"""
        for provider, model in OPENROUTER_AUTO_FALLBACKS.items():
            assert model is not None
            assert len(model) > 0


class TestEdgeCases:
    """Test edge cases and special scenarios"""

    def test_transform_with_version_variations(self):
        """Test transformation handles version variations"""
        # v3 vs v3.1 vs v3p1
        models = [
            "deepseek-ai/deepseek-v3",
            "deepseek-ai/deepseek-v3.1",
            "deepseek-ai/deepseek-v3p1",
        ]

        for model in models:
            result = transform_model_id(model, "fireworks")
            assert result is not None
            assert len(result) > 0

    def test_transform_preserves_special_characters(self):
        """Test transformation preserves necessary special characters"""
        result = transform_model_id("openrouter/auto", "openrouter")
        assert "/" in result

    def test_transform_handles_mixed_case(self):
        """Test transformation handles mixed case input"""
        result = transform_model_id("Meta-Llama/LLAMA-3.3-70B", "fireworks")
        assert result is not None
        assert "llama" in result.lower()

    def test_transform_with_instruct_suffix(self):
        """Test transformation handles instruct suffix"""
        result1 = transform_model_id("meta-llama/llama-3.3-70b", "fireworks")
        result2 = transform_model_id("meta-llama/llama-3.3-70b-instruct", "fireworks")
        # Both should map to the same model
        assert result1 == result2


class TestNormalizeModelName:
    """Test model name normalization function"""

    def test_normalize_basic(self):
        """Test basic normalization"""
        result = normalize_model_name("Meta-Llama/Llama-3.3-70B")
        assert result == result.lower()

    def test_normalize_removes_version_separators(self):
        """Test normalization handles version separators"""
        # These should normalize to similar values
        n1 = normalize_model_name("llama-3.1-70b")
        n2 = normalize_model_name("llama-3p1-70b")
        # Both contain the core model info
        assert "llama" in n1
        assert "llama" in n2
