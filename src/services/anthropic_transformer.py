"""
Anthropic Messages API Transformer
Converts between Anthropic Messages API format and OpenAI Chat Completions format

Compatible with: https://platform.claude.com/docs/en/api/messages
"""

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def extract_message_with_tools(choice_message: Any) -> dict[str, Any]:
    """
    Extract message data including role, content, and any tool calls or function calls.

    This is a shared utility function used by all provider response processors to
    reduce code duplication when extracting message data from OpenAI-compatible responses.

    Handles both object-based messages (with attributes) and dict-based messages.

    For reasoning/thinking models (e.g., Kimi-K2-Thinking, GPT-OSS-120B on Near AI),
    content may be null while the response is in 'reasoning' or 'reasoning_content' fields.
    This function extracts and includes reasoning content when present.

    Args:
        choice_message: The message object/dict from a choice in the response

    Returns:
        Dictionary with role, content, and optionally tool_calls/function_call/reasoning_content
    """
    # Extract basic message data
    if isinstance(choice_message, dict):
        role = choice_message.get("role", "assistant")
        content = choice_message.get("content")
        tool_calls = choice_message.get("tool_calls")
        function_call = choice_message.get("function_call")
        # Extract reasoning content for thinking models
        reasoning = choice_message.get("reasoning") or choice_message.get("reasoning_content")
    else:
        role = choice_message.role
        content = choice_message.content
        tool_calls = getattr(choice_message, "tool_calls", None)
        function_call = getattr(choice_message, "function_call", None)
        # Extract reasoning content for thinking models
        reasoning = getattr(choice_message, "reasoning", None) or getattr(
            choice_message, "reasoning_content", None
        )

    # Handle case where content is None but reasoning is present (thinking models)
    # For compatibility, we keep content as-is but also expose reasoning
    if content is None:
        content = ""

    # Build message dict with available fields
    msg = {"role": role, "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if function_call:
        msg["function_call"] = function_call
    # Include reasoning content if present (for thinking/reasoning models)
    if reasoning:
        msg["reasoning_content"] = reasoning

    return msg


def _extract_system_text(system: str | list[dict[str, Any]] | None) -> str | None:
    """
    Extract system prompt text from string or array of content blocks.

    Anthropic API supports system as either:
    - A simple string
    - An array of SystemContentBlock objects with type="text"

    Args:
        system: System prompt (string or array of content blocks)

    Returns:
        Combined system text or None
    """
    if system is None:
        return None

    if isinstance(system, str):
        return system

    if isinstance(system, list):
        text_parts = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        return "\n".join(text_parts) if text_parts else None

    return str(system)


def _transform_content_block(block: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]] | None:
    """
    Transform a single Anthropic content block to OpenAI format.

    Handles:
    - text: Simple text content
    - image: Base64 or URL image content
    - document: PDF or text document content (converted to text)
    - tool_use: Tool/function call (passed through for assistant messages)
    - tool_result: Result of a tool call (converted to text)

    Args:
        block: Anthropic content block

    Returns:
        OpenAI content block(s) or None
    """
    if not isinstance(block, dict):
        return None

    block_type = block.get("type")

    if block_type == "text":
        return {"type": "text", "text": block.get("text", "")}

    elif block_type == "image":
        source = block.get("source", {})
        if isinstance(source, dict):
            if source.get("type") == "base64":
                return {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{source.get('media_type', 'image/jpeg')};base64,{source.get('data', '')}"
                    },
                }
            elif source.get("type") == "url":
                return {"type": "image_url", "image_url": {"url": source.get("url", "")}}
        return None

    elif block_type == "document":
        # Documents are not directly supported by OpenAI, convert to text description
        source = block.get("source", {})
        title = block.get("title", "Document")
        context = block.get("context", "")
        # Check if context has actual content (not just whitespace)
        has_context = context and context.strip()

        if isinstance(source, dict):
            if source.get("type") == "text":
                doc_content = source.get("data", "")
                if has_context:
                    text = f"[Document: {title}]\n{context}\n{doc_content}"
                else:
                    text = f"[Document: {title}]\n{doc_content}"
                return {"type": "text", "text": text}
            elif source.get("type") in ("base64", "url"):
                # For binary documents, include metadata
                media_type = source.get("media_type", "unknown")
                text = f"[Document: {title} ({media_type})]"
                if has_context:
                    text += f"\n{context}"
                return {"type": "text", "text": text}
        return {"type": "text", "text": f"[Document: {title}]"}

    elif block_type == "tool_use":
        # Tool use blocks should only appear in assistant messages and are handled
        # separately in transform_anthropic_to_openai. If we reach here, it means
        # this is an unexpected tool_use block (e.g., in a user message), so convert
        # it to a text representation for compatibility.
        tool_name = block.get("name", "unknown_tool")
        tool_input = block.get("input", {})
        tool_id = block.get("id", "")
        return {
            "type": "text",
            "text": f"[Tool Call: {tool_name}] (id: {tool_id}) Input: {json.dumps(tool_input)}",
        }

    elif block_type == "tool_result":
        # Tool result blocks are from user messages
        tool_content = block.get("content", "")
        tool_use_id = block.get("tool_use_id", "")
        is_error = block.get("is_error", False)

        # Convert tool_result to text representation for OpenAI
        if isinstance(tool_content, str):
            result_text = tool_content
        elif isinstance(tool_content, list):
            # Extract text from content blocks
            result_parts = []
            for item in tool_content:
                if isinstance(item, dict) and item.get("type") == "text":
                    result_parts.append(item.get("text", ""))
            result_text = " ".join(result_parts)
        else:
            result_text = str(tool_content)

        prefix = "[Tool Error]" if is_error else "[Tool Result]"
        return {"type": "text", "text": f"{prefix} ({tool_use_id}): {result_text}"}

    else:
        # Pass through unknown types
        logger.debug(f"Unknown content block type: {block_type}")
        return block


