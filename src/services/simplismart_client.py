"""
Simplismart AI provider client module.

Simplismart provides OpenAI-compatible API endpoints for various LLM models,
image generation (Flux, SDXL), and speech-to-text (Whisper).

API Documentation: https://docs.simplismart.ai/overview
Base URL: https://api.simplismart.live
Pricing: https://simplismart.ai/pricing

FIXED: Simplismart lists pricing per 1M tokens on their website.
Our database stores pricing per 1K tokens, so all prices below have been
converted (divided by 1000) to match our internal format.

Supported LLM models (listed as per 1M tokens, stored as per 1K):
- meta-llama/Meta-Llama-3.1-8B-Instruct ($0.13 per 1M = $0.00013 per 1K)
- meta-llama/Meta-Llama-3.1-70B-Instruct ($0.74 per 1M = $0.00074 per 1K)
- meta-llama/Meta-Llama-3.1-405B-Instruct ($3.00 per 1M = $0.003 per 1K)
- meta-llama/Llama-3.3-70B-Instruct ($0.74 per 1M)
- meta-llama/Llama-4-Maverick-17B-Instruct (preview)
- deepseek-ai/DeepSeek-R1 ($3.90 per 1M)
- deepseek-ai/DeepSeek-V3 ($0.90 per 1M)
- deepseek-ai/DeepSeek-R1-Distill-Llama-70B ($0.74 per 1M)
- deepseek-ai/DeepSeek-R1-Distill-Qwen-32B ($1.08 per 1M)
- google/gemma-3-1b-it ($0.06 per 1M)
- google/gemma-3-4b-it ($0.10 per 1M)
- google/gemma-3-27b-it ($0.30 per 1M)
- microsoft/Phi-3-medium-128k-instruct ($0.08 per 1M)
- microsoft/Phi-3-mini-4k-instruct ($0.08 per 1M)
- Qwen/Qwen2.5-7B-Instruct ($0.30 per 1M)
- Qwen/Qwen2.5-14B-Instruct ($0.30 per 1M)
- Qwen/Qwen2.5-32B-Instruct ($1.08 per 1M)
- Qwen/Qwen2.5-72B-Instruct ($1.08 per 1M)
- Qwen/Qwen3-4B ($0.10 per 1M)
- mistralai/Mixtral-8x7B-Instruct-v0.1-FP8 ($0.30 per 1M)
- mistralai/Devstral-Small-2505 ($0.30 per 1M)

Supported Diffusion models (per 1024x1024 image):
- simplismart/flux-1.1-pro ($0.05)
- simplismart/flux-dev ($0.03)
- simplismart/flux-kontext ($0.04)
- simplismart/flux-1.1-pro-redux ($0.05)
- simplismart/flux-pro-canny ($0.05)
- simplismart/flux-pro-depth ($0.05)
- simplismart/sdxl ($0.28)

Supported Speech-to-Text models (per audio minute):
- simplismart/whisper-large-v2 ($0.0028)
- simplismart/whisper-large-v3 ($0.0030)
- simplismart/whisper-v3-turbo ($0.0018)
"""

import logging

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_simplismart_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)

# Simplismart base URL
SIMPLISMART_BASE_URL = "https://api.simplismart.live"

