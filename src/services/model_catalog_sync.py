"""
Dynamic model catalog synchronization service
Fetches models from all provider APIs and syncs to database
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from src.db.models_catalog_db import bulk_upsert_models
from src.db.providers_db import (
    create_provider,
    get_provider_by_slug,
)
from src.services.aihubmix_client import fetch_models_from_aihubmix
from src.services.aimo_client import fetch_models_from_aimo
from src.services.alibaba_cloud_client import fetch_models_from_alibaba
from src.services.anannas_client import fetch_models_from_anannas
from src.services.anthropic_client import fetch_models_from_anthropic
from src.services.canopywave_client import fetch_models_from_canopywave
from src.services.cerebras_client import fetch_models_from_cerebras
from src.services.chutes_client import fetch_models_from_chutes
from src.services.clarifai_client import fetch_models_from_clarifai
from src.services.cloudflare_workers_ai_client import fetch_models_from_cloudflare_workers_ai
from src.services.cohere_client import fetch_models_from_cohere
from src.services.deepinfra_client import fetch_models_from_deepinfra
from src.services.fal_image_client import fetch_models_from_fal
from src.services.featherless_client import fetch_models_from_featherless
from src.services.fireworks_client import fetch_models_from_fireworks
from src.services.google_vertex_client import fetch_models_from_google_vertex
from src.services.groq_client import fetch_models_from_groq
from src.services.helicone_client import fetch_models_from_helicone
from src.services.huggingface_models import fetch_models_from_huggingface_api
from src.services.modelz_client import fetch_models_from_modelz
from src.services.morpheus_client import fetch_models_from_morpheus
from src.services.near_client import fetch_models_from_near
from src.services.nebius_client import fetch_models_from_nebius
from src.services.novita_client import fetch_models_from_novita
from src.services.onerouter_client import fetch_models_from_onerouter
from src.services.openai_client import fetch_models_from_openai
from src.services.openrouter_client import fetch_models_from_openrouter
from src.services.simplismart_client import fetch_models_from_simplismart
from src.services.sybil_client import fetch_models_from_sybil
from src.services.together_client import fetch_models_from_together
from src.services.vercel_ai_gateway_client import fetch_models_from_vercel_ai_gateway
from src.services.xai_client import fetch_models_from_xai
from src.services.zai_client import fetch_models_from_zai

logger = logging.getLogger(__name__)


# Map provider slugs to their fetch functions
PROVIDER_FETCH_FUNCTIONS = {
    "openrouter": fetch_models_from_openrouter,
    "deepinfra": fetch_models_from_deepinfra,
    "featherless": fetch_models_from_featherless,
    "chutes": fetch_models_from_chutes,
    "groq": fetch_models_from_groq,
    "fireworks": fetch_models_from_fireworks,
    "together": fetch_models_from_together,
    "aimo": fetch_models_from_aimo,
    "near": fetch_models_from_near,
    "fal": fetch_models_from_fal,
    "vercel-ai-gateway": fetch_models_from_vercel_ai_gateway,
    "aihubmix": fetch_models_from_aihubmix,
    "helicone": fetch_models_from_helicone,
    "anannas": fetch_models_from_anannas,
    "alibaba": fetch_models_from_alibaba,
    "huggingface": fetch_models_from_huggingface_api,
    "cerebras": fetch_models_from_cerebras,
    "google-vertex": fetch_models_from_google_vertex,
    "xai": fetch_models_from_xai,
    "nebius": fetch_models_from_nebius,
    "novita": fetch_models_from_novita,
    # Additional providers that were missing
    "openai": fetch_models_from_openai,
    "anthropic": fetch_models_from_anthropic,
    "clarifai": fetch_models_from_clarifai,
    "simplismart": fetch_models_from_simplismart,
    "onerouter": fetch_models_from_onerouter,
    "cloudflare-workers-ai": fetch_models_from_cloudflare_workers_ai,
    "modelz": fetch_models_from_modelz,
    "cohere": fetch_models_from_cohere,
    # Recently added providers
    "zai": fetch_models_from_zai,
    "morpheus": fetch_models_from_morpheus,
    "sybil": fetch_models_from_sybil,
    "canopywave": fetch_models_from_canopywave,
}


def safe_decimal(value: Any) -> Decimal | None:
    """Safely convert a value to Decimal"""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        if isinstance(value, str):
            # Remove any non-numeric characters except decimal point and minus
            cleaned = "".join(c for c in value if c.isdigit() or c in ".-")
            if cleaned:
                return Decimal(cleaned)
        return None
    except (ValueError, TypeError, ArithmeticError):
        return None


def extract_modality(model: dict[str, Any]) -> str:
    """Extract modality from normalized model structure"""
    # Check metadata.architecture first (new location)
    metadata = model.get("metadata", {})
    if isinstance(metadata, dict):
        architecture = metadata.get("architecture")
        if isinstance(architecture, dict):
            modality = architecture.get("modality")
            if modality:
                return modality

    # Fallback to architecture field (deprecated, for backwards compatibility)
    architecture = model.get("architecture")
    if isinstance(architecture, dict):
        modality = architecture.get("modality")
        if modality:
            return modality

    # Check top-level modality field
    if model.get("modality"):
        return model["modality"]

    # Default to text->text
    return "text->text"


def extract_pricing(model: dict[str, Any]) -> dict[str, Decimal | None]:
    """Extract pricing from normalized model structure"""
    pricing = model.get("pricing", {})

    if not isinstance(pricing, dict):
        return {
            "prompt": None,
            "completion": None,
            "image": None,
            "request": None,
        }

    return {
        "prompt": safe_decimal(pricing.get("prompt")),
        "completion": safe_decimal(pricing.get("completion")),
        "image": safe_decimal(pricing.get("image")),
        "request": safe_decimal(pricing.get("request")),
    }


def extract_capabilities(model: dict[str, Any]) -> dict[str, bool]:
    """Extract capability flags from normalized model.

    Capability resolution strategy (metadata-first, name-based fallback):
    1. Check explicit capability fields on the model dict (set by provider clients
       that have authoritative knowledge, e.g. cohere_client.py).
    2. Check architecture metadata (input_modalities for vision; supported_parameters
       for function calling) — derived from provider API responses.
    3. Fall back to model-name heuristics only when metadata is absent.
       Name-based detection is a last resort because it breaks for new models
       whose names don't follow the expected pattern.
    """
    # Check metadata.architecture first (new location)
    metadata = model.get("metadata", {})
    architecture = metadata.get("architecture") if isinstance(metadata, dict) else None

    # Fallback to architecture field (deprecated, for backwards compatibility)
    if not architecture:
        architecture = model.get("architecture", {})

    # Determine capabilities based on modality and architecture
    supports_streaming = model.get("supports_streaming", False)

    # --- supports_function_calling ---
    # Strategy: explicit field > supported_parameters metadata > name-based fallback
    if model.get("supports_function_calling") is not None:
        # Explicit flag set by provider client (authoritative)
        supports_function_calling = bool(model["supports_function_calling"])
    else:
        supported_params = model.get("supported_parameters") or []
        if isinstance(supported_params, list) and supported_params:
            # Provider API returned a parameter list — check for tool/function support
            supports_function_calling = any(
                p in supported_params
                for p in ("tools", "tool_choice", "function_call", "functions")
            )
        else:
            # Fall back to name-based detection only if metadata is missing.
            # This is approximate and may miss newly released models.
            model_id = (model.get("id") or model.get("slug") or "").lower()
            supports_function_calling = any(
                pattern in model_id
                for pattern in (
                    "gpt-4",
                    "gpt-3.5-turbo",
                    "claude-3",
                    "gemini",
                    "mistral",
                    "llama-3",
                )
            )

    # --- supports_vision ---
    # Strategy: explicit field > input_modalities metadata > name-based fallback
    if model.get("supports_vision") is not None:
        # Explicit flag set by provider client (authoritative)
        supports_vision = bool(model["supports_vision"])
    elif isinstance(architecture, dict) and architecture.get("input_modalities"):
        # Provider API returned input_modalities — use that (authoritative)
        input_modalities = architecture["input_modalities"]
        if isinstance(input_modalities, list):
            supports_vision = "image" in input_modalities
        else:
            supports_vision = False
    else:
        # Fall back to name-based detection only if metadata is missing.
        # This is approximate and may miss newly released vision models.
        model_id = (model.get("id") or model.get("slug") or "").lower()
        supports_vision = "vision" in model_id or "vl" in model_id

    return {
        "supports_streaming": supports_streaming,
        "supports_function_calling": supports_function_calling,
        "supports_vision": supports_vision,
    }


def transform_normalized_model_to_db_schema(
    normalized_model: dict[str, Any], provider_id: int, provider_slug: str
) -> dict[str, Any] | None:
    """
    Transform a normalized model (from fetch functions) to database schema

    Args:
        normalized_model: Model data from provider fetch function (already normalized)
        provider_id: Database provider ID
        provider_slug: Provider slug for context

    Returns:
        Dictionary matching database schema or None if invalid
    """
    try:
        # CRITICAL: Extract provider_model_id FIRST (the provider's actual API identifier)
        # This is what the provider API expects (e.g., "openai/gpt-4", "gemini-1.5-pro-preview")
        provider_model_id = (
            normalized_model.get("provider_model_id")  # Explicit provider ID if set
            or normalized_model.get("id")  # Most providers use "id" field
            or normalized_model.get("slug")  # Some use "slug"
        )

        if not provider_model_id:
            logger.warning(
                f"[{provider_slug.upper()}] Model SKIPPED | "
                f"Reason: Missing provider_model_id | "
                f"Available keys: {list(normalized_model.keys())} | "
                f"Name: {normalized_model.get('name', 'N/A')}"
            )
            return None

        # Extract model_name (common display name - NOT necessarily unique)
        # This is just a human-readable name like "GPT-4", "Claude 3 Opus", etc.
        # Can be duplicate across providers (e.g., multiple providers may have "GPT-4")
        # Prefer name > id as fallback (id is provider-specific, so use as last resort)
        model_name = (
            normalized_model.get("name")  # Best: explicit display name
            or normalized_model.get("model_id")  # Legacy: old model_id field (being phased out)
            or normalized_model.get("id")  # Fallback: use provider ID if no display name
        )

        if not model_name:
            logger.warning(
                f"[{provider_slug.upper()}] Model SKIPPED | "
                f"Reason: Missing model_name | "
                f"Provider Model ID: {provider_model_id} | "
                f"Available keys: {list(normalized_model.keys())}"
            )
            return None

        # Safety validation: ensure name is clean even if normalization missed it
        from src.utils.model_name_validator import clean_model_name

        model_name = clean_model_name(model_name)
        provider_model_id = clean_model_name(provider_model_id)

        # Extract description
        description = normalized_model.get("description")

        # Extract context length
        context_length = normalized_model.get("context_length")
        if context_length and isinstance(context_length, (int, str)):
            try:
                context_length = int(context_length)
            except (ValueError, TypeError):
                context_length = None

        # Extract modality
        modality = extract_modality(normalized_model)

        # Extract architecture info
        architecture = normalized_model.get("architecture")
        architecture_str = None
        if isinstance(architecture, dict):
            # Store relevant architecture info
            architecture_str = architecture.get("tokenizer") or architecture.get("instruct_type")

        # Extract pricing
        pricing = extract_pricing(normalized_model)

        # Extract capabilities
        capabilities = extract_capabilities(normalized_model)

        # Build metadata
        metadata = {
            "synced_at": datetime.now(UTC).isoformat(),
            "source": provider_slug,
            "source_gateway": normalized_model.get("source_gateway", provider_slug),
            "provider_slug": normalized_model.get("provider_slug"),
            "hugging_face_id": normalized_model.get("hugging_face_id"),
        }

        # Add architecture to metadata if available
        if architecture:
            metadata["architecture"] = architecture

        # Add supported parameters if available
        if normalized_model.get("supported_parameters"):
            metadata["supported_parameters"] = normalized_model["supported_parameters"]

        # Add default parameters if available
        if normalized_model.get("default_parameters"):
            metadata["default_parameters"] = normalized_model["default_parameters"]

        # Store architecture string in metadata (no top-level DB column for it)
        if architecture_str:
            metadata["architecture_str"] = architecture_str

        # NOTE: Both provider_model_id and model_name were extracted at the beginning of this function
        # - provider_model_id (lines 201-209): Provider's UNIQUE API identifier
        #   (e.g., "openai/gpt-4", "gemini-1.5-pro-preview")
        #   Used for making API calls to the provider
        # - model_name (lines 215-219): Common display name (NOT unique, can be duplicate)
        #   (e.g., "GPT-4", "Claude 3 Opus")
        #   Used for display purposes only

        # Build model data - pricing is stored separately in model_pricing table
        model_data = {
            "provider_id": provider_id,
            "model_name": str(model_name),
            "provider_model_id": str(provider_model_id),
            "description": description,
            "context_length": context_length,
            "modality": modality,
            # Capabilities
            "supports_streaming": capabilities["supports_streaming"],
            "supports_function_calling": capabilities["supports_function_calling"],
            "supports_vision": capabilities["supports_vision"],
            # Status
            "is_active": True,
            "metadata": metadata,
        }

        # Store pricing info in metadata for later sync to model_pricing table
        if any(pricing.values()):
            model_data["metadata"]["pricing_raw"] = {
                "prompt": str(pricing["prompt"]) if pricing["prompt"] else None,
                "completion": str(pricing["completion"]) if pricing["completion"] else None,
                "image": str(pricing["image"]) if pricing["image"] else None,
                "request": str(pricing["request"]) if pricing["request"] else None,
            }

        return model_data

    except Exception as e:
        model_id = normalized_model.get("id", "UNKNOWN")
        model_name = normalized_model.get("name", "UNKNOWN")
        logger.error(
            f"[{provider_slug.upper()}] Transformation EXCEPTION | "
            f"Model ID: {model_id} | "
            f"Model Name: {model_name} | "
            f"Error Type: {type(e).__name__} | "
            f"Error: {str(e)} | "
            f"Available Keys: {list(normalized_model.keys())[:10]} | "
            f"Provider ID: {provider_id}",
            exc_info=False,  # Don't include full traceback for transformation errors
        )
        return None


def ensure_provider_exists(provider_slug: str) -> dict[str, Any] | None:
    """
    Ensure provider exists in database, create if not

    Args:
        provider_slug: Provider slug

    Returns:
        Provider dictionary or None on error
    """
    try:
        # Check if provider already exists
        provider = get_provider_by_slug(provider_slug)
        if provider:
            logger.info(f"Provider '{provider_slug}' already exists (ID: {provider['id']})")
            return provider

        # Provider doesn't exist, create it
        logger.info(f"Creating new provider: {provider_slug}")

        # Map of provider metadata
        provider_metadata = {
            "openrouter": {
                "name": "OpenRouter",
                "description": "Multi-provider AI model router",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env_var": "OPENROUTER_API_KEY",
                "site_url": "https://openrouter.ai",
                "supports_streaming": True,
            },
            "deepinfra": {
                "name": "DeepInfra",
                "description": "Deep learning infrastructure",
                "base_url": "https://api.deepinfra.com/v1",
                "api_key_env_var": "DEEPINFRA_API_KEY",
                "site_url": "https://deepinfra.com",
                "supports_streaming": True,
            },
            "featherless": {
                "name": "Featherless",
                "description": "Featherless AI provider",
                "base_url": "https://api.featherless.ai/v1",
                "api_key_env_var": "FEATHERLESS_API_KEY",
                "site_url": "https://featherless.ai",
                "supports_streaming": True,
            },
            "fireworks": {
                "name": "Fireworks AI",
                "description": "Fast AI model inference",
                "base_url": "https://api.fireworks.ai/inference/v1",
                "api_key_env_var": "FIREWORKS_API_KEY",
                "site_url": "https://fireworks.ai",
                "supports_streaming": True,
            },
            "together": {
                "name": "Together AI",
                "description": "Together AI platform",
                "base_url": "https://api.together.xyz/v1",
                "api_key_env_var": "TOGETHER_API_KEY",
                "site_url": "https://together.ai",
                "supports_streaming": True,
            },
            "huggingface": {
                "name": "HuggingFace",
                "description": "HuggingFace inference API",
                "base_url": "https://router.huggingface.co",
                "api_key_env_var": "HUGGINGFACE_API_KEY",
                "site_url": "https://huggingface.co",
                "supports_streaming": True,
            },
            "cerebras": {
                "name": "Cerebras",
                "description": "Cerebras AI ultra-fast inference",
                "base_url": "https://api.cerebras.ai/v1",
                "api_key_env_var": "CEREBRAS_API_KEY",
                "site_url": "https://cerebras.ai",
                "supports_streaming": True,
            },
            "google-vertex": {
                "name": "Google Vertex AI",
                "description": "Google Cloud Vertex AI",
                "base_url": None,
                "api_key_env_var": "GOOGLE_APPLICATION_CREDENTIALS",
                "site_url": "https://cloud.google.com/vertex-ai",
                "supports_streaming": True,
            },
            "xai": {
                "name": "XAI",
                "description": "X.AI (Grok) provider",
                "base_url": "https://api.x.ai/v1",
                "api_key_env_var": "XAI_API_KEY",
                "site_url": "https://x.ai",
                "supports_streaming": True,
            },
            "openai": {
                "name": "OpenAI",
                "description": "OpenAI GPT models",
                "base_url": "https://api.openai.com/v1",
                "api_key_env_var": "OPENAI_API_KEY",
                "site_url": "https://openai.com",
                "supports_streaming": True,
                "supports_function_calling": True,
                "supports_vision": True,
            },
            "anthropic": {
                "name": "Anthropic",
                "description": "Anthropic Claude models",
                "base_url": "https://api.anthropic.com/v1",
                "api_key_env_var": "ANTHROPIC_API_KEY",
                "site_url": "https://anthropic.com",
                "supports_streaming": True,
                "supports_function_calling": True,
                "supports_vision": True,
            },
            "clarifai": {
                "name": "Clarifai",
                "description": "Clarifai AI platform",
                "base_url": "https://api.clarifai.com",
                "api_key_env_var": "CLARIFAI_API_KEY",
                "site_url": "https://clarifai.com",
                "supports_streaming": True,
            },
            "simplismart": {
                "name": "SimpliSmart",
                "description": "SimpliSmart AI inference",
                "base_url": "https://api.simplismart.ai/v1",
                "api_key_env_var": "SIMPLISMART_API_KEY",
                "site_url": "https://simplismart.ai",
                "supports_streaming": True,
            },
            "onerouter": {
                "name": "Infron AI",
                "description": "Infron AI (formerly OneRouter) multi-provider gateway",
                "base_url": "https://api.infron.ai/v1",
                "api_key_env_var": "ONEROUTER_API_KEY",
                "site_url": "https://infron.ai",
                "supports_streaming": True,
            },
            "cloudflare-workers-ai": {
                "name": "Cloudflare Workers AI",
                "description": "Cloudflare Workers AI inference",
                "base_url": None,
                "api_key_env_var": "CLOUDFLARE_API_TOKEN",
                "site_url": "https://developers.cloudflare.com/workers-ai",
                "supports_streaming": True,
            },
            "nebius": {
                "name": "Nebius",
                "description": "Nebius AI Studio",
                "base_url": "https://api.studio.nebius.ai/v1",
                "api_key_env_var": "NEBIUS_API_KEY",
                "site_url": "https://studio.nebius.ai",
                "supports_streaming": True,
            },
            "novita": {
                "name": "Novita",
                "description": "Novita AI inference",
                "base_url": "https://api.novita.ai/v3/openai",
                "api_key_env_var": "NOVITA_API_KEY",
                "site_url": "https://novita.ai",
                "supports_streaming": True,
            },
            "modelz": {
                "name": "Modelz",
                "description": "Modelz AI model deployment platform",
                "base_url": "https://backend.alpacanetwork.ai",
                "api_key_env_var": "MODELZ_API_KEY",
                "site_url": "https://modelz.ai",
                "supports_streaming": True,
            },
        }

        # Get metadata for this provider or use defaults
        metadata = provider_metadata.get(
            provider_slug,
            {
                "name": provider_slug.replace("-", " ").replace("_", " ").title(),
                "description": f"{provider_slug} AI provider",
                "supports_streaming": True,
            },
        )

        provider_data = {
            "name": metadata.get("name"),
            "slug": provider_slug,
            "description": metadata.get("description"),
            "base_url": metadata.get("base_url"),
            "api_key_env_var": metadata.get("api_key_env_var"),
            "site_url": metadata.get("site_url"),
            "is_active": True,
            "supports_streaming": metadata.get("supports_streaming", False),
            "supports_function_calling": metadata.get("supports_function_calling", False),
            "supports_vision": metadata.get("supports_vision", False),
            "supports_image_generation": metadata.get("supports_image_generation", False),
            "metadata": {},
        }

        created_provider = create_provider(provider_data)
        if created_provider:
            logger.info(
                f"Successfully created provider '{provider_slug}' (ID: {created_provider['id']})"
            )
        return created_provider

    except Exception as e:
        logger.error(f"Error ensuring provider exists for {provider_slug}: {e}")
        return None


def sync_provider_models(
    provider_slug: str, dry_run: bool = False, batch_mode: bool = False
) -> dict[str, Any]:
    """
    Sync models for a specific provider with comprehensive performance tracking

    Args:
        provider_slug: Provider slug (e.g., 'openrouter', 'deepinfra')
        dry_run: If True, fetch but don't write to database
        batch_mode: If True, only invalidate provider-specific cache (skip
            full catalog / unique / stats invalidation — caller handles those)

    Returns:
        Dictionary with sync results including performance metrics
    """
    import time

    # Performance tracking
    start_time = time.time()
    metrics = {
        "provider_check_duration": 0,
        "fetch_duration": 0,
        "transform_duration": 0,
        "db_sync_duration": 0,
        "cache_invalidation_duration": 0,
    }

    try:
        # Ensure provider exists in database
        provider_check_start = time.time()
        provider = ensure_provider_exists(provider_slug)
        metrics["provider_check_duration"] = time.time() - provider_check_start
        if not provider:
            return {
                "success": False,
                "error": f"Failed to ensure provider '{provider_slug}' exists",
                "models_fetched": 0,
                "models_synced": 0,
            }

        if not provider.get("is_active"):
            return {
                "success": False,
                "error": f"Provider '{provider_slug}' is inactive",
                "models_fetched": 0,
                "models_synced": 0,
            }

        # Get fetch function for this provider
        fetch_func = PROVIDER_FETCH_FUNCTIONS.get(provider_slug)
        if not fetch_func:
            return {
                "success": False,
                "error": f"No fetch function configured for '{provider_slug}'",
                "models_fetched": 0,
                "models_synced": 0,
            }

        logger.info(f"[{provider_slug.upper()}] Starting model fetch...")

        # Fetch models from provider API (these are already normalized)
        fetch_start = time.time()
        normalized_models = fetch_func()
        metrics["fetch_duration"] = time.time() - fetch_start

        if not normalized_models:
            total_duration = time.time() - start_time
            logger.warning(
                f"[{provider_slug.upper()}] No models returned | "
                f"Duration: {total_duration:.2f}s"
            )
            return {
                "success": True,
                "provider": provider_slug,
                "provider_id": provider["id"],
                "models_fetched": 0,
                "models_synced": 0,
                "message": "No models fetched (may be API error or empty catalog)",
                "metrics": metrics,
                "total_duration": total_duration,
            }

        logger.info(
            f"[{provider_slug.upper()}] Fetch completed | "
            f"Models: {len(normalized_models)} | "
            f"Duration: {metrics['fetch_duration']:.2f}s | "
            f"Rate: {len(normalized_models) / metrics['fetch_duration']:.0f} models/sec"
        )

        # Transform to database schema
        transform_start = time.time()
        db_models = []
        skipped = 0
        for model in normalized_models:
            try:
                db_model = transform_normalized_model_to_db_schema(
                    model, provider["id"], provider_slug
                )
                if db_model:
                    db_models.append(db_model)
                else:
                    skipped += 1
            except Exception as e:
                logger.error(
                    f"[{provider_slug.upper()}] Transformation FAILED | "
                    f"Model ID: {model.get('id', 'UNKNOWN')} | "
                    f"Error: {type(e).__name__}: {str(e)}"
                )
                skipped += 1
                continue

        metrics["transform_duration"] = time.time() - transform_start

        if not db_models:
            total_duration = time.time() - start_time
            return {
                "success": False,
                "error": f"Failed to transform any models ({skipped} skipped)",
                "provider": provider_slug,
                "provider_id": provider["id"],
                "models_fetched": len(normalized_models),
                "models_synced": 0,
                "metrics": metrics,
                "total_duration": total_duration,
            }

        logger.info(
            f"[{provider_slug.upper()}] Transformation completed | "
            f"Transformed: {len(db_models)} | "
            f"Skipped: {skipped} | "
            f"Duration: {metrics['transform_duration']:.2f}s | "
            f"Rate: {len(db_models) / metrics['transform_duration']:.0f} models/sec"
        )

        # Sync to database (unless dry run)
        if not dry_run:
            logger.info(f"[{provider_slug.upper()}] Starting database sync...")
            db_sync_start = time.time()
            synced_models = bulk_upsert_models(db_models)
            models_synced = len(synced_models) if synced_models else 0
            metrics["db_sync_duration"] = time.time() - db_sync_start

            logger.info(
                f"[{provider_slug.upper()}] Database sync completed | "
                f"Synced: {models_synced} | "
                f"Duration: {metrics['db_sync_duration']:.2f}s | "
                f"Rate: {models_synced / metrics['db_sync_duration']:.0f} models/sec"
            )

            # Pricing is now synced directly during model sync via metadata.pricing_raw
            # The separate pricing sync service has been deprecated

            # Invalidate caches to ensure fresh data is served on next request.
            # In batch_mode, only invalidate provider-specific cache (no cascade
            # to full catalog). The caller (sync_all_providers) handles global
            # invalidation ONCE at the end instead of 38+ times per provider.
            cache_invalidation_start = time.time()
            try:
                from src.services.model_catalog_cache import (
                    invalidate_catalog_stats,
                    invalidate_provider_catalog,
                    invalidate_unique_models,
                )

                if batch_mode:
                    # Provider-only: cascade=False prevents invalidating full catalog
                    invalidate_provider_catalog(provider_slug, cascade=False)
                    logger.debug(f"[{provider_slug.upper()}] Cache INVALIDATE (batch, no cascade)")
                else:
                    # Single-provider sync: full cascade
                    invalidate_provider_catalog(provider_slug, cascade=True)
                    invalidate_unique_models()
                    invalidate_catalog_stats()
                    logger.info(f"[{provider_slug.upper()}] Cache INVALIDATE (full cascade)")

            except Exception as cache_e:
                logger.warning(f"[{provider_slug.upper()}] Cache invalidation failed: {cache_e}")

            metrics["cache_invalidation_duration"] = time.time() - cache_invalidation_start
        else:
            models_synced = 0
            logger.info(f"[{provider_slug.upper()}] DRY RUN: Would sync {len(db_models)} models")

        # Calculate total duration and efficiency metrics
        total_duration = time.time() - start_time
        models_per_sec = len(normalized_models) / total_duration if total_duration > 0 else 0

        logger.info(
            f"[{provider_slug.upper()}] SYNC COMPLETE | "
            f"Total Duration: {total_duration:.2f}s | "
            f"Models: {models_synced}/{len(normalized_models)} | "
            f"Overall Rate: {models_per_sec:.0f} models/sec"
        )

        return {
            "success": True,
            "provider": provider_slug,
            "provider_id": provider["id"],
            "models_fetched": len(normalized_models),
            "models_transformed": len(db_models),
            "models_skipped": skipped,
            "models_synced": models_synced,
            "dry_run": dry_run,
            "metrics": metrics,
            "total_duration": total_duration,
            "models_per_sec": round(models_per_sec, 2),
        }

    except Exception as e:
        logger.error(f"Error syncing models for {provider_slug}: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "provider": provider_slug,
            "models_fetched": 0,
            "models_synced": 0,
        }


def sync_all_providers(
    provider_slugs: list[str] | None = None, dry_run: bool = False
) -> dict[str, Any]:
    """
    Sync models from all providers with comprehensive performance tracking

    Args:
        provider_slugs: Optional list of specific providers to sync
        dry_run: If True, fetch but don't write to database

    Returns:
        Dictionary with overall sync results and performance metrics
    """
    import time

    try:
        from src.config.config import Config

        sync_start_time = time.time()

        # Get providers to sync
        if provider_slugs:
            providers_to_sync = provider_slugs
        else:
            # Use all providers that have fetch functions, minus skipped ones
            skip_set = Config.MODEL_SYNC_SKIP_PROVIDERS
            all_providers = list(PROVIDER_FETCH_FUNCTIONS.keys())
            providers_to_sync = [p for p in all_providers if p not in skip_set]
            if skip_set:
                logger.info(
                    f"Skipping {len(skip_set)} providers from sync: {', '.join(sorted(skip_set))}"
                )

        logger.info(f"\n{'='*80}")
        logger.info(f"{'STARTING PROVIDER SYNC':^80}")
        logger.info(f"{'='*80}")
        logger.info(f"Providers to sync: {len(providers_to_sync)}")
        logger.info(f"Dry run mode: {dry_run}")
        logger.info(f"{'='*80}\n")

        results = []
        total_fetched = 0
        total_transformed = 0
        total_skipped = 0
        total_synced = 0
        errors = []

        import gc

        for i, provider_slug in enumerate(providers_to_sync, 1):
            logger.info(
                f"\n{'='*80}\n[{i}/{len(providers_to_sync)}] Syncing: {provider_slug.upper()}\n{'='*80}"
            )
            result = sync_provider_models(provider_slug, dry_run=dry_run, batch_mode=True)
            results.append(result)

            if result["success"]:
                total_fetched += result.get("models_fetched", 0)
                total_transformed += result.get("models_transformed", 0)
                total_skipped += result.get("models_skipped", 0)
                total_synced += result.get("models_synced", 0)
            else:
                errors.append({"provider": provider_slug, "error": result.get("error")})

            # Free memory after each provider to prevent accumulation.
            # Large providers like featherless (17k+ models) can hold ~50MB
            # in fetch/transform buffers that Python's GC won't collect
            # immediately without an explicit nudge.
            gc.collect()

        # Invalidate global caches ONCE after all providers are done
        # (instead of 35+ times per provider in the loop)
        if total_synced > 0 and not dry_run:
            try:
                from src.services.model_catalog_cache import (
                    invalidate_catalog_stats,
                    invalidate_full_catalog,
                    invalidate_unique_models,
                )

                invalidate_full_catalog()
                invalidate_unique_models()
                invalidate_catalog_stats()
                logger.info(
                    f"Global caches invalidated once after syncing {len(providers_to_sync)} providers"
                )
            except Exception as cache_e:
                logger.warning(f"Post-sync global cache invalidation failed: {cache_e}")

        success = len(errors) == 0
        total_duration = time.time() - sync_start_time

        # Calculate performance metrics
        avg_duration_per_provider = (
            total_duration / len(providers_to_sync) if providers_to_sync else 0
        )
        overall_models_per_sec = total_fetched / total_duration if total_duration > 0 else 0

        # Find slowest and fastest providers
        slowest = max(results, key=lambda r: r.get("total_duration", 0)) if results else None
        fastest = min(results, key=lambda r: r.get("total_duration", 999999)) if results else None

        # Calculate success rate
        success_count = sum(1 for r in results if r.get("success"))
        success_rate = (success_count / len(results) * 100) if results else 0

        # Print comprehensive dashboard
        logger.info(f"\n{'='*80}")
        logger.info(f"{'SYNC SUMMARY DASHBOARD':^80}")
        logger.info(f"{'='*80}\n")

        # Overall Statistics
        logger.info(f"{'OVERALL STATISTICS':-<80}")
        logger.info(f"{'Total Duration':<40} {total_duration:>19.2f}s")
        logger.info(f"{'Providers Processed':<40} {len(providers_to_sync):>20}")
        logger.info(f"{'Successful Syncs':<40} {success_count:>20} ({success_rate:.1f}%)")
        logger.info(f"{'Failed Syncs':<40} {len(errors):>20}")
        logger.info(f"{'Dry Run Mode':<40} {str(dry_run):>20}\n")

        # Model Statistics
        logger.info(f"{'MODEL STATISTICS':-<80}")
        logger.info(f"{'Total Fetched':<40} {total_fetched:>20}")
        logger.info(
            f"{'Total Transformed':<40} {total_transformed:>20} ({total_transformed/max(total_fetched,1)*100:>6.1f}%)"
        )
        logger.info(
            f"{'Total Skipped':<40} {total_skipped:>20} ({total_skipped/max(total_fetched,1)*100:>6.1f}%)"
        )
        logger.info(
            f"{'Total Synced':<40} {total_synced:>20} ({total_synced/max(total_transformed,1)*100:>6.1f}%)\n"
        )

        # Performance Metrics
        logger.info(f"{'PERFORMANCE METRICS':-<80}")
        logger.info(f"{'Overall Rate':<40} {overall_models_per_sec:>15.0f} models/sec")
        logger.info(f"{'Avg Duration per Provider':<40} {avg_duration_per_provider:>19.2f}s")

        if slowest:
            logger.info(
                f"{'Slowest Provider':<40} {slowest['provider']:>20} ({slowest.get('total_duration', 0):.2f}s)"
            )
        if fastest:
            logger.info(
                f"{'Fastest Provider':<40} {fastest['provider']:>20} ({fastest.get('total_duration', 0):.2f}s)\n"
            )

        # Top 5 Largest Providers (by models synced)
        top_providers = sorted(results, key=lambda r: r.get("models_synced", 0), reverse=True)[:5]
        if top_providers:
            logger.info(f"{'TOP 5 PROVIDERS (by models synced)':-<80}")
            for rank, provider_result in enumerate(top_providers, 1):
                prov_name = provider_result.get("provider", "unknown")
                prov_count = provider_result.get("models_synced", 0)
                prov_duration = provider_result.get("total_duration", 0)
                prov_rate = provider_result.get("models_per_sec", 0)
                logger.info(
                    f"{rank}. {prov_name:<25} {prov_count:>10} models | "
                    f"{prov_duration:>6.2f}s | {prov_rate:>6.0f} m/s"
                )
            logger.info("")

        # Error Details
        if errors:
            logger.info(f"{'ERRORS':-<80}")
            for i, error in enumerate(errors, 1):
                logger.error(f"{i}. {error['provider']:<25} {error['error']}")
            logger.info("")

        logger.info(f"{'='*80}\n")

        return {
            "success": success,
            "providers_processed": len(providers_to_sync),
            "successful_syncs": success_count,
            "failed_syncs": len(errors),
            "success_rate_percent": round(success_rate, 2),
            "total_models_fetched": total_fetched,
            "total_models_transformed": total_transformed,
            "total_models_skipped": total_skipped,
            "total_models_synced": total_synced,
            "total_duration": round(total_duration, 2),
            "avg_duration_per_provider": round(avg_duration_per_provider, 2),
            "overall_models_per_sec": round(overall_models_per_sec, 2),
            "slowest_provider": slowest["provider"] if slowest else None,
            "fastest_provider": fastest["provider"] if fastest else None,
            "errors": errors,
            "results": results,
            "dry_run": dry_run,
            "synced_at": datetime.now(UTC).isoformat(),
        }

    except Exception as e:
        logger.error(f"Error in sync_all_providers: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "providers_processed": 0,
            "total_models_fetched": 0,
            "total_models_synced": 0,
        }
