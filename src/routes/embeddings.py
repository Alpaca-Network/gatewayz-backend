"""OpenAI-compatible embeddings endpoint (``POST /v1/embeddings``).

Continue uses an embeddings endpoint for codebase indexing, and several agent
tools use one for retrieval over a repo. Without this route those features have
to point at a second provider, which breaks the "one key" promise the wedge is
sold on.

This is a thin proxy: the request is forwarded to whichever provider owns the
requested embedding model, and the response is returned in OpenAI shape. There
is no gateway-side embedding model.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.config import Config
from src.security.deps import get_api_key
from src.services.connection_pool import get_http_client
from src.services.upstream.anonymize import scrub_upstream_kwargs

logger = logging.getLogger(__name__)

router = APIRouter(tags=["embeddings"])

# Provider slug -> (base_url, api-key attribute on Config).
EMBEDDING_PROVIDERS: dict[str, tuple[str, str]] = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "deepinfra": ("https://api.deepinfra.com/v1/openai", "DEEPINFRA_API_KEY"),
}

# Model-prefix routing. Most embedding model IDs are unambiguous about their
# provider; anything else must be explicitly namespaced by the caller.
MODEL_PREFIX_ROUTING: tuple[tuple[str, str], ...] = (
    ("text-embedding-", "openai"),
    ("openai/", "openai"),
    ("together/", "together"),
    ("deepinfra/", "deepinfra"),
    ("BAAI/", "deepinfra"),
    ("sentence-transformers/", "deepinfra"),
)


class EmbeddingsRequest(BaseModel):
    model: str = Field(..., description="Embedding model identifier")
    input: str | list[str] | list[int] | list[list[int]] = Field(
        ..., description="Text (or pre-tokenised input) to embed"
    )
    encoding_format: str | None = Field(None, description="'float' or 'base64'")
    dimensions: int | None = Field(None, ge=1, description="Output dimensionality")
    user: str | None = None

    class Config:
        extra = "allow"


def resolve_provider(model: str) -> tuple[str, str, str]:
    """Resolve ``model`` to ``(provider, base_url, api_key)``.

    Raises HTTPException(400) when the model is not routable, rather than
    guessing a provider — a wrong guess produces a confusing upstream 404
    instead of an actionable error.
    """
    lowered = (model or "").lower()

    provider: str | None = None
    for prefix, candidate in MODEL_PREFIX_ROUTING:
        if lowered.startswith(prefix.lower()):
            provider = candidate
            break

    if provider is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unroutable_embedding_model",
                "message": (
                    f"Cannot determine a provider for embedding model '{model}'. "
                    "Namespace it explicitly, e.g. 'openai/text-embedding-3-small'."
                ),
                "supported_prefixes": [p for p, _ in MODEL_PREFIX_ROUTING],
            },
        )

    base_url, key_attr = EMBEDDING_PROVIDERS[provider]
    api_key = getattr(Config, key_attr, None)
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "provider_not_configured",
                "message": f"Embeddings provider '{provider}' is not configured on this gateway.",
            },
        )
    return provider, base_url, api_key


def strip_provider_prefix(model: str, provider: str) -> str:
    """Remove the gateway's namespacing before forwarding upstream."""
    prefix = f"{provider}/"
    return model[len(prefix) :] if model.lower().startswith(prefix.lower()) else model


@router.post("/embeddings", tags=["embeddings"])
async def create_embeddings(
    req: EmbeddingsRequest,
    request: Request = None,
    api_key: str = Depends(get_api_key),
):
    """Create embeddings.

    Note: embeddings are billed by the upstream provider and are currently
    forwarded at cost — they are not metered through the credit ledger. This is
    deliberate for launch (embedding spend for a repo index is small relative to
    chat) and is flagged rather than hidden, because an unmetered path is
    exactly the kind of thing that becomes a surprise later.
    """
    provider, base_url, provider_key = resolve_provider(req.model)
    upstream_model = strip_provider_prefix(req.model, provider)

    payload: dict[str, Any] = {"model": upstream_model, "input": req.input}
    if req.encoding_format:
        payload["encoding_format"] = req.encoding_format
    if req.dimensions:
        payload["dimensions"] = req.dimensions
    # Upstream identity firewall (docs/security/ANONYMITY_THREAT_MODEL.md G1):
    # `req.user` is accepted for OpenAI-SDK compatibility but never forwarded --
    # this is a no-op today (the dict above never included it) and guards
    # against a future regression that adds it.
    payload = scrub_upstream_kwargs(payload)

    logger.info("Embeddings request: provider=%s, model=%s", provider, upstream_model)

    try:
        client = get_http_client()
        response = client.post(
            f"{base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {provider_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "Embeddings upstream error: provider=%s status=%s",
            provider,
            e.response.status_code,
        )
        raise HTTPException(
            status_code=e.response.status_code,
            detail={
                "error": "upstream_error",
                "provider": provider,
                "message": e.response.text[:500],
            },
        ) from e
    except Exception as e:
        logger.error("Embeddings request failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail={"error": "provider_unreachable", "provider": provider},
        ) from e

    data = response.json()
    data.setdefault("object", "list")
    data.setdefault("model", req.model)
    return data


@router.get("/embeddings/models", tags=["embeddings"])
async def list_embedding_models():
    """Embedding models this gateway can route, and which are usable right now."""
    available = []
    for provider, (_, key_attr) in EMBEDDING_PROVIDERS.items():
        available.append(
            {
                "provider": provider,
                "configured": bool(getattr(Config, key_attr, None)),
            }
        )
    return {
        "object": "list",
        "providers": available,
        "routing_prefixes": [p for p, _ in MODEL_PREFIX_ROUTING],
        "note": (
            "Embeddings are forwarded at cost and are not currently metered through "
            "the credit ledger."
        ),
        "timestamp": int(time.time()),
    }
