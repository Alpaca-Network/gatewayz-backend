import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from src.services.model_catalog_cache import cache_gateway_catalog
from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_featherless_pooled_client
from src.utils.model_name_validator import clean_model_name
from src.utils.security_validators import sanitize_for_logging

# Initialize logging
logger = logging.getLogger(__name__)

# Modality constants
MODALITY_TEXT_TO_TEXT = "text->text"


def get_featherless_client():
    """Get Featherless.ai client with connection pooling for better performance

    Featherless.ai provides OpenAI-compatible API endpoints for various models
    """
    try:
        if not Config.FEATHERLESS_API_KEY:
            raise ValueError("Featherless API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_featherless_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Featherless client: {e}")
        raise


def _sanitize_messages_for_featherless(messages: list[dict]) -> list[dict]:
    """
    Sanitize messages for Featherless API compatibility.

    Featherless expects:
    - tool_calls to be an array or omitted entirely (not null)
    - Validation errors occur when tool_calls is null

    Args:
        messages: List of message dictionaries

    Returns:
        Sanitized list of messages
    """
    sanitized = []
    for msg in messages:
        clean_msg = msg.copy()

        # Remove null tool_calls (Featherless rejects null, expects array or omitted)
        if 'tool_calls' in clean_msg and clean_msg['tool_calls'] is None:
            logger.debug(f"Removing null tool_calls from message")
            del clean_msg['tool_calls']

        # Ensure tool_calls is array if present
        if 'tool_calls' in clean_msg and not isinstance(clean_msg['tool_calls'], list):
            logger.warning(
                f"Invalid tool_calls type: {type(clean_msg['tool_calls'])}, removing field"
            )
            del clean_msg['tool_calls']

        sanitized.append(clean_msg)

    return sanitized


