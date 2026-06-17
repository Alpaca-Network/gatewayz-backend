import asyncio
import importlib
import logging
import time
import uuid
from contextvars import ContextVar

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import src.db.activity as activity_module
import src.db.api_keys as api_keys_module
import src.db.chat_history as chat_history_module
import src.db.plans as plans_module
import src.db.rate_limits as rate_limits_module
import src.db.users as users_module
from src.config import Config
from src.db.chat_completion_requests_enhanced import save_chat_completion_request_with_cost
from src.schemas import ProxyRequest
from src.security.deps import get_optional_api_key
from src.services.anonymous_rate_limiter import (
    ANONYMOUS_ALLOWED_MODELS,
    ANONYMOUS_DAILY_LIMIT,
    get_anonymous_rate_limit_headers,
    validate_anonymous_request,
)
from src.services.passive_health_monitor import capture_model_health
from src.services.prometheus_metrics import (
    record_free_model_usage,
)
from src.utils.exceptions import APIExceptions
from src.utils.performance_tracker import PerformanceTracker
from src.utils.rate_limit_headers import get_rate_limit_headers

# Optional Traceloop integration - gracefully handle if not installed
try:
    from src.config.traceloop_config import set_association_properties as set_traceloop_properties
except ImportError:
    # Traceloop not available - provide no-op function
    def set_traceloop_properties(**kwargs):
        pass


# Unified chat handler and adapters for chat unification
from src.handlers.error_persistence import save_failed_request

# Request correlation ID for distributed tracing
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


# Braintrust removed for cost reduction (see docs/superpowers/specs/2026-05-25-cost-reduction-design.md)
def traced(*args, **kwargs):
    if args and callable(args[0]):
        return args[0]

    def _wrap(fn):
        return fn

    return _wrap


def check_braintrust_available():
    return False


def braintrust_flush():
    return None


# Import provider registry from canonical module (breaks circular dep with chat_handler.py)
from src.handlers.provider_registry import (
    PROVIDER_FUNCTIONS,
    _provider_import_errors,
    _safe_import_provider,
)

# Inject provider functions into this module's globals for backward compatibility.
# Tests patch src.routes.chat.make_openrouter_request_openai etc., so these
# functions must exist as attributes of the chat module.
_current_globals = globals()
for _prov_name, _func_names in PROVIDER_FUNCTIONS.items():
    _prov_module = _safe_import_provider(_prov_name, _func_names)
    for _fn in _func_names:
        _current_globals[_fn] = _prov_module.get(_fn)


import src.services.rate_limiting as rate_limiting_service
import src.services.trial_validation as trial_module

# Step-3 dispatch (streaming / non-streaming provider-call loops) lives in chat_dispatch.
from src.routes.chat_dispatch import dispatch_non_streaming, dispatch_streaming  # noqa: E402
from src.security.inference_gates import (
    enforce_anonymous_gate,
    enforce_model_pricing_gate,
    enforce_subscription_status_gate,
)
from src.services.pricing import calculate_cost_async, get_model_pricing_async
from src.utils.token_estimator import estimate_message_tokens


# Backwards compatibility wrappers for test patches
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


def create_chat_session(*args, **kwargs):
    return chat_history_module.create_chat_session(*args, **kwargs)


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


logger = logging.getLogger(__name__)
router = APIRouter()

# Log module initialization to help debug route loading
logger.info("🔄 Chat module initialized - router created")
logger.info(f"   Router type: {type(router)}")


# Auto-routing constants
# Using "router" prefix to avoid confusion with OpenRouter's "openrouter/auto" model
AUTO_ROUTE_MODEL_PREFIX = "router"

# Code router constants
CODE_ROUTER_PREFIX = "router:code"


