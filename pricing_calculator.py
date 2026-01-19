"""
Provider Pricing Calculator
Uses provider_pricing_standards.json to accurately calculate costs for any model
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Cache for pricing standards
_pricing_standards_cache: Optional[Dict[str, Any]] = None


def load_pricing_standards() -> Dict[str, Any]:
    """Load provider pricing standards from JSON file"""
    global _pricing_standards_cache

    if _pricing_standards_cache is not None:
        return _pricing_standards_cache

    try:
        standards_file = Path(__file__).parent / "provider_pricing_standards.json"

        if not standards_file.exists():
            logger.warning(f"Pricing standards file not found: {standards_file}")
            return {}

        with open(standards_file) as f:
            _pricing_standards_cache = json.load(f)

        logger.info(
            f"Loaded pricing standards for {len(_pricing_standards_cache.get('providers', {}))} providers"
        )
        return _pricing_standards_cache

    except Exception as e:
        logger.error(f"Failed to load pricing standards: {e}")
        return {}


def get_provider_standard(provider: str) -> Optional[Dict[str, Any]]:
    """
    Get pricing standard for a specific provider

    Args:
        provider: Provider name (e.g., 'openrouter', 'deepinfra')

    Returns:
        Provider pricing standard dictionary or None
    """
    standards = load_pricing_standards()
    providers = standards.get("providers", {})
    return providers.get(provider.lower())


def normalize_to_per_token(
    price: float,
    api_format: str,
    amount: Optional[float] = None,
    scale: Optional[int] = None
) -> float:
    """
    Normalize any pricing format to per-token pricing

    Args:
        price: Price value from API
        api_format: Format from provider standard (e.g., 'per_1K_tokens', 'per_1M_tokens')
        amount: For scientific notation (Near AI)
        scale: For scientific notation (Near AI)

    Returns:
        Normalized price in USD per single token
    """
    if api_format == "per_token":
        return price

    elif api_format == "per_1K_tokens":
        return price / 1000

    elif api_format == "per_1M_tokens":
        return price / 1000000

    elif api_format == "amount_scale":
        if amount is not None and scale is not None:
            return amount * (10 ** scale)
        else:
            logger.warning("Amount and scale required for scientific notation")
            return 0.0

    else:
        logger.warning(f"Unknown API format: {api_format}")
        return price


def calculate_text_model_cost(
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    pricing_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Calculate cost for text-to-text models

    Args:
        provider: Provider name
        prompt_tokens: Number of prompt tokens
        completion_tokens: Number of completion tokens
        pricing_data: Pricing data from API

    Returns:
        Dictionary with cost breakdown
    """
    provider_standard = get_provider_standard(provider)

    if not provider_standard:
        logger.warning(f"No pricing standard found for provider: {provider}")
        # Fallback: assume per-token pricing
        prompt_price = float(pricing_data.get("prompt", 0))
        completion_price = float(pricing_data.get("completion", 0))
    else:
        api_format = provider_standard.get("api_format", "per_token")
        modality_config = provider_standard.get("supported_modalities", {}).get("text->text", {})

        # Handle different field naming conventions
        field_mapping = modality_config.get("field_mapping", {})

        # Get prompt pricing
        prompt_field = field_mapping.get("input", "prompt")
        raw_prompt_price = float(pricing_data.get(prompt_field, 0))

        # Get completion pricing
        completion_field = field_mapping.get("output", "completion")
        raw_completion_price = float(pricing_data.get(completion_field, 0))

        # Normalize to per-token
        prompt_price = normalize_to_per_token(raw_prompt_price, api_format)
        completion_price = normalize_to_per_token(raw_completion_price, api_format)

    # Calculate costs
    prompt_cost = prompt_tokens * prompt_price
    completion_cost = completion_tokens * completion_price
    total_cost = prompt_cost + completion_cost

    return {
        "provider": provider,
        "modality": "text->text",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "prompt_price_per_token": prompt_price,
        "completion_price_per_token": completion_price,
        "prompt_cost": prompt_cost,
        "completion_cost": completion_cost,
        "total_cost": total_cost,
        "currency": "USD"
    }


def calculate_image_model_cost(
    provider: str,
    num_images: int,
    pricing_data: Dict[str, Any],
    image_dimensions: Optional[str] = "1024x1024"
) -> Dict[str, Any]:
    """
    Calculate cost for text-to-image models

    Args:
        provider: Provider name
        num_images: Number of images generated
        pricing_data: Pricing data from API
        image_dimensions: Image size (for reference)

    Returns:
        Dictionary with cost breakdown
    """
    provider_standard = get_provider_standard(provider)

    if not provider_standard:
        logger.warning(f"No pricing standard found for provider: {provider}")
        image_price = float(pricing_data.get("image", 0))
    else:
        # Image pricing is typically flat per image, no conversion needed
        image_price = float(pricing_data.get("image", 0))

    total_cost = num_images * image_price

    return {
        "provider": provider,
        "modality": "text->image",
        "num_images": num_images,
        "image_dimensions": image_dimensions,
        "price_per_image": image_price,
        "total_cost": total_cost,
        "currency": "USD"
    }


