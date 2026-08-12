"""Model routing gate for the chat route.

The original prompt/code/general auto-routing engine (D2 cluster) was removed
in the MVP refactor (Task 13). ``resolve_auto_routed_model`` is its replacement
(gatewayz-backend#2215), built on the Phase 1-3 catalog/classifier/scoring
engine (``get_candidate_models`` / ``classify_task`` / ``select_model``)
instead of the deleted keyword-classifier cluster.

``resolve_model_routing`` keeps its call shape (returns
``(code_router_decision, is_code_route)``) so downstream provider detection and
response-metadata code paths are unchanged for ordinary model ids. It still
rejects any alias that reaches it unresolved -- in normal operation that never
happens (``resolve_auto_routed_model`` runs earlier and either resolves the
alias in place or raises), but it stays as a defense-in-depth backstop rather
than being removed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import HTTPException

from src.config import Config

logger = logging.getLogger(__name__)

# Bare aliases that used to fold onto the `router` prefix. `openrouter/auto` is
# deliberately EXCLUDED — it is a real passthrough model served by OpenRouter.
# `gatewayz-router` is the branded name for this feature (gatewayz-backend#2216
# follow-up) -- same resolution path as `auto`, just a friendlier model id.
_ROUTER_BARE_ALIASES = {"auto", "gatewayz/auto", "gatewayz-router"}
_ROUTER_PREFIXES = ("router", "auto:", "gatewayz-general", "gatewayz-code")


def _is_router_model(model: str | None) -> bool:
    """True for the removed auto/prompt/code/general router model aliases."""
    if not model:
        return False
    m = model.lower().strip()
    if m in _ROUTER_BARE_ALIASES:
        return True
    return m.startswith(_ROUTER_PREFIXES)


async def resolve_model_routing(req, original_model: str) -> tuple[None, bool]:
    """Reject removed auto-router aliases; pass explicit models through.

    Returns ``(None, False)`` (no code-router decision, not a code route) for
    every explicit model id. Raises 400 for ``auto`` / ``router:*`` /
    ``gatewayz-general*`` / ``gatewayz-code*`` aliases, which no longer resolve
    to a backing engine.
    """
    if _is_router_model(original_model):
        raise HTTPException(
            status_code=400,
            detail=(
                "Automatic prompt routing ('auto'/'router:*') has been removed. "
                "Specify an explicit model id (e.g. 'openai/gpt-4o-mini')."
            ),
        )
    return None, False


def _raise_auto_routing_failed() -> None:
    raise HTTPException(
        status_code=400,
        detail=(
            "Unable to automatically select a model for this request. "
            "Specify an explicit model id (e.g. 'openai/gpt-4o-mini')."
        ),
    )


async def resolve_auto_routed_model(
    req: Any,
    is_anonymous: bool,
    api_key: str | None = None,
    conversation_id: str | None = None,
) -> None:
    """Resolve an `auto`/`router:*` alias to a real model, in place on `req`.

    No-op (falls through to today's exact 400-on-alias behavior downstream)
    unless ALL of the following hold:
      - the request is authenticated (auto-routing costs a real LLM call;
        anonymous traffic never had alias access to begin with -- it's still
        rejected by `enforce_model_pricing_gate` exactly as before this phase)
      - `req.model` is actually a router alias
      - `Config.AUTO_ROUTING_ENABLED` is true (gatewayz-backend#2216's global
        kill-switch -- disabled by default, so this whole function is inert
        until someone deliberately turns it on)
      - the caller didn't pass `auto_routing=False` on the request
      - the key's `routing_policies` row (if any) doesn't explicitly opt out

    Each of these can only NARROW access relative to the one before it --
    none of them can force auto-routing on when a higher-priority setting
    says no.

    MUST be called before any gate that treats `req.model` as a real, priced
    model (`enforce_model_pricing_gate` in particular) -- that gate 400s on
    `auto` today because it isn't a catalog/pricing model, which is what makes
    this the actual insertion point rather than the later `resolve_model_routing`
    call site.

    `classify_task` and `select_model` are synchronous, blocking functions
    (an LLM HTTP call and Supabase reads respectively) -- both are run via
    `asyncio.to_thread` so they never stall this (async) request's event loop.
    The per-key policy lookup is the same shape of call and is also offloaded.

    Known gap: `get_candidate_models` is never given a `min_context_length`
    here, even for long messages -- `TaskClassification` doesn't carry an
    estimated-token-count signal (it's computed internally by
    `extract_required_capabilities` but discarded before it reaches this
    caller), so a long request can still be routed to a small-context model.
    Not a new failure mode (the resulting provider error is the same one a
    human picking the wrong model manually would hit) but worth closing in a
    follow-up by exposing that estimate on `TaskClassification`.

    Raises the same 400 as `resolve_model_routing` on any failure to resolve
    (classifier error, or no candidate could be selected) -- fail-SAFE, not
    fail-open, since this sits in front of billing-adjacent gates.
    """
    if (
        is_anonymous
        or not _is_router_model(req.model)
        or not Config.AUTO_ROUTING_ENABLED
        or getattr(req, "auto_routing", None) is False
    ):
        return

    from src.db.routing_policies import is_auto_routing_disabled_for_key

    if await asyncio.to_thread(is_auto_routing_disabled_for_key, api_key):
        # Same as the other three narrowing checks above: fall through
        # silently to enforce_model_pricing_gate's standard 400 downstream,
        # rather than raising a distinct message here. All four opt-out
        # paths must behave identically regardless of which setting fired.
        logger.info("auto-routing: disabled by routing_policies for this key")
        return

    from src.db.models_catalog_db import get_candidate_models
    from src.services.model_selector import select_model
    from src.services.task_classifier import classify_task

    original_alias = req.model
    messages = [m.model_dump() for m in (req.messages or [])]

    classification = await asyncio.to_thread(
        classify_task,
        messages,
        tools=req.tools,
        response_format=req.response_format,
    )
    if classification.error is not None:
        logger.warning(
            f"auto-routing: classification failed for alias {original_alias!r} "
            f"({classification.error}); rejecting"
        )
        _raise_auto_routing_failed()

    candidates = await asyncio.to_thread(
        get_candidate_models,
        required_capabilities=classification.capability_names or None,
    )
    selection = await asyncio.to_thread(
        select_model,
        candidates,
        classification,
        conversation_id=conversation_id,
    )
    if not selection.model_id:
        logger.warning(
            f"auto-routing: no candidate selected for alias {original_alias!r} "
            f"(reason={selection.reason}, considered={selection.considered}); rejecting"
        )
        _raise_auto_routing_failed()

    logger.info(
        f"auto-routing: resolved alias {original_alias!r} -> {selection.model_id!r} "
        f"(task_type={classification.task_type}, confidence={classification.confidence}, "
        f"reason={selection.reason})"
    )
    req.model = selection.model_id
