import logging

from fastapi import APIRouter
from openai import OpenAI

from src.config import Config

# Initialize logging
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

router = APIRouter()


def get_openrouter_client():
    """Get OpenRouter client with proper configuration"""
    try:
        if not Config.OPENROUTER_API_KEY:
            raise ValueError("OpenRouter API key not configured")

        return OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=Config.OPENROUTER_API_KEY,
            default_headers={
                "HTTP-Referer": Config.OPENROUTER_SITE_URL,
                "X-TitleSection": Config.OPENROUTER_SITE_NAME,
            },
        )
    except Exception as e:
        logger.error(f"Failed to initialize OpenRouter client: {e}")
        raise


def make_openrouter_request_openai(messages, model, **kwargs):
    """Make request to OpenRouter using OpenAI client"""
    try:
        client = get_openrouter_client()
        response = client.chat.completions.create(model=model, messages=messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"OpenRouter request failed: {e}")
        raise


def process_openrouter_response(response):
    """Process OpenRouter response to extract relevant data"""
    try:
        choices = []
        for choice in response.choices:
            msg = {"role": choice.message.role, "content": choice.message.content}

            # Include tool_calls if present (for function calling)
            if hasattr(choice.message, 'tool_calls') and choice.message.tool_calls:
                msg["tool_calls"] = choice.message.tool_calls

            # Include function_call if present (for legacy function_call format)
            if hasattr(choice.message, 'function_call') and choice.message.function_call:
                msg["function_call"] = choice.message.function_call

            choices.append({
                "index": choice.index,
                "message": msg,
                "finish_reason": choice.finish_reason,
            })

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
        logger.error(f"Failed to process OpenRouter response: {e}")
        raise


def make_openrouter_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to OpenRouter using OpenAI client"""
    try:
        client = get_openrouter_client()
        stream = client.chat.completions.create(
            model=model, messages=messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"OpenRouter streaming request failed: {e}")
        raise
