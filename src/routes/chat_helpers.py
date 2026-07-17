"""Pure helper utilities for the chat route.

Extracted from ``src/routes/chat.py`` to shrink that module. These are
side-effect-free (or near-pure) utilities with no dependency on the chat
module's globals, provider functions, or request state. They are re-imported
into ``src/routes/chat.py`` so existing references (and any ``src.routes.chat.*``
patch points) keep resolving.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def validate_and_adjust_max_tokens(optional: dict, model: str) -> None:
    """
    Validate and adjust max_tokens for models with minimum token requirements.

    Google Gemini models require max_tokens >= 16. This function automatically
    adjusts the value if it's below the minimum to prevent API errors.

    Args:
        optional: Dictionary of optional parameters (modified in-place)
        model: The model ID being used
    """
    if "max_tokens" not in optional or optional["max_tokens"] is None:
        return

    model_lower = model.lower()

    # Check if this is a Gemini model that requires min tokens >= 16
    if "gemini" in model_lower or "google" in model_lower:
        min_tokens = 16
        if optional["max_tokens"] < min_tokens:
            logger.warning(
                f"Adjusting max_tokens from {optional['max_tokens']} to {min_tokens} "
                f"for Gemini model {model} (minimum requirement)"
            )
            optional["max_tokens"] = min_tokens


def mask_key(k: str) -> str:
    return f"...{k[-4:]}" if k and len(k) >= 4 else "****"


def is_free_model(model_id: str) -> bool:
    """Check if the model is a free model (OpenRouter free models end with :free suffix).

    Args:
        model_id: The model identifier (e.g., "google/gemini-2.0-flash-exp:free")

    Returns:
        True if the model is free, False otherwise
    """
    if not model_id:
        return False
    return model_id.endswith(":free")


async def _to_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)
