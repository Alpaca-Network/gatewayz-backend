# Streaming Improvements - Ready-to-Apply Code Snippets
# This file contains concrete code improvements for chat streaming
# See STREAMING_DIAGNOSTICS.md for detailed explanation

import asyncio
import json
import logging
import time
from contextvars import ContextVar

import httpx
from fastapi import Request

logger = logging.getLogger(__name__)
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


# ============================================================================
# IMPROVEMENT 1: Enhanced Error Messages
# ============================================================================
# Location: src/routes/chat.py, lines 768-779
# Current issue: Generic error message "Streaming error occurred"
# Solution: Specific error classification with request_id and debug info

async def stream_generator_with_enhanced_errors(
    stream,
    user,
    api_key,
    model,
    trial,
    environment_tag,
    session_id,
    messages,
    rate_limit_mgr=None,
    provider="openrouter",
    tracker=None,
):
    """Generate SSE stream from OpenAI stream response (OPTIMIZED: background post-processing)"""
    accumulated_content = ""
    accumulated_thinking = ""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    start_time = time.monotonic()
    has_thinking = False
    streaming_ctx = None

    try:
        if tracker:
            streaming_ctx = tracker.streaming()
            streaming_ctx.__enter__()

        chunk_count = 0
        for chunk in stream:
            chunk_count += 1
            logger.debug(f"[STREAM] Processing chunk {chunk_count} for model {model}")

            # ... existing chunk processing code ...
            # (lines 650-705 unchanged)

            yield f"data: {json.dumps(chunk_dict)}\n\n"

        logger.info(
            f"[STREAM] Stream completed with {chunk_count} chunks, "
            f"accumulated content length: {len(accumulated_content)}"
        )

        # ... token calculation and plan limit check code ...
        # (lines 712-747 unchanged)

        # REPLACEMENT STARTS HERE: Enhanced error handling

    except asyncio.CancelledError:
        # Task was cancelled, client disconnected or request was cancelled
        logger.info(
            f"[STREAM] Stream cancelled (request_id={request_id_var.get()}, "
            f"user_id={user['id']}, model={model}, chunks_sent={chunk_count})"
        )
        # Don't yield error, stream already cancelled
        return

    except asyncio.TimeoutError as e:
        logger.warning(
            f"[STREAM] Timeout during streaming (request_id={request_id_var.get()}, "
            f"user_id={user['id']}, model={model})"
        )
        error_chunk = {
            "error": {
                "message": "Stream timeout: provider took too long to respond",
                "type": "stream_timeout",
                "request_id": request_id_var.get()
            }
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    except httpx.TimeoutException as e:
        logger.warning(
            f"[STREAM] Provider timeout (request_id={request_id_var.get()}, "
            f"user_id={user['id']}, model={model}, provider={provider})"
        )
        error_chunk = {
            "error": {
                "message": f"Provider timeout: {provider} API not responding",
                "type": "provider_timeout",
                "provider": provider,
                "request_id": request_id_var.get()
            }
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    except httpx.RequestError as e:
        logger.error(
            f"[STREAM] Network error (request_id={request_id_var.get()}, "
            f"user_id={user['id']}, model={model}): {e}"
        )
        error_chunk = {
            "error": {
                "message": f"Network error: {str(e)[:100]}",
                "type": "network_error",
                "request_id": request_id_var.get()
            }
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.error(
            f"[STREAM] Provider HTTP error (request_id={request_id_var.get()}, "
            f"user_id={user['id']}, model={model}, status={status_code})"
        )

        # Specific error handling for common HTTP errors
        if status_code == 401:
            error_msg = "Provider authentication failed - check API key"
            error_type = "provider_auth_error"
        elif status_code == 429:
            error_msg = f"Provider rate limit exceeded - try again later"
            error_type = "provider_rate_limit"
        elif status_code == 500:
            error_msg = f"Provider server error - try again later"
            error_type = "provider_server_error"
        else:
            error_msg = f"Provider returned HTTP {status_code}"
            error_type = "provider_http_error"

        error_chunk = {
            "error": {
                "message": error_msg,
                "type": error_type,
                "status_code": status_code,
                "provider": provider,
                "request_id": request_id_var.get()
            }
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.exception(
            f"[STREAM] Unexpected error (request_id={request_id_var.get()}, "
            f"user_id={user['id']}, model={model}): {type(e).__name__}"
        )
        error_chunk = {
            "error": {
                "message": f"Streaming error: {type(e).__name__}",
                "type": "stream_error",
                "request_id": request_id_var.get(),
                # Only include debug info if explicitly enabled
                # "debug_info": str(e)[:200] if Config.DEBUG else None
            }
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    finally:
        if streaming_ctx:
            streaming_ctx.__exit__(None, None, None)
        if tracker:
            tracker.record_percentages()


# ============================================================================
# IMPROVEMENT 2: Client Disconnect Detection
# ============================================================================
# Location: src/routes/chat.py, lines 614-780 (stream_generator function)
# Current issue: No detection of client disconnect, wasted compute
# Solution: Check if client is still connected before yielding chunks

async def stream_generator_with_disconnect_detection(
    stream,
    user,
    api_key,
    model,
    trial,
    environment_tag,
    session_id,
    messages,
    request: Request = None,  # NEW: Add request parameter
    rate_limit_mgr=None,
    provider="openrouter",
    tracker=None,
):
    """Generate SSE stream with client disconnect detection"""
    accumulated_content = ""
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    start_time = time.monotonic()
    streaming_ctx = None
    client_disconnected = False
    background_task = None

    try:
        if tracker:
            streaming_ctx = tracker.streaming()
            streaming_ctx.__enter__()

        chunk_count = 0
        for chunk in stream:
            # NEW: Check if client is still connected
            if request:
                try:
                    # FastAPI provides is_disconnected() method on Request
                    # This check is non-blocking and will detect client disconnects
                    if hasattr(request, 'is_disconnected') and request.is_disconnected():
                        logger.warning(
                            f"[STREAM] Client disconnected at chunk {chunk_count} "
                            f"(user_id={user['id']}, model={model}, "
                            f"request_id={request_id_var.get()})"
                        )
                        client_disconnected = True
                        break
                except Exception as e:
                    # If check fails, continue anyway - we'll find out when we try to yield
                    logger.debug(f"[STREAM] Error checking client connection: {e}")

            chunk_count += 1
            logger.debug(f"[STREAM] Processing chunk {chunk_count} for model {model}")

            # ... existing chunk processing code ...
            # (lines 650-705 unchanged)

            # NEW: Wrap yield with try-except to catch send errors
            try:
                yield f"data: {json.dumps(chunk_dict)}\n\n"
            except Exception as e:
                # Connection error when trying to send chunk
                logger.warning(
                    f"[STREAM] Failed to send chunk {chunk_count}: {type(e).__name__} "
                    f"(user_id={user['id']}, model={model}, "
                    f"request_id={request_id_var.get()})"
                )
                client_disconnected = True
                break

        # NEW: If client disconnected, stop here
        if client_disconnected:
            logger.info(
                f"[STREAM] Stream aborted due to client disconnect "
                f"(chunks_sent={chunk_count}, content_length={len(accumulated_content)}, "
                f"user_id={user['id']}, model={model}, "
                f"request_id={request_id_var.get()})"
            )
            # Don't send [DONE], client already gone
            # Don't process credits (no completed request)
            return

        logger.info(
            f"[STREAM] Stream completed with {chunk_count} chunks, "
            f"accumulated content length: {len(accumulated_content)}"
        )

        # ... token calculation and plan limit check code ...
        # (lines 712-747 unchanged)

        yield "data: [DONE]\n\n"

        # NEW: Track background task for cleanup
        background_task = asyncio.create_task(
            _process_stream_completion_background(
                user=user,
                api_key=api_key,
                model=model,
                trial=trial,
                environment_tag=environment_tag,
                session_id=session_id,
                messages=messages,
                accumulated_content=accumulated_content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                elapsed=max(0.001, time.monotonic() - start_time),
                provider=provider,
            )
        )
        logger.debug(
            f"[STREAM] Background task scheduled (task_id={background_task.get_name()})"
        )

    except Exception as e:
        # ... enhanced error handling (see Improvement 1) ...
        logger.error(f"Streaming error: {e}")
        error_chunk = {"error": {"message": "Streaming error occurred", "type": "stream_error"}}
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    finally:
        if streaming_ctx:
            streaming_ctx.__exit__(None, None, None)
        if tracker:
            tracker.record_percentages()


# ============================================================================
# IMPROVEMENT 3: Session Persistence with Retry Logic
# ============================================================================
# Location: src/routes/chat.py, lines 453-611 (_process_stream_completion_background)
# Current issue: Fire-and-forget, no retry, silent failures
# Solution: Exponential backoff retry with proper error logging

async def _process_stream_completion_background_with_retry(
    user,
    api_key,
    model,
    trial,
    environment_tag,
    session_id,
    messages,
    accumulated_content,
    prompt_tokens,
    completion_tokens,
    total_tokens,
    elapsed,
    provider,
    max_retries=3,
):
    """Process stream completion with retry logic and comprehensive error handling"""
    request_id = request_id_var.get()

    try:
        # === Trial & Credit Processing ===
        if trial.get("is_trial"):
            try:
                await _to_thread(
                    track_trial_usage,
                    trial["id"],
                    completion_tokens,
                    model,
                )
                logger.info(
                    f"[BG] Trial usage tracked (trial_id={trial['id']}, "
                    f"tokens={completion_tokens}, request_id={request_id})"
                )
            except Exception as e:
                logger.error(
                    f"[BG] Failed to track trial usage: {e}",
                    extra={"request_id": request_id}
                )

        # Deduct credits with error handling
        try:
            await _to_thread(
                deduct_credits,
                user["id"],
                model,
                completion_tokens,
                prompt_tokens,
                environment_tag,
                api_key,
                elapsed,
                provider,
            )
            logger.info(
                f"[BG] Credits deducted (user_id={user['id']}, "
                f"model={model}, tokens={total_tokens}, request_id={request_id})"
            )
        except Exception as e:
            logger.error(
                f"[BG] Failed to deduct credits (user_id={user['id']}, "
                f"model={model}, tokens={total_tokens}): {e}",
                extra={"request_id": request_id}
            )
            # Log error but don't fail - this gets retried on next request

        # === Usage Recording ===
        try:
            await _to_thread(
                record_api_usage,
                api_key,
                model,
                total_tokens,
                completion_tokens,
                elapsed,
                provider,
            )
            logger.debug(
                f"[BG] API usage recorded (request_id={request_id})"
            )
        except Exception as e:
            logger.warning(
                f"[BG] Failed to record usage: {e}",
                extra={"request_id": request_id}
            )

        # === Activity Logging ===
        try:
            await _to_thread(
                log_activity,
                user["id"],
                "chat_completion",
                {
                    "model": model,
                    "provider": provider,
                    "tokens": total_tokens,
                    "duration_ms": int(elapsed * 1000),
                },
            )
            logger.debug(f"[BG] Activity logged (request_id={request_id})")
        except Exception as e:
            logger.warning(
                f"[BG] Failed to log activity: {e}",
                extra={"request_id": request_id}
            )

        # === Session History Persistence (with retry) ===
        if session_id:
            retry_count = 0
            last_error = None

            while retry_count < max_retries:
                try:
                    session = await _to_thread(
                        get_chat_session,
                        session_id,
                        user["id"],
                    )

                    if not session:
                        logger.warning(
                            f"[BG] Session not found (session_id={session_id}, "
                            f"user_id={user['id']}, request_id={request_id})"
                        )
                        break

                    # Save messages to session
                    user_message = messages[-1] if messages else None
                    await _to_thread(
                        save_chat_messages,
                        session_id,
                        user_message,
                        accumulated_content,
                        total_tokens,
                    )
                    logger.info(
                        f"[BG] Chat history saved (session_id={session_id}, "
                        f"tokens={total_tokens}, request_id={request_id})"
                    )
                    break

                except Exception as e:
                    retry_count += 1
                    last_error = e

                    if retry_count < max_retries:
                        # Exponential backoff: 0.1s, 0.2s, 0.4s
                        wait_time = (2 ** (retry_count - 1)) * 0.1
                        logger.warning(
                            f"[BG] Failed to save session (attempt {retry_count}/{max_retries}, "
                            f"session_id={session_id}, request_id={request_id}). "
                            f"Retrying in {wait_time:.2f}s: {type(e).__name__}: {str(e)[:100]}"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(
                            f"[BG] Failed to save session after {max_retries} attempts "
                            f"(session_id={session_id}, user_id={user['id']}, "
                            f"request_id={request_id}): {type(last_error).__name__}: {str(last_error)[:100]}"
                        )

        logger.info(
            f"[BG] Stream processing completed (user_id={user['id']}, "
            f"model={model}, tokens={total_tokens}, "
            f"elapsed_ms={int(elapsed*1000)}, request_id={request_id})"
        )

    except Exception as e:
        logger.exception(
            f"[BG] Unexpected error in stream processing "
            f"(user_id={user['id']}, request_id={request_id})"
        )


# ============================================================================
# IMPROVEMENT 4: Session ID Validation
# ============================================================================
# Location: src/routes/chat.py, lines 878-911 (session handling in chat_completions)
# Current issue: No validation of session_id, silently fails if not found
# Solution: Validate session exists and user owns it

async def validate_session_id(session_id: int, user_id: str):
    """Validate session ID before streaming

    Args:
        session_id: Session ID from query parameter
        user_id: Current user ID

    Raises:
        HTTPException: If session invalid or not found
    """
    # Validate session_id format
    if session_id is None:
        return None  # Session ID is optional

    if session_id <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid session_id: must be positive integer, got {session_id}"
        )

    # Check if session exists and user owns it
    try:
        session = await _to_thread(
            get_chat_session,
            session_id,
            user_id,
        )
        if not session:
            raise HTTPException(
                status_code=404,
                detail=f"Chat session {session_id} not found or does not belong to your account"
            )
        logger.debug(
            f"[CHAT] Session validated (session_id={session_id}, "
            f"user_id={user_id}, messages={len(session.get('messages', []))})"
        )
        return session
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[CHAT] Error validating session: {e}",
            extra={"session_id": session_id, "user_id": user_id}
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to validate chat session"
        )


# ============================================================================
# IMPROVEMENT 5: Plan Limit Pre-Check
# ============================================================================
# Location: src/routes/chat.py, before calling provider
# Current issue: Limits checked after streaming, allows content to be sent
# Solution: Check estimated limits before streaming starts

def estimate_tokens_for_request(messages, model: str) -> int:
    """Estimate tokens that will be used for request

    Args:
        messages: List of message dicts with content
        model: Model name (for model-specific estimation)

    Returns:
        Estimated total tokens (prompt + estimated completion)
    """
    # Count prompt tokens
    prompt_chars = 0
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, str):
            prompt_chars += len(content)
        elif isinstance(content, list):
            # For multimodal content, extract text parts
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    prompt_chars += len(item.get("text", ""))

    prompt_tokens = max(1, prompt_chars // 4)

    # Estimate completion tokens (very rough: ~25% of prompt)
    # Different models have different ratios, but this is a safe estimate
    estimated_completion_tokens = max(1, int(prompt_tokens * 0.25))

    total_estimated = prompt_tokens + estimated_completion_tokens
    return total_estimated


async def check_plan_limits_before_streaming(
    user_id: str,
    estimated_tokens: int,
    environment_tag: str,
) -> bool:
    """Check if request would exceed plan limits

    Args:
        user_id: User ID
        estimated_tokens: Estimated tokens for this request
        environment_tag: Environment (prod/staging)

    Raises:
        HTTPException: If would exceed limits

    Returns:
        True if allowed, False if would exceed
    """
    try:
        plan = await _to_thread(
            check_plan_limits_dry_run,  # New function to implement
            user_id,
            estimated_tokens,
            environment_tag,
        )

        remaining = plan.get("remaining_tokens", 0)
        if remaining < estimated_tokens:
            logger.warning(
                f"[PLAN] Request would exceed plan limits "
                f"(user_id={user_id}, estimated={estimated_tokens}, remaining={remaining})"
            )
            raise HTTPException(
                status_code=402,  # Payment Required
                detail=f"Request would exceed plan limits. "
                        f"Remaining: {remaining} tokens, "
                        f"Estimated: {estimated_tokens} tokens. "
                        f"Please upgrade your plan or try a shorter request."
            )
        return True
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[PLAN] Error checking plan limits: {e}",
            extra={"user_id": user_id}
        )
        # On error, allow request to proceed (fail open)
        return True


# ============================================================================
# IMPROVEMENT 6: Comprehensive Streaming Logging
# ============================================================================
# Add these log statements throughout stream_generator function

def add_comprehensive_logging_to_chat_completions(request_id: str, user_id: str):
    """Helper to add comprehensive logging

    Call this at key points in chat_completions and stream_generator:
    """

    # At request start
    logger.info(
        f"[CHAT] Starting chat request (request_id={request_id}, "
        f"user_id={user_id}, timestamp={time.time()})"
    )

    # Before provider call
    logger.debug(
        f"[CHAT] Calling provider (request_id={request_id}, "
        f"provider=openrouter, model=gpt-4)"
    )

    # At stream start
    logger.info(
        f"[STREAM] Starting stream (request_id={request_id}, "
        f"user_id={user_id}, provider=openrouter, session_id=123)"
    )

    # At stream completion
    logger.info(
        f"[STREAM] Completed successfully (request_id={request_id}, "
        f"user_id={user_id}, chunks=42, content_length=1234, "
        f"prompt_tokens=100, completion_tokens=25, elapsed_ms=1500)"
    )

    # On error
    logger.error(
        f"[STREAM] Failed (request_id={request_id}, "
        f"user_id={user_id}, error_type=TimeoutError, "
        f"chunks_sent=10, elapsed_ms=5000)"
    )


# ============================================================================
# Summary: Implementation Order
# ============================================================================
"""
1. Start with IMPROVEMENT 1: Enhanced Error Messages
   - Easy to implement
   - High visibility impact
   - No breaking changes
   - Adds request_id to errors

2. Add IMPROVEMENT 6: Comprehensive Logging
   - Simple logging additions
   - Helps with diagnostics
   - No functional changes

3. Implement IMPROVEMENT 2: Client Disconnect Detection
   - Medium complexity
   - High impact on resource usage
   - Prevents wasted compute

4. Add IMPROVEMENT 5: Session ID Validation
   - Low complexity
   - Improves UX
   - Better error messages

5. Implement IMPROVEMENT 3: Session Persistence with Retry
   - Medium complexity
   - High reliability impact
   - Requires careful testing

6. Add IMPROVEMENT 4: Plan Limit Pre-Check
   - Medium complexity
   - Better UX
   - Requires estimator function

Each improvement is independent and can be tested separately.
"""
