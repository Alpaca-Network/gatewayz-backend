"""Tests for Simplismart client"""

from unittest.mock import Mock, patch

import pytest

from src.services.simplismart_client import (
    SIMPLISMART_BASE_URL,
    SIMPLISMART_MODEL_ALIASES,
    SIMPLISMART_MODELS,
    fetch_models_from_simplismart,
    get_simplismart_client,
    is_simplismart_model,
    make_simplismart_request_openai,
    make_simplismart_request_openai_stream,
    process_simplismart_response,
    resolve_simplismart_model,
)


class TestSimplismartClient:
    """Test Simplismart client functionality"""

    @patch("src.services.simplismart_client.Config.SIMPLISMART_API_KEY", "test_key")
    def test_get_simplismart_client(self):
        """Test getting Simplismart client"""
        client = get_simplismart_client()
        assert client is not None
        assert str(client.base_url).rstrip("/") == SIMPLISMART_BASE_URL

    @patch("src.services.simplismart_client.Config.SIMPLISMART_API_KEY", None)
    def test_get_simplismart_client_no_key(self):
        """Test getting Simplismart client without API key"""
        with pytest.raises(ValueError, match="Simplismart API key not configured"):
            get_simplismart_client()

    @patch("src.services.simplismart_client.get_simplismart_client")
    def test_make_simplismart_request_openai(self, mock_get_client):
        """Test making request to Simplismart"""
        # Mock the client and response
        mock_client = Mock()
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.model = "meta-llama/Meta-Llama-3.1-8B-Instruct"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        response = make_simplismart_request_openai(messages, "llama-3.1-8b")

        assert response is not None
        assert response.id == "test_id"
        mock_client.chat.completions.create.assert_called_once()

    @patch("src.services.simplismart_client.get_simplismart_client")
    def test_make_simplismart_request_openai_stream(self, mock_get_client):
        """Test making streaming request to Simplismart"""
        # Mock the client and stream
        mock_client = Mock()
        mock_stream = Mock()
        mock_client.chat.completions.create.return_value = mock_stream
        mock_get_client.return_value = mock_client

        messages = [{"role": "user", "content": "Hello"}]
        stream = make_simplismart_request_openai_stream(messages, "llama-3.1-8b")

        assert stream is not None
        mock_client.chat.completions.create.assert_called_once()
        # Verify stream=True was passed
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("stream") is True

    def test_process_simplismart_response(self):
        """Test processing Simplismart response"""
        # Create a mock response
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "meta-llama/Meta-Llama-3.1-8B-Instruct"

        # Mock choice
        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Test response"
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]

        # Mock usage
        mock_response.usage = Mock()
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 20
        mock_response.usage.total_tokens = 30

        processed = process_simplismart_response(mock_response)

        assert processed["id"] == "test_id"
        assert processed["object"] == "chat.completion"
        assert processed["model"] == "meta-llama/Meta-Llama-3.1-8B-Instruct"
        assert len(processed["choices"]) == 1
        assert processed["choices"][0]["message"]["content"] == "Test response"
        assert processed["usage"]["total_tokens"] == 30

    def test_process_simplismart_response_no_usage(self):
        """Test processing response when usage is not available"""
        mock_response = Mock()
        mock_response.id = "test_id"
        mock_response.object = "chat.completion"
        mock_response.created = 1234567890
        mock_response.model = "meta-llama/Meta-Llama-3.1-8B-Instruct"
        mock_response.usage = None

        mock_choice = Mock()
        mock_choice.index = 0
        mock_choice.message = Mock()
        mock_choice.message.role = "assistant"
        mock_choice.message.content = "Test response"
        mock_choice.finish_reason = "stop"
        mock_response.choices = [mock_choice]

        processed = process_simplismart_response(mock_response)

        assert processed["usage"] == {}


