from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# Chat History Models
class ChatMessage(BaseModel):
    id: int | None = None
    session_id: int
    role: str  # 'user' or 'assistant'
    content: str
    model: str | None = None
    tokens: int | None = 0
    created_at: datetime | None = None


class ChatSession(BaseModel):
    id: int | None = None
    user_id: int
    title: str
    model: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    is_active: bool | None = True
    messages: list[ChatMessage] | None = []


class CreateChatSessionRequest(BaseModel):
    title: str | None = None
    model: str | None = None


class UpdateChatSessionRequest(BaseModel):
    title: str | None = None
    model: str | None = None


class ChatSessionResponse(BaseModel):
    success: bool
    data: ChatSession | None = None
    message: str | None = None


class ChatSessionsListResponse(BaseModel):
    success: bool
    data: list[ChatSession]
    count: int
    message: str | None = None


class ChatSessionStatsResponse(BaseModel):
    success: bool
    stats: dict[str, Any]
    message: str | None = None


class SearchChatSessionsRequest(BaseModel):
    query: str
    limit: int | None = 20


class SaveChatMessageRequest(BaseModel):
    role: str  # 'user' or 'assistant'
    content: str
    model: str | None = None
    tokens: int | None = 0
    created_at: str | None = None  # ISO datetime string from frontend


# Message Feedback Models
FeedbackType = Literal["thumbs_up", "thumbs_down", "regenerate"]


class MessageFeedback(BaseModel):
    id: int | None = None
    session_id: int | None = None
    message_id: int | None = None
    user_id: int
    feedback_type: FeedbackType
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = None
    model: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SaveMessageFeedbackRequest(BaseModel):
    session_id: int | None = None
    message_id: int | None = None
    feedback_type: FeedbackType
    rating: int | None = Field(default=None, ge=1, le=5)
    comment: str | None = None
    model: str | None = None
    metadata: dict[str, Any] | None = None


class MessageFeedbackResponse(BaseModel):
    success: bool
    data: MessageFeedback | None = None
    message: str | None = None


class MessageFeedbackListResponse(BaseModel):
    success: bool
    data: list[MessageFeedback]
    count: int
    message: str | None = None


class FeedbackStatsResponse(BaseModel):
    success: bool
    stats: dict[str, Any]
    message: str | None = None
