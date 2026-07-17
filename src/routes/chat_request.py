"""Upstream request preparation for the chat route (provider selection).

Extracted verbatim from chat_completions (Phase 0d): builds the optional-params
dict, auto-detects/normalizes the provider, and assembles the health-aware
provider failover chain. Pure orchestration over the providers/health services;
returns ``(model, provider, provider_chain, optional)``. May raise HTTPException
for non-chat (FAL) models. Behavior unchanged.
"""

from __future__ import annotations

import asyncio
import logging

from src.routes.chat_helpers import validate_and_adjust_max_tokens
from src.services.health_routing import (
    get_healthy_alternative_provider,
    is_model_healthy,
    should_use_health_based_routing,
)
from src.services.model_transformations import detect_provider_from_model_id, transform_model_id
from src.services.provider_failover import (
    build_provider_failover_chain,
    enforce_model_failover_rules,
    filter_by_circuit_breaker,
)
from src.utils.security_validators import sanitize_for_logging

logger = logging.getLogger(__name__)


async def prepare_upstream_request(
    req,
    original_model: str,
    is_code_route: bool,
    tracker,
) -> tuple[str, str, list, dict]:
    with tracker.stage("request_preparation"):
        optional = {}
        for name in (
            "max_tokens",
            "temperature",
            "top_p",
            "frequency_penalty",
            "presence_penalty",
            "tools",
        ):
            val = getattr(req, name, None)
            if val is not None:
                optional[name] = val

        # Validate and adjust max_tokens for models with minimum requirements
        validate_and_adjust_max_tokens(optional, original_model)

        # Auto-detect provider if not specified
        req_provider_missing = req.provider is None or (
            isinstance(req.provider, str) and not req.provider
        )
        provider = (req.provider or "openrouter").lower()

        # Normalize provider aliases
        if provider == "hug":
            provider = "huggingface"

        provider_locked = not req_provider_missing

        # Use routed model for provider detection when code routing is active
        model_for_provider_detection = req.model if is_code_route and req.model else original_model
        override_provider = detect_provider_from_model_id(model_for_provider_detection)
        if override_provider:
            override_provider = override_provider.lower()
            if override_provider == "hug":
                override_provider = "huggingface"
            if provider_locked and override_provider != provider:
                logger.info(
                    "Skipping provider override for model %s: request locked provider to '%s'",
                    sanitize_for_logging(original_model),
                    sanitize_for_logging(provider),
                )
            else:
                if override_provider != provider:
                    logger.info(
                        f"Provider override applied for model {original_model}: '{provider}' -> '{override_provider}'"
                    )
                    provider = override_provider
                # Mark provider as determined even if it matches the default
                # This prevents the fallback logic from incorrectly routing to wrong providers
                req_provider_missing = False

        if req_provider_missing:
            # Try to detect provider from model ID using the transformation module
            # Use routed model when code routing is active
            detected_provider = detect_provider_from_model_id(model_for_provider_detection)
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
                from src.services.models import get_cached_models

                # OPTIMIZATION: Fetch full catalog once instead of making N calls for disjoint providers.
                # This prevents 499 errors caused by sequential DB fetches when cache is cold.
                # Run in thread to avoid blocking event loop during DB fetch
                all_models_catalog = await asyncio.to_thread(get_cached_models, "all") or []
                all_model_ids = {m.get("id") for m in all_models_catalog}

                # Try each provider with transformation against the in-memory set
                for test_provider in [
                    "huggingface",
                    "featherless",
                    "fireworks",
                    "together",
                    "google-vertex",
                ]:
                    transformed = transform_model_id(original_model, test_provider)
                    if transformed in all_model_ids:
                        provider = test_provider
                        logger.info(
                            f"Auto-detected provider '{provider}' for model {original_model} (transformed to {transformed})"
                        )
                        break
                # Otherwise default to openrouter (already set)

        # Use the routed model (from code router or other routing logic) instead of original
        # This ensures that routing decisions are actually applied downstream
        effective_model = req.model if req.model else original_model

        provider_chain = build_provider_failover_chain(provider)
        provider_chain = enforce_model_failover_rules(effective_model, provider_chain)
        provider_chain = filter_by_circuit_breaker(effective_model, provider_chain)

        # Gatewayz One Phase 2 — smart-router reordering (flag-gated, off by default).
        # Reorders the chain by the policy-based router using the Phase 1 offers
        # projection; never drops a provider, no-ops when there are no offers.
        # Runs BEFORE health-based routing on purpose: health routing below gets the
        # final say and will bump an unhealthy cost-winner off the front of the chain.
        from src.config import Config

        if Config.SMART_ROUTER_ENABLED and provider_chain:
            from src.services.smart_router_bridge import reorder_provider_chain

            provider_chain = reorder_provider_chain(
                effective_model, provider_chain, policy=Config.SMART_ROUTER_POLICY
            )

        # HEALTH FIX #1094: Check model health and reorder provider chain to prioritize healthy providers
        # This proactively routes requests away from unhealthy models BEFORE they fail
        if should_use_health_based_routing() and provider_chain:
            primary_provider = provider_chain[0]
            is_healthy, health_error = is_model_healthy(effective_model, primary_provider)

            if not is_healthy:
                logger.warning(
                    f"Primary provider '{primary_provider}' for model '{effective_model}' is unhealthy: {health_error}. "
                    f"Checking for healthy alternatives..."
                )

                # Try to find a healthy alternative provider
                alt_provider = get_healthy_alternative_provider(effective_model, primary_provider)

                if alt_provider and alt_provider in provider_chain:
                    # Move healthy provider to the front of the chain
                    provider_chain = [alt_provider] + [
                        p for p in provider_chain if p != alt_provider
                    ]
                    logger.info(
                        f"✓ Health-based routing: Moved '{alt_provider}' to front of chain for model '{effective_model}' "
                        f"(primary '{primary_provider}' is unhealthy)"
                    )
                elif alt_provider:
                    # Alternative provider not in chain - add it at the front
                    provider_chain = [alt_provider] + provider_chain
                    logger.info(
                        f"✓ Health-based routing: Added healthy provider '{alt_provider}' to chain for model '{effective_model}'"
                    )
                else:
                    # No healthy alternative found - log warning but proceed with unhealthy provider
                    logger.warning(
                        f"⚠ No healthy alternative found for model '{effective_model}' on '{primary_provider}'. "
                        f"Proceeding with unhealthy provider (may fail, but circuit breaker will handle it)"
                    )
            else:
                logger.debug(
                    f"✓ Health check passed for model '{effective_model}' on '{primary_provider}'"
                )

        model = effective_model

    # Diagnostic logging for tools parameter
    if "tools" in optional:
        logger.info(
            "Tools parameter detected: tools_count=%d, provider=%s, model=%s",
            len(optional["tools"]) if isinstance(optional["tools"], list) else 0,
            sanitize_for_logging(provider),
            sanitize_for_logging(original_model),
        )
        logger.debug("Tools content: %s", sanitize_for_logging(str(optional["tools"])[:500]))

    return model, provider, provider_chain, optional
