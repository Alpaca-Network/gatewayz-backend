"""
Model ID transformation logic for supporting multiple input formats.
Converts simplified "{org}/{model}" format to provider-specific formats.

This module handles transformations between user-friendly model IDs
(like "deepseek-ai/deepseek-v3") and provider-specific formats
(like "accounts/fireworks/models/deepseek-v3p1").
"""

import logging

from typing import Optional, List, Any, Tuple
logger = logging.getLogger(__name__)

MODEL_PROVIDER_OVERRIDES = {
    "katanemo/arch-router-1.5b": "huggingface",
}

# Gemini model name constants to reduce duplication
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


def transform_model_id(
    model_id: str,
    provider: str,
    use_multi_provider: bool = True,
    required_features: Optional[List[str]] = None,
) -> str:
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

    provider_slug = (provider or "").strip().lower()
    incoming_model_id = str(model_id).strip()

    registry = None
    try:
        from src.services.multi_provider_registry import get_registry

        registry = get_registry()
    except ImportError:
        registry = None
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning(
            "Error accessing multi-provider registry for transform: %s", exc
        )
        registry = None

    canonical_id: Optional[str] = None
    if registry:
        canonical_id = registry.lookup_canonical_id(incoming_model_id)
        if not canonical_id and provider_slug:
            canonical_id = registry.get_canonical_id_for_provider(
                provider_slug, incoming_model_id
            )

    if registry and canonical_id and provider_slug:
        provider_adapter = registry.get_canonical_provider(
            canonical_id, provider_slug
        )
        if provider_adapter and _provider_supports_features(
            provider_adapter, required_features
        ):
            native_id = provider_adapter.native_model_id or incoming_model_id
            logger.info(
                "Canonical transform: %s -> %s (provider: %s)",
                incoming_model_id,
                native_id,
                provider_slug,
            )
            return native_id.lower()

    if registry and use_multi_provider:
        lookup_keys = [incoming_model_id]
        if canonical_id:
            lookup_keys.append(canonical_id)

        for candidate in lookup_keys:
            if registry.has_model(candidate):
                model = registry.get_model(candidate)
                if not model:
                    continue
                provider_config = model.get_provider_by_name(provider_slug)
                if provider_config:
                    logger.info(
                        "Multi-provider transform fallback: %s -> %s (provider: %s)",
                        incoming_model_id,
                        provider_config.model_id,
                        provider_slug,
                    )
                    return provider_config.model_id.lower()

    normalized = incoming_model_id.lower()
    if incoming_model_id != normalized:
        logger.debug(
            "Normalized model ID to lowercase: '%s' -> '%s'",
            incoming_model_id,
            normalized,
        )

    return normalized


def _provider_supports_features(
    provider: Any, required_features: Optional[List[str]]
) -> bool:
    if not required_features:
        return True

    capabilities = getattr(provider, "capabilities", {}) or {}
    features = capabilities.get("features") or []
    return all(feature in features for feature in required_features)


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

    provider_slug = (provider or "").strip().lower()

    registry = None
    try:
        from src.services.multi_provider_registry import get_registry

        registry = get_registry()
    except ImportError:
        registry = None
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.debug("Registry lookup failed for simplified ID: %s", exc)
        registry = None

    if registry and provider_slug:
        canonical_id = registry.get_canonical_id_for_provider(provider_slug, native_id)
        if canonical_id:
            return canonical_id

        candidates = registry.get_providers_for_native_id(native_id)
        for canonical_candidate, adapter in candidates:
            if adapter.provider_slug == provider_slug:
                return canonical_candidate

    if provider_slug == "fireworks" and native_id.startswith("accounts/fireworks/models/"):
        return native_id.replace("accounts/fireworks/models/", "")

    return native_id


def detect_provider_from_model_id(
    model_id: str,
    preferred_provider: Optional[str] = None,
    required_features: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Try to detect which provider a model belongs to based on its ID.

    Now supports multi-provider models with automatic provider selection.

    Args:
        model_id: The model ID to analyze
        preferred_provider: Optional preferred provider (for multi-provider models)

    Returns:
        The detected provider name, or None if unable to detect
    """

    normalized_id = (model_id or "").strip()
    preferred = preferred_provider.lower() if preferred_provider else None
    normalized_lower = normalized_id.lower()

    registry = None
    try:
        from src.services.multi_provider_registry import get_registry

        registry = get_registry()
    except ImportError:
        registry = None
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning("Error checking multi-provider registry: %s", exc)
        registry = None

    candidates: List[Tuple[str, Any]] = []

    if registry:
        canonical_id = registry.lookup_canonical_id(normalized_id)

        if canonical_id:
            model = registry.get_canonical_model(canonical_id)
            if model:
                for adapter in model.providers.values():
                    native_id = (adapter.native_model_id or "").lower()
                    if native_id and native_id == normalized_lower:
                        logger.info(
                            "Provider detection matched native ID: %s -> %s",
                            normalized_id,
                            adapter.provider_slug,
                        )
                        return adapter.provider_slug

            selected = registry.select_canonical_provider(
                canonical_id,
                preferred_provider=preferred,
                required_features=required_features,
            )
            if selected:
                logger.info(
                    "Canonical provider detection: %s -> %s (features=%s)",
                    normalized_id,
                    selected.provider_slug,
                    required_features or [],
                )
                return selected.provider_slug

            if model:
                candidates = [
                    (canonical_id, adapter)
                    for adapter in model.providers.values()
                ]
        else:
            candidates = registry.get_providers_for_native_id(normalized_id)

        if candidates:
            if preferred:
                for cid, adapter in candidates:
                    if adapter.provider_slug == preferred and _provider_supports_features(
                        adapter, required_features
                    ):
                        logger.info(
                            "Provider detection honored preference: %s -> %s",
                            normalized_id,
                            adapter.provider_slug,
                        )
                        return adapter.provider_slug

            for cid, adapter in candidates:
                if _provider_supports_features(adapter, required_features):
                    logger.info(
                        "Provider detection fallback: %s -> %s (canonical=%s)",
                        normalized_id,
                        adapter.provider_slug,
                        cid,
                    )
                    return adapter.provider_slug

    # Apply explicit overrides as a last resort
    normalized_lower = normalized_id.lower()
    normalized_base = normalized_lower.split(":", 1)[0]
    override = MODEL_PROVIDER_OVERRIDES.get(normalized_base)
    if override:
        logger.info(f"Provider override for model '{model_id}': {override}")
        return override

    # Minimal fallback heuristics for provider-specific formats
    if normalized_lower.startswith("accounts/fireworks/models/"):
        return "fireworks"
    if normalized_lower.startswith("@") and not normalized_lower.startswith("@google/models/"):
        return "portkey"

    logger.debug(f"Could not detect provider for model '{model_id}'")
    return None
