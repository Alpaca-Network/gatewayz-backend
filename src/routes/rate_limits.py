import asyncio
import logging
from datetime import datetime, UTC
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.db.api_keys import get_api_key_by_id
from src.db.rate_limits import (
    bulk_update_rate_limit_configs,
    get_rate_limit_alerts,
    get_rate_limit_config,
    get_rate_limit_usage_stats,
    get_system_rate_limit_stats,
    get_user_rate_limit_configs,
    set_user_rate_limits,
    update_rate_limit_config,
)
from src.security.deps import get_api_key, require_admin
from src.services.user_lookup_cache import get_user

# Initialize logging
logger = logging.getLogger(__name__)

router = APIRouter()

# =============================================================================
# ADVANCED RATE LIMITING ENDPOINTS
# =============================================================================


def _fetch_user_rate_limits_sync(api_key: str) -> dict | None:
    """Synchronous helper to fetch all user rate limit data in a thread."""
    user = get_user(api_key)
    if not user:
        return None

    configs = get_user_rate_limit_configs(user["id"])

    enhanced_configs = []
    for config in configs:
        usage_stats = {
            "minute": get_rate_limit_usage_stats(config["api_key"], "minute"),
            "hour": get_rate_limit_usage_stats(config["api_key"], "hour"),
            "day": get_rate_limit_usage_stats(config["api_key"], "day"),
        }

        enhanced_configs.append(
            {
                **config,
                "usage_stats": usage_stats,
                "current_status": {
                    "requests_remaining_minute": max(
                        0,
                        config["rate_limit_config"].get("requests_per_minute", 60)
                        - usage_stats["minute"]["total_requests"],
                    ),
                    "tokens_remaining_minute": max(
                        0,
                        config["rate_limit_config"].get("tokens_per_minute", 10000)
                        - usage_stats["minute"]["total_tokens"],
                    ),
                    "requests_remaining_hour": max(
                        0,
                        config["rate_limit_config"].get("requests_per_hour", 1000)
                        - usage_stats["hour"]["total_requests"],
                    ),
                    "tokens_remaining_hour": max(
                        0,
                        config["rate_limit_config"].get("tokens_per_hour", 100000)
                        - usage_stats["hour"]["total_tokens"],
                    ),
                    "requests_remaining_day": max(
                        0,
                        config["rate_limit_config"].get("requests_per_day", 10000)
                        - usage_stats["day"]["total_requests"],
                    ),
                    "tokens_remaining_day": max(
                        0,
                        config["rate_limit_config"].get("tokens_per_day", 1000000)
                        - usage_stats["day"]["total_tokens"],
                    ),
                },
            }
        )

    return {"user": user, "configs": enhanced_configs}


