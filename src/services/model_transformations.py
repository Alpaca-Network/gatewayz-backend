"""
Model ID transformation logic for supporting multiple input formats.
Converts simplified "{org}/{model}" format to provider-specific formats.

This module handles transformations between user-friendly model IDs
(like "deepseek-ai/deepseek-v3") and provider-specific formats
(like "accounts/fireworks/models/deepseek-v3p1").
"""

import logging

logger = logging.getLogger(__name__)

MODEL_PROVIDER_OVERRIDES = {
    "katanemo/arch-router-1.5b": "huggingface",
    "zai-org/glm-4.6-fp8": "near",
    # Z.AI GLM models - route to Z.AI gateway
    "zai/glm-4.7": "zai",
    "zai/glm-4.6v": "zai",
    "zai/glm-4.5-air": "zai",
    "zai/glm-4.5": "zai",
    "glm-4.7": "zai",
    "glm-4.6v": "zai",
    "glm-4.5-air": "zai",
    "glm-4.5": "zai",
    # Featherless-only models - not available on OpenRouter
    "c10x/longwriter-qwen2.5-7b-instruct": "featherless",
    # Meta Llama model ID aliases (support both meta/ and meta-llama/)
    "meta/llama-3-8b-instruct": "openrouter",  # Alias maps to meta-llama/llama-3-8b-instruct
    # BFL/Black Forest Labs model ID aliases
    "bfl/flux-1-1-pro": "fal",  # Alias maps to black-forest-labs/flux-1.1-pro
    "bfl/flux-1.1-pro": "fal",  # Alternative spelling
    # DeepSeek models NOT available on Fireworks - route to OpenRouter instead
    # Fireworks ONLY has: deepseek-v3p1 (V3/V3.1) and deepseek-r1-0528 (R1)
    # All other DeepSeek models must go to OpenRouter
    # V3.2 - not on Fireworks
    "deepseek/deepseek-v3.2": "openrouter",
    "deepseek-ai/deepseek-v3.2": "openrouter",
    "deepseek-v3.2": "openrouter",
    # V2/V2.5 - older versions, not on Fireworks
    "deepseek/deepseek-v2": "openrouter",
    "deepseek-ai/deepseek-v2": "openrouter",
    "deepseek-v2": "openrouter",
    "deepseek/deepseek-v2.5": "openrouter",
    "deepseek-ai/deepseek-v2.5": "openrouter",
    "deepseek-v2.5": "openrouter",
    # DeepSeek Coder - not on Fireworks
    "deepseek/deepseek-coder": "openrouter",
    "deepseek-ai/deepseek-coder": "openrouter",
    "deepseek-coder": "openrouter",
    # DeepSeek Chat - the default chat model, on OpenRouter
    "deepseek/deepseek-chat": "openrouter",
    "deepseek-ai/deepseek-chat": "openrouter",
    "deepseek-chat": "openrouter",
    # DeepSeek Chat V3 variants - these are user-requested aliases that should route to OpenRouter
    # since "deepseek-chat" is the canonical OpenRouter model name for DeepSeek's latest chat model
    "deepseek/deepseek-chat-v3": "openrouter",
    "deepseek/deepseek-chat-v3.1": "openrouter",
    "deepseek/deepseek-chat-v3-0324": "openrouter",
    "deepseek-ai/deepseek-chat-v3": "openrouter",
    "deepseek-ai/deepseek-chat-v3.1": "openrouter",
    "deepseek-ai/deepseek-chat-v3-0324": "openrouter",
    "deepseek-chat-v3": "openrouter",
    "deepseek-chat-v3.1": "openrouter",
    "deepseek-chat-v3-0324": "openrouter",
    # Google Gemini models - route to Google Vertex AI by default
    # Gemini 3 series
    "gemini-3": "google-vertex",
    "gemini-3-flash": "google-vertex",
    "gemini-3-flash-preview": "google-vertex",
    "gemini-3-pro": "google-vertex",
    "gemini-3-pro-preview": "google-vertex",
    "google/gemini-3": "google-vertex",
    "google/gemini-3-flash": "google-vertex",
    "google/gemini-3-flash-preview": "google-vertex",
    "google/gemini-3-pro": "google-vertex",
    "google/gemini-3-pro-preview": "google-vertex",
    # Gemini 2.5 series
    "gemini-2.5": "google-vertex",
    "gemini-2.5-flash": "google-vertex",
    "gemini-2.5-flash-lite": "google-vertex",
    "gemini-2.5-flash-preview": "google-vertex",
    "gemini-2.5-flash-image": "google-vertex",
    "gemini-2.5-pro": "google-vertex",
    "gemini-2.5-pro-preview": "google-vertex",
    "google/gemini-2.5": "google-vertex",
    "google/gemini-2.5-flash": "google-vertex",
    "google/gemini-2.5-flash-lite": "google-vertex",
    "google/gemini-2.5-flash-preview": "google-vertex",
    "google/gemini-2.5-flash-image": "google-vertex",
    "google/gemini-2.5-pro": "google-vertex",
    "google/gemini-2.5-pro-preview": "google-vertex",
    # Gemini 2.0 series
    "gemini-2.0": "google-vertex",
    "gemini-2.0-flash": "google-vertex",
    "gemini-2.0-flash-thinking": "google-vertex",
    "gemini-2.0-flash-exp": "google-vertex",
    "gemini-2.0-pro": "google-vertex",
    "google/gemini-2.0": "google-vertex",
    "google/gemini-2.0-flash": "google-vertex",
    "google/gemini-2.0-flash-thinking": "google-vertex",
    "google/gemini-2.0-flash-exp": "google-vertex",
    "google/gemini-2.0-pro": "google-vertex",
    # Gemini 1.5 series (legacy)
    "gemini-1.5": "google-vertex",
    "gemini-1.5-flash": "google-vertex",
    "gemini-1.5-pro": "google-vertex",
    "google/gemini-1.5": "google-vertex",
    "google/gemini-1.5-flash": "google-vertex",
    "google/gemini-1.5-pro": "google-vertex",
    # Gemini 1.0 series
    "gemini-1.0": "google-vertex",
    "gemini-1.0-pro": "google-vertex",
    "gemini-1.0-pro-vision": "google-vertex",
    "google/gemini-1.0": "google-vertex",
    "google/gemini-1.0-pro": "google-vertex",
    "google/gemini-1.0-pro-vision": "google-vertex",
    # Generic Gemini (any variant not explicitly matched)
    "gemini": "google-vertex",
    "google/gemini": "google-vertex",
    # Note: Cerebras DOES support Llama models natively (3.1 and 3.3 series)
    # No override needed - let natural provider detection route to Cerebras
}

# Canonical aliases for commonly mistyped or reformatted model IDs.
# Keep keys in lowercase to simplify lookups.
MODEL_ID_ALIASES = {
    # OpenAI GPT models without org prefix - map to canonical openai/ prefix
    # This ensures proper provider routing (locks to OpenRouter) and failover behavior
    "gpt-4": "openai/gpt-4",
    "gpt-4-turbo": "openai/gpt-4-turbo",
    "gpt-4-turbo-preview": "openai/gpt-4-turbo-preview",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gpt-4o-mini-2024-07-18": "openai/gpt-4o-mini-2024-07-18",
    "gpt-4o-2024-05-13": "openai/gpt-4o-2024-05-13",
    "gpt-4o-2024-08-06": "openai/gpt-4o-2024-08-06",
    "gpt-4o-2024-11-20": "openai/gpt-4o-2024-11-20",
    "gpt-4-0125-preview": "openai/gpt-4-0125-preview",
    "gpt-4-1106-preview": "openai/gpt-4-1106-preview",
    "gpt-4-vision-preview": "openai/gpt-4-vision-preview",
    "gpt-3.5-turbo": "openai/gpt-3.5-turbo",
    "gpt-3.5-turbo-16k": "openai/gpt-3.5-turbo-16k",
    "gpt-3.5-turbo-0125": "openai/gpt-3.5-turbo-0125",
    "gpt-3.5-turbo-1106": "openai/gpt-3.5-turbo-1106",
    # GPT-5.1 variants (hyphen, underscore, missing org, etc.)
    "openai/gpt-5-1": "openai/gpt-5.1",
    "openai/gpt5-1": "openai/gpt-5.1",
    "openai/gpt5.1": "openai/gpt-5.1",
    "openai/gpt-5_1": "openai/gpt-5.1",
    "openai/gpt5_1": "openai/gpt-5.1",
    "gpt-5-1": "openai/gpt-5.1",
    "gpt5-1": "openai/gpt-5.1",
    "gpt-5_1": "openai/gpt-5.1",
    "gpt5_1": "openai/gpt-5.1",
    "gpt5.1": "openai/gpt-5.1",
    "gpt-5.1": "openai/gpt-5.1",
    # OpenAI o-series reasoning models (o1, o3, o4-mini)
    # o1 variants
    "o1": "openai/o1",
    "o1-pro": "openai/o1-pro",
    # o3 variants
    "o3": "openai/o3",
    "o3-mini": "openai/o3-mini",
    "o3-mini-high": "openai/o3-mini-high",
    "o3-pro": "openai/o3-pro",
    "o3-deep-research": "openai/o3-deep-research",
    # o4-mini variants
    "o4-mini": "openai/o4-mini",
    "o4-mini-high": "openai/o4-mini-high",
    "o4-mini-deep-research": "openai/o4-mini-deep-research",
    # Anthropic Claude models - aliases for version variants
    # This ensures proper provider routing (locks to OpenRouter) and failover behavior
    # Claude 3 base models
    "claude-3-opus": "anthropic/claude-3-opus",
    "claude-3-sonnet": "anthropic/claude-3-sonnet",
    "claude-3-haiku": "anthropic/claude-3-haiku",
    # Claude 3.5 models
    "claude-3.5-sonnet": "anthropic/claude-3.5-sonnet",
    "claude-3.5-haiku": "anthropic/claude-3.5-haiku",
    # Claude 3.7 Sonnet
    "claude-3.7-sonnet": "anthropic/claude-3.7-sonnet",
    # Claude 4 series - Opus variants
    "claude-opus-4": "anthropic/claude-opus-4",
    "claude-opus-4.1": "anthropic/claude-opus-4.1",
    "claude-opus-4.5": "anthropic/claude-opus-4.5",
    "opus-4": "anthropic/claude-opus-4",
    "opus-4.1": "anthropic/claude-opus-4.1",
    "opus-4.5": "anthropic/claude-opus-4.5",
    # Claude 4 series - Sonnet variants
    "claude-sonnet-4": "anthropic/claude-sonnet-4",
    "sonnet-4": "anthropic/claude-sonnet-4",
    # Claude 4.5 series - Sonnet variants (Anthropic native dated format used by Claude Code)
    "claude-sonnet-4-20250514": "anthropic/claude-sonnet-4.5",
    "claude-sonnet-4.5-20250514": "anthropic/claude-sonnet-4.5",
    "claude-4-5-sonnet-20250514": "anthropic/claude-sonnet-4.5",
    "claude-4.5-sonnet-20250514": "anthropic/claude-sonnet-4.5",
    "sonnet-4.5": "anthropic/claude-sonnet-4.5",
    "claude-sonnet-4.5": "anthropic/claude-sonnet-4.5",
    # Claude 4.5 series - Opus variants (Anthropic native dated format used by Claude Code)
    "claude-opus-4-20250514": "anthropic/claude-opus-4.5",
    "claude-opus-4.5-20250514": "anthropic/claude-opus-4.5",
    "claude-4-5-opus-20250514": "anthropic/claude-opus-4.5",
    "claude-4.5-opus-20250514": "anthropic/claude-opus-4.5",
    # Claude 3.5 series (Anthropic native dated formats)
    "claude-3-5-sonnet-20241022": "anthropic/claude-3.5-sonnet",
    "claude-3-5-haiku-20241022": "anthropic/claude-3.5-haiku",
    # Claude 4 series - Haiku variants
    "claude-haiku-4.5": "anthropic/claude-haiku-4.5",
    "haiku-4.5": "anthropic/claude-haiku-4.5",
    # DeepSeek R1 and V3 series - newest variants
    "deepseek-r1": "deepseek/deepseek-r1",
    "r1": "deepseek/deepseek-r1",
    "deepseek-v3.2": "deepseek/deepseek-v3.2",
    # Meta Llama 4 series
    "llama-4-scout": "meta-llama/llama-4-scout",
    "llama-4-maverick": "meta-llama/llama-4-maverick",
    # Google Gemini 3 series
    "gemini-3": "google/gemini-3-flash-preview",
    "gemini-3-flash": "google/gemini-3-flash-preview",
    "gemini-3-pro": "google/gemini-3-pro-preview",
    # XAI Grok 3 series
    "grok-3": "x-ai/grok-3",
    "grok-3-beta": "x-ai/grok-3-beta",
    "grok-3-mini": "x-ai/grok-3-mini",
    "grok-3-mini-beta": "x-ai/grok-3-mini-beta",
    # XAI Grok 4 series
    "grok-4": "x-ai/grok-4",
    "grok-4-fast": "x-ai/grok-4-fast",
    "grok-4.1-fast": "x-ai/grok-4.1-fast",
    # XAI Grok specialized models
    "grok-code-fast-1": "x-ai/grok-code-fast-1",
    # XAI Grok deprecated models (grok-beta was deprecated 2025-09-15, use grok-3)
    # Note: Map directly to canonical x-ai/ prefix (apply_model_alias now resolves one chain level)
    "grok-beta": "x-ai/grok-3",
    "xai/grok-beta": "x-ai/grok-3",
    "grok-vision-beta": "x-ai/grok-3",
    "xai/grok-vision-beta": "x-ai/grok-3",
    # Zhipu AI GLM models - z-ai/ prefix aliases (OpenRouter format)
    # GLM-4.7 doesn't exist, map to closest available version GLM-4-flash
    "z-ai/glm-4.7": "z-ai/glm-4-flash",
    "z-ai/glm-4-7": "z-ai/glm-4-flash",
    "z-ai/glm4.7": "z-ai/glm-4-flash",
    # Map z-ai/ prefixed GLM models to canonical OpenRouter IDs
    "z-ai/glm-4.5": "z-ai/glm-4-flash",
    "z-ai/glm-4.6": "z-ai/glm-4-flash",
    # Black Forest Labs FLUX model aliases - ensure consistent pricing lookup
    # Map dot variant to dash variant for pricing database consistency
    "bfl/flux-1.1-pro": "bfl/flux-1-1-pro",
    "bfl/flux1.1-pro": "bfl/flux-1-1-pro",
    "bfl/flux1-1-pro": "bfl/flux-1-1-pro",
}

