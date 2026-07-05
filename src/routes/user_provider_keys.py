"""BYOK management API — Phase 5 of the direct-supply pivot.

Lets a customer register, list, and revoke their own upstream provider keys.
The plaintext key is accepted on write, encrypted at rest, and NEVER returned.
See docs/BUSINESS_PIVOT_DIRECT_SUPPLY.md.
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.db.user_provider_keys import (
    delete_provider_key,
    list_provider_keys,
    upsert_provider_key,
)
from src.security.deps import get_user_id
from src.services.gateway_registry import get_valid_gateway_values

logger = logging.getLogger(__name__)

router = APIRouter()


class ProviderKeyRequest(BaseModel):
    provider_slug: str = Field(..., description="Provider slug, e.g. 'deepinfra'")
    api_key: str = Field(..., min_length=8, description="Your upstream provider API key")


@router.post("/user/provider-keys", tags=["byok"])
async def add_provider_key(
    body: ProviderKeyRequest, user_id: int = Depends(get_user_id)
) -> dict[str, Any]:
    """Register (or replace) the caller's BYOK key for a provider."""
    slug = body.provider_slug.lower().strip()
    if slug not in get_valid_gateway_values() or slug == "all":
        raise HTTPException(status_code=400, detail=f"Unknown provider '{slug}'")
    try:
        record = upsert_provider_key(user_id, slug, body.api_key)
    except RuntimeError as e:
        # Encryption not configured — refuse rather than store plaintext.
        raise HTTPException(
            status_code=503, detail="Key storage unavailable (encryption not configured)"
        ) from e
    return {"success": True, "data": record}


@router.get("/user/provider-keys", tags=["byok"])
async def get_provider_keys(user_id: int = Depends(get_user_id)) -> dict[str, Any]:
    """List the caller's BYOK keys (masked — last 4 digits only)."""
    keys = list_provider_keys(user_id)
    return {"success": True, "data": keys, "count": len(keys)}


@router.delete("/user/provider-keys/{provider_slug}", tags=["byok"])
async def remove_provider_key(
    provider_slug: str, user_id: int = Depends(get_user_id)
) -> dict[str, Any]:
    """Revoke the caller's BYOK key for a provider."""
    removed = delete_provider_key(user_id, provider_slug.lower().strip())
    if not removed:
        raise HTTPException(status_code=404, detail="No BYOK key for that provider")
    return {"success": True, "provider_slug": provider_slug}
