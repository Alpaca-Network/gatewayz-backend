"""
Response formatters for different API formats.
Converts internal unified response to OpenAI, Anthropic, or Responses API format.
"""

from typing import Any
import time


class ResponseFormatter:
    """Format unified responses to specific API formats"""

    @staticmethod
    def to_openai(response: dict[str, Any]) -> dict[str, Any]:
        """
        Format internal response as OpenAI chat completion.

        Args:
            response: Unified internal response

        Returns:
            OpenAI-formatted response

        Example output:
            {
                "id": "chatcmpl-123",
                "object": "chat.completion",
                "created": 1677652288,
                "model": "gpt-4",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello!"
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 12,
                    "total_tokens": 21
                }
            }
        """

        formatted = {
            "id": response.get("id", f"chatcmpl-{int(time.time())}"),
            "object": "chat.completion",
            "created": response.get("created", int(time.time())),
            "model": response.get("model", "unknown"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response.get("content", "")
                    },
                    "finish_reason": response.get("finish_reason", "stop")
                }
            ],
            "usage": response.get("usage", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            })
        }

        # Add function/tool calls if present
        if response.get("tool_calls"):
            formatted["choices"][0]["message"]["tool_calls"] = response["tool_calls"]

        # Add gateway usage metadata
        if response.get("gateway_usage"):
            formatted["gateway_usage"] = response["gateway_usage"]

        return formatted

    @staticmethod
    def to_anthropic(response: dict[str, Any]) -> dict[str, Any]:
        """
        Format internal response as Anthropic message.

        Args:
            response: Unified internal response

        Returns:
            Anthropic-formatted response

        Example output:
            {
                "id": "msg_123",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello!"}],
                "model": "claude-3-opus",
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": 9,
                    "output_tokens": 12
                }
            }
        """

        usage = response.get("usage", {})

        formatted = {
            "id": response.get("id", f"msg_{int(time.time())}"),
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": response.get("content", "")
                }
            ],
            "model": response.get("model", "unknown"),
            "stop_reason": _map_finish_reason_to_anthropic(
                response.get("finish_reason", "stop")
            ),
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0)
            }
        }

        # Add tool use if present
        if response.get("tool_calls"):
            # Convert OpenAI tool calls to Anthropic format
            for tool_call in response["tool_calls"]:
                formatted["content"].append({
                    "type": "tool_use",
                    "id": tool_call.get("id"),
                    "name": tool_call.get("function", {}).get("name"),
                    "input": tool_call.get("function", {}).get("arguments")
                })

        return formatted

    @staticmethod
    def to_responses_api(response: dict[str, Any]) -> dict[str, Any]:
        """
        Format internal response as OpenAI Responses API.

        Args:
            response: Unified internal response

        Returns:
            Responses API-formatted response

        Example output:
            {
                "id": "resp_123",
                "object": "response",
                "created": 1677652288,
                "model": "gpt-4",
                "output": [{
                    "index": 0,
                    "role": "assistant",
                    "content": "Hello!",
                    "finish_reason": "stop"
                }],
                "usage": {...}
            }
        """

        formatted = {
            "id": response.get("id", f"resp_{int(time.time())}"),
            "object": "response",
            "created": response.get("created", int(time.time())),
            "model": response.get("model", "unknown"),
            "output": [
                {
                    "index": 0,
                    "role": "assistant",
                    "content": response.get("content", ""),
                    "finish_reason": response.get("finish_reason", "stop")
                }
            ],
            "usage": response.get("usage", {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            })
        }

        # Add tool calls if present
        if response.get("tool_calls"):
            formatted["output"][0]["tool_calls"] = response["tool_calls"]

        # Add gateway usage
        if response.get("gateway_usage"):
            formatted["gateway_usage"] = response["gateway_usage"]

        return formatted

    @staticmethod
    def format_response(
        response: dict[str, Any],
        format: str
    ) -> dict[str, Any]:
        """
        Route to appropriate formatter based on format.

        Args:
            response: Unified internal response
            format: Target format ("openai", "anthropic", "responses")

        Returns:
            Formatted response in target format
        """
        formatters = {
            "openai": ResponseFormatter.to_openai,
            "anthropic": ResponseFormatter.to_anthropic,
            "responses": ResponseFormatter.to_responses_api,
        }

        formatter = formatters.get(format.lower(), ResponseFormatter.to_openai)
        return formatter(response)


def _map_finish_reason_to_anthropic(finish_reason: str) -> str:
    """
    Map OpenAI finish reasons to Anthropic stop reasons.

    OpenAI: stop, length, function_call, content_filter, null
    Anthropic: end_turn, max_tokens, stop_sequence, tool_use
    """
    mapping = {
        "stop": "end_turn",
        "length": "max_tokens",
        "function_call": "tool_use",
        "tool_calls": "tool_use",
        "content_filter": "end_turn",
    }

    return mapping.get(finish_reason, "end_turn")
