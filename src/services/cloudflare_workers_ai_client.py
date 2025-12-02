"""
Cloudflare Workers AI client for chat completions.

Cloudflare Workers AI provides an OpenAI-compatible API endpoint for running
AI models on Cloudflare's global network.

API Documentation:
- https://developers.cloudflare.com/workers-ai/
- https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/
- https://developers.cloudflare.com/api/resources/ai/subresources/models/methods/list/

OpenAI-compatible endpoint:
- Base URL: https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1
- Authentication: Bearer token via Authorization header

Model listing endpoint:
- GET /accounts/{account_id}/ai/models/search
- Used to dynamically fetch available models

Model naming convention:
- Cloudflare models use @cf/ prefix (e.g., @cf/meta/llama-3.1-8b-instruct)
"""

import logging
from typing import Any

import httpx

from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_cloudflare_workers_ai_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)


def get_cloudflare_workers_ai_client():
    """Get Cloudflare Workers AI client with connection pooling for better performance

    Cloudflare Workers AI provides OpenAI-compatible API endpoints for various models.
    Requires both an API token and an account ID.
    """
    try:
        if not Config.CLOUDFLARE_API_TOKEN:
            raise ValueError("Cloudflare API token not configured")
        if not Config.CLOUDFLARE_ACCOUNT_ID:
            raise ValueError("Cloudflare Account ID not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_cloudflare_workers_ai_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Cloudflare Workers AI client: {e}")
        raise