class TestSimplismartModelResolution:
    """Test model ID resolution for Simplismart"""

    def test_resolve_alias_llama_3_1_8b(self):
        """Test resolving llama-3.1-8b alias"""
        resolved = resolve_simplismart_model("llama-3.1-8b")
        assert resolved == "meta-llama/Meta-Llama-3.1-8B-Instruct"

    def test_resolve_alias_llama_3_1_70b(self):
        """Test resolving llama-3.1-70b alias"""
        resolved = resolve_simplismart_model("llama-3.1-70b")
        assert resolved == "meta-llama/Meta-Llama-3.1-70B-Instruct"

    def test_resolve_alias_llama_3_3_70b(self):
        """Test resolving llama-3.3-70b alias"""
        resolved = resolve_simplismart_model("llama-3.3-70b")
        assert resolved == "meta-llama/Llama-3.3-70B-Instruct"

    def test_resolve_alias_gemma(self):
        """Test resolving gemma aliases"""
        assert resolve_simplismart_model("gemma-3-1b") == "google/gemma-3-1b-it"
        assert resolve_simplismart_model("gemma-3-4b") == "google/gemma-3-4b-it"
        assert resolve_simplismart_model("gemma-3-27b") == "google/gemma-3-27b-it"

    def test_resolve_alias_qwen(self):
        """Test resolving qwen aliases"""
        assert resolve_simplismart_model("qwen-2.5-14b") == "Qwen/Qwen2.5-14B-Instruct"
        assert resolve_simplismart_model("qwen-2.5-32b") == "Qwen/Qwen2.5-32B-Instruct"
        assert resolve_simplismart_model("qwen2.5-14b") == "Qwen/Qwen2.5-14B-Instruct"

    def test_resolve_alias_deepseek(self):
        """Test resolving deepseek aliases"""
        assert (
            resolve_simplismart_model("deepseek-r1-distill-llama-70b")
            == "deepseek-ai/DeepSeek-R1-Distill-Llama-70B"
        )
        assert (
            resolve_simplismart_model("deepseek-r1-distill-qwen-32b")
            == "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B"
        )

    def test_resolve_alias_mixtral(self):
        """Test resolving mixtral aliases"""
        assert (
            resolve_simplismart_model("mixtral-8x7b") == "mistralai/Mixtral-8x7B-Instruct-v0.1-FP8"
        )
        assert (
            resolve_simplismart_model("mixtral-8x7b-instruct")
            == "mistralai/Mixtral-8x7B-Instruct-v0.1-FP8"
        )

    def test_resolve_alias_devstral(self):
        """Test resolving devstral aliases"""
        assert resolve_simplismart_model("devstral-small") == "mistralai/Devstral-Small-2505"

    def test_resolve_full_model_id(self):
        """Test that full model IDs are returned as-is"""
        full_id = "meta-llama/Meta-Llama-3.1-8B-Instruct"
        assert resolve_simplismart_model(full_id) == full_id

    def test_resolve_unknown_model(self):
        """Test that unknown models are returned as-is"""
        unknown = "unknown/model-name"
        assert resolve_simplismart_model(unknown) == unknown

    def test_resolve_case_insensitive(self):
        """Test that alias resolution is case insensitive"""
        assert resolve_simplismart_model("LLAMA-3.1-8B") == "meta-llama/Meta-Llama-3.1-8B-Instruct"
        assert resolve_simplismart_model("Llama-3.1-8b") == "meta-llama/Meta-Llama-3.1-8B-Instruct"