# Simplismart model catalog - models available via the API
# FIXED: Pricing from https://simplismart.ai/pricing is per 1M tokens
# We store pricing per single token in database, so divide by 1,000,000
SIMPLISMART_MODELS = {
    # Llama 3.1 series
    "meta-llama/Meta-Llama-3.1-8B-Instruct": {
        "name": "Meta Llama 3.1 8B Instruct",
        "context_length": 131072,
        "description": "Meta's Llama 3.1 8B parameter instruction-tuned model",
        "pricing": {
            "prompt": "0.00000013",
            "completion": "0.00000013",
            "request": "0",
            "image": "0",
        },  # 0.13/1M
    },
    "meta-llama/Meta-Llama-3.1-70B-Instruct": {
        "name": "Meta Llama 3.1 70B Instruct",
        "context_length": 131072,
        "description": "Meta's Llama 3.1 70B parameter instruction-tuned model",
        "pricing": {
            "prompt": "0.00000074",
            "completion": "0.00000074",
            "request": "0",
            "image": "0",
        },  # 0.74/1M
    },
    "meta-llama/Meta-Llama-3.1-405B-Instruct": {
        "name": "Meta Llama 3.1 405B Instruct",
        "context_length": 131072,
        "description": "Meta's Llama 3.1 405B parameter instruction-tuned model",
        "pricing": {
            "prompt": "0.000003",
            "completion": "0.000003",
            "request": "0",
            "image": "0",
        },  # 3.00/1M
    },
    # Llama 3.3 series
    "meta-llama/Llama-3.3-70B-Instruct": {
        "name": "Meta Llama 3.3 70B Instruct",
        "context_length": 131072,
        "description": "Meta's Llama 3.3 70B parameter instruction-tuned model",
        "pricing": {
            "prompt": "0.00000074",
            "completion": "0.00000074",
            "request": "0",
            "image": "0",
        },  # 0.74/1M
    },
    # Llama 4 series (preview)
    "meta-llama/Llama-4-Maverick-17B-Instruct": {
        "name": "Meta Llama 4 Maverick 17B Instruct",
        "context_length": 131072,
        "description": "Meta's Llama 4 Maverick 17B parameter instruction-tuned model (preview)",
        "pricing": {
            "prompt": "0.00000074",
            "completion": "0.00000074",
            "request": "0",
            "image": "0",
        },  # 0.74/1M
    },
    # DeepSeek series
    "deepseek-ai/DeepSeek-R1": {
        "name": "DeepSeek R1",
        "context_length": 131072,
        "description": "DeepSeek's R1 reasoning model",
        "pricing": {
            "prompt": "0.0000039",
            "completion": "0.0000039",
            "request": "0",
            "image": "0",
        },  # 3.90/1M
    },
    "deepseek-ai/DeepSeek-V3": {
        "name": "DeepSeek V3",
        "context_length": 131072,
        "description": "DeepSeek's V3 large language model",
        "pricing": {
            "prompt": "0.0000009",
            "completion": "0.0000009",
            "request": "0",
            "image": "0",
        },  # 0.90/1M
    },
    "deepseek-ai/DeepSeek-R1-Distill-Llama-70B": {
        "name": "DeepSeek R1 Distill Llama 70B",
        "context_length": 65536,
        "description": "DeepSeek R1 distilled into Llama 70B architecture",
        "pricing": {
            "prompt": "0.00000074",
            "completion": "0.00000074",
            "request": "0",
            "image": "0",
        },  # 0.74/1M
    },
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B": {
        "name": "DeepSeek R1 Distill Qwen 32B",
        "context_length": 65536,
        "description": "DeepSeek R1 distilled into Qwen 32B architecture",
        "pricing": {
            "prompt": "0.00000108",
            "completion": "0.00000108",
            "request": "0",
            "image": "0",
        },  # 1.08/1M
    },
    # Gemma 3 series
    "google/gemma-3-1b-it": {
        "name": "Google Gemma 3 1B IT",
        "context_length": 8192,
        "description": "Google's Gemma 3 1B instruction-tuned model",
        "pricing": {
            "prompt": "0.00000006",
            "completion": "0.00000006",
            "request": "0",
            "image": "0",
        },  # 0.06/1M
    },
    "google/gemma-3-4b-it": {
        "name": "Google Gemma 3 4B IT",
        "context_length": 8192,
        "description": "Google's Gemma 3 4B instruction-tuned model",
        "pricing": {
            "prompt": "0.0000001",
            "completion": "0.0000001",
            "request": "0",
            "image": "0",
        },  # 0.10/1M
    },
    "google/gemma-3-27b-it": {
        "name": "Google Gemma 3 27B IT",
        "context_length": 8192,
        "description": "Google's Gemma 3 27B instruction-tuned model",
        "pricing": {
            "prompt": "0.0000003",
            "completion": "0.0000003",
            "request": "0",
            "image": "0",
        },  # 0.30/1M
    },
    # Phi-3 series
    "microsoft/Phi-3-medium-128k-instruct": {
        "name": "Microsoft Phi-3 Medium 128K",
        "context_length": 128000,
        "description": "Microsoft's Phi-3 medium model with 128K context",
        "pricing": {
            "prompt": "0.00000008",
            "completion": "0.00000008",
            "request": "0",
            "image": "0",
        },  # 0.08/1M
    },
    "microsoft/Phi-3-mini-4k-instruct": {
        "name": "Microsoft Phi-3 Mini 4K",
        "context_length": 4096,
        "description": "Microsoft's Phi-3 mini model with 4K context",
        "pricing": {
            "prompt": "0.00000008",
            "completion": "0.00000008",
            "request": "0",
            "image": "0",
        },  # 0.08/1M
    },
    # Qwen series
    "Qwen/Qwen2.5-7B-Instruct": {
        "name": "Qwen 2.5 7B Instruct",
        "context_length": 32768,
        "description": "Alibaba's Qwen 2.5 7B instruction-tuned model",
        "pricing": {
            "prompt": "0.0000003",
            "completion": "0.0000003",
            "request": "0",
            "image": "0",
        },  # 0.30/1M
    },
    "Qwen/Qwen2.5-14B-Instruct": {
        "name": "Qwen 2.5 14B Instruct",
        "context_length": 32768,
        "description": "Alibaba's Qwen 2.5 14B instruction-tuned model",
        "pricing": {
            "prompt": "0.0000003",
            "completion": "0.0000003",
            "request": "0",
            "image": "0",
        },  # 0.30/1M
    },
    "Qwen/Qwen2.5-32B-Instruct": {
        "name": "Qwen 2.5 32B Instruct",
        "context_length": 32768,
        "description": "Alibaba's Qwen 2.5 32B instruction-tuned model",
        "pricing": {
            "prompt": "0.00000108",
            "completion": "0.00000108",
            "request": "0",
            "image": "0",
        },  # 1.08/1M
    },
    "Qwen/Qwen2.5-72B-Instruct": {
        "name": "Qwen 2.5 72B Instruct",
        "context_length": 32768,
        "description": "Alibaba's Qwen 2.5 72B instruction-tuned model",
        "pricing": {
            "prompt": "0.00000108",
            "completion": "0.00000108",
            "request": "0",
            "image": "0",
        },  # 1.08/1M
    },
    "Qwen/Qwen3-4B": {
        "name": "Qwen 3 4B",
        "context_length": 32768,
        "description": "Alibaba's Qwen 3 4B model",
        "pricing": {
            "prompt": "0.0000001",
            "completion": "0.0000001",
            "request": "0",
            "image": "0",
        },  # 0.10/1M
    },
    # Mixtral series
    "mistralai/Mixtral-8x7B-Instruct-v0.1-FP8": {
        "name": "Mixtral 8x7B Instruct FP8",
        "context_length": 32768,
        "description": "Mistral's Mixtral 8x7B MoE instruction model (FP8 quantized)",
        "pricing": {
            "prompt": "0.0000003",
            "completion": "0.0000003",
            "request": "0",
            "image": "0",
        },  # 0.30/1M
    },
    # Devstral series
    "mistralai/Devstral-Small-2505": {
        "name": "Devstral Small 2505",
        "context_length": 32768,
        "description": "Mistral's Devstral Small coding assistant model",
        "pricing": {
            "prompt": "0.0000003",
            "completion": "0.0000003",
            "request": "0",
            "image": "0",
        },  # 0.30/1M
    },
    # =====================
    # Diffusion/Image Models
    # =====================
    # Pricing is per 1024x1024 image from https://simplismart.ai/pricing
    "simplismart/flux-1.1-pro": {
        "name": "Flux 1.1 Pro",
        "type": "text-to-image",
        "description": "High-quality Flux 1.1 Pro image generation model",
        "pricing": {
            "prompt": "0",
            "completion": "0",
            "request": "0.05",
            "image": "0.05",
            "pricing_model": "per_image",
        },
    },
    "simplismart/flux-dev": {
        "name": "Flux Dev",
        "type": "text-to-image",
        "description": "Flux Dev image generation model for development",
        "pricing": {
            "prompt": "0",
            "completion": "0",
            "request": "0.03",
            "image": "0.03",
            "pricing_model": "per_image",
        },
    },
    "simplismart/flux-kontext": {
        "name": "Flux Kontext",
        "type": "text-to-image",
        "description": "Flux Kontext context-aware image generation",
        "pricing": {
            "prompt": "0",
            "completion": "0",
            "request": "0.04",
            "image": "0.04",
            "pricing_model": "per_image",
        },
    },
    "simplismart/flux-1.1-pro-redux": {
        "name": "Flux 1.1 Pro Redux",
        "type": "image-to-image",
        "description": "Flux 1.1 Pro Redux for image variations and remixing",
        "pricing": {
            "prompt": "0",
            "completion": "0",
            "request": "0.05",
            "image": "0.05",
            "pricing_model": "per_image",
        },
    },
    "simplismart/flux-pro-canny": {
        "name": "Flux Pro Canny",
        "type": "image-to-image",
        "description": "Flux Pro with Canny edge detection control",
        "pricing": {
            "prompt": "0",
            "completion": "0",
            "request": "0.05",
            "image": "0.05",
            "pricing_model": "per_image",
        },
    },
    "simplismart/flux-pro-depth": {
        "name": "Flux Pro Depth",
        "type": "image-to-image",
        "description": "Flux Pro with depth map control",
        "pricing": {
            "prompt": "0",
            "completion": "0",
            "request": "0.05",
            "image": "0.05",
            "pricing_model": "per_image",
        },
    },
    "simplismart/sdxl": {
        "name": "Stable Diffusion XL",
        "type": "text-to-image",
        "description": "Stable Diffusion XL image generation model",
        "pricing": {
            "prompt": "0",
            "completion": "0",
            "request": "0.28",
            "image": "0.28",
            "pricing_model": "per_image",
        },
    },
    # =====================
    # Speech-to-Text Models
    # =====================
    # Pricing is per audio minute from https://simplismart.ai/pricing
    "simplismart/whisper-large-v2": {
        "name": "Whisper Large v2",
        "type": "speech-to-text",
        "description": "OpenAI Whisper Large v2 for speech transcription",
        "pricing": {
            "prompt": "0",
            "completion": "0",
            "request": "0.0028",
            "image": "0",
            "pricing_model": "per_minute",
        },
    },
    "simplismart/whisper-large-v3": {
        "name": "Whisper Large v3",
        "type": "speech-to-text",
        "description": "OpenAI Whisper Large v3 for speech transcription",
        "pricing": {
            "prompt": "0",
            "completion": "0",
            "request": "0.0030",
            "image": "0",
            "pricing_model": "per_minute",
        },
    },
    "simplismart/whisper-v3-turbo": {
        "name": "Whisper v3 Turbo",
        "type": "speech-to-text",
        "description": "Fast OpenAI Whisper v3 Turbo for speech transcription",
        "pricing": {
            "prompt": "0",
            "completion": "0",
            "request": "0.0018",
            "image": "0",
            "pricing_model": "per_minute",
        },
    },
}

