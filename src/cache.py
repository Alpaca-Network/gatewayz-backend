"""Cache module for storing model and provider data"""
import threading
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Cache dictionaries for models and providers
_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL
    "stale_ttl": 7200  # 2 hours stale-while-revalidate
}

_portkey_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 1800,  # 30 minute TTL for Portkey catalog
    "stale_ttl": 3600
}

_featherless_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL for Featherless catalog
    "stale_ttl": 7200
}

_huggingface_cache = {
    "data": {},
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL
    "stale_ttl": 7200
}

_provider_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL
    "stale_ttl": 7200
}

_chutes_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL for Chutes catalog
    "stale_ttl": 7200
}

_groq_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 1800,  # 30 minute TTL for Groq catalog
    "stale_ttl": 3600
}

_fireworks_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 1800,  # 30 minute TTL for Fireworks catalog
    "stale_ttl": 3600
}

_together_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 1800,  # 30 minute TTL for Together catalog
    "stale_ttl": 3600
}

# Modelz cache (for token data)
_modelz_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 1800,  # 30 minute TTL for Modelz token data
    "stale_ttl": 3600
}


# DeepInfra cache (individual models only)
_deepinfra_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL
    "stale_ttl": 7200
}

# Portkey-based individual provider caches
_google_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL
    "stale_ttl": 7200
}

_cerebras_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL
    "stale_ttl": 7200
}

_nebius_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL
    "stale_ttl": 7200
}

_xai_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL
    "stale_ttl": 7200
}

_novita_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL
    "stale_ttl": 7200
}

_huggingface_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL
    "stale_ttl": 7200
}

_aimo_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL for AIMO catalog
    "stale_ttl": 7200
}

_near_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL for Near AI catalog
    "stale_ttl": 7200
}

_fal_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL for Fal.ai catalog
    "stale_ttl": 7200
}

_google_vertex_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL for Google Vertex AI models
    "stale_ttl": 7200
}

_vercel_ai_gateway_models_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour TTL for Vercel AI Gateway catalog
    "stale_ttl": 7200
}

# BACKWARD COMPATIBILITY: Alias for old cache name
# Some deployed modules may still reference the old name
_hug_models_cache = _huggingface_models_cache


# Cache access functions
def get_models_cache(gateway: str):
    """Get cache for a specific gateway"""
    cache_map = {
        "openrouter": _models_cache,
        "portkey": _portkey_models_cache,
        "featherless": _featherless_models_cache,
        "deepinfra": _deepinfra_models_cache,
        "chutes": _chutes_models_cache,
        "groq": _groq_models_cache,
        "fireworks": _fireworks_models_cache,
        "together": _together_models_cache,
        "google": _google_models_cache,
        "google-vertex": _google_vertex_models_cache,
        "cerebras": _cerebras_models_cache,
        "nebius": _nebius_models_cache,
        "xai": _xai_models_cache,
        "novita": _novita_models_cache,
        "huggingface": _huggingface_models_cache,
        "hug": _huggingface_models_cache,  # Alias for backward compatibility
        "aimo": _aimo_models_cache,
        "near": _near_models_cache,
        "fal": _fal_models_cache,
        "vercel-ai-gateway": _vercel_ai_gateway_models_cache,
        "modelz": _modelz_cache
    }
    return cache_map.get(gateway.lower())


def get_providers_cache():
    """Get the providers cache"""
    return _provider_cache


def clear_models_cache(gateway: str):
    """Clear cache for a specific gateway"""
    cache_map = {
        "openrouter": _models_cache,
        "portkey": _portkey_models_cache,
        "featherless": _featherless_models_cache,
        "deepinfra": _deepinfra_models_cache,
        "chutes": _chutes_models_cache,
        "groq": _groq_models_cache,
        "fireworks": _fireworks_models_cache,
        "together": _together_models_cache,
        "google": _google_models_cache,
        "google-vertex": _google_vertex_models_cache,
        "cerebras": _cerebras_models_cache,
        "nebius": _nebius_models_cache,
        "xai": _xai_models_cache,
        "novita": _novita_models_cache,
        "huggingface": _huggingface_models_cache,
        "hug": _huggingface_models_cache,  # Alias for backward compatibility
        "aimo": _aimo_models_cache,
        "near": _near_models_cache,
        "fal": _fal_models_cache,
        "vercel-ai-gateway": _vercel_ai_gateway_models_cache,
        "modelz": _modelz_cache
    }
    cache = cache_map.get(gateway.lower())
    if cache:
        cache["data"] = None
        cache["timestamp"] = None


def clear_providers_cache():
    """Clear the providers cache"""
    _provider_cache["data"] = None
    _provider_cache["timestamp"] = None


def get_modelz_cache():
    """Get the Modelz cache"""
    return _modelz_cache


def clear_modelz_cache():
    """Clear the Modelz cache"""
    _modelz_cache["data"] = None
    _modelz_cache["timestamp"] = None


def is_cache_fresh(cache: dict) -> bool:
    """Check if cache is within fresh TTL"""
    if not cache.get("data") or not cache.get("timestamp"):
        return False
    cache_age = (datetime.now(timezone.utc) - cache["timestamp"]).total_seconds()
    return cache_age < cache.get("ttl", 3600)


def is_cache_stale_but_usable(cache: dict) -> bool:
    """Check if cache is stale but within stale-while-revalidate window"""
    if not cache.get("data") or not cache.get("timestamp"):
        return False
    cache_age = (datetime.now(timezone.utc) - cache["timestamp"]).total_seconds()
    ttl = cache.get("ttl", 3600)
    stale_ttl = cache.get("stale_ttl", ttl * 2)
    return ttl <= cache_age < stale_ttl


def should_revalidate_in_background(cache: dict) -> bool:
    """Check if cache should trigger background revalidation"""
    return not is_cache_fresh(cache) and is_cache_stale_but_usable(cache)


def initialize_fal_cache():
    """Initialize FAL models cache from static catalog

    This function is called from models.py to avoid circular imports.
    If initialization fails, FAL models will be loaded lazily on first request.
    """
    try:
        from src.services.fal_image_client import load_fal_models_catalog
        from src.services.models import normalize_fal_model

        # Load raw models from catalog
        raw_models = load_fal_models_catalog()

        if raw_models:
            # Normalize models
            normalized_models = [
                normalize_fal_model(model) for model in raw_models if model
            ]

            # Populate cache
            if normalized_models:
                _fal_models_cache["data"] = normalized_models
                _fal_models_cache["timestamp"] = datetime.now(timezone.utc)
                logger.debug(f"Initialized FAL models cache with {len(normalized_models)} models")
    except (ImportError, AttributeError, ValueError, TypeError) as error:
        # Log but do not fail - FAL models will be loaded on first request
        logger.debug(f"FAL cache initialization deferred: {error}")
