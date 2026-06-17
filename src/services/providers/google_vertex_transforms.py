"""Google Vertex AI request/response transforms (extracted from
google_vertex_client.py, Phase 0c-2 thinning). Pure functions: model-id/location
resolution and OpenAI<->Vertex tool/message translation. No shared state; imported
by the client's request impl. Behavior unchanged (verbatim move)."""

from __future__ import annotations

import logging
from typing import Any

from src.config import Config

logger = logging.getLogger(__name__)


def _get_model_location(model_name: str, try_regional_fallback: bool = False) -> str:
    """
    Determine the appropriate GCP location for a given model.

    Preview models (e.g., Gemini 3, models with 'preview' in name) are only available
    on global endpoints. Generally available models use regional endpoints by default
    for better performance (44-45% faster TTFC based on testing).

    Args:
        model_name: The model name to check
        try_regional_fallback: If True, attempts regional endpoint even for preview models
                               (useful for performance testing and fallback scenarios)

    Returns:
        The location string to use ('global' or the configured regional location)
    """
    model_lower = model_name.lower()

    # Preview models require global endpoint:
    # 1. Gemini 3 models (e.g., gemini-3-flash-preview, gemini-3-pro-preview)
    # 2. Any model with 'preview' in the name
    # Regional endpoints for these show ~20% failure rate (404 Model Not Found)
    is_preview_model = "gemini-3" in model_lower or "preview" in model_lower

    if is_preview_model:
        if try_regional_fallback:
            logger.info(
                f"Model {model_name} normally requires global endpoint, but trying regional "
                f"fallback ({Config.GOOGLE_VERTEX_LOCATION}) for performance optimization"
            )
            return Config.GOOGLE_VERTEX_LOCATION
        logger.debug(f"Model {model_name} requires global endpoint (preview model)")
        return "global"

    # Generally available models (e.g., gemini-2.5-flash-lite, gemini-2.0-flash-exp)
    # use regional endpoints by default for better performance
    logger.debug(
        f"Model {model_name} using regional endpoint ({Config.GOOGLE_VERTEX_LOCATION}) for optimal performance"
    )
    return Config.GOOGLE_VERTEX_LOCATION


