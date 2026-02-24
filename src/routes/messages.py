"""
Anthropic Messages API endpoint
Compatible with Claude API: https://docs.claude.com/en/api/messages
"""

import asyncio
import importlib
import logging
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import src.db.activity as activity_module
import src.db.api_keys as api_keys_module
import src.db.chat_completion_requests as chat_completion_requests_module
import src.db.chat_history as chat_history_module
import src.db.model_health as model_health_module
import src.db.plans as plans_module
import src.db.rate_limits as rate_limits_module
import src.db.users as users_module
import src.services.rate_limiting as rate_limiting_service
import src.services.trial_validation as trial_module
from src.adapters.chat import AnthropicChatAdapter
from src.config import Config

# Unified chat handler and adapters for chat unification
from src.handlers.chat_handler import ChatInferenceHandler
from src.schemas import MessagesRequest
from src.security.deps import get_api_key
from src.services.anthropic_transformer import (
    extract_text_from_content,
    transform_anthropic_to_openai,
    transform_openai_to_anthropic,
)
from src.services.model_transformations import detect_provider_from_model_id, transform_model_id
from src.services.passive_health_monitor import capture_model_health
from src.utils.performance_tracker import PerformanceTracker
from src.utils.rate_limit_headers import get_rate_limit_headers
from src.utils.security_validators import sanitize_for_logging
from src.utils.token_estimator import estimate_message_tokens

logger = logging.getLogger(__name__)
router = APIRouter()


# Backwards compatibility wrappers
def increment_api_key_usage(*args, **kwargs):
    return api_keys_module.increment_api_key_usage(*args, **kwargs)


def enforce_plan_limits(*args, **kwargs):
    return plans_module.enforce_plan_limits(*args, **kwargs)


def create_rate_limit_alert(*args, **kwargs):
    return rate_limits_module.create_rate_limit_alert(*args, **kwargs)


def update_rate_limit_usage(*args, **kwargs):
    return rate_limits_module.update_rate_limit_usage(*args, **kwargs)


def get_user(*args, **kwargs):
    return users_module.get_user(*args, **kwargs)


def deduct_credits(*args, **kwargs):
    return users_module.deduct_credits(*args, **kwargs)


def log_api_usage_transaction(*args, **kwargs):
    return users_module.log_api_usage_transaction(*args, **kwargs)


def record_usage(*args, **kwargs):
    return users_module.record_usage(*args, **kwargs)


def save_chat_message(*args, **kwargs):
    return chat_history_module.save_chat_message(*args, **kwargs)


def get_chat_session(*args, **kwargs):
    return chat_history_module.get_chat_session(*args, **kwargs)


def log_activity(*args, **kwargs):
    return activity_module.log_activity(*args, **kwargs)


def get_provider_from_model(*args, **kwargs):
    return activity_module.get_provider_from_model(*args, **kwargs)


def get_rate_limit_manager(*args, **kwargs):
    return rate_limiting_service.get_rate_limit_manager(*args, **kwargs)


def validate_trial_access(*args, **kwargs):
    return trial_module.validate_trial_access(*args, **kwargs)


def track_trial_usage(*args, **kwargs):
    return trial_module.track_trial_usage(*args, **kwargs)


def record_model_call(*args, **kwargs):
    return model_health_module.record_model_call(*args, **kwargs)


def _fallback_get_user(api_key: str):
    try:
        supabase_module = importlib.import_module("src.config.supabase_config")
        client = supabase_module.get_supabase_client()
        result = client.table("users").select("*").eq("api_key", api_key).execute()
        if result.data:
            logging.getLogger(__name__).debug(
                "Messages fallback user lookup succeeded for %s", api_key
            )
            return result.data[0]
        logging.getLogger(__name__).debug(
            "Messages fallback lookup found no data; snapshot=%s",
            client.table("users").select("*").execute().data,
        )
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "Messages fallback user lookup error for %s: %s", api_key, exc
        )
    return None


DEFAULT_PROVIDER_TIMEOUT = 60
PROVIDER_TIMEOUTS = {
    "huggingface": 120,
}


