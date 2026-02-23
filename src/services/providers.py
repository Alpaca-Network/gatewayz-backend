import json
import logging
import threading
from datetime import datetime, UTC
from urllib.parse import urlparse

import httpx

from src.config import Config

# Initialize logging
logger = logging.getLogger(__name__)

# Provider cache for OpenRouter providers
_provider_cache = {
    "data": None,
    "timestamp": None,
    "ttl": 3600,  # 1 hour (providers change less frequently)
}
_provider_cache_lock = threading.Lock()

# Manual mapping for major providers (high-quality logos) — built once at module level
MANUAL_LOGO_DB = {
    "openai": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/openai.svg",
    "anthropic": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/anthropic.svg",
    "google": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/google.svg",
    "meta": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/meta.svg",
    "microsoft": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/microsoft.svg",
    "nvidia": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/nvidia.svg",
    "cohere": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/cohere.svg",
    "mistralai": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/mistralai.svg",
    "perplexity": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/perplexity.svg",
    "amazon": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/amazon.svg",
    "baidu": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/baidu.svg",
    "tencent": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/tencent.svg",
    "alibaba": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/alibabacloud.svg",
    "ai21": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/ai21labs.svg",
    "inflection": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/inflection.svg",
    "vercel": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/vercel.svg",
    "helicone": "https://www.helicone.ai/favicon.ico",
    "aihubmix": "https://aihubmix.com/favicon.ico",
    "anannas": "https://api.anannas.ai/favicon.ico",
    "alpaca-network": "https://console.anyscale.com/favicon.ico",
    "onerouter": "https://infron.ai/favicon.ico",
    "simplismart": "https://simplismart.ai/favicon.ico",
    # Gateway-slug entries (matching GATEWAY_REGISTRY keys in catalog.py)
    "openrouter": "https://openrouter.ai/favicon.ico",
    "groq": "https://groq.com/favicon.ico",
    "together": "https://together.ai/favicon.ico",
    "fireworks": "https://fireworks.ai/favicon.ico",
    "vercel-ai-gateway": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/vercel.svg",
    "featherless": "https://featherless.ai/favicon.ico",
    "chutes": "https://chutes.ai/favicon.ico",
    "deepinfra": "https://deepinfra.com/favicon.ico",
    "google-vertex": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/google.svg",
    "cerebras": "https://cerebras.ai/favicon.ico",
    "nebius": "https://nebius.ai/favicon.ico",
    "xai": "https://x.ai/favicon.ico",
    "novita": "https://novita.ai/favicon.ico",
    "huggingface": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/huggingface.svg",
    "aimo": "https://aimo.network/favicon.ico",
    "near": "https://near.ai/favicon.ico",
    "fal": "https://fal.ai/favicon.ico",
    "alpaca": "https://alpaca.network/favicon.ico",
    "clarifai": "https://clarifai.com/favicon.ico",
    "zai": "https://z.ai/favicon.ico",
    "sybil": "https://sybil.com/favicon.ico",
    "cloudflare-workers-ai": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/cloudflare.svg",
    "morpheus": "https://mor.org/favicon.ico",
    "canopywave": "https://canopywave.io/favicon.ico",
    "notdiamond": "https://notdiamond.ai/favicon.ico",
    # Additional model-developer slugs (appear as provider_slug derived from model IDs)
    "deepseek": "https://deepseek.com/favicon.ico",
    "qwen": "https://qwen.ai/favicon.ico",
    "sambanova": "https://sambanova.ai/favicon.ico",
    "mistral": "https://cdn.jsdelivr.net/gh/simple-icons/simple-icons@develop/icons/mistralai.svg",
}


def get_cached_providers():
    """Get cached providers or fetch from OpenRouter if cache is expired"""
    try:
        with _provider_cache_lock:
            if _provider_cache["data"] and _provider_cache["timestamp"]:
                cache_age = (datetime.now(UTC) - _provider_cache["timestamp"]).total_seconds()
                if cache_age < _provider_cache["ttl"]:
                    return _provider_cache["data"]

        # Cache expired or empty, fetch fresh data
        return fetch_providers_from_openrouter()
    except Exception as e:
        logger.error(f"Error getting cached providers: {e}")
        return None


