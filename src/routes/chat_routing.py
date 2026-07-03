"""Model routing for the chat route (auto / general / code routers).

Extracted verbatim from chat_completions Steps 2.3-2.5 (Phase 0d). Fail-open: any
router error falls back to a default model. Mutates ``req.model`` in place and
returns ``(code_router_decision, is_code_route)`` for downstream provider
detection and response metadata.
"""

from __future__ import annotations

import logging
from typing import Any

from src.routes.chat_helpers import (
    _get_auto_route_default_model,
    _get_code_router_default_model,
    _to_thread,
)

logger = logging.getLogger(__name__)

# Code router constants (mirrors chat.py)
CODE_ROUTER_PREFIX = "router:code"


async def resolve_model_routing(
    req,
    original_model: str,
    messages: list[dict],
    session_id: int | None,
    user: dict | None,
    is_auto_route: bool,
    tracker,
) -> tuple[Any, bool]:
    # === 2.3) Prompt-Level Routing (if model="auto") ===
    # This is a fail-open router - if it fails or times out, it returns a default cheap model
    router_decision = None
    if is_auto_route:
        with tracker.stage("prompt_routing"):
            try:
                from src.schemas.router import UserRouterPreferences
                from src.services.prompt_router import (
                    is_auto_route_request,
                    parse_auto_route_options,
                    route_request,
                )

                if is_auto_route_request(original_model):
                    tier, optimization = parse_auto_route_options(original_model)

                    # Build user preferences (could be loaded from DB in future)
                    user_preferences = UserRouterPreferences(
                        default_optimization=optimization,
                        enabled=True,
                    )

                    # Get conversation ID for sticky routing (use session_id if available)
                    conversation_id = str(session_id) if session_id else None

                    # Route the request (fail-open, < 2ms target)
                    router_decision = await _to_thread(
                        route_request,
                        messages=messages,
                        tools=getattr(req, "tools", None),
                        response_format=getattr(req, "response_format", None),
                        user_preferences=user_preferences,
                        conversation_id=conversation_id,
                        tier=tier,
                    )

                    # Update model with routed selection
                    req.model = router_decision.selected_model

                    logger.info(
                        "Prompt router selected model: %s (category=%s, confidence=%.2f, time=%.2fms, reason=%s)",
                        router_decision.selected_model,
                        (
                            router_decision.classification.category.value
                            if router_decision.classification
                            else "unknown"
                        ),
                        (
                            router_decision.classification.confidence
                            if router_decision.classification
                            else 0
                        ),
                        router_decision.decision_time_ms,
                        router_decision.reason,
                    )

            except Exception as e:
                # Fail open - log warning and use default model
                logger.warning(
                    "Prompt router failed, falling back to default: %s",
                    str(e),
                )
                # Use default model since original was an auto-route request
                req.model = _get_auto_route_default_model()

    # === 2.4) General Router (if model="router:general" or "gatewayz-general") ===
    # Intelligent routing for general-purpose prompts
    # Normalize model string to handle hyphenated aliases
    from src.services.general_router import normalize_model_string

    normalized_model = normalize_model_string(original_model) if original_model else original_model

    # Check for general router
    GENERAL_ROUTER_PREFIX = "router:general"
    is_general_route = normalized_model and normalized_model.lower().startswith(
        GENERAL_ROUTER_PREFIX
    )

    if is_general_route:
        with tracker.stage("general_routing"):
            try:
                from src.services.general_router import (
                    parse_router_model_string as parse_general_router,
                )
                from src.services.general_router import route_general_prompt

                # Parse mode from model string
                is_general_router, router_mode = parse_general_router(normalized_model.lower())

                if is_general_router:
                    # Route using the general router
                    general_router_decision = await route_general_prompt(
                        messages=messages,
                        mode=router_mode,
                        context=None,
                        user_default_model=user.get("default_model") if user else None,
                    )

                    # Update model with routed selection
                    req.model = general_router_decision["model_id"]

                    logger.info(
                        "General router selected model: %s (mode=%s, confidence=%.2f, time=%.2fms, fallback=%s)",
                        general_router_decision["model_id"],
                        general_router_decision["mode"],
                        general_router_decision.get("confidence", 0),
                        general_router_decision["routing_latency_ms"],
                        general_router_decision.get("fallback_used", False),
                    )

            except Exception as e:
                # Fail open - use fallback
                logger.warning("General router failed: %s", str(e))
                try:
                    from src.services.prometheus_metrics import track_general_router_fallback

                    track_general_router_fallback(reason="exception", mode="balanced")
                except ImportError:
                    # Prometheus metrics are optional; skip tracking if not available
                    logger.debug(
                        "Prometheus metrics not available for general router fallback tracking"
                    )
                from src.db.system_config import get_config

                req.model = get_config("default_fallback_model", "anthropic/claude-sonnet-4")

    # === 2.5) Code-Optimized Routing (if model="router:code" or "router:code:<mode>") ===
    # Specialized router for code-related tasks with 2026 benchmark-optimized model selection
    code_router_decision = None
    # Use normalized_model for code router as well (supports gatewayz-code aliases)
    is_code_route = normalized_model and normalized_model.lower().startswith(CODE_ROUTER_PREFIX)

    if is_code_route:
        with tracker.stage("code_routing"):
            try:
                from src.services.code_router import parse_router_model_string, route_code_prompt

                # Parse the router mode from model string (use normalized model)
                is_code_router, router_mode = parse_router_model_string(normalized_model.lower())

                if is_code_router:
                    # Extract last user message for classification
                    last_user_message = ""
                    for msg in reversed(messages):
                        if msg.get("role") == "user":
                            content = msg.get("content", "")
                            if isinstance(content, str):
                                last_user_message = content
                            elif isinstance(content, list):
                                # Handle multi-part messages
                                for part in content:
                                    if isinstance(part, dict) and part.get("type") == "text":
                                        last_user_message = part.get("text", "")
                                        break
                            break

                    # Validate: skip routing if no valid user message found
                    if not last_user_message or not last_user_message.strip():
                        logger.warning(
                            "Code router: no valid user message found, using default model"
                        )
                        try:
                            from src.services.prometheus_metrics import track_code_router_fallback

                            track_code_router_fallback(reason="empty_message")
                        except ImportError:
                            # Prometheus metrics are optional - silently skip if not available
                            pass
                        req.model = _get_code_router_default_model()
                    else:
                        # Extract context from messages
                        from src.services.code_classifier import get_classifier

                        classifier = get_classifier()
                        context = classifier.extract_context_from_messages(messages)

                        # Route the code prompt
                        code_router_decision = route_code_prompt(
                            prompt=last_user_message,
                            mode=router_mode,
                            context=context,
                            user_default_model=user.get("default_model") if user else None,
                        )

                        # Update model with routed selection
                        req.model = code_router_decision["model_id"]

                        logger.info(
                            "Code router selected model: %s (tier=%d, category=%s, confidence=%.2f, time=%.2fms, mode=%s)",
                            code_router_decision["model_id"],
                            code_router_decision["tier"],
                            code_router_decision["task_category"],
                            code_router_decision["confidence"],
                            code_router_decision["routing_latency_ms"],
                            code_router_decision["mode"],
                        )

            except Exception as e:
                # Fail open - log warning and use default model
                logger.warning(
                    "Code router failed, falling back to default: %s",
                    str(e),
                )
                try:
                    from src.services.prometheus_metrics import track_code_router_fallback

                    track_code_router_fallback(reason="exception")
                except ImportError:
                    # Prometheus metrics are optional - silently skip if not available
                    pass
                # Use default code model since original was a code-route request
                req.model = _get_code_router_default_model()

    return code_router_decision, is_code_route
