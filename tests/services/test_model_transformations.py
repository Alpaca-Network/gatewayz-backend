from unittest.mock import patch
from src.services.model_transformations import (
    transform_model_id,
    detect_provider_from_model_id,
    parse_model_id,
    extract_provider_hint,
    normalize_to_canonical_id,
    ParsedModelId,
)


def test_openrouter_prefixed_model_keeps_nested_provider():
    result = transform_model_id("openrouter/openai/gpt-4", "openrouter")
    assert result == "openai/gpt-4"


def test_openrouter_gpt51_hyphen_alias_transforms():
    result = transform_model_id("openai/gpt-5-1", "openrouter")
    assert result == "openai/gpt-5.1"


def test_detect_provider_gpt51_alias_without_org():
    assert detect_provider_from_model_id("gpt-5-1") == "openrouter"


def test_openrouter_auto_preserves_prefix():
    result = transform_model_id("openrouter/auto", "openrouter")
    assert result == "openrouter/auto"


def test_openrouter_auto_transforms_for_huggingface():
    result = transform_model_id("openrouter/auto", "huggingface")
    assert result == "meta-llama/Llama-3.3-70B-Instruct"


def test_openrouter_auto_transforms_for_cerebras():
    result = transform_model_id("openrouter/auto", "cerebras")
    assert result == "llama-3.3-70b"


def test_detect_provider_from_model_id_fal_ai():
    """Test that fal-ai models are detected as 'fal' provider"""
    result = detect_provider_from_model_id("fal-ai/stable-diffusion-v15")
    assert result == "fal"


def test_detect_provider_from_model_id_fal_orgs():
    """Test that various Fal-related orgs are detected as 'fal' provider"""
    test_cases = [
        "fal/some-model",
        "minimax/video-01",
        "stabilityai/stable-diffusion-xl",
        "hunyuan3d/some-model",
        "meshy/mesh-model",
        "tripo3d/3d-model"
    ]

    for model_id in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == "fal", f"Expected 'fal' for {model_id}, got {result}"


def test_detect_provider_from_model_id_existing_providers():
    """Test that existing provider detection still works"""
    test_cases = [
        ("anthropic/claude-3-sonnet", "openrouter"),
        ("openai/gpt-4", "openrouter"),
        ("meta-llama/llama-2-7b", None),  # This model doesn't match any specific provider
    ]

    for model_id, expected in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


@patch.dict('os.environ', {'GOOGLE_VERTEX_CREDENTIALS_JSON': '{"type":"service_account"}'})
def test_detect_provider_google_vertex_models():
    """Test that Google Vertex AI models are correctly detected when credentials are available"""
    test_cases = [
        ("gemini-2.5-flash", "google-vertex"),
        ("gemini-2.0-flash", "google-vertex"),
        ("gemini-1.5-pro", "google-vertex"),
        ("google/gemini-2.5-flash", "google-vertex"),
        ("google/gemini-2.0-flash", "google-vertex"),
        ("@google/models/gemini-2.5-flash", "google-vertex"),  # Key test case - should NOT be portkey
        ("@google/models/gemini-2.0-flash", "google-vertex"),
    ]

    for model_id, expected in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_detect_provider_at_prefix_models():
    """Test that models with @ prefix are routed to OpenRouter after Portkey removal

    Previously these were routed to Portkey, but Portkey has been removed.
    Now these models are detected by OpenRouter model catalog.
    """
    test_cases = [
        ("@anthropic/claude-3-sonnet", "openrouter"),
        ("@openai/gpt-4", "openrouter"),
    ]

    for model_id, expected in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_z_ai_glm_with_exacto_suffix():
    """Test that z-ai/glm-4.6:exacto is correctly detected as OpenRouter"""
    # Test provider detection - :exacto suffix indicates OpenRouter
    result = detect_provider_from_model_id("z-ai/glm-4.6:exacto")
    assert result == "openrouter", f"Expected 'openrouter' for z-ai/glm-4.6:exacto, got {result}"

    # Test model transformation - OpenRouter passes through as-is (lowercase)
    transformed = transform_model_id("z-ai/glm-4.6:exacto", "openrouter")
    assert transformed == "z-ai/glm-4.6:exacto", f"Expected 'z-ai/glm-4.6:exacto', got {transformed}"


def test_openrouter_colon_suffix_variants():
    """Test that OpenRouter models with colon suffixes are correctly detected"""
    test_cases = [
        ("z-ai/glm-4.6:exacto", "openrouter"),
        ("google/gemini-2.0-flash-exp:free", "openrouter"),
        ("anthropic/claude-3-opus:extended", "openrouter"),
    ]

    for model_id, expected_provider in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == expected_provider, f"Expected '{expected_provider}' for {model_id}, got {result}"


def test_detect_provider_groq_models():
    """Test that Groq models are correctly detected as 'groq' provider"""
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


