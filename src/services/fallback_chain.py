"""
Fallback Chain Builder for Prompt Router.

Builds fallback chains where ALL models:
1. Satisfy the same capability requirements (no format drift)
2. Are from different providers (avoid correlated failures)
3. Have similar format behavior (JSON, tools adherence)

Target latency: < 0.2ms
"""

import logging

from src.schemas.router import ModelCapabilities, RequiredCapabilities

logger = logging.getLogger(__name__)

# Maximum fallbacks per tier
MAX_FALLBACKS_BY_TIER = {
    "small": 2,
    "medium": 3,
    "large": 5,
}

# Provider extraction from model ID
PROVIDER_PREFIXES = {
    "openai/": "openai",
    "anthropic/": "anthropic",
    "google/": "google",
    "deepseek/": "deepseek",
    "meta-llama/": "meta",
    "mistral/": "mistral",
    "cohere/": "cohere",
    "perplexity/": "perplexity",
    "together/": "together",
    "groq/": "groq",
    "fireworks/": "fireworks",
    "anyscale/": "anyscale",
}


def build_fallback_chain(
    primary_model: str,
    required_capabilities: RequiredCapabilities,
    healthy_candidates: list[str],
    capabilities_registry: dict[str, ModelCapabilities],
    tier: str = "small",
    excluded_models: list[str] | None = None,
) -> list[tuple[str, str]]:
    """
    Build a fallback chain for the primary model.

    All fallback models must:
    - Satisfy the same capability requirements
    - Be from different providers (when possible)
    - Have similar format behavior

    Target latency: < 0.2ms

    Args:
        primary_model: The primary selected model
        required_capabilities: Capabilities the request requires
        healthy_candidates: List of healthy model IDs
        capabilities_registry: Model capabilities lookup
        tier: Routing tier (affects max fallbacks)
        excluded_models: Models to exclude from fallback chain

    Returns:
        List of (model_id, provider) tuples for fallback
    """
    chain: list[tuple[str, str]] = []
    max_fallbacks = MAX_FALLBACKS_BY_TIER.get(tier, 2)

    # Track used providers to prefer diversity
    primary_provider = _get_provider(primary_model)
    used_providers = {primary_provider}

    # Get primary model capabilities for compatibility checking
    primary_caps = capabilities_registry.get(primary_model)

    # Build exclusion set
    exclude_set = {primary_model}
    if excluded_models:
        exclude_set.update(excluded_models)

    # First pass: different providers
    for candidate in healthy_candidates:
        if candidate in exclude_set:
            continue

        provider = _get_provider(candidate)
        if provider in used_providers:
            continue

        # Check capability compatibility
        candidate_caps = capabilities_registry.get(candidate)
        if not _is_compatible_fallback(
            candidate, candidate_caps, required_capabilities, primary_caps
        ):
            continue

        chain.append((candidate, provider))
        used_providers.add(provider)
        exclude_set.add(candidate)

        if len(chain) >= max_fallbacks:
            break

    # Second pass: allow same provider if we don't have enough fallbacks
    if len(chain) < max_fallbacks:
        for candidate in healthy_candidates:
            if candidate in exclude_set:
                continue

            provider = _get_provider(candidate)

            # Check capability compatibility
            candidate_caps = capabilities_registry.get(candidate)
            if not _is_compatible_fallback(
                candidate, candidate_caps, required_capabilities, primary_caps
            ):
                continue

            chain.append((candidate, provider))
            exclude_set.add(candidate)

            if len(chain) >= max_fallbacks:
                break

    return chain


def _get_provider(model_id: str) -> str:
    """Extract provider from model ID."""
    for prefix, provider in PROVIDER_PREFIXES.items():
        if model_id.startswith(prefix):
            return provider

    # Fallback: use first part before /
    if "/" in model_id:
        return model_id.split("/")[0]

    return "unknown"


def _is_compatible_fallback(
    candidate: str,
    candidate_caps: ModelCapabilities | None,
    required: RequiredCapabilities,
    primary_caps: ModelCapabilities | None,
) -> bool:
    """
    Check if candidate is a compatible fallback.

    Compatibility means:
    1. Satisfies all required capabilities
    2. Has similar format behavior to primary (if specified)
    """
    if not candidate_caps:
        # Unknown model - not safe for fallback
        return False

    # Must satisfy required capabilities
    if not candidate_caps.satisfies(required):
        return False

    # Check format compatibility with primary
    if primary_caps:
        # If primary has high tool adherence, fallback should too
        if primary_caps.tool_schema_adherence == "high":
            if candidate_caps.tool_schema_adherence != "high":
                logger.debug(f"Excluding {candidate} from fallback: tool adherence mismatch")
                return False

        # If primary supports JSON schema, prefer fallbacks that do too
        if primary_caps.json_schema and not candidate_caps.json_schema:
            # Allow but log - JSON mode might still work
            logger.debug(f"Fallback {candidate} lacks json_schema support (primary has it)")

    return True


def get_fallback_reason(
    candidate: str,
    candidate_caps: ModelCapabilities | None,
    required: RequiredCapabilities,
) -> str | None:
    """
    Get reason why a candidate was excluded from fallback chain.
    Useful for debugging.
    """
    if not candidate_caps:
        return "unknown_model"

    if required.needs_tools and not candidate_caps.tools:
        return "missing_tools"
    if required.needs_json and not candidate_caps.json_mode:
        return "missing_json_mode"
    if required.needs_json_schema and not candidate_caps.json_schema:
        return "missing_json_schema"
    if required.needs_vision and not candidate_caps.vision:
        return "missing_vision"
    if required.min_context_tokens > candidate_caps.max_context:
        return "context_too_small"
    if required.max_cost_per_1k and candidate_caps.cost_per_1k_input > required.max_cost_per_1k:
        return "cost_exceeds_limit"

    return None


def validate_fallback_chain(
    chain: list[tuple[str, str]],
    required: RequiredCapabilities,
    capabilities_registry: dict[str, ModelCapabilities],
) -> list[str]:
    """
    Validate that all models in fallback chain satisfy requirements.
    Returns list of invalid model IDs.
    """
    invalid = []
    for model_id, _ in chain:
        caps = capabilities_registry.get(model_id)
        if not caps or not caps.satisfies(required):
            invalid.append(model_id)
    return invalid
