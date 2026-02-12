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
    """Fetch models from Featherless API with step-by-step logging

    Note: Featherless API ignores the 'limit' and 'offset' parameters and returns
    ALL models (~6,452) in a single request. We only need one API call.
    """
    from src.utils.step_logger import StepLogger
    from src.utils.provider_error_logging import (
        ProviderErrorType,
        ProviderFetchContext,
        log_provider_fetch_error,
        log_provider_fetch_success,
    )
    import time

    start_time = time.time()
    step_logger = StepLogger("Featherless Model Fetch", total_steps=4)
    url = "https://api.featherless.ai/v1/models"

    step_logger.start(provider="featherless", endpoint=url)

    try:
        # Step 1: Validate API configuration
        step_logger.step(1, "Validating API configuration", provider="featherless")

        if not Config.FEATHERLESS_API_KEY:
            error_msg = "Featherless API key not configured"
            step_logger.failure(ValueError(error_msg))
            logger.error(f"[FEATHERLESS] {error_msg}")
            return None

        step_logger.success(status="configured")

        # Step 2: Fetch all models from API (single request)
        step_logger.step(2, "Fetching all models from API (single request)", endpoint=url, timeout="30s")

        headers = {"Authorization": f"Bearer {Config.FEATHERLESS_API_KEY}"}
        response = httpx.get(url, headers=headers, params={"limit": 10000}, timeout=30.0)
        response.raise_for_status()

        payload = response.json()
        all_models = payload.get("data", [])

        if not all_models:
            logger.warning("[FEATHERLESS] No models returned from API")
            step_logger.failure(ValueError("No models returned from API"))
            return None

        step_logger.success(raw_count=len(all_models), status_code=response.status_code)

        # Step 3: Normalize, filter, and combine with export catalog if needed
        step_logger.step(3, "Normalizing and filtering models", raw_count=len(all_models))

        normalized_models = [
            norm_model
            for model in all_models
            if model
            for norm_model in [normalize_featherless_model(model)]
            if norm_model is not None
        ]

        filtered_count = len(all_models) - len(normalized_models)

        # Load export catalog if API returned fewer than expected models
        if len(normalized_models) < 6000:
            logger.info(
                f"[FEATHERLESS] API returned {len(normalized_models)} models; loading extended catalog export for completeness"
            )
            export_models = load_featherless_catalog_export()
            if export_models:
                # Filter models that have a valid id
                combined = {model["id"]: model for model in normalized_models if model.get("id")}
                export_added = 0

                for export_model in export_models:
                    # Run export models through pricing enrichment to filter those without valid pricing
                    from src.services.pricing_lookup import enrich_model_with_pricing

                    enriched = enrich_model_with_pricing(export_model, "featherless")
                    if enriched and enriched.get("id") and enriched["id"] not in combined:
                        combined[enriched["id"]] = enriched
                        export_added += 1

                normalized_models = list(combined.values())
                step_logger.success(
                    normalized_count=len(normalized_models),
                    filtered_count=filtered_count,
                    export_added=export_added,
                    total_sources="API+export",
                )
            else:
                step_logger.success(
                    normalized_count=len(normalized_models), filtered_count=filtered_count, export_added=0
                )
        else:
            step_logger.success(normalized_count=len(normalized_models), filtered_count=filtered_count, source="API")

        # Step 4: Cache the models
        step_logger.step(4, "Caching models", cache_type="redis+local", model_count=len(normalized_models))

        cache_gateway_catalog("featherless", normalized_models)
        step_logger.success(cached_count=len(normalized_models))

        # Complete with summary
        duration = time.time() - start_time
        step_logger.complete(total_models=len(normalized_models), duration_seconds=f"{duration:.2f}")

        # Log success with provider_error_logging utility
        log_provider_fetch_success(
            provider_slug="featherless",
            models_count=len(normalized_models),
            duration=duration,
            additional_context={"endpoint": url, "raw_count": len(all_models)},
        )

        return normalized_models

    except httpx.TimeoutException as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="featherless",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.API_TIMEOUT,
        )
        log_provider_fetch_error("featherless", e, context)
        return None

    except httpx.HTTPStatusError as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="featherless",
            endpoint_url=url,
            status_code=e.response.status_code,
            duration=duration,
        )
        log_provider_fetch_error("featherless", e, context)
        return None

    except httpx.NetworkError as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="featherless",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.NETWORK_ERROR,
        )
        log_provider_fetch_error("featherless", e, context)
        return None

    except (ValueError, TypeError, KeyError) as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="featherless",
            endpoint_url=url,
            duration=duration,
            error_type=ProviderErrorType.PARSING_ERROR,
        )
        log_provider_fetch_error("featherless", e, context)
        return None

    except Exception as e:
        duration = time.time() - start_time
        step_logger.failure(e)

        context = ProviderFetchContext(
            provider_slug="featherless", endpoint_url=url, duration=duration, error_type=ProviderErrorType.UNKNOWN
        )
        log_provider_fetch_error("featherless", e, context)

        # Attempt database fallback
        from src.services.models import apply_database_fallback

        fallback_models = apply_database_fallback("featherless", normalize_featherless_model, e)
        if fallback_models:
            cache_gateway_catalog("featherless", fallback_models)
            return fallback_models

        return None
