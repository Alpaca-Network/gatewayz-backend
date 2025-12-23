"""
API routes for user memory management.

Provides endpoints for:
- Listing user memories
- Getting a specific memory
- Deleting individual or all memories
- Getting memory statistics
- Manually triggering memory extraction
"""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks

from src.db.user_memory import (
    get_user_memories,
    get_memory_by_id,
    delete_user_memory,
    delete_all_user_memories,
    get_user_memory_stats,
    hard_delete_user_memory,
    MEMORY_CATEGORIES,
)
from src.db.chat_history import get_chat_session
from src.services.user_lookup_cache import get_user
from src.services.memory_service import memory_service
from src.schemas.memory import (
    MemoryResponse,
    MemoryListResponse,
    MemoryStatsResponse,
    DeleteMemoriesResponse,
    ExtractMemoriesRequest,
    ExtractMemoriesResponse,
)
from src.security.deps import get_api_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/memory", tags=["memory"])


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    api_key: str = Depends(get_api_key),
    category: str = Query(None, description="Filter by category"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    List all memories for the authenticated user.

    Optionally filter by category: preference, context, instruction, fact, name, project, general
    """
    start_time = time.time()
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Validate category if provided
        if category and category not in MEMORY_CATEGORIES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid category. Must be one of: {', '.join(MEMORY_CATEGORIES)}",
            )

        memories = get_user_memories(
            user_id=user["id"],
            category=category,
            limit=limit,
            offset=offset,
            active_only=True,
        )

        total_ms = (time.time() - start_time) * 1000
        logger.info(
            f"Retrieved {len(memories)} memories for user {user['id']} "
            f"(category={category}, time={total_ms:.1f}ms)"
        )

        return MemoryListResponse(
            success=True,
            data=memories,
            count=len(memories),
            message=f"Retrieved {len(memories)} memories",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list memories: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list memories: {str(e)}") from e


@router.get("/stats", response_model=MemoryStatsResponse)
async def get_stats(api_key: str = Depends(get_api_key)):
    """Get memory statistics for the authenticated user."""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        stats = get_user_memory_stats(user["id"])

        logger.info(f"Retrieved memory stats for user {user['id']}: {stats['total_memories']} total")

        return MemoryStatsResponse(
            success=True,
            stats=stats,
            message="Memory statistics retrieved successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get memory stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get memory stats: {str(e)}") from e


@router.get("/{memory_id}", response_model=MemoryResponse)
async def get_memory(memory_id: int, api_key: str = Depends(get_api_key)):
    """Get a specific memory by ID."""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        memory = get_memory_by_id(memory_id, user["id"])

        if not memory:
            raise HTTPException(status_code=404, detail="Memory not found")

        logger.info(f"Retrieved memory {memory_id} for user {user['id']}")

        return MemoryResponse(
            success=True,
            data=memory,
            message="Memory retrieved successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get memory: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get memory: {str(e)}") from e


@router.delete("/{memory_id}", response_model=DeleteMemoriesResponse)
async def delete_memory(
    memory_id: int,
    api_key: str = Depends(get_api_key),
    permanent: bool = Query(False, description="Permanently delete (cannot be undone)"),
):
    """
    Delete a specific memory.

    By default, performs a soft delete (can be recovered).
    Set permanent=true to permanently delete.
    """
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Verify memory exists and belongs to user
        memory = get_memory_by_id(memory_id, user["id"])
        if not memory:
            raise HTTPException(status_code=404, detail="Memory not found")

        if permanent:
            success = hard_delete_user_memory(memory_id, user["id"])
        else:
            success = delete_user_memory(memory_id, user["id"])

        if not success:
            raise HTTPException(status_code=500, detail="Failed to delete memory")

        logger.info(f"Deleted memory {memory_id} for user {user['id']} (permanent={permanent})")

        return DeleteMemoriesResponse(
            success=True,
            deleted_count=1,
            message="Memory deleted successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete memory: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete memory: {str(e)}") from e


@router.delete("", response_model=DeleteMemoriesResponse)
async def delete_all_memories(
    api_key: str = Depends(get_api_key),
    permanent: bool = Query(False, description="Permanently delete all (cannot be undone)"),
):
    """
    Delete all memories for the authenticated user.

    By default, performs a soft delete (can be recovered).
    Set permanent=true to permanently delete all memories.
    """
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        deleted_count = delete_all_user_memories(user["id"], hard_delete=permanent)

        logger.info(f"Deleted {deleted_count} memories for user {user['id']} (permanent={permanent})")

        return DeleteMemoriesResponse(
            success=True,
            deleted_count=deleted_count,
            message=f"Deleted {deleted_count} memories",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete all memories: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete all memories: {str(e)}") from e


@router.post("/extract", response_model=ExtractMemoriesResponse)
async def extract_memories(
    request: ExtractMemoriesRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(get_api_key),
):
    """
    Manually trigger memory extraction from a chat session.

    This will analyze the conversation and extract key facts/preferences.
    Normally this happens automatically after conversations.
    """
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Get the session with messages
        session = get_chat_session(request.session_id, user["id"])
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")

        messages = session.get("messages", [])
        if not messages:
            return ExtractMemoriesResponse(
                success=True,
                extracted_count=0,
                memories=[],
                message="No messages in session to extract from",
            )

        # Format messages for extraction
        formatted_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages
        ]

        # Extract memories
        extracted = await memory_service.extract_memories_from_messages(
            user_id=user["id"],
            session_id=request.session_id,
            messages=formatted_messages,
        )

        logger.info(
            f"Extracted {len(extracted)} memories from session {request.session_id} "
            f"for user {user['id']}"
        )

        return ExtractMemoriesResponse(
            success=True,
            extracted_count=len(extracted),
            memories=extracted,
            message=f"Extracted {len(extracted)} memories from conversation",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to extract memories: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to extract memories: {str(e)}") from e


@router.get("/categories", response_model=dict)
async def list_categories():
    """List available memory categories."""
    return {
        "categories": list(MEMORY_CATEGORIES),
        "descriptions": {
            "preference": "User preferences (e.g., prefers TypeScript)",
            "context": "Professional/personal context (e.g., works as backend engineer)",
            "instruction": "Explicit instructions (e.g., explain code step by step)",
            "fact": "Factual information (e.g., project uses PostgreSQL)",
            "name": "Names mentioned (e.g., user's name is Alex)",
            "project": "Project details (e.g., building e-commerce app)",
            "general": "General information",
        },
    }
