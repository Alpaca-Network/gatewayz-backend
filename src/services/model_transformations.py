"""
Model ID transformation logic for supporting multiple input formats.
Converts simplified "{org}/{model}" format to provider-specific formats.

This module handles transformations between user-friendly model IDs
(like "deepseek-ai/deepseek-v3") and provider-specific formats
(like "accounts/fireworks/models/deepseek-v3p1").

Mapping data is now stored in three Supabase tables and loaded into
an in-memory cache at startup via src/services/model_mappings_cache.py:
  - model_aliases           → replaces MODEL_ID_ALIASES
  - model_provider_mappings → replaces _MODEL_ID_MAPPINGS
  - model_routing_rules     → replaces MODEL_PROVIDER_OVERRIDES
"""

import logging

from src.services.model_mappings_cache import (
    get_aliases,
    get_provider_mappings,
    get_provider_native_values,
    get_routing_rules,
)

logger = logging.getLogger(__name__)


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
    "alibaba-cloud": "qwen/qwen-plus",
    "simplismart": "meta-llama/Llama-3.3-70B-Instruct",
}


# Shared helper for resolving aliases before any downstream routing logic runs.
# Normalization is idempotent: applying twice yields same result as once.
def apply_model_alias(model_id: str | None) -> str | None:
    if not model_id:
        return model_id

    alias_key = model_id.lower()
    aliases = get_aliases()
    canonical = aliases.get(alias_key)
    if canonical:
        # Guard against chaining: if the resolved value is itself an alias key that
        # maps to something different, resolve one more level so that calling this
        # function on the output always returns the same result as calling it once.
        second_key = canonical.lower()
        if second_key != alias_key:
            second_canonical = aliases.get(second_key)
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
# GEMINI_1_5_PRO and GEMINI_1_5_FLASH removed — Gemini 1.5 retired on Vertex AI
# Sep 2025; requests are now redirected to 2.5-flash/2.5-flash-lite in the mapping.
GEMINI_1_0_PRO = "gemini-1.0-pro"  # Kept for reference only; retired Sep 2025 on Vertex

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

    # Native providers: strip the provider prefix (e.g. "openai/gpt-4o" -> "gpt-4o")
    # These providers expect bare model IDs, not prefixed ones
    _STRIP_PREFIX_PROVIDERS = {
        "openai": "openai/",
        "anthropic": "anthropic/",
        "groq": "groq/",
        "cerebras": "cerebras/",
        "xai": "xai/",
        "nebius": "nebius/",
    }
    strip_prefix = _STRIP_PREFIX_PROVIDERS.get(provider_lower)
    if strip_prefix and model_id.startswith(strip_prefix):
        stripped = model_id[len(strip_prefix) :]
        logger.debug(f"Stripped provider prefix for {provider_lower}: '{model_id}' -> '{stripped}'")
        return stripped

    # xAI models use "x-ai/" prefix in the catalog but the xAI API expects bare model names
    # (e.g., "x-ai/grok-2-1212" -> "grok-2-1212")
    if provider_lower == "xai" and model_id.startswith("x-ai/"):
        stripped = model_id[5:]  # len("x-ai/") == 5
        logger.debug(f"Stripped x-ai/ prefix for xai: '{model_id}' -> '{stripped}'")
        return stripped

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


def get_model_id_mapping(provider: str) -> dict[str, str]:
    """
    Get simplified -> native format mapping for a specific provider.
    This maps user-friendly input to what the provider API expects.
    """
    return get_provider_mappings(provider)


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
    override = get_routing_rules().get(normalized_base)
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
        "near",
        "alpaca-network",
        "alibaba-cloud",
        "fal",
        "xai",
        "groq",
        "cloudflare-workers-ai",
        "morpheus",
        "simplismart",
    ]:
        mapping = get_provider_mappings(provider)
        if model_id in mapping:
            logger.info(f"Detected provider '{provider}' for model '{model_id}'")
            return provider

        # Also check the values (native formats) using pre-built set for O(1) lookup
        values_set = get_provider_native_values(provider)
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

        # Moonshot (Kimi) models (e.g., "moonshot/kimi-k2.6", "moonshot/kimi-k3")
        if org == "moonshot":
            return "moonshot"

        # MiniMax models (e.g., "minimax/MiniMax-Text-01")
        if org == "minimax":
            return "minimax"

        # Cerebras models (e.g., "cerebras/llama-3.3-70b")
        if org == "cerebras":
            return "cerebras"

        # OpenRouter models (e.g., "openrouter/auto")
        if org == "openrouter":
            return "openrouter"

        # Morpheus models (e.g., "morpheus/llama-3.1-8b")
        if org == "morpheus":
            return "morpheus"

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

        # Mistral models — route to OpenRouter which carries the full mistralai catalog
        if org == "mistralai":
            logger.info(f"Routing '{model_id}' to openrouter (mistralai org prefix)")
            return "openrouter"

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

        # XAI models (e.g., "xai/grok-2" or "x-ai/grok-2-1212")
        if org in ("xai", "x-ai"):
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
