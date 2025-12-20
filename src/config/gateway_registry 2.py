"""
Gateway Registry Configuration
Centralizes all gateway configurations to reduce code duplication
"""

from typing import TypedDict


class GatewayConfig(TypedDict):
    """Gateway configuration structure"""
    name: str  # Display name
    has_providers_endpoint: bool  # Whether this gateway has a dedicated providers endpoint
    supports_public_catalog: bool  # Whether this gateway publishes a public model catalog


# Centralized gateway registry
GATEWAY_REGISTRY: dict[str, GatewayConfig] = {
    "openrouter": {
        "name": "OpenRouter",
        "has_providers_endpoint": True,
        "supports_public_catalog": True,
    },
    "featherless": {
        "name": "Featherless",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "deepinfra": {
        "name": "DeepInfra",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "chutes": {
        "name": "Chutes",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "groq": {
        "name": "Groq",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "fireworks": {
        "name": "Fireworks",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "together": {
        "name": "Together",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "cerebras": {
        "name": "Cerebras",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "nebius": {
        "name": "Nebius",
        "has_providers_endpoint": False,
        "supports_public_catalog": False,  # No public listing
    },
    "xai": {
        "name": "xAI",
        "has_providers_endpoint": False,
        "supports_public_catalog": False,  # No public listing
    },
    "novita": {
        "name": "Novita",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "hug": {
        "name": "Hugging Face",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "aimo": {
        "name": "AIMO",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "near": {
        "name": "Near",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "fal": {
        "name": "Fal",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "helicone": {
        "name": "Helicone",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "anannas": {
        "name": "Anannas",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "aihubmix": {
        "name": "AiHubMix",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "vercel-ai-gateway": {
        "name": "Vercel AI Gateway",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "alibaba": {
        "name": "Alibaba Cloud",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
    "onerouter": {
        "name": "OneRouter",
        "has_providers_endpoint": False,
        "supports_public_catalog": True,
    },
}


def get_gateway_config(gateway_slug: str) -> GatewayConfig | None:
    """Get configuration for a specific gateway"""
    return GATEWAY_REGISTRY.get(gateway_slug)


def get_all_gateway_slugs() -> list[str]:
    """Get list of all registered gateway slugs"""
    return list(GATEWAY_REGISTRY.keys())


def is_valid_gateway(gateway_slug: str) -> bool:
    """Check if a gateway slug is valid"""
    return gateway_slug in GATEWAY_REGISTRY or gateway_slug == "all"


def get_gateways_with_public_catalogs() -> list[str]:
    """Get list of gateways that publish public model catalogs"""
    return [
        slug for slug, config in GATEWAY_REGISTRY.items()
        if config["supports_public_catalog"]
    ]


def get_gateway_display_name(gateway_slug: str) -> str:
    """Get display name for a gateway"""
    config = get_gateway_config(gateway_slug)
    return config["name"] if config else gateway_slug.title()


def get_comparison_gateways() -> list[str]:
    """
    Get list of gateways suitable for model comparison
    Returns gateways with public catalogs for price/availability comparison
    """
    return [
        slug for slug, config in GATEWAY_REGISTRY.items()
        if config["supports_public_catalog"]
    ]


def get_gateway_note(gateway_slug: str) -> str:
    """
    Get descriptive note for a gateway or combination of gateways

    Args:
        gateway_slug: Either a specific gateway slug or "all"

    Returns:
        Human-readable description of the gateway catalog(s)
    """
    if gateway_slug == "all":
        gateway_names = [config["name"] for config in GATEWAY_REGISTRY.values()]
        return f"Combined {', '.join(gateway_names)} catalogs"

    config = get_gateway_config(gateway_slug)
    if config:
        note = f"{config['name']} catalog"
        if not config["supports_public_catalog"]:
            note += " (no public listing is currently available)"
        return note

    # Fallback for unknown gateways
    return f"{gateway_slug.title()} catalog"


def get_gateway_description_text(include_all: bool = False, include_auto_detect: bool = False) -> str:
    """
    Generate gateway description text dynamically from registry

    Replaces hard-coded DESC_GATEWAY_AUTO_DETECT and DESC_GATEWAY_WITH_ALL constants

    Args:
        include_all: Whether to include 'all' as an option
        include_auto_detect: Whether to include auto-detect message

    Returns:
        Dynamically generated gateway list string for API documentation
    """
    gateway_slugs = sorted(get_all_gateway_slugs())

    # Add huggingface alias
    gateway_list = []
    for slug in gateway_slugs:
        if slug == "hug":
            gateway_list.append(f"'{slug}' (or 'huggingface')")
        else:
            gateway_list.append(f"'{slug}'")

    if include_all:
        gateway_list.insert(0, "'all'")

    description = f"Gateway to use: {', '.join(gateway_list)}"

    if include_auto_detect:
        description += ", or auto-detect if not specified"

    return description
