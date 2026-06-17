"""Google Vertex AI model-catalog fetching/normalization (extracted from
google_vertex_client.py, Phase 0c-2 thinning). Off the hot request path — used by
the model-sync pipeline. Behavior unchanged; the auth helpers it needs stay in
google_vertex_client (imported below; no cycle — the client does not import this)."""

from __future__ import annotations

import logging

import httpx

from src.config import Config
from src.services.providers.google_vertex_client import (
    _get_google_vertex_access_token,
    _prepare_vertex_environment,
)
from src.utils.model_name_validator import clean_model_name

logger = logging.getLogger(__name__)


def _fetch_models_from_vertex_api() -> list[dict] | None:
    """Fetch available models dynamically from Google Vertex AI API.

    Uses the publishers/google/models endpoint to list all available Gemini models.
    Returns None if the API call fails, allowing fallback to static config.

    Note: This function attempts both regional and global endpoints due to inconsistent
    availability across different regions and API versions.
    """
    try:
        _prepare_vertex_environment()
        access_token = _get_google_vertex_access_token()

        # Use the Vertex AI discovery endpoint to list publisher models
        # This endpoint lists all Google-published models available in Vertex AI
        location = Config.GOOGLE_VERTEX_LOCATION
        project_id = Config.GOOGLE_PROJECT_ID

        # Try multiple endpoint patterns as the API availability varies by region
        # Pattern 1: Regional endpoint with project context (most accurate for access control)
        # Pattern 2: Regional endpoint without project (simpler, but may not respect all access)
        # Pattern 3: Global endpoint (fallback, but doesn't always have all models)
        endpoint_patterns = [
            f"https://{location}-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{location}/publishers/google/models",
            f"https://{location}-aiplatform.googleapis.com/v1/publishers/google/models",
            "https://aiplatform.googleapis.com/v1/publishers/google/models",
        ]

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        last_error = None
        for url in endpoint_patterns:
            try:
                logger.debug(f"Attempting to fetch models from Vertex AI API: {url}")

                with httpx.Client(timeout=30.0) as client:
                    response = client.get(url, headers=headers)

                    if response.status_code == 200:
                        data = response.json()
                        models = data.get("publisherModels", [])
                        logger.info(
                            f"Successfully fetched {len(models)} models from Vertex AI API using endpoint: {url}"
                        )
                        return models
                    else:
                        last_error = f"Status {response.status_code}: {response.text[:500]}"
                        logger.debug(f"Endpoint {url} returned {response.status_code}")
                        continue

            except Exception as e:
                last_error = str(e)
                logger.debug(f"Endpoint {url} failed: {e}")
                continue

        # All endpoints failed
        logger.warning(
            f"Failed to fetch models from all Vertex AI API endpoints. Last error: {last_error}. "
            "Falling back to static model configuration (12 models)."
        )
        return None

    except Exception as e:
        logger.warning(
            f"Failed to fetch models from Vertex AI API: {e}. Using fallback configuration."
        )
        return None