@router.get("/user/rate-limits", tags=["authentication"])
async def get_user_rate_limits_advanced(api_key: str = Depends(get_api_key)):
    """Get advanced rate limit configuration and status for user's API keys"""
    try:
        # FIX (2026-02-05): Run all synchronous DB queries in a thread
        # to prevent blocking the event loop and causing 499/500 errors
        result = await asyncio.to_thread(_fetch_user_rate_limits_sync, api_key)
        if result is None:
            raise HTTPException(status_code=401, detail="Invalid API key")

        return {
            "status": "success",
            "user_id": result["user"]["id"],
            "rate_limit_configs": result["configs"],
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting advanced rate limits: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.put("/user/rate-limits/{key_id}", tags=["authentication"])
async def update_user_rate_limits_advanced(
    key_id: int, rate_limit_config: dict, api_key: str = Depends(get_api_key)
):
    """Update rate limit configuration for a specific API key"""
    try:
        user = await asyncio.to_thread(get_user, api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Verify the user owns the key
        key_to_update = await asyncio.to_thread(get_api_key_by_id, key_id, user["id"])
        if not key_to_update:
            raise HTTPException(status_code=404, detail="API key not found")

        # Validate rate limit configuration
        required_fields = [
            "requests_per_minute",
            "requests_per_hour",
            "requests_per_day",
            "tokens_per_minute",
            "tokens_per_hour",
            "tokens_per_day",
        ]

        for field in required_fields:
            if field not in rate_limit_config:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")

            if not isinstance(rate_limit_config[field], int) or rate_limit_config[field] < 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid value for {field}: must be non-negative integer",
                )

        # Update rate limit configuration
        success = await asyncio.to_thread(
            update_rate_limit_config, key_to_update["api_key"], rate_limit_config
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to update rate limit configuration")

        return {
            "status": "success",
            "message": "Rate limit configuration updated successfully",
            "key_id": key_id,
            "updated_config": rate_limit_config,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating rate limits: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/user/rate-limits/bulk-update", tags=["authentication"])
async def bulk_update_user_rate_limits(
    rate_limit_config: dict, api_key: str = Depends(get_api_key)
):
    """Bulk update rate limit configuration for all user's API keys"""
    try:
        user = await asyncio.to_thread(get_user, api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Validate rate limit configuration
        required_fields = [
            "requests_per_minute",
            "requests_per_hour",
            "requests_per_day",
            "tokens_per_minute",
            "tokens_per_hour",
            "tokens_per_day",
        ]

        for field in required_fields:
            if field not in rate_limit_config:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")

            if not isinstance(rate_limit_config[field], int) or rate_limit_config[field] < 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid value for {field}: must be non-negative integer",
                )

        # Bulk update rate limit configurations
        updated_count = await asyncio.to_thread(
            bulk_update_rate_limit_configs, user["id"], rate_limit_config
        )

        return {
            "status": "success",
            "message": f"Rate limit configuration updated for {updated_count} API keys",
            "updated_count": updated_count,
            "updated_config": rate_limit_config,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error bulk updating rate limits: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/user/rate-limits/usage/{key_id}", tags=["authentication"])
async def get_api_key_rate_limit_usage(
    key_id: int, time_window: str = "minute", api_key: str = Depends(get_api_key)
):
    """Get detailed rate limit usage statistics for a specific API key"""
    try:
        user = await asyncio.to_thread(get_user, api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        # Verify the user owns the key
        key_to_check = await asyncio.to_thread(get_api_key_by_id, key_id, user["id"])
        if not key_to_check:
            raise HTTPException(status_code=404, detail="API key not found")

        # Validate time window
        if time_window not in ["minute", "hour", "day"]:
            raise HTTPException(
                status_code=400, detail="Invalid time window. Must be 'minute', 'hour', or 'day'"
            )

        # Get usage statistics and config in parallel threads
        usage_stats, rate_limit_config = await asyncio.gather(
            asyncio.to_thread(get_rate_limit_usage_stats, key_to_check["api_key"], time_window),
            asyncio.to_thread(get_rate_limit_config, key_to_check["api_key"]),
        )

        return {
            "status": "success",
            "key_id": key_id,
            "key_name": key_to_check["key_name"],
            "time_window": time_window,
            "usage_stats": usage_stats,
            "rate_limit_config": rate_limit_config,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting rate limit usage: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/admin/rate-limits/system", tags=["admin"])
async def get_system_rate_limits(admin_user: dict = Depends(require_admin)):
    """Get system-wide rate limiting statistics"""
    try:
        stats = await asyncio.to_thread(get_system_rate_limit_stats)

        return {
            "status": "success",
            "system_stats": stats,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except Exception as e:
        logger.error(f"Error getting system rate limit stats: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/admin/rate-limits/alerts", tags=["admin"])
async def get_rate_limit_alerts_endpoint(
    api_key: str | None = None,
    resolved: bool = False,
    limit: int = 100,
    admin_user: dict = Depends(require_admin),
):
    """Get rate limit alerts for monitoring"""
    try:
        alerts = await asyncio.to_thread(get_rate_limit_alerts, api_key, resolved, limit)

        return {
            "status": "success",
            "total_alerts": len(alerts),
            "alerts": alerts,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except Exception as e:
        logger.error(f"Error getting rate limit alerts: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


# =============================================================================
# ADMIN RATE LIMIT MANAGEMENT ENDPOINTS
# =============================================================================


# Default rate limit configuration
DEFAULT_RATE_LIMIT_CONFIG = {
    "requests_per_minute": 60,
    "requests_per_hour": 1000,
    "requests_per_day": 10000,
    "tokens_per_minute": 10000,
    "tokens_per_hour": 100000,
    "tokens_per_day": 1000000,
    "burst_limit": 100,
    "concurrency_limit": 50,
}


class RateLimitConfigRequest(BaseModel):
    """Request to set rate limit configuration"""

    requests_per_minute: int = Field(60, ge=0, description="Requests allowed per minute")
    requests_per_hour: int = Field(1000, ge=0, description="Requests allowed per hour")
    requests_per_day: int = Field(10000, ge=0, description="Requests allowed per day")
    tokens_per_minute: int = Field(10000, ge=0, description="Tokens allowed per minute")
    tokens_per_hour: int = Field(100000, ge=0, description="Tokens allowed per hour")
    tokens_per_day: int = Field(1000000, ge=0, description="Tokens allowed per day")
    burst_limit: int = Field(100, ge=0, description="Burst limit for sudden spikes")
    concurrency_limit: int = Field(50, ge=0, description="Max concurrent requests")


class AdminRateLimitUpdateRequest(BaseModel):
    """Request to update rate limits for a user"""

    api_key: str = Field(..., description="User's API key")
    config: RateLimitConfigRequest = Field(..., description="Rate limit configuration")


class BulkRateLimitUpdateRequest(BaseModel):
    """Request to update rate limits for multiple users"""

    api_keys: list[str] = Field(..., min_length=1, description="List of API keys to update")
    config: RateLimitConfigRequest = Field(..., description="Rate limit configuration")


@router.get("/admin/rate-limits/config", tags=["admin"])
async def get_admin_rate_limit_config(
    api_key: str | None = Query(None, description="Optional API key to get config for"),
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Get rate limit configuration.

    If api_key is provided, returns config for that specific key.
    Otherwise, returns the default system configuration.

    **Query Parameters:**
    - `api_key`: Optional specific API key to get config for

    **Response:**
    - Rate limit configuration
    - Default values if no custom config exists
    """
    try:
        if api_key:
            # Get config for specific API key
            config = await asyncio.to_thread(get_rate_limit_config, api_key)
            return {
                "status": "success",
                "api_key": api_key[:15] + "...",
                "config": config or DEFAULT_RATE_LIMIT_CONFIG,
                "is_default": config is None,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        else:
            # Return default configuration
            return {
                "status": "success",
                "config": DEFAULT_RATE_LIMIT_CONFIG,
                "description": "Default system rate limit configuration",
                "timestamp": datetime.now(UTC).isoformat(),
            }

    except Exception as e:
        logger.error(f"Error getting rate limit config: {e}")
        raise HTTPException(status_code=500, detail="Failed to get rate limit config") from e


@router.post("/admin/rate-limits/config", tags=["admin"])
async def set_admin_rate_limit_config(
    request: AdminRateLimitUpdateRequest,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Set rate limit configuration for a specific API key.

    **Request:**
    - `api_key`: Target API key
    - `config`: Rate limit configuration object

    **Response:**
    - Updated configuration
    - Success status
    """
    try:
        config_dict = request.config.model_dump()

        # Update rate limit configuration
        success = await asyncio.to_thread(
            update_rate_limit_config, request.api_key, config_dict
        )

        if not success:
            raise HTTPException(
                status_code=500, detail="Failed to update rate limit configuration"
            )

        logger.info(
            f"Admin {admin_user.get('username')} updated rate limits for key {request.api_key[:15]}..."
        )

        return {
            "status": "success",
            "message": "Rate limit configuration updated successfully",
            "api_key": request.api_key[:15] + "...",
            "config": config_dict,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting rate limit config: {e}")
        raise HTTPException(status_code=500, detail="Failed to set rate limit config") from e


@router.post("/admin/rate-limits/config/reset", tags=["admin"])
async def reset_rate_limit_config(
    api_key: str = Query(..., description="API key to reset config for"),
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Reset rate limit configuration to defaults for a specific API key.

    **Query Parameters:**
    - `api_key`: API key to reset configuration for

    **Response:**
    - Reset configuration (defaults)
    - Success status
    """
    try:
        # Reset to default configuration
        success = await asyncio.to_thread(
            update_rate_limit_config, api_key, DEFAULT_RATE_LIMIT_CONFIG
        )

        if not success:
            raise HTTPException(
                status_code=500, detail="Failed to reset rate limit configuration"
            )

        logger.info(
            f"Admin {admin_user.get('username')} reset rate limits to defaults for key {api_key[:15]}..."
        )

        return {
            "status": "success",
            "message": "Rate limit configuration reset to defaults",
            "api_key": api_key[:15] + "...",
            "config": DEFAULT_RATE_LIMIT_CONFIG,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resetting rate limit config: {e}")
        raise HTTPException(status_code=500, detail="Failed to reset rate limit config") from e


@router.put("/admin/rate-limits/update", tags=["admin"])
async def update_admin_rate_limits(
    request: AdminRateLimitUpdateRequest,
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Update rate limits for a user.

    This endpoint updates the rate limit configuration for a specific API key.
    It's an alias for POST /admin/rate-limits/config for dashboard compatibility.

    **Request:**
    - `api_key`: Target API key
    - `config`: Rate limit configuration object

    **Response:**
    - Updated configuration
    - Success status
    """
    try:
        config_dict = request.config.model_dump()

        # Use the set_user_rate_limits function (async)
        await set_user_rate_limits(request.api_key, config_dict)

        logger.info(
            f"Admin {admin_user.get('username')} updated rate limits for key {request.api_key[:15]}..."
        )

        return {
            "status": "success",
            "message": "Rate limits updated successfully",
            "api_key": request.api_key[:15] + "...",
            "config": config_dict,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Error updating rate limits: {e}")
        raise HTTPException(status_code=500, detail="Failed to update rate limits") from e


@router.delete("/admin/rate-limits/delete", tags=["admin"])
async def delete_rate_limits(
    api_key: str = Query(..., description="API key to delete rate limits for"),
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Delete custom rate limits for a user (reset to defaults).

    This endpoint removes custom rate limit configuration for a specific API key,
    effectively resetting them to system defaults.

    **Query Parameters:**
    - `api_key`: API key to delete rate limits for

    **Response:**
    - Success status
    - The key will use default rate limits going forward
    """
    from src.config.supabase_config import get_supabase_client

    def _delete_rate_limits_sync(target_api_key: str) -> dict:
        """Synchronous helper to delete rate limits in a thread."""
        client = get_supabase_client()

        # Delete from rate_limit_configs table (migrated from legacy rate_limits table)
        deleted_from_rate_limit_configs = False
        api_key_id = None
        user_id = None

        try:
            # Get API key ID and user_id
            key_record = client.table("api_keys_new").select("id, user_id").eq("api_key", target_api_key).execute()
            if key_record.data and len(key_record.data) > 0:
                api_key_id = key_record.data[0]["id"]
                user_id = key_record.data[0]["user_id"]
                result = client.table("rate_limit_configs").delete().eq("api_key_id", api_key_id).execute()
                deleted_from_rate_limit_configs = len(result.data) > 0 if result.data else False
        except Exception as e:
            logger.debug(f"Could not delete from rate_limit_configs table: {e}")

        # Try to reset rate_limit_config in api_keys_new table
        reset_in_api_keys = False
        try:
            result = (
                client.table("api_keys_new")
                .update({
                    "rate_limit_config": None,
                    "updated_at": datetime.now(UTC).isoformat(),
                })
                .eq("api_key", target_api_key)
                .execute()
            )
            reset_in_api_keys = len(result.data) > 0 if result.data else False
        except Exception as e:
            logger.debug(f"Could not reset rate_limit_config in api_keys_new: {e}")

        # Add audit log entry
        if user_id and api_key_id:
            try:
                client.table("api_key_audit_logs").insert({
                    "user_id": user_id,
                    "api_key_id": api_key_id,
                    "action": "rate_limits_deleted",
                    "details": {
                        "deleted_from_rate_limit_configs": deleted_from_rate_limit_configs,
                        "reset_in_api_keys": reset_in_api_keys,
                        "admin_user": admin_user.get('username'),
                    },
                    "timestamp": datetime.now(UTC).isoformat(),
                }).execute()
            except Exception as audit_error:
                logger.debug(f"Failed to create audit log: {audit_error}")

        return {
            "deleted_from_rate_limit_configs": deleted_from_rate_limit_configs,
            "reset_in_api_keys": reset_in_api_keys,
        }

    try:
        result = await asyncio.to_thread(_delete_rate_limits_sync, api_key)

        logger.info(
            f"Admin {admin_user.get('username')} deleted rate limits for key {api_key[:15]}..."
        )

        return {
            "status": "success",
            "message": "Rate limits deleted successfully. Key will use default limits.",
            "api_key": api_key[:15] + "...",
            "deleted_from_rate_limit_configs": result["deleted_from_rate_limit_configs"],
            "reset_in_api_keys": result["reset_in_api_keys"],
            "default_config": DEFAULT_RATE_LIMIT_CONFIG,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except Exception as e:
        logger.error(f"Error deleting rate limits: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete rate limits") from e


@router.get("/admin/rate-limits/users", tags=["admin"])
async def get_users_rate_limits(
    limit: int = Query(50, ge=1, le=500, description="Maximum users to return"),
    offset: int = Query(0, ge=0, description="Number of users to skip"),
    user_id: int | None = Query(None, description="Filter by specific user ID"),
    has_custom_config: bool | None = Query(None, description="Filter by custom config presence"),
    admin_user: dict = Depends(require_admin),
) -> dict[str, Any]:
    """
    Get rate limits for all users.

    This endpoint provides a list of users and their rate limit configurations.

    **Query Parameters:**
    - `limit`: Maximum users to return (1-500)
    - `offset`: Number to skip for pagination
    - `user_id`: Filter by specific user ID
    - `has_custom_config`: Filter by custom config presence (true/false)

    **Response:**
    - List of users with their rate limit configurations
    - Pagination info
    """
    def _fetch_users_rate_limits_sync(
        q_limit: int, q_offset: int, q_user_id: int | None, q_has_custom: bool | None
    ) -> dict:
        """Synchronous helper to fetch all user rate limits in a thread."""
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Try query with rate_limit_config column first, fallback without it
        rate_limit_config_available = True
        try:
            query = client.table("api_keys_new").select(
                "id, api_key, key_name, user_id, rate_limit_config, environment_tag, created_at"
            )

            if q_user_id is not None:
                query = query.eq("user_id", q_user_id)

            if q_has_custom is not None:
                if q_has_custom:
                    query = query.not_.is_("rate_limit_config", "null")
                else:
                    query = query.is_("rate_limit_config", "null")

            result = query.range(q_offset, q_offset + q_limit).execute()
            api_keys = result.data or []
        except Exception as e:
            if "rate_limit_config" in str(e):
                logger.debug(f"rate_limit_config column not available: {e}")
                rate_limit_config_available = False
                query = client.table("api_keys_new").select(
                    "id, api_key, key_name, user_id, environment_tag, created_at"
                )

                if q_user_id is not None:
                    query = query.eq("user_id", q_user_id)

                result = query.range(q_offset, q_offset + q_limit).execute()
                api_keys = result.data or []
            else:
                raise

        q_has_more = len(api_keys) > q_limit
        api_keys = api_keys[:q_limit]

        users_rate_limits = []
        for key in api_keys:
            if rate_limit_config_available:
                config = key.get("rate_limit_config") or DEFAULT_RATE_LIMIT_CONFIG
                key_has_custom = key.get("rate_limit_config") is not None
            else:
                config = DEFAULT_RATE_LIMIT_CONFIG
                key_has_custom = False
                try:
                    config_result = (
                        client.table("rate_limit_configs")
                        .select("*")
                        .eq("api_key_id", key["id"])
                        .execute()
                    )
                    if config_result.data:
                        cfg = config_result.data[0]
                        config = {
                            "requests_per_minute": cfg.get("max_requests", 1000) // 60,
                            "requests_per_hour": cfg.get("max_requests", 1000),
                            "requests_per_day": cfg.get("max_requests", 1000) * 24,
                            "tokens_per_minute": cfg.get("max_tokens", 1000000) // 60,
                            "tokens_per_hour": cfg.get("max_tokens", 1000000),
                            "tokens_per_day": cfg.get("max_tokens", 1000000) * 24,
                        }
                        key_has_custom = True
                except Exception:
                    pass

            users_rate_limits.append({
                "key_id": key["id"],
                "api_key": key["api_key"][:15] + "...",
                "key_name": key.get("key_name"),
                "user_id": key["user_id"],
                "environment_tag": key.get("environment_tag"),
                "has_custom_config": key_has_custom,
                "config": config,
                "created_at": key.get("created_at"),
            })

        return {"users": users_rate_limits, "has_more": q_has_more}

    try:
        result = await asyncio.to_thread(
            _fetch_users_rate_limits_sync, limit, offset, user_id, has_custom_config
        )

        return {
            "status": "success",
            "total": len(result["users"]),
            "users": result["users"],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "has_more": result["has_more"],
            },
            "filters": {
                "user_id": user_id,
                "has_custom_config": has_custom_config,
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except Exception as e:
        logger.error(f"Error getting users rate limits: {e}")
        raise HTTPException(status_code=500, detail="Failed to get users rate limits") from e
