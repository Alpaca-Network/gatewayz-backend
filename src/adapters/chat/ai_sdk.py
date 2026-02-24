"""
AI SDK (Vercel AI SDK) format adapter.

Converts between Vercel AI SDK format and internal unified format.
The AI SDK uses a simplified OpenAI-like format without advanced features.
Reference: https://sdk.vercel.ai/docs
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


class AISDKChatAdapter(BaseChatAdapter):
    """
    Adapter for Vercel AI SDK format.

    Key differences from OpenAI:
    - Simpler message format (no tool support, no complex content blocks)
    - Fewer parameters (no tools, response_format, etc.)
    - Same SSE streaming format as OpenAI
    - Response format matches OpenAI Chat Completions API
    """

    @property
    def format_name(self) -> str:
        return "ai-sdk"

    def to_internal_request(self, external_request: dict[str, Any]) -> InternalChatRequest:
        """
        Convert AI SDK format request to internal format.

        AI SDK format:
        {
            "model": "openai/gpt-4o",
            "messages": [{"role": "user", "content": "..."}],
            "max_tokens": 1024,
            "temperature": 0.7,
            "stream": false
        }
        """
        # Convert messages - AI SDK uses simple role/content structure
        internal_messages = []
        for msg in external_request.get("messages", []):
            internal_messages.append(
                InternalMessage(
                    role=msg.get("role", "user"),
                    content=msg.get("content"),
                    # AI SDK doesn't support name, tool_calls, or tool_call_id
                )
            )

        # Build internal request with supported parameters
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
            # AI SDK doesn't support tools, tool_choice, response_format
        )

        logger.debug(
            f"[AISDKAdapter] Converted request: model={internal_request.model}, "
            f"messages={len(internal_request.messages)}, stream={internal_request.stream}"
        )

        return internal_request

    def from_internal_response(self, internal_response: InternalChatResponse) -> dict[str, Any]:
        """
        Convert internal response to AI SDK format.

        AI SDK response format (matches OpenAI):
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "..."},
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30
            }
        }
        """
        # Build choice object
        choice = {
            "message": {
                "role": "assistant",
                "content": internal_response.content,
            },
            "finish_reason": internal_response.finish_reason or "stop",
        }

        # Build response (simplified compared to OpenAI - no id, object, created, model)
        response = {
            "choices": [choice],
            "usage": {
                "prompt_tokens": internal_response.usage.prompt_tokens,
                "completion_tokens": internal_response.usage.completion_tokens,
                "total_tokens": internal_response.usage.total_tokens,
            },
        }

        # Add gateway-specific metadata
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
            f"[AISDKAdapter] Converted response: "
            f"tokens={response['usage']['total_tokens']}, cost=${response['gateway_usage']['cost_usd']:.6f}"
        )

        return response

    async def from_internal_stream(
        self, internal_stream: AsyncIterator[InternalStreamChunk]
    ) -> AsyncIterator[str]:
        """
        Convert internal stream to AI SDK Server-Sent Events (SSE) format.

        AI SDK uses the same SSE format as OpenAI:
        data: {"choices": [{"delta": {"role": "assistant", "content": "..."}}]}

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

                # Build choice
                choice = {
                    "delta": delta,
                    "finish_reason": chunk.finish_reason,
                }

                # Build chunk response
                chunk_response = {
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

            logger.debug(f"[AISDKAdapter] Streamed {chunk_count} chunks")

        except Exception as e:
            logger.error(f"[AISDKAdapter] Error during streaming: {e}", exc_info=True)
            # Send error as SSE
            error_chunk = {
                "error": {
                    "message": str(e),
                    "type": "internal_error",
                }
            }
            yield f"data: {json.dumps(error_chunk)}\n\n"
            yield "data: [DONE]\n\n"
