from unittest.mock import patch
from src.services.model_transformations import transform_model_id, detect_provider_from_model_id


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
    # Note: gemini-1.5-pro was removed as it's retired on Vertex AI (April-September 2025)
    test_cases = [
        ("gemini-2.5-flash", "google-vertex"),
        ("gemini-2.0-flash", "google-vertex"),
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
# OneRouter Provider Tests
# ============================================================================

def test_detect_provider_onerouter_prefixed_models():
    """Test that onerouter/ prefixed models are detected as 'onerouter' provider"""
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


def test_detect_provider_onerouter_versioned_models():
    """Test that OneRouter models with @ version suffix are detected correctly"""
    test_cases = [
        ("claude-3-5-sonnet@20240620", "onerouter"),
        ("gpt-4@latest", "onerouter"),
        ("gpt-4o@latest", "onerouter"),
        ("gpt-3.5-turbo@latest", "onerouter"),
    ]

    for model_id, expected in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_transform_onerouter_strips_prefix():
    """Test that onerouter/ prefix is stripped when transforming for OneRouter provider"""
    test_cases = [
        ("onerouter/claude-3-5-sonnet", "claude-3-5-sonnet@20240620"),
        ("onerouter/gpt-4", "gpt-4@latest"),
        ("onerouter/gpt-4o", "gpt-4o@latest"),
        ("onerouter/gpt-3.5-turbo", "gpt-3.5-turbo@latest"),
    ]

    for model_id, expected in test_cases:
        result = transform_model_id(model_id, "onerouter")
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_transform_onerouter_passthrough_versioned():
    """Test that versioned OneRouter models pass through correctly"""
    test_cases = [
        ("claude-3-5-sonnet@20240620", "claude-3-5-sonnet@20240620"),
        ("gpt-4@latest", "gpt-4@latest"),
        ("gpt-4o@latest", "gpt-4o@latest"),
    ]

    for model_id, expected in test_cases:
        result = transform_model_id(model_id, "onerouter")
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_transform_onerouter_simple_names():
    """Test that simple model names get @ version suffix added"""
    test_cases = [
        ("claude-3-5-sonnet", "claude-3-5-sonnet@20240620"),
        ("gpt-4", "gpt-4@latest"),
        ("gpt-4o", "gpt-4o@latest"),
        ("gpt-3.5-turbo", "gpt-3.5-turbo@latest"),
    ]

    for model_id, expected in test_cases:
        result = transform_model_id(model_id, "onerouter")
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_transform_onerouter_unknown_model_passthrough():
    """Test that unknown models pass through as lowercase"""
    # Unknown models should pass through (lowercased)
    result = transform_model_id("some-unknown-model", "onerouter")
    assert result == "some-unknown-model", f"Expected passthrough, got {result}"

    result = transform_model_id("org/custom-model", "onerouter")
    assert result == "org/custom-model", f"Expected passthrough, got {result}"


def test_onerouter_model_id_mapping_exists():
    """Test that OneRouter has model ID mappings defined"""
    from src.services.model_transformations import get_model_id_mapping

    mapping = get_model_id_mapping("onerouter")
    assert mapping is not None
    assert len(mapping) > 0
    assert "onerouter/claude-3-5-sonnet" in mapping
    assert "gpt-4" in mapping


# ============================================================================
# DeepSeek V3.2 Model Tests (Fix for 404 NotFoundError)
# ============================================================================

def test_deepseek_v32_speciale_alias():
    """Test that deepseek-v3.2-speciale variants are properly aliased"""
    from src.services.model_transformations import apply_model_alias

    test_cases = [
        ("deepseek-v3.2-speciale", "deepseek/deepseek-v3.2"),
        ("deepseek/deepseek-v3.2-speciale", "deepseek/deepseek-v3.2"),
        ("deepseek-ai/deepseek-v3.2-speciale", "deepseek/deepseek-v3.2"),
        ("deepseek-v3.2-exp", "deepseek/deepseek-v3.2"),
        ("deepseek-v3.2-experimental", "deepseek/deepseek-v3.2"),
    ]

    for model_id, expected in test_cases:
        result = apply_model_alias(model_id)
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_deepseek_v32_detect_provider_routes_to_chutes():
    """Test that DeepSeek V3.2 models are routed to Chutes (has DeepSeek-V3.2-Exp)"""
    test_cases = [
        ("deepseek/deepseek-v3.2", "chutes"),
        ("deepseek-ai/deepseek-v3.2", "chutes"),
    ]

    for model_id, expected in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_deepseek_v32_transform_for_chutes():
    """Test that DeepSeek V3.2 is correctly transformed for Chutes provider"""
    test_cases = [
        ("deepseek/deepseek-v3.2", "deepseek-ai/DeepSeek-V3.2-Exp"),
        ("deepseek-ai/deepseek-v3.2", "deepseek-ai/DeepSeek-V3.2-Exp"),
        ("deepseek-v3.2", "deepseek-ai/DeepSeek-V3.2-Exp"),
    ]

    for model_id, expected in test_cases:
        result = transform_model_id(model_id, "chutes")
        # Result is lowercased, so compare case-insensitively
        assert result.lower() == expected.lower(), f"Expected '{expected}' for {model_id}, got {result}"


def test_deepseek_v32_transform_for_fireworks_fallback():
    """Test that DeepSeek V3.2 falls back to v3p1 on Fireworks (Fireworks doesn't have v3.2)"""
    test_cases = [
        ("deepseek/deepseek-v3.2", "accounts/fireworks/models/deepseek-v3p1"),
        ("deepseek-ai/deepseek-v3.2", "accounts/fireworks/models/deepseek-v3p1"),
        ("deepseek-v3.2", "accounts/fireworks/models/deepseek-v3p1"),
    ]

    for model_id, expected in test_cases:
        result = transform_model_id(model_id, "fireworks")
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_deepseek_v32_transform_for_openrouter():
    """Test that DeepSeek V3.2 is correctly transformed for OpenRouter"""
    test_cases = [
        ("deepseek/deepseek-v3.2", "deepseek/deepseek-chat"),
        ("deepseek-ai/deepseek-v3.2", "deepseek/deepseek-chat"),
    ]

    for model_id, expected in test_cases:
        result = transform_model_id(model_id, "openrouter")
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_deepseek_unknown_variant_fallback_on_fireworks():
    """Test that unknown DeepSeek variants use sensible fallbacks on Fireworks"""
    # This tests the improved fallback logic that prevents constructing invalid model IDs
    test_cases = [
        ("deepseek/deepseek-v3.99-unknown", "accounts/fireworks/models/deepseek-v3p1"),
        ("deepseek-ai/deepseek-v4-future", "accounts/fireworks/models/deepseek-v3p1"),
        # R1 variants should fall back to R1
        ("deepseek/deepseek-r1-custom", "accounts/fireworks/models/deepseek-r1-0528"),
    ]

    for model_id, expected in test_cases:
        result = transform_model_id(model_id, "fireworks")
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_deepseek_standard_models_unchanged():
    """Test that standard DeepSeek models still work correctly"""
    # Ensure our changes don't break existing behavior
    test_cases = [
        ("deepseek/deepseek-v3", "fireworks", "accounts/fireworks/models/deepseek-v3p1"),
        ("deepseek-ai/deepseek-v3.1", "fireworks", "accounts/fireworks/models/deepseek-v3p1"),
        ("deepseek/deepseek-r1", "fireworks", "accounts/fireworks/models/deepseek-r1-0528"),
        ("deepseek-ai/deepseek-v3", "openrouter", "deepseek/deepseek-chat"),
    ]

    for model_id, provider, expected in test_cases:
        result = transform_model_id(model_id, provider)
        assert result == expected, f"Expected '{expected}' for {model_id}@{provider}, got {result}"