def make_cloudflare_workers_ai_request_openai(messages, model, **kwargs):
    """Make request to Cloudflare Workers AI using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use (e.g., @cf/meta/llama-3.1-8b-instruct)
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_cloudflare_workers_ai_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Cloudflare Workers AI request failed: {e}")
        raise


def make_cloudflare_workers_ai_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Cloudflare Workers AI using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use (e.g., @cf/meta/llama-3.1-8b-instruct)
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_cloudflare_workers_ai_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Cloudflare Workers AI streaming request failed: {e}")
        raise


def process_cloudflare_workers_ai_response(response):
    """Process Cloudflare Workers AI response to extract relevant data"""
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
        logger.error(f"Failed to process Cloudflare Workers AI response: {e}")
        raise


# Default models catalog for Cloudflare Workers AI
# These are the text generation models available via OpenAI-compatible API
# Full list: https://developers.cloudflare.com/workers-ai/models/
DEFAULT_CLOUDFLARE_WORKERS_AI_MODELS = [
    # ==========================================================================
    # OpenAI GPT-OSS Models (Latest Flagship)
    # ==========================================================================
    {
        "id": "@cf/openai/gpt-oss-120b",
        "name": "GPT-OSS 120B",
        "description": "OpenAI's GPT-OSS 120B - for production, general purpose, high reasoning use-cases",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/openai/gpt-oss-20b",
        "name": "GPT-OSS 20B",
        "description": "OpenAI's GPT-OSS 20B - for lower latency and specialized use-cases",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # Meta Llama 4 Models
    # ==========================================================================
    {
        "id": "@cf/meta/llama-4-scout-17b-16e-instruct",
        "name": "Llama 4 Scout 17B",
        "description": "Meta's Llama 4 Scout with mixture-of-experts, function calling support",
        "context_length": 131072,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # Meta Llama 3.3 Models
    # ==========================================================================
    {
        "id": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "name": "Llama 3.3 70B Instruct FP8 Fast",
        "description": "Quantized Llama 3.3 70B optimized for speed, supports function calling",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # Meta Llama 3.2 Models
    # ==========================================================================
    {
        "id": "@cf/meta/llama-3.2-11b-vision-instruct",
        "name": "Llama 3.2 11B Vision Instruct",
        "description": "Meta's Llama 3.2 with vision capabilities for image reasoning and captioning",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/meta/llama-3.2-3b-instruct",
        "name": "Llama 3.2 3B Instruct",
        "description": "Meta's Llama 3.2 3B instruction-tuned for multilingual dialogue",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/meta/llama-3.2-1b-instruct",
        "name": "Llama 3.2 1B Instruct",
        "description": "Meta's Llama 3.2 1B lightweight instruction-tuned model",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # Meta Llama 3.1 Models
    # ==========================================================================
    {
        "id": "@cf/meta/llama-3.1-70b-instruct",
        "name": "Llama 3.1 70B Instruct",
        "description": "Meta's Llama 3.1 70B with multilingual dialogue optimization",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/meta/llama-3.1-8b-instruct-fast",
        "name": "Llama 3.1 8B Instruct Fast",
        "description": "Fast variant of Llama 3.1 8B Instruct optimized for speed",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/meta/llama-3.1-8b-instruct",
        "name": "Llama 3.1 8B Instruct",
        "description": "Meta's Llama 3.1 8B with multilingual dialogue optimization",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/meta/llama-3.1-8b-instruct-fp8",
        "name": "Llama 3.1 8B Instruct FP8",
        "description": "Meta's Llama 3.1 8B quantized to FP8 for efficiency",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/meta/llama-3.1-8b-instruct-awq",
        "name": "Llama 3.1 8B Instruct AWQ",
        "description": "Meta's Llama 3.1 8B with AWQ quantization",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # Meta Llama 3 Models
    # ==========================================================================
    {
        "id": "@cf/meta/meta-llama-3-8b-instruct",
        "name": "Meta Llama 3 8B Instruct",
        "description": "Meta's Llama 3 8B instruction-tuned model",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/meta/llama-3-8b-instruct",
        "name": "Llama 3 8B Instruct",
        "description": "Meta's Llama 3 8B instruction-tuned model",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/meta/llama-3-8b-instruct-awq",
        "name": "Llama 3 8B Instruct AWQ",
        "description": "Meta's Llama 3 8B with AWQ quantization",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # Meta Llama 2 Models (Legacy)
    # ==========================================================================
    {
        "id": "@cf/meta/llama-2-7b-chat-fp16",
        "name": "Llama 2 7B Chat FP16",
        "description": "Meta's Llama 2 7B chat model in FP16 precision",
        "context_length": 4096,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/meta/llama-2-7b-chat-int8",
        "name": "Llama 2 7B Chat INT8",
        "description": "Meta's Llama 2 7B chat model quantized to INT8",
        "context_length": 4096,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/meta-llama/llama-2-7b-chat-hf-lora",
        "name": "Llama 2 7B Chat HF LoRA",
        "description": "Meta's Llama 2 7B with LoRA fine-tuning support",
        "context_length": 4096,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # Meta Llama Guard (Safety)
    # ==========================================================================
    {
        "id": "@cf/meta/llama-guard-3-8b",
        "name": "Llama Guard 3 8B",
        "description": "Meta's safety classifier for content moderation",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # Qwen Models
    # ==========================================================================
    {
        "id": "@cf/qwen/qwen3-30b-a3b-fp8",
        "name": "Qwen3 30B A3B FP8",
        "description": "Alibaba Qwen3 with reasoning, agent capabilities, multilingual support",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/qwen/qwq-32b",
        "name": "QwQ 32B",
        "description": "Qwen's specialized reasoning model with LoRA support",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/qwen/qwen2.5-coder-32b-instruct",
        "name": "Qwen2.5 Coder 32B Instruct",
        "description": "Qwen's code-specific LLM with LoRA support",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # Google Gemma Models
    # ==========================================================================
    {
        "id": "@cf/google/gemma-3-12b-it",
        "name": "Gemma 3 12B IT",
        "description": "Google's Gemma 3 - multimodal text/image, 128K context, 140+ languages",
        "context_length": 131072,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/google/gemma-7b-it",
        "name": "Gemma 7B IT",
        "description": "Google's Gemma 7B instruction-tuned model",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/google/gemma-7b-it-lora",
        "name": "Gemma 7B IT LoRA",
        "description": "Google's Gemma 7B with LoRA fine-tuning support",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/google/gemma-2b-it-lora",
        "name": "Gemma 2B IT LoRA",
        "description": "Google's Gemma 2B with LoRA fine-tuning support",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # Mistral Models
    # ==========================================================================
    {
        "id": "@cf/mistral/mistral-small-3.1-24b-instruct",
        "name": "Mistral Small 3.1 24B Instruct",
        "description": "MistralAI's model with vision understanding, 128k context, function calling",
        "context_length": 128000,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/mistralai/mistral-7b-instruct-v0.2",
        "name": "Mistral 7B Instruct v0.2",
        "description": "MistralAI's Mistral 7B instruction-tuned v0.2",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/mistralai/mistral-7b-instruct-v0.2-lora",
        "name": "Mistral 7B Instruct v0.2 LoRA",
        "description": "MistralAI's Mistral 7B v0.2 with LoRA support",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/mistralai/mistral-7b-instruct-v0.1",
        "name": "Mistral 7B Instruct v0.1",
        "description": "MistralAI's Mistral 7B instruction-tuned v0.1",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # DeepSeek Models
    # ==========================================================================
    {
        "id": "@cf/deepseek/deepseek-r1-distill-qwen-32b",
        "name": "DeepSeek R1 Distill Qwen 32B",
        "description": "DeepSeek R1 reasoning model distilled to Qwen 32B",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # IBM Granite Models
    # ==========================================================================
    {
        "id": "@cf/ibm/granite-4.0-h-micro",
        "name": "IBM Granite 4.0 H Micro",
        "description": "IBM's Granite 4.0 micro model for efficient inference",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # AI Singapore Models
    # ==========================================================================
    {
        "id": "@cf/aisingapore/gemma-sea-lion-v4-27b-it",
        "name": "Gemma SEA-LION v4 27B IT",
        "description": "AI Singapore's SEA-LION multilingual model based on Gemma",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # NousResearch Models
    # ==========================================================================
    {
        "id": "@cf/nousresearch/hermes-2-pro-mistral-7b",
        "name": "Hermes 2 Pro Mistral 7B",
        "description": "NousResearch's Hermes 2 Pro based on Mistral 7B",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # ==========================================================================
    # Microsoft Models
    # ==========================================================================
    {
        "id": "@cf/microsoft/phi-2",
        "name": "Microsoft Phi-2",
        "description": "Microsoft's Phi-2 small language model",
        "context_length": 2048,
        "provider": "cloudflare-workers-ai",
    },
]


async def fetch_models_from_cloudflare_api() -> list[dict[str, Any]]:
    """
    Fetch available models from Cloudflare Workers AI API.

    Uses the /accounts/{account_id}/ai/models/search endpoint to get
    the list of available models.

    API Documentation:
    https://developers.cloudflare.com/api/resources/ai/subresources/models/methods/list/

    Returns:
        List of model dictionaries with id, name, description, context_length, provider
    """
    if not Config.CLOUDFLARE_API_TOKEN or not Config.CLOUDFLARE_ACCOUNT_ID:
        logger.warning("Cloudflare credentials not configured, returning empty list from API")
        return []

    base_url = f"https://api.cloudflare.com/client/v4/accounts/{Config.CLOUDFLARE_ACCOUNT_ID}"
    headers = {
        "Authorization": f"Bearer {Config.CLOUDFLARE_API_TOKEN}",
        "Content-Type": "application/json",
    }

    models = []
    page = 1
    per_page = 100  # Max results per page

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            while True:
                url = f"{base_url}/ai/models/search"
                params = {
                    "page": page,
                    "per_page": per_page,
                    "task": "Text Generation",  # Filter for text generation models
                }

                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()

                data = response.json()

                if not data.get("success"):
                    logger.error(f"Cloudflare API returned error: {data.get('errors')}")
                    break

                result = data.get("result", [])
                if not result:
                    break

                for model in result:
                    # Convert Cloudflare model format to our standard format
                    model_id = model.get("name", "")
                    if not model_id:
                        continue

                    # Use (x or {}) pattern to handle both missing keys and explicit None values
                    properties = model.get("properties") or {}
                    task_info = model.get("task") or {}

                    models.append({
                        "id": model_id,
                        "name": model.get("description") or model_id.split("/")[-1],
                        "description": model.get("description") or "",
                        "context_length": properties.get("max_total_tokens") or 8192,
                        "provider": "cloudflare-workers-ai",
                        "task": task_info.get("name") or "Text Generation",
                    })

                # Check if there are more pages
                if len(result) < per_page:
                    break

                page += 1

        logger.info(f"Fetched {len(models)} models from Cloudflare Workers AI API")
        return models

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching Cloudflare models: {e.response.status_code} - {e}")
        return []
    except httpx.RequestError as e:
        logger.error(f"Request error fetching Cloudflare models: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching Cloudflare models: {e}")
        return []


def fetch_models_from_cloudflare_workers_ai() -> list[dict[str, Any]]:
    """
    Return the list of available Cloudflare Workers AI models.

    Returns a static curated list of text generation models. For production use,
    use fetch_models_from_cloudflare_api() to get the dynamic list.

    The static list is maintained as a fallback and for environments where
    API credentials are not available.
    """
    return DEFAULT_CLOUDFLARE_WORKERS_AI_MODELS


async def fetch_models_from_cloudflare_workers_ai_async() -> list[dict[str, Any]]:
    """
    Async version that attempts to fetch from API, falling back to static list.

    Tries to fetch models dynamically from Cloudflare API. If that fails
    (due to missing credentials, network issues, etc.), falls back to the
    curated static model list.

    Returns:
        List of model dictionaries with id, name, description, context_length, provider
    """
    # Try dynamic fetch first
    api_models = await fetch_models_from_cloudflare_api()

    if api_models:
        # Filter to only include text generation models
        text_models = [m for m in api_models if "text" in m.get("task", "").lower()]
        if text_models:
            logger.info(f"Using {len(text_models)} models from Cloudflare API")
            return text_models

    # Fall back to static list
    logger.info("Falling back to static Cloudflare Workers AI model list")
    return DEFAULT_CLOUDFLARE_WORKERS_AI_MODELS
