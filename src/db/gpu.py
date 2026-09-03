"""DB access for gpu_providers and gpu_nodes (Milestone 4 W-A1,
gatewayz-backend#2262).

Mirrors src/db/user_wallets.py's try/except + logger.warning +
safe-default convention exactly -- callers must treat a lookup failure as
"no data," never as a hard failure, matching every other DB module in
this session. Other M4 tables (provider_work, provider_earnings,
provider_settlements, gpu_utilization_hourly) get their own DB modules in
their owning workstreams (W-A2/B/C) -- this module owns gpu_providers and
gpu_nodes only.

See spec: .../scratchpad/m4/spec.md sections 2-3, and the W-A1 brief.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)

_PROVIDERS_TABLE = "gpu_providers"
_NODES_TABLE = "gpu_nodes"

# Node statuses that still count as "in the fleet" for the liveness sweep
# -- a node that never heartbeated ('registered') or was explicitly
# disabled is left alone.
_LIVE_NODE_STATUSES = ("active", "degraded")


# ---------------------------------------------------------------------------
# gpu_providers
# ---------------------------------------------------------------------------


def create_provider(
    user_id: int,
    display_name: str,
    payout_wallet_address: str,
    contact_email: str | None = None,
    region_default: str | None = None,
) -> dict[str, Any] | None:
    """Create a provider registration (status='pending'). Returns the
    created row, or None on any failure -- including the UNIQUE(user_id)
    conflict (a user may only register once). Callers that need to
    distinguish "already registered" (409) from a transient error should
    call get_provider_by_user(user_id) first, same pattern as
    user_wallets.link_wallet."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_PROVIDERS_TABLE)
            .insert(
                {
                    "user_id": user_id,
                    "display_name": display_name,
                    "payout_wallet_address": payout_wallet_address.lower(),
                    "contact_email": contact_email,
                    "region_default": region_default,
                    "status": "pending",
                }
            )
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"gpu_providers create failed for user {user_id}: {e}")
        return None


def get_provider_by_user(user_id: int) -> dict[str, Any] | None:
    """The gpu_providers row for a user, or None if unregistered (or on error)."""
    try:
        client = get_supabase_client()
        result = client.table(_PROVIDERS_TABLE).select("*").eq("user_id", user_id).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"gpu_providers lookup failed for user {user_id}: {e}")
        return None


def get_provider(provider_id: int) -> dict[str, Any] | None:
    """A single gpu_providers row by id, or None if missing (or on error)."""
    try:
        client = get_supabase_client()
        result = client.table(_PROVIDERS_TABLE).select("*").eq("id", provider_id).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"gpu_providers lookup failed for provider {provider_id}: {e}")
        return None


def set_provider_status(
    provider_id: int, status: str, approved_by: int | None = None
) -> dict[str, Any] | None:
    """Update a provider's status (admin approve/suspend). Returns the
    updated row, or None on any failure. Sets approved_at/approved_by when
    transitioning to 'approved'; leaves them untouched otherwise."""
    try:
        client = get_supabase_client()
        updates: dict[str, Any] = {"status": status}
        if status == "approved":
            updates["approved_at"] = datetime.now().astimezone().isoformat()
            updates["approved_by"] = approved_by
        result = client.table(_PROVIDERS_TABLE).update(updates).eq("id", provider_id).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"gpu_providers status update failed for provider {provider_id}: {e}")
        return None


def list_providers(status: str | None = None) -> list[dict[str, Any]]:
    """All providers, optionally filtered by status. Empty list on error."""
    try:
        client = get_supabase_client()
        query = client.table(_PROVIDERS_TABLE).select("*").order("created_at", desc=True)
        if status is not None:
            query = query.eq("status", status)
        result = query.execute()
        return result.data or []
    except Exception as e:
        logger.warning(f"gpu_providers list failed (status={status}): {e}")
        return []


# ---------------------------------------------------------------------------
# gpu_nodes
# ---------------------------------------------------------------------------


