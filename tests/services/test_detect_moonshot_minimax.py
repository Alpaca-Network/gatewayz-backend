"""Provider detection must resolve moonshot/minimax prefixed model ids to their
direct provider, not fall through to openrouter (which broke Kimi routing)."""

from src.services.model_transformations import detect_provider_from_model_id


def test_moonshot_models_detect_moonshot():
    assert detect_provider_from_model_id("moonshot/kimi-k2.6") == "moonshot"
    assert detect_provider_from_model_id("moonshot/kimi-k3") == "moonshot"


def test_minimax_models_detect_minimax():
    assert detect_provider_from_model_id("minimax/MiniMax-Text-01") == "minimax"


def test_existing_providers_unaffected():
    assert detect_provider_from_model_id("openai/gpt-4o") == "openai"
    assert detect_provider_from_model_id("anthropic/claude-sonnet-5") == "anthropic"
