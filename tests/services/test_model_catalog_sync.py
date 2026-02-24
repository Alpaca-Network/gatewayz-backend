"""Tests for model catalog sync functionality."""

from src.services.model_catalog_sync import (
    PROVIDER_FETCH_FUNCTIONS,
    extract_capabilities,
    extract_modality,
    extract_pricing,
    transform_normalized_model_to_db_schema,
)


class TestTransformNormalizedModelToDbSchema:
    """Tests for transform_normalized_model_to_db_schema function."""

    def test_uses_explicit_provider_model_id(self):
        """Test that explicit provider_model_id is used when available.

        This is critical for providers like Google Vertex where the canonical
        model ID (e.g., 'gemini-3-flash') differs from the actual provider
        model ID used in API requests (e.g., 'gemini-3-flash-preview').
        """
        normalized_model = {
            "id": "gemini-3-flash",
            "name": "Gemini 3 Flash",
            "description": "Fast multimodal model",
            "context_length": 1000000,
            "provider_model_id": "gemini-3-flash-preview",  # Explicit provider model ID
            "pricing": {"prompt": "0.0005", "completion": "0.003"},
            "architecture": {"modality": "text->text"},
            "source_gateway": "google-vertex",
        }

        result = transform_normalized_model_to_db_schema(
            normalized_model, provider_id=1, provider_slug="google-vertex"
        )

        assert result is not None
        assert result["model_id"] == "gemini-3-flash"
        assert result["provider_model_id"] == "gemini-3-flash-preview"

    def test_falls_back_to_model_id_when_provider_model_id_missing(self):
        """Test that model_id is used when provider_model_id is not specified."""
        normalized_model = {
            "id": "gpt-4",
            "name": "GPT-4",
            "description": "OpenAI GPT-4",
            "context_length": 8192,
            # No provider_model_id - should fall back to id
            "pricing": {"prompt": "0.03", "completion": "0.06"},
            "architecture": {"modality": "text->text"},
            "source_gateway": "openai",
        }

        result = transform_normalized_model_to_db_schema(
            normalized_model, provider_id=2, provider_slug="openai"
        )

        assert result is not None
        assert result["model_id"] == "gpt-4"
        assert result["provider_model_id"] == "gpt-4"

    def test_handles_none_provider_model_id(self):
        """Test that None provider_model_id falls back to model_id."""
        normalized_model = {
            "id": "claude-3-opus",
            "name": "Claude 3 Opus",
            "description": "Anthropic Claude 3 Opus",
            "context_length": 200000,
            "provider_model_id": None,  # Explicit None
            "pricing": {"prompt": "0.015", "completion": "0.075"},
            "architecture": {"modality": "text->text"},
        }

        result = transform_normalized_model_to_db_schema(
            normalized_model, provider_id=3, provider_slug="anthropic"
        )

        assert result is not None
        assert result["model_id"] == "claude-3-opus"
        assert result["provider_model_id"] == "claude-3-opus"


class TestGoogleVertexProviderModelId:
    """Tests specifically for Google Vertex model ID handling."""

    def test_gemini_3_flash_provider_model_id(self):
        """Test that Gemini 3 Flash models have correct provider_model_id."""
        from src.services.google_vertex_client import fetch_models_from_google_vertex

        models = fetch_models_from_google_vertex()

        # Find gemini-3-flash
        gemini_3_flash = next((m for m in models if m["id"] == "gemini-3-flash"), None)
        assert gemini_3_flash is not None, "gemini-3-flash model not found in catalog"

        # Transform to DB schema
        result = transform_normalized_model_to_db_schema(
            gemini_3_flash, provider_id=1, provider_slug="google-vertex"
        )

        assert result is not None
        assert result["model_id"] == "gemini-3-flash"
        assert (
            result["provider_model_id"] == "gemini-3-flash-preview"
        ), "Gemini 3 Flash should have provider_model_id 'gemini-3-flash-preview' for proper DB lookup"

    def test_gemini_3_pro_provider_model_id(self):
        """Test that Gemini 3 Pro models have correct provider_model_id."""
        from src.services.google_vertex_client import fetch_models_from_google_vertex

        models = fetch_models_from_google_vertex()

        # Find gemini-3-pro
        gemini_3_pro = next((m for m in models if m["id"] == "gemini-3-pro"), None)
        assert gemini_3_pro is not None, "gemini-3-pro model not found in catalog"

        # Transform to DB schema
        result = transform_normalized_model_to_db_schema(
            gemini_3_pro, provider_id=1, provider_slug="google-vertex"
        )

        assert result is not None
        assert result["model_id"] == "gemini-3-pro"
        assert (
            result["provider_model_id"] == "gemini-3-pro-preview"
        ), "Gemini 3 Pro should have provider_model_id 'gemini-3-pro-preview' for proper DB lookup"

    def test_gemini_2_models_same_provider_model_id(self):
        """Test that Gemini 2.x models have same model_id and provider_model_id."""
        from src.services.google_vertex_client import fetch_models_from_google_vertex

        models = fetch_models_from_google_vertex()

        # Find gemini-2.5-flash
        gemini_25_flash = next((m for m in models if m["id"] == "gemini-2.5-flash"), None)
        assert gemini_25_flash is not None, "gemini-2.5-flash model not found in catalog"

        # Transform to DB schema
        result = transform_normalized_model_to_db_schema(
            gemini_25_flash, provider_id=1, provider_slug="google-vertex"
        )

        assert result is not None
        assert result["model_id"] == "gemini-2.5-flash"
        # Gemini 2.x models use the same ID as provider_model_id
        assert result["provider_model_id"] == "gemini-2.5-flash"


