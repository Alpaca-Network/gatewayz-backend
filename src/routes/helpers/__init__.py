"""
Route helpers module - Centralized location for all route helper functions.

This module organizes helper functions by domain:
- catalog: Catalog endpoint helpers (model/provider fetching, normalization)
- chat: Chat endpoint helpers (streaming, message handling, auth, billing)
"""

# Catalog helpers
from src.routes.helpers.catalog import (
    enhance_models_batch,
    fetch_and_merge_providers,
    get_graduation_filter_description,
    get_timestamp,
    handle_endpoint_errors,
    normalize_gateway_value,
)

# Chat helpers
from src.routes.helpers.chat import (
    POSTGRES_INT_MAX,
    POSTGRES_INT_MIN,
    build_optional_params,
    check_plan_limits,
    check_rate_limits,
    ensure_capacity,
    get_rate_limit_headers,
    handle_billing,
    inject_chat_history,
    transform_input_to_messages,
    validate_and_adjust_max_tokens,
    validate_session_id,
    validate_trial,
    validate_user_and_auth,
)

__all__ = [
    # Catalog
    "handle_endpoint_errors",
    "normalize_gateway_value",
    "get_graduation_filter_description",
    "enhance_models_batch",
    "get_timestamp",
    "fetch_and_merge_providers",
    # Chat
    "validate_user_and_auth",
    "validate_trial",
    "check_plan_limits",
    "ensure_capacity",
    "check_rate_limits",
    "handle_billing",
    "get_rate_limit_headers",
    "validate_session_id",
    "inject_chat_history",
    "build_optional_params",
    "transform_input_to_messages",
    "validate_and_adjust_max_tokens",
    "POSTGRES_INT_MIN",
    "POSTGRES_INT_MAX",
]
