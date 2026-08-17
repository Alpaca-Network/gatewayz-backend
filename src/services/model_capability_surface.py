"""Public ``supported_parameters`` for catalog models.

Agent tools (and our own ``/models/[model]/pricing`` pages) need to know which
models accept tools, JSON schema output, vision input and prompt caching. That
information exists internally -- ``ModelCapabilities`` in ``schemas/router.py``
drives capability gating -- but was never exposed, so a client had to discover
support by sending a request and reading the error.

This module derives the OpenRouter-style ``supported_parameters`` array from
catalog metadata so the catalog can answer the question directly.

Honesty rule: absence of evidence is reported as absence of support. A model we
have no capability data for gets the universal baseline, not an optimistic
guess -- an agent that trusts a false "tools: true" fails at runtime, which is
worse than routing around the model.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Parameters every chat model accepts.
BASELINE_PARAMETERS: tuple[str, ...] = (
    "max_tokens",
    "temperature",
    "top_p",
    "stop",
)

TOOL_PARAMETERS: tuple[str, ...] = ("tools", "tool_choice", "parallel_tool_calls")

# Providers whose prompt caching the gateway can actually deliver. Keep this in
# sync with PROVIDER_CACHE_MULTIPLIERS in pricing/cache_pricing.py -- claiming
# cache support the billing layer cannot price would produce exactly the
# mispricing this whole change set exists to fix.
CACHE_CAPABLE_PROVIDERS: frozenset[str] = frozenset(
    {"anthropic", "openai", "google", "google-vertex", "deepseek"}
)

# Model families known to support tool calling, used when the catalog row has
# no explicit flag. Matched as substrings against the lowercased model ID.
KNOWN_TOOL_FAMILIES: tuple[str, ...] = (
    "claude",
    "gpt-3.5-turbo",
    "gpt-4",
    "gpt-5",
    "gpt-4o",
    "o1",
    "o3",
    "o4",
    "gemini",
    "mistral-large",
    "command-r",
    "llama-3.1",
    "llama-3.3",
    "llama-4",
    "qwen",
    "qwen2.5",
    "qwen3",
    "deepseek",
    "deepseek-v3",
    "deepseek-r1",
    "grok",
    # Families added when the provider roster moved to direct supply. Without
    # these, a live production catalog reported tools:false for models that
    # plainly support tool calling (Kimi K2 among them) — under-reporting fails
    # safe, but it hides a chunk of the catalog from agent tools that filter on
    # capability before sending a request.
    "kimi",
    "moonshot",
    "minimax",
    "glm",
    "mimo",
    "step",
)

KNOWN_VISION_FAMILIES: tuple[str, ...] = (
    "claude-3",
    "claude-sonnet",
    "claude-opus",
    "claude-haiku",
    "gpt-4o",
    "gpt-4-turbo",
    "gpt-5",
    "gemini",
    "llama-3.2-vision",
    "qwen2-vl",
    "pixtral",
    "grok-vision",
)


def _matches_family(model_id: str, families: tuple[str, ...]) -> bool:
    lowered = (model_id or "").lower()
    return any(family in lowered for family in families)


def _flag(model: dict[str, Any], *names: str) -> bool | None:
    """Read the first present boolean flag from a catalog row.

    Returns None when the row says nothing, so callers can distinguish
    "explicitly false" from "unknown" and fall back accordingly.
    """
    for name in names:
        if name in model and model[name] is not None:
            return bool(model[name])
    return None


def _model_identifier(model: dict[str, Any]) -> str:
    """The string identifier to pattern-match against, from either row shape.

    A public/API-formatted model dict (routes/catalog.py, pricing_lookup.py)
    carries a string ``id``. A raw ``models`` table row (models_catalog_db.py's
    ``get_candidate_models``, used by auto-routing) carries an integer PK in
    ``id`` and the actual model slug in ``provider_model_id`` instead --
    calling ``.lower()`` on the raw int used to raise unconditionally for
    every auto-routing candidate. Prefer the slug when present; str() the
    fallback defensively so a non-string ``id`` can never crash this again.
    """
    identifier = model.get("provider_model_id") or model.get("id") or ""
    return str(identifier)


def supports_tools(model: dict[str, Any]) -> bool:
    explicit = _flag(model, "supports_tools", "tools", "function_calling")
    if explicit is not None:
        return explicit
    # `supports_function_calling` is the raw catalog-sync column -- trust an
    # explicit True from it, but NOT an explicit False: that column's own
    # detection heuristic is narrower than KNOWN_TOOL_FAMILIES (that's why
    # the family list exists -- it was added specifically to rescue models,
    # e.g. Kimi/Moonshot/MiniMax/GLM/Mimo/Step, that this column under-reports
    # for). Short-circuiting on its False would silently reintroduce that
    # exact under-reporting bug for the auto-routing path.
    if model.get("supports_function_calling") is True:
        return True
    return _matches_family(_model_identifier(model), KNOWN_TOOL_FAMILIES)


def supports_vision(model: dict[str, Any]) -> bool:
    explicit = _flag(model, "supports_vision", "vision", "multimodal")
    if explicit is not None:
        return explicit
    return _matches_family(_model_identifier(model), KNOWN_VISION_FAMILIES)


def supports_reasoning(model: dict[str, Any]) -> bool:
    """Whether the model is a dedicated reasoning model (catalog `is_reasoning` flag).

    No family-matching fallback: unlike tools/vision, reasoning capability
    isn't reliably inferable from a model id, so an unflagged model reports
    unsupported rather than guessing (same honesty rule as the module docstring).
    """
    explicit = _flag(model, "is_reasoning", "supports_reasoning")
    return bool(explicit)


def supports_caching(model: dict[str, Any]) -> bool:
    """Whether the gateway can deliver -- and correctly bill -- prompt caching."""
    explicit = _flag(model, "supports_caching", "prompt_caching")
    if explicit is not None:
        return explicit
    metadata = model.get("metadata") if isinstance(model.get("metadata"), dict) else {}
    provider = (
        str(
            model.get("provider_slug")
            or model.get("source_gateway")
            or metadata.get("provider_slug")
            or metadata.get("source_gateway")
            or ""
        )
        .strip()
        .lower()
    )
    if provider in CACHE_CAPABLE_PROVIDERS:
        return True
    model_id = _model_identifier(model).lower()
    return any(p in model_id for p in CACHE_CAPABLE_PROVIDERS)


def build_supported_parameters(model: dict[str, Any]) -> list[str]:
    """The ``supported_parameters`` array for a catalog row."""
    params: list[str] = list(BASELINE_PARAMETERS)

    if supports_tools(model):
        params.extend(TOOL_PARAMETERS)

    # response_format is near-universal on OpenAI-compatible endpoints; models
    # with tool support reliably have it, and it is the practical signal
    # coding agents care about for structured edits.
    if supports_tools(model):
        params.append("response_format")

    if supports_caching(model):
        params.append("cache_control")

    return params


def enrich_model_with_capabilities(model: dict[str, Any]) -> dict[str, Any]:
    """Add capability fields to a catalog row, in place.

    Returns the same dict for convenient chaining with the pricing enrichment.
    """
    if not isinstance(model, dict):
        return model

    try:
        model["supported_parameters"] = build_supported_parameters(model)
        model["capabilities"] = {
            "tools": supports_tools(model),
            "vision": supports_vision(model),
            "prompt_caching": supports_caching(model),
            "streaming": True,
        }
    except Exception as e:
        # Capability metadata is a nice-to-have; never let it break the catalog.
        logger.warning("Could not derive capabilities for model %s: %s", model.get("id"), e)
    return model