class TestSimplismartModelCatalog:
    """Test Simplismart model catalog functions"""

    def test_fetch_models_from_simplismart(self):
        """Test fetching models from Simplismart catalog"""
        models = fetch_models_from_simplismart()

        assert len(models) > 0
        assert len(models) == len(SIMPLISMART_MODELS)

        # Check that all models have required fields
        for model in models:
            assert "id" in model
            assert "name" in model
            assert "provider" in model
            assert model["provider"] == "simplismart"
            # context_length is only required for LLM models, not image/speech models
            model_type = model.get("type")
            if model_type not in ("text-to-image", "image-to-image", "speech-to-text"):
                assert "context_length" in model
            # Verify source_gateway is set for proper frontend discovery
            assert "source_gateway" in model
            assert model["source_gateway"] == "simplismart"
            assert "provider_slug" in model
            assert model["provider_slug"] == "simplismart"

    def test_fetch_models_has_llama_models(self):
        """Test that catalog includes Llama models"""
        models = fetch_models_from_simplismart()
        model_ids = [m["id"] for m in models]

        assert "meta-llama/Meta-Llama-3.1-8B-Instruct" in model_ids
        assert "meta-llama/Meta-Llama-3.1-70B-Instruct" in model_ids
        assert "meta-llama/Llama-3.3-70B-Instruct" in model_ids

    def test_fetch_models_has_gemma_models(self):
        """Test that catalog includes Gemma models"""
        models = fetch_models_from_simplismart()
        model_ids = [m["id"] for m in models]

        assert "google/gemma-3-1b-it" in model_ids
        assert "google/gemma-3-4b-it" in model_ids
        assert "google/gemma-3-27b-it" in model_ids

    def test_fetch_models_has_qwen_models(self):
        """Test that catalog includes Qwen models"""
        models = fetch_models_from_simplismart()
        model_ids = [m["id"] for m in models]

        assert "Qwen/Qwen2.5-14B-Instruct" in model_ids
        assert "Qwen/Qwen2.5-32B-Instruct" in model_ids

    def test_fetch_models_has_deepseek_models(self):
        """Test that catalog includes DeepSeek models"""
        models = fetch_models_from_simplismart()
        model_ids = [m["id"] for m in models]

        assert "deepseek-ai/DeepSeek-R1-Distill-Llama-70B" in model_ids
        assert "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B" in model_ids


class TestIsSimplismartModel:
    """Test is_simplismart_model function"""

    def test_is_simplismart_model_with_alias(self):
        """Test checking models with aliases"""
        assert is_simplismart_model("llama-3.1-8b") is True
        assert is_simplismart_model("llama-3.3-70b") is True
        assert is_simplismart_model("gemma-3-27b") is True
        assert is_simplismart_model("qwen-2.5-32b") is True

    def test_is_simplismart_model_with_full_id(self):
        """Test checking models with full IDs"""
        assert is_simplismart_model("meta-llama/Meta-Llama-3.1-8B-Instruct") is True
        assert is_simplismart_model("google/gemma-3-27b-it") is True
        assert is_simplismart_model("Qwen/Qwen2.5-32B-Instruct") is True

    def test_is_simplismart_model_case_insensitive(self):
        """Test case insensitivity"""
        assert is_simplismart_model("LLAMA-3.1-8B") is True
        assert is_simplismart_model("META-LLAMA/META-LLAMA-3.1-8B-INSTRUCT") is True

    def test_is_simplismart_model_unknown(self):
        """Test that unknown models return False"""
        assert is_simplismart_model("unknown/model") is False
        assert is_simplismart_model("gpt-4") is False
        assert is_simplismart_model("claude-3-sonnet") is False


class TestSimplismartConstants:
    """Test Simplismart constants"""

    def test_base_url(self):
        """Test that base URL is correct"""
        assert SIMPLISMART_BASE_URL == "https://api.simplismart.live"

    def test_model_catalog_not_empty(self):
        """Test that model catalog is not empty"""
        assert len(SIMPLISMART_MODELS) > 0

    def test_model_aliases_not_empty(self):
        """Test that model aliases are not empty"""
        assert len(SIMPLISMART_MODEL_ALIASES) > 0

    def test_all_aliases_resolve_to_catalog_models(self):
        """Test that all aliases resolve to models in the catalog"""
        for alias, model_id in SIMPLISMART_MODEL_ALIASES.items():
            assert (
                model_id in SIMPLISMART_MODELS
            ), f"Alias '{alias}' resolves to unknown model '{model_id}'"

    def test_models_have_required_metadata(self):
        """Test that all models have required metadata"""
        for model_id, info in SIMPLISMART_MODELS.items():
            assert "name" in info, f"Model '{model_id}' missing 'name'"
            # context_length is only required for LLM models
            model_type = info.get("type")
            if model_type not in ("text-to-image", "image-to-image", "speech-to-text"):
                assert "context_length" in info, f"Model '{model_id}' missing 'context_length'"
                assert info["context_length"] > 0, f"Model '{model_id}' has invalid context_length"

    def test_models_have_pricing(self):
        """Test that all models have pricing information"""
        for model_id, info in SIMPLISMART_MODELS.items():
            assert "pricing" in info, f"Model '{model_id}' missing 'pricing'"
            pricing = info["pricing"]
            assert "prompt" in pricing, f"Model '{model_id}' missing 'prompt' pricing"
            assert "completion" in pricing, f"Model '{model_id}' missing 'completion' pricing"
            # Verify pricing is a valid number string
            assert float(pricing["prompt"]) >= 0, f"Model '{model_id}' has invalid prompt pricing"
            assert (
                float(pricing["completion"]) >= 0
            ), f"Model '{model_id}' has invalid completion pricing"