# Provider-specific fallbacks for the OpenRouter auto model.
# When failover routes an OpenRouter-only model to another provider, we remap it
# to a widely available general-purpose chat model for that provider.
OPENROUTER_AUTO_FALLBACKS = {
    "cerebras": "llama-3.3-70b",
    "huggingface": "meta-llama/llama-3.3-70b",
    "hug": "meta-llama/llama-3.3-70b",
    "featherless": "meta-llama/llama-3.3-70b",
    "fireworks": "meta-llama/llama-3.3-70b",
    "together": "meta-llama/llama-3.3-70b",
    "google-vertex": "gemini-2.5-flash",  # Updated from retired gemini-1.5-pro
    "vercel-ai-gateway": "openai/gpt-4o-mini",
    "aihubmix": "openai/gpt-4o-mini",
    "anannas": "openai/gpt-4o-mini",
    "alibaba-cloud": "qwen/qwen-plus",
    "simplismart": "meta-llama/Llama-3.3-70B-Instruct",
}


# Shared helper for resolving aliases before any downstream routing logic runs.
# Normalization is idempotent: applying twice yields same result as once.
def apply_model_alias(model_id: str | None) -> str | None:
    if not model_id:
        return model_id

    alias_key = model_id.lower()
    canonical = MODEL_ID_ALIASES.get(alias_key)
    if canonical:
        # Guard against chaining: if the resolved value is itself an alias key that
        # maps to something different, resolve one more level so that calling this
        # function on the output always returns the same result as calling it once.
        second_key = canonical.lower()
        if second_key != alias_key:
            second_canonical = MODEL_ID_ALIASES.get(second_key)
            if second_canonical and second_canonical != canonical:
                logger.debug(
                    "Resolved chained model alias '%s' -> '%s' -> '%s'",
                    model_id,
                    canonical,
                    second_canonical,
                )
                return second_canonical
        logger.debug("Resolved model alias '%s' -> '%s'", model_id, canonical)
        return canonical
    return model_id


# Gemini model name constants to reduce duplication
GEMINI_3_FLASH_PREVIEW = "gemini-3-flash-preview"
GEMINI_2_5_FLASH_LITE_PREVIEW = "gemini-2.5-flash-lite-preview-09-2025"
GEMINI_2_5_FLASH_PREVIEW = "gemini-2.5-flash-preview-09-2025"
GEMINI_2_5_PRO_PREVIEW = "gemini-2.5-pro-preview-09-2025"
GEMINI_2_0_FLASH = "gemini-2.0-flash"
GEMINI_2_0_PRO = "gemini-2.0-pro"
GEMINI_1_5_PRO = "gemini-1.5-pro"
GEMINI_1_5_FLASH = "gemini-1.5-flash"
GEMINI_1_0_PRO = "gemini-1.0-pro"

# Claude model name constants to reduce duplication
CLAUDE_SONNET_4_5 = "anthropic/claude-sonnet-4.5"


def transform_model_id(model_id: str, provider: str, use_multi_provider: bool = True) -> str:
    """
    Transform model ID from simplified format to provider-specific format.

    Now supports multi-provider models - will automatically get the correct
    provider-specific model ID from the registry.

    NOTE: All model IDs are normalized to lowercase before being sent to providers
    to ensure compatibility. Fireworks requires lowercase, while other providers
    are case-insensitive, so lowercase works universally.

    Args:
        model_id: The input model ID (e.g., "deepseek-ai/deepseek-v3")
        provider: The target provider (e.g., "fireworks", "openrouter")
        use_multi_provider: Whether to check multi-provider registry first (default: True)

    Returns:
        The transformed model ID suitable for the provider's API (always lowercase)

    Examples:
        Input: "deepseek-ai/DeepSeek-V3", provider="fireworks"
        Output: "accounts/fireworks/models/deepseek-v3p1"

        Input: "meta-llama/Llama-3.3-70B", provider="fireworks"
        Output: "accounts/fireworks/models/llama-v3p3-70b-instruct"

        Input: "OpenAI/GPT-4", provider="openrouter"
        Output: "openai/gpt-4"
    """

    if not model_id:
        return model_id

    user_supplied_model_id = model_id
    model_id = apply_model_alias(model_id)
    if model_id != user_supplied_model_id:
        logger.info(
            "Applying model alias: '%s' -> '%s' before provider transformation",
            user_supplied_model_id,
            model_id,
        )

    provider_lower = (provider or "").lower()

    # Check multi-provider registry first (if enabled)
    if use_multi_provider:
        try:
            from src.services.multi_provider_registry import get_registry

            registry = get_registry()
            if registry.has_model(model_id):
                # Get provider-specific model ID from registry
                model = registry.get_model(model_id)
                if model:
                    provider_config = model.get_provider_by_name(provider)
                    if provider_config:
                        provider_model_id = provider_config.model_id
                        logger.info(
                            f"Multi-provider transform: {model_id} -> {provider_model_id} "
                            f"(provider: {provider})"
                        )
                        return provider_model_id.lower()
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"Error checking multi-provider registry for transform: {e}")

    requested_model_id = model_id

    # Remap OpenRouter auto selections when routed through other providers.
    if requested_model_id and requested_model_id.lower() == "openrouter/auto":
        if provider_lower != "openrouter":
            fallback_model = OPENROUTER_AUTO_FALLBACKS.get(provider_lower)
            if fallback_model:
                logger.info(
                    "Mapping 'openrouter/auto' to provider fallback '%s' for %s",
                    fallback_model,
                    provider_lower or "unknown",
                )
                model_id = fallback_model
            else:
                logger.warning(
                    "Provider '%s' does not support 'openrouter/auto' and lacks a fallback; "
                    "continuing with original ID",
                    provider_lower or "unknown",
                )

    # Normalize input to lowercase for case-insensitive matching
    # Store original for logging
    original_model_id = model_id
    model_id = model_id.lower()

    if original_model_id != model_id:
        logger.debug(f"Normalized model ID to lowercase: '{original_model_id}' -> '{model_id}'")

    # If already in full Fireworks path format, return as-is (already lowercase)
    if model_id.startswith("accounts/fireworks/models/"):
        logger.debug(f"Model ID already in Fireworks format: {model_id}")
        return model_id

    # If model starts with @, but is not a Google model, keep as-is
    # (@ prefix is used by some providers but Portkey has been removed)
    if model_id.startswith("@") and not model_id.startswith("@google/models/"):
        logger.debug(f"Model ID with @ prefix (non-Google): {model_id}")
        return model_id

    # Special handling for OpenRouter: strip 'openrouter/' prefix if present
    # EXCEPT for OpenRouter meta-models which need to keep the prefix
    OPENROUTER_META_MODELS = {"openrouter/auto", "openrouter/bodybuilder"}
    if provider_lower == "openrouter" and model_id.startswith("openrouter/"):
        # Don't strip the prefix from OpenRouter meta-models - they need the full ID
        if model_id not in OPENROUTER_META_MODELS:
            stripped = model_id[len("openrouter/") :]
            logger.info(
                f"Stripped 'openrouter/' prefix: '{model_id}' -> '{stripped}' for OpenRouter"
            )
            model_id = stripped
        else:
            logger.info(
                f"Preserving '{model_id}' - this OpenRouter meta-model requires the full ID"
            )

    # Special handling for Near: strip 'near/' prefix if present
    if provider_lower == "near" and model_id.startswith("near/"):
        stripped = model_id[len("near/") :]
        logger.info(f"Stripped 'near/' prefix: '{model_id}' -> '{stripped}' for Near")
        model_id = stripped

    # Special handling for AIMO: strip 'aimo/' prefix if present
    # AIMO models need to be in provider_pubkey:model_name format for actual API calls
    # The aimo_native_id field contains the correct format
    if provider_lower == "aimo" and model_id.startswith("aimo/"):
        stripped = model_id[len("aimo/") :]
        logger.info(f"Stripped 'aimo/' prefix: '{model_id}' -> '{stripped}' for AIMO")
        model_id = stripped

    # Special handling for Groq: strip 'groq/' prefix if present
    # Groq API expects just the model name without the provider prefix
    if provider_lower == "groq" and model_id.startswith("groq/"):
        stripped = model_id[len("groq/") :]
        logger.info(f"Stripped 'groq/' prefix: '{model_id}' -> '{stripped}' for Groq")
        model_id = stripped

    # Special handling for Morpheus: strip 'morpheus/' prefix if present
    # Morpheus API expects just the model name without the provider prefix
    if provider_lower == "morpheus" and model_id.startswith("morpheus/"):
        stripped = model_id[len("morpheus/") :]
        logger.info(f"Stripped 'morpheus/' prefix: '{model_id}' -> '{stripped}' for Morpheus")
        model_id = stripped

    # Special handling for Infron AI: strip 'onerouter/' prefix if present
    # Infron AI API expects just the model name without the provider prefix
    if provider_lower == "onerouter" and model_id.startswith("onerouter/"):
        stripped = model_id[len("onerouter/") :]
        logger.info(f"Stripped 'onerouter/' prefix: '{model_id}' -> '{stripped}' for Infron AI")
        model_id = stripped

    # Get the mapping for this provider
    mapping = get_model_id_mapping(provider_lower)

    # Check direct mapping first
    if model_id in mapping:
        transformed = mapping[model_id]
        logger.info(f"Transformed '{model_id}' to '{transformed}' for {provider}")
        return transformed

    # Check for partial matches (e.g., without org prefix)
    if "/" in model_id:
        _, model_name = model_id.split("/", 1)
        # Try without org prefix
        if model_name in mapping:
            transformed = mapping[model_name]
            logger.info(
                f"Transformed '{model_id}' to '{transformed}' for {provider} (matched by model name)"
            )
            return transformed

    # Check fuzzy matching for version variations
    normalized = normalize_model_name(model_id)
    for incoming, native in mapping.items():
        if normalize_model_name(incoming) == normalized:
            logger.info(f"Transformed '{model_id}' to '{native}' for {provider} (fuzzy match)")
            return native

    # Special handling for Fireworks - DO NOT naively construct model IDs
    # Previously this code tried to construct "accounts/fireworks/models/{model_name}"
    # for unknown models, but this often resulted in invalid model IDs like
    # "accounts/fireworks/models/deepseek-v3p2-speciale" which don't exist on Fireworks.
    # Instead, we now log a warning and pass through the model ID as-is.
    # This allows Fireworks to return a proper "model not found" error rather than
    # a confusing 404 for a model ID that was never valid in the first place.
    if provider_lower == "fireworks" and "/" in model_id:
        logger.warning(
            f"No explicit Fireworks mapping for model '{model_id}'. "
            f"The model may not be available on Fireworks. "
            f"Passing through as-is - Fireworks API will reject if model is not valid."
        )

    # If no transformation needed or found, return original
    logger.debug(f"No transformation for '{model_id}' with provider {provider}")
    return model_id