def test_transform_groq_model_strips_prefix():
    """Test that groq/ prefix is stripped when transforming for Groq provider"""
    test_cases = [
        ("groq/llama-3.3-70b-versatile", "llama-3.3-70b-versatile"),
        ("groq/mixtral-8x7b-32768", "mixtral-8x7b-32768"),
        ("groq/gemma2-9b-it", "gemma2-9b-it"),
    ]

    for model_id, expected in test_cases:
        result = transform_model_id(model_id, "groq")
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_transform_groq_model_without_prefix():
    """Test that Groq models without prefix pass through correctly"""
    test_cases = [
        ("llama-3.3-70b-versatile", "llama-3.3-70b-versatile"),
        ("mixtral-8x7b-32768", "mixtral-8x7b-32768"),
        ("llama3-70b-8192", "llama3-70b-8192"),
    ]

    for model_id, expected in test_cases:
        result = transform_model_id(model_id, "groq")
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


# ============================================================================
# Tests for researcher/model:provider naming convention
# ============================================================================


class TestParseModelId:
    """Tests for the parse_model_id function"""

    def test_parse_simple_model(self):
        """Test parsing a simple model name without org or provider"""
        result = parse_model_id("gpt-4")
        assert result is not None
        assert result.canonical_id == "gpt-4"
        assert result.researcher is None
        assert result.model_name == "gpt-4"
        assert result.provider_hint is None

    def test_parse_researcher_model(self):
        """Test parsing researcher/model format"""
        result = parse_model_id("meta-llama/llama-3.3-70b")
        assert result is not None
        assert result.canonical_id == "meta-llama/llama-3.3-70b"
        assert result.researcher == "meta-llama"
        assert result.model_name == "llama-3.3-70b"
        assert result.provider_hint is None

    def test_parse_researcher_model_provider(self):
        """Test parsing researcher/model:provider format"""
        result = parse_model_id("meta-llama/llama-3.3-70b:fireworks")
        assert result is not None
        assert result.canonical_id == "meta-llama/llama-3.3-70b"
        assert result.researcher == "meta-llama"
        assert result.model_name == "llama-3.3-70b"
        assert result.provider_hint == "fireworks"

    def test_parse_model_with_provider(self):
        """Test parsing model:provider format (no researcher)"""
        result = parse_model_id("gpt-4:openrouter")
        assert result is not None
        assert result.canonical_id == "gpt-4"
        assert result.researcher is None
        assert result.model_name == "gpt-4"
        assert result.provider_hint == "openrouter"

    def test_parse_openrouter_exacto_suffix(self):
        """Test that :exacto is NOT treated as a provider hint"""
        result = parse_model_id("z-ai/glm-4.6:exacto")
        assert result is not None
        assert result.canonical_id == "z-ai/glm-4.6:exacto"
        assert result.researcher == "z-ai"
        assert result.model_name == "glm-4.6:exacto"
        assert result.provider_hint is None  # NOT a provider hint

    def test_parse_openrouter_free_suffix(self):
        """Test that :free is NOT treated as a provider hint"""
        result = parse_model_id("google/gemini-2.0-flash-exp:free")
        assert result is not None
        assert result.canonical_id == "google/gemini-2.0-flash-exp:free"
        assert result.provider_hint is None

    def test_parse_openrouter_extended_suffix(self):
        """Test that :extended is NOT treated as a provider hint"""
        result = parse_model_id("anthropic/claude-3-opus:extended")
        assert result is not None
        assert result.canonical_id == "anthropic/claude-3-opus:extended"
        assert result.provider_hint is None

    def test_parse_at_prefix_model(self):
        """Test parsing models with @ prefix"""
        result = parse_model_id("@google/models/gemini-2.5-flash")
        assert result is not None
        assert result.canonical_id == "@google/models/gemini-2.5-flash"
        assert result.researcher == "@google"
        assert result.model_name == "models/gemini-2.5-flash"

    def test_parse_none_returns_none(self):
        """Test that None input returns None"""
        result = parse_model_id(None)
        assert result is None

    def test_parse_empty_string_returns_none(self):
        """Test that empty string returns None"""
        result = parse_model_id("")
        assert result is None

    def test_provider_hint_is_lowercase(self):
        """Test that provider hint is normalized to lowercase"""
        result = parse_model_id("meta-llama/llama-3.3-70b:FIREWORKS")
        assert result is not None
        assert result.provider_hint == "fireworks"

    def test_full_id_property(self):
        """Test the full_id property"""
        result = parse_model_id("meta-llama/llama-3.3-70b:fireworks")
        assert result is not None
        assert result.full_id == "meta-llama/llama-3.3-70b:fireworks"

        result2 = parse_model_id("meta-llama/llama-3.3-70b")
        assert result2 is not None
        assert result2.full_id == "meta-llama/llama-3.3-70b"


