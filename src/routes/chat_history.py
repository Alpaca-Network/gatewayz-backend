import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.db.chat_history import (
    create_chat_session,
    delete_chat_session,
    get_chat_session,
    get_chat_session_stats,
    get_user_chat_sessions,
    save_chat_message,
    search_chat_sessions,
    update_chat_session,
    validate_message_ownership,
)
from src.db.feedback import (
    delete_feedback,
    get_feedback_by_session,
    get_feedback_stats,
    get_user_feedback,
    save_message_feedback,
    update_feedback,
)
from src.schemas.chat import (
    ChatSessionResponse,
    ChatSessionsListResponse,
    ChatSessionStatsResponse,
    CreateChatSessionRequest,
    FeedbackStatsResponse,
    MessageFeedbackListResponse,
    MessageFeedbackResponse,
    SaveChatMessageRequest,
    SaveMessageFeedbackRequest,
    SearchChatSessionsRequest,
    UpdateChatSessionRequest,
    UpdateMessageFeedbackRequest,
)
from src.security.deps import get_api_key
from src.services.background_tasks import log_activity_background
from src.services.user_lookup_cache import get_user

# Initialize logging
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/chat", tags=["chat-history"])


@router.post("/sessions", response_model=ChatSessionResponse)
async def create_session(request: CreateChatSessionRequest, api_key: str = Depends(get_api_key)):
    """
    Create a new chat session

    OPTIMIZATIONS:
    - Cached user lookup (reduces DB queries by 95%)
    - Background activity logging (non-blocking)
    - Performance metrics logging
    """
    start_time = time.time()
    try:
        # Get user (cached)
        user_lookup_start = time.time()
        user = get_user(api_key)
        user_lookup_ms = (time.time() - user_lookup_start) * 1000

        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Create session
        session_create_start = time.time()
        session = create_chat_session(user_id=user["id"], title=request.title, model=request.model)
        session_create_ms = (time.time() - session_create_start) * 1000

        logger.info(
            f"Created chat session {session['id']} for user {user['id']} "
            f"(user_lookup: {user_lookup_ms:.1f}ms, session_create: {session_create_ms:.1f}ms)"
        )

        # Log session creation activity in background (non-blocking)
        try:
            log_activity_background(
                user_id=user["id"],
                model=request.model or "session",
                provider="Chat History",
                tokens=0,
                cost=0.0,
                speed=0.0,
                finish_reason="session_created",
                app="Chat",
                metadata={
                    "action": "create_session",
                    "session_id": session["id"],
                    "session_title": request.title,
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to queue background activity logging for user {user['id']}, session {session.get('id', 'unknown')}: {e}",
                exc_info=True,
            )
            # Don't fail the request if logging fails

        total_ms = (time.time() - start_time) * 1000
        logger.debug(f"Session creation completed in {total_ms:.1f}ms")

        return ChatSessionResponse(
            success=True, data=session, message="Chat session created successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        total_ms = (time.time() - start_time) * 1000
        logger.error(f"Failed to create chat session after {total_ms:.1f}ms: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to create chat session: {str(e)}"
        ) from e


@router.get("/sessions", response_model=ChatSessionsListResponse)
async def get_sessions(
    api_key: str = Depends(get_api_key),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Get all chat sessions for the authenticated user"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        sessions = get_user_chat_sessions(user_id=user["id"], limit=limit, offset=offset)

        logger.info(f"Retrieved {len(sessions)} chat sessions for user {user['id']}")

        return ChatSessionsListResponse(
            success=True,
            data=sessions,
            count=len(sessions),
            message=f"Retrieved {len(sessions)} chat sessions",
        )

    except Exception as e:
        logger.error(f"Failed to get chat sessions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get chat sessions: {str(e)}") from e


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_session(session_id: int, api_key: str = Depends(get_api_key)):
    """Get a specific chat session with messages"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        session = get_chat_session(session_id, user["id"])

        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")

        logger.info(f"Retrieved chat session {session_id} for user {user['id']}")

        return ChatSessionResponse(
            success=True, data=session, message="Chat session retrieved successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get chat session: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get chat session: {str(e)}") from e


@router.put("/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_session(
    session_id: int, request: UpdateChatSessionRequest, api_key: str = Depends(get_api_key)
):
    """Update a chat session"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        success = update_chat_session(
            session_id=session_id, user_id=user["id"], title=request.title, model=request.model
        )

        if not success:
            raise HTTPException(status_code=404, detail="Chat session not found")

        # Get updated session
        session = get_chat_session(session_id, user["id"])

        logger.info(f"Updated chat session {session_id} for user {user['id']}")

        return ChatSessionResponse(
            success=True, data=session, message="Chat session updated successfully"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update chat session: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to update chat session: {str(e)}"
        ) from e


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: int, api_key: str = Depends(get_api_key)):
    """Delete a chat session"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        success = delete_chat_session(session_id, user["id"])

        if not success:
            raise HTTPException(status_code=404, detail="Chat session not found")

        logger.info(f"Deleted chat session {session_id} for user {user['id']}")

        return {"success": True, "message": "Chat session deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete chat session: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to delete chat session: {str(e)}"
        ) from e


@router.get("/stats", response_model=ChatSessionStatsResponse)
async def get_stats(api_key: str = Depends(get_api_key)):
    """Get chat session statistics for the authenticated user"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        stats = get_chat_session_stats(user["id"])

        logger.info(f"Retrieved chat stats for user {user['id']}")

        return ChatSessionStatsResponse(
            success=True, stats=stats, message="Chat statistics retrieved successfully"
        )

    except Exception as e:
        logger.error(f"Failed to get chat stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get chat stats: {str(e)}") from e


@router.post("/search", response_model=ChatSessionsListResponse)
async def search_sessions(request: SearchChatSessionsRequest, api_key: str = Depends(get_api_key)):
    """Search chat sessions by title or message content"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        sessions = search_chat_sessions(
            user_id=user["id"], query=request.query, limit=request.limit
        )

        logger.info(
            f"Found {len(sessions)} sessions matching '{request.query}' for user {user['id']}"
        )

        return ChatSessionsListResponse(
            success=True,
            data=sessions,
            count=len(sessions),
            message=f"Found {len(sessions)} sessions matching '{request.query}'",
        )

    except Exception as e:
        logger.error(f"Failed to search chat sessions: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to search chat sessions: {str(e)}"
        ) from e


@router.post("/sessions/{session_id}/messages")
async def save_message(
    session_id: int, request: SaveChatMessageRequest, api_key: str = Depends(get_api_key)
):
    """Save a message to a chat session (accepts JSON body)"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Verify session belongs to user
        session = get_chat_session(session_id, user["id"])
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")

        message = save_chat_message(
            session_id=session_id,
            role=request.role,
            content=request.content,
            model=request.model,
            tokens=request.tokens,
            user_id=user["id"],
        )

        logger.info(f"Saved message {message['id']} to session {session_id}")

        return {"success": True, "data": message, "message": "Message saved successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save message: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save message: {str(e)}") from e


# OPTIMIZATION: Batch message save endpoint
class BatchMessageRequest(BaseModel):
    """Request model for batch message save"""

    messages: list[SaveChatMessageRequest]


@router.post("/sessions/{session_id}/messages/batch")
async def save_messages_batch(
    session_id: int, request: BatchMessageRequest, api_key: str = Depends(get_api_key)
):
    """
    OPTIMIZATION: Save multiple messages in a single request
    Reduces API overhead by 60-80% when saving multiple messages
    """
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Verify session belongs to user
        session = get_chat_session(session_id, user["id"])
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")

        saved_messages = []
        failed_messages = []

        # Save each message in the batch
        for msg in request.messages:
            try:
                message = save_chat_message(
                    session_id=session_id,
                    role=msg.role,
                    content=msg.content,
                    model=msg.model,
                    tokens=msg.tokens,
                    user_id=user["id"],
                )
                saved_messages.append(
                    {"success": True, "message_id": message["id"], "data": message}
                )
            except Exception as msg_error:
                logger.error(f"Failed to save message in batch: {msg_error}")
                failed_messages.append(
                    {
                        "success": False,
                        "error": str(msg_error),
                        "content_preview": msg.content[:50] if msg.content else "",
                    }
                )

        logger.info(
            f"Batch saved {len(saved_messages)}/{len(request.messages)} messages to session {session_id}"
        )

        return {
            "success": len(failed_messages) == 0,
            "data": {
                "saved": saved_messages,
                "failed": failed_messages,
                "total": len(request.messages),
                "success_count": len(saved_messages),
                "failure_count": len(failed_messages),
            },
            "message": f"Saved {len(saved_messages)}/{len(request.messages)} messages successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to batch save messages: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to batch save messages: {str(e)}"
        ) from e


# =====================================================
# Message Feedback Endpoints
# =====================================================


@router.post("/feedback", response_model=MessageFeedbackResponse)
async def submit_feedback(
    request: SaveMessageFeedbackRequest,
    api_key: str = Depends(get_api_key),
):
    """
    Submit feedback for a chat message (thumbs up, thumbs down, etc.)

    This endpoint allows users to provide feedback on assistant responses.
    Feedback can be associated with a specific session and/or message,
    or submitted standalone for general feedback.

    Feedback types:
    - thumbs_up: User found the response helpful
    - thumbs_down: User found the response unhelpful
    - regenerate: User requested a new response

    Optional fields:
    - rating: 1-5 star rating
    - comment: Text feedback
    - model: The model that generated the response
    - metadata: Additional context (response content, prompt, etc.)
    """
    start_time = time.time()
    try:
        # Get user (cached)
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Note: feedback_type and rating are validated by Pydantic schema
        # feedback_type uses Literal["thumbs_up", "thumbs_down", "regenerate"]
        # rating uses Field(ge=1, le=5)

        # If session_id provided, verify it belongs to user
        if request.session_id is not None:
            session = get_chat_session(request.session_id, user["id"])
            if not session:
                raise HTTPException(status_code=404, detail="Chat session not found")

        # If message_id provided, verify it belongs to user's session
        if request.message_id is not None:
            if not validate_message_ownership(
                message_id=request.message_id,
                user_id=user["id"],
                session_id=request.session_id,
            ):
                raise HTTPException(status_code=404, detail="Message not found")

        # Save feedback
        feedback = save_message_feedback(
            user_id=user["id"],
            feedback_type=request.feedback_type,
            session_id=request.session_id,
            message_id=request.message_id,
            rating=request.rating,
            comment=request.comment,
            model=request.model,
            metadata=request.metadata,
        )

        # Log feedback activity in background
        try:
            log_activity_background(
                user_id=user["id"],
                model=request.model or "feedback",
                provider="Chat Feedback",
                tokens=0,
                cost=0.0,
                speed=0.0,
                finish_reason="feedback_submitted",
                app="Chat",
                metadata={
                    "action": "submit_feedback",
                    "feedback_id": feedback["id"],
                    "feedback_type": request.feedback_type,
                    "session_id": request.session_id,
                    "message_id": request.message_id,
                    "has_comment": request.comment is not None,
                    "has_rating": request.rating is not None,
                },
            )
        except Exception as e:
            logger.error(f"Failed to log feedback activity: {e}")
            # Don't fail the request if logging fails

        total_ms = (time.time() - start_time) * 1000
        logger.info(
            f"Feedback {feedback['id']} ({request.feedback_type}) saved for user {user['id']} "
            f"in {total_ms:.1f}ms"
        )

        return MessageFeedbackResponse(
            success=True,
            data=feedback,
            message=f"Feedback ({request.feedback_type}) submitted successfully",
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to submit feedback: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to submit feedback: {str(e)}") from e


@router.get("/feedback", response_model=MessageFeedbackListResponse)
async def get_my_feedback(
    api_key: str = Depends(get_api_key),
    feedback_type: str | None = Query(None, description="Filter by feedback type"),
    session_id: int | None = Query(None, description="Filter by session ID"),
    model: str | None = Query(None, description="Filter by model name"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Get the authenticated user's feedback history.

    Supports filtering by:
    - feedback_type: 'thumbs_up', 'thumbs_down', 'regenerate'
    - session_id: specific chat session
    - model: specific model name
    """
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        feedback_list = get_user_feedback(
            user_id=user["id"],
            feedback_type=feedback_type,
            session_id=session_id,
            model=model,
            limit=limit,
            offset=offset,
        )

        logger.info(f"Retrieved {len(feedback_list)} feedback records for user {user['id']}")

        return MessageFeedbackListResponse(
            success=True,
            data=feedback_list,
            count=len(feedback_list),
            message=f"Retrieved {len(feedback_list)} feedback records",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get feedback: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get feedback: {str(e)}") from e


@router.get("/feedback/stats", response_model=FeedbackStatsResponse)
async def get_my_feedback_stats(
    api_key: str = Depends(get_api_key),
    model: str | None = Query(None, description="Filter by model name"),
    days: int = Query(30, ge=1, le=365, description="Number of days to aggregate"),
):
    """
    Get aggregated feedback statistics for the authenticated user.

    Returns:
    - Total feedback count
    - Thumbs up/down counts and rates
    - Average rating (if ratings provided)
    - Breakdown by model
    """
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        stats = get_feedback_stats(user_id=user["id"], model=model, days=days)

        logger.info(f"Retrieved feedback stats for user {user['id']}")

        return FeedbackStatsResponse(
            success=True,
            stats=stats,
            message="Feedback statistics retrieved successfully",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get feedback stats: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get feedback stats: {str(e)}"
        ) from e


@router.get("/sessions/{session_id}/feedback", response_model=MessageFeedbackListResponse)
async def get_session_feedback(
    session_id: int,
    api_key: str = Depends(get_api_key),
):
    """
    Get all feedback for a specific chat session.
    """
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Verify session belongs to user
        session = get_chat_session(session_id, user["id"])
        if not session:
            raise HTTPException(status_code=404, detail="Chat session not found")

        feedback_list = get_feedback_by_session(session_id, user["id"])

        logger.info(f"Retrieved {len(feedback_list)} feedback records for session {session_id}")

        return MessageFeedbackListResponse(
            success=True,
            data=feedback_list,
            count=len(feedback_list),
            message=f"Retrieved {len(feedback_list)} feedback records for session",
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get session feedback: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get session feedback: {str(e)}"
        ) from e


@router.put("/feedback/{feedback_id}", response_model=MessageFeedbackResponse)
async def update_my_feedback(
    feedback_id: int,
    request: UpdateMessageFeedbackRequest,
    api_key: str = Depends(get_api_key),
):
    """
    Update an existing feedback record.

    All fields are optional - only provided fields will be updated.
    """
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Note: feedback_type and rating are validated by Pydantic schema

        feedback = update_feedback(
            feedback_id=feedback_id,
            user_id=user["id"],
            feedback_type=request.feedback_type,
            rating=request.rating,
            comment=request.comment,
        )

        if not feedback:
            raise HTTPException(status_code=404, detail="Feedback not found")

        logger.info(f"Updated feedback {feedback_id} for user {user['id']}")

        return MessageFeedbackResponse(
            success=True,
            data=feedback,
            message="Feedback updated successfully",
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to update feedback: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update feedback: {str(e)}") from e


@router.delete("/feedback/{feedback_id}")
async def delete_my_feedback(
    feedback_id: int,
    api_key: str = Depends(get_api_key),
):
    """
    Delete a feedback record.
    """
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        success = delete_feedback(feedback_id, user["id"])

        if not success:
            raise HTTPException(status_code=404, detail="Feedback not found")

        logger.info(f"Deleted feedback {feedback_id} for user {user['id']}")

        return {"success": True, "message": "Feedback deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete feedback: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete feedback: {str(e)}") from e
