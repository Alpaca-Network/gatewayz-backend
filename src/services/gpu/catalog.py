"""``community/*`` catalog projection (gatewayz-backend#2262, spec §4 item 3).

Community model ids are not fetched from an upstream provider API the way
every other catalog entry is (``model_catalog_sync.py``'s fetch-function
registry) -- they come from the ``models`` jsonb column of whichever GPU
nodes are currently ``active`` (W-A1's ``gpu_nodes`` table), unioned across
nodes so a model offered by any node appears once.

``community_catalog_models`` is intentionally a pure function over a list of
node dicts rather than a DB-reading one: it's the transform, independently
testable from wherever the node list comes from. ``sync_community_catalog``
is the (currently best-effort) glue to W-A1's DB layer -- W-A1's documented
surface (``select_nodes_for_model``, ``adjust_outstanding``, ``get_node``,
``get_provider``) does not yet include a "list every active node" call, so
this lazily looks for ``src.db.gpu.list_active_nodes`` and degrades to an
empty catalog (logged, not raised) until that lands or the name is aligned.
Wiring this into the cached ``/v1/models`` response path (``src/routes/catalog.py``)
or into a scheduled sync job is deferred to that follow-up for the same
reason -- see the W-A2 report.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

SOURCE_GATEWAY = "community"
PROVIDER_SLUG = "community"


def community_catalog_models(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union the declared ``models`` of every *active* node into catalog rows.

    Each ``nodes[i]["models"]`` entry is ``{"id": ..., "max_context": ...,
    "dtype": ...}`` (spec §2 ``gpu_nodes.models``). Only nodes with
    ``status == "active"`` contribute. The first node to declare a given
    model id wins its ``max_context`` (nodes may disagree; picking the first
    is deterministic and good enough for display -- routing itself always
    re-selects a live node per request via ``select_nodes_for_model``).
    """
    seen: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if node.get("status") != "active":
            continue
        for model in node.get("models") or []:
            model_id = model.get("id")
            if not model_id or model_id in seen:
                continue
            seen[model_id] = {
                "id": f"community/{model_id}",
                "name": model_id,
                "source_gateway": SOURCE_GATEWAY,
                "provider_slug": PROVIDER_SLUG,
                "context_length": model.get("max_context"),
            }
    return list(seen.values())


def sync_community_catalog() -> list[dict[str, Any]]:
    """Best-effort glue from W-A1's node list to ``community_catalog_models``.

    Returns ``[]`` (never raises) if ``src.db.gpu`` isn't available yet, or
    doesn't (yet) export a way to list every active node.
    """
    try:
        from src.db.gpu import list_active_nodes
    except ImportError:
        logger.info("src.db.gpu.list_active_nodes not available; community catalog is empty")
        return []
    try:
        nodes = list_active_nodes() or []
    except Exception as e:
        logger.warning("list_active_nodes() failed: %s", e)
        return []
    return community_catalog_models(nodes)
