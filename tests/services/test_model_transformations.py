from unittest.mock import patch

from src.services.model_transformations import detect_provider_from_model_id, transform_model_id


def test_openrouter_prefixed_model_keeps_nested_provider():
    result = transform_model_id("openrouter/openai/gpt-4", "openrouter")
    assert result == "openai/gpt-4"


def test_openrouter_gpt51_hyphen_alias_transforms():
    result = transform_model_id("openai/gpt-5-1", "openrouter")
    assert result == "openai/gpt-5.1"


def test_detect_provider_gpt51_alias_without_org():
    """Test that gpt-5-1 alias routes to native OpenAI provider.

    gpt-5-1 gets aliased to openai/gpt-5.1, which should route to native OpenAI first.
    Failover to OpenRouter is handled by provider_failover.py.
    """
    assert detect_provider_from_model_id("gpt-5-1") == "openai"


def test_bare_openai_model_names_alias_to_canonical():
    """Test that bare OpenAI model names (without openai/ prefix) are aliased correctly.

    This is critical to prevent OpenAI models from being incorrectly routed to
    other providers like HuggingFace during failover.
    """
    from src.services.model_transformations import apply_model_alias

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


def test_bare_openai_model_names_detect_as_native_openai():
    """Test that bare OpenAI model names are detected as native OpenAI provider.

    After aliasing, these should all be detected as the native 'openai' provider
    (via the openai/ prefix). The failover to OpenRouter is handled separately.
    """
    test_cases = [
        "gpt-4",
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
    ]

    for model_id in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == "openai", f"Expected 'openai' for {model_id}, got {result}"


def test_bare_anthropic_model_names_alias_to_canonical():
    """Test that bare Anthropic/Claude model names (without anthropic/ prefix) are aliased correctly.

    This is critical to prevent Claude models from being incorrectly routed to
    other providers like HuggingFace during failover.
    """
    from src.services.model_transformations import apply_model_alias

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


def test_bare_anthropic_model_names_detect_as_native_anthropic():
    """Test that bare Anthropic/Claude model names are detected as native Anthropic provider.

    After aliasing, these should all be detected as the native 'anthropic' provider
    (via the anthropic/ prefix). The failover to OpenRouter is handled separately.
    """
    test_cases = [
        "claude-3-opus",
        "claude-3-sonnet",
        "claude-3-haiku",
        "claude-3.5-sonnet",
        "claude-3.5-haiku",
        "claude-3.7-sonnet",
        "claude-sonnet-4",
        "claude-opus-4",
    ]

    for model_id in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == "anthropic", f"Expected 'anthropic' for {model_id}, got {result}"


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
        "tripo3d/3d-model",
    ]

    for model_id in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == "fal", f"Expected 'fal' for {model_id}, got {result}"


def test_detect_provider_from_model_id_existing_providers():
    """Test that existing provider detection still works.

    Note: OpenAI and Anthropic models now route to their native providers first,
    with failover to OpenRouter handled by provider_failover.py.
    """
    test_cases = [
        (
            "anthropic/claude-3-sonnet",
            "anthropic",
        ),  # Native Anthropic first, OpenRouter as fallback
        ("openai/gpt-4", "openai"),  # Native OpenAI first, OpenRouter as fallback
        ("meta-llama/llama-2-7b", None),  # This model doesn't match any specific provider
    ]

    for model_id, expected in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


