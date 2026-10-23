"""Groq's /models returns OpenRouter-shaped per-token prices; use them as-is."""

from src.services.providers.groq_catalog import normalize_groq_model


def test_uses_groq_reported_per_token_prices():
    model = normalize_groq_model(
        {
            "id": "qwen/qwen3.8-27b",
            "owned_by": "Alibaba Cloud",
            "pricing": {
                "prompt": "0.0000008",
                "completion": "0.000004",
                "image": "0",
                "request": "0",
            },
        }
    )
    assert float(model["pricing"]["prompt"]) == 0.0000008
    assert float(model["pricing"]["completion"]) == 0.000004


def test_legacy_cents_shape_still_supported():
    model = normalize_groq_model(
        {
            "id": "x/y",
            "pricing": {"cents_per_input_token": 0.00005, "cents_per_output_token": 0.0001},
        }
    )
    assert float(model["pricing"]["prompt"]) == 0.0000005
    assert float(model["pricing"]["completion"]) == 0.000001
