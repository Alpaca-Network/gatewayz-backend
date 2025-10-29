"""
Anthropic Messages API endpoint
Compatible with Claude API: https://docs.claude.com/en/api/messages
"""

import logging
import asyncio
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from typing import Optional

import src.db.api_keys as api_keys_module
import src.db.plans as plans_module
import src.db.rate_limits as rate_limits_module
import src.db.users as users_module
import src.db.chat_history as chat_history_module
import src.db.activity as activity_module
from src.schemas import MessagesRequest
from src.security.deps import require_authenticated_api_key as get_api_key
from src.services.openrouter_client import make_openrouter_request_openai, process_openrouter_response
from src.services.portkey_client import make_portkey_request_openai, process_portkey_response
from src.services.featherless_client import make_featherless_request_openai, process_featherless_response
from src.services.fireworks_client import make_fireworks_request_openai, process_fireworks_response
from src.services.together_client import make_together_request_openai, process_together_response
from src.services.huggingface_client import make_huggingface_request_openai, process_huggingface_response
from src.services.model_transformations import detect_provider_from_model_id, transform_model_id
from src.services.provider_failover import build_provider_failover_chain, map_provider_error, should_failover
import src.services.rate_limiting as rate_limiting_service
import src.services.trial_validation as trial_module
from src.services.pricing import calculate_cost
from src.services.anthropic_transformer import (
    transform_anthropic_to_openai,
    transform_openai_to_anthropic,
    extract_text_from_content
)

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


def _fallback_get_user(api_key: str):
    try:
        supabase_module = importlib.import_module("src.config.supabase_config")
        client = supabase_module.get_supabase_client()
        result = client.table("users").select("*").eq("api_key", api_key).execute()
        if result.data:
            logging.getLogger(__name__).debug("Messages fallback user lookup succeeded for %s", api_key)
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