def calculate_audio_model_cost(
    provider: str,
    duration: float,
    pricing_data: Dict[str, Any],
    modality: str = "audio->text",
    unit: str = "minutes"
) -> Dict[str, Any]:
    """
    Calculate cost for audio models

    Args:
        provider: Provider name
        duration: Duration in specified unit (minutes or seconds)
        pricing_data: Pricing data from API
        modality: Audio modality (e.g., 'audio->text', 'text->audio')
        unit: Duration unit ('minutes' or 'seconds')

    Returns:
        Dictionary with cost breakdown
    """
    provider_standard = get_provider_standard(provider)

    if not provider_standard:
        logger.warning(f"No pricing standard found for provider: {provider}")
        request_price = float(pricing_data.get("request", 0))
    else:
        request_price = float(pricing_data.get("request", 0))

    total_cost = duration * request_price

    return {
        "provider": provider,
        "modality": modality,
        "duration": duration,
        "duration_unit": unit,
        "price_per_unit": request_price,
        "total_cost": total_cost,
        "currency": "USD"
    }


def calculate_model_cost(
    provider: str,
    model_data: Dict[str, Any],
    usage: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Universal cost calculator that handles any model type

    Args:
        provider: Provider name (e.g., 'openrouter', 'deepinfra')
        model_data: Model data from API including pricing and modality
        usage: Usage data with appropriate fields for the modality
            For text models: {"prompt_tokens": int, "completion_tokens": int}
            For image models: {"num_images": int, "dimensions": str (optional)}
            For audio models: {"duration": float, "unit": str (optional)}

    Returns:
        Dictionary with detailed cost breakdown

    Example:
        >>> model = {
        ...     "id": "openai/gpt-4",
        ...     "architecture": {"modality": "text->text"},
        ...     "pricing": {"prompt": "0.00003", "completion": "0.00006"}
        ... }
        >>> usage = {"prompt_tokens": 100, "completion_tokens": 50}
        >>> calculate_model_cost("openrouter", model, usage)
        {
            "provider": "openrouter",
            "modality": "text->text",
            "total_cost": 0.006,
            ...
        }
    """
    # Determine modality
    architecture = model_data.get("architecture", {})
    modality = architecture.get("modality", "text->text")
    pricing_data = model_data.get("pricing", {})

    # Route to appropriate calculator
    if modality == "text->text" or modality.startswith("text") and "text" in modality:
        return calculate_text_model_cost(
            provider=provider,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            pricing_data=pricing_data
        )

    elif "image" in modality:
        return calculate_image_model_cost(
            provider=provider,
            num_images=usage.get("num_images", 1),
            pricing_data=pricing_data,
            image_dimensions=usage.get("dimensions", "1024x1024")
        )

    elif "audio" in modality:
        return calculate_audio_model_cost(
            provider=provider,
            duration=usage.get("duration", 0),
            pricing_data=pricing_data,
            modality=modality,
            unit=usage.get("unit", "minutes")
        )

    else:
        logger.warning(f"Unknown modality: {modality}")
        return {
            "provider": provider,
            "modality": modality,
            "total_cost": 0.0,
            "error": "Unknown modality type"
        }


def get_provider_info(provider: str) -> Dict[str, Any]:
    """
    Get detailed information about a provider's pricing standards

    Args:
        provider: Provider name

    Returns:
        Dictionary with provider information
    """
    standard = get_provider_standard(provider)

    if not standard:
        return {"error": f"Provider {provider} not found"}

    return {
        "provider": provider,
        "name": standard.get("name"),
        "pricing_unit": standard.get("pricing_unit"),
        "api_format": standard.get("api_format"),
        "conversion_factor": standard.get("conversion_factor"),
        "supported_modalities": list(standard.get("supported_modalities", {}).keys()),
        "special_features": standard.get("special_features", {})
    }


def list_all_providers() -> list[Dict[str, Any]]:
    """
    List all providers with their pricing standards

    Returns:
        List of provider information dictionaries
    """
    standards = load_pricing_standards()
    providers = standards.get("providers", {})

    return [
        {
            "provider": provider_key,
            "name": provider_data.get("name"),
            "pricing_unit": provider_data.get("pricing_unit"),
            "supported_modalities": list(provider_data.get("supported_modalities", {}).keys())
        }
        for provider_key, provider_data in providers.items()
    ]


# Example usage and testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Example 1: OpenRouter text model
    print("\n=== OpenRouter GPT-4 ===")
    model = {
        "id": "openai/gpt-4",
        "architecture": {"modality": "text->text"},
        "pricing": {"prompt": "0.00003", "completion": "0.00006"}
    }
    usage = {"prompt_tokens": 100, "completion_tokens": 50}
    cost = calculate_model_cost("openrouter", model, usage)
    print(json.dumps(cost, indent=2))

    # Example 2: DeepInfra model (per 1M tokens)
    print("\n=== DeepInfra Llama ===")
    model = {
        "id": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "architecture": {"modality": "text->text"},
        "pricing": {"prompt": "0.055", "completion": "0.055"}
    }
    usage = {"prompt_tokens": 1000, "completion_tokens": 500}
    cost = calculate_model_cost("deepinfra", model, usage)
    print(json.dumps(cost, indent=2))

    # Example 3: Image model
    print("\n=== SimpliSmart Flux ===")
    model = {
        "id": "simplismart/flux-1.1-pro",
        "architecture": {"modality": "text->image"},
        "pricing": {"image": "0.05", "prompt": "0", "completion": "0"}
    }
    usage = {"num_images": 3}
    cost = calculate_model_cost("simplismart", model, usage)
    print(json.dumps(cost, indent=2))

    # Example 4: List all providers
    print("\n=== All Providers ===")
    providers = list_all_providers()
    for p in providers[:5]:  # Show first 5
        print(f"- {p['name']} ({p['provider']}): {p['pricing_unit']}")
