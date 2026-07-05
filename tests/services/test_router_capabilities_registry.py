"""Pure builder for the auto-router capabilities registry.

Guards the two bugs that made auto-routing pick wrong models:
  * non-chat models (audio/image output, e.g. whisper) must be excluded, and
  * costs must be REAL (from model_pricing) so cost-based scoring can distinguish
    candidates instead of every model tying at a placeholder rate.
"""

from __future__ import annotations

import pytest

from src.services.prompt_router import build_capabilities_registry


def _row(**kw):
    base = {
        "provider_model_id": "openai/gpt-4o-mini",
        "canonical_id": "openai/gpt-4o-mini",
        "modality": "text->text",
        "context_length": 128000,
        "supports_function_calling": True,
        "supports_vision": False,
        "has_json_mode": True,
        "model_pricing": {"price_per_input_token": 1.5e-07, "price_per_output_token": 6e-07},
    }
    base.update(kw)
    return base


def test_chat_model_included_with_real_cost():
    reg = build_capabilities_registry([_row()])
    assert "openai/gpt-4o-mini" in reg
    cap = reg["openai/gpt-4o-mini"]
    # 1.5e-07 per-token → per-1k = 1.5e-04
    assert cap.cost_per_1k_input == pytest.approx(0.00015)
    assert cap.tools is True
    assert cap.max_context == 128000


def test_audio_output_model_excluded():
    # whisper: modality "audio" → not a chat model
    reg = build_capabilities_registry(
        [_row(provider_model_id="openai/whisper-large-v3", modality="audio")]
    )
    assert reg == {}


def test_image_output_model_excluded():
    reg = build_capabilities_registry(
        [_row(provider_model_id="black-forest/flux", modality="text->image")]
    )
    assert reg == {}


def test_transcription_and_tts_excluded():
    # audio->text (speech-to-text, e.g. whisper) and text->audio (TTS) are not chat
    stt = build_capabilities_registry(
        [_row(provider_model_id="openai/whisper-large-v3", modality="audio->text")]
    )
    tts = build_capabilities_registry(
        [_row(provider_model_id="openai/tts-1", modality="text->audio")]
    )
    assert stt == {}
    assert tts == {}


def test_multimodal_text_output_included():
    # vision-in, text-out is a valid chat model and should set vision=True
    reg = build_capabilities_registry([_row(modality="text+image->text", supports_vision=True)])
    cap = reg["openai/gpt-4o-mini"]
    assert cap.vision is True


def test_dedup_keeps_cheapest_across_providers():
    rows = [
        _row(
            provider_model_id="meta/llama",
            canonical_id="meta/llama",
            model_pricing={"price_per_input_token": 9e-07},
        ),
        _row(
            provider_model_id="meta/llama",
            canonical_id="meta/llama",
            model_pricing={"price_per_input_token": 2e-07},
        ),
    ]
    reg = build_capabilities_registry(rows)
    assert reg["meta/llama"].cost_per_1k_input == pytest.approx(0.0002)


def test_unpriced_model_still_included_with_zero_cost():
    # No pricing row: keep the model (still routable) but at zero cost.
    reg = build_capabilities_registry([_row(model_pricing=None)])
    assert "openai/gpt-4o-mini" in reg
    assert reg["openai/gpt-4o-mini"].cost_per_1k_input == 0.0


def test_inactive_or_idless_rows_skipped():
    assert build_capabilities_registry([_row(provider_model_id=None, canonical_id=None)]) == {}


def test_mislabeled_whisper_excluded_by_name():
    # Dirty catalog: whisper duplicated as "text->text" — name guard still drops it.
    reg = build_capabilities_registry(
        [
            _row(
                provider_model_id="openai/whisper-large-v3",
                canonical_id="openai/whisper-large-v3",
                modality="text->text",
            ),
        ]
    )
    assert reg == {}


def test_embedding_and_image_families_excluded_by_name():
    for mid in ["openai/text-embedding-3-small", "black-forest-labs/flux-1", "stability/sdxl"]:
        reg = build_capabilities_registry([_row(provider_model_id=mid, canonical_id=mid)])
        assert reg == {}, mid