class TestSimplismartPricing:
    """Test Simplismart pricing functionality"""

    def test_fetch_models_includes_pricing(self):
        """Test that fetched models include pricing data"""
        models = fetch_models_from_simplismart()
        for model in models:
            assert "pricing" in model, f"Model '{model['id']}' missing pricing in fetch result"
            pricing = model["pricing"]
            assert "prompt" in pricing
            assert "completion" in pricing

    def test_pricing_values_match_source(self):
        """Test specific pricing values from https://simplismart.ai/pricing"""
        models = fetch_models_from_simplismart()
        models_by_id = {m["id"]: m for m in models}

        # Verify specific pricing values
        assert models_by_id["meta-llama/Meta-Llama-3.1-8B-Instruct"]["pricing"]["prompt"] == "0.13"
        assert models_by_id["meta-llama/Meta-Llama-3.1-70B-Instruct"]["pricing"]["prompt"] == "0.74"
        assert (
            models_by_id["meta-llama/Meta-Llama-3.1-405B-Instruct"]["pricing"]["prompt"] == "3.00"
        )
        assert models_by_id["deepseek-ai/DeepSeek-R1"]["pricing"]["prompt"] == "3.90"
        assert models_by_id["deepseek-ai/DeepSeek-V3"]["pricing"]["prompt"] == "0.90"
        assert models_by_id["google/gemma-3-1b-it"]["pricing"]["prompt"] == "0.06"
        assert models_by_id["google/gemma-3-4b-it"]["pricing"]["prompt"] == "0.10"
        assert models_by_id["microsoft/Phi-3-medium-128k-instruct"]["pricing"]["prompt"] == "0.08"
        assert models_by_id["Qwen/Qwen2.5-72B-Instruct"]["pricing"]["prompt"] == "1.08"
        assert models_by_id["Qwen/Qwen2.5-7B-Instruct"]["pricing"]["prompt"] == "0.30"

    def test_new_models_present(self):
        """Test that new models from pricing page are present"""
        models = fetch_models_from_simplismart()
        model_ids = [m["id"] for m in models]

        # Verify new models added from pricing page
        assert "deepseek-ai/DeepSeek-R1" in model_ids
        assert "deepseek-ai/DeepSeek-V3" in model_ids
        assert "meta-llama/Meta-Llama-3.1-405B-Instruct" in model_ids
        assert "microsoft/Phi-3-medium-128k-instruct" in model_ids
        assert "microsoft/Phi-3-mini-4k-instruct" in model_ids
        assert "Qwen/Qwen2.5-7B-Instruct" in model_ids
        assert "Qwen/Qwen2.5-72B-Instruct" in model_ids
        assert "Qwen/Qwen3-4B" in model_ids

    def test_new_aliases_work(self):
        """Test that new model aliases resolve correctly"""
        # DeepSeek aliases
        assert resolve_simplismart_model("deepseek-r1") == "deepseek-ai/DeepSeek-R1"
        assert resolve_simplismart_model("deepseek-v3") == "deepseek-ai/DeepSeek-V3"

        # Llama 405B aliases
        assert (
            resolve_simplismart_model("llama-3.1-405b") == "meta-llama/Meta-Llama-3.1-405B-Instruct"
        )

        # Phi-3 aliases
        assert resolve_simplismart_model("phi-3-medium") == "microsoft/Phi-3-medium-128k-instruct"
        assert resolve_simplismart_model("phi-3-mini") == "microsoft/Phi-3-mini-4k-instruct"

        # Qwen aliases
        assert resolve_simplismart_model("qwen-2.5-7b") == "Qwen/Qwen2.5-7B-Instruct"
        assert resolve_simplismart_model("qwen-2.5-72b") == "Qwen/Qwen2.5-72B-Instruct"
        assert resolve_simplismart_model("qwen3-4b") == "Qwen/Qwen3-4B"