def _transform_tool_choice(tool_choice: Any) -> Any:
    """
    Transform Anthropic tool_choice to OpenAI format.

    Anthropic tool_choice can be:
    - {"type": "auto"} -> "auto"
    - {"type": "any"} -> "required"
    - {"type": "none"} -> "none"
    - {"type": "tool", "name": "tool_name"} -> {"type": "function", "function": {"name": "tool_name"}}

    Args:
        tool_choice: Anthropic tool_choice object

    Returns:
        OpenAI tool_choice value
    """
    if tool_choice is None:
        return None

    if isinstance(tool_choice, str):
        return tool_choice

    if isinstance(tool_choice, dict):
        choice_type = tool_choice.get("type")
        if choice_type == "auto":
            return "auto"
        elif choice_type == "any":
            return "required"
        elif choice_type == "none":
            return "none"
        elif choice_type == "tool":
            return {
                "type": "function",
                "function": {"name": tool_choice.get("name", "")},
            }

    return tool_choice


def _transform_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """
    Transform Anthropic tool definitions to OpenAI format.

    Anthropic tools have:
    - name: str
    - description: str (optional)
    - input_schema: dict

    OpenAI tools have:
    - type: "function"
    - function: {"name": str, "description": str, "parameters": dict}

    Args:
        tools: List of Anthropic tool definitions

    Returns:
        List of OpenAI tool definitions
    """
    if not tools:
        return None

    openai_tools = []
    for tool in tools:
        if isinstance(tool, dict):
            openai_tool = {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "parameters": tool.get("input_schema", {}),
                },
            }
            if tool.get("description"):
                openai_tool["function"]["description"] = tool["description"]
            openai_tools.append(openai_tool)

    return openai_tools if openai_tools else None


