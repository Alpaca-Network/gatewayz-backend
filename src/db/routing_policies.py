"""Per-key auto-routing policy overrides (gatewayz-backend#2216).

`routing_policies` (supabase/migrations/20260617000000_gatewayz_one_phase1_registry.sql)
already existed for Gatewayz One's provider-choice smart_router, keyed by
`api_key_id` (NULL = system default). No code read it before this phase --
`smart_router_bridge.py` computes its policy purely from the global
`Config.SMART_ROUTER_POLICY`. This module adds the read path Phase 5 needs:
letting a key opt OUT of the (separately-flagged) model-choice auto-routing
engine, via the `auto_routing_enabled` column added alongside this module
(20260812000000_add_auto_routing_enabled_to_routing_policies.sql).

Mirrors the api_key -> api_keys_new.id -> per-key-row lookup pattern already
established in src/db/rate_limits.py::get_user_rate_limits.
"""

from __future__ import annotations

import logging
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


def get_routing_policy_for_key(api_key: str) -> dict[str, Any] | None:
    """Look up the `routing_policies` row for one API key, if any.

    Returns None on no row / any lookup error -- callers must treat that as
    "no override, fall back to the global default," never as a hard failure.
    """
    try:
        client = get_supabase_client()
        key_record = client.table("api_keys_new").select("id").eq("api_key", api_key).execute()
        if not key_record.data:
            return None

        policy_row = (
            client.table("routing_policies")
            .select("*")
            .eq("api_key_id", key_record.data[0]["id"])
            .execute()
        )
        if not policy_row.data:
            return None
        return policy_row.data[0]
    except Exception as e:
        logger.warning(f"routing_policies lookup failed, using default: {e}")
        return None


def is_auto_routing_disabled_for_key(api_key: str | None) -> bool:
    """True only if this key's routing_policies row explicitly opts out.

    No row, a lookup error, or an explicit `true` all mean "not disabled" --
    this function only ever narrows access relative to the global
    `Config.AUTO_ROUTING_ENABLED` flag, never widens it.
    """
    if not api_key:
        return False
    policy = get_routing_policy_for_key(api_key)
    if not policy:
        return False
    return policy.get("auto_routing_enabled") is False