def validate_trial_with_free_model_bypass(
    trial: dict,
    model_id: str,
    request_id: str,
    api_key: str | None,
    logger_instance,
) -> dict:
    """Validate trial access with free model bypass logic.

    Allows expired trials and trials with exceeded limits to access free models.
    Records metrics and logs for free model access.

    Args:
        trial: Trial validation result from validate_trial_access()
        model_id: The requested model identifier
        request_id: Request correlation ID for logging
        api_key: The API key (will be masked in logs)
        logger_instance: Logger to use for logging

    Returns:
        Updated trial dict with is_valid=True if free model bypass applies

    Raises:
        HTTPException: If trial is invalid and model is not free
    """
    model_is_free = is_free_model(model_id)

    # IMPORTANT: Work with a copy to avoid mutating the cached trial dict.
    # The trial dict may be cached in _trial_cache and mutating it would corrupt
    # the cache, potentially allowing expired trials to access premium models.
    trial = trial.copy()

    if not trial.get("is_valid", False):
        if trial.get("is_trial") and trial.get("is_expired"):
            if model_is_free:
                # Allow expired trial to use free model - log and track this access
                logger_instance.info(
                    "Expired trial accessing free model (request_id=%s, api_key=%s, model=%s, trial_end_date=%s)",
                    request_id,
                    mask_key(api_key) if api_key else "unknown",
                    model_id,
                    trial.get("trial_end_date", "unknown"),
                    extra={"request_id": request_id, "free_model_bypass": True},
                )
                record_free_model_usage("expired_trial", model_id)
                # Mark trial as valid for this request (free model access)
                trial["is_valid"] = True
                trial["free_model_bypass"] = True
            else:
                raise HTTPException(
                    status_code=403,
                    detail=trial["error"],
                    headers={
                        "X-Trial-Expired": "true",
                        "X-Trial-End-Date": trial.get("trial_end_date", ""),
                    },
                )
        elif trial.get("is_trial"):
            # Trial limits exceeded (tokens/requests/credits)
            if model_is_free:
                # Allow trial with exceeded limits to use free model
                logger_instance.info(
                    "Trial with exceeded limits accessing free model (request_id=%s, api_key=%s, model=%s, error=%s)",
                    request_id,
                    mask_key(api_key) if api_key else "unknown",
                    model_id,
                    trial.get("error", "unknown"),
                    extra={"request_id": request_id, "free_model_bypass": True},
                )
                record_free_model_usage("active_trial", model_id)
                # Mark trial as valid for this request (free model access)
                trial["is_valid"] = True
                trial["free_model_bypass"] = True
            else:
                headers = {}
                for k in ("remaining_tokens", "remaining_requests", "remaining_credits"):
                    if k in trial:
                        headers[f"X-Trial-{k.replace('_', '-').title()}"] = str(trial[k])
                raise HTTPException(status_code=429, detail=trial["error"], headers=headers)
        else:
            raise APIExceptions.forbidden(detail=trial.get("error", "Access denied"))
    elif model_is_free:
        # Track free model usage for valid trials and paid users too
        if trial.get("is_trial"):
            record_free_model_usage("active_trial", model_id)
        else:
            record_free_model_usage("paid", model_id)

    return trial


# Post-processing functions extracted to src/handlers/post_processing.py
# Re-exported here so existing patches at src.routes.chat.* continue to work.
from src.handlers.post_processing import (  # noqa: F401
    _ensure_plan_capacity,
    _handle_credits_and_usage,
    _handle_credits_and_usage_with_fallback,
    _process_stream_completion_background,
    _record_inference_metrics_and_health,
)
from src.routes.chat_helpers import (  # noqa: F401
    _get_auto_route_default_model,
    _get_code_router_default_model,
    _to_thread,
    is_free_model,
    mask_key,
    validate_and_adjust_max_tokens,
)


def _fallback_get_user(api_key: str):
    try:
        supabase_module = importlib.import_module("src.config.supabase_config")
        client = supabase_module.get_supabase_client()
        result = client.table("users").select("*").eq("api_key", api_key).execute()
        if result.data:
            logging.getLogger(__name__).debug("Fallback user lookup succeeded for %s", api_key)
            return result.data[0]
        logging.getLogger(__name__).debug(
            "Fallback lookup found no data; table snapshot=%s",
            client.table("users").select("*").execute().data,
        )
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "Fallback user lookup error for %s: %s", mask_key(api_key), exc
        )
        return None
    return None


from src.routes.chat_context import inject_conversation_history, persist_conversation_turn
from src.routes.chat_request import prepare_upstream_request
from src.routes.chat_routing import resolve_model_routing
from src.routes.chat_streaming import stream_generator  # noqa: F401

# Log route registration for debugging
logger.info("📍 Registering /chat/completions endpoint")


