from unittest.mock import patch

from typing import Optional

import pytest

from src.services.model_transformations import transform_model_id, detect_provider_from_model_id
from src.services.multi_provider_registry import (
    get_registry,
    CanonicalModelProvider,
)


def _register_canonical(
    canonical_id: str,
    provider_slug: str,
    native_id: str,
    aliases: Optional[list] = None,
    features: Optional[list] = None,
    priority: int = 1,
) -> None:
    registry = get_registry()
    display = {"canonical_slug": canonical_id, "slug": canonical_id}
    if aliases:
        display["aliases"] = aliases

    provider = CanonicalModelProvider(
        provider_slug=provider_slug,
        native_model_id=native_id,
        capabilities={"features": features or []},
        metadata={"canonical_slug": canonical_id, "slug": canonical_id, "priority": priority},
    )

    registry.register_canonical_provider(canonical_id, display, provider)


@pytest.fixture(autouse=True)
def seed_canonical_registry():
    registry = get_registry()
    registry.reset_canonical_models()

    _register_canonical(
        "openai/gpt-4",
        "openrouter",
        "openai/gpt-4",
        aliases=["openrouter/openai/gpt-4"],
    )
    _register_canonical("openrouter/auto", "openrouter", "openrouter/auto")

    _register_canonical(
        "anthropic/claude-3-sonnet",
        "openrouter",
        "anthropic/claude-3-sonnet-20240229",
        aliases=["anthropic/claude-3-sonnet"],
    )
    _register_canonical(
        "anthropic/claude-3-sonnet",
        "portkey",
        "@anthropic/claude-3-sonnet",
        aliases=["@anthropic/claude-3-sonnet"],
        priority=2,
    )

    fal_aliases = [
        "fal-ai/stable-diffusion-v15",
        "fal/some-model",
        "minimax/video-01",
        "stabilityai/stable-diffusion-xl",
        "hunyuan3d/some-model",
        "meshy/mesh-model",
        "tripo3d/3d-model",
    ]
    _register_canonical(
        "fal-ai/stable-diffusion-v15",
        "fal",
        "fal-ai/stable-diffusion-v15",
        aliases=fal_aliases,
    )

    for gemini_id in [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
    ]:
        aliases = [f"google/{gemini_id}", f"@google/models/{gemini_id}"]
        _register_canonical(
            gemini_id,
            "google-vertex",
            gemini_id,
            aliases=aliases,
        )

    yield
    registry.reset_canonical_models()


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
