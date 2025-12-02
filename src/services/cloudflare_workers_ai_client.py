"""
Cloudflare Workers AI client for chat completions.

Cloudflare Workers AI provides an OpenAI-compatible API endpoint for running
AI models on Cloudflare's global network.

API Documentation:
- https://developers.cloudflare.com/workers-ai/
- https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/

OpenAI-compatible endpoint:
- Base URL: https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1
- Authentication: Bearer token via Authorization header

Model naming convention:
- Cloudflare models use @cf/ prefix (e.g., @cf/meta/llama-3.1-8b-instruct)
"""

import logging

from openai import OpenAI

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
DEFAULT_CLOUDFLARE_WORKERS_AI_MODELS = [
    # Text Generation - Latest flagship models
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
        "description": "OpenAI's GPT-OSS 20B - for lower latency, and local or specialized use-cases",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # Meta Llama models
    {
        "id": "@cf/meta/llama-4-scout-17b-16e-instruct",
        "name": "Llama 4 Scout 17B",
        "description": "Meta's Llama 4 Scout with mixture-of-experts architecture",
        "context_length": 131072,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "name": "Llama 3.3 70B Instruct FP8",
        "description": "Quantized Llama 3.3 70B optimized for speed",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/meta/llama-3.1-8b-instruct-fast",
        "name": "Llama 3.1 8B Instruct Fast",
        "description": "Fast variant of Llama 3.1 8B Instruct",
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
        "id": "@cf/meta/llama-3.2-3b-instruct",
        "name": "Llama 3.2 3B Instruct",
        "description": "Meta's Llama 3.2 3B instruction-tuned model",
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
    {
        "id": "@cf/meta/llama-3.2-11b-vision-instruct",
        "name": "Llama 3.2 11B Vision Instruct",
        "description": "Meta's Llama 3.2 with vision capabilities",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # Qwen models
    {
        "id": "@cf/qwen/qwen3-30b-a3b-fp8",
        "name": "Qwen3 30B A3B FP8",
        "description": "Qwen3 with reasoning and agent capabilities",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/qwen/qwq-32b",
        "name": "QwQ 32B",
        "description": "Qwen's specialized reasoning model",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    {
        "id": "@cf/qwen/qwen2.5-coder-32b-instruct",
        "name": "Qwen2.5 Coder 32B Instruct",
        "description": "Qwen's code-specific LLM",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # Google Gemma models
    {
        "id": "@cf/google/gemma-3-12b-it",
        "name": "Gemma 3 12B IT",
        "description": "Google's Gemma 3 - multimodal, supports 140+ languages",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
    # Mistral models
    {
        "id": "@cf/mistral/mistral-small-3.1-24b-instruct",
        "name": "Mistral Small 3.1 24B Instruct",
        "description": "MistralAI's model with vision understanding, 128k context",
        "context_length": 128000,
        "provider": "cloudflare-workers-ai",
    },
    # DeepSeek models
    {
        "id": "@cf/deepseek/deepseek-r1-distill-qwen-32b",
        "name": "DeepSeek R1 Distill Qwen 32B",
        "description": "DeepSeek R1 distilled to Qwen 32B",
        "context_length": 8192,
        "provider": "cloudflare-workers-ai",
    },
]


def fetch_models_from_cloudflare_workers_ai():
    """
    Return the list of available Cloudflare Workers AI models.

    Currently returns a static list of models. In the future, this could
    be extended to fetch from Cloudflare's API if they provide a models endpoint.
    """
    return DEFAULT_CLOUDFLARE_WORKERS_AI_MODELS