_MODEL_ID_MAPPINGS: dict[str, dict[str, str]] = {
    "fireworks": {
        # Full format with org
        "deepseek-ai/deepseek-v3": "accounts/fireworks/models/deepseek-v3p1",
        "deepseek-ai/deepseek-v3.1": "accounts/fireworks/models/deepseek-v3p1",
        "deepseek-ai/deepseek-v3p1": "accounts/fireworks/models/deepseek-v3p1",
        "deepseek-ai/deepseek-r1": "accounts/fireworks/models/deepseek-r1-0528",
        # Alternative "deepseek/" org prefix (common user input format)
        "deepseek/deepseek-v3": "accounts/fireworks/models/deepseek-v3p1",
        "deepseek/deepseek-v3.1": "accounts/fireworks/models/deepseek-v3p1",
        "deepseek/deepseek-v3p1": "accounts/fireworks/models/deepseek-v3p1",
        "deepseek/deepseek-r1": "accounts/fireworks/models/deepseek-r1-0528",
        # Llama models
        "meta-llama/llama-3.3-70b": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "meta-llama/llama-3.3-70b-instruct": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "meta-llama/llama-3.1-70b": "accounts/fireworks/models/llama-v3p1-70b-instruct",
        "meta-llama/llama-3.1-70b-instruct": "accounts/fireworks/models/llama-v3p1-70b-instruct",
        "meta-llama/llama-3.1-8b": "accounts/fireworks/models/llama-v3p1-8b-instruct",
        "meta-llama/llama-3.1-8b-instruct": "accounts/fireworks/models/llama-v3p1-8b-instruct",
        "meta-llama/llama-4-scout": "accounts/fireworks/models/llama4-scout-instruct-basic",
        "meta-llama/llama-4-maverick": "accounts/fireworks/models/llama4-maverick-instruct-basic",
        # Without org prefix (common shortcuts)
        "deepseek-v3": "accounts/fireworks/models/deepseek-v3p1",
        "deepseek-v3.1": "accounts/fireworks/models/deepseek-v3p1",
        "deepseek-v3p1": "accounts/fireworks/models/deepseek-v3p1",
        "deepseek-r1": "accounts/fireworks/models/deepseek-r1-0528",
        "llama-3.3-70b": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "llama-3.1-70b": "accounts/fireworks/models/llama-v3p1-70b-instruct",
        "llama-3.1-8b": "accounts/fireworks/models/llama-v3p1-8b-instruct",
        # Qwen models
        "qwen/qwen-2.5-32b": "accounts/fireworks/models/qwen2p5-vl-32b-instruct",
        "qwen/qwen-3-235b": "accounts/fireworks/models/qwen3-235b-a22b",
        "qwen/qwen-3-235b-instruct": "accounts/fireworks/models/qwen3-235b-a22b-instruct-2507",
        "qwen/qwen-3-235b-thinking": "accounts/fireworks/models/qwen3-235b-a22b-thinking-2507",
        "qwen/qwen-3-30b-thinking": "accounts/fireworks/models/qwen3-30b-a3b-thinking-2507",
        "qwen/qwen-3-coder-480b": "accounts/fireworks/models/qwen3-coder-480b-a35b-instruct",
        # Other models
        "moonshot-ai/kimi-k2": "accounts/fireworks/models/kimi-k2-instruct",
        "moonshot-ai/kimi-k2-instruct": "accounts/fireworks/models/kimi-k2-instruct",
        "zhipu-ai/glm-4.5": "accounts/fireworks/models/glm-4p5",
        "gpt-oss/gpt-120b": "accounts/fireworks/models/gpt-oss-120b",
        "gpt-oss/gpt-20b": "accounts/fireworks/models/gpt-oss-20b",
    },
    "openrouter": {
        # OpenRouter already uses org/model format, so mostly pass-through
        # But support common variations
        "openai/gpt-4": "openai/gpt-4",
        "openai/gpt-4-turbo": "openai/gpt-4-turbo",
        "openai/gpt-3.5-turbo": "openai/gpt-3.5-turbo",
        # Claude 3 models - OpenRouter expects base model IDs without date suffixes
        "anthropic/claude-3-opus": "anthropic/claude-3-opus",
        "anthropic/claude-3-sonnet": "anthropic/claude-3-sonnet",
        "anthropic/claude-3-haiku": "anthropic/claude-3-haiku",
        # Claude 3.5 models
        "anthropic/claude-3.5-sonnet": "anthropic/claude-3.5-sonnet",
        "anthropic/claude-3.5-haiku": "anthropic/claude-3.5-haiku",
        "claude-3.5-sonnet": "anthropic/claude-3.5-sonnet",
        "claude-3.5-haiku": "anthropic/claude-3.5-haiku",
        # Claude 3.7 Sonnet
        "anthropic/claude-3.7-sonnet": "anthropic/claude-3.7-sonnet",
        "claude-3.7-sonnet": "anthropic/claude-3.7-sonnet",
        # Claude 4 series
        "anthropic/claude-sonnet-4": "anthropic/claude-sonnet-4",
        "anthropic/claude-opus-4": "anthropic/claude-opus-4",
        "anthropic/claude-opus-4.1": "anthropic/claude-opus-4.1",
        "claude-sonnet-4": "anthropic/claude-sonnet-4",
        "claude-opus-4": "anthropic/claude-opus-4",
        "claude-opus-4.1": "anthropic/claude-opus-4.1",
        # Claude 4.5 series
        "anthropic/claude-sonnet-4.5": CLAUDE_SONNET_4_5,
        "anthropic/claude-opus-4.5": "anthropic/claude-opus-4.5",
        "anthropic/claude-haiku-4.5": "anthropic/claude-haiku-4.5",
        "anthropic/claude-4.5-sonnet": CLAUDE_SONNET_4_5,
        "anthropic/claude-4.5-sonnet-20250929": CLAUDE_SONNET_4_5,
        "claude-sonnet-4.5": CLAUDE_SONNET_4_5,
        "claude-sonnet-4-5-20250929": CLAUDE_SONNET_4_5,
        "claude-opus-4.5": "anthropic/claude-opus-4.5",
        "claude-haiku-4.5": "anthropic/claude-haiku-4.5",
        # Claude Code native format (Anthropic API dated model IDs)
        "claude-sonnet-4-20250514": CLAUDE_SONNET_4_5,
        "claude-sonnet-4.5-20250514": CLAUDE_SONNET_4_5,
        "claude-4-5-sonnet-20250514": CLAUDE_SONNET_4_5,
        "claude-4.5-sonnet-20250514": CLAUDE_SONNET_4_5,
        "claude-opus-4-20250514": "anthropic/claude-opus-4.5",
        "claude-opus-4.5-20250514": "anthropic/claude-opus-4.5",
        "claude-4-5-opus-20250514": "anthropic/claude-opus-4.5",
        "claude-4.5-opus-20250514": "anthropic/claude-opus-4.5",
        # Claude 3.5 series (Anthropic native dated formats)
        "claude-3-5-sonnet-20241022": "anthropic/claude-3.5-sonnet",
        "claude-3-5-haiku-20241022": "anthropic/claude-3.5-haiku",
        # Google Gemini models on OpenRouter
        "google/gemini-3-flash-preview": "google/gemini-3-flash-preview",
        "google/gemini-3-pro-preview": "google/gemini-3-pro-preview",
        "gemini-3-flash-preview": "google/gemini-3-flash-preview",
        "gemini-3-pro-preview": "google/gemini-3-pro-preview",
        "gemini-3-flash": "google/gemini-3-flash-preview",
        "gemini-3-pro": "google/gemini-3-pro-preview",
        "google/gemini-2.5-flash": "google/gemini-2.5-flash",
        "google/gemini-2.5-pro": "google/gemini-2.5-pro",
        "gemini-2.5-flash": "google/gemini-2.5-flash",
        "gemini-2.5-pro": "google/gemini-2.5-pro",
        "google/gemini-2.0-flash-001": "google/gemini-2.0-flash-001",
        "gemini-2.0-flash": "google/gemini-2.0-flash-001",
        "google/gemini-flash-1.5": "google/gemini-flash-1.5",
        "google/gemini-pro-1.5": "google/gemini-pro-1.5",
        "gemini-1.5-flash": "google/gemini-flash-1.5",
        "gemini-1.5-pro": "google/gemini-pro-1.5",
        # Other models
        "meta-llama/llama-3.1-70b": "meta-llama/llama-3.1-70b-instruct",
        "deepseek-ai/deepseek-v3": "deepseek/deepseek-chat",
        # DeepSeek models routed to OpenRouter (not available on Fireworks)
        # V3.2 - not on Fireworks
        "deepseek/deepseek-v3.2": "deepseek/deepseek-chat",
        "deepseek-ai/deepseek-v3.2": "deepseek/deepseek-chat",
        "deepseek-v3.2": "deepseek/deepseek-chat",
        # V2/V2.5 - older versions
        "deepseek/deepseek-v2": "deepseek/deepseek-chat",
        "deepseek-ai/deepseek-v2": "deepseek/deepseek-chat",
        "deepseek-v2": "deepseek/deepseek-chat",
        "deepseek/deepseek-v2.5": "deepseek/deepseek-chat",
        "deepseek-ai/deepseek-v2.5": "deepseek/deepseek-chat",
        "deepseek-v2.5": "deepseek/deepseek-chat",
        # DeepSeek Coder
        "deepseek/deepseek-coder": "deepseek/deepseek-coder",
        "deepseek-ai/deepseek-coder": "deepseek/deepseek-coder",
        "deepseek-coder": "deepseek/deepseek-coder",
        # DeepSeek Chat (default chat model)
        "deepseek/deepseek-chat": "deepseek/deepseek-chat",
        "deepseek-ai/deepseek-chat": "deepseek/deepseek-chat",
        "deepseek-chat": "deepseek/deepseek-chat",
        # DeepSeek Chat V3 variants - map to the canonical deepseek/deepseek-chat on OpenRouter
        "deepseek/deepseek-chat-v3": "deepseek/deepseek-chat",
        "deepseek/deepseek-chat-v3.1": "deepseek/deepseek-chat",
        "deepseek/deepseek-chat-v3-0324": "deepseek/deepseek-chat",
        "deepseek-ai/deepseek-chat-v3": "deepseek/deepseek-chat",
        "deepseek-ai/deepseek-chat-v3.1": "deepseek/deepseek-chat",
        "deepseek-ai/deepseek-chat-v3-0324": "deepseek/deepseek-chat",
        "deepseek-chat-v3": "deepseek/deepseek-chat",
        "deepseek-chat-v3.1": "deepseek/deepseek-chat",
        "deepseek-chat-v3-0324": "deepseek/deepseek-chat",
        # Cerebras models explicitly routed through OpenRouter
        # (for users who request provider="openrouter" explicitly or failover scenarios)
        "cerebras/llama-3.3-70b": "meta-llama/llama-3.3-70b-instruct",
        "cerebras/llama-3.3-70b-instruct": "meta-llama/llama-3.3-70b-instruct",
        "cerebras/llama-3.1-70b": "meta-llama/llama-3.1-70b-instruct",
        "cerebras/llama-3.1-70b-instruct": "meta-llama/llama-3.1-70b-instruct",
    },
    "featherless": {
        # Featherless uses direct provider/model format
        # Most pass through directly
        "deepseek-ai/deepseek-v3": "deepseek-ai/DeepSeek-V3",
        "meta-llama/llama-3.3-70b": "meta-llama/Llama-3.3-70B-Instruct",
        "meta-llama/llama-3.1-70b": "meta-llama/Meta-Llama-3.1-70B-Instruct",
    },
    "together": {
        # Together AI uses specific naming
        "meta-llama/llama-3.3-70b": "meta-llama/Llama-3.3-70B-Instruct",
        "meta-llama/llama-3.1-70b": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
        "deepseek-ai/deepseek-v3": "deepseek-ai/DeepSeek-V3",
    },
    "huggingface": {
        # HuggingFace uses org/model format directly
        # Most models pass through as-is, but we map common variations
        "meta-llama/llama-3.3-70b": "meta-llama/Llama-3.3-70B-Instruct",
        "meta-llama/llama-3.3-70b-instruct": "meta-llama/Llama-3.3-70B-Instruct",
        "meta-llama/llama-3.1-70b": "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "meta-llama/llama-3.1-70b-instruct": "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "meta-llama/llama-3.1-8b": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "meta-llama/llama-3.1-8b-instruct": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        # DeepSeek models
        "deepseek-ai/deepseek-v3": "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/deepseek-r1": "deepseek-ai/DeepSeek-R1",
        # Qwen models
        "qwen/qwen-2.5-72b": "Qwen/Qwen2.5-72B-Instruct",
        "qwen/qwen-2.5-72b-instruct": "Qwen/Qwen2.5-72B-Instruct",
        "qwen/qwen-2.5-7b": "Qwen/Qwen2.5-7B-Instruct",
        "qwen/qwen-2.5-7b-instruct": "Qwen/Qwen2.5-7B-Instruct",
        # Mistral models
        "mistralai/mistral-7b": "mistralai/Mistral-7B-Instruct-v0.3",
        "mistralai/mistral-7b-instruct": "mistralai/Mistral-7B-Instruct-v0.3",
        "mistralai/mixtral-8x7b": "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "mistralai/mixtral-8x7b-instruct": "mistralai/Mixtral-8x7B-Instruct-v0.1",
        # Microsoft models
        "microsoft/phi-3": "microsoft/Phi-3-medium-4k-instruct",
        "microsoft/phi-3-medium": "microsoft/Phi-3-medium-4k-instruct",
        # Google Gemma models removed - they should use Google Vertex AI provider
    },
    "hug": {
        # Alias for huggingface - use same mappings
        # HuggingFace uses org/model format directly
        # Most models pass through as-is, but we map common variations
        "meta-llama/llama-3.3-70b": "meta-llama/Llama-3.3-70B-Instruct",
        "meta-llama/llama-3.3-70b-instruct": "meta-llama/Llama-3.3-70B-Instruct",
        "meta-llama/llama-3.1-70b": "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "meta-llama/llama-3.1-70b-instruct": "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "meta-llama/llama-3.1-8b": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "meta-llama/llama-3.1-8b-instruct": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        # DeepSeek models
        "deepseek-ai/deepseek-v3": "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/deepseek-r1": "deepseek-ai/DeepSeek-R1",
        # Qwen models
        "qwen/qwen-2.5-72b": "Qwen/Qwen2.5-72B-Instruct",
        "qwen/qwen-2.5-72b-instruct": "Qwen/Qwen2.5-72B-Instruct",
        "qwen/qwen-2.5-7b": "Qwen/Qwen2.5-7B-Instruct",
        "qwen/qwen-2.5-7b-instruct": "Qwen/Qwen2.5-7B-Instruct",
        # Mistral models
        "mistralai/mistral-7b": "mistralai/Mistral-7B-Instruct-v0.3",
        "mistralai/mistral-7b-instruct": "mistralai/Mistral-7B-Instruct-v0.3",
        "mistralai/mixtral-8x7b": "mistralai/Mixtral-8x7B-Instruct-v0.1",
        "mistralai/mixtral-8x7b-instruct": "mistralai/Mixtral-8x7B-Instruct-v0.1",
        # Microsoft models
        "microsoft/phi-3": "microsoft/Phi-3-medium-4k-instruct",
        "microsoft/phi-3-medium": "microsoft/Phi-3-medium-4k-instruct",
        # Google Gemma models removed - they should use Google Vertex AI provider
    },
    "chutes": {
        # Chutes uses org/model format directly
        # Most models pass through as-is from their catalog
        # Keep the exact format from the catalog for proper routing
    },
    "groq": {
        # Groq models use simple names without org prefix
        # The groq/ prefix is stripped in transform_model_id
        # Popular Groq models:
        "llama-3.3-70b-versatile": "llama-3.3-70b-versatile",
        "llama-3.1-70b-versatile": "llama-3.1-70b-versatile",
        "llama-3.1-8b-instant": "llama-3.1-8b-instant",
        "llama3-70b-8192": "llama3-70b-8192",
        "llama3-8b-8192": "llama3-8b-8192",
        "mixtral-8x7b-32768": "mixtral-8x7b-32768",
        "gemma2-9b-it": "gemma2-9b-it",
        "gemma-7b-it": "gemma-7b-it",
        # With groq/ prefix (stripped automatically)
        "groq/llama-3.3-70b-versatile": "llama-3.3-70b-versatile",
        "groq/llama-3.1-70b-versatile": "llama-3.1-70b-versatile",
        "groq/llama-3.1-8b-instant": "llama-3.1-8b-instant",
        "groq/mixtral-8x7b-32768": "mixtral-8x7b-32768",
        "groq/gemma2-9b-it": "gemma2-9b-it",
    },
    "google-vertex": {
        # Google Vertex AI models - simple names
        # Full resource names are constructed by the client
        # Gemini 3 models (latest - released Dec 17, 2025)
        "gemini-3-flash": GEMINI_3_FLASH_PREVIEW,
        "gemini-3-flash-preview": GEMINI_3_FLASH_PREVIEW,
        "google/gemini-3-flash": GEMINI_3_FLASH_PREVIEW,
        "google/gemini-3-flash-preview": GEMINI_3_FLASH_PREVIEW,
        "@google/models/gemini-3-flash": GEMINI_3_FLASH_PREVIEW,
        "@google/models/gemini-3-flash-preview": GEMINI_3_FLASH_PREVIEW,
        # Gemini 2.5 models (newest)
        # Flash Lite (stable GA version - use stable by default)
        "gemini-2.5-flash-lite": "gemini-2.5-flash-lite",  # Use stable GA version
        "google/gemini-2.5-flash-lite": "gemini-2.5-flash-lite",
        "@google/models/gemini-2.5-flash-lite": "gemini-2.5-flash-lite",
        # Preview version (only if explicitly requested)
        "gemini-2.5-flash-lite-preview-09-2025": GEMINI_2_5_FLASH_LITE_PREVIEW,
        "google/gemini-2.5-flash-lite-preview-09-2025": GEMINI_2_5_FLASH_LITE_PREVIEW,
        "@google/models/gemini-2.5-flash-lite-preview-09-2025": GEMINI_2_5_FLASH_LITE_PREVIEW,
        "gemini-2.5-flash-lite-preview-06-17": "gemini-2.5-flash-lite-preview-06-17",
        "google/gemini-2.5-flash-lite-preview-06-17": "gemini-2.5-flash-lite-preview-06-17",
        # Gemini 2.5 flash models (use stable GA version by default)
        "gemini-2.5-flash": "gemini-2.5-flash",  # Stable GA version for production
        "google/gemini-2.5-flash": "gemini-2.5-flash",
        "@google/models/gemini-2.5-flash": "gemini-2.5-flash",
        # Preview version (only if explicitly requested)
        "gemini-2.5-flash-preview-09-2025": GEMINI_2_5_FLASH_PREVIEW,
        "gemini-2.5-flash-preview": GEMINI_2_5_FLASH_PREVIEW,
        "google/gemini-2.5-flash-preview-09-2025": GEMINI_2_5_FLASH_PREVIEW,
        "@google/models/gemini-2.5-flash-preview-09-2025": GEMINI_2_5_FLASH_PREVIEW,
        # Image-specific models (GA version only - no preview version exists)
        "google/gemini-2.5-flash-image": "gemini-2.5-flash-image",
        "gemini-2.5-flash-image": "gemini-2.5-flash-image",
        "@google/models/gemini-2.5-flash-image": "gemini-2.5-flash-image",
        # Pro (use stable GA version by default)
        "gemini-2.5-pro": "gemini-2.5-pro",  # Use stable GA version
        "google/gemini-2.5-pro": "gemini-2.5-pro",
        "@google/models/gemini-2.5-pro": "gemini-2.5-pro",
        # Preview version (only if explicitly requested)
        "gemini-2.5-pro-preview-09-2025": GEMINI_2_5_PRO_PREVIEW,
        "google/gemini-2.5-pro-preview-09-2025": GEMINI_2_5_PRO_PREVIEW,
        "@google/models/gemini-2.5-pro-preview-09-2025": GEMINI_2_5_PRO_PREVIEW,
        "gemini-2.5-pro-preview": GEMINI_2_5_PRO_PREVIEW,
        "google/gemini-2.5-pro-preview": GEMINI_2_5_PRO_PREVIEW,
        "gemini-2.5-pro-preview-05-06": "gemini-2.5-pro-preview-05-06",
        "google/gemini-2.5-pro-preview-05-06": "gemini-2.5-pro-preview-05-06",
        # Gemini 2.0 models (stable versions)
        "gemini-2.0-flash": GEMINI_2_0_FLASH,
        "gemini-2.0-flash-thinking": "gemini-2.0-flash-thinking",
        "gemini-2.0-flash-001": "gemini-2.0-flash-001",
        "gemini-2.0-flash-lite-001": "gemini-2.0-flash-lite-001",
        "gemini-2.0-flash-exp": "gemini-2.0-flash-exp",
        "google/gemini-2.0-flash": GEMINI_2_0_FLASH,
        "google/gemini-2.0-flash-001": "gemini-2.0-flash-001",
        "google/gemini-2.0-flash-lite-001": "gemini-2.0-flash-lite-001",
        "google/gemini-2.0-flash-exp": "gemini-2.0-flash-exp",
        "@google/models/gemini-2.0-flash": GEMINI_2_0_FLASH,
        "gemini-2.0-pro": GEMINI_2_0_PRO,
        "gemini-2.0-pro-001": "gemini-2.0-pro-001",
        "google/gemini-2.0-pro": GEMINI_2_0_PRO,
        "@google/models/gemini-2.0-pro": GEMINI_2_0_PRO,
        # Gemini 1.5 models - RETIRED (April-September 2025)
        # These models are NO LONGER AVAILABLE on Google Vertex AI
        # Removed all google-vertex mappings to prevent 404 errors
        # Users must use OpenRouter provider directly for legacy Gemini 1.5 models
        # Gemini 1.0 models
        "gemini-1.0-pro": GEMINI_1_0_PRO,
        "gemini-1.0-pro-vision": "gemini-1.0-pro-vision",
        "google/gemini-1.0-pro": GEMINI_1_0_PRO,
        "@google/models/gemini-1.0-pro": GEMINI_1_0_PRO,
        # Aliases for convenience
        "gemini-2.0": GEMINI_2_0_FLASH,
        # Note: gemini-1.5 alias removed - model is retired on Vertex AI
        # Gemma models (open source models from Google)
        "google/gemma-2-9b": "gemma-2-9b-it",
        "google/gemma-2-9b-it": "gemma-2-9b-it",
        "google/gemma-2-27b-it": "gemma-2-27b-it",
        "google/gemma-3-4b-it": "gemma-3-4b-it",
        "google/gemma-3-12b-it": "gemma-3-12b-it",
        "google/gemma-3-27b-it": "gemma-3-27b-it",
        "google/gemma-3n-e2b-it": "gemma-3n-e2b-it",
        "google/gemma-3n-e4b-it": "gemma-3n-e4b-it",
        "gemma-2-9b-it": "gemma-2-9b-it",
        "gemma-2-27b-it": "gemma-2-27b-it",
        "gemma-3-4b-it": "gemma-3-4b-it",
        "gemma-3-12b-it": "gemma-3-12b-it",
        "gemma-3-27b-it": "gemma-3-27b-it",
        "gemma-3n-e2b-it": "gemma-3n-e2b-it",
        "gemma-3n-e4b-it": "gemma-3n-e4b-it",
    },
    "vercel-ai-gateway": {
        # Vercel AI Gateway uses standard model identifiers
        # The gateway automatically routes requests to the appropriate provider
        # Using pass-through format - any model ID is supported
        # Minimal mappings to avoid conflicts with other providers during auto-detection
    },
    "helicone": {
        # Helicone AI Gateway uses standard model identifiers
        # The gateway provides observability on top of standard provider APIs
        # Using pass-through format - any model ID is supported
        # Minimal mappings to avoid conflicts with other providers during auto-detection
    },
    "aihubmix": {
        # AiHubMix uses OpenAI-compatible model identifiers
        # Pass-through format - any model ID is supported
        # Minimal mappings to avoid conflicts with other providers during auto-detection
    },
    "anannas": {
        # Anannas uses OpenAI-compatible model identifiers
        # Pass-through format - any model ID is supported
        # Minimal mappings to avoid conflicts with other providers during auto-detection
    },
    "near": {
        # Near AI uses HuggingFace-style model naming with proper case
        # Maps lowercase input variants to actual NEAR model IDs
        # Reference: https://cloud.near.ai/models for current available models
        # DeepSeek models - only DeepSeek-V3.1 is currently available on Near AI
        "deepseek-ai/deepseek-v3": "deepseek-ai/DeepSeek-V3.1",  # Map v3 to v3.1 (only available)
        "deepseek-ai/deepseek-v3.1": "deepseek-ai/DeepSeek-V3.1",
        "deepseek-v3": "deepseek-ai/DeepSeek-V3.1",
        "deepseek-v3.1": "deepseek-ai/DeepSeek-V3.1",
        # GPT-OSS models - requires openai/ prefix
        "gpt-oss/gpt-oss-120b": "openai/gpt-oss-120b",
        "gpt-oss-120b": "openai/gpt-oss-120b",
        # Qwen models
        "qwen/qwen-2-72b": "Qwen/Qwen3-30B-A3B-Instruct-2507",  # Map old qwen-2-72b to qwen-3-30b
        "qwen-2-72b": "Qwen/Qwen3-30B-A3B-Instruct-2507",
        # Qwen3 models - proper case required
        "qwen/qwen-3-30b": "Qwen/Qwen3-30B-A3B-Instruct-2507",
        "qwen/qwen-3-30b-instruct": "Qwen/Qwen3-30B-A3B-Instruct-2507",
        "qwen-3-30b": "Qwen/Qwen3-30B-A3B-Instruct-2507",
        "qwen/qwen3-30b-a3b-instruct-2507": "Qwen/Qwen3-30B-A3B-Instruct-2507",
        "qwen3-30b-a3b-instruct-2507": "Qwen/Qwen3-30B-A3B-Instruct-2507",
        "qwen/qwen3-30b-a3b-thinking-2507": "Qwen/Qwen3-30B-A3B-Thinking-2507",
        "qwen3-30b-a3b-thinking-2507": "Qwen/Qwen3-30B-A3B-Thinking-2507",
        # GLM models from Zhipu AI
        "zai-org/glm-4.6-fp8": "zai-org/GLM-4.6",
        "zai-org/glm-4.6": "zai-org/GLM-4.6",
        "glm-4.6-fp8": "zai-org/GLM-4.6",
        "glm-4.6": "zai-org/GLM-4.6",
        # Note: Kimi-K2-Thinking model is NOT available on Near AI
        # Users requesting moonshotai/kimi-k2-thinking should use OpenRouter instead
        # Near AI only has DeepSeek, Qwen, and GLM models currently
    },
    "alpaca-network": {
        # Alpaca Network uses Anyscale infrastructure with DeepSeek models
        # Service: deepseek-v3-1 via https://deepseek-v3-1-b18ty.cld-kvytpjjrw13e2gvq.s.anyscaleuserdata.com
        # DeepSeek V3.1 models
        "deepseek-ai/deepseek-v3.1": "deepseek-v3-1",
        "deepseek-ai/deepseek-v3": "deepseek-v3-1",  # Map v3 to v3.1
        "deepseek/deepseek-v3.1": "deepseek-v3-1",
        "deepseek/deepseek-v3": "deepseek-v3-1",
        "deepseek-v3.1": "deepseek-v3-1",
        "deepseek-v3": "deepseek-v3-1",
        "deepseek-v3-1": "deepseek-v3-1",  # Direct service name
    },
    "alibaba-cloud": {
        # Alibaba Cloud / DashScope models
        # Uses OpenAI-compatible API with direct model IDs
        # Reference: https://dashscope.aliyuncs.com/compatible-mode/v1
        # Qwen commercial models
        "qwen/qwen-plus": "qwen-plus",
        "qwen/qwen-max": "qwen-max",
        "qwen/qwen-flash": "qwen-flash",
        "qwen-plus": "qwen-plus",
        "qwen-max": "qwen-max",
        "qwen-flash": "qwen-flash",
        # Qwen specialized models
        "qwen/qwq-plus": "qwq-plus",
        "qwen/qwen-long": "qwen-long",
        "qwen/qwen-omni": "qwen-omni",
        "qwen/qwen-vl": "qwen-vl",
        "qwen/qwen-math": "qwen-math",
        "qwen/qwen-mt": "qwen-mt",
        "qwen/qvq": "qvq",
        "qwq-plus": "qwq-plus",
        "qwen-long": "qwen-long",
        "qwen-omni": "qwen-omni",
        "qwen-vl": "qwen-vl",
        "qwen-math": "qwen-math",
        "qwen-mt": "qwen-mt",
        "qvq": "qvq",
        # Qwen Coder models
        "qwen/qwen-coder": "qwen-coder",
        "qwen-coder": "qwen-coder",
        # Qwen 2.5 Coder models (specific versions)
        "qwen/qwen-2.5-coder-32b-instruct": "qwen2.5-coder-32b-instruct",
        "qwen/qwen2.5-coder-32b-instruct": "qwen2.5-coder-32b-instruct",
        "qwen-2.5-coder-32b-instruct": "qwen2.5-coder-32b-instruct",
        "qwen2.5-coder-32b-instruct": "qwen2.5-coder-32b-instruct",
        "qwen/qwen-2.5-coder-32b": "qwen2.5-coder-32b-instruct",
        "qwen/qwen-2.5-coder-7b-instruct": "qwen2.5-coder-7b-instruct",
        "qwen/qwen2.5-coder-7b-instruct": "qwen2.5-coder-7b-instruct",
        "qwen-2.5-coder-7b-instruct": "qwen2.5-coder-7b-instruct",
        "qwen2.5-coder-7b-instruct": "qwen2.5-coder-7b-instruct",
        "qwen/qwen-2.5-coder-7b": "qwen2.5-coder-7b-instruct",
        "qwen/qwen-2.5-coder-14b-instruct": "qwen2.5-coder-14b-instruct",
        "qwen/qwen2.5-coder-14b-instruct": "qwen2.5-coder-14b-instruct",
        "qwen-2.5-coder-14b-instruct": "qwen2.5-coder-14b-instruct",
        "qwen2.5-coder-14b-instruct": "qwen2.5-coder-14b-instruct",
        "qwen/qwen-2.5-coder-14b": "qwen2.5-coder-14b-instruct",
        # Qwen reasoning models
        "qwen/qwq-32b-preview": "qwq-32b-preview",
        "qwq-32b-preview": "qwq-32b-preview",
        # Qwen thinking models
        "qwen/qwen-3-30b-a3b-thinking": "qwen-3-30b-a3b-thinking",
        "qwen/qwen-3-80b-a3b-thinking": "qwen-3-80b-a3b-thinking",
        "qwen-3-30b-a3b-thinking": "qwen-3-30b-a3b-thinking",
        "qwen-3-80b-a3b-thinking": "qwen-3-80b-a3b-thinking",
        # Qwen 3 series
        "qwen/qwen-3-30b": "qwen-3-30b-a3b-instruct",
        "qwen/qwen-3-80b": "qwen-3-80b-a3b-instruct",
        "qwen/qwen3-32b": "qwen-3-32b-a3b-instruct",
        "qwen3-30b": "qwen-3-30b-a3b-instruct",
        "qwen3-80b": "qwen-3-80b-a3b-instruct",
        "qwen3-32b": "qwen-3-32b-a3b-instruct",
        # Qwen 2.5 series
        "qwen/qwen-2.5-72b": "qwen-2.5-72b-instruct",
        "qwen/qwen-2.5-7b": "qwen-2.5-7b-instruct",
        "qwen-2.5-72b": "qwen-2.5-72b-instruct",
        "qwen-2.5-7b": "qwen-2.5-7b-instruct",
        # Qwen 2 series
        "qwen/qwen-2-72b": "qwen-2-72b-instruct",
        "qwen/qwen-2-7b": "qwen-2-7b-instruct",
        "qwen-2-72b": "qwen-2-72b-instruct",
        "qwen-2-7b": "qwen-2-7b-instruct",
        # Qwen 1.5 models
        "qwen/qwen-1.5-72b": "qwen-1.5-72b-chat",
        "qwen/qwen-1.5-14b": "qwen-1.5-14b-chat",
        "qwen-1.5-72b": "qwen-1.5-72b-chat",
        "qwen-1.5-14b": "qwen-1.5-14b-chat",
        # Alternative naming formats (shorthand)
        "qwen": "qwen-plus",  # Default to Plus for unspecified qwen
        "qwen-max-latest": "qwen-max",
        "qwen-plus-latest": "qwen-plus",
    },
    "clarifai": {
        # Clarifai OpenAI-compatible API requires full model URLs or abbreviated paths
        # Format: https://clarifai.com/{user_id}/{app_id}/models/{model_id}
        # Or abbreviated: {user_id}/{app_id}/models/{model_id}
        # See: https://docs.clarifai.com/compute/inference/open-ai/
        #
        # OpenAI models (via Clarifai)
        "openai/gpt-4o": "openai/chat-completion/models/gpt-4o",
        "openai/gpt-4-turbo": "openai/chat-completion/models/gpt-4-turbo",
        "openai/gpt-4": "openai/chat-completion/models/gpt-4",
        "gpt-4o": "openai/chat-completion/models/gpt-4o",
        "gpt-4-turbo": "openai/chat-completion/models/gpt-4-turbo",
        "gpt-4": "openai/chat-completion/models/gpt-4",
        # GPT-OSS (Clarifai's open-source GPT)
        "gpt-oss-120b": "openai/chat-completion/models/gpt-oss-120b",
        "openai/gpt-oss-120b": "openai/chat-completion/models/gpt-oss-120b",
        # Anthropic Claude models (via Clarifai)
        "anthropic/claude-3-opus": "anthropic/completion/models/claude-3-opus",
        "anthropic/claude-3.5-sonnet": "anthropic/completion/models/claude-3-5-sonnet",
        "anthropic/claude-3-sonnet": "anthropic/completion/models/claude-3-sonnet",
        "claude-3-opus": "anthropic/completion/models/claude-3-opus",
        "claude-3.5-sonnet": "anthropic/completion/models/claude-3-5-sonnet",
        "claude-3-sonnet": "anthropic/completion/models/claude-3-sonnet",
        # Meta Llama models (via Clarifai)
        "meta-llama/llama-3.1-70b": "meta/llama-2/models/llama-3-1-70b-instruct",
        "meta-llama/llama-3-70b": "meta/llama-2/models/llama-3-70b-instruct",
        "llama-3.1-70b": "meta/llama-2/models/llama-3-1-70b-instruct",
        "llama-3-70b": "meta/llama-2/models/llama-3-70b-instruct",
        # Mistral models (via Clarifai)
        "mistralai/mistral-7b": "mistralai/completion/models/mistral-7b-instruct",
        "mistralai/mixtral-8x7b": "mistralai/completion/models/mixtral-8x7b-instruct",
        "mistral-7b": "mistralai/completion/models/mistral-7b-instruct",
        "mixtral-8x7b": "mistralai/completion/models/mixtral-8x7b-instruct",
    },
    "xai": {
        # XAI Grok models - pass-through format
        # Models are referenced by their simple names (e.g., "grok-2", "grok-3")
        # Can also use xai/grok-* format
        # Note: grok-beta was deprecated on 2025-09-15, now redirected to grok-3
        "grok-beta": "grok-3",
        "grok-2": "grok-2",
        "grok-2-1212": "grok-2-1212",
        "grok-3": "grok-3",
        "grok-vision-beta": "grok-3",  # grok-vision-beta also deprecated
        "xai/grok-beta": "grok-3",
        "xai/grok-2": "grok-2",
        "xai/grok-2-1212": "grok-2-1212",
        "xai/grok-3": "grok-3",
        "xai/grok-vision-beta": "grok-3",
    },
    "cerebras": {
        # Cerebras API expects model IDs without the "cerebras/" prefix
        # Transform: "cerebras/llama-3.3-70b" → "llama-3.3-70b"
        "cerebras/llama-3.3-70b": "llama-3.3-70b",
        "cerebras/llama-3.3-70b-instruct": "llama-3.3-70b",
        "cerebras/llama-3.3-405b": "llama-3.3-405b",
        "cerebras/llama-3.1-70b": "llama3.1-70b",
        "cerebras/llama-3.1-70b-instruct": "llama3.1-70b",
        "cerebras/llama-3.1-8b": "llama3.1-8b",
        "cerebras/llama-3.1-8b-instruct": "llama3.1-8b",
        "cerebras/llama-3.1-405b": "llama3.1-405b",
        # Qwen models
        "cerebras/qwen-3-32b": "qwen-3-32b",
        "cerebras/qwen-3-32b-instruct": "qwen-3-32b",
        "cerebras/qwen-3-235b": "qwen-3-235b-a22b-instruct-2507",
        "cerebras/qwen-3-235b-instruct": "qwen-3-235b-a22b-instruct-2507",
        "qwen-3-32b": "qwen-3-32b",
        "qwen3-32b": "qwen-3-32b",
        "qwen/qwen3-32b": "qwen-3-32b",
        "qwen/qwen-3-235b": "qwen-3-235b-a22b-instruct-2507",
        "qwen-3-235b": "qwen-3-235b-a22b-instruct-2507",
        # Z.ai GLM models
        "cerebras/zai-glm-4.6": "zai-glm-4.6",
        "zai-glm-4.6": "zai-glm-4.6",
        "zai/glm-4.6": "zai-glm-4.6",
        # Support direct model names (passthrough)
        "llama-3.3-70b": "llama-3.3-70b",
        "llama-3.3-405b": "llama-3.3-405b",
        "llama3.1-70b": "llama3.1-70b",
        "llama3.1-8b": "llama3.1-8b",
        "llama3.1-405b": "llama3.1-405b",
    },
    "cloudflare-workers-ai": {
        # Cloudflare Workers AI uses @cf/ prefix for model names
        # OpenAI-compatible API: https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1
        # Documentation: https://developers.cloudflare.com/workers-ai/
        #
        # ======== OpenAI GPT-OSS models ========
        "openai/gpt-oss-120b": "@cf/openai/gpt-oss-120b",
        "openai/gpt-oss-20b": "@cf/openai/gpt-oss-20b",
        "gpt-oss-120b": "@cf/openai/gpt-oss-120b",
        "gpt-oss-20b": "@cf/openai/gpt-oss-20b",
        "gpt-oss/gpt-120b": "@cf/openai/gpt-oss-120b",
        "gpt-oss/gpt-20b": "@cf/openai/gpt-oss-20b",
        #
        # ======== Meta Llama 4 models ========
        "meta-llama/llama-4-scout-17b": "@cf/meta/llama-4-scout-17b-16e-instruct",
        "meta-llama/llama-4-scout-17b-16e-instruct": "@cf/meta/llama-4-scout-17b-16e-instruct",
        "llama-4-scout": "@cf/meta/llama-4-scout-17b-16e-instruct",
        #
        # ======== Meta Llama 3.3 models ========
        "meta-llama/llama-3.3-70b-instruct": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "meta-llama/llama-3.3-70b": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "llama-3.3-70b": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        #
        # ======== Meta Llama 3.2 models ========
        "meta-llama/llama-3.2-11b-vision-instruct": "@cf/meta/llama-3.2-11b-vision-instruct",
        "meta-llama/llama-3.2-3b-instruct": "@cf/meta/llama-3.2-3b-instruct",
        "meta-llama/llama-3.2-1b-instruct": "@cf/meta/llama-3.2-1b-instruct",
        "llama-3.2-11b-vision": "@cf/meta/llama-3.2-11b-vision-instruct",
        "llama-3.2-3b": "@cf/meta/llama-3.2-3b-instruct",
        "llama-3.2-1b": "@cf/meta/llama-3.2-1b-instruct",
        #
        # ======== Meta Llama 3.1 models ========
        "meta-llama/llama-3.1-70b-instruct": "@cf/meta/llama-3.1-70b-instruct",
        "meta-llama/llama-3.1-70b": "@cf/meta/llama-3.1-70b-instruct",
        "meta-llama/llama-3.1-8b-instruct": "@cf/meta/llama-3.1-8b-instruct",
        "meta-llama/llama-3.1-8b": "@cf/meta/llama-3.1-8b-instruct",
        "llama-3.1-70b": "@cf/meta/llama-3.1-70b-instruct",
        "llama-3.1-8b": "@cf/meta/llama-3.1-8b-instruct",
        "llama-3.1-8b-fast": "@cf/meta/llama-3.1-8b-instruct-fast",
        #
        # ======== Meta Llama 3 models ========
        "meta-llama/llama-3-8b-instruct": "@cf/meta/llama-3-8b-instruct",
        "meta-llama/llama-3-8b": "@cf/meta/llama-3-8b-instruct",
        "llama-3-8b": "@cf/meta/llama-3-8b-instruct",
        #
        # ======== Meta Llama 2 models (Legacy) ========
        "meta-llama/llama-2-7b-chat": "@cf/meta/llama-2-7b-chat-fp16",
        "llama-2-7b-chat": "@cf/meta/llama-2-7b-chat-fp16",
        "llama-2-7b": "@cf/meta/llama-2-7b-chat-fp16",
        #
        # ======== Meta Llama Guard ========
        "meta-llama/llama-guard-3-8b": "@cf/meta/llama-guard-3-8b",
        "llama-guard-3": "@cf/meta/llama-guard-3-8b",
        #
        # ======== Qwen models ========
        "qwen/qwen3-30b": "@cf/qwen/qwen3-30b-a3b-fp8",
        "qwen/qwq-32b": "@cf/qwen/qwq-32b",
        "qwen/qwen2.5-coder-32b-instruct": "@cf/qwen/qwen2.5-coder-32b-instruct",
        "qwen/qwen2.5-coder-32b": "@cf/qwen/qwen2.5-coder-32b-instruct",
        "qwq-32b": "@cf/qwen/qwq-32b",
        "qwen3-30b": "@cf/qwen/qwen3-30b-a3b-fp8",
        "qwen2.5-coder-32b": "@cf/qwen/qwen2.5-coder-32b-instruct",
        #
        # ======== Google Gemma models ========
        "google/gemma-3-12b-it": "@cf/google/gemma-3-12b-it",
        "google/gemma-7b-it": "@cf/google/gemma-7b-it",
        "google/gemma-2b-it": "@cf/google/gemma-2b-it-lora",
        "gemma-3-12b": "@cf/google/gemma-3-12b-it",
        "gemma-7b": "@cf/google/gemma-7b-it",
        "gemma-2b": "@cf/google/gemma-2b-it-lora",
        #
        # ======== Mistral models ========
        "mistralai/mistral-small-3.1-24b-instruct": "@cf/mistral/mistral-small-3.1-24b-instruct",
        "mistralai/mistral-7b-instruct-v0.2": "@cf/mistralai/mistral-7b-instruct-v0.2",
        "mistralai/mistral-7b-instruct-v0.1": "@cf/mistralai/mistral-7b-instruct-v0.1",
        "mistral-small-3.1-24b": "@cf/mistral/mistral-small-3.1-24b-instruct",
        "mistral-7b-instruct": "@cf/mistralai/mistral-7b-instruct-v0.2",
        "mistral-7b": "@cf/mistralai/mistral-7b-instruct-v0.2",
        #
        # ======== DeepSeek models ========
        "deepseek-ai/deepseek-r1-distill-qwen-32b": "@cf/deepseek/deepseek-r1-distill-qwen-32b",
        "deepseek-r1-distill-qwen-32b": "@cf/deepseek/deepseek-r1-distill-qwen-32b",
        "deepseek-r1-distill": "@cf/deepseek/deepseek-r1-distill-qwen-32b",
        #
        # ======== IBM Granite models ========
        "ibm/granite-4.0-h-micro": "@cf/ibm/granite-4.0-h-micro",
        "granite-4.0-micro": "@cf/ibm/granite-4.0-h-micro",
        #
        # ======== AI Singapore models ========
        "aisingapore/gemma-sea-lion-v4-27b-it": "@cf/aisingapore/gemma-sea-lion-v4-27b-it",
        "sea-lion-27b": "@cf/aisingapore/gemma-sea-lion-v4-27b-it",
        #
        # ======== NousResearch models ========
        "nousresearch/hermes-2-pro-mistral-7b": "@cf/nousresearch/hermes-2-pro-mistral-7b",
        "hermes-2-pro": "@cf/nousresearch/hermes-2-pro-mistral-7b",
        #
        # ======== Microsoft models ========
        "microsoft/phi-2": "@cf/microsoft/phi-2",
        "phi-2": "@cf/microsoft/phi-2",
        #
        # ======== Direct @cf/ model names (passthrough) ========
        "@cf/openai/gpt-oss-120b": "@cf/openai/gpt-oss-120b",
        "@cf/openai/gpt-oss-20b": "@cf/openai/gpt-oss-20b",
        "@cf/meta/llama-4-scout-17b-16e-instruct": "@cf/meta/llama-4-scout-17b-16e-instruct",
        "@cf/meta/llama-3.3-70b-instruct-fp8-fast": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "@cf/meta/llama-3.2-11b-vision-instruct": "@cf/meta/llama-3.2-11b-vision-instruct",
        "@cf/meta/llama-3.2-3b-instruct": "@cf/meta/llama-3.2-3b-instruct",
        "@cf/meta/llama-3.2-1b-instruct": "@cf/meta/llama-3.2-1b-instruct",
        "@cf/meta/llama-3.1-70b-instruct": "@cf/meta/llama-3.1-70b-instruct",
        "@cf/meta/llama-3.1-8b-instruct-fast": "@cf/meta/llama-3.1-8b-instruct-fast",
        "@cf/meta/llama-3.1-8b-instruct": "@cf/meta/llama-3.1-8b-instruct",
        "@cf/meta/llama-3.1-8b-instruct-fp8": "@cf/meta/llama-3.1-8b-instruct-fp8",
        "@cf/meta/llama-3.1-8b-instruct-awq": "@cf/meta/llama-3.1-8b-instruct-awq",
        "@cf/meta/meta-llama-3-8b-instruct": "@cf/meta/meta-llama-3-8b-instruct",
        "@cf/meta/llama-3-8b-instruct": "@cf/meta/llama-3-8b-instruct",
        "@cf/meta/llama-3-8b-instruct-awq": "@cf/meta/llama-3-8b-instruct-awq",
        "@cf/meta/llama-2-7b-chat-fp16": "@cf/meta/llama-2-7b-chat-fp16",
        "@cf/meta/llama-2-7b-chat-int8": "@cf/meta/llama-2-7b-chat-int8",
        "@cf/meta-llama/llama-2-7b-chat-hf-lora": "@cf/meta-llama/llama-2-7b-chat-hf-lora",
        "@cf/meta/llama-guard-3-8b": "@cf/meta/llama-guard-3-8b",
        "@cf/qwen/qwen3-30b-a3b-fp8": "@cf/qwen/qwen3-30b-a3b-fp8",
        "@cf/qwen/qwq-32b": "@cf/qwen/qwq-32b",
        "@cf/qwen/qwen2.5-coder-32b-instruct": "@cf/qwen/qwen2.5-coder-32b-instruct",
        "@cf/google/gemma-3-12b-it": "@cf/google/gemma-3-12b-it",
        "@cf/google/gemma-7b-it": "@cf/google/gemma-7b-it",
        "@cf/google/gemma-7b-it-lora": "@cf/google/gemma-7b-it-lora",
        "@cf/google/gemma-2b-it-lora": "@cf/google/gemma-2b-it-lora",
        "@cf/mistral/mistral-small-3.1-24b-instruct": "@cf/mistral/mistral-small-3.1-24b-instruct",
        "@cf/mistralai/mistral-7b-instruct-v0.2": "@cf/mistralai/mistral-7b-instruct-v0.2",
        "@cf/mistralai/mistral-7b-instruct-v0.2-lora": "@cf/mistralai/mistral-7b-instruct-v0.2-lora",
        "@cf/mistralai/mistral-7b-instruct-v0.1": "@cf/mistralai/mistral-7b-instruct-v0.1",
        "@cf/deepseek/deepseek-r1-distill-qwen-32b": "@cf/deepseek/deepseek-r1-distill-qwen-32b",
        "@cf/ibm/granite-4.0-h-micro": "@cf/ibm/granite-4.0-h-micro",
        "@cf/aisingapore/gemma-sea-lion-v4-27b-it": "@cf/aisingapore/gemma-sea-lion-v4-27b-it",
        "@cf/nousresearch/hermes-2-pro-mistral-7b": "@cf/nousresearch/hermes-2-pro-mistral-7b",
        "@cf/microsoft/phi-2": "@cf/microsoft/phi-2",
    },
    "morpheus": {
        # Morpheus AI Gateway uses OpenAI-compatible model identifiers
        # Models are dynamically fetched from the Morpheus API
        # Pass-through format - model IDs from the Morpheus /models endpoint
        # Strip morpheus/ prefix for actual API calls
        "morpheus/llama-3.1-8b": "llama-3.1-8b",
        "morpheus/llama-3.1-70b": "llama-3.1-70b",
        "morpheus/mistral-7b": "mistral-7b",
        "morpheus/deepseek-r1": "deepseek-r1",
        # Direct model names (passthrough)
        "llama-3.1-8b": "llama-3.1-8b",
        "llama-3.1-70b": "llama-3.1-70b",
        "mistral-7b": "mistral-7b",
        "deepseek-r1": "deepseek-r1",
    },
    "onerouter": {
        # Infron AI uses OpenAI-compatible model identifiers with @ versioning
        # Format: model-name@version (e.g., "claude-3-5-sonnet@20240620")
        # Models are dynamically fetched from Infron AI's /v1/models endpoint
        # Strip onerouter/ prefix for actual API calls
        "onerouter/claude-3-5-sonnet": "claude-3-5-sonnet@20240620",
        "onerouter/gpt-4": "gpt-4@latest",
        "onerouter/gpt-4o": "gpt-4o@latest",
        "onerouter/gpt-3.5-turbo": "gpt-3.5-turbo@latest",
        # Direct model names (passthrough with @ version suffix)
        "claude-3-5-sonnet@20240620": "claude-3-5-sonnet@20240620",
        "gpt-4@latest": "gpt-4@latest",
        "gpt-4o@latest": "gpt-4o@latest",
        "gpt-3.5-turbo@latest": "gpt-3.5-turbo@latest",
        # Models can also use simpler names - Infron AI handles routing
        "claude-3-5-sonnet": "claude-3-5-sonnet@20240620",
        "gpt-4": "gpt-4@latest",
        "gpt-4o": "gpt-4o@latest",
        "gpt-3.5-turbo": "gpt-3.5-turbo@latest",
    },
    "simplismart": {
        # Simplismart uses org/model format, supports various LLM models
        # Llama 3.1 models
        "simplismart/llama-3.1-8b": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "simplismart/llama-3.1-70b": "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "simplismart/llama-3.1-405b": "meta-llama/Meta-Llama-3.1-405B-Instruct",
        "llama-3.1-8b": "meta-llama/Meta-Llama-3.1-8B-Instruct",
        "llama-3.1-70b": "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "llama-3.1-405b": "meta-llama/Meta-Llama-3.1-405B-Instruct",
        # Llama 3.3 models
        "simplismart/llama-3.3-70b": "meta-llama/Llama-3.3-70B-Instruct",
        "llama-3.3-70b": "meta-llama/Llama-3.3-70B-Instruct",
        # Llama 4 models
        "simplismart/llama-4-maverick": "meta-llama/Llama-4-Maverick-17B-Instruct",
        "llama-4-maverick": "meta-llama/Llama-4-Maverick-17B-Instruct",
        # DeepSeek models
        "simplismart/deepseek-r1": "deepseek-ai/DeepSeek-R1",
        "simplismart/deepseek-v3": "deepseek-ai/DeepSeek-V3",
        "deepseek-r1": "deepseek-ai/DeepSeek-R1",
        "deepseek-v3": "deepseek-ai/DeepSeek-V3",
        # Gemma models
        "simplismart/gemma-3-1b": "google/gemma-3-1b-it",
        "simplismart/gemma-3-4b": "google/gemma-3-4b-it",
        "simplismart/gemma-3-27b": "google/gemma-3-27b-it",
        "gemma-3-1b": "google/gemma-3-1b-it",
        "gemma-3-4b": "google/gemma-3-4b-it",
        "gemma-3-27b": "google/gemma-3-27b-it",
        # Qwen models
        "simplismart/qwen-2.5-14b": "Qwen/Qwen2.5-14B-Instruct",
        "simplismart/qwen-2.5-32b": "Qwen/Qwen2.5-32B-Instruct",
        "qwen-2.5-14b": "Qwen/Qwen2.5-14B-Instruct",
        "qwen-2.5-32b": "Qwen/Qwen2.5-32B-Instruct",
        # Mixtral models
        "simplismart/mixtral-8x7b": "mistralai/Mixtral-8x7B-Instruct-v0.1-FP8",
        "mixtral-8x7b": "mistralai/Mixtral-8x7B-Instruct-v0.1-FP8",
        # Devstral
        "simplismart/devstral-small": "mistralai/Devstral-Small-2505",
        "devstral-small": "mistralai/Devstral-Small-2505",
    },
}


