"""
OpenAI chat completion format adapter.

Converts between OpenAI Chat Completions API format and internal unified format.
Reference: https://platform.openai.com/docs/api-reference/chat
"""

import json
import logging
import time
from typing import Any, AsyncIterator

from src.adapters.chat.base import BaseChatAdapter
from src.schemas.internal.chat import (
    InternalChatRequest,
    InternalChatResponse,
    InternalMessage,
    InternalStreamChunk,
)

logger = logging.getLogger(__name__)


class OpenAIChatAdapter(BaseChatAdapter):
    """
    Adapter for OpenAI Chat Completions API format.

    Handles conversion between OpenAI's format and our internal format.
    Supports all OpenAI parameters including tools, response format, and streaming.
    """

    @property
    def format_name(self) -> str:
        return "openai"

    def to_internal_request(self, external_request: dict[str, Any]) -> InternalChatRequest:
        """
        Convert OpenAI format request to internal format.

        OpenAI format:
        {
            "messages": [{"role": "user", "content": "..."}],
            "model": "gpt-4",
            "temperature": 0.7,
            "max_tokens": 100,
            ...
        }
        """
        # Convert messages
        internal_messages = []
        for msg in external_request.get("messages", []):
            internal_messages.append(
                InternalMessage(
                    role=msg.get("role", "user"),
                    content=msg.get("content"),
                    name=msg.get("name"),
                    tool_call_id=msg.get("tool_call_id"),
                    tool_calls=msg.get("tool_calls"),
                )
            )

        # Build internal request
        internal_request = InternalChatRequest(
            messages=internal_messages,
            model=external_request["model"],
            temperature=external_request.get("temperature"),
            max_tokens=external_request.get("max_tokens"),
            top_p=external_request.get("top_p"),
            frequency_penalty=external_request.get("frequency_penalty"),
            presence_penalty=external_request.get("presence_penalty"),
            stop=external_request.get("stop"),
            stream=external_request.get("stream", False),
            tools=external_request.get("tools"),
            tool_choice=external_request.get("tool_choice"),
            response_format=external_request.get("response_format"),
            user=external_request.get("user"),
        )

        logger.debug(
            f"[OpenAIAdapter] Converted request: model={internal_request.model}, "
            f"messages={len(internal_request.messages)}, stream={internal_request.stream}"
        )

        return internal_request

    def from_internal_response(self, internal_response: InternalChatResponse) -> dict[str, Any]:
        """
        Convert internal response to OpenAI format.

        Returns OpenAI Chat Completion response format.
        """
        # Build choice object
        choice = {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": internal_response.content,
            },
            "finish_reason": internal_response.finish_reason or "stop",
        }

        # Add tool calls if present
        if internal_response.tool_calls:
            choice["message"]["tool_calls"] = internal_response.tool_calls

        # Build full response
        response = {
            "id": internal_response.id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": internal_response.model,
            "choices": [choice],
            "usage": {
                "prompt_tokens": internal_response.usage.prompt_tokens,
                "completion_tokens": internal_response.usage.completion_tokens,
                "total_tokens": internal_response.usage.total_tokens,
            },
        }

        # Add gateway-specific usage info
        response["gateway_usage"] = {
            "provider": internal_response.provider_used,
            "cost_usd": round(internal_response.cost_usd, 6),
            "input_cost_usd": round(internal_response.input_cost_usd, 6),
            "output_cost_usd": round(internal_response.output_cost_usd, 6),
        }

        # Add processing time if available
        if internal_response.processing_time_ms:
            response["gateway_usage"]["processing_time_ms"] = internal_response.processing_time_ms

        logger.debug(
            f"[OpenAIAdapter] Converted response: id={response['id']}, "
            f"tokens={response['usage']['total_tokens']}, cost=${response['gateway_usage']['cost_usd']:.6f}"
        )

        return response

    async def from_internal_stream(
        self, internal_stream: AsyncIterator[InternalStreamChunk]
    ) -> AsyncIterator[str]:
        """
        Convert internal stream to OpenAI Server-Sent Events (SSE) format.

        OpenAI streaming format:
        data: {"id": "...", "object": "chat.completion.chunk", "choices": [...]}

        data: [DONE]
        """
        chunk_count = 0

        try:
            async for chunk in internal_stream:
                chunk_count += 1

                # Build delta object
                delta: dict[str, Any] = {}

                # Add role in first chunk
                if chunk.role:
                    delta["role"] = chunk.role

                # Add content if present
                if chunk.content is not None:
                    delta["content"] = chunk.content

                # Add tool calls if present
                if chunk.tool_calls:
                    delta["tool_calls"] = chunk.tool_calls

                # Build choice
                choice = {
                    "index": 0,
                    "delta": delta,
                    "finish_reason": chunk.finish_reason,
                }

                # Build chunk response
                chunk_response = {
                    "id": chunk.id,
                    "object": "chat.completion.chunk",
                    "created": chunk.created,
                    "model": chunk.model,
                    "choices": [choice],
                }

                # Add usage in final chunk if present
                if chunk.usage:
                    chunk_response["usage"] = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens,
                    }

                # Format as SSE
                sse_data = f"data: {json.dumps(chunk_response)}\n\n"
                yield sse_data

            # Send [DONE] marker
            yield "data: [DONE]\n\n"

            logger.debug(f"[OpenAIAdapter] Streamed {chunk_count} chunks")

        except Exception as e:
            logger.error(f"[OpenAIAdapter] Error during streaming: {e}", exc_info=True)
            # Send error as SSE
            error_chunk = {
                "error": {
                    "message": str(e),
                    "type": "internal_error",
                }
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
