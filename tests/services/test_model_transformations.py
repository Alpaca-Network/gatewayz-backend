from unittest.mock import patch
from src.services.model_transformations import transform_model_id, detect_provider_from_model_id


def test_openrouter_prefixed_model_keeps_nested_provider():
    result = transform_model_id("openrouter/openai/gpt-4", "openrouter")
    assert result == "openai/gpt-4"


def test_openrouter_auto_preserves_prefix():
    result = transform_model_id("openrouter/auto", "openrouter")
    assert result == "openrouter/auto"


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


def test_detect_provider_portkey_models():
    """Test that Portkey models with @ prefix are correctly detected"""
    test_cases = [
        ("@anthropic/claude-3-sonnet", "portkey"),
        ("@openai/gpt-4", "portkey"),
    ]

    for model_id, expected in test_cases:
        result = detect_provider_from_model_id(model_id)
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"


def test_z_ai_glm_with_exacto_suffix():
    """Test that z-ai/glm-4.6:exacto is correctly detected and transformed"""
    # Test provider detection
    result = detect_provider_from_model_id("z-ai/glm-4.6:exacto")
    assert result == "near", f"Expected 'near' for z-ai/glm-4.6:exacto, got {result}"

    # Test model transformation
    transformed = transform_model_id("z-ai/glm-4.6:exacto", "near")
    assert transformed == "zai-org/GLM-4.6", f"Expected 'zai-org/GLM-4.6', got {transformed}"


def test_colon_suffix_stripping():
    """Test that colon-based suffixes are stripped from model IDs"""
    test_cases = [
        ("z-ai/glm-4.6:exacto", "near", "zai-org/GLM-4.6"),
        ("z-ai/glm-4.6:free", "near", "zai-org/GLM-4.6"),
        ("z-ai/glm-4.6-fp8:exacto", "near", "zai-org/GLM-4.6"),
    ]

    for model_id, provider, expected in test_cases:
        result = transform_model_id(model_id, provider)
        assert result == expected, f"Expected '{expected}' for {model_id}, got {result}"