@router.post("/v1/messages", tags=["chat"])
async def anthropic_messages(
    req: MessagesRequest,
    api_key: str = Depends(get_api_key),
    session_id: Optional[int] = Query(None, description="Chat session ID to save messages to"),
    request: Request = None
):
    """
    Anthropic Messages API endpoint (Claude API compatible).

    This endpoint accepts Anthropic-style requests and transforms them to work
    with OpenAI-compatible providers (OpenRouter, Portkey, Featherless).

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
    if Config.IS_TESTING and request:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            api_key = auth_header.split(" ", 1)[1].strip()

    logger.info("anthropic_messages start (api_key=%s, model=%s)", mask_key(api_key), req.model)
    logger.debug("Messages endpoint Config.IS_TESTING=%s", Config.IS_TESTING)

    try:
        # === 1) User + plan/trial prechecks ===
        user = await _to_thread(get_user, api_key)
        if not user and not Config.IS_TESTING:
            user = await _to_thread(_fallback_get_user, api_key)
        if not user:
            logger.warning("Invalid API key or user not found for key %s", mask_key(api_key))
            raise HTTPException(status_code=401, detail="Invalid API key")

        environment_tag = user.get("environment_tag", "live")

        pre_plan = await _to_thread(enforce_plan_limits, user["id"], 0, environment_tag)
        if not pre_plan.get("allowed", False):
            raise HTTPException(status_code=429, detail=f"Plan limit exceeded: {pre_plan.get('reason', 'unknown')}")

        trial = await _to_thread(validate_trial_access, api_key)
        if not trial.get("is_valid", False):
            if trial.get("is_trial") and trial.get("is_expired"):
                raise HTTPException(
                    status_code=403,
                    detail=trial["error"],
                    headers={"X-Trial-Expired": "true", "X-Trial-End-Date": trial.get("trial_end_date", "")},
                )
            elif trial.get("is_trial"):
                headers = {}
                for k in ("remaining_tokens", "remaining_requests", "remaining_credits"):
                    if k in trial:
                        headers[f"X-Trial-{k.replace('_','-').title()}"] = str(trial[k])
                raise HTTPException(status_code=429, detail=trial["error"], headers=headers)
            else:
                raise HTTPException(status_code=403, detail=trial.get("error", "Access denied"))

        rate_limit_mgr = get_rate_limit_manager()
        should_release_concurrency = not trial.get("is_trial", False)

        if should_release_concurrency:
            rl_pre = await rate_limit_mgr.check_rate_limit(api_key, tokens_used=0)
            if not rl_pre.allowed:
                await _to_thread(create_rate_limit_alert, api_key, "rate_limit_exceeded", {
                    "reason": rl_pre.reason,
                    "retry_after": rl_pre.retry_after,
                    "remaining_requests": rl_pre.remaining_requests,
                    "remaining_tokens": rl_pre.remaining_tokens
                })
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {rl_pre.reason}",
                    headers={"Retry-After": str(rl_pre.retry_after)} if rl_pre.retry_after else None
                )

        if not trial.get("is_trial", False) and user.get("credits", 0.0) <= 0:
            raise HTTPException(status_code=402, detail="Insufficient credits")

        # === 2) Transform Anthropic format to OpenAI format ===
        messages_data = [msg.model_dump() for msg in req.messages]
        openai_messages, openai_params = transform_anthropic_to_openai(
            messages=messages_data,
            system=req.system,
            max_tokens=req.max_tokens,
            temperature=req.temperature,
            top_p=req.top_p,
            top_k=req.top_k,
            stop_sequences=req.stop_sequences
        )

        # === 2.1) Inject conversation history if session_id provided ===
        if session_id:
            try:
                session = await _to_thread(get_chat_session, session_id, user['id'])
                if session and session.get('messages'):
                    history_messages = [
                        {"role": msg["role"], "content": msg["content"]}
                        for msg in session['messages']
                    ]
                    # Insert history after system message (if present)
                    if openai_messages and openai_messages[0].get("role") == "system":
                        openai_messages = [openai_messages[0]] + history_messages + openai_messages[1:]
                    else:
                        openai_messages = history_messages + openai_messages
                    logger.info(f"Injected {len(history_messages)} messages from session {session_id}")
            except Exception as e:
                logger.warning(f"Failed to fetch chat history for session {session_id}: {e}")

        original_model = req.model

        # Auto-detect provider
        provider = (req.provider or "openrouter").lower()

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
                    logger.info(f"Auto-detected provider '{provider}' for model {original_model}")
                else:
                    # Fallback to checking cached models
                    from src.services.models import get_cached_models

                    # Try each provider with transformation
                    for test_provider in ["huggingface", "featherless", "fireworks", "together", "portkey"]:
                        transformed = transform_model_id(original_model, test_provider)
                        provider_models = get_cached_models(test_provider) or []
                        if any(m.get("id") == transformed for m in provider_models):
                            provider = test_provider
                            logger.info(f"Auto-detected provider '{provider}' for model {original_model} (transformed to {transformed})")
                            break
                    # Otherwise default to openrouter (already set)

        provider_chain = build_provider_failover_chain(provider)
        model = original_model

        # === 3) Call upstream with failover ===
        start = time.monotonic()
        processed = None
        last_http_exc = None

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
                logger.debug("Using extended timeout %ss for provider %s", request_timeout, attempt_provider)

            http_exc = None
            try:
                if attempt_provider == "portkey":
                    if Config.IS_TESTING:
                        logger.info(
                            "Messages: using mocked openrouter path for portkey in tests (Config.IS_TESTING=%s)",
                            Config.IS_TESTING,
                        )
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
                    else:
                        logger.info(
                            "Messages: calling real portkey provider (Config.IS_TESTING=%s)",
                            Config.IS_TESTING,
                        )
                        portkey_provider = req.portkey_provider or "anthropic"
                        portkey_virtual_key = getattr(req, "portkey_virtual_key", None)
                        resp_raw = await asyncio.wait_for(
                            _to_thread(
                                make_portkey_request_openai,
                                openai_messages,
                                request_model,
                                portkey_provider,
                                portkey_virtual_key,
                                **openai_params,
                            ),
                            timeout=request_timeout,
                        )
                        processed = await _to_thread(process_portkey_response, resp_raw)
                elif attempt_provider == "featherless":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(make_featherless_request_openai, openai_messages, request_model, **openai_params),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_featherless_response, resp_raw)
                elif attempt_provider == "fireworks":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(make_fireworks_request_openai, openai_messages, request_model, **openai_params),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_fireworks_response, resp_raw)
                elif attempt_provider == "together":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(make_together_request_openai, openai_messages, request_model, **openai_params),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_together_response, resp_raw)
                elif attempt_provider == "huggingface":
                    resp_raw = await asyncio.wait_for(
                        _to_thread(make_huggingface_request_openai, openai_messages, request_model, **openai_params),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_huggingface_response, resp_raw)
                else:
                    resp_raw = await asyncio.wait_for(
                        _to_thread(make_openrouter_request_openai, openai_messages, request_model, **openai_params),
                        timeout=request_timeout,
                    )
                    processed = await _to_thread(process_openrouter_response, resp_raw)

                provider = attempt_provider
                model = request_model
                break
            except Exception as exc:
                if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)):
                    logger.warning("Upstream timeout (%s): %s", attempt_provider, exc)
                elif isinstance(exc, httpx.RequestError):
                    logger.warning("Upstream network error (%s): %s", attempt_provider, exc)
                elif isinstance(exc, httpx.HTTPStatusError):
                    logger.debug(
                        "Upstream HTTP error (%s): %s", attempt_provider, exc.response.status_code
                    )
                else:
                    logger.error(f"Upstream error for model {request_model} on {attempt_provider}: {exc}")
                http_exc = map_provider_error(attempt_provider, request_model, exc)

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

            raise http_exc

        if processed is None:
            raise last_http_exc or HTTPException(status_code=502, detail="Upstream error")

        elapsed = max(0.001, time.monotonic() - start)

        # === 4) Usage, pricing, final checks ===
        usage = processed.get("usage", {}) or {}
        total_tokens = usage.get("total_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        post_plan = await _to_thread(enforce_plan_limits, user["id"], total_tokens, environment_tag)
        if not post_plan.get("allowed", False):
            raise HTTPException(status_code=429, detail=f"Plan limit exceeded: {post_plan.get('reason', 'unknown')}")

        if trial.get("is_trial") and not trial.get("is_expired"):
            try:
                await _to_thread(track_trial_usage, api_key, total_tokens, 1)
            except Exception as e:
                logger.warning("Failed to track trial usage: %s", e)

        if should_release_concurrency and rate_limit_mgr:
            rl_final = await rate_limit_mgr.check_rate_limit(api_key, tokens_used=total_tokens)
            if not rl_final.allowed:
                await _to_thread(create_rate_limit_alert, api_key, "rate_limit_exceeded", {
                    "reason": rl_final.reason,
                    "retry_after": rl_final.retry_after,
                    "remaining_requests": rl_final.remaining_requests,
                    "remaining_tokens": rl_final.remaining_tokens,
                    "tokens_requested": total_tokens
                })
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded: {rl_final.reason}",
                    headers={"Retry-After": str(rl_final.retry_after)} if rl_final.retry_after else None
                )

            try:
                await rate_limit_mgr.release_concurrency(api_key)
            except Exception as exc:
                logger.debug("Failed to release concurrency for %s: %s", mask_key(api_key), exc)

        cost = calculate_cost(model, prompt_tokens, completion_tokens)

        if not trial.get("is_trial", False):
            try:
                await _to_thread(deduct_credits, api_key, cost, f"API usage - {model}", {
                    "model": model,
                    "total_tokens": total_tokens,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "cost_usd": cost,
                })
                await _to_thread(record_usage, user["id"], api_key, model, total_tokens, cost, int(elapsed * 1000))
            except ValueError as e:
                raise HTTPException(status_code=402, detail=str(e))
            except Exception as e:
                logger.error("Usage recording error: %s", e)

            await _to_thread(update_rate_limit_usage, api_key, total_tokens)

        await _to_thread(increment_api_key_usage, api_key)

        # === 4.5) Log activity ===
        try:
            provider_name = get_provider_from_model(model)
            speed = total_tokens / elapsed if elapsed > 0 else 0
            await _to_thread(
                log_activity,
                user_id=user["id"],
                model=model,
                provider=provider_name,
                tokens=total_tokens,
                cost=cost if not trial.get("is_trial", False) else 0.0,
                speed=speed,
                finish_reason=processed.get("choices", [{}])[0].get("finish_reason", "stop"),
                app="API",
                metadata={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "endpoint": "/v1/messages",
                    "session_id": session_id,
                    "gateway": provider  # Track which gateway was used
                }
            )
        except Exception as e:
            logger.warning(f"Failed to log activity: {e}")

        # === 5) Save chat history ===
        if session_id:
            try:
                session = await _to_thread(get_chat_session, session_id, user["id"])
                if session:
                    # Save last user message
                    last_user = None
                    for m in reversed(openai_messages):
                        if m.get("role") == "user":
                            last_user = m
                            break

                    if last_user:
                        user_content = extract_text_from_content(last_user.get("content", ""))
                        await _to_thread(save_chat_message, session_id, "user", user_content, model, 0)

                    # Save assistant response
                    assistant_content = processed.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if assistant_content:
                        await _to_thread(save_chat_message, session_id, "assistant", assistant_content, model, total_tokens)
            except Exception as e:
                logger.warning("Failed to save chat history: %s", e)

        # === 6) Transform response to Anthropic format ===
        anthropic_response = transform_openai_to_anthropic(processed, model)

        # Add gateway usage metadata (Gatewayz-specific)
        anthropic_response["gateway_usage"] = {
            "tokens_charged": total_tokens,
            "request_ms": int(elapsed * 1000),
        }
        if not trial.get("is_trial", False):
            anthropic_response["gateway_usage"]["cost_usd"] = round(cost, 6)

        return anthropic_response

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unhandled server error in anthropic_messages")
        raise HTTPException(status_code=500, detail="Internal server error")
from src.config import Config
import importlib
import src.config.supabase_config as supabase_config

# When running in test mode we reuse OpenRouter client for providers that
# normally rely on external credentials, so unit tests can stub a single path
if Config.IS_TESTING:
    make_portkey_request_openai = make_openrouter_request_openai
    process_portkey_response = process_openrouter_response
