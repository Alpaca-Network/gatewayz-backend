"""User-memory management endpoints (Gatewayz One Phase 4).

Lets a user (or the frontend on their behalf) read/write their portable,
model-agnostic memory — the facts the context assembler injects into requests
when ``CONTEXT_ASSEMBLY_ENABLED`` is on. Authenticated by the caller's API key;
every operation is scoped to that user's id, so a key can only touch its own
memory. Mounted under ``/v1/user/memory``.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.db import user_memory as mem
from src.security.deps import get_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user/memory", tags=["User Memory"])


class MemoryIn(BaseModel):
    content: str = Field(..., min_length=1, max_length=4000)
    kind: str = Field("fact", max_length=64)
    salience: float = Field(0.5, ge=0.0, le=1.0)


@router.get("")
async def list_memory(user_id: int = Depends(get_user_id)):
    """List the caller's memory items (highest salience first)."""
    items = await asyncio.to_thread(mem.get_memories, user_id, 200, use_cache=False)
    return {"items": items, "count": len(items)}


@router.post("")
async def create_memory(body: MemoryIn, user_id: int = Depends(get_user_id)):
    """Add a memory item for the caller."""
    try:
        item = await asyncio.to_thread(
            mem.add_memory, user_id, body.content, kind=body.kind, salience=body.salience
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.warning("user_memory add failed for %s: %s", user_id, e)
        raise HTTPException(status_code=502, detail="could not store memory") from e
    return {"item": item}


@router.delete("/{memory_id}")
async def remove_memory(memory_id: int, user_id: int = Depends(get_user_id)):
    """Delete one of the caller's memory items."""
    deleted = await asyncio.to_thread(mem.delete_memory, memory_id, user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory item not found")
    return {"deleted": True}


@router.delete("")
async def clear_memory(user_id: int = Depends(get_user_id)):
    """Delete all of the caller's memory items."""
    count = await asyncio.to_thread(mem.clear_memories, user_id)
    return {"deleted_count": count}