def transform_anthropic_to_openai(
    messages: list[dict[str, Any]],
    system: str | list[dict[str, Any]] | None = None,
    max_tokens: int = 950,
    temperature: float | None = None,
    top_p: float | None = None,
    top_k: int | None = None,
    stop_sequences: list[str] | None = None,
    tools: list[dict] | None = None,
    tool_choice: Any | None = None,
) -> tuple:
    """
    Transform Anthropic Messages API request to OpenAI Chat Completions format.

    Compatible with: https://platform.claude.com/docs/en/api/messages

    Args:
        messages: List of Anthropic message objects
        system: System prompt (string or array of content blocks)
        max_tokens: Max tokens to generate (required in Anthropic)
        temperature: Temperature parameter
        top_p: Top-p parameter
        top_k: Top-k parameter (Anthropic-specific, ignored)
        stop_sequences: Stop sequences (maps to 'stop' in OpenAI)
        tools: Tool/function definitions for function calling
        tool_choice: Tool selection strategy ("auto", "any", "none", or specific tool)

    Returns:
        Tuple of (openai_messages, openai_params)
    """
    openai_messages = []

    # Add system message if provided (Anthropic separates this, can be string or array)
    system_text = _extract_system_text(system)
    if system_text:
        openai_messages.append({"role": "system", "content": system_text})

    # Transform messages
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")

        openai_msg = {"role": role}

        # Handle content (can be string or array of content blocks)
        if isinstance(content, str):
            openai_msg["content"] = content
        elif isinstance(content, list):
            # Content blocks
            content_parts = []
            tool_calls_list = []

            for block in content:
                if isinstance(block, dict):
                    block_type = block.get("type")

                    # Handle tool_use blocks in assistant messages
                    if block_type == "tool_use" and role == "assistant":
                        tool_calls_list.append(
                            {
                                "id": block.get("id", f"toolu_{int(time.time())}"),
                                "type": "function",
                                "function": {
                                    "name": block.get("name", ""),
                                    "arguments": json.dumps(block.get("input", {})),
                                },
                            }
                        )
                    else:
                        transformed = _transform_content_block(block)
                        if transformed:
                            if isinstance(transformed, list):
                                content_parts.extend(transformed)
                            else:
                                content_parts.append(transformed)

            # Set content
            if len(content_parts) > 1:
                openai_msg["content"] = content_parts
            elif len(content_parts) == 1:
                # For single text block, use string for better compatibility
                if content_parts[0].get("type") == "text":
                    openai_msg["content"] = content_parts[0].get("text", "")
                else:
                    openai_msg["content"] = content_parts
            else:
                openai_msg["content"] = "" if not tool_calls_list else None

            # Add tool_calls if present (for assistant messages)
            if tool_calls_list:
                openai_msg["tool_calls"] = tool_calls_list
        else:
            openai_msg["content"] = str(content) if content is not None else ""

        openai_messages.append(openai_msg)

    # Build optional parameters
    openai_params = {"max_tokens": max_tokens}

    if temperature is not None:
        openai_params["temperature"] = temperature
    if top_p is not None:
        openai_params["top_p"] = top_p
    if stop_sequences:
        openai_params["stop"] = stop_sequences

    # Transform tools to OpenAI format
    transformed_tools = _transform_tools(tools)
    if transformed_tools:
        openai_params["tools"] = transformed_tools

    # Transform tool_choice to OpenAI format
    transformed_tool_choice = _transform_tool_choice(tool_choice)
    if transformed_tool_choice:
        openai_params["tool_choice"] = transformed_tool_choice

    # Note: top_k is Anthropic-specific and not supported in OpenAI
    if top_k is not None:
        logger.debug(f"top_k parameter ({top_k}) is Anthropic-specific and will be ignored")

    return openai_messages, openai_params