@patch.dict("os.environ", {"GOOGLE_VERTEX_CREDENTIALS_JSON": '{"type":"service_account"}'})
def test_detect_provider_google_vertex_models():
    """Test that Google Vertex AI models are correctly detected when credentials are available

    Note: gemini-1.5-pro is excluded because gemini-1.5 models are retired on Vertex AI.
    See the detect_provider_from_model_id function which explicitly excludes gemini-1.5.
    """
    test_cases = [
        ("gemini-2.5-flash", "google-vertex"),
        ("gemini-2.0-flash", "google-vertex"),
        # gemini-1.5-pro excluded - retired on Vertex AI, routed to openrouter instead
        ("google/gemini-2.5-flash", "google-vertex"),
        ("google/gemini-2.0-flash", "google-vertex"),
        (
            "@google/models/gemini-2.5-flash",
            "google-vertex",
        ),  # Key test case - should NOT be portkey
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
    assert (
        transformed == "z-ai/glm-4.6:exacto"
    ), f"Expected 'z-ai/glm-4.6:exacto', got {transformed}"


def test_z_ai_glm_prefix_detected_as_openrouter():
    """Test that z-ai/ prefixed models are detected as OpenRouter provider"""
    test_cases = [
        ("z-ai/glm-4-flash", "openrouter"),
        ("z-ai/glm-4.5", "openrouter"),
        ("z-ai/glm-4.6", "openrouter"),
        ("z-ai/glm-4.7", "openrouter"),  # Non-existent but should still route to OpenRouter
        ("zai/glm-4-flash", "openrouter"),  # Alternate prefix without hyphen
    ]

    for model_id, expected in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_z_ai_glm_47_alias_transforms():
    """Test that z-ai/glm-4.7 (non-existent model) is aliased to existing model"""
    from src.services.model_transformations import apply_model_alias

    # z-ai/glm-4.7 doesn't exist, should map to z-ai/glm-4-flash
    test_cases = [
        ("z-ai/glm-4.7", "z-ai/glm-4-flash"),
        ("z-ai/glm-4-7", "z-ai/glm-4-flash"),
        ("z-ai/glm4.7", "z-ai/glm-4-flash"),
    ]

    for model_id, expected in test_cases:
        result = apply_model_alias(model_id)
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_openrouter_colon_suffix_variants():
    """Test that OpenRouter models with colon suffixes are correctly detected"""
    test_cases = [
        ("z-ai/glm-4.6:exacto", "openrouter"),
        (
            "google/gemini-2.0-flash-exp:free",
            "google-vertex",
        ),  # Gemini models route to Google Vertex
        ("anthropic/claude-3-opus:extended", "openrouter"),
    ]

    for model_id, expected_provider in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert (
            result == expected_provider
        ), f"Expected '{expected_provider}' for {model_id}, got {result}"


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
# Fireworks Fallback Behavior Tests
# ============================================================================


def test_fireworks_unknown_model_does_not_construct_invalid_id():
    """Test that unknown models are NOT naively transformed into invalid Fireworks IDs.

    Previously, an unknown model like 'deepseek/deepseek-v3.2-speciale' would be
    naively transformed to 'accounts/fireworks/models/deepseek-v3p2-speciale',
    which is not a valid Fireworks model ID.

    The fix ensures that unknown models are passed through as-is (lowercase),
    allowing Fireworks to return a proper "model not found" error.
    """
    from src.services.model_transformations import transform_model_id

    # This model variant does not exist - it should NOT be transformed to a fake Fireworks ID
    result = transform_model_id("deepseek/deepseek-v3.2-speciale", "fireworks")

    # Should NOT construct invalid ID like "accounts/fireworks/models/deepseek-v3p2-speciale"
    assert not result.startswith(
        "accounts/fireworks/models/"
    ), f"Unknown model should not be naively constructed to Fireworks format: {result}"

    # Should pass through as lowercase
    assert (
        result == "deepseek/deepseek-v3.2-speciale"
    ), f"Unknown model should pass through as-is (lowercase): {result}"


def test_fireworks_known_model_still_transforms():
    """Test that known Fireworks models are still properly transformed."""
    from src.services.model_transformations import transform_model_id

    # Known models should still be transformed correctly
    test_cases = [
        ("deepseek-ai/deepseek-v3", "accounts/fireworks/models/deepseek-v3p1"),
        ("deepseek/deepseek-v3.1", "accounts/fireworks/models/deepseek-v3p1"),
        ("meta-llama/llama-3.3-70b", "accounts/fireworks/models/llama-v3p3-70b-instruct"),
        ("deepseek-ai/deepseek-r1", "accounts/fireworks/models/deepseek-r1-0528"),
    ]

    for model_id, expected in test_cases:
        result = transform_model_id(model_id, "fireworks")
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_fireworks_unknown_model_without_slash_passthrough():
    """Test that unknown models without org prefix pass through as-is."""
    from src.services.model_transformations import transform_model_id

    result = transform_model_id("some-unknown-model", "fireworks")
    assert result == "some-unknown-model", f"Expected passthrough, got {result}"


def test_fireworks_nonexistent_variant_passthrough():
    """Test various nonexistent model variants pass through without naive construction."""
    from src.services.model_transformations import transform_model_id

    nonexistent_variants = [
        "deepseek/deepseek-v4",
        "deepseek/deepseek-r2-ultra",
        "meta-llama/llama-5-100b",
        "qwen/qwen-99b-super",
        "fictional-org/fictional-model-v1.2.3",
    ]

    for model_id in nonexistent_variants:
        result = transform_model_id(model_id, "fireworks")
        # Should NOT start with the Fireworks prefix for unknown models
        assert not result.startswith(
            "accounts/fireworks/models/"
        ), f"Unknown model '{model_id}' should not be naively constructed to Fireworks format: {result}"
        # Should pass through as-is
        assert result == model_id, f"Expected passthrough for '{model_id}', got {result}"


def test_fireworks_fuzzy_match_still_works():
    """Test that fuzzy matching still works for models with slight variations."""
    from src.services.model_transformations import transform_model_id

    # Test case-insensitive matching (should still work via normalize_model_name)
    result = transform_model_id("DeepSeek-AI/DeepSeek-V3", "fireworks")
    # Should match to known mapping via fuzzy matching
    assert (
        result == "accounts/fireworks/models/deepseek-v3p1"
    ), f"Fuzzy matching should still work for known models: {result}"
