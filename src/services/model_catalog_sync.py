"""
Dynamic model catalog synchronization service
Fetches models from all provider APIs and syncs to database
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.db.models_catalog_db import bulk_upsert_models
from src.db.providers_db import (
    create_provider,
    get_provider_by_slug,
)
from src.services.cerebras_client import fetch_models_from_cerebras
from src.services.clarifai_client import fetch_models_from_clarifai
from src.services.cloudflare_workers_ai_client import fetch_models_from_cloudflare_workers_ai
from src.services.cohere_client import fetch_models_from_cohere
from src.services.google_vertex_client import fetch_models_from_google_vertex
from src.services.huggingface_models import fetch_models_from_huggingface_api
from src.services.models import (
    fetch_models_from_aihubmix,
    fetch_models_from_aimo,
    fetch_models_from_alibaba,
    fetch_models_from_anannas,
    fetch_models_from_anthropic,
    fetch_models_from_chutes,
    fetch_models_from_deepinfra,
    fetch_models_from_fal,
    fetch_models_from_featherless,
    fetch_models_from_fireworks,
    fetch_models_from_groq,
    fetch_models_from_helicone,
    fetch_models_from_near,
    fetch_models_from_openai,
    fetch_models_from_openrouter,
    fetch_models_from_together,
    fetch_models_from_vercel_ai_gateway,
    fetch_models_from_zai,
)
from src.services.modelz_client import fetch_models_from_modelz
from src.services.nebius_client import fetch_models_from_nebius
from src.services.novita_client import fetch_models_from_novita
from src.services.onerouter_client import fetch_models_from_onerouter
from src.services.simplismart_client import fetch_models_from_simplismart
from src.services.xai_client import fetch_models_from_xai
from src.services.morpheus_client import fetch_models_from_morpheus
from src.services.sybil_client import fetch_models_from_sybil
from src.services.canopywave_client import fetch_models_from_canopywave

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
    # Check architecture field first
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
    """Extract capability flags from normalized model"""
    architecture = model.get("architecture", {})

    # Determine capabilities based on modality and architecture
    supports_streaming = model.get("supports_streaming", False)
    supports_function_calling = model.get("supports_function_calling", False)

    # Check for vision support
    supports_vision = False
    if isinstance(architecture, dict):
        input_modalities = architecture.get("input_modalities", [])
        if isinstance(input_modalities, list):
            supports_vision = "image" in input_modalities

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
        # Extract model ID - try various fields
        model_id = (
            normalized_model.get("id")
            or normalized_model.get("slug")
            or normalized_model.get("canonical_slug")
            or normalized_model.get("model_id")
        )

        if not model_id:
            logger.warning(f"Skipping model without ID from {provider_slug}: {normalized_model}")
            return None

        # Extract model name
        model_name = normalized_model.get("name") or model_id

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

        # Extract top provider
        top_provider = normalized_model.get("top_provider") or provider_slug

        # Extract per-request limits
        per_request_limits = normalized_model.get("per_request_limits")

        # Extract pricing
        pricing = extract_pricing(normalized_model)

        # Extract capabilities
        capabilities = extract_capabilities(normalized_model)

        # Build metadata
        metadata = {
            "synced_at": datetime.now(timezone.utc).isoformat(),
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

        # Extract provider_model_id - use explicit field if available, otherwise fall back to model_id
        # This is important for providers like Google Vertex where the model_id (e.g., "gemini-3-flash")
        # differs from the provider_model_id (e.g., "gemini-3-flash-preview")
        provider_model_id = normalized_model.get("provider_model_id") or model_id

        # Build model data - pricing is stored separately in model_pricing table
        model_data = {
            "provider_id": provider_id,
            "model_id": str(model_id),
            "model_name": str(model_name),
            "provider_model_id": str(provider_model_id),
            "description": description,
            "context_length": context_length,
            "modality": modality,
            "architecture": architecture_str,
            "top_provider": top_provider,
            "per_request_limits": per_request_limits,
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
        logger.error(
            f"Error transforming model {normalized_model.get('id')} from {provider_slug}: {e}"
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


def sync_provider_models(provider_slug: str, dry_run: bool = False) -> dict[str, Any]:
    """
    Sync models for a specific provider

    Args:
        provider_slug: Provider slug (e.g., 'openrouter', 'deepinfra')
        dry_run: If True, fetch but don't write to database

    Returns:
        Dictionary with sync results
    """
    try:
        # Ensure provider exists in database
        provider = ensure_provider_exists(provider_slug)
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

        logger.info(f"Fetching models from {provider_slug}...")

        # Fetch models from provider API (these are already normalized)
        normalized_models = fetch_func()

        if not normalized_models:
            logger.warning(f"No models returned from {provider_slug}")
            return {
                "success": True,
                "provider": provider_slug,
                "provider_id": provider["id"],
                "models_fetched": 0,
                "models_synced": 0,
                "message": "No models fetched (may be API error or empty catalog)",
            }

        logger.info(f"Fetched {len(normalized_models)} models from {provider_slug}")

        # Transform to database schema
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
                logger.error(f"Error transforming model {model.get('id')}: {e}")
                skipped += 1
                continue

        if not db_models:
            return {
                "success": False,
                "error": f"Failed to transform any models ({skipped} skipped)",
                "provider": provider_slug,
                "provider_id": provider["id"],
                "models_fetched": len(normalized_models),
                "models_synced": 0,
            }

        logger.info(f"Transformed {len(db_models)} models for {provider_slug} ({skipped} skipped)")

        # Sync to database (unless dry run)
        if not dry_run:
            logger.info(f"Syncing {len(db_models)} models to database...")
            synced_models = bulk_upsert_models(db_models)
            models_synced = len(synced_models) if synced_models else 0
            logger.info(f"Successfully synced {models_synced} models for {provider_slug}")

            # Sync pricing for the synced models
            if synced_models:
                try:
                    from src.services.pricing_sync_background import sync_pricing_on_model_update

                    # Extract model IDs from synced models
                    model_ids = [m.get("id") for m in synced_models if m.get("id")]

                    if model_ids:
                        logger.info(f"Syncing pricing for {len(model_ids)} models...")
                        pricing_stats = sync_pricing_on_model_update(model_ids)
                        logger.info(f"Pricing sync complete: {pricing_stats}")
                except Exception as pricing_e:
                    logger.warning(f"Pricing sync failed for {provider_slug}: {pricing_e}")

                # Invalidate caches to ensure fresh data is served immediately
                try:
                    from src.cache import clear_models_cache
                    from src.services.model_catalog_cache import (
                        invalidate_full_catalog,
                        invalidate_provider_catalog,
                    )

                    # Invalidate in-memory cache for this provider
                    clear_models_cache(provider_slug)
                    # Invalidate Redis provider-specific cache
                    invalidate_provider_catalog(provider_slug)
                    # Then invalidate Redis full catalog (aggregated view)
                    invalidate_full_catalog()
                    logger.info(f"Cache invalidated for {provider_slug} after model sync")
                except Exception as cache_e:
                    logger.warning(f"Cache invalidation failed for {provider_slug}: {cache_e}")
        else:
            models_synced = 0
            logger.info(f"DRY RUN: Would sync {len(db_models)} models for {provider_slug}")

        return {
            "success": True,
            "provider": provider_slug,
            "provider_id": provider["id"],
            "models_fetched": len(normalized_models),
            "models_transformed": len(db_models),
            "models_skipped": skipped,
            "models_synced": models_synced,
            "dry_run": dry_run,
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
    Sync models from all providers (or specified list)

    Args:
        provider_slugs: Optional list of specific providers to sync
        dry_run: If True, fetch but don't write to database

    Returns:
        Dictionary with overall sync results
    """
    try:
        # Get providers to sync
        if provider_slugs:
            providers_to_sync = provider_slugs
        else:
            # Use all providers that have fetch functions
            providers_to_sync = list(PROVIDER_FETCH_FUNCTIONS.keys())

        logger.info(f"Starting model sync for {len(providers_to_sync)} providers...")

        results = []
        total_fetched = 0
        total_transformed = 0
        total_skipped = 0
        total_synced = 0
        errors = []

        for provider_slug in providers_to_sync:
            logger.info(f"\n{'='*60}\nSyncing provider: {provider_slug}\n{'='*60}")
            result = sync_provider_models(provider_slug, dry_run=dry_run)
            results.append(result)

            if result["success"]:
                total_fetched += result.get("models_fetched", 0)
                total_transformed += result.get("models_transformed", 0)
                total_skipped += result.get("models_skipped", 0)
                total_synced += result.get("models_synced", 0)
            else:
                errors.append({"provider": provider_slug, "error": result.get("error")})

        success = len(errors) == 0

        logger.info(f"\n{'='*60}\nSync Summary\n{'='*60}")
        logger.info(f"Providers processed: {len(providers_to_sync)}")
        logger.info(f"Total models fetched: {total_fetched}")
        logger.info(f"Total models transformed: {total_transformed}")
        logger.info(f"Total models skipped: {total_skipped}")
        logger.info(f"Total models synced: {total_synced}")
        logger.info(f"Errors: {len(errors)}")
        if errors:
            for error in errors:
                logger.error(f"  - {error['provider']}: {error['error']}")

        return {
            "success": success,
            "providers_processed": len(providers_to_sync),
            "total_models_fetched": total_fetched,
            "total_models_transformed": total_transformed,
            "total_models_skipped": total_skipped,
            "total_models_synced": total_synced,
            "errors": errors,
            "results": results,
            "dry_run": dry_run,
            "synced_at": datetime.now(timezone.utc).isoformat(),
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