class TestExtractProviderHint:
    """Tests for the extract_provider_hint function"""

    def test_extract_with_provider(self):
        """Test extracting provider hint when present"""
        canonical, hint = extract_provider_hint("meta-llama/llama-3.3-70b:fireworks")
        assert canonical == "meta-llama/llama-3.3-70b"
        assert hint == "fireworks"

    def test_extract_without_provider(self):
        """Test extracting when no provider hint"""
        canonical, hint = extract_provider_hint("meta-llama/llama-3.3-70b")
        assert canonical == "meta-llama/llama-3.3-70b"
        assert hint is None

    def test_extract_openrouter_suffix(self):
        """Test that OpenRouter suffixes are not extracted as provider hints"""
        canonical, hint = extract_provider_hint("z-ai/glm-4.6:exacto")
        assert canonical == "z-ai/glm-4.6:exacto"
        assert hint is None


class TestNormalizeToCanonicalId:
    """Tests for the normalize_to_canonical_id function"""

    def test_normalize_with_provider(self):
        """Test normalizing removes provider hint"""
        result = normalize_to_canonical_id("meta-llama/llama-3.3-70b:fireworks")
        assert result == "meta-llama/llama-3.3-70b"

    def test_normalize_without_provider(self):
        """Test normalizing without provider hint returns same"""
        result = normalize_to_canonical_id("meta-llama/llama-3.3-70b")
        assert result == "meta-llama/llama-3.3-70b"

    def test_normalize_none(self):
        """Test normalizing None returns None"""
        result = normalize_to_canonical_id(None)
        assert result is None


class TestDetectProviderWithHint:
    """Tests for detect_provider_from_model_id with :provider suffix"""

    def test_explicit_provider_hint_takes_precedence(self):
        """Test that :provider suffix takes precedence over auto-detection"""
        # Without hint, this would auto-detect based on mapping
        # With hint, it should use the explicit provider
        result = detect_provider_from_model_id("meta-llama/llama-3.3-70b:cerebras")
        assert result == "cerebras"

    def test_explicit_fireworks_hint(self):
        """Test explicit :fireworks provider hint"""
        result = detect_provider_from_model_id("deepseek-ai/deepseek-v3:fireworks")
        assert result == "fireworks"

    def test_explicit_openrouter_hint(self):
        """Test explicit :openrouter provider hint"""
        result = detect_provider_from_model_id("openai/gpt-4:openrouter")
        assert result == "openrouter"

    def test_explicit_huggingface_hint(self):
        """Test explicit :huggingface provider hint"""
        result = detect_provider_from_model_id("meta-llama/llama-3.3-70b:huggingface")
        assert result == "huggingface"

    def test_openrouter_special_suffixes_not_treated_as_hints(self):
        """Test that :exacto/:free/:extended are NOT provider hints"""
        # These should still route to OpenRouter, but via the special suffix logic
        result = detect_provider_from_model_id("z-ai/glm-4.6:exacto")
        assert result == "openrouter"

        result = detect_provider_from_model_id("google/gemini-2.0-flash-exp:free")
        assert result == "openrouter"


class TestTransformModelIdWithHint:
    """Tests for transform_model_id with :provider suffix"""

    def test_transform_with_provider_hint(self):
        """Test that model transformation works with :provider suffix"""
        # The :fireworks hint should be stripped for transformation
        result = transform_model_id("deepseek-ai/deepseek-v3:fireworks", "fireworks")
        assert result == "accounts/fireworks/models/deepseek-v3p1"

    def test_transform_strips_provider_hint(self):
        """Test that provider hint is stripped before transformation"""
        # Even with :cerebras hint, if we're transforming for fireworks,
        # we should get the fireworks format
        result = transform_model_id("meta-llama/llama-3.3-70b:cerebras", "fireworks")
        assert result == "accounts/fireworks/models/llama-v3p3-70b-instruct"

    def test_transform_cerebras_with_hint(self):
        """Test Cerebras transformation with provider hint"""
        result = transform_model_id("meta-llama/llama-3.3-70b:cerebras", "cerebras")
        assert result == "llama-3.3-70b"


class TestBackwardsCompatibility:
    """Tests to ensure backwards compatibility with existing formats"""

    def test_old_provider_model_format_still_works(self):
        """Test that old provider/model format still works"""
        # These should still be detected correctly
        assert detect_provider_from_model_id("groq/llama-3.3-70b-versatile") == "groq"
        assert detect_provider_from_model_id("cerebras/llama-3.3-70b") == "cerebras"

    def test_old_transform_still_works(self):
        """Test that old transformations still work"""
        result = transform_model_id("deepseek-ai/deepseek-v3", "fireworks")
        assert result == "accounts/fireworks/models/deepseek-v3p1"

    def test_openrouter_nested_format_still_works(self):
        """Test that openrouter/provider/model format still works"""
        result = transform_model_id("openrouter/openai/gpt-4", "openrouter")
        assert result == "openai/gpt-4"