def make_featherless_request_openai(messages, model, **kwargs):
    """Make request to Featherless.ai using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        # Sanitize messages before sending to Featherless
        sanitized_messages = _sanitize_messages_for_featherless(messages)

        client = get_featherless_client()
        response = client.chat.completions.create(
            model=model,
            messages=sanitized_messages,
            **kwargs
        )
        return response
    except Exception as e:
        logger.error(f"Featherless request failed: {e}")
        raise


def make_featherless_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Featherless.ai using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        # Sanitize messages before sending to Featherless
        sanitized_messages = _sanitize_messages_for_featherless(messages)

        client = get_featherless_client()
        stream = client.chat.completions.create(
            model=model,
            messages=sanitized_messages,
            stream=True,
            **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Featherless streaming request failed: {e}")
        raise


def process_featherless_response(response):
    """Process Featherless response to extract relevant data"""
    try:
        choices = []
        for choice in response.choices:
            msg = extract_message_with_tools(choice.message)

            choices.append(
                {
                    "index": choice.index,
                    "message": msg,
                    "finish_reason": choice.finish_reason,
                }
            )

        return {
            "id": response.id,
            "object": response.object,
            "created": response.created,
            "model": response.model,
            "choices": choices,
            "usage": (
                {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
                if response.usage
                else {}
            ),
        }
    except Exception as e:
        logger.error(f"Failed to process Featherless response: {e}")
        raise


# ============================================================================
# Model Catalog Functions
# ============================================================================


def load_featherless_catalog_export() -> list:
    """
    Load Featherless models from a static export CSV if available.
    Returns a list of normalized model records or None.
    """
    try:
        repo_root = Path(__file__).resolve().parents[2]
        export_candidates = [
            repo_root / "models_export_2025-10-16_202520.csv",
            repo_root / "models_export_2025-10-16_202501.csv",
        ]

        for csv_path in export_candidates:
            if not csv_path.exists():
                continue

            logger.info(f"Loading Featherless catalog export from {csv_path}")
            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = [
                    row for row in reader if (row.get("gateway") or "").lower() == "featherless"
                ]

            if not rows:
                logger.warning(f"No Featherless rows found in export {csv_path}")
                continue

            normalized = []
            for row in rows:
                model_id = row.get("id")
                if not model_id:
                    continue

                try:
                    context_length = int(float(row.get("context_length", 0) or 0))
                except (TypeError, ValueError):
                    context_length = 0

                def parse_price(value: str) -> str:
                    try:
                        if value is None or value == "":
                            return "0"
                        return str(float(value))
                    except (TypeError, ValueError):
                        return "0"

                prompt_price = parse_price(row.get("prompt_price"))
                completion_price = parse_price(row.get("completion_price"))

                normalized.append(
                    {
                        "id": model_id,
                        "slug": model_id,
                        "canonical_slug": model_id,
                        "hugging_face_id": None,
                        "name": row.get("name") or model_id,
                        "created": None,
                        "description": row.get("description")
                        or f"Featherless catalog entry for {model_id}.",
                        "context_length": context_length,
                        "architecture": {
                            "modality": row.get("modality") or MODALITY_TEXT_TO_TEXT,
                            "input_modalities": ["text"],
                            "output_modalities": ["text"],
                            "tokenizer": None,
                            "instruct_type": None,
                        },
                        "pricing": {
                            "prompt": prompt_price,
                            "completion": completion_price,
                            "request": "0",
                            "image": "0",
                            "web_search": "0",
                            "internal_reasoning": "0",
                        },
                        "top_provider": None,
                        "per_request_limits": None,
                        "supported_parameters": [],
                        "default_parameters": {},
                        "provider_slug": row.get("provider_slug")
                        or (model_id.split("/")[0] if "/" in model_id else "featherless"),
                        "provider_site_url": None,
                        "model_logo_url": None,
                        "source_gateway": "featherless",
                        "raw_featherless": row,
                    }
                )

            logger.info(f"Loaded {len(normalized)} Featherless models from export {csv_path}")
            return normalized
        return None
    except Exception as exc:
        logger.error(
            "Failed to load Featherless catalog export: %s",
            sanitize_for_logging(str(exc)),
            exc_info=True,
        )
        return None


def normalize_featherless_model(featherless_model: dict) -> dict:
    """Normalize Featherless catalog entries to resemble OpenRouter model shape"""
    from src.services.pricing_lookup import enrich_model_with_pricing

    model_id = featherless_model.get("id", "")
    if not model_id:
        return {"source_gateway": "featherless", "raw_featherless": featherless_model or {}}

    # Extract provider slug (everything before the last slash)
    provider_slug = model_id.split("/")[0] if "/" in model_id else "featherless"

    # Model handle is the full ID
    raw_display_name = model_id.replace("-", " ").replace("_", " ").title()
    # Clean malformed model names (remove company prefix, parentheses, etc.)
    display_name = clean_model_name(raw_display_name)

    description = (
        featherless_model.get("description")
        or f"Featherless catalog entry for {model_id}. Pricing data not available from Featherless API."
    )

    # Use null for unknown pricing (Featherless API doesn't provide pricing)
    pricing = {
        "prompt": featherless_model.get("prompt_price"),
        "completion": featherless_model.get("completion_price"),
        "request": featherless_model.get("request_price"),
        "image": featherless_model.get("image_price"),
        "web_search": featherless_model.get("web_search_price"),
        "internal_reasoning": featherless_model.get("internal_reasoning_price"),
    }

    architecture = {
        "modality": MODALITY_TEXT_TO_TEXT,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "tokenizer": None,
        "instruct_type": None,
    }

    normalized = {
        "id": model_id,
        "slug": model_id,
        "canonical_slug": model_id,
        "hugging_face_id": None,
        "name": display_name,
        "created": featherless_model.get("created"),
        "description": description,
        "context_length": 0,
        "architecture": architecture,
        "pricing": pricing,
        "per_request_limits": None,
        "supported_parameters": [],
        "default_parameters": {},
        "provider_slug": provider_slug,
        "provider_site_url": None,
        "model_logo_url": None,
        "source_gateway": "featherless",
        "raw_featherless": featherless_model,
    }

    # Enrich with manual pricing if available
    return enrich_model_with_pricing(normalized, "featherless")


def fetch_models_from_featherless():
    """Fetch models from Featherless API and normalize to the catalog schema

    Note: Featherless API ignores the 'limit' and 'offset' parameters and returns
    ALL models (~6,452) in a single request. We only need one API call.
    """
    try:
        if not Config.FEATHERLESS_API_KEY:
            logger.error("Featherless API key not configured")
            return None

        headers = {"Authorization": f"Bearer {Config.FEATHERLESS_API_KEY}"}

        # Featherless API returns all models in a single request (ignores pagination params)
        url = "https://api.featherless.ai/v1/models"

        logger.info("Fetching all models from Featherless API (single request)")

        response = httpx.get(url, headers=headers, params={"limit": 10000}, timeout=30.0)
        response.raise_for_status()

        payload = response.json()
        all_models = payload.get("data", [])

        if not all_models:
            logger.warning("No models returned from Featherless API")
            return None

        logger.info(f"Fetched {len(all_models)} total models from Featherless")

        # Filter out None values since enrich_model_with_pricing may return None for gateway providers
        normalized_models = [
            norm_model
            for model in all_models
            if model
            for norm_model in [normalize_featherless_model(model)]
            if norm_model is not None
        ]

        if len(normalized_models) < 6000:
            logger.warning(
                f"Featherless API returned {len(normalized_models)} models; loading extended catalog export for completeness"
            )
            export_models = load_featherless_catalog_export()
            if export_models:
                # Filter models that have a valid id (normalize functions may return models without id)
                combined = {model["id"]: model for model in normalized_models if model.get("id")}
                for export_model in export_models:
                    # Run export models through pricing enrichment to filter those without valid pricing.
                    # Note: During catalog build (_is_building_catalog=True), models are kept even without
                    # pricing to bootstrap the catalog. During regular operation, only models with valid
                    # pricing (from manual_pricing.json or cross-reference) are kept. This intentionally
                    # filters out export models without pricing to prevent them appearing as "free".
                    from src.services.pricing_lookup import enrich_model_with_pricing

                    enriched = enrich_model_with_pricing(export_model, "featherless")
                    if enriched and enriched.get("id"):
                        combined[enriched["id"]] = enriched
                normalized_models = list(combined.values())
                logger.info(
                    f"Combined Featherless catalog now includes {len(normalized_models)} models from API + export"
                )

        cache_gateway_catalog("featherless", normalized_models)
        logger.info(f"Normalized and cached {len(normalized_models)} Featherless models")
        return normalized_models
    except httpx.HTTPStatusError as e:
        error_msg = f"HTTP {e.response.status_code} - {sanitize_for_logging(e.response.text)}"
        logger.error("Featherless HTTP error: %s", error_msg)
        # Error tracking now automatic via Redis cache circuit breaker
        return None
    except Exception as e:
        error_msg = sanitize_for_logging(str(e))
        logger.error("Failed to fetch models from Featherless: %s", error_msg)
        # Error tracking now automatic via Redis cache circuit breaker
        return None