# Model ID aliases for user convenience
SIMPLISMART_MODEL_ALIASES = {
    # Llama 3.1 aliases
    "llama-3.1-8b": "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "llama-3.1-8b-instruct": "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "meta-llama-3.1-8b": "meta-llama/Meta-Llama-3.1-8B-Instruct",
    "llama-3.1-70b": "meta-llama/Meta-Llama-3.1-70B-Instruct",
    "llama-3.1-70b-instruct": "meta-llama/Meta-Llama-3.1-70B-Instruct",
    "meta-llama-3.1-70b": "meta-llama/Meta-Llama-3.1-70B-Instruct",
    "llama-3.1-405b": "meta-llama/Meta-Llama-3.1-405B-Instruct",
    "llama-3.1-405b-instruct": "meta-llama/Meta-Llama-3.1-405B-Instruct",
    "meta-llama-3.1-405b": "meta-llama/Meta-Llama-3.1-405B-Instruct",
    # Llama 3.3 aliases
    "llama-3.3-70b": "meta-llama/Llama-3.3-70B-Instruct",
    "llama-3.3-70b-instruct": "meta-llama/Llama-3.3-70B-Instruct",
    "meta-llama-3.3-70b": "meta-llama/Llama-3.3-70B-Instruct",
    # Llama 4 aliases
    "llama-4-maverick": "meta-llama/Llama-4-Maverick-17B-Instruct",
    "llama-4-maverick-17b": "meta-llama/Llama-4-Maverick-17B-Instruct",
    # DeepSeek aliases
    "deepseek-r1": "deepseek-ai/DeepSeek-R1",
    "deepseek-v3": "deepseek-ai/DeepSeek-V3",
    "deepseek-r1-distill-llama-70b": "deepseek-ai/DeepSeek-R1-Distill-Llama-70B",
    "deepseek-r1-distill-qwen-32b": "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
    # Gemma aliases
    "gemma-3-1b": "google/gemma-3-1b-it",
    "gemma-3-4b": "google/gemma-3-4b-it",
    "gemma-3-27b": "google/gemma-3-27b-it",
    # Phi-3 aliases
    "phi-3-medium": "microsoft/Phi-3-medium-128k-instruct",
    "phi-3-medium-128k": "microsoft/Phi-3-medium-128k-instruct",
    "phi-3-mini": "microsoft/Phi-3-mini-4k-instruct",
    "phi-3-mini-4k": "microsoft/Phi-3-mini-4k-instruct",
    # Qwen aliases
    "qwen-2.5-7b": "Qwen/Qwen2.5-7B-Instruct",
    "qwen2.5-7b": "Qwen/Qwen2.5-7B-Instruct",
    "qwen-2.5-14b": "Qwen/Qwen2.5-14B-Instruct",
    "qwen2.5-14b": "Qwen/Qwen2.5-14B-Instruct",
    "qwen-2.5-32b": "Qwen/Qwen2.5-32B-Instruct",
    "qwen2.5-32b": "Qwen/Qwen2.5-32B-Instruct",
    "qwen-2.5-72b": "Qwen/Qwen2.5-72B-Instruct",
    "qwen2.5-72b": "Qwen/Qwen2.5-72B-Instruct",
    "qwen3-4b": "Qwen/Qwen3-4B",
    "qwen-3-4b": "Qwen/Qwen3-4B",
    # Mixtral aliases
    "mixtral-8x7b": "mistralai/Mixtral-8x7B-Instruct-v0.1-FP8",
    "mixtral-8x7b-instruct": "mistralai/Mixtral-8x7B-Instruct-v0.1-FP8",
    # Devstral aliases
    "devstral-small": "mistralai/Devstral-Small-2505",
    # Flux image model aliases
    "flux-1.1-pro": "simplismart/flux-1.1-pro",
    "flux-pro": "simplismart/flux-1.1-pro",
    "flux-dev": "simplismart/flux-dev",
    "flux-kontext": "simplismart/flux-kontext",
    "flux-1.1-pro-redux": "simplismart/flux-1.1-pro-redux",
    "flux-pro-redux": "simplismart/flux-1.1-pro-redux",
    "flux-pro-canny": "simplismart/flux-pro-canny",
    "flux-canny": "simplismart/flux-pro-canny",
    "flux-pro-depth": "simplismart/flux-pro-depth",
    "flux-depth": "simplismart/flux-pro-depth",
    "sdxl": "simplismart/sdxl",
    "stable-diffusion-xl": "simplismart/sdxl",
    # Whisper speech-to-text aliases
    "whisper-large-v2": "simplismart/whisper-large-v2",
    "whisper-v2": "simplismart/whisper-large-v2",
    "whisper-large-v3": "simplismart/whisper-large-v3",
    "whisper-v3": "simplismart/whisper-large-v3",
    "whisper-v3-turbo": "simplismart/whisper-v3-turbo",
    "whisper-turbo": "simplismart/whisper-v3-turbo",
}