@router.post("/chat/completions", tags=["chat"])
@traced(name="chat_completions", type="llm")
async def chat_completions(
    req: ProxyRequest,
    background_tasks: BackgroundTasks,
    api_key: str | None = Depends(get_optional_api_key),
    session_id: int | None = Query(None, description="Chat session ID to save messages to"),
    request: Request = None,
):
    # === 0) Setup / sanity ===
    # Generate request correlation ID for distributed tracing
    request_id = str(uuid.uuid4())
    request_id_var.set(request_id)

    # Never print keys; log masked
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

    # Determine if this is an authenticated or anonymous request
    is_anonymous = api_key is None

    logger.info(
        "chat_completions start (request_id=%s, api_key=%s, model=%s, anonymous=%s)",
        request_id,
        mask_key(api_key) if api_key else "anonymous",
        req.model,
        is_anonymous,
        extra={"request_id": request_id},
    )

    # Abuse-control gates (anonymous + unpriced models). Centralized helpers so
    # /v1/images and /v1/audio can adopt the same policy.
    enforce_anonymous_gate(is_anonymous, request_id=request_id, model_id=req.model)
    await enforce_model_pricing_gate(
        req.model,
        request_id=request_id,
        api_key_mask=mask_key(api_key) if api_key else "anonymous",
    )

    # Initialize performance tracker
    tracker = PerformanceTracker(endpoint="/v1/chat/completions")

    try:
        # === 1) User + plan/trial prechecks (OPTIMIZED: parallelized DB calls) ===
        with tracker.stage("auth_validation"):
            if is_anonymous:
                # Anonymous user - validate model whitelist and rate limits
                # Get client IP for rate limiting
                client_ip = "unknown"
                if request:
                    # Parse X-Forwarded-For header with defensive bounds checking
                    forwarded_for = request.headers.get("X-Forwarded-For", "")
                    if forwarded_for:
                        parts = forwarded_for.split(",")
                        if parts:  # Defensive check (split always returns at least [''])
                            client_ip = parts[0].strip()

                    if not client_ip:
                        client_ip = request.headers.get("X-Real-IP", "")
                    if not client_ip and hasattr(request, "client") and request.client:
                        client_ip = request.client.host or "unknown"

                # Validate anonymous request (model whitelist + rate limit)
                anon_validation = await _to_thread(validate_anonymous_request, client_ip, req.model)
                if not anon_validation["allowed"]:
                    logger.warning(
                        "Anonymous request denied (request_id=%s, ip=%s, model=%s, reason=%s)",
                        request_id,
                        client_ip[:16] + "..." if len(client_ip) > 16 else client_ip,
                        req.model,
                        anon_validation["reason"][:50],
                    )
                    # Return appropriate error based on failure type
                    if not anon_validation.get("model_allowed", True):
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "error": {
                                    "message": anon_validation["reason"],
                                    "type": "model_not_allowed",
                                    "code": "anonymous_model_restricted",
                                    "allowed_models": ANONYMOUS_ALLOWED_MODELS[:5],
                                }
                            },
                        )
                    else:
                        raise HTTPException(
                            status_code=429,
                            detail={
                                "error": {
                                    "message": anon_validation["reason"],
                                    "type": "rate_limit_exceeded",
                                    "code": "anonymous_daily_limit",
                                }
                            },
                            headers=get_anonymous_rate_limit_headers(
                                limit=ANONYMOUS_DAILY_LIMIT,
                                remaining=0,
                            ),
                        )

                user = None
                api_key_id = None
                trial = {"is_valid": True, "is_trial": False, "is_anonymous": True}
                environment_tag = "live"
                logger.info(
                    "Processing anonymous chat request (request_id=%s, ip=%s, model=%s, remaining=%d)",
                    request_id,
                    client_ip[:16] + "..." if len(client_ip) > 16 else client_ip,
                    req.model,
                    anon_validation.get("remaining_requests", 0),
                )

                # Track anonymous request (no API key)
                try:
                    from src.services.prometheus_metrics import api_key_tracking_failures

                    api_key_tracking_failures.labels(reason="anonymous").inc()
                except ImportError:
                    pass
            else:
                # Authenticated user - perform full validation
                # OPTIMIZED: Run auth operations in parallel to reduce overhead from 200-500ms → 100-150ms
                from src.utils.api_key_lookup import get_api_key_id_with_retry

                # Parallelize independent auth operations
                user_task = _to_thread(get_user, api_key)
                api_key_id_task = get_api_key_id_with_retry(api_key, max_retries=3, retry_delay=0.1)
                trial_task = _to_thread(validate_trial_access, api_key)

                # Wait for all operations to complete in parallel
                user, api_key_id, trial = await asyncio.gather(
                    user_task,
                    api_key_id_task,
                    trial_task,
                )

                # Fallback user lookup if primary lookup failed (testing only)
                if not user and Config.IS_TESTING:
                    logger.debug("Fallback user lookup invoked for %s", mask_key(api_key))
                    user = await _to_thread(_fallback_get_user, api_key)

                # Validate user exists
                if not user:
                    logger.warning(
                        "Invalid API key or user not found for key %s", mask_key(api_key)
                    )
                    raise APIExceptions.invalid_api_key()

                enforce_subscription_status_gate(user, request_id=request_id)

                # Track API key ID lookup results
                if api_key_id is None:
                    logger.warning(
                        "Could not retrieve API key ID for tracking (request_id=%s, key=%s)",
                        request_id,
                        mask_key(api_key),
                    )
                else:
                    # Track successful API key tracking
                    try:
                        from src.services.prometheus_metrics import api_key_tracking_success

                        api_key_tracking_success.labels(request_type="authenticated").inc()
                    except ImportError:
                        pass

                environment_tag = user.get("environment_tag", "live")

                # Set Traceloop association properties for customer tracking (OpenLLMetry)
                # This enables model popularity tracking by user/customer ID
                set_traceloop_properties(
                    user_id=str(user.get("id", "")),
                    api_key_id=str(api_key_id) if api_key_id else None,
                    model=req.model,
                )

        # Validate trial access with free model bypass (only for authenticated users)
        if not is_anonymous:
            trial = validate_trial_with_free_model_bypass(
                trial=trial,
                model_id=req.model,
                request_id=request_id,
                api_key=api_key,
                logger_instance=logger,
            )

        # Fast-fail requests that would exceed plan limits before hitting any upstream provider
        # (only for authenticated users)
        if not is_anonymous:
            await _ensure_plan_capacity(user["id"], environment_tag)

        rate_limit_mgr = get_rate_limit_manager()
        should_release_concurrency = not trial.get("is_trial", False) and not is_anonymous

        # Pre-check plan limits before making any upstream calls (only for authenticated users)
        if not is_anonymous:
            pre_plan = await _to_thread(enforce_plan_limits, user["id"], 0, environment_tag)
            if not pre_plan.get("allowed", False):
                raise APIExceptions.plan_limit_exceeded(reason=pre_plan.get("reason", "unknown"))

        # Allow disabling rate limiting for testing (DEV ONLY) or internal live-test calls.
        # is_live_test is set by security_middleware after validating X-Internal-Source + ADMIN_API_KEY.
        import os

        disable_rate_limiting = os.getenv(
            "DISABLE_RATE_LIMITING", "false"
        ).lower() == "true" or bool(request and getattr(request.state, "is_live_test", False))

        # Initialize rate limit variables
        rl_pre = None
        rl_final = None

        # Rate limiting (only for authenticated non-trial users)
        if (
            not is_anonymous
            and not trial.get("is_trial", False)
            and rate_limit_mgr
            and not disable_rate_limiting
        ):
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
                raise APIExceptions.rate_limited(
                    retry_after=rl_pre.retry_after,
                    reason=rl_pre.reason,
                    rate_limit_headers=get_rate_limit_headers(rl_pre),
                )

        # Credit check (only for authenticated non-trial users)
        # Uses cost-based pre-check instead of simple balance > 0
        if not is_anonymous and not trial.get("is_trial", False) and not is_free_model(req.model):
            from src.services.billing.credit_precheck import estimate_and_check_credits

            _user_credits = float(user.get("subscription_allowance") or 0) + float(
                user.get("purchased_credits") or 0
            )
            if _user_credits <= 0:
                raise APIExceptions.payment_required(credits=_user_credits)
            _msgs = [{"role": m.role, "content": m.content} for m in req.messages]
            _precheck = estimate_and_check_credits(
                model_id=req.model,
                messages=_msgs,
                user_credits=_user_credits,
                max_tokens=getattr(req, "max_tokens", None),
            )
            if not _precheck["allowed"] and _precheck.get("capped_max_tokens") is None:
                raise APIExceptions.payment_required(credits=_user_credits)

        # Pricing pre-check: block high-value models without pricing BEFORE
        # hitting any upstream provider. get_model_pricing_async() raises ValueError
        # for high-value models if only default pricing is available.
        if not is_anonymous and not trial.get("is_trial", False):
            try:
                await get_model_pricing_async(req.model)
            except ValueError as pricing_err:
                err_str = str(pricing_err)
                is_pricing_missing = (
                    "Pricing data not available" in err_str
                    or "HIGH_VALUE_MODEL_PRICING_MISSING" in err_str
                )
                if is_pricing_missing:
                    logger.warning(
                        "Pricing pre-check failed (request_id=%s, model=%s): %s",
                        request_id,
                        req.model,
                        err_str,
                    )
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error": {
                                "message": err_str,
                                "type": "pricing_unavailable",
                                "code": "model_pricing_missing",
                            }
                        },
                    )
                # Unknown ValueError — let it propagate as 500
                raise

        # Pre-check plan limits before streaming (fail fast) - only for authenticated users
        if not is_anonymous:
            pre_plan = await _to_thread(enforce_plan_limits, user["id"], 0, environment_tag)
            if not pre_plan.get("allowed", False):
                raise APIExceptions.plan_limit_exceeded(reason=pre_plan.get("reason", "unknown"))

        # === 2) Build upstream request ===
        with tracker.stage("request_parsing"):
            messages = [m.model_dump() for m in req.messages]

        # === 2.1) Inject conversation history if session_id provided ===
        messages, session_id = await inject_conversation_history(
            session_id, is_anonymous, user, messages
        )

        # === 2.1.5) Auto Web Search - start search in parallel to hide latency ===
        web_search_task = None  # Will hold the async task if search is triggered

        # Get auto_web_search setting (default to "auto")
        auto_web_search = getattr(req, "auto_web_search", "auto")
        web_search_threshold = getattr(req, "web_search_threshold", None)
        if web_search_threshold is None:
            web_search_threshold = 0.5

        # Determine if we should perform web search (classifier is ~0.06ms, negligible)
        should_search = False
        search_query = None

        if auto_web_search is True:
            # Explicit enable - always search
            should_search = True
            logger.debug("Auto web search explicitly enabled")
        elif auto_web_search == "auto":
            # Auto mode - use query classifier
            try:
                from src.services.query_classifier import should_auto_search

                should_search, web_search_classification = should_auto_search(
                    messages=messages,
                    threshold=web_search_threshold,
                    enabled=True,
                )
                if should_search:
                    logger.info(
                        "Auto web search triggered: confidence=%.2f, reason=%s",
                        web_search_classification.confidence,
                        web_search_classification.reason,
                    )
            except Exception as e:
                logger.warning("Query classification failed, skipping auto search: %s", str(e))
                should_search = False

        # Start web search task in parallel (non-blocking) to hide latency
        if should_search:
            # Extract search query
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        search_query = content
                    elif isinstance(content, list):
                        text_parts = [
                            p.get("text", "") if isinstance(p, dict) else str(p)
                            for p in content
                            if (isinstance(p, dict) and p.get("type") == "text")
                            or isinstance(p, str)
                        ]
                        search_query = " ".join(text_parts)
                    break

            if search_query and len(search_query.strip()) > 0:
                # Start search as background task - runs in parallel with provider detection
                from src.services.tools import execute_tool

                logger.info("Starting parallel web search for: %s...", search_query[:50])
                web_search_task = asyncio.create_task(
                    execute_tool(
                        "web_search",
                        {
                            "query": search_query,
                            "max_results": 5,
                            "include_answer": True,
                            "search_depth": "basic",
                        },
                    )
                )

        # === 2.2) Plan limit pre-check with estimated tokens (only for authenticated users) ===
        estimated_tokens = estimate_message_tokens(messages, getattr(req, "max_tokens", None))
        if not is_anonymous:
            pre_plan = await _to_thread(
                enforce_plan_limits, user["id"], estimated_tokens, environment_tag
            )
            if not pre_plan.get("allowed", False):
                raise HTTPException(
                    status_code=429,
                    detail=f"Plan limit exceeded: {pre_plan.get('reason', 'unknown')}",
                )

        # Store original model for response and routing logic
        original_model = req.model
        is_auto_route = original_model and original_model.lower().startswith(
            AUTO_ROUTE_MODEL_PREFIX
        )

        # === 2.3-2.5) Model routing (auto / general / code) ===
        code_router_decision, is_code_route = await resolve_model_routing(
            req, original_model, messages, session_id, user, is_auto_route, tracker
        )

        # === 2.6) Prepare upstream request (params + provider + failover chain) ===
        model, provider, provider_chain, optional = await prepare_upstream_request(
            req, original_model, is_code_route, tracker
        )

        # === 2.5) Await web search results if task was started (runs in parallel, minimal added latency) ===
        if web_search_task is not None:
            try:
                # Wait for search with timeout (5s max to avoid blocking too long)
                search_result = await asyncio.wait_for(web_search_task, timeout=5.0)

                if search_result.success and search_result.result:
                    results = search_result.result.get("results", [])
                    answer = search_result.result.get("answer")

                    if results or answer:
                        context_parts = ["[Web Search Results]"]

                        if answer:
                            context_parts.append(f"\nSummary: {answer}")

                        if results:
                            context_parts.append("\nSources:")
                            for i, item in enumerate(results[:5], 1):
                                title = item.get("title", "Untitled")
                                content_snippet = item.get("content", "")
                                url = item.get("url", "")

                                if len(content_snippet) > 300:
                                    content_snippet = content_snippet[:297] + "..."

                                context_parts.append(f"\n{i}. {title}")
                                if content_snippet:
                                    context_parts.append(f"   {content_snippet}")
                                if url:
                                    context_parts.append(f"   {url}")

                        context_parts.append("\n[End of Search Results]\n")
                        search_context = "\n".join(context_parts)

                        # Prepend search context as a system message
                        search_system_message = {
                            "role": "system",
                            "content": (
                                f"The following web search results were retrieved to help answer "
                                f"the user's query. Use this information to provide accurate, "
                                f"up-to-date responses. Cite sources when appropriate.\n\n{search_context}"
                            ),
                        }

                        # Insert after any existing system messages
                        insert_index = 0
                        for i, msg in enumerate(messages):
                            if msg.get("role") == "system":
                                insert_index = i + 1
                            else:
                                break

                        messages.insert(insert_index, search_system_message)

                        logger.info(
                            "Auto web search augmented messages with %d results (context_length=%d)",
                            len(results),
                            len(search_context),
                        )
                else:
                    logger.warning(
                        "Auto web search returned no results: %s",
                        search_result.error or "empty results",
                    )

            except TimeoutError:
                logger.warning("Auto web search timed out after 5s, continuing without results")
                web_search_task.cancel()
            except Exception as e:
                logger.warning(
                    "Auto web search failed, continuing without augmentation: %s", str(e)
                )

        # Gatewayz One Phase 4 — capture durable self-stated facts to user_memory
        # (flag-gated, off by default). Scheduled as a post-response background task
        # (covers both streaming and non-streaming paths); never affects the request.
        if Config.MEMORY_CAPTURE_ENABLED and not is_anonymous and user:
            from src.services.memory_extraction import capture_user_memory

            background_tasks.add_task(capture_user_memory, user.get("id"), list(messages))

        # Gatewayz One Phase 4 — context assembly (flag-gated, off by default).
        # Reassemble the final messages within the model's token budget (system +
        # per-user memory + rolling summary + most-recent turns, oldest dropped
        # first). Exact passthrough when disabled; never raises.
        if Config.CONTEXT_ASSEMBLY_ENABLED:
            from src.services.context_assembly_bridge import apply_context_budget

            messages = apply_context_budget(
                messages,
                model=model,
                budget_ratio=Config.CONTEXT_ASSEMBLY_BUDGET_RATIO,
                default_budget=Config.CONTEXT_ASSEMBLY_DEFAULT_BUDGET,
                user_id=(user or {}).get("id") if not is_anonymous else None,
            )

        # === 3) Call upstream (streaming or non-streaming) ===
        if req.stream:
            return await dispatch_streaming(
                is_anonymous=is_anonymous,
                provider_chain=provider_chain,
                messages=messages,
                original_model=original_model,
                optional=optional,
                api_key=api_key,
                api_key_id=api_key_id,
                background_tasks=background_tasks,
                request=request,
                request_id=request_id,
                rl_pre=rl_pre,
                tracker=tracker,
                user=user,
                trial=trial,
                environment_tag=environment_tag,
                session_id=session_id,
                rate_limit_mgr=rate_limit_mgr,
                client_ip=client_ip,
            )

        # Non-streaming response
        start = time.monotonic()
        processed, provider, model = await dispatch_non_streaming(
            is_anonymous=is_anonymous,
            provider_chain=provider_chain,
            messages=messages,
            original_model=original_model,
            optional=optional,
            model=model,
            provider=provider,
            api_key=api_key,
            background_tasks=background_tasks,
            request=request,
            user=user,
            trial=trial,
        )
        elapsed = max(0.001, time.monotonic() - start)

        # === 4) Usage, pricing, final checks ===
        usage = processed.get("usage", {}) or {}
        total_tokens = usage.get("total_tokens", 0)
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # Plan limits and usage tracking (only for authenticated users)
        if not is_anonymous:
            post_plan = await _to_thread(
                enforce_plan_limits, user["id"], total_tokens, environment_tag
            )
            if not post_plan.get("allowed", False):
                raise HTTPException(
                    status_code=429,
                    detail=f"Plan limit exceeded: {post_plan.get('reason', 'unknown')}",
                )

            if trial.get("is_trial") and not trial.get("is_expired"):
                try:
                    # NOTE: `request_model` was a dispatch-loop local (set only on the
                    # anonymous branch) and is unbound on this authenticated path — it was
                    # already unbound here before the Step-3 dispatch extraction, so this
                    # call has always raised and been caught (trial usage is not tracked
                    # here). Behavior preserved verbatim; fixing it is out of scope for this
                    # refactor (would change trial accounting). Tracked separately.
                    await _to_thread(
                        track_trial_usage,
                        api_key,
                        total_tokens,
                        1,
                        model_id=request_model,  # noqa: F821  (pre-existing unbound; see note above)
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                    )
                except Exception as e:
                    logger.warning("Failed to track trial usage: %s", e)

            if should_release_concurrency and rate_limit_mgr and not disable_rate_limiting:
                try:
                    await rate_limit_mgr.release_concurrency(api_key)
                except Exception as exc:
                    logger.debug(
                        "Failed to release concurrency before final check for %s: %s",
                        mask_key(api_key),
                        exc,
                    )
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
                    rl_final_hdrs = get_rate_limit_headers(rl_final)
                    if rl_final.retry_after:
                        rl_final_hdrs["Retry-After"] = str(rl_final.retry_after)
                    raise HTTPException(
                        status_code=429,
                        detail=f"Rate limit exceeded: {rl_final.reason}",
                        headers=rl_final_hdrs or None,
                    )

        # Credit/usage tracking (only for authenticated users)
        if not is_anonymous:
            cost = await _handle_credits_and_usage(
                api_key=api_key,
                user=user,
                model=model,
                trial=trial,
                total_tokens=total_tokens,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                elapsed_ms=int(elapsed * 1000),
                is_streaming=False,
                request_id=request_id,
            )
            await _to_thread(increment_api_key_usage, api_key)
        else:
            cost = await calculate_cost_async(model, prompt_tokens, completion_tokens)

        # Record Prometheus metrics and passive health monitoring (allowed for anonymous)
        await _record_inference_metrics_and_health(
            provider=provider,
            model=model,
            elapsed_seconds=elapsed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=cost,
            success=True,
            error_message=None,
        )

        # === 4.5) Log activity for tracking and analytics (only for authenticated users) ===
        if not is_anonymous:
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
                    finish_reason=(processed.get("choices") or [{}])[0].get(
                        "finish_reason", "stop"
                    ),
                    app="API",
                    metadata={
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "endpoint": "/v1/chat/completions",
                        "session_id": session_id,
                        "gateway": provider,  # Track which gateway was used
                    },
                )
            except Exception as e:
                logger.error(
                    f"Failed to log activity for user {user['id']}, model {model}: {e}",
                    exc_info=True,
                )

        # === 5) History (use the last user message in this request only) ===
        await persist_conversation_turn(
            session_id, is_anonymous, user, messages, model, processed, total_tokens
        )

        # === 6) Attach gateway usage (non-sensitive) ===
        processed.setdefault("gateway_usage", {})
        processed["gateway_usage"].update(
            {
                "tokens_charged": total_tokens,
                "request_ms": int(elapsed * 1000),
            }
        )
        if not trial.get("is_trial", False):
            # If you can cheaply re-fetch balance, do it here; otherwise omit
            processed["gateway_usage"]["cost_usd"] = round(cost, 6)

        # === 6.1) Attach code router metadata if code routing was used ===
        if code_router_decision:
            try:
                from src.services.code_router import get_routing_metadata

                routing_metadata = get_routing_metadata(code_router_decision)
                processed["routing_metadata"] = routing_metadata
            except Exception as e:
                logger.debug(f"Failed to attach code routing metadata: {e}")

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

        # Save chat completion request metadata to database with cost tracking - run as background task
        # Calculate cost breakdown for analytics
        from src.services.pricing import get_model_pricing

        try:
            pricing_info = get_model_pricing(model)
            input_cost = prompt_tokens * pricing_info.get("prompt", 0)
            output_cost = completion_tokens * pricing_info.get("completion", 0)
        except ValueError:
            logger.warning(
                f"[ANALYTICS] Pricing unavailable for high-value model {model}, "
                f"using zero cost for analytics record"
            )
            input_cost = 0.0
            output_cost = 0.0

        background_tasks.add_task(
            save_chat_completion_request_with_cost,
            request_id=request_id,
            model_name=model,
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            processing_time_ms=int(elapsed * 1000),
            cost_usd=cost,
            input_cost_usd=input_cost,
            output_cost_usd=output_cost,
            pricing_source="calculated",
            status="completed",
            error_message=None,
            user_id=user["id"] if not is_anonymous else None,
            provider_name=provider,
            model_id=None,
            api_key_id=api_key_id,
            is_anonymous=is_anonymous,
        )

        # Prepare headers including rate limit information
        headers = {}
        if rl_final is not None:
            headers.update(get_rate_limit_headers(rl_final))

        return JSONResponse(content=processed, headers=headers)

    except HTTPException as http_exc:
        # Save failed request for HTTPException errors (rate limits, auth errors, etc.)
        await save_failed_request(
            _to_thread=_to_thread,
            save_chat_completion_request_with_cost=save_chat_completion_request_with_cost,
            request_id=request_id,
            model=model if "model" in dir() else None,
            original_model=original_model if "original_model" in dir() else None,
            prompt_tokens=prompt_tokens if "prompt_tokens" in dir() else 0,
            start_time=start if "start" in dir() else 0,
            error=http_exc,
            error_message=f"HTTP {http_exc.status_code}: {http_exc.detail}",
            user=user if "user" in dir() else None,
            provider=provider if "provider" in dir() else None,
            api_key_id=api_key_id if "api_key_id" in dir() else None,
            is_anonymous=is_anonymous if "is_anonymous" in dir() else False,
        )
        raise
    except Exception as e:
        logger.exception(
            f"[{request_id}] Unhandled server error: {type(e).__name__}",
            extra={"request_id": request_id, "error_type": type(e).__name__},
        )

        # Save failed request for unexpected errors
        await save_failed_request(
            _to_thread=_to_thread,
            save_chat_completion_request_with_cost=save_chat_completion_request_with_cost,
            request_id=request_id,
            model=model if "model" in dir() else None,
            original_model=original_model if "original_model" in dir() else None,
            prompt_tokens=prompt_tokens if "prompt_tokens" in dir() else 0,
            start_time=start if "start" in dir() else 0,
            error=e,
            error_message=f"{type(e).__name__}: {str(e)[:500]}",
            user=user if "user" in dir() else None,
            provider=provider if "provider" in dir() else None,
            api_key_id=api_key_id if "api_key_id" in dir() else None,
            is_anonymous=is_anonymous if "is_anonymous" in dir() else False,
        )

        # Don't leak internal details, but include request ID for support
        raise HTTPException(
            status_code=500, detail=f"Internal server error (request ID: {request_id})"
        )


# Log successful module load - this should appear in startup logs if chat.py loads correctly
logger.info("✅ Chat module fully loaded - all routes registered successfully")
logger.info(f"   Total routes in router: {len(router.routes)}")

# Log any provider import errors that occurred during safe imports
if _provider_import_errors:
    logger.warning(f"⚠  Provider import warnings ({len(_provider_import_errors)} failed):")
    for provider_name, error_msg in _provider_import_errors.items():
        logger.warning(f"     - {provider_name}: {error_msg}")
else:
    logger.info("✓ All provider clients loaded successfully")
