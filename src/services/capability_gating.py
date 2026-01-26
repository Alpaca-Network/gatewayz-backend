"""
Capability Gating Service for Prompt Router.

Extracts required capabilities from requests and filters models
by capabilities BEFORE any scoring happens.

This is the first gate in the routing pipeline - models that don't
satisfy capability requirements are never considered.
"""

import logging
from typing import Any

from src.schemas.router import ModelCapabilities, RequiredCapabilities

logger = logging.getLogger(__name__)

# Approximate tokens per character for estimation
CHARS_PER_TOKEN = 4


def extract_capabilities(
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    response_format: dict | None = None,
    max_cost_per_1k: float | None = None,
) -> RequiredCapabilities:
    """
    Extract required capabilities from a request.

    This is a pure function with no I/O - target: < 0.1ms.

    Args:
        messages: Conversation messages
        tools: Optional tools/functions parameter
        response_format: Optional response format specification
        max_cost_per_1k: Optional max cost constraint from user preferences

    Returns:
        RequiredCapabilities describing what the model must support
    """
    needs_tools = tools is not None and len(tools) > 0
    needs_json = False
    needs_json_schema = False
    strict_json = False

    if response_format:
        format_type = response_format.get("type", "text")
        needs_json = format_type == "json_object"
        needs_json_schema = format_type == "json_schema"
        strict_json = needs_json or needs_json_schema

    needs_vision = _has_image_content(messages)
    min_context_tokens = _estimate_input_tokens(messages)

    # Determine tool schema adherence requirement
    tool_schema_adherence = "any"
    if needs_tools:
        # If using tools with strict JSON, require high adherence
        if strict_json:
            tool_schema_adherence = "high"
        else:
            tool_schema_adherence = "medium"

    return RequiredCapabilities(
        needs_tools=needs_tools,
        needs_json=needs_json,
        needs_json_schema=needs_json_schema,
        needs_vision=needs_vision,
        min_context_tokens=min_context_tokens,
        max_cost_per_1k=max_cost_per_1k,
        strict_json=strict_json,
        tool_schema_adherence=tool_schema_adherence,
    )


def _has_image_content(messages: list[dict[str, Any]]) -> bool:
    """
    Check if any message contains image content.
    Handles both OpenAI and Anthropic message formats.
    """
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    part_type = part.get("type", "")
                    if part_type == "image" or part_type == "image_url":
                        return True
                    # Anthropic format
                    if part_type == "image" and part.get("source"):
                        return True
    return False


def _estimate_input_tokens(messages: list[dict[str, Any]]) -> int:
    """
    Estimate total input tokens from messages.
    Uses simple character count heuristic - not exact but fast.
    """
    total_chars = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text", "")
                    if text:
                        total_chars += len(text)
                elif isinstance(part, str):
                    total_chars += len(part)

    # Add overhead for message structure (~20 tokens per message)
    overhead = len(messages) * 20

    return (total_chars // CHARS_PER_TOKEN) + overhead


def filter_by_capabilities(
    models: list[str],
    capabilities_registry: dict[str, ModelCapabilities],
    required: RequiredCapabilities,
) -> list[str]:
    """
    Filter models to only those satisfying required capabilities.

    This is a pure function with no I/O - target: < 0.2ms.

    Args:
        models: List of model IDs to filter
        capabilities_registry: Registry mapping model_id -> ModelCapabilities
        required: Required capabilities

    Returns:
        Filtered list of model IDs that satisfy all requirements
    """
    result = []

    for model_id in models:
        caps = capabilities_registry.get(model_id)
        if not caps:
            # Unknown model - skip (fail closed for capability gating)
            logger.debug(f"Skipping unknown model in capability gating: {model_id}")
            continue

        if caps.satisfies(required):
            result.append(model_id)
        else:
            logger.debug(f"Model {model_id} filtered by capability gating")

    return result


def get_capability_mismatch_reason(
    model_caps: ModelCapabilities,
    required: RequiredCapabilities,
) -> str | None:
    """
    Get the reason why a model doesn't satisfy requirements.
    Useful for debugging and logging.
    """
    if required.needs_tools and not model_caps.tools:
        return "missing_tools_capability"
    if required.needs_json and not model_caps.json_mode:
        return "missing_json_mode"
    if required.needs_json_schema and not model_caps.json_schema:
        return "missing_json_schema"
    if required.needs_vision and not model_caps.vision:
        return "missing_vision"
    if required.min_context_tokens > model_caps.max_context:
        return f"context_too_small_{model_caps.max_context}"
    if required.max_cost_per_1k and model_caps.cost_per_1k_input > required.max_cost_per_1k:
        return f"cost_exceeds_limit_{model_caps.cost_per_1k_input}"
    if required.tool_schema_adherence == "high" and model_caps.tool_schema_adherence != "high":
        return "tool_adherence_too_low"
    return None
