"""OpenCode Zen model-catalog functions (fetch + normalize).

OpenCode Zen (https://opencode.ai/docs/zen) is OpenCode's pay-as-you-go model
gateway: a curated set of GPT, Claude, Gemini, Grok and open-weight models sold
at the upstream list price. It is an aggregator, listed as supply by the
2026-11-25 North Star §5 amendment.

Catalog ids are ``opencode/<zen-id>`` (e.g. ``opencode/claude-opus-5-5``); the
adapter strips the prefix before the upstream call.

Excluded at ingestion:

* Free chat models (``*-free`` ids and ``big-pickle``). Zen's docs state they
  "require OpenCode" — other clients are rejected — and several retain or
  train on prompts. Serving them through Gatewayz would fail or leak data.

Pricing: Zen's ``/models`` schema is not documented, so prices are not read
from it. They come from Zen's published table in ``src/data/manual_pricing.json``
(the ``opencode`` block), and failing that from the OpenRouter price book via
cross-reference — unpriced models are gated out automatically (see
``src/services/pricing/pricing_lookup.py``), never listed as free.
"""

import logging

import httpx

from src.config import Config
from src.services.model_catalog_cache import cache_gateway_catalog
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

MODALITY_TEXT_TO_TEXT = "text->text"

OPENCODE_MODELS_URL = "https://opencode.ai/inference/v1/models"

# Free Zen models that are allowed only from the OpenCode client.
_OPENCODE_ONLY_FREE_IDS = frozenset({"big-pickle"})


def is_opencode_client_only(model_id: str) -> bool:
    """True for Zen models that only the OpenCode client may call."""
    mid = (model_id or "").lower()
    return mid.endswith("-free") or mid in _OPENCODE_ONLY_FREE_IDS


def normalize_opencode_model(zen_model: dict) -> dict | None:
    """Normalize OpenCode Zen catalog entries to resemble the OpenRouter model shape."""
    from src.services.pricing_lookup import enrich_model_with_pricing

    provider_model_id = zen_model.get("id")
    if not provider_model_id:
        return {"source_gateway": "opencode", "raw_opencode": zen_model or {}}

    if is_opencode_client_only(provider_model_id):
        return None

    slug = f"opencode/{provider_model_id}"
    provider_slug = "opencode"

    display_name = clean_model_name(provider_model_id.replace("-", " ").replace("_", " ").title())
    owned_by = zen_model.get("owned_by", "opencode")
    description = f"{display_name} via OpenCode Zen (upstream: {owned_by})."

    context_length = zen_model.get("context_length") or 0

    pricing = {
        "prompt": None,
        "completion": None,
        "request": None,
        "image": None,
        "web_search": None,
        "internal_reasoning": None,
    }

    architecture = {
        "modality": MODALITY_TEXT_TO_TEXT,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "tokenizer": None,
        "instruct_type": None,
    }

    normalized = {
        "id": slug,
        "slug": slug,
        "canonical_slug": slug,
        "hugging_face_id": None,
        "name": display_name,
        "created": zen_model.get("created"),
        "description": description,
        "context_length": context_length,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": "https://opencode.ai/zen",
        "model_logo_url": None,
        "source_gateway": "opencode",
        "raw_opencode": zen_model,
    }

    return enrich_model_with_pricing(normalized, "opencode")


def fetch_models_from_opencode():
    """Fetch models from OpenCode Zen's /models endpoint and normalize them."""
    from src.services.gateway_health_service import clear_gateway_error, set_gateway_error

    try:
        if not Config.OPENCODE_API_KEY:
            logger.error("OpenCode Zen API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.OPENCODE_API_KEY}",
            "Content-Type": "application/json",
        }

        logger.info("Fetching models from OpenCode Zen")

        response = httpx.get(OPENCODE_MODELS_URL, headers=headers, timeout=20.0)
        response.raise_for_status()

        payload = response.json()
        raw_models = payload.get("data", []) if isinstance(payload, dict) else payload or []

        logger.info(f"Fetched {len(raw_models)} models from OpenCode Zen")

        normalized_models = [
            norm_model
            for model in raw_models
            if isinstance(model, dict)
            for norm_model in [normalize_opencode_model(model)]
            if norm_model is not None
        ]

        cache_gateway_catalog("opencode", normalized_models)
        clear_gateway_error("opencode")

        logger.info(f"Successfully cached {len(normalized_models)} OpenCode Zen models")
        return normalized_models
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("OpenCode Zen HTTP error: %s", error_msg)
        set_gateway_error("opencode", error_msg)
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from OpenCode Zen: %s", error_msg)
        set_gateway_error("opencode", error_msg)
        return None
