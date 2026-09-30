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
from src.services.billing.billing_ref import resolve_billing_ref
from src.services.billing.simple_metering import charge_credits, precheck_credits
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


# USD per 1M input tokens (embeddings have no output tokens). Keyed by the model
# name with the provider namespace stripped, lower-cased. Verified 2026-09-29
# against OpenAI (3-small $0.02, 3-large $0.13, ada-002 $0.10), DeepInfra
# (bge-large $0.01, bge-base $0.005, all-MiniLM-L6-v2 $0.005) and Together
# (multilingual-e5-large-instruct $0.02, together.ai/models page) list prices.
# Billed at provider cost x Config.EMBEDDING_MARGIN. Together currently offers no
# serverless embedding models (m2-bert etc. are "launching soon"; bge/gte have no
# published Together price), so those fall to the conservative fallback.
EMBEDDING_PRICE_PER_M_TOKENS: dict[str, float] = {
    "text-embedding-3-small": 0.02,
    "text-embedding-3-large": 0.13,
    "text-embedding-ada-002": 0.10,
    "intfloat/multilingual-e5-large-instruct": 0.02,
    "baai/bge-large-en-v1.5": 0.01,
    "baai/bge-base-en-v1.5": 0.005,
    "sentence-transformers/all-minilm-l6-v2": 0.005,
}
# Unknown models bill at the highest known rate: over-charging a little is
# safer than serving provider spend for free.
EMBEDDING_FALLBACK_PRICE_PER_M_TOKENS = 0.13


def embedding_cost(model: str, input_tokens: int) -> float:
    """USD cost for ``input_tokens`` of ``model`` (always > 0 for tokens > 0)."""
    name = (model or "").lower()
    for prefix in ("openai/", "together/", "deepinfra/"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    price = EMBEDDING_PRICE_PER_M_TOKENS.get(name, EMBEDDING_FALLBACK_PRICE_PER_M_TOKENS)
    margin = max(1.0, Config.EMBEDDING_MARGIN)
    return max(0, input_tokens) * price * margin / 1_000_000


def estimate_input_tokens(value: Any) -> int:
    """Conservative pre-call token estimate (~4 chars/token, 1 per pre-tokenised id)."""
    if isinstance(value, str):
        return max(1, len(value) // 4 + 1)
    if isinstance(value, list):
        return max(1, sum(estimate_input_tokens(v) if not isinstance(v, int) else 1 for v in value))
    return 1


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
    """Create embeddings, metered on input tokens through the credit ledger."""
    billing_ref = resolve_billing_ref(request)
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

    # Pre-check: positive balance covering the estimated cost, before any provider spend.
    est_tokens = estimate_input_tokens(req.input)
    user = await precheck_credits(api_key, embedding_cost(req.model, est_tokens))

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

    usage = data.get("usage") or {}
    billed_tokens = int(usage.get("prompt_tokens") or usage.get("total_tokens") or est_tokens)
    await charge_credits(
        api_key=api_key,
        user=user,
        cost=embedding_cost(req.model, billed_tokens),
        description=f"Embeddings - {req.model}",
        model=req.model,
        tokens=billed_tokens,
        billing_ref=billing_ref,
        metadata={"endpoint": "/v1/embeddings", "input_tokens": billed_tokens},
    )

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
        "note": ("Embeddings are metered per input token through the credit ledger."),
        "timestamp": int(time.time()),
    }