class TestSimplismartImageModels:
    """Test Simplismart image/diffusion model functionality"""

    def test_fetch_models_has_flux_models(self):
        """Test that catalog includes Flux image models"""
        models = fetch_models_from_simplismart()
        model_ids = [m["id"] for m in models]

        assert "simplismart/flux-1.1-pro" in model_ids
        assert "simplismart/flux-dev" in model_ids
        assert "simplismart/flux-kontext" in model_ids
        assert "simplismart/flux-1.1-pro-redux" in model_ids
        assert "simplismart/flux-pro-canny" in model_ids
        assert "simplismart/flux-pro-depth" in model_ids

    def test_fetch_models_has_sdxl(self):
        """Test that catalog includes SDXL"""
        models = fetch_models_from_simplismart()
        model_ids = [m["id"] for m in models]

        assert "simplismart/sdxl" in model_ids

    def test_image_models_have_type(self):
        """Test that image models have correct type"""
        models = fetch_models_from_simplismart()
        models_by_id = {m["id"]: m for m in models}

        assert models_by_id["simplismart/flux-1.1-pro"]["type"] == "text-to-image"
        assert models_by_id["simplismart/flux-dev"]["type"] == "text-to-image"
        assert models_by_id["simplismart/flux-kontext"]["type"] == "text-to-image"
        assert models_by_id["simplismart/flux-1.1-pro-redux"]["type"] == "image-to-image"
        assert models_by_id["simplismart/flux-pro-canny"]["type"] == "image-to-image"
        assert models_by_id["simplismart/flux-pro-depth"]["type"] == "image-to-image"
        assert models_by_id["simplismart/sdxl"]["type"] == "text-to-image"

    def test_image_models_have_per_image_pricing(self):
        """Test that image models have per_image pricing model"""
        models = fetch_models_from_simplismart()
        models_by_id = {m["id"]: m for m in models}

        for model_id in [
            "simplismart/flux-1.1-pro",
            "simplismart/flux-dev",
            "simplismart/flux-kontext",
            "simplismart/flux-1.1-pro-redux",
            "simplismart/flux-pro-canny",
            "simplismart/flux-pro-depth",
            "simplismart/sdxl",
        ]:
            pricing = models_by_id[model_id]["pricing"]
            assert pricing["pricing_model"] == "per_image"
            assert float(pricing["image"]) > 0

    def test_image_model_pricing_values(self):
        """Test specific image model pricing from simplismart.ai/pricing"""
        models = fetch_models_from_simplismart()
        models_by_id = {m["id"]: m for m in models}

        assert models_by_id["simplismart/flux-1.1-pro"]["pricing"]["image"] == "0.05"
        assert models_by_id["simplismart/flux-dev"]["pricing"]["image"] == "0.03"
        assert models_by_id["simplismart/flux-kontext"]["pricing"]["image"] == "0.04"
        assert models_by_id["simplismart/sdxl"]["pricing"]["image"] == "0.28"

    def test_flux_aliases_resolve_correctly(self):
        """Test Flux image model aliases"""
        assert resolve_simplismart_model("flux-1.1-pro") == "simplismart/flux-1.1-pro"
        assert resolve_simplismart_model("flux-pro") == "simplismart/flux-1.1-pro"
        assert resolve_simplismart_model("flux-dev") == "simplismart/flux-dev"
        assert resolve_simplismart_model("flux-kontext") == "simplismart/flux-kontext"
        assert resolve_simplismart_model("flux-pro-redux") == "simplismart/flux-1.1-pro-redux"
        assert resolve_simplismart_model("flux-canny") == "simplismart/flux-pro-canny"
        assert resolve_simplismart_model("flux-depth") == "simplismart/flux-pro-depth"
        assert resolve_simplismart_model("sdxl") == "simplismart/sdxl"
        assert resolve_simplismart_model("stable-diffusion-xl") == "simplismart/sdxl"


