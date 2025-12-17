"""
Provider Configuration

This file defines all LLM provider configurations in one place.
To add a new provider, simply add an entry to PROVIDER_CONFIGS below.

Benefits:
- Single source of truth for provider settings
- Easy to see all providers at a glance
- Simple to add/remove providers
- Configuration-driven approach
"""

from typing import Dict, Any

# Provider configurations
# Format: provider_name: {timeout, priority, auto_detect}
PROVIDER_CONFIGS: Dict[str, Dict[str, Any]] = {
    "openrouter": {
        "timeout": 30,
        "priority": 1,
        "auto_detect": True,
        "module_name": "openrouter",
        "supports_async_streaming": True,
    },
    "onerouter": {
        "timeout": 30,
        "priority": 2,
        "auto_detect": True,
        "module_name": "onerouter",
    },
    "huggingface": {
        "timeout": 120,  # Extended timeout for large models
        "priority": 3,
        "auto_detect": True,
        "module_name": "huggingface",
    },
    "featherless": {
        "timeout": 30,
        "priority": 4,
        "auto_detect": True,
        "module_name": "featherless",
    },
    "fireworks": {
        "timeout": 30,
        "priority": 5,
        "auto_detect": True,
        "module_name": "fireworks",
    },
    "together": {
        "timeout": 30,
        "priority": 6,
        "auto_detect": True,
        "module_name": "together",
    },
    "google-vertex": {
        "timeout": 30,
        "priority": 7,
        "auto_detect": True,
        "module_name": "google_vertex",
    },
    "aimo": {
        "timeout": 30,
        "priority": 8,
        "auto_detect": False,
        "module_name": "aimo",
    },
    "xai": {
        "timeout": 30,
        "priority": 9,
        "auto_detect": False,
        "module_name": "xai",
    },
    "cerebras": {
        "timeout": 30,
        "priority": 10,
        "auto_detect": False,
        "module_name": "cerebras",
    },
    "chutes": {
        "timeout": 30,
        "priority": 11,
        "auto_detect": False,
        "module_name": "chutes",
    },
    "near": {
        "timeout": 120,  # Large models like Qwen3-30B need extended timeout
        "priority": 12,
        "auto_detect": False,
        "module_name": "near",
    },
    "vercel-ai-gateway": {
        "timeout": 30,
        "priority": 13,
        "auto_detect": False,
        "module_name": "vercel_ai_gateway",
    },
    "helicone": {
        "timeout": 30,
        "priority": 14,
        "auto_detect": False,
        "module_name": "helicone",
    },
    "aihubmix": {
        "timeout": 30,
        "priority": 15,
        "auto_detect": False,
        "module_name": "aihubmix",
    },
    "anannas": {
        "timeout": 30,
        "priority": 16,
        "auto_detect": False,
        "module_name": "anannas",
    },
    "alpaca-network": {
        "timeout": 30,
        "priority": 17,
        "auto_detect": False,
        "module_name": "alpaca_network",
    },
    "alibaba-cloud": {
        "timeout": 30,
        "priority": 18,
        "auto_detect": False,
        "module_name": "alibaba_cloud",
    },
    "clarifai": {
        "timeout": 30,
        "priority": 19,
        "auto_detect": False,
        "module_name": "clarifai",
    },
    "groq": {
        "timeout": 30,
        "priority": 20,
        "auto_detect": False,
        "module_name": "groq",
    },
    "cloudflare-workers-ai": {
        "timeout": 30,
        "priority": 21,
        "auto_detect": False,
        "module_name": "cloudflare_workers_ai",
    },
    "morpheus": {
        "timeout": 30,
        "priority": 22,
        "auto_detect": False,
        "module_name": "morpheus",
    },
}

# Provider aliases (for normalization)
PROVIDER_ALIASES = {
    "hug": "huggingface",
}

# Default provider if none specified
DEFAULT_PROVIDER = "openrouter"

# Default timeout if provider not found
DEFAULT_TIMEOUT = 30

# Auto-detection order (providers to try for auto-detection)
# Built from PROVIDER_CONFIGS based on priority and auto_detect flag
AUTO_DETECT_PROVIDERS = sorted(
    [name for name, config in PROVIDER_CONFIGS.items() if config.get("auto_detect", False)],
    key=lambda name: PROVIDER_CONFIGS[name]["priority"],
)


def get_provider_timeout(provider_name: str) -> int:
    """Get timeout for a provider"""
    config = PROVIDER_CONFIGS.get(provider_name, {})
    return config.get("timeout", DEFAULT_TIMEOUT)


def normalize_provider_name(provider_name: str) -> str:
    """Normalize provider name using aliases"""
    return PROVIDER_ALIASES.get(provider_name, provider_name)


def get_module_name(provider_name: str) -> str:
    """Get Python module name for a provider"""
    config = PROVIDER_CONFIGS.get(provider_name, {})
    return config.get("module_name", provider_name.replace("-", "_"))
