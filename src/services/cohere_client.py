"""
Cohere API Client
Fetches models from Cohere API for model catalog synchronization
"""

import logging

logger = logging.getLogger(__name__)


def fetch_models_from_cohere():
    """
    Fetch models from Cohere API

    Note: Cohere doesn't have a public models API endpoint, so we'll define
    their known models manually based on official documentation.

    Reference: https://docs.cohere.com/docs/models
    """
    try:
        # Cohere's known production models
        # These are manually maintained based on official docs
        models = [
            {
                "id": "cohere/command-r-plus",
                "name": "Command R+",
                "provider": "Cohere",
                "source_gateway": "openrouter",
                "provider_slug": "cohere",
                "context_length": 128000,
                "max_output_tokens": 4096,
                "supports_streaming": True,
                "supports_function_calling": True,
                "pricing": {
                    "prompt": "0.0000025",  # $2.50 per 1M tokens
                    "completion": "0.00001",  # $10.00 per 1M tokens
                },
                "description": "Cohere's most powerful model with enhanced RAG capabilities",
            },
            {
                "id": "cohere/command-r",
                "name": "Command R",
                "provider": "Cohere",
                "source_gateway": "openrouter",
                "provider_slug": "cohere",
                "context_length": 128000,
                "max_output_tokens": 4096,
                "supports_streaming": True,
                "supports_function_calling": True,
                "pricing": {
                    "prompt": "0.0000005",  # $0.50 per 1M tokens
                    "completion": "0.0000015",  # $1.50 per 1M tokens
                },
                "description": "Optimized for conversational AI and RAG",
            },
            {
                "id": "cohere/command-r-plus-08-2024",
                "name": "Command R+ (08-2024)",
                "provider": "Cohere",
                "source_gateway": "openrouter",
                "provider_slug": "cohere",
                "context_length": 128000,
                "max_output_tokens": 4096,
                "supports_streaming": True,
                "supports_function_calling": True,
                "pricing": {
                    "prompt": "0.0000025",
                    "completion": "0.00001",
                },
                "description": "August 2024 version of Command R+",
            },
            {
                "id": "cohere/command",
                "name": "Command",
                "provider": "Cohere",
                "source_gateway": "openrouter",
                "provider_slug": "cohere",
                "context_length": 4096,
                "max_output_tokens": 4096,
                "supports_streaming": True,
                "supports_function_calling": False,
                "pricing": {
                    "prompt": "0.000001",  # $1.00 per 1M tokens
                    "completion": "0.000002",  # $2.00 per 1M tokens
                },
                "description": "Cohere's foundational command model",
            },
            {
                "id": "cohere/command-light",
                "name": "Command Light",
                "provider": "Cohere",
                "source_gateway": "openrouter",
                "provider_slug": "cohere",
                "context_length": 4096,
                "max_output_tokens": 4096,
                "supports_streaming": True,
                "supports_function_calling": False,
                "pricing": {
                    "prompt": "0.0000003",  # $0.30 per 1M tokens
                    "completion": "0.0000006",  # $0.60 per 1M tokens
                },
                "description": "Faster, lighter version of Command",
            },
        ]

        logger.info(f"Loaded {len(models)} Cohere models from static configuration")
        return models

    except Exception as e:
        logger.error(f"Error fetching Cohere models: {e}")
        return []


def fetch_cohere_model_info(model_id: str) -> dict | None:
    """
    Get information about a specific Cohere model

    Args:
        model_id: The model ID (e.g., "cohere/command-r-plus")

    Returns:
        Model information dict or None if not found
    """
    models = fetch_models_from_cohere()

    for model in models:
        if model.get("id") == model_id:
            return model

    return None
