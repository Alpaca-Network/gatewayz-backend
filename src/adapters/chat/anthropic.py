"""
Anthropic Messages API format adapter.

Converts between Anthropic's Messages API format and internal unified format.
Reference: https://docs.anthropic.com/claude/reference/messages_post
"""

import json
import logging
from typing import Any, AsyncIterator

from src.adapters.chat.base import BaseChatAdapter
from src.schemas.internal.chat import (
    InternalChatRequest,
    InternalChatResponse,
    InternalMessage,
    InternalStreamChunk,
)

logger = logging.getLogger(__name__)


class AnthropicChatAdapter(BaseChatAdapter):
    """
    Adapter for Anthropic Messages API format.

    Key differences from OpenAI:
    - System message is a separate 'system' parameter, not in messages array
    - max_tokens is REQUIRED (not optional)
    - stop sequences called 'stop_sequences' not 'stop'
    - Different streaming event format (message_start, content_block_delta, etc.)
    - Response format is different (content array, not single string)
    """

    @property
    def format_name(self) -> str:
        return "anthropic"

    def to_internal_request(self, external_request: dict[str, Any]) -> InternalChatRequest:
        """
        Convert Anthropic Messages API request to internal format.

        Anthropic format:
        {
            "model": "claude-3-sonnet",
            "messages": [{"role": "user", "content": "..."}],
            "system": "You are a helpful assistant",  # Separate from messages!
            "max_tokens": 1024,  # REQUIRED
            "stop_sequences": ["Human:", "Assistant:"],
            ...
        }
        """
        internal_messages = []

        # Handle system message specially - Anthropic has it as separate parameter
        # Convert it to a system message in our internal format
        if external_request.get("system"):
            internal_messages.append(
                InternalMessage(role="system", content=external_request["system"])
            )

        # Convert conversation messages
        for msg in external_request.get("messages", []):
            # Anthropic content can be string or array of content blocks
            content = msg.get("content")

            # If content is array of blocks, we keep it as-is (multimodal)
            # If it's a string, keep as string
            internal_messages.append(
                InternalMessage(
                    role=msg.get("role", "user"),
                    content=content,
                    name=msg.get("name"),
                )
            )

        # Build internal request
        internal_request = InternalChatRequest(
            messages=internal_messages,
            model=external_request["model"],
            max_tokens=external_request.get("max_tokens", 1024),  # Required in Anthropic
            temperature=external_request.get("temperature"),
            top_p=external_request.get("top_p"),
            stop=external_request.get("stop_sequences"),  # Different name!
            stream=external_request.get("stream", False),
            # Anthropic doesn't have frequency/presence penalties
        )

        logger.debug(
            f"[AnthropicAdapter] Converted request: model={internal_request.model}, "
            f"messages={len(internal_request.messages)}, stream={internal_request.stream}"
        )

        return internal_request

    def from_internal_response(self, internal_response: InternalChatResponse) -> dict[str, Any]:
        """
        Convert internal response to Anthropic Messages API format.

        Anthropic response format:
        {
            "id": "msg_...",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "..."}],  # Array format!
            "model": "claude-3-sonnet",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20}
        }
        """
        # Convert content to Anthropic's array format
        content_blocks = [{"type": "text", "text": internal_response.content}]

        # Map finish reason to Anthropic's stop_reason
        stop_reason_map = {
            "stop": "end_turn",
            "length": "max_tokens",
            "tool_calls": "tool_use",
            "content_filter": "stop_sequence",
        }
        stop_reason = stop_reason_map.get(internal_response.finish_reason or "stop", "end_turn")

        # Build response
        response = {
            "id": internal_response.id,
            "type": "message",
            "role": "assistant",
            "content": content_blocks,
            "model": internal_response.model,
            "stop_reason": stop_reason,
            "usage": {
                "input_tokens": internal_response.usage.prompt_tokens,
                "output_tokens": internal_response.usage.completion_tokens,
            },
        }

        # Add gateway-specific metadata (not standard Anthropic, but useful)
        response["gateway_usage"] = {
            "provider": internal_response.provider_used,
            "cost_usd": round(internal_response.cost_usd, 6),
            "input_cost_usd": round(internal_response.input_cost_usd, 6),
            "output_cost_usd": round(internal_response.output_cost_usd, 6),
        }

        logger.debug(
            f"[AnthropicAdapter] Converted response: id={response['id']}, "
            f"tokens={response['usage']['input_tokens'] + response['usage']['output_tokens']}, "
            f"cost=${response['gateway_usage']['cost_usd']:.6f}"
        )

        return response

    async def from_internal_stream(
        self, internal_stream: AsyncIterator[InternalStreamChunk]
    ) -> AsyncIterator[str]:
        """
        Convert internal stream to Anthropic Messages API streaming format.

        Anthropic uses different event types:
        - message_start: Initial message metadata
        - content_block_start: Start of a content block
        - content_block_delta: Incremental content
        - content_block_stop: End of content block
        - message_delta: Updates to message metadata (usage, stop_reason)
        - message_stop: End of message

        Format:
        event: message_start
        data: {"type": "message_start", "message": {...}}

        event: content_block_delta
        data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "..."}}
        """
        chunk_count = 0
        first_chunk = True
        content_block_started = False

        try:
            async for chunk in internal_stream:
                chunk_count += 1

                # First chunk: send message_start event
                if first_chunk:
                    message_start = {
                        "type": "message_start",
                        "message": {
                            "id": chunk.id,
                            "type": "message",
                            "role": "assistant",
                            "content": [],
                            "model": chunk.model,
                        },
                    }
                    yield f"event: message_start\ndata: {json.dumps(message_start)}\n\n"

                    # Send content_block_start
                    content_block_start = {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    }
                    yield f"event: content_block_start\ndata: {json.dumps(content_block_start)}\n\n"
                    content_block_started = True
                    first_chunk = False

                # Send content delta if present
                if chunk.content:
                    delta_event = {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": chunk.content},
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(delta_event)}\n\n"

                # If this is the final chunk
                if chunk.finish_reason:
                    # Close content block
                    if content_block_started:
                        content_block_stop = {"type": "content_block_stop", "index": 0}
                        yield f"event: content_block_stop\ndata: {json.dumps(content_block_stop)}\n\n"

                    # Send message_delta with usage and stop_reason
                    if chunk.usage:
                        # Map finish reason
                        stop_reason_map = {
                            "stop": "end_turn",
                            "length": "max_tokens",
                            "tool_calls": "tool_use",
                        }
                        stop_reason = stop_reason_map.get(chunk.finish_reason, "end_turn")

                        message_delta = {
                            "type": "message_delta",
                            "delta": {"stop_reason": stop_reason},
                            "usage": {
                                "output_tokens": chunk.usage.completion_tokens,
                            },
                        }
                        yield f"event: message_delta\ndata: {json.dumps(message_delta)}\n\n"

                    # Send message_stop
                    message_stop = {"type": "message_stop"}
                    yield f"event: message_stop\ndata: {json.dumps(message_stop)}\n\n"

            logger.debug(f"[AnthropicAdapter] Streamed {chunk_count} chunks")

        except Exception as e:
            logger.error(f"[AnthropicAdapter] Error during streaming: {e}", exc_info=True)
            # Send error event
            error_event = {
                "type": "error",
                "error": {
                    "type": "internal_error",
                    "message": str(e),
                },
            }
            yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
