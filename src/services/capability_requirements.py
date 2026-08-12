"""
Capability Requirements.

Derives what a chat request needs from a model — tool support, vision, JSON
mode, a rough input-token estimate — purely from the request shape (messages,
tools, response_format). No I/O, no catalog lookups; the output plugs directly
into `models_catalog_db.get_candidate_models(required_capabilities=...)`.

This replaces the extraction half of the deleted D2 auto-router's
`capability_gating.py` (removed in commit e94e095c along with the rest of that
cluster). It is a fresh, self-contained module rather than a revival: the
original imported `ModelCapabilities`/`RequiredCapabilities` from
`schemas/router.py`, which was deleted separately and would otherwise have to
be resurrected just for its types.

Key properties:
  * `extract_required_capabilities()` is PURE and deterministic — no I/O,
    fully unit-testable.
  * Vision detection recognizes both OpenAI-style (`type: "image_url"`) and
    Anthropic-style (`type: "image"`) content blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RequiredCapabilities:
    """What one chat request needs from a model."""

    # Subset of {"tools", "vision"} — feeds directly into
    # get_candidate_models(required_capabilities=...).
    capability_names: frozenset[str] = field(default_factory=frozenset)
    needs_json: bool = False
    estimated_input_tokens: int = 0
    max_cost_per_1k: float | None = None


def _has_image_content(messages: list[dict[str, Any]] | None) -> bool:
    """True if any message contains an image content block.

    Recognizes OpenAI's `{"type": "image_url", ...}` and Anthropic's
    `{"type": "image", "source": {...}}` shapes.
    """
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("image", "image_url"):
                return True
    return False


def _estimate_input_tokens(messages: list[dict[str, Any]] | None) -> int:
    """Rough ~4-chars-per-token estimate, used only to gate long-context routing."""
    total_chars = 0
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total_chars += len(part["text"])
    return total_chars // 4


def extract_required_capabilities(
    messages: list[dict[str, Any]] | None,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    max_cost_per_1k: float | None = None,
) -> RequiredCapabilities:
    """Derive `RequiredCapabilities` from raw chat-request fields.

    Args:
        messages: the request's message list (OpenAI or Anthropic shape).
        tools: the request's tool/function definitions, if any.
        response_format: the request's response_format field, if any.
        max_cost_per_1k: caller-supplied cost ceiling to pass through.

    Returns:
        A `RequiredCapabilities` describing what a candidate model must support.
    """
    capability_names: set[str] = set()
    if tools:
        capability_names.add("tools")
    if _has_image_content(messages):
        capability_names.add("vision")

    return RequiredCapabilities(
        capability_names=frozenset(capability_names),
        needs_json=bool(response_format),
        estimated_input_tokens=_estimate_input_tokens(messages),
        max_cost_per_1k=max_cost_per_1k,
    )
