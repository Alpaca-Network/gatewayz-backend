"""Per-user auto-routing preferences (model-choice level).

Reuses the existing `users.settings` JSONB column (already used today for
things like `default_model`; see
`supabase/migrations/20260125000000_add_user_settings_column.sql`) instead
of a new table -- these are simple user-editable preferences, not the kind
of write-heavy or relationally-queried data a dedicated table earns its
keep for.

Distinct from `src.db.routing_policies` / the `routing_policies` table,
which governs PROVIDER choice for an already-chosen model (keyed by
`api_key_id`, feeds `smart_router.RoutingPolicy`). This module is
MODEL-choice level -- see `src.services.model_selector`'s own docstring for
the same naming-collision warning. Do not conflate the two.
"""

from __future__ import annotations

import logging

from src.db.users import get_user

logger = logging.getLogger(__name__)

INDUSTRIES: tuple[str, ...] = (
    "general",
    "software_engineering",
    "legal",
    "medical_healthcare",
    "finance_accounting",
    "marketing_sales",
    "customer_support",
    "education",
    "research_science",
    "creative_writing",
    "business_operations",
    "other",
)
DEFAULT_INDUSTRY = "general"

# "balanced" is intentionally NOT user-selectable -- it stays an
# internal-only fallback (see model_selector.resolve_adaptive_mode).
VALID_MODES = frozenset({"price", "quality", "fast", "auto"})
DEFAULT_MODE = "auto"


def get_routing_preferences_for_key(api_key: str | None) -> tuple[str, str]:
    """Look up (mode, industry) auto-routing preferences for one API key.

    Reads `settings.routing_mode` / `settings.routing_industry` off the
    user's `users.settings` JSONB blob. Falls back to
    `(DEFAULT_MODE, DEFAULT_INDUSTRY)` on a missing key, no matching user,
    a missing/invalid value, or any lookup error -- mirrors the
    try/except-log-and-fallback shape of
    `routing_policies.get_routing_policy_for_key`. Never raises.
    """
    try:
        if not api_key:
            return DEFAULT_MODE, DEFAULT_INDUSTRY

        user = get_user(api_key)
        if not user:
            return DEFAULT_MODE, DEFAULT_INDUSTRY

        settings = user.get("settings") or {}
        mode = settings.get("routing_mode")
        industry = settings.get("routing_industry")

        if mode not in VALID_MODES:
            mode = DEFAULT_MODE
        if industry not in INDUSTRIES:
            industry = DEFAULT_INDUSTRY

        return mode, industry
    except Exception as e:
        logger.warning(f"routing_preferences lookup failed, using defaults: {e}")
        return DEFAULT_MODE, DEFAULT_INDUSTRY