def mask_key(k: str) -> str:
    return f"...{k[-4:]}" if k and len(k) >= 4 else "****"


async def _to_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


async def _ensure_plan_capacity(user_id: int, environment_tag: str) -> dict[str, Any]:
    """Ensure plan limits allow another request before contacting providers."""
    plan_check = await _to_thread(enforce_plan_limits, user_id, 0, environment_tag)
    if not plan_check.get("allowed", False):
        raise HTTPException(
            status_code=429,
            detail=f"Plan limit exceeded: {plan_check.get('reason', 'unknown')}",
        )
    return plan_check


@router.post("/messages", tags=["chat"])
async def anthropic_messages(
    req: MessagesRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(get_api_key),
    session_id: int | None = Query(None, description="Chat session ID to save messages to"),
    request: Request = None,
):
    """
    Anthropic Messages API endpoint (Claude API compatible).

    This endpoint accepts Anthropic-style requests and transforms them to work
    with OpenAI-compatible providers (OpenRouter, Featherless).

    Key differences from OpenAI Chat Completions:
    - Uses 'messages' array but 'system' is a separate parameter
    - 'max_tokens' is REQUIRED (not optional like in OpenAI)
    - Returns Anthropic-style response with 'content' array and 'stop_reason'
    - Supports 'stop_sequences' instead of 'stop'
    - Supports 'top_k' parameter (Anthropic-specific, logged but not used)

    Example request:
    ```json
    {
      "model": "claude-sonnet-4-5-20250929",
      "max_tokens": 1024,
      "messages": [
        {"role": "user", "content": "Hello, Claude!"}
      ]
    }
    ```

    Example response:
    ```json
    {
      "id": "msg-123",
      "type": "message",
      "role": "assistant",
      "content": [{"type": "text", "text": "Hello! How can I help?"}],
      "model": "claude-sonnet-4-5-20250929",
      "stop_reason": "end_turn",
      "usage": {"input_tokens": 10, "output_tokens": 12}
    }
    ```
    """
    # Generate request correlation ID for distributed tracing
    request_id = str(uuid.uuid4())

    # Initialize performance tracker
    PerformanceTracker(endpoint="/v1/messages")

    if Config.IS_TESTING and request:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            parts = auth_header.split(" ", 1)
            if len(parts) == 2:
                api_key = parts[1].strip()
            else:
                logger.warning(
                    f"Malformed Authorization header in testing mode: {auth_header[:20]}..."
                )

    logger.info(
        "anthropic_messages start (request_id=%s, api_key=%s, model=%s)",
        request_id,
        mask_key(api_key),
        req.model,
        extra={"request_id": request_id},
    )
    logger.debug("Messages endpoint Config.IS_TESTING=%s", Config.IS_TESTING)

    try:
        # === 1) User + plan/trial prechecks ===
        user = await _to_thread(get_user, api_key)
        if not user and not Config.IS_TESTING:
            user = await _to_thread(_fallback_get_user, api_key)
        if not user:
            logger.warning("Invalid API key or user not found for key %s", mask_key(api_key))
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Get API key ID for tracking (if available)
        api_key_record = await _to_thread(api_keys_module.get_api_key_by_key, api_key)
        api_key_id = api_key_record.get("id") if api_key_record else None

        environment_tag = user.get("environment_tag", "live")

        trial = await _to_thread(validate_trial_access, api_key)
        if not trial.get("is_valid", False):
            if trial.get("is_trial") and trial.get("is_expired"):
                raise HTTPException(
                    status_code=403,
                    detail=trial["error"],
                    headers={
                        "X-Trial-Expired": "true",
                        "X-Trial-End-Date": trial.get("trial_end_date", ""),
                    },
                )
            elif trial.get("is_trial"):
                headers = {}
                for k in ("remaining_tokens", "remaining_requests", "remaining_credits"):
                    if k in trial:
                        headers[f"X-Trial-{k.replace('_','-').title()}"] = str(trial[k])
                raise HTTPException(status_code=429, detail=trial["error"], headers=headers)
            else:
                raise HTTPException(status_code=403, detail=trial.get("error", "Access denied"))

        await _ensure_plan_capacity(user["id"], environment_tag)

        rate_limit_mgr = get_rate_limit_manager()
        should_release_concurrency = not trial.get("is_trial", False)
        disable_rate_limiting = os.getenv("DISABLE_RATE_LIMITING", "false").lower() == "true"

        # Pre-check plan limits before making upstream calls
        pre_plan = await _to_thread(enforce_plan_limits, user["id"], 0, environment_tag)
        if not pre_plan.get("allowed", False):
            raise HTTPException(
                status_code=429,
                detail=f"Plan limit exceeded: {pre_plan.get('reason', 'unknown')}",
            )

        # Rate limit pre-check (non-trial users only)
        rl_final = None
        if rate_limit_mgr and not disable_rate_limiting and not trial.get("is_trial", False):
            rl_pre = await rate_limit_mgr.check_rate_limit(api_key, tokens_used=0)
            if not rl_pre.allowed:
                await _to_thread(
                    create_rate_limit_alert,
                    api_key,
                    "rate_limit_exceeded",
                    {
                        "reason": rl_pre.reason,
                        "retry_after": rl_pre.retry_after,
                        "remaining_requests": rl_pre.remaining_requests,
                        "remaining_tokens": rl_pre.remaining_tokens,
                    },
                )
                headers = get_rate_limit_headers(rl_pre)
                if rl_pre.retry_after:
                    headers["Retry-After"] = str(rl_pre.retry_after)
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {rl_pre.reason}",
                    headers=headers or None,
                )

        if not trial.get("is_trial", False) and user.get("credits", 0.0) <= 0:
            raise HTTPException(status_code=402, detail="Insufficient credits")

        # Pre-check plan limits before processing (fail fast)
        pre_plan = await _to_thread(enforce_plan_limits, user["id"], 0, environment_tag)
        if not pre_plan.get("allowed", False):
            raise HTTPException(
                status_code=429, detail=f"Plan limit exceeded: {pre_plan.get('reason', 'unknown')}"
            )

        # Rate limit precheck (before making upstream request)
        rl_pre = None
        if rate_limit_mgr:
            rl_pre = await rate_limit_mgr.check_rate_limit(api_key, tokens_used=0)
            if not rl_pre.allowed:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {rl_pre.reason}",
                    headers=(
                        {"Retry-After": str(rl_pre.retry_after)} if rl_pre.retry_after else None
                    ),
                )

        # === 2) Transform Anthropic format to OpenAI format ===
        messages_data = [msg.model_dump() for msg in req.messages]

        # Handle system parameter (can be string or list of content blocks)
        system_param = req.system
        if isinstance(system_param, list):
            # Convert SystemContentBlock list to list of dicts
            system_param = [
                block.model_dump() if hasattr(block, "model_dump") else block
                for block in system_param
            ]

        # Convert tools to dicts if they are ToolDefinition objects
        tools_param = req.tools
        if tools_param:
            tools_param = [
                tool.model_dump() if hasattr(tool, "model_dump") else tool for tool in tools_param
            ]

        # Convert tool_choice to dict if it's a model
        tool_choice_param = req.tool_choice
        if tool_choice_param and hasattr(tool_choice_param, "model_dump"):
            tool_choice_param = tool_choice_param.model_dump()

        openai_messages, openai_params = transform_anthropic_to_openai(
            messages=messages_data,
            system=system_param,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            top_p=req.top_p,
            top_k=req.top_k,
            stop_sequences=req.stop_sequences,
            tools=tools_param,
            tool_choice=tool_choice_param,
        )

        # === 2.1) Inject conversation history if session_id provided ===
        if session_id:
            try:
                session = await _to_thread(get_chat_session, session_id, user["id"])
                if session and session.get("messages"):
                    history_messages = [
                        {"role": msg["role"], "content": msg["content"]}
                        for msg in session["messages"]
                    ]
                    # Insert history after system message (if present)
                    if openai_messages and openai_messages[0].get("role") == "system":
                        openai_messages = (
                            [openai_messages[0]] + history_messages + openai_messages[1:]
                        )
                    else:
                        openai_messages = history_messages + openai_messages
                    logger.info(
                        "Injected %d messages from session %s",
                        len(history_messages),
                        sanitize_for_logging(str(session_id)),
                    )
            except Exception as e:
                logger.warning(
                    "Failed to fetch chat history for session %s: %s",
                    sanitize_for_logging(str(session_id)),
                    sanitize_for_logging(str(e)),
                )

        # === 2.2) Plan limit pre-check with estimated tokens ===
        estimated_tokens = estimate_message_tokens(openai_messages, req.max_tokens)
        pre_plan = await _to_thread(
            enforce_plan_limits, user["id"], estimated_tokens, environment_tag
        )
        if not pre_plan.get("allowed", False):
            raise HTTPException(
                status_code=429, detail=f"Plan limit exceeded: {pre_plan.get('reason', 'unknown')}"
            )

        original_model = req.model

        # Auto-detect provider
        provider = (req.provider or "onerouter").lower()

        # Normalize provider aliases
        if provider == "hug":
            provider = "huggingface"

        if not Config.IS_TESTING:
            if req.provider:
                req_provider_missing = False
            else:
                req_provider_missing = True

            override_provider = detect_provider_from_model_id(original_model)
            if override_provider:
                override_provider = override_provider.lower()
                if override_provider == "hug":
                    override_provider = "huggingface"
                if override_provider != provider:
                    logger.info(
                        f"Provider override applied for model {original_model}: '{provider}' -> '{override_provider}'"
                    )
                    provider = override_provider
                    req_provider_missing = False

            if req_provider_missing:
                # Try to detect provider from model ID using the transformation module
                detected_provider = detect_provider_from_model_id(original_model)
                if detected_provider:
                    provider = detected_provider
                    # Normalize provider aliases
                    if provider == "hug":
                        provider = "huggingface"
                    logger.info(
                        "Auto-detected provider '%s' for model %s",
                        sanitize_for_logging(provider),
                        sanitize_for_logging(original_model),
                    )
                else:
                    # Fallback to checking cached models
                    # OPTIMIZATION: Fetch full catalog once instead of making N calls for disjoint providers.
                    # CRITICAL FIX: Run in thread to avoid blocking event loop during DB fetch
                    import asyncio

                    from src.services.models import get_cached_models

                    all_models_catalog = await asyncio.to_thread(get_cached_models, "all") or []
                    all_model_ids = {m.get("id") for m in all_models_catalog}

                    # Try each provider with transformation against the in-memory set
                    for test_provider in [
                        "huggingface",
                        "featherless",
                        "fireworks",
                        "together",
                    ]:
                        transformed = transform_model_id(original_model, test_provider)
                        if transformed in all_model_ids:
                            provider = test_provider
                            logger.info(
                                "Auto-detected provider '%s' for model %s (transformed to %s)",
                                sanitize_for_logging(provider),
                                sanitize_for_logging(original_model),
                                sanitize_for_logging(transformed),
                            )
                            break
                    # Otherwise default to onerouter (already set)

        # === 3) Call upstream using unified handler ===
        start = time.monotonic()
        processed = None
        model = original_model
        provider = "onerouter"  # Default, will be updated by handler

        try:
            logger.info(
                f"[Unified Handler] Processing Messages API request for model {original_model}"
            )

            # Prepare Anthropic-format request for adapter
            anthropic_request = {
                "model": req.model,
                "messages": messages_data,
                "max_tokens": req.max_tokens,
                "temperature": req.temperature,
                "top_p": req.top_p,
                "top_k": req.top_k,
                "stop_sequences": req.stop_sequences,
                "system": system_param,
                "tools": tools_param,
                "tool_choice": tool_choice_param,
                "stream": False,
            }

            # Convert Anthropic format to internal format
            adapter = AnthropicChatAdapter()
            internal_request = adapter.to_internal_request(anthropic_request)

            # Create unified handler with user context (pass request for disconnect detection)
            handler = ChatInferenceHandler(api_key, background_tasks, request=request)

            # Process request through unified pipeline
            internal_response = await handler.process(internal_request)

            # Convert internal response back to OpenAI format (for compatibility with existing postprocessing)
            from src.adapters.chat import OpenAIChatAdapter

            openai_adapter = OpenAIChatAdapter()
            processed = openai_adapter.from_internal_response(internal_response)

            # Extract values for postprocessing (maintain compatibility)
            provider = internal_response.provider_used or "onerouter"
            model = internal_response.model or original_model

            logger.info(
                f"[Unified Handler] Successfully processed Messages request: provider={provider}, model={model}"
            )

        except Exception as exc:
            # Map any errors to HTTPException
            logger.error(f"[Unified Handler] Error: {type(exc).__name__}: {exc}", exc_info=True)
            if isinstance(exc, HTTPException):
                raise
            # Map provider-specific errors
            from src.services.provider_failover import map_provider_error

            http_exc = map_provider_error(
                provider if "provider" in locals() else "onerouter",
                model if "model" in locals() else original_model,
                exc,
            )
            raise http_exc

        # Keep old failover code for reference (removed in production)
        """
        OLD CODE: Provider routing with manual failover (replaced by unified handler)

        provider_chain = build_provider_failover_chain(provider)
        provider_chain = enforce_model_failover_rules(original_model, provider_chain)
        model = original_model

        start = time.monotonic()
        processed = None
        last_http_exc = None
        request_start_time = None

        for idx, attempt_provider in enumerate(provider_chain):
            logger.debug("Messages failover iteration %s provider=%s", idx, attempt_provider)
            attempt_model = transform_model_id(original_model, attempt_provider)
            if attempt_model != original_model:
                logger.info(
                    f"Transformed model ID from '{original_model}' to '{attempt_model}' for provider {attempt_provider}"
                )

            request_model = attempt_model
            request_timeout = PROVIDER_TIMEOUTS.get(attempt_provider, DEFAULT_PROVIDER_TIMEOUT)
            if request_timeout != DEFAULT_PROVIDER_TIMEOUT:
                logger.debug(
                    "Using extended timeout %ss for provider %s", request_timeout, attempt_provider
                )

            request_start_time = time.monotonic()  # Start timing this specific provider attempt
            http_exc = None
            try:
                if attempt_provider == "aihubmix":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(
                            make_aihubmix_request_openai,
                            openai_messages,
                            request_model,
                            **openai_params,
                        ),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_aihubmix_response, resp_raw)
                elif attempt_provider == "alibaba-cloud":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(
                            make_alibaba_cloud_request_openai,
                            openai_messages,
                            request_model,
                            **openai_params,
                        ),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_alibaba_cloud_response, resp_raw)
                elif attempt_provider == "anannas":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(
                            make_anannas_request_openai,
                            openai_messages,
                            request_model,
                            **openai_params,
                        ),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_anannas_response, resp_raw)
                elif attempt_provider == "featherless":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(
                            make_featherless_request_openai,
                            openai_messages,
                            request_model,
                            **openai_params,
                        ),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_featherless_response, resp_raw)
                elif attempt_provider == "fireworks":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(
                            make_fireworks_request_openai,
                            openai_messages,
                            request_model,
                            **openai_params,
                        ),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_fireworks_response, resp_raw)
                elif attempt_provider == "together":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(
                            make_together_request_openai,
                            openai_messages,
                            request_model,
                            **openai_params,
                        ),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_together_response, resp_raw)
                elif attempt_provider == "huggingface":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(
                            make_huggingface_request_openai,
                            openai_messages,
                            request_model,
                            **openai_params,
                        ),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_huggingface_response, resp_raw)
                elif attempt_provider == "cerebras":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(
                            make_cerebras_request_openai,
                            openai_messages,
                            request_model,
                            **openai_params,
                        ),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_cerebras_response, resp_raw)
                elif attempt_provider == "google-vertex":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(
                            make_google_vertex_request_openai,
                            openai_messages,
                            request_model,
                            **openai_params,
                        ),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_google_vertex_response, resp_raw)
                elif attempt_provider == "vercel-ai-gateway":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(
                            make_vercel_ai_gateway_request_openai,
                            openai_messages,
                            request_model,
                            **openai_params,
                        ),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_vercel_ai_gateway_response, resp_raw)
                elif attempt_provider == "fal":
                    # FAL models are for image/video generation, not chat/messages
                    # Return a clear error message directing users to the correct endpoint
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "error": {
                                "message": f"Model '{request_model}' is a FAL.ai image/video generation model "
                                "and is not available through the messages endpoint. "
                                "Please use the /v1/images/generations endpoint with provider='fal' instead.",
                                "type": "invalid_request_error",
                                "code": "model_not_supported_for_chat",
                            }
                        },
                    )
                else:
                    resp_raw = await asyncio.wait_for(
                        _to_thread(
                            make_openrouter_request_openai,
                            openai_messages,
                            request_model,
                            **openai_params,
                        ),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_openrouter_response, resp_raw)

                provider = attempt_provider
                model = request_model

                # Record successful model call
                request_elapsed = (time.monotonic() - request_start_time) * 1000 if request_start_time else 0
                background_tasks.add_task(
                    record_model_call,
                    provider=attempt_provider,
                    model=request_model,
                    response_time_ms=request_elapsed,
                    status="success",
                )
                break
            except Exception as exc:
                if isinstance(exc, httpx.TimeoutException | asyncio.TimeoutError):
                    logger.warning("Upstream timeout (%s): %s", attempt_provider, exc)
                elif isinstance(exc, httpx.RequestError):
                    logger.warning("Upstream network error (%s): %s", attempt_provider, exc)
                elif isinstance(exc, httpx.HTTPStatusError):
                    logger.debug(
                        "Upstream HTTP error (%s): %s", attempt_provider, exc.response.status_code
                    )
                else:
                    logger.error(
                        "Upstream error for model %s on %s: %s",
                        sanitize_for_logging(request_model),
                        sanitize_for_logging(attempt_provider),
                        sanitize_for_logging(str(exc)),
                    )
                http_exc = map_provider_error(attempt_provider, request_model, exc)

                # Determine error status for health tracking
                if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
                    error_status = "timeout"
                elif isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                    error_status = "rate_limited"
                elif isinstance(exc, httpx.RequestError):
                    error_status = "network_error"
                else:
                    error_status = "error"

                # Record failed model call
                request_elapsed = (time.monotonic() - request_start_time) * 1000 if request_start_time else 0
                background_tasks.add_task(
                    record_model_call,
                    provider=attempt_provider,
                    model=request_model,
                    response_time_ms=request_elapsed,
                    status=error_status,
                    error_message=str(exc)[:500],  # Limit error message length
                )

            if http_exc is None:
                continue

            last_http_exc = http_exc
            if idx < len(provider_chain) - 1 and should_failover(http_exc):
                next_provider = provider_chain[idx + 1]
                logger.warning(
                    "Provider '%s' failed with status %s (%s). Falling back to '%s'.",
                    attempt_provider,
                    http_exc.status_code,
                    http_exc.detail,
                    next_provider,
                )
                continue

            # If this is a 402 (Payment Required) error and we've exhausted the chain,
            # rebuild the chain allowing payment failover to try alternative providers
            if http_exc.status_code == 402 and idx == len(provider_chain) - 1:
                extended_chain = build_provider_failover_chain(provider)
                extended_chain = enforce_model_failover_rules(
                    original_model, extended_chain, allow_payment_failover=True
                )
                extended_chain = filter_by_circuit_breaker(original_model, extended_chain)
                # Find providers we haven't tried yet
                new_providers = [p for p in extended_chain if p not in provider_chain]
                if new_providers:
                    logger.warning(
                        "Provider '%s' returned 402 (Payment Required). "
                        "Extending failover chain with: %s",
                        attempt_provider,
                        new_providers,
                    )
                    provider_chain.extend(new_providers)
                    continue

            raise http_exc

        if processed is None:
            raise last_http_exc or HTTPException(status_code=502, detail="Upstream error")
        """

        # Verify we have a response
        if processed is None:
            raise HTTPException(status_code=502, detail="Upstream error")

        elapsed = max(0.001, time.monotonic() - start)

        # === 4) Usage, pricing, final checks ===
        usage = processed.get("usage", {}) or {}
        total_tokens = usage.get("total_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        post_plan = await _to_thread(enforce_plan_limits, user["id"], total_tokens, environment_tag)
        if not post_plan.get("allowed", False):
            raise HTTPException(
                status_code=429, detail=f"Plan limit exceeded: {post_plan.get('reason', 'unknown')}"
            )

        if trial.get("is_trial") and not trial.get("is_expired"):
            try:
                await _to_thread(
                    track_trial_usage,
                    api_key,
                    total_tokens,
                    1,
                    model_id=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            except Exception as e:
                logger.warning("Failed to track trial usage: %s", e)

        if should_release_concurrency and rate_limit_mgr:
            rl_final = await rate_limit_mgr.check_rate_limit(api_key, tokens_used=total_tokens)
            if not rl_final.allowed:
                await _to_thread(
                    create_rate_limit_alert,
                    api_key,
                    "rate_limit_exceeded",
                    {
                        "reason": rl_final.reason,
                        "retry_after": rl_final.retry_after,
                        "remaining_requests": rl_final.remaining_requests,
                        "remaining_tokens": rl_final.remaining_tokens,
                        "tokens_requested": total_tokens,
                    },
                )
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {rl_final.reason}",
                    headers=(
                        {"Retry-After": str(rl_final.retry_after)} if rl_final.retry_after else None
                    ),
                )

            try:
                await rate_limit_mgr.release_concurrency(api_key)
            except Exception as exc:
                logger.debug("Failed to release concurrency for %s: %s", mask_key(api_key), exc)

        # Use unified credit handler for consistent billing across all endpoints
        from src.services.credit_handler import handle_credits_and_usage

        try:
            cost = await handle_credits_and_usage(
                api_key=api_key,
                user=user,
                model=model,
                trial=trial,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                elapsed_ms=int(elapsed * 1000),
                endpoint="/v1/messages",
                request_id=request_id,
            )
        except ValueError as e:
            # Insufficient credits
            raise HTTPException(status_code=402, detail=str(e))

        await _to_thread(increment_api_key_usage, api_key)

        # === 4.5) Log activity (moved to background for better latency) ===
        try:
            provider_name = get_provider_from_model(model)
            speed = total_tokens / elapsed if elapsed > 0 else 0
            # Run in background to reduce user-perceived latency
            background_tasks.add_task(
                log_activity,
                user_id=user["id"],
                model=model,
                provider=provider_name,
                tokens=total_tokens,
                cost=cost if not trial.get("is_trial", False) else 0.0,
                speed=speed,
                finish_reason=(
                    processed.get("choices", [{}])[0].get("finish_reason", "stop")
                    if processed.get("choices") and len(processed.get("choices")) > 0
                    else "stop"
                ),
                app="API",
                metadata={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "endpoint": "/v1/messages",
                    "session_id": session_id,
                    "gateway": provider,  # Track which gateway was used
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to schedule activity logging for user {user['id']}, model {model}: {e}",
                exc_info=True,
            )

        # === 5) Save chat history (moved to background for better latency) ===
        if session_id:

            def save_chat_history_task():
                """Background task to save chat history without blocking response."""
                try:
                    session = get_chat_session(session_id, user["id"])
                    if session:
                        # Save last user message
                        last_user = None
                        for m in reversed(openai_messages):
                            if m.get("role") == "user":
                                last_user = m
                                break

                        if last_user:
                            user_content = extract_text_from_content(last_user.get("content", ""))
                            save_chat_message(
                                session_id,
                                "user",
                                user_content,
                                model,
                                0,
                                user["id"],
                            )

                        # Save assistant response
                        choices = processed.get("choices", [])
                        assistant_content = ""
                        if choices and len(choices) > 0:
                            assistant_content = choices[0].get("message", {}).get("content", "")
                        if assistant_content:
                            save_chat_message(
                                session_id,
                                "assistant",
                                assistant_content,
                                model,
                                total_tokens,
                                user["id"],
                            )
                except Exception as e:
                    logger.error(
                        f"Failed to save chat history for session {session_id}, user {user['id']}: {e}",
                        exc_info=True,
                    )

            # Run chat history saving in background
            background_tasks.add_task(save_chat_history_task)

        # === 6) Transform response to Anthropic format ===
        anthropic_response = transform_openai_to_anthropic(
            processed, model, stop_sequences=req.stop_sequences
        )

        # Add gateway usage metadata (Gatewayz-specific)
        anthropic_response["gateway_usage"] = {
            "tokens_charged": total_tokens,
            "request_ms": int(elapsed * 1000),
        }
        if not trial.get("is_trial", False):
            anthropic_response["gateway_usage"]["cost_usd"] = round(cost, 6)

        # Capture health metrics (passive monitoring) - run as background task
        background_tasks.add_task(
            capture_model_health,
            provider=provider,
            model=model,
            response_time_ms=elapsed * 1000,
            status="success",
            usage={
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        )

        # Save chat completion request metadata to database - run as background task
        background_tasks.add_task(
            chat_completion_requests_module.save_chat_completion_request,
            request_id=request_id,
            model_name=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            processing_time_ms=int(elapsed * 1000),
            status="completed",
            error_message=None,
            user_id=user["id"],
            provider_name=provider,
            model_id=None,
            api_key_id=api_key_id,
        )

        # Prepare headers including rate limit information
        headers = {}
        if rl_final is not None:
            headers.update(get_rate_limit_headers(rl_final))
        elif rl_pre is not None:
            headers.update(get_rate_limit_headers(rl_pre))

        return JSONResponse(content=anthropic_response, headers=headers)

    except HTTPException as http_exc:
        # Save failed request for HTTPException errors
        if request_id:
            try:
                # Calculate elapsed time
                error_elapsed = time.monotonic() - start if "start" in dir() else 0

                # Save failed request to database
                await _to_thread(
                    chat_completion_requests_module.save_chat_completion_request,
                    request_id=request_id,
                    model_name=model if "model" in dir() else req.model,
                    input_tokens=prompt_tokens if "prompt_tokens" in dir() else 0,
                    output_tokens=0,  # No output on error
                    processing_time_ms=int(error_elapsed * 1000),
                    status="failed",
                    error_message=f"HTTP {http_exc.status_code}: {http_exc.detail}",
                    user_id=user["id"] if user and "user" in dir() else None,
                    provider_name=provider if "provider" in dir() else None,
                    model_id=None,
                    api_key_id=api_key_id if "api_key_id" in dir() else None,
                )
            except Exception as save_err:
                logger.debug(f"Failed to save failed request metadata: {save_err}")
        raise
    except Exception as e:
        logger.exception("Unhandled server error in anthropic_messages")

        # Save failed request for unexpected errors
        if request_id:
            try:
                # Calculate elapsed time
                error_elapsed = time.monotonic() - start if "start" in dir() else 0

                # Save failed request to database
                await _to_thread(
                    chat_completion_requests_module.save_chat_completion_request,
                    request_id=request_id,
                    model_name=model if "model" in dir() else req.model,
                    input_tokens=prompt_tokens if "prompt_tokens" in dir() else 0,
                    output_tokens=0,  # No output on error
                    processing_time_ms=int(error_elapsed * 1000),
                    status="failed",
                    error_message=f"{type(e).__name__}: {str(e)[:500]}",
                    user_id=user["id"] if user and "user" in dir() else None,
                    provider_name=provider if "provider" in dir() else None,
                    model_id=None,
                    api_key_id=api_key_id if "api_key_id" in dir() else None,
                )
            except Exception as save_err:
                logger.debug(f"Failed to save failed request metadata: {save_err}")

        raise HTTPException(status_code=500, detail="Internal server error")