def _normalize_vertex_api_model(api_model: dict) -> dict | None:
    """Convert a Vertex AI API model response to our normalized format.

    Args:
        api_model: Raw model data from the Vertex AI publishers/google/models API

    Returns:
        Normalized model dict or None if the model should be skipped
    """
    try:
        # Extract model name from the full resource name
        # Format: publishers/google/models/gemini-2.0-flash
        name = api_model.get("name", "")
        provider_model_id = name.split("/")[-1] if "/" in name else name

        if not provider_model_id:
            return None

        # Skip non-generative models (embeddings handled separately, imagen, etc.)
        # We want chat/text generation models
        supported_actions = api_model.get("supportedActions", {})
        if not supported_actions.get("generateContent") and not supported_actions.get(
            "streamGenerateContent"
        ):
            # Check if it's an embedding model we want to include
            if (
                not supported_actions.get("computeTokens")
                and "embedding" not in provider_model_id.lower()
            ):
                return None

        # Get version info
        version_info = api_model.get("versionId", "")
        raw_display_name = api_model.get("openSourceCategory", "") or provider_model_id
        # Clean malformed model names (remove company prefix, parentheses, etc.)
        display_name = clean_model_name(raw_display_name)  # noqa: F841

        # Extract from publisherModelTemplate if available
        template = api_model.get("publisherModelTemplate", {})  # noqa: F841

        # Get context length from inputTokenLimit or default
        input_token_limit = None
        output_token_limit = None

        # Try to get limits from various possible locations in the API response
        if "inputTokenLimit" in api_model:
            input_token_limit = api_model.get("inputTokenLimit")
        if "outputTokenLimit" in api_model:
            output_token_limit = api_model.get("outputTokenLimit")  # noqa: F841

        # Default context lengths based on model family
        if input_token_limit is None:
            if (
                "gemini-3" in provider_model_id
                or "gemini-2.5" in provider_model_id
                or "gemini-2.0" in provider_model_id
            ):
                input_token_limit = 1000000  # 1M context for newer models
            elif "gemini-1.5" in provider_model_id:
                input_token_limit = 1000000
            elif "gemma" in provider_model_id:
                input_token_limit = 8192
            else:
                input_token_limit = 32768  # Safe default

        # Determine modalities
        input_modalities = ["text"]
        output_modalities = ["text"]

        # Gemini models support multimodal input
        if "gemini" in provider_model_id.lower():
            input_modalities = ["text", "image", "audio", "video"]

        # Check for image generation models
        if "imagen" in provider_model_id.lower() or "image" in provider_model_id.lower():
            output_modalities = ["image"]

        # Determine features based on model capabilities
        features = ["streaming"]
        if "gemini" in provider_model_id.lower():
            features.extend(["multimodal", "function_calling"])
            if "pro" in provider_model_id.lower() or "flash" in provider_model_id.lower():
                features.append("thinking")

        if "embedding" in provider_model_id.lower():
            features = ["embeddings"]
            input_modalities = ["text"]
            output_modalities = ["embedding"]

        # Build description
        description = api_model.get("description", "") or f"Google {provider_model_id} model"

        # Create display name
        name_display = (
            api_model.get("displayName", "") or provider_model_id.replace("-", " ").title()
        )

        prefixed_slug = f"google-vertex/{provider_model_id}"

        return {
            "id": provider_model_id,
            "slug": prefixed_slug,
            "canonical_slug": prefixed_slug,
            "hugging_face_id": None,
            "name": name_display,
            "created": None,
            "description": description,
            "context_length": input_token_limit,
            "architecture": {
                "modality": "text->text",
                "input_modalities": input_modalities,
                "output_modalities": output_modalities,
                "tokenizer": None,
                "instruct_type": "chat",
            },
            "pricing": {
                "prompt": None,  # Dynamic pricing not available from API
                "completion": None,
                "request": None,
                "image": None,
                "web_search": None,
                "internal_reasoning": None,
            },
            "per_request_limits": None,
            "supported_parameters": [
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "stream",
            ],
            "default_parameters": {},
            "provider_slug": "google",
            "provider_site_url": "https://cloud.google.com/vertex-ai",
            "model_logo_url": None,
            "source_gateway": "google-vertex",
            "tags": features,
            "raw_google_vertex": {
                "id": provider_model_id,
                "name": name_display,
                "version": version_info,
                "api_response": api_model,
            },
        }

    except Exception as e:
        logger.warning(f"Failed to normalize Vertex AI model: {e}")
        return None