def fetch_providers_from_openrouter():
    """Fetch providers from OpenRouter API"""
    try:
        if not Config.OPENROUTER_API_KEY:
            logger.error("OpenRouter API key not configured")
            return None

        headers = {
            "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "Gatewayz/2.0.4 (https://gatewayz.ai)",
        }

        response = httpx.get("https://openrouter.ai/api/v1/providers", headers=headers, timeout=30)
        response.raise_for_status()

        try:
            providers_data = response.json()
        except json.JSONDecodeError as json_err:
            logger.error(
                f"Failed to parse JSON response from OpenRouter providers API: {json_err}. "
                f"Response status: {response.status_code}, Content-Type: {response.headers.get('content-type')}"
            )
            return None

        with _provider_cache_lock:
            _provider_cache["data"] = providers_data.get("data", [])
            _provider_cache["timestamp"] = datetime.now(UTC)

        return _provider_cache["data"]
    except Exception as e:
        logger.error(f"Failed to fetch providers from OpenRouter: {e}")
        return None


def get_provider_logo_from_services(provider_id: str, site_url: str = None) -> str:
    """Get provider logo using third-party services and manual mapping"""
    try:
        # Try manual mapping first
        if provider_id in MANUAL_LOGO_DB:
            logger.info(f"Found manual logo for {provider_id}")
            return MANUAL_LOGO_DB[provider_id]

        # Fallback to third-party service using site URL
        if site_url:
            try:
                parsed = urlparse(site_url)
                domain = parsed.netloc
                # Remove www. prefix if present
                if domain.startswith("www."):
                    domain = domain[4:]

                # Use Clearbit Logo API
                logo_url = f"https://logo.clearbit.com/{domain}"
                logger.info(f"Using Clearbit logo service for {provider_id}: {logo_url}")
                return logo_url
            except Exception as e:
                logger.error(f"Error extracting domain from {site_url}: {e}")

        logger.info(f"No logo found for provider {provider_id}")
        return None
    except Exception as e:
        logger.error(f"Error getting provider logo for {provider_id}: {e}")
        return None


def get_provider_info(provider_id: str, provider_name: str) -> dict:
    """Get provider information from OpenRouter providers API with Hugging Face logos"""
    try:
        providers = get_cached_providers()
        if not providers:
            return {
                "logo_url": None,
                "site_url": None,
                "privacy_policy_url": None,
                "terms_of_service_url": None,
                "status_page_url": None,
            }

        # Find provider by slug (provider_id)
        provider_info = None
        for provider in providers:
            if provider.get("slug") == provider_id:
                provider_info = provider
                break

        # Get site URL from OpenRouter
        site_url = None
        if provider_info and provider_info.get("privacy_policy_url"):
            # Extract domain from privacy policy URL
            parsed = urlparse(provider_info["privacy_policy_url"])
            site_url = f"{parsed.scheme}://{parsed.netloc}"

        # Get logo using manual mapping and third-party services
        logo_url = get_provider_logo_from_services(provider_id, site_url)

        if provider_info:
            return {
                "logo_url": logo_url,
                "site_url": site_url,
                "privacy_policy_url": provider_info.get("privacy_policy_url"),
                "terms_of_service_url": provider_info.get("terms_of_service_url"),
                "status_page_url": provider_info.get("status_page_url"),
            }
        else:
            # Provider not found in OpenRouter providers list
            return {
                "logo_url": logo_url,
                "site_url": site_url,
                "privacy_policy_url": None,
                "terms_of_service_url": None,
                "status_page_url": None,
            }
    except Exception as e:
        logger.error(f"Error getting provider info for {provider_id}: {e}")
        return {
            "logo_url": None,
            "site_url": None,
            "privacy_policy_url": None,
            "terms_of_service_url": None,
            "status_page_url": None,
        }


def enhance_providers_with_logos_and_sites(providers: list) -> list:
    """Enhance providers with site_url and logo_url (shared logic)"""
    try:
        enhanced_providers = []
        for provider in providers:
            # Extract site URL from various sources
            site_url = None

            # Try privacy policy URL first
            if provider.get("privacy_policy_url"):
                try:
                    parsed = urlparse(provider["privacy_policy_url"])
                    site_url = f"{parsed.scheme}://{parsed.netloc}"
                except Exception as e:
                    logger.debug(f"Failed to parse privacy_policy_url for provider: {e}")

            # Try terms of service URL if privacy policy didn't work
            if not site_url and provider.get("terms_of_service_url"):
                try:
                    parsed = urlparse(provider["terms_of_service_url"])
                    site_url = f"{parsed.scheme}://{parsed.netloc}"
                except Exception as e:
                    logger.debug(f"Failed to parse terms_of_service_url for provider: {e}")

            # Try status page URL if others didn't work
            if not site_url and provider.get("status_page_url"):
                try:
                    parsed = urlparse(provider["status_page_url"])
                    site_url = f"{parsed.scheme}://{parsed.netloc}"
                except Exception as e:
                    logger.debug(f"Failed to parse status_page_url for provider: {e}")

            # Manual mapping for known providers
            if not site_url:
                manual_site_urls = {
                    "openai": "https://openai.com",
                    "anthropic": "https://anthropic.com",
                    "google": "https://google.com",
                    "meta": "https://meta.com",
                    "microsoft": "https://microsoft.com",
                    "cohere": "https://cohere.com",
                    "mistralai": "https://mistral.ai",
                    "perplexity": "https://perplexity.ai",
                    "amazon": "https://aws.amazon.com",
                    "baidu": "https://baidu.com",
                    "tencent": "https://tencent.com",
                    "alibaba": "https://alibaba.com",
                    "ai21": "https://ai21.com",
                    "inflection": "https://inflection.ai",
                }
                site_url = manual_site_urls.get(provider.get("slug"))

            # Generate logo URL using Google favicon service
            logo_url = None
            if site_url:
                # Extract domain from URL for favicon service
                try:
                    parsed_url = urlparse(site_url)
                    domain = parsed_url.netloc or parsed_url.path
                    # Remove www. prefix if present
                    if domain.startswith("www."):
                        domain = domain[4:]
                    logo_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
                except Exception as e:
                    logger.warning(f"Failed to parse site_url '{site_url}': {e}")
                    # Fallback to old method
                    clean_url = (
                        site_url.replace("https://", "").replace("http://", "").split("/")[0]
                    )
                    if clean_url.startswith("www."):
                        clean_url = clean_url[4:]
                    logo_url = f"https://www.google.com/s2/favicons?domain={clean_url}&sz=128"

            # Validate logo URL: must start with http:// or https://
            if logo_url:
                try:
                    parsed_logo = urlparse(logo_url)
                    if parsed_logo.scheme not in ("http", "https"):
                        logger.debug(f"Invalid logo URL scheme for provider '{provider.get('slug')}': {logo_url!r}")
                        logo_url = None
                except Exception:
                    logo_url = None

            enhanced_provider = {**provider, "site_url": site_url, "logo_url": logo_url}

            enhanced_providers.append(enhanced_provider)

        return enhanced_providers
    except Exception as e:
        logger.error(f"Error enhancing providers with logos and sites: {e}")
        return providers


# Import fetch_models functions from their respective client modules
def fetch_models_from_cerebras():
    """Fetch models from Cerebras client"""
    from src.services.cerebras_client import fetch_models_from_cerebras as _fetch

    return _fetch()


def fetch_models_from_xai():
    """Fetch models from xAI client"""
    from src.services.xai_client import fetch_models_from_xai as _fetch

    return _fetch()


def fetch_models_from_nebius():
    """Fetch models from Nebius client"""
    from src.services.nebius_client import fetch_models_from_nebius as _fetch

    return _fetch()


def fetch_models_from_novita():
    """Fetch models from Novita client"""
    from src.services.novita_client import fetch_models_from_novita as _fetch

    return _fetch()


def fetch_models_from_onerouter():
    """Fetch models from Infron AI client"""
    from src.services.onerouter_client import fetch_models_from_onerouter as _fetch

    return _fetch()


def fetch_models_from_morpheus():
    """Fetch models from Morpheus AI Gateway client"""
    from src.services.morpheus_client import fetch_models_from_morpheus as _fetch

    return _fetch()


def fetch_models_from_simplismart():
    """Fetch models from Simplismart client"""
    from src.services.simplismart_client import fetch_models_from_simplismart as _fetch

    return _fetch()