def get_simplismart_client():
    """Get Simplismart client with connection pooling for better performance.

    Simplismart provides OpenAI-compatible API endpoints for various models.
    """
    try:
        if not Config.SIMPLISMART_API_KEY:
            raise ValueError("Simplismart API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_simplismart_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Simplismart client: {e}")
        raise


def resolve_simplismart_model(model_id: str) -> str:
    """Resolve model ID to Simplismart-specific format.

    Args:
        model_id: Input model ID (can be alias or full name)

    Returns:
        Simplismart-compatible model ID
    """
    # Check aliases first (case-insensitive)
    lower_model = model_id.lower()
    if lower_model in SIMPLISMART_MODEL_ALIASES:
        resolved = SIMPLISMART_MODEL_ALIASES[lower_model]
        logger.debug(f"Resolved Simplismart model alias '{model_id}' -> '{resolved}'")
        return resolved

    # Check if model exists in catalog
    if model_id in SIMPLISMART_MODELS:
        return model_id

    # Try case-insensitive match against catalog
    for catalog_model in SIMPLISMART_MODELS:
        if catalog_model.lower() == lower_model:
            return catalog_model

    # Return as-is and let Simplismart handle validation
    logger.debug(f"Using model ID as-is for Simplismart: {model_id}")
    return model_id