def create_node(
    provider_id: int,
    name: str,
    region: str,
    gpu_model: str,
    vram_gb: int,
    endpoint_url: str,
    node_token_hash: str,
    models: list[dict[str, Any]],
    bandwidth_mbps: int | None = None,
    endpoint_api_key_encrypted: str | None = None,
) -> dict[str, Any] | None:
    """Create a node under a provider. Returns the created row, or None on
    any failure -- including the UNIQUE(node_token_hash) conflict (should
    never happen with a properly random token, but treated the same as
    every other DB module's safe-default convention)."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_NODES_TABLE)
            .insert(
                {
                    "provider_id": provider_id,
                    "name": name,
                    "region": region,
                    "gpu_model": gpu_model,
                    "vram_gb": vram_gb,
                    "bandwidth_mbps": bandwidth_mbps,
                    "endpoint_url": endpoint_url,
                    "endpoint_api_key_encrypted": endpoint_api_key_encrypted,
                    "models": models,
                    "node_token_hash": node_token_hash,
                    "status": "registered",
                }
            )
            .execute()
        )
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"gpu_nodes create failed for provider {provider_id}: {e}")
        return None


def get_node(node_id: int) -> dict[str, Any] | None:
    """A single gpu_nodes row by id, or None if missing (or on error)."""
    try:
        client = get_supabase_client()
        result = client.table(_NODES_TABLE).select("*").eq("id", node_id).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"gpu_nodes lookup failed for node {node_id}: {e}")
        return None


def get_node_by_token_hash(token_hash: str) -> dict[str, Any] | None:
    """The gpu_nodes row whose bearer token hashes to `token_hash`, or None
    if no node matches (or on error) -- the node-auth lookup path."""
    try:
        client = get_supabase_client()
        result = client.table(_NODES_TABLE).select("*").eq("node_token_hash", token_hash).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"gpu_nodes token lookup failed: {e}")
        return None


def list_nodes(provider_id: int) -> list[dict[str, Any]]:
    """All nodes belonging to a provider, most-recently-created first.
    Empty list on any lookup error."""
    try:
        client = get_supabase_client()
        result = (
            client.table(_NODES_TABLE)
            .select("*")
            .eq("provider_id", provider_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data or []
    except Exception as e:
        logger.warning(f"gpu_nodes list failed for provider {provider_id}: {e}")
        return []


def update_node(node_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Apply a partial update to a node. Returns the updated row, or None
    on any failure."""
    try:
        client = get_supabase_client()
        result = client.table(_NODES_TABLE).update(updates).eq("id", node_id).execute()
        if not result.data:
            return None
        return result.data[0]
    except Exception as e:
        logger.warning(f"gpu_nodes update failed for node {node_id}: {e}")
        return None


def set_node_status(node_id: int, status: str) -> dict[str, Any] | None:
    """Update a node's status. Returns the updated row, or None on failure."""
    return update_node(node_id, {"status": status})


def record_heartbeat(
    node_id: int,
    outstanding: int,
    models: list[dict[str, Any]] | None = None,
    attested: bool = False,
) -> dict[str, Any] | None:
    """Record a node heartbeat: bumps last_heartbeat_at, marks the node
    'active', and updates outstanding_requests (and models, if the node
    reported an updated catalog). `attested` reflects whether the caller
    already verified an optional wallet-signature over this heartbeat
    (src/routes/gpu.py) -- there is no gpu_nodes column for it (spec
    section 2 has none), so it is accepted here for interface symmetry
    with the brief but is not persisted; callers that need to surface it
    do so in the route response, not via this row.
    """
    updates: dict[str, Any] = {
        "last_heartbeat_at": datetime.now().astimezone().isoformat(),
        "status": "active",
        "outstanding_requests": max(0, outstanding),
    }
    if models is not None:
        updates["models"] = models
    del attested  # documented no-op, see docstring
    return update_node(node_id, updates)