def _sanitize_system_content(content: Any) -> str:
    """Normalize system message content into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        return "\n".join(part for part in text_parts if part)
    return str(content)


def _translate_openai_tools_to_vertex(tools: list[dict]) -> list[dict]:
    """Translate OpenAI tools format to Google Vertex AI functionDeclarations format.

    OpenAI format:
    [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "City name"}
                    },
                    "required": ["location"]
                }
            }
        }
    ]

    Vertex AI format:
    [
        {
            "functionDeclarations": [
                {
                    "name": "get_weather",
                    "description": "Get weather for a location",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City name"}
                        },
                        "required": ["location"]
                    }
                }
            ]
        }
    ]

    Args:
        tools: List of OpenAI-format tool definitions

    Returns:
        List containing a single dict with functionDeclarations for Vertex AI
    """
    if not tools:
        return []

    function_declarations = []

    for tool in tools:
        # Only process function-type tools
        if tool.get("type") != "function":
            logger.warning(
                f"Skipping non-function tool type: {tool.get('type')}. "
                "Only 'function' type tools are supported for Vertex AI."
            )
            continue

        function_def = tool.get("function", {})
        if not function_def:
            logger.warning("Skipping tool with missing 'function' definition")
            continue

        name = function_def.get("name")
        if not name:
            logger.warning("Skipping function with missing 'name' field")
            continue

        # Build Vertex AI function declaration
        vertex_function = {
            "name": name,
        }

        # Add description if present
        if function_def.get("description"):
            vertex_function["description"] = function_def["description"]

        # Add parameters if present
        # Vertex AI uses OpenAPI 3.0.3 schema format which is compatible with OpenAI's JSON Schema
        if function_def.get("parameters"):
            vertex_function["parameters"] = function_def["parameters"]

        function_declarations.append(vertex_function)

    if not function_declarations:
        logger.warning("No valid function declarations found after translation")
        return []

    logger.info(
        f"Translated {len(function_declarations)} OpenAI tool(s) to Vertex AI functionDeclarations"
    )

    # Vertex AI expects tools as: [{"functionDeclarations": [...]}]
    return [{"functionDeclarations": function_declarations}]


def _translate_tool_choice_to_vertex(tool_choice: str | dict | None) -> dict | None:
    """Translate OpenAI tool_choice to Vertex AI toolConfig.

    OpenAI tool_choice values:
    - "none": Model will not call any tools
    - "auto": Model decides whether to call tools (default)
    - "required": Model must call at least one tool
    - {"type": "function", "function": {"name": "..."}}:  Model must call the specific function

    Vertex AI toolConfig format:
    {
        "functionCallingConfig": {
            "mode": "NONE" | "AUTO" | "ANY",
            "allowedFunctionNames": ["..."]  # Only for ANY mode
        }
    }

    Args:
        tool_choice: OpenAI tool_choice value

    Returns:
        Vertex AI toolConfig dict, or None if no translation needed
    """
    if tool_choice is None:
        return None

    # String values
    if isinstance(tool_choice, str):
        if tool_choice == "none":
            return {"functionCallingConfig": {"mode": "NONE"}}
        elif tool_choice == "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        elif tool_choice == "required":
            return {"functionCallingConfig": {"mode": "ANY"}}
        else:
            logger.warning(f"Unknown tool_choice value: {tool_choice}. Using AUTO mode.")
            return {"functionCallingConfig": {"mode": "AUTO"}}

    # Object value: force specific function
    if isinstance(tool_choice, dict):
        if tool_choice.get("type") == "function":
            function_obj = tool_choice.get("function")
            function_name = function_obj.get("name") if function_obj else None
            if function_name:
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [function_name],
                    }
                }
            else:
                logger.warning("tool_choice function object missing 'name'. Using ANY mode.")
                return {"functionCallingConfig": {"mode": "ANY"}}

    logger.warning(f"Unrecognized tool_choice format: {tool_choice}. Using AUTO mode.")
    return {"functionCallingConfig": {"mode": "AUTO"}}


def _prepare_vertex_contents(messages: list) -> tuple[list, str | None]:
    """Split OpenAI messages into conversational content and system instruction."""
    system_messages = []
    conversational_messages = []

    for message in messages:
        role = message.get("role", "user")
        if role == "system":
            system_messages.append(_sanitize_system_content(message.get("content", "")))
            continue
        conversational_messages.append(message)

    contents = _build_vertex_content(conversational_messages)
    system_instruction = "\n\n".join(filter(None, system_messages)) if system_messages else None
    return contents, system_instruction


def transform_google_vertex_model_id(model_id: str) -> str:
    """Transform model ID to Google Vertex AI format

    For the REST API, we just need the model name (e.g., 'gemini-2.5-flash-lite').
    The full URL path is constructed in the API call functions.

    Args:
        model_id: Model identifier (e.g., 'gemini-2.0-flash', 'gemini-1.5-pro',
                  'google/gemini-2.0-flash')

    Returns:
        Simple model name (e.g., 'gemini-2.5-flash-lite')
    """
    # If already in full format, extract the model name
    if model_id.startswith("projects/"):
        # Extract model name from projects/.../models/{model}
        return model_id.split("/models/")[-1]

    # Strip provider prefix (e.g., 'google/gemini-2.0-flash' -> 'gemini-2.0-flash')
    if model_id.startswith("google/"):
        return model_id[7:]  # len("google/") == 7

    # Otherwise, return as-is
    return model_id


def _build_vertex_content(messages: list) -> list:
    """Convert OpenAI message format to Google Vertex AI content format

    Args:
        messages: List of OpenAI-format messages

    Returns:
        List of content objects in Vertex AI format
    """
    contents = []

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")

        # Map OpenAI roles to Vertex AI roles
        vertex_role = "user" if role == "user" else "model"

        # Handle content as string or list (for multimodal)
        if isinstance(content, str):
            parts = [{"text": content}]
        elif isinstance(content, list):
            parts = []
            for item in content:
                if item.get("type") == "text":
                    parts.append({"text": item.get("text", "")})
                elif item.get("type") == "image_url":
                    # Vertex AI supports inline base64 or URLs
                    image_url = item.get("image_url", {}).get("url", "")
                    if image_url.startswith("data:"):
                        # Base64 encoded image - extract MIME type and raw base64 data
                        # Format: data:image/jpeg;base64,<base64_data>
                        # Vertex AI expects only the raw base64 data, not the data URI prefix
                        try:
                            # Parse the data URL to extract MIME type and base64 data
                            # Expected format: data:<mime_type>;base64,<data>
                            header, base64_data = image_url.split(",", 1)
                            # Extract MIME type from header (e.g., "data:image/png;base64")
                            mime_type = "image/jpeg"  # default
                            if header.startswith("data:") and ";" in header:
                                mime_part = header[5:].split(";")[0]  # Remove "data:" prefix
                                if mime_part:
                                    mime_type = mime_part
                            parts.append(
                                {"inline_data": {"mime_type": mime_type, "data": base64_data}}
                            )
                        except ValueError:
                            # If parsing fails, log warning and skip this part
                            logger.warning(f"Failed to parse base64 data URL: {image_url[:50]}...")
                    else:
                        # URL reference
                        parts.append(
                            {"file_data": {"mime_type": "image/jpeg", "file_uri": image_url}}
                        )
        else:
            parts = [{"text": str(content)}]

        contents.append({"role": vertex_role, "parts": parts})

    return contents