def make_simplismart_request_openai(messages, model, **kwargs):
    """Make request to Simplismart using OpenAI client.

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_simplismart_client()
        resolved_model = resolve_simplismart_model(model)
        response = client.chat.completions.create(model=resolved_model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Simplismart request failed: {e}")
        raise


def make_simplismart_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Simplismart using OpenAI client.

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_simplismart_client()
        resolved_model = resolve_simplismart_model(model)
        stream = client.chat.completions.create(
            model=resolved_model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Simplismart streaming request failed: {e}")
        raise


def process_simplismart_response(response):
    """Process Simplismart response to extract relevant data.

    Args:
        response: OpenAI-format response object

    Returns:
        Normalized response dictionary
    """
    try:
        choices = []
        for choice in response.choices:
            msg = extract_message_with_tools(choice.message)

            choices.append(
                {
                    "index": choice.index,
                    "message": msg,
                    "finish_reason": choice.finish_reason,
                }
            )

        return {
            "id": response.id,
            "object": response.object,
            "created": response.created,
            "model": response.model,
            "choices": choices,
            "usage": (
                {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }
                if response.usage
                else {}
            ),
        }
    except Exception as e:
        logger.error(f"Failed to process Simplismart response: {e}")
        raise


def fetch_models_from_simplismart():
    """Fetch available models from Simplismart.

    Returns a list of model info dictionaries in catalog format.
    Includes pricing data from https://simplismart.ai/pricing

    Supports LLM, text-to-image, image-to-image, and speech-to-text models.
    """
    try:
        models = []
        for model_id, model_info in SIMPLISMART_MODELS.items():
            model_data = {
                "id": model_id,
                "name": model_info["name"],
                "description": model_info.get("description", ""),
                "provider": "simplismart",
                "provider_name": "Simplismart",
                "provider_slug": "simplismart",
                "source_gateway": "simplismart",
            }
            # Include model type if available (text-to-image, speech-to-text, etc.)
            if "type" in model_info:
                model_data["type"] = model_info["type"]
            # Include context_length for LLM models
            if "context_length" in model_info:
                model_data["context_length"] = model_info["context_length"]
            # Include pricing if available
            if "pricing" in model_info:
                model_data["pricing"] = model_info["pricing"]
            models.append(model_data)
        logger.info(f"Fetched {len(models)} models from Simplismart")
        return models
    except Exception as e:
        logger.error(f"Failed to fetch models from Simplismart: {e}")
        return []


def is_simplismart_model(model_id: str) -> bool:
    """Check if a model ID is available on Simplismart.

    Args:
        model_id: The model ID to check

    Returns:
        True if model is available on Simplismart
    """
    lower_model = model_id.lower()

    # Check aliases
    if lower_model in SIMPLISMART_MODEL_ALIASES:
        return True

    # Check catalog directly
    if model_id in SIMPLISMART_MODELS:
        return True

    # Case-insensitive catalog check
    for catalog_model in SIMPLISMART_MODELS:
        if catalog_model.lower() == lower_model:
            return True

    return False