# Pre-built reverse lookup: for each provider, the set of native model IDs (values)
_MODEL_ID_VALUES_BY_PROVIDER: dict[str, set[str]] = {
    provider: set(mapping.values()) for provider, mapping in _MODEL_ID_MAPPINGS.items()
}


def get_model_id_mapping(provider: str) -> dict[str, str]:
    """
    Get simplified -> native format mapping for a specific provider.
    This maps user-friendly input to what the provider API expects.
    """
    return _MODEL_ID_MAPPINGS.get(provider, {})


def normalize_model_name(model_id: str) -> str:
    """
    Normalize model name for fuzzy matching.
    Handles common variations in model naming.
    """
    normalized = model_id.lower()

    # Remove org prefix if present
    if "/" in normalized:
        _, normalized = normalized.split("/", 1)

    # Normalize version numbers
    normalized = normalized.replace("v3p1", "v3")
    normalized = normalized.replace("v3.1", "v3")
    normalized = normalized.replace("3.3", "3p3")
    normalized = normalized.replace("3.1", "3p1")
    normalized = normalized.replace(".", "p")

    # Normalize separators
    normalized = normalized.replace("_", "-")

    # Remove common suffixes for matching
    for suffix in ["-instruct", "-chat", "-turbo", "-basic"]:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]

    return normalized