class TestExtractModality:
    """Tests for extract_modality function."""

    def test_extract_from_architecture(self):
        """Test modality extraction from architecture field."""
        model = {"architecture": {"modality": "text->text"}}
        assert extract_modality(model) == "text->text"

    def test_extract_from_top_level(self):
        """Test modality extraction from top-level field."""
        model = {"modality": "text->image"}
        assert extract_modality(model) == "text->image"

    def test_default_modality(self):
        """Test default modality when not specified."""
        model = {}
        assert extract_modality(model) == "text->text"


class TestExtractPricing:
    """Tests for extract_pricing function."""

    def test_extract_valid_pricing(self):
        """Test pricing extraction with valid values."""
        model = {
            "pricing": {
                "prompt": "0.001",
                "completion": "0.002",
                "image": "0.01",
                "request": "0.0001",
            }
        }
        pricing = extract_pricing(model)
        assert pricing["prompt"] is not None
        assert pricing["completion"] is not None

    def test_extract_missing_pricing(self):
        """Test pricing extraction with missing pricing."""
        model = {}
        pricing = extract_pricing(model)
        assert pricing["prompt"] is None
        assert pricing["completion"] is None


class TestExtractCapabilities:
    """Tests for extract_capabilities function."""

    def test_extract_vision_support(self):
        """Test vision support extraction."""
        model = {"architecture": {"input_modalities": ["text", "image"]}}
        caps = extract_capabilities(model)
        assert caps["supports_vision"] is True

    def test_no_vision_support(self):
        """Test no vision support."""
        model = {"architecture": {"input_modalities": ["text"]}}
        caps = extract_capabilities(model)
        assert caps["supports_vision"] is False


class TestProviderFetchFunctionsRegistry:
    """Tests for PROVIDER_FETCH_FUNCTIONS registry completeness."""

    def test_openai_registered(self):
        """Test that OpenAI provider is registered."""
        assert "openai" in PROVIDER_FETCH_FUNCTIONS
        assert callable(PROVIDER_FETCH_FUNCTIONS["openai"])

    def test_anthropic_registered(self):
        """Test that Anthropic provider is registered."""
        assert "anthropic" in PROVIDER_FETCH_FUNCTIONS
        assert callable(PROVIDER_FETCH_FUNCTIONS["anthropic"])

    def test_clarifai_registered(self):
        """Test that Clarifai provider is registered."""
        assert "clarifai" in PROVIDER_FETCH_FUNCTIONS
        assert callable(PROVIDER_FETCH_FUNCTIONS["clarifai"])

    def test_simplismart_registered(self):
        """Test that SimpliSmart provider is registered."""
        assert "simplismart" in PROVIDER_FETCH_FUNCTIONS
        assert callable(PROVIDER_FETCH_FUNCTIONS["simplismart"])

    def test_onerouter_registered(self):
        """Test that OneRouter provider is registered."""
        assert "onerouter" in PROVIDER_FETCH_FUNCTIONS
        assert callable(PROVIDER_FETCH_FUNCTIONS["onerouter"])

    def test_cloudflare_workers_ai_registered(self):
        """Test that Cloudflare Workers AI provider is registered."""
        assert "cloudflare-workers-ai" in PROVIDER_FETCH_FUNCTIONS
        assert callable(PROVIDER_FETCH_FUNCTIONS["cloudflare-workers-ai"])

    def test_modelz_registered(self):
        """Test that Modelz provider is registered."""
        assert "modelz" in PROVIDER_FETCH_FUNCTIONS
        assert callable(PROVIDER_FETCH_FUNCTIONS["modelz"])

    def test_all_core_providers_registered(self):
        """Test that all core providers have fetch functions registered."""
        expected_providers = [
            "openrouter",
            "deepinfra",
            "featherless",
            "chutes",
            "groq",
            "fireworks",
            "together",
            "aimo",
            "near",
            "fal",
            "vercel-ai-gateway",
            "aihubmix",
            "helicone",
            "anannas",
            "alibaba",
            "huggingface",
            "cerebras",
            "google-vertex",
            "xai",
            "nebius",
            "novita",
            "openai",
            "anthropic",
            "clarifai",
            "simplismart",
            "onerouter",
            "cloudflare-workers-ai",
            "modelz",
        ]
        for provider in expected_providers:
            assert provider in PROVIDER_FETCH_FUNCTIONS, f"Provider '{provider}' not registered"
            assert callable(
                PROVIDER_FETCH_FUNCTIONS[provider]
            ), f"Provider '{provider}' fetch function is not callable"

    def test_provider_count(self):
        """Test that we have the expected number of providers registered."""
        # 28 providers total after adding the missing ones
        assert (
            len(PROVIDER_FETCH_FUNCTIONS) >= 28
        ), f"Expected at least 28 providers, got {len(PROVIDER_FETCH_FUNCTIONS)}"
