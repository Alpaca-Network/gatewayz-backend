"""
Format transformers for unified chat completions API.

Supports both OpenAI Chat Completions format and Anthropic Messages format
in a single /v1/chat/completions endpoint.
"""

from typing import Any, Literal
import logging
import time

logger = logging.getLogger(__name__)


class RequestFormat:
    """Enum-like class for request formats"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


def detect_request_format(request_data: dict) -> str:
    """
    Detect whether request is in OpenAI or Anthropic format.

    Detection strategy:
    - Anthropic format has 'system' as top-level parameter (not in messages)
    - Anthropic format may use 'stop_sequences' instead of 'stop'
    - Anthropic format may have 'top_k' parameter
    - Anthropic format requires 'max_tokens' (OpenAI makes it optional)

    Args:
        request_data: The request JSON dict

    Returns:
        RequestFormat.OPENAI or RequestFormat.ANTHROPIC
    """
    # Strong indicators of Anthropic format
    if "system" in request_data and request_data.get("system") is not None:
        logger.debug("Detected Anthropic format: has 'system' parameter")
        return RequestFormat.ANTHROPIC

    if "stop_sequences" in request_data:
        logger.debug("Detected Anthropic format: has 'stop_sequences'")
        return RequestFormat.ANTHROPIC

    if "top_k" in request_data:
        logger.debug("Detected Anthropic format: has 'top_k'")
        return RequestFormat.ANTHROPIC

    # Default to OpenAI format
    logger.debug("Detected OpenAI format (default)")
    return RequestFormat.OPENAI


class AnthropicToOpenAITransformer:
    """Transform Anthropic Messages API requests to OpenAI Chat Completions format"""

    @staticmethod
    def transform_request(anthropic_request: dict) -> dict:
        """
        Transform Anthropic Messages API request to OpenAI format.

        Anthropic format:
        {
            "model": "claude-sonnet-4",
            "max_tokens": 1024,  # REQUIRED
            "system": "You are helpful",  # Separate from messages
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 40,  # Anthropic-specific
            "stop_sequences": ["Human:", "AI:"]  # Instead of 'stop'
        }

        OpenAI format:
        {
            "model": "gpt-4",
            "messages": [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hello"}
            ],
            "max_tokens": 1024,
            "temperature": 0.7,
            "top_p": 0.9,
            "stop": ["Human:", "AI:"]
        }
        """
        openai_request = anthropic_request.copy()

        # 1. Handle system prompt - move to first message
        system_prompt = openai_request.pop("system", None)
        if system_prompt:
            messages = openai_request.get("messages", [])
            # Insert system message at the beginning
            openai_request["messages"] = [
                {"role": "system", "content": system_prompt}
            ] + messages
            logger.debug(f"Transformed 'system' parameter to system message")

        # 2. Transform stop_sequences → stop
        stop_sequences = openai_request.pop("stop_sequences", None)
        if stop_sequences:
            openai_request["stop"] = stop_sequences
            logger.debug(f"Transformed 'stop_sequences' to 'stop'")

        # 3. Handle top_k (Anthropic-specific, log warning)
        top_k = openai_request.pop("top_k", None)
        if top_k is not None:
            logger.warning(
                f"Anthropic 'top_k' parameter ({top_k}) is not supported in OpenAI format. "
                "This parameter will be ignored."
            )

        # 4. Handle metadata (Anthropic-specific, log)
        metadata = openai_request.pop("metadata", None)
        if metadata:
            logger.debug(f"Anthropic 'metadata' parameter will be preserved in context")

        # 5. Transform content blocks if they're in Anthropic format
        messages = openai_request.get("messages", [])
        for msg in messages:
            content = msg.get("content")
            # If content is array of blocks (Anthropic), extract text
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                if text_parts:
                    msg["content"] = "\n".join(text_parts)
                    logger.debug("Transformed content blocks to plain text")

        logger.info("Transformed Anthropic request to OpenAI format")
        return openai_request


class OpenAIToAnthropicTransformer:
    """Transform OpenAI Chat Completions responses to Anthropic Messages format"""

    @staticmethod
    def transform_response(openai_response: dict) -> dict:
        """
        Transform OpenAI Chat Completions response to Anthropic Messages format.

        OpenAI format:
        {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1677652288,
            "model": "gpt-4",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15
            }
        }

        Anthropic format:
        {
            "id": "msg-123",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}],
            "model": "claude-sonnet-4",
            "stop_reason": "end_turn",
            "stop_sequence": null,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5
            }
        }
        """
        # Extract first choice (Anthropic doesn't support multiple choices)
        choice = openai_response.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "stop")

        # Transform finish_reason to stop_reason
        stop_reason_map = {
            "stop": "end_turn",
            "length": "max_tokens",
            "content_filter": "stop_sequence",
            "tool_calls": "tool_use",
            "function_call": "tool_use"
        }
        stop_reason = stop_reason_map.get(finish_reason, "end_turn")

        # Transform content to content blocks
        content_text = message.get("content", "")
        content_blocks = [{"type": "text", "text": content_text}] if content_text else []

        # Handle tool_calls if present
        tool_calls = message.get("tool_calls")
        if tool_calls:
            for tool_call in tool_calls:
                function = tool_call.get("function", {})
                content_blocks.append({
                    "type": "tool_use",
                    "id": tool_call.get("id"),
                    "name": function.get("name"),
                    "input": function.get("arguments", {})
                })

        # Transform usage
        usage = openai_response.get("usage", {})
        anthropic_usage = {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0)
        }

        # Build Anthropic response
        anthropic_response = {
            "id": openai_response.get("id", "msg-unknown"),
            "type": "message",
            "role": "assistant",
            "content": content_blocks,
            "model": openai_response.get("model", "unknown"),
            "stop_reason": stop_reason,
            "stop_sequence": None,  # TODO: detect actual stop sequence if available
            "usage": anthropic_usage
        }

        # Preserve gateway_usage if present
        if "gateway_usage" in openai_response:
            anthropic_response["gateway_usage"] = openai_response["gateway_usage"]

        logger.info(f"Transformed OpenAI response to Anthropic format (stop_reason: {stop_reason})")
        return anthropic_response

    @staticmethod
    def transform_streaming_chunk(openai_chunk: str, is_first_chunk: bool = False) -> str:
        """
        Transform OpenAI SSE chunk to Anthropic streaming format.

        OpenAI streaming format:
        data: {"id":"123","choices":[{"delta":{"content":"Hello"},"index":0}]}

        Anthropic streaming format:
        event: message_start
        data: {"type":"message_start","message":{...}}

        event: content_block_delta
        data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}

        event: message_stop
        data: {"type":"message_stop"}
        """
        # This is a simplified version - full implementation would parse JSON
        # and properly transform each event type

        if not openai_chunk or openai_chunk.strip() == "data: [DONE]":
            # Transform [DONE] to message_stop
            return "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"

        # For now, pass through - full streaming implementation needed
        logger.debug("Streaming transformation not yet fully implemented")
        return openai_chunk


def transform_request_if_needed(request_data: dict) -> tuple[dict, str]:
    """
    Detect format and transform request if needed.

    Returns:
        Tuple of (transformed_request, original_format)
    """
    format_type = detect_request_format(request_data)

    if format_type == RequestFormat.ANTHROPIC:
        transformed = AnthropicToOpenAITransformer.transform_request(request_data)
        return transformed, RequestFormat.ANTHROPIC

    # Already OpenAI format
    return request_data, RequestFormat.OPENAI


def transform_response_if_needed(response_data: dict, original_format: str) -> dict:
    """
    Transform response back to original request format.

    Args:
        response_data: OpenAI format response
        original_format: The format the request came in as

    Returns:
        Response in the appropriate format
    """
    if original_format == RequestFormat.ANTHROPIC:
        return OpenAIToAnthropicTransformer.transform_response(response_data)

    # Keep OpenAI format
    return response_data