class TestSimplismartSpeechModels:
    """Test Simplismart speech-to-text model functionality"""

    def test_fetch_models_has_whisper_models(self):
        """Test that catalog includes Whisper models"""
        models = fetch_models_from_simplismart()
        model_ids = [m["id"] for m in models]

        assert "simplismart/whisper-large-v2" in model_ids
        assert "simplismart/whisper-large-v3" in model_ids
        assert "simplismart/whisper-v3-turbo" in model_ids

    def test_whisper_models_have_type(self):
        """Test that Whisper models have correct type"""
        models = fetch_models_from_simplismart()
        models_by_id = {m["id"]: m for m in models}

        assert models_by_id["simplismart/whisper-large-v2"]["type"] == "speech-to-text"
        assert models_by_id["simplismart/whisper-large-v3"]["type"] == "speech-to-text"
        assert models_by_id["simplismart/whisper-v3-turbo"]["type"] == "speech-to-text"

    def test_whisper_models_have_per_minute_pricing(self):
        """Test that Whisper models have per_minute pricing model"""
        models = fetch_models_from_simplismart()
        models_by_id = {m["id"]: m for m in models}

        for model_id in [
            "simplismart/whisper-large-v2",
            "simplismart/whisper-large-v3",
            "simplismart/whisper-v3-turbo",
        ]:
            pricing = models_by_id[model_id]["pricing"]
            assert pricing["pricing_model"] == "per_minute"
            assert float(pricing["request"]) > 0

    def test_whisper_model_pricing_values(self):
        """Test specific Whisper model pricing from simplismart.ai/pricing"""
        models = fetch_models_from_simplismart()
        models_by_id = {m["id"]: m for m in models}

        assert models_by_id["simplismart/whisper-large-v2"]["pricing"]["request"] == "0.0028"
        assert models_by_id["simplismart/whisper-large-v3"]["pricing"]["request"] == "0.0030"
        assert models_by_id["simplismart/whisper-v3-turbo"]["pricing"]["request"] == "0.0018"

    def test_whisper_aliases_resolve_correctly(self):
        """Test Whisper model aliases"""
        assert resolve_simplismart_model("whisper-large-v2") == "simplismart/whisper-large-v2"
        assert resolve_simplismart_model("whisper-v2") == "simplismart/whisper-large-v2"
        assert resolve_simplismart_model("whisper-large-v3") == "simplismart/whisper-large-v3"
        assert resolve_simplismart_model("whisper-v3") == "simplismart/whisper-large-v3"
        assert resolve_simplismart_model("whisper-v3-turbo") == "simplismart/whisper-v3-turbo"
        assert resolve_simplismart_model("whisper-turbo") == "simplismart/whisper-v3-turbo"


class TestSimplismartIsModelWithNewTypes:
    """Test is_simplismart_model function with new model types"""

    def test_is_simplismart_model_flux(self):
        """Test checking Flux image models"""
        assert is_simplismart_model("flux-1.1-pro") is True
        assert is_simplismart_model("flux-dev") is True
        assert is_simplismart_model("sdxl") is True
        assert is_simplismart_model("simplismart/flux-1.1-pro") is True

    def test_is_simplismart_model_whisper(self):
        """Test checking Whisper speech models"""
        assert is_simplismart_model("whisper-large-v3") is True
        assert is_simplismart_model("whisper-turbo") is True
        assert is_simplismart_model("simplismart/whisper-v3-turbo") is True