def transform_openai_to_anthropic(
    openai_response: dict[str, Any],
    model: str,
    stop_sequences: list[str] | None = None,
) -> dict[str, Any]:
    """
    Transform OpenAI Chat Completions response to Anthropic Messages API format.

    Compatible with: https://platform.claude.com/docs/en/api/messages#response-format

    Args:
        openai_response: OpenAI chat completion response
        model: Model name to include in response
        stop_sequences: List of stop sequences (to detect which one was triggered)

    Returns:
        Anthropic Messages API response with proper stop_reason values:
        - end_turn: Natural stopping point
        - max_tokens: Exceeded max_tokens limit
        - stop_sequence: Custom stop sequence was generated
        - tool_use: Model invoked tool(s)
        - pause_turn: Long-running turn was paused (streaming only)
        - refusal: Streaming classifier intervened for safety
    """
    # Extract data from OpenAI response
    choice = openai_response.get("choices", [{}])[0]
    message = choice.get("message", {})
    content = message.get("content", "")
    finish_reason = choice.get("finish_reason", "stop")

    usage = openai_response.get("usage", {})
    prompt_tokens = usage.get("prompt_tokens", 0)
    completion_tokens = usage.get("completion_tokens", 0)

    # Map OpenAI finish_reason to Anthropic stop_reason
    # See: https://platform.claude.com/docs/en/api/messages#stop-reasons
    stop_reason_map = {
        "stop": "end_turn",
        "length": "max_tokens",
        "content_filter": "refusal",  # Map content filter to refusal (safety classifier)
        "tool_calls": "tool_use",
        "function_call": "tool_use",
    }
    stop_reason = stop_reason_map.get(finish_reason, "end_turn")

    # Check if stopped by a stop sequence (only for string content and natural stop)
    # Only check for stop sequences when the finish_reason was "stop" (end_turn)
    # to avoid overriding more specific stop reasons like tool_use or max_tokens
    stop_sequence_triggered = None
    if stop_sequences and isinstance(content, str) and content and stop_reason == "end_turn":
        for seq in stop_sequences:
            if content.endswith(seq):
                stop_reason = "stop_sequence"
                stop_sequence_triggered = seq
                break

    # Build content array for Anthropic response
    content_blocks = []

    # Check for reasoning/thinking content first (for extended thinking models)
    reasoning_content = message.get("reasoning") or message.get("reasoning_content")
    if reasoning_content:
        content_blocks.append(
            {
                "type": "thinking",
                "thinking": reasoning_content,
                "signature": "",  # Signature is typically provided by the model
            }
        )

    # Check for tool_calls (they take priority over text content)
    tool_calls = message.get("tool_calls")
    has_tool_calls = tool_calls and len(tool_calls) > 0

    # Handle tool_calls from OpenAI format (convert to Anthropic tool_use blocks)
    if has_tool_calls:
        for tool_call in tool_calls:
            # Extract tool information
            tool_name = tool_call.get("function", {}).get("name", "tool")
            tool_args = tool_call.get("function", {}).get("arguments", "{}")
            tool_id = tool_call.get("id", f"toolu_{int(time.time())}")

            # Parse arguments if they're a string
            if isinstance(tool_args, str):
                try:
                    tool_args = json.loads(tool_args)
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

            # Add tool_use content block in Anthropic format
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": tool_name,
                    "input": tool_args,
                }
            )

    # Add text content if present and non-empty
    if content and isinstance(content, str) and content.strip():
        content_blocks.append({"type": "text", "text": content})

    # If no content blocks were created, add empty text block
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    # Build usage object with cache fields
    usage_response = {
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
    }

    # Add cache-related fields if present in the response (use 'in' to include zero values)
    if "cache_creation_input_tokens" in usage:
        usage_response["cache_creation_input_tokens"] = usage["cache_creation_input_tokens"]
    if "cache_read_input_tokens" in usage:
        usage_response["cache_read_input_tokens"] = usage["cache_read_input_tokens"]

    # Build Anthropic-style response
    # See: https://platform.claude.com/docs/en/api/messages#response-format
    anthropic_response = {
        "id": openai_response.get("id", f"msg_{int(time.time())}"),
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": openai_response.get("model", model),
        "stop_reason": stop_reason,
        "stop_sequence": stop_sequence_triggered,
        "usage": usage_response,
    }

    # Preserve gateway usage if present
    if "gateway_usage" in openai_response:
        anthropic_response["gateway_usage"] = openai_response["gateway_usage"]

    return anthropic_response


def extract_text_from_content(content: str | list[dict[str, Any]]) -> str:
    """
    Extract plain text from Anthropic content (string or content blocks).

    Args:
        content: Content string or array of content blocks

    Returns:
        Plain text string
    """
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        return " ".join(text_parts) if text_parts else "[multimodal content]"

    return str(content) if content is not None else ""
