import logging


from src.config import Config
from src.services.anthropic_transformer import extract_message_with_tools
from src.services.connection_pool import get_featherless_pooled_client

# Initialize logging
logger = logging.getLogger(__name__)


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


def _sanitize_messages_for_featherless(messages: list[dict]) -> list[dict]:
    """
    Sanitize messages for Featherless API compatibility.

    Featherless expects:
    - tool_calls to be an array or omitted entirely (not null)
    - Validation errors occur when tool_calls is null

    Args:
        messages: List of message dictionaries

    Returns:
        Sanitized list of messages
    """
    sanitized = []
    for msg in messages:
        clean_msg = msg.copy()

        # Remove null tool_calls (Featherless rejects null, expects array or omitted)
        if 'tool_calls' in clean_msg and clean_msg['tool_calls'] is None:
            logger.debug(f"Removing null tool_calls from message")
            del clean_msg['tool_calls']

        # Ensure tool_calls is array if present
        if 'tool_calls' in clean_msg and not isinstance(clean_msg['tool_calls'], list):
            logger.warning(
                f"Invalid tool_calls type: {type(clean_msg['tool_calls'])}, removing field"
            )
            del clean_msg['tool_calls']

        sanitized.append(clean_msg)

    return sanitized


def make_featherless_request_openai(messages, model, **kwargs):
    """Make request to Featherless.ai using OpenAI client

    Args:
        messages: List of message objects
        model: Model name to use
        **kwargs: Additional parameters like max_tokens, temperature, etc.
    """
    try:
        # Sanitize messages before sending to Featherless
        sanitized_messages = _sanitize_messages_for_featherless(messages)

        client = get_featherless_client()
        response = client.chat.completions.create(
            model=model,
            messages=sanitized_messages,
            **kwargs
        )
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
        # Sanitize messages before sending to Featherless
        sanitized_messages = _sanitize_messages_for_featherless(messages)

        client = get_featherless_client()
        stream = client.chat.completions.create(
            model=model,
            messages=sanitized_messages,
            stream=True,
            **kwargs
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
