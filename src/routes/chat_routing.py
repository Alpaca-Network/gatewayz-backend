"""Model routing gate for the chat route.

The prompt/code/general auto-routing engine (D2 cluster) was removed in the MVP
refactor (Task 13). The public contract for the ``auto`` / ``router:*`` model
aliases is now an explicit 400 rather than silent classification-based routing:
callers must specify an explicit model id.

``resolve_model_routing`` keeps its call shape (returns
``(code_router_decision, is_code_route)``) so downstream provider detection and
response-metadata code paths are unchanged for ordinary model ids.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Bare aliases that used to fold onto the `router` prefix. `openrouter/auto` is
# deliberately EXCLUDED — it is a real passthrough model served by OpenRouter.
_ROUTER_BARE_ALIASES = {"auto", "gatewayz/auto"}
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