def get_simplified_model_id(native_id: str, provider: str) -> str:
    """
    Convert a native provider model ID back to simplified format.
    This is the reverse of transform_model_id.

    Args:
        native_id: The provider's native model ID
        provider: The provider name

    Returns:
        A simplified, user-friendly model ID

    Examples:
        Input: "accounts/fireworks/models/deepseek-v3p1", provider="fireworks"
        Output: "deepseek-ai/deepseek-v3"
    """

    # Get reverse mapping
    mapping = get_model_id_mapping(provider)
    reverse_mapping = {v: k for k, v in mapping.items() if "/" in k}  # Only keep ones with org

    if native_id in reverse_mapping:
        return reverse_mapping[native_id]

    # For Fireworks, try to construct a reasonable simplified version
    if provider == "fireworks" and native_id.startswith("accounts/fireworks/models/"):
        model_name = native_id.replace("accounts/fireworks/models/", "")

        # Try to guess the org based on model name
        if model_name.startswith("deepseek"):
            return f"deepseek-ai/{model_name.replace('p', '.')}"
        elif model_name.startswith("llama"):
            return f"meta-llama/{model_name}"
        elif model_name.startswith("qwen"):
            return f"qwen/{model_name}"
        elif model_name.startswith("kimi"):
            return f"moonshot-ai/{model_name}"
        elif model_name.startswith("glm"):
            return f"zhipu-ai/{model_name}"
        else:
            # Unknown org, just return the model name
            return model_name

    # Return as-is if no transformation found
    return native_id