def _get_static_model_config() -> list[dict]:
    """Get static model configurations with pricing info.

    Returns models from google_models_config.py which contains
    accurate pricing and feature information.
    """
    from src.services.google_models_config import get_google_models

    multi_provider_models = get_google_models()
    normalized_models = []

    for model in multi_provider_models:
        vertex_provider = next((p for p in model.providers if p.name == "google-vertex"), None)

        pricing = {}
        features = []
        if vertex_provider:
            pricing = {
                "prompt": str(vertex_provider.cost_per_1k_input),
                "completion": str(vertex_provider.cost_per_1k_output),
                "request": None,
                "image": None,
                "web_search": None,
                "internal_reasoning": None,
            }
            features = vertex_provider.features

        input_modalities = model.modalities if model.modalities else ["text"]
        output_modalities = ["text"]

        prefixed_slug = f"google-vertex/{model.id}"
        # Use the provider-specific model_id if available (e.g., gemini-3-flash-preview)
        # This is the actual model ID used when making API requests to the provider
        provider_model_id = vertex_provider.model_id if vertex_provider else model.id
        normalized = {
            "id": model.id,
            "slug": prefixed_slug,
            "canonical_slug": prefixed_slug,
            "hugging_face_id": None,
            "name": model.name,
            "created": None,
            "description": model.description,
            "context_length": model.context_length,
            "architecture": {
                "modality": "text->text",
                "input_modalities": input_modalities,
                "output_modalities": output_modalities,
                "tokenizer": None,
                "instruct_type": "chat",
            },
            "pricing": pricing,
            "per_request_limits": None,
            "supported_parameters": [
                "max_tokens",
                "temperature",
                "top_p",
                "top_k",
                "stream",
            ],
            "default_parameters": {},
            "provider_slug": "google",
            "provider_site_url": "https://cloud.google.com/vertex-ai",
            "model_logo_url": None,
            "source_gateway": "google-vertex",
            "tags": features,
            # Include provider_model_id for database sync - this is the actual model ID
            # used by the provider (e.g., "gemini-3-flash-preview" for Vertex AI)
            "provider_model_id": provider_model_id,
            "raw_google_vertex": {
                "id": model.id,
                "name": model.name,
                "provider_model_id": provider_model_id,
                "modalities": model.modalities,
                "context_length": model.context_length,
            },
        }
        normalized_models.append(normalized)

    return normalized_models


def fetch_models_from_google_vertex():
    """Fetch models from Google Vertex AI API.

    Attempts to fetch models dynamically from the Vertex AI API.
    Falls back to static configuration if the API call fails.
    Merges dynamic models with static config to get accurate pricing.
    """

    from src.services.model_catalog_cache import cache_gateway_catalog

    logger.info("Fetching Google Vertex AI model catalog")

    try:
        # Get static config for pricing and known models
        static_models = _get_static_model_config()
        static_by_id = {m["id"]: m for m in static_models}

        # Try to fetch dynamic models from API
        api_models = _fetch_models_from_vertex_api()

        if api_models is not None:
            # Merge API models with static config
            normalized_models = []
            seen_ids = set()

            for api_model in api_models:
                normalized = _normalize_vertex_api_model(api_model)
                if normalized is None:
                    continue

                model_id = normalized["id"]

                # If we have static config for this model, use its pricing
                if model_id in static_by_id:
                    static = static_by_id[model_id]
                    normalized["pricing"] = static["pricing"]
                    normalized["tags"] = static["tags"]
                    normalized["name"] = static["name"]
                    normalized["description"] = static["description"]

                normalized_models.append(normalized)
                seen_ids.add(model_id)

            # Add any static models not returned by API (e.g., embeddings, special models)
            for model_id, static_model in static_by_id.items():
                if model_id not in seen_ids:
                    normalized_models.append(static_model)
                    logger.debug(f"Added static model not in API: {model_id}")

            logger.info(
                f"Loaded {len(normalized_models)} Google Vertex AI models "
                f"({len(api_models)} from API, merged with static config)"
            )
        else:
            # Fallback to static config only
            normalized_models = static_models
            logger.info(
                f"Loaded {len(normalized_models)} Google Vertex AI models from static config (API unavailable)"
            )

        # Update cache
        cache_gateway_catalog("google-vertex", normalized_models)

        return normalized_models

    except Exception as e:
        logger.error(f"Failed to load Google Vertex AI models: {e}")
        return []
