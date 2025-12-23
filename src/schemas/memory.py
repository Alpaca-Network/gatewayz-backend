"""
Pydantic schemas for user memory endpoints.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# Valid memory categories
MemoryCategory = Literal[
    "preference",
    "context",
    "instruction",
    "fact",
    "name",
    "project",
    "general",
]


class UserMemory(BaseModel):
    """A single user memory entry."""

    id: int
    user_id: int
    category: MemoryCategory
    content: str
    source_session_id: int | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    is_active: bool = True
    access_count: int = 0
    last_accessed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CreateMemoryRequest(BaseModel):
    """Request to create a new memory."""

    category: MemoryCategory = "general"
    content: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)


class MemoryResponse(BaseModel):
    """Response containing a single memory."""

    success: bool
    data: UserMemory | None = None
    message: str | None = None


class MemoryListResponse(BaseModel):
    """Response containing a list of memories."""

    success: bool
    data: list[UserMemory]
    count: int
    message: str | None = None


class MemoryStatsResponse(BaseModel):
    """Response containing memory statistics."""

    success: bool
    stats: dict[str, Any]
    message: str | None = None


class DeleteMemoriesResponse(BaseModel):
    """Response for delete operations."""

    success: bool
    deleted_count: int
    message: str | None = None


class ExtractMemoriesRequest(BaseModel):
    """Request to trigger memory extraction from a session."""

    session_id: int


class ExtractMemoriesResponse(BaseModel):
    """Response for memory extraction."""

    success: bool
    extracted_count: int
    memories: list[UserMemory] = []
    message: str | None = None
