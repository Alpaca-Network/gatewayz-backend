"""
API Models Routes

This module provides the /api/models/detail endpoint for frontend compatibility.
The frontend calls /api/models/detail with query parameters instead of the RESTful
path-based endpoint /v1/models/{provider}/{model}.
"""

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query

from src.routes.catalog import (
    annotate_provider_sources,
    derive_providers_from_models,
    merge_provider_lists,
    normalize_developer_segment,
    normalize_model_segment,
)
from src.services.models import (
    enhance_model_with_huggingface_data,
    enhance_model_with_provider_info,
    fetch_specific_model,
    get_cached_models,
)
from src.services.providers import enhance_providers_with_logos_and_sites, get_cached_providers
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api-models"])


@router.get("/models/detail")
async def get_model_detail(
    modelId: str | None = Query(None, description="Full model ID (e.g., 'z-ai/glm-4-7')"),
    developer: str | None = Query(None, description="Developer/provider name (e.g., 'z-ai')"),
    modelName: str | None = Query(None, description="Model name (e.g., 'glm-4-7')"),
    include_huggingface: bool = Query(
        True, description="Include Hugging Face metrics if available"
    ),
    gateway: str | None = Query(None, description="Gateway to use for fetching model data"),
):
    """
    Get specific model details using query parameters.

    This endpoint provides compatibility with frontend clients that use query parameters
    instead of the RESTful path-based endpoint /v1/models/{provider}/{model}.

    The endpoint accepts either:
    - modelId: Full model ID in format "developer/model-name"
    - developer + modelName: Separate parameters for developer and model name

    Examples:
        GET /api/models/detail?modelId=z-ai/glm-4-7
        GET /api/models/detail?developer=z-ai&modelName=glm-4-7
        GET /api/models/detail?modelId=openai/gpt-4&gateway=openrouter
    """
    try:
        # Determine provider_name and model_name from query parameters
        provider_name = None
        model_name = None

        if modelId:
            # Parse modelId to extract provider and model name
            if "/" in modelId:
                parts = modelId.split("/", 1)
                provider_name = parts[0]
                model_name = parts[1] if len(parts) > 1 else None
            else:
                # If no slash, treat the whole thing as model name
                model_name = modelId

        # Use developer/modelName if provided (overrides parsed values)
        if developer:
            provider_name = developer
        if modelName:
            model_name = modelName

        # Validate we have required parameters
        if not provider_name or not model_name:
            raise HTTPException(
                status_code=400,
                detail="Missing required parameters. Provide either 'modelId' (e.g., 'z-ai/glm-4-7') "
                "or both 'developer' and 'modelName' parameters.",
            )

        # Normalize the parameters
        provider_name = normalize_developer_segment(provider_name) or provider_name
        model_name = normalize_model_segment(model_name) or model_name

        logger.info(
            "Fetching model detail: provider=%s, model=%s, gateway=%s",
            sanitize_for_logging(provider_name),
            sanitize_for_logging(model_name),
            sanitize_for_logging(gateway) if gateway else "auto",
        )

        # Fetch model data from appropriate gateway
        model_data = await asyncio.to_thread(
            fetch_specific_model, provider_name, model_name, gateway
        )

        if not model_data:
            gateway_msg = f" from gateway '{gateway}'" if gateway else ""
            raise HTTPException(
                status_code=404, detail=f"Model {provider_name}/{model_name} not found{gateway_msg}"
            )

        # Determine which gateway was used
        detected_gateway = model_data.get("source_gateway", gateway or "all")

        # Get enhanced providers data for all gateways
        provider_groups: list[list[dict]] = []

        # Always try to get OpenRouter providers for cross-reference
        openrouter_providers = get_cached_providers()
        if openrouter_providers:
            enhanced_openrouter = annotate_provider_sources(
                enhance_providers_with_logos_and_sites(openrouter_providers),
                "openrouter",
            )
            provider_groups.append(enhanced_openrouter)

        # Add providers from other gateways based on detected gateway
        if detected_gateway in [
            "featherless",
            "deepinfra",
            "chutes",
            "groq",
            "fireworks",
            "together",
            "cerebras",
            "nebius",
            "xai",
            "novita",
            "hug",
            "aimo",
            "near",
            "fal",
            "anannas",
            "aihubmix",
            "vercel-ai-gateway",
            "onerouter",
            "helicone",
        ]:
            # Get models from the detected gateway to derive providers
            # Get models from the detected gateway to derive providers
            gateway_models = await asyncio.to_thread(get_cached_models, detected_gateway)
            if gateway_models:
                derived_providers = derive_providers_from_models(gateway_models, detected_gateway)
                annotated_providers = annotate_provider_sources(derived_providers, detected_gateway)
                provider_groups.append(annotated_providers)

        # Merge all provider data
        enhanced_providers = merge_provider_lists(*provider_groups) if provider_groups else []

        # Enhance with provider information and logos
        if isinstance(model_data, dict):
            model_data = enhance_model_with_provider_info(model_data, enhanced_providers)

            # Then enhance with Hugging Face data if requested
            if include_huggingface and model_data.get("hugging_face_id"):
                model_data = enhance_model_with_huggingface_data(model_data)

        # Extract provider list from model data for frontend compatibility
        providers = []
        if model_data.get("source_gateways"):
            providers = model_data.get("source_gateways", [])
        elif detected_gateway:
            providers = [detected_gateway]

        return {
            "data": model_data,
            "providers": providers,
            "provider": provider_name,
            "model": model_name,
            "gateway": detected_gateway,
            "include_huggingface": include_huggingface,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get model detail for %s/%s: %s",
            sanitize_for_logging(provider_name) if provider_name else "unknown",
            sanitize_for_logging(model_name) if model_name else "unknown",
            sanitize_for_logging(str(e)),
        )
        raise HTTPException(status_code=500, detail="Failed to get model data")