def adjust_outstanding(node_id: int, delta: int) -> dict[str, Any] | None:
    """Adjust a node's outstanding_requests counter by `delta` (clamped at
    0). Best-effort read-modify-write -- not atomic under concurrent
    callers, same tradeoff as every other simple counter in this codebase
    (e.g. api_keys.increment_api_key_usage). Returns the updated row, or
    None on any failure (including the node not existing)."""
    node = get_node(node_id)
    if node is None:
        return None
    new_value = max(0, (node.get("outstanding_requests") or 0) + delta)
    return update_node(node_id, {"outstanding_requests": new_value})


def select_nodes_for_model(model: str) -> list[dict[str, Any]]:
    """Active nodes of approved providers that list `model`, ordered by
    outstanding_requests ascending then health_score descending (least
    loaded, healthiest first). Empty list on any lookup error or if no
    node currently serves the model.

    `models` is stored as a jsonb list of {id, max_context, dtype} --
    PostgREST containment filtering on that shape is awkward, so this
    fetches active nodes + approved providers and filters/sorts in
    Python, same tradeoff src/db/routing_policies.py-style modules make
    for anything beyond a flat column filter.
    """
    try:
        client = get_supabase_client()
        nodes_result = client.table(_NODES_TABLE).select("*").eq("status", "active").execute()
        candidate_nodes = nodes_result.data or []
        if not candidate_nodes:
            return []

        provider_ids = sorted({n["provider_id"] for n in candidate_nodes})
        providers_result = (
            client.table(_PROVIDERS_TABLE)
            .select("id")
            .eq("status", "approved")
            .in_("id", provider_ids)
            .execute()
        )
        approved_provider_ids = {p["id"] for p in (providers_result.data or [])}

        matching = [
            node
            for node in candidate_nodes
            if node["provider_id"] in approved_provider_ids
            and any(m.get("id") == model for m in (node.get("models") or []))
        ]
        matching.sort(
            key=lambda n: (n.get("outstanding_requests") or 0, -(n.get("health_score") or 0))
        )
        return matching
    except Exception as e:
        logger.warning(f"gpu_nodes select_nodes_for_model failed for {model}: {e}")
        return []


def sweep_liveness(now: datetime, degraded_after_s: int, offline_after_s: int) -> tuple[int, int]:
    """Downgrade nodes that have stopped heartbeating: 'active' -> 'degraded'
    after `degraded_after_s` without a heartbeat, and 'active'/'degraded' ->
    'offline' after `offline_after_s`. Never touches 'registered' (never
    heartbeated) or 'disabled' nodes. Returns (n_degraded, n_offline) --
    the counts of nodes actually transitioned this run. (0, 0) on any
    lookup error.
    """
    try:
        client = get_supabase_client()
        result = (
            client.table(_NODES_TABLE)
            .select("id, status, last_heartbeat_at")
            .in_("status", list(_LIVE_NODE_STATUSES))
            .execute()
        )
        candidates = result.data or []
    except Exception as e:
        logger.warning(f"gpu_nodes liveness sweep lookup failed: {e}")
        return 0, 0

    to_degrade: list[int] = []
    to_offline: list[int] = []
    for node in candidates:
        last_heartbeat_at = node.get("last_heartbeat_at")
        if not last_heartbeat_at:
            continue
        try:
            last_seen = datetime.fromisoformat(last_heartbeat_at.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        age_seconds = (now - last_seen).total_seconds()
        if age_seconds >= offline_after_s and node["status"] != "offline":
            to_offline.append(node["id"])
        elif age_seconds >= degraded_after_s and node["status"] != "degraded":
            to_degrade.append(node["id"])

    try:
        client = get_supabase_client()
        if to_degrade:
            client.table(_NODES_TABLE).update({"status": "degraded"}).in_(
                "id", to_degrade
            ).execute()
        if to_offline:
            client.table(_NODES_TABLE).update({"status": "offline"}).in_("id", to_offline).execute()
    except Exception as e:
        logger.warning(f"gpu_nodes liveness sweep update failed: {e}")
        return 0, 0

    return len(to_degrade), len(to_offline)