def detect_provider_from_model_id(
    model_id: str, preferred_provider: str | None = None
) -> str | None:
    """
    Try to detect which provider a model belongs to based on its ID.

    Now supports multi-provider models with automatic provider selection.

    IMPORTANT - First-match behavior:
        Returns the first matching provider found. For models that exist on multiple
        providers, the result is determined by the detection priority order below:
          1. Multi-provider registry (uses selector with preferred_provider hint)
          2. Explicit MODEL_PROVIDER_OVERRIDES entries
          3. Hard-coded prefix/suffix rules (Fireworks accounts/ path, @cf/, Vertex, etc.)
          4. org-prefix rules (openai/, anthropic/, cerebras/, deepseek/, etc.)
          5. Ordered mapping-table scan (see provider list below — list order is priority)
          6. Pattern-based org-prefix fallbacks

        If a model is available on multiple providers and the wrong one is returned,
        either (a) pass `preferred_provider` to honour a caller-specified preference via
        the multi-provider registry, (b) add an entry to MODEL_PROVIDER_OVERRIDES, or
        (c) adjust the order of providers in the mapping scan list.

    Args:
        model_id: The model ID to analyze
        preferred_provider: Optional preferred provider hint (for multi-provider models).
            Only honoured when the model is present in the multi-provider registry.

    Returns:
        The detected provider name, or None if unable to detect
    """

    model_id = apply_model_alias(model_id)

    # Check multi-provider registry first
    try:
        from src.services.multi_provider_registry import get_registry

        registry = get_registry()
        if registry.has_model(model_id):
            # Model is in multi-provider registry
            from src.services.provider_selector import get_selector

            selector = get_selector()
            selected_provider = selector.registry.select_provider(
                model_id=model_id,
                preferred_provider=preferred_provider,
            )

            if selected_provider:
                logger.info(
                    f"Multi-provider model {model_id}: selected {selected_provider.name} "
                    f"(priority {selected_provider.priority})"
                )
                return selected_provider.name
    except ImportError:
        # Multi-provider modules not available, fall through to legacy detection
        pass
    except Exception as e:
        logger.warning(f"Error checking multi-provider registry: {e}")
        # Fall through to legacy detection

    # Apply explicit overrides first
    normalized_id = (model_id or "").lower()
    normalized_base = normalized_id.split(":", 1)[0]
    override = MODEL_PROVIDER_OVERRIDES.get(normalized_base)
    if override:
        logger.info(f"Provider override for model '{model_id}': {override}")
        return override

    # OpenRouter models with colon-based suffixes (e.g., :exacto, :free, :extended)
    # These are OpenRouter-specific model variants
    if ":" in model_id and "/" in model_id:
        # Models like "z-ai/glm-4.6:exacto", "google/gemini-2.0-flash-exp:free"
        suffix = model_id.split(":", 1)[1]
        if suffix in ["exacto", "free", "extended"]:
            logger.info(f"Detected OpenRouter model with :{suffix} suffix: {model_id}")
            return "openrouter"

    # Check if it's already in a provider-specific format
    if model_id.startswith("accounts/fireworks/models/"):
        return "fireworks"

    # Normalize to lowercase for consistency in all @ prefix checks
    normalized_model = model_id.lower()

    # Check for Cloudflare Workers AI models (use @cf/ prefix)
    # IMPORTANT: This must come before the general @ prefix check below
    if normalized_model.startswith("@cf/"):
        logger.info(f"Detected Cloudflare Workers AI model: {model_id}")
        return "cloudflare-workers-ai"

    # Check for Google Vertex AI models first (before Portkey check)
    if model_id.startswith("projects/") and "/models/" in model_id:
        return "google-vertex"
    if normalized_model.startswith("@google/models/") and any(
        pattern in normalized_model
        for pattern in ["gemini-3", "gemini-2.5", "gemini-2.0", "gemini-1.0"]
    ):
        # Patterns like "@google/models/gemini-3-flash" or "@google/models/gemini-2.5-flash"
        # Note: gemini-1.5 excluded - models are retired on Vertex AI
        return "google-vertex"
    if (
        any(
            pattern in normalized_model
            for pattern in ["gemini-3", "gemini-2.5", "gemini-2.0", "gemini-1.0"]
        )
        and "/" not in model_id
    ):
        # Simple patterns like "gemini-3-flash", "gemini-2.5-flash", "gemini-2.0-flash"
        # Note: gemini-1.5 excluded - models are retired on Vertex AI
        return "google-vertex"
    if model_id.startswith("google/") and "gemini" in normalized_model:
        # Patterns like "google/gemini-2.5-flash" or "google/gemini-2.0-flash-001"
        # These can go to either Vertex AI or OpenRouter
        # Check if Vertex AI credentials are available
        import os

        # Debug logging
        gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        gvc = os.environ.get("GOOGLE_VERTEX_CREDENTIALS_JSON")
        logger.info(
            f"[CREDENTIAL CHECK] model={model_id}, "
            f"GOOGLE_APPLICATION_CREDENTIALS={'SET' if gac else 'NOT SET'}, "
            f"GOOGLE_VERTEX_CREDENTIALS_JSON={'SET (len=' + str(len(gvc)) + ')' if gvc else 'NOT SET'}"
        )

        has_credentials = gac or gvc
        if has_credentials:
            logger.info(f"✅ Routing {model_id} to google-vertex (credentials available)")
            return "google-vertex"
        else:
            # No Vertex credentials, route to OpenRouter which supports google/ prefix
            logger.warning(f"⚠️ Routing {model_id} to openrouter (no Vertex credentials found)")
            return "openrouter"

    # Note: @ prefix used to indicate Portkey format, but Portkey has been removed
    # After Portkey removal, @ prefix models are now routed through OpenRouter
    # which supports multi-provider model format
    if model_id.startswith("@") and "/" in model_id:
        if not normalized_model.startswith("@google/models/"):
            # Route @ prefix models (e.g., "@anthropic/claude-3-sonnet") to OpenRouter
            logger.info(f"Routing @ prefix model {model_id} to openrouter (Portkey removed)")
            return "openrouter"

    # PRIORITY: Route OpenAI and Anthropic models to their native providers first
    # This must be checked BEFORE the mapping loop to ensure these models aren't
    # incorrectly routed to OpenRouter (which also has these models in its mapping)
    # Failover to OpenRouter is handled separately by provider_failover.py
    if "/" in model_id:
        prefix = model_id.split("/", 1)[0].lower()
        if prefix == "openai":
            logger.info(f"Routing '{model_id}' to native OpenAI provider")
            return "openai"
        if prefix == "anthropic":
            logger.info(f"Routing '{model_id}' to native Anthropic provider")
            return "anthropic"

    # Check all mappings to see if this model exists.
    # Returns first matching provider. For models on multiple providers, priority order matters.
    # The list below is the authoritative priority order: providers listed earlier take
    # precedence over providers listed later when a model ID appears in multiple mappings.
    # To change routing priority for a specific model, use MODEL_PROVIDER_OVERRIDES instead
    # of reordering this list, which affects all models.
    # IMPORTANT: cerebras is checked FIRST to prioritize cerebras/ prefix models
    for provider in [
        "cerebras",  # Check Cerebras first for cerebras/ prefix models
        "fireworks",
        "openrouter",
        "featherless",
        "together",
        "huggingface",
        "hug",
        "chutes",
        "google-vertex",
        "vercel-ai-gateway",
        "helicone",
        "aihubmix",
        "anannas",
        "near",
        "alpaca-network",
        "alibaba-cloud",
        "fal",
        "xai",
        "groq",
        "cloudflare-workers-ai",
        "morpheus",
        "onerouter",
        "simplismart",
    ]:
        mapping = _MODEL_ID_MAPPINGS.get(provider, {})
        if model_id in mapping:
            logger.info(f"Detected provider '{provider}' for model '{model_id}'")
            return provider

        # Also check the values (native formats) using pre-built set for O(1) lookup
        values_set = _MODEL_ID_VALUES_BY_PROVIDER.get(provider, set())
        if model_id in values_set:
            logger.info(f"Detected provider '{provider}' for native model '{model_id}'")
            return provider

    # Check by model patterns
    if "/" in model_id:
        org, model_name = model_id.split("/", 1)

        # Google Vertex models should only be routed if explicitly in the mapping above
        # OpenRouter also has google/ models (with :free suffix) that should stay with OpenRouter
        # So we comment this out to avoid routing OpenRouter's google/ models to Vertex AI
        # if org == "google":
        #     return "google-vertex"

        # Near AI models (e.g., "near/deepseek-ai/DeepSeek-V3", "near/deepseek-ai/DeepSeek-R1")
        if org == "near":
            return "near"

        # Cerebras models (e.g., "cerebras/llama-3.3-70b")
        if org == "cerebras":
            return "cerebras"

        # Anannas models (e.g., "anannas/openai/gpt-4o")
        if org == "anannas":
            return "anannas"

        # OpenRouter models (e.g., "openrouter/auto")
        if org == "openrouter":
            return "openrouter"

        # Helicone models (e.g., "helicone/gpt-4o-mini")
        if org == "helicone":
            return "helicone"

        # Morpheus models (e.g., "morpheus/llama-3.1-8b")
        if org == "morpheus":
            return "morpheus"

        # Infron AI models (e.g., "onerouter/claude-3-5-sonnet", "onerouter/gpt-4")
        if org == "onerouter":
            return "onerouter"

        # Z-AI / Zhipu AI GLM models (e.g., "z-ai/glm-4-flash", "z-ai/glm-4.6")
        # These are hosted on OpenRouter with the z-ai/ prefix
        if org == "z-ai" or org == "zai":
            logger.info(f"Detected OpenRouter provider for Zhipu AI model '{model_id}'")
            return "openrouter"

        # Alpaca Network models (e.g., "alpaca-network/deepseek-v3-1")
        if org == "alpaca-network" or org == "alpaca":
            return "alpaca-network"

        # Alibaba Cloud / Qwen models (e.g., "qwen/qwen-plus", "alibaba-cloud/qwen-max")
        # IMPORTANT: Check if this is a Cerebras-specific Qwen model first
        # Cerebras supports: qwen-3-32b, qwen-3-235b
        if org == "qwen" or org == "alibaba-cloud" or org == "alibaba":
            # Check if this specific qwen model is available on Cerebras
            cerebras_qwen_models = ["qwen-3-32b", "qwen3-32b", "qwen-3-235b"]
            model_base = (
                model_name.lower().replace("-instruct", "").replace("-a22b-instruct-2507", "")
            )
            if model_base in cerebras_qwen_models:
                logger.info(
                    f"Routing qwen model '{model_id}' to cerebras (model supported by both)"
                )
                return "cerebras"
            return "alibaba-cloud"

        # DeepSeek models are primarily on Fireworks in this system
        # Support both "deepseek-ai/" and "deepseek/" org prefixes
        if org in ("deepseek-ai", "deepseek") and "deepseek" in model_name.lower():
            return "fireworks"

        # OpenAI models go to native OpenAI provider first
        # Failover to OpenRouter is handled by provider_failover.py
        if org == "openai":
            return "openai"

        # Anthropic models go to native Anthropic provider first
        # Failover to OpenRouter is handled by provider_failover.py
        if org == "anthropic":
            return "anthropic"

        # Fal.ai models (e.g., "fal-ai/stable-diffusion-v15", "minimax/video-01")
        if org == "fal-ai" or org in [
            "fal",
            "minimax",
            "stabilityai",
            "hunyuan3d",
            "meshy",
            "tripo3d",
        ]:
            return "fal"

        # XAI models (e.g., "xai/grok-2")
        if org == "xai":
            return "xai"

        # Groq models (e.g., "groq/llama-3.3-70b-versatile", "groq/mixtral-8x7b-32768")
        if org == "groq":
            return "groq"

    # Check for grok models without org prefix (e.g., "grok-2", "grok-beta", "grok-vision-beta")
    if model_id.startswith("grok-"):
        logger.info(f"Detected XAI provider for Grok model '{model_id}'")
        return "xai"

    logger.debug(f"Could not detect provider for model '{model_id}'")
    return None
