import logging
from typing import Any


from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_featherless_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)


def _sanitize_messages_for_featherless(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Sanitize messages before sending to Featherless API.

    Featherless has strict validation and rejects messages with null tool_calls.
    Error: 'messages.0.tool_calls': ['Expected array, received null']

    This function:
    - Removes tool_calls field if it's null (Featherless expects array or absent)
    - Removes tool_call_id field if it's null
    - Preserves all other message fields

    Args:
        messages: List of message dictionaries

    Returns:
        Sanitized list of messages safe for Featherless API
    """
    sanitized = []
    for msg in messages:
        # Create a copy to avoid mutating the original
        clean_msg = {}
        for key, value in msg.items():
            # Skip null tool_calls - Featherless expects array or field to be absent
            if key == "tool_calls" and value is None:
                continue
            # Skip null tool_call_id
            if key == "tool_call_id" and value is None:
                continue
            clean_msg[key] = value
        sanitized.append(clean_msg)
    return sanitized


def get_featherless_client():
    """Get Featherless.ai client with connection pooling for better performance

    Featherless.ai provides OpenAI-compatible API endpoints for various models
    """
    try:
        if not Config.FEATHERLESS_API_KEY:
            raise ValueError("Featherless API key not configured")

        # Use pooled client for ~10-20ms performance improvement per request
        return get_featherless_pooled_client()
    except Exception as e:
        logger.error(f"Failed to initialize Featherless client: {e}")
        raise


def make_featherless_request_openai(messages, model, **kwargs):
    """Make request to Featherless.ai using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_featherless_client()
        # Sanitize messages to remove null tool_calls that Featherless rejects
        sanitized_messages = _sanitize_messages_for_featherless(messages)
        response = client.chat.completions.create(model=model, messages=sanitized_messages, **kwargs)
        return response
    except Exception as e:
        logger.error(f"Featherless request failed: {e}")
        raise


def make_featherless_request_openai_stream(messages, model, **kwargs):
    """Make streaming request to Featherless.ai using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        client = get_featherless_client()
        # Sanitize messages to remove null tool_calls that Featherless rejects
        sanitized_messages = _sanitize_messages_for_featherless(messages)
        stream = client.chat.completions.create(
            model=model, messages=sanitized_messages, stream=True, **kwargs
        )
        return stream
    except Exception as e:
        logger.error(f"Featherless streaming request failed: {e}")
        raise


def process_featherless_response(response):
    """Process Featherless response to extract relevant data"""
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
        logger.error(f"Failed to process Featherless response: {e}")
        raise
