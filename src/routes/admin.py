import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from src.cache import _huggingface_cache, _models_cache, _provider_cache
from src.config import Config
from src.db.chat_completion_requests import get_chat_completion_requests_by_api_key
from src.db.credit_transactions import get_all_transactions, get_transaction_summary
from src.db.rate_limits import get_user_rate_limits, set_user_rate_limits
from src.db.trials import get_trial_analytics
from src.db.users import (
    add_credits_to_user,
    create_enhanced_user,
    get_admin_monitor_data,
    get_all_users,
    get_user,
)
from src.enhanced_notification_service import enhanced_notification_service
from src.schemas import (
    AddCreditsRequest,
    SetRateLimitRequest,
    UserRegistrationRequest,
    UserRegistrationResponse,
)
from src.security.deps import require_admin
from src.services.models import (
    enhance_model_with_provider_info,
    fetch_huggingface_model,
    get_cached_models,
)
from src.services.providers import fetch_providers_from_openrouter, get_cached_providers

# Initialize logging
logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/create", response_model=UserRegistrationResponse, tags=["authentication"])
async def create_api_key(request: UserRegistrationRequest):
    """Create an API key for the user after dashboard login"""
    try:
        # Validate input
        if request.environment_tag not in ["test", "staging", "live", "development"]:
            raise HTTPException(status_code=400, detail="Invalid environment tag")

        # Create a user account and generate an API key for a dashboard user
        user_data = create_enhanced_user(
            username=request.username,
            email=request.email,
            auth_method=request.auth_method,
            credits=5,  # $5 worth of credits (250,000 tokens)
        )

        # Send a welcome email with API key information
        try:
            enhanced_notification_service.send_welcome_email(
                user_id=user_data["user_id"],
                username=user_data["username"],
                email=user_data["email"],
                credits=user_data["credits"],
            )
        except Exception as e:
            logger.warning(f"Failed to send welcome email: {e}")

        return UserRegistrationResponse(
            user_id=user_data["user_id"],
            username=user_data["username"],
            email=user_data["email"],
            api_key=user_data["primary_api_key"],
            credits=user_data["credits"],
            environment_tag=request.environment_tag,
            scope_permissions={
                "read": ["models", "usage", "profile"],
                "write": ["chat", "completions", "profile_update"],
            },
            auth_method=request.auth_method,
            subscription_status="trial",
            message="API key created successfully!",
            timestamp=datetime.now(timezone.utc),
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"API key creation failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


# Admin endpoints
@router.post("/admin/add_credits", tags=["admin"])
async def admin_add_credits(req: AddCreditsRequest, admin_user: dict = Depends(require_admin)):
    try:
        user = get_user(req.api_key)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Build description with reason if provided
        description = req.reason if req.reason else "Admin credit adjustment"

        # Add credits to a user account with description
        add_credits_to_user(
            user_id=user["id"],
            credits=req.credits,
            transaction_type="admin_credit",
            description=description,
        )

        updated_user = get_user(req.api_key)

        return {
            "status": "success",
            "message": f"Added {req.credits} credits to user {user.get('username', user['id'])}",
            "new_balance": updated_user["credits"],
            "user_id": user["id"],
            "reason": description,
        }

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Add credits failed: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/admin/balance", tags=["admin"])
async def admin_get_all_balances(admin_user: dict = Depends(require_admin)):
    try:
        users = get_all_users()

        user_balances = []
        for user in users:
            user_balances.append(
                {
                    "api_key": user["api_key"],
                    "credits": user["credits"],
                    "created_at": user.get("created_at"),
                    "updated_at": user.get("updated_at"),
                }
            )

        return {"status": "success", "total_users": len(user_balances), "users": user_balances}

    except Exception as e:
        logger.error(f"Error getting all user balances: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/admin/monitor", tags=["admin"])
async def admin_monitor(admin_user: dict = Depends(require_admin)):
    try:
        monitor_data = get_admin_monitor_data()

        if not monitor_data:
            raise HTTPException(status_code=500, detail="Failed to retrieve monitoring data")

        # Check if there's an error in the response
        if "error" in monitor_data:
            logger.error(f"Admin monitor data contains error: {monitor_data['error']}")
            # Still return the data but log the error
            return {
                "status": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": monitor_data,
                "warning": "Data retrieved with errors, some information may be incomplete",
            }

        return {
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": monitor_data,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting admin monitor data: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/admin/limit", tags=["admin"])
async def admin_set_rate_limit(req: SetRateLimitRequest, admin_user: dict = Depends(require_admin)):
    try:
        await set_user_rate_limits(req.api_key, req.rate_limits.model_dump())

        rate_limits = get_user_rate_limits(req.api_key)

        if not rate_limits:
            raise HTTPException(status_code=404, detail="User not found")

        return {
            "status": "success",
            "message": f"Rate limits updated for user {req.api_key[:10]}...",
            "rate_limits": {
                "requests_per_minute": rate_limits["requests_per_minute"],
                "requests_per_hour": rate_limits["requests_per_hour"],
                "requests_per_day": rate_limits["requests_per_day"],
                "tokens_per_minute": rate_limits["tokens_per_minute"],
                "tokens_per_hour": rate_limits["tokens_per_hour"],
                "tokens_per_day": rate_limits["tokens_per_day"],
            },
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error setting rate limits: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


# Admin cache management endpoints
@router.post("/admin/refresh-providers", tags=["admin"])
async def admin_refresh_providers(admin_user: dict = Depends(require_admin)):
    try:
        # Invalidate provider cache to force refresh
        _provider_cache["data"] = None
        _provider_cache["timestamp"] = None

        providers = get_cached_providers()

        return {
            "status": "success",
            "message": "Provider cache refreshed successfully",
            "total_providers": len(providers) if providers else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to refresh provider cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to refresh provider cache") from e


@router.get("/admin/cache-status", tags=["admin"])
async def admin_cache_status(admin_user: dict = Depends(require_admin)):
    try:
        cache_age = None
        if _provider_cache["timestamp"]:
            cache_age = (datetime.now(timezone.utc) - _provider_cache["timestamp"]).total_seconds()

        return {
            "status": "success",
            "cache_info": {
                "has_data": _provider_cache["data"] is not None,
                "cache_age_seconds": cache_age,
                "ttl_seconds": _provider_cache["ttl"],
                "is_valid": cache_age is not None and cache_age < _provider_cache["ttl"],
                "total_cached_providers": (
                    len(_provider_cache["data"]) if _provider_cache["data"] else 0
                ),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to get cache status: {e}")
        raise HTTPException(status_code=500, detail="Failed to get cache status") from e


@router.get("/admin/huggingface-cache-status", tags=["admin"])
async def admin_huggingface_cache_status(admin_user: dict = Depends(require_admin)):
    """Get Hugging Face cache status and statistics"""
    try:
        cache_age = None
        if _huggingface_cache["timestamp"]:
            cache_age = (
                datetime.now(timezone.utc) - _huggingface_cache["timestamp"]
            ).total_seconds()

        return {
            "huggingface_cache": {
                "age_seconds": cache_age,
                "is_valid": cache_age is not None and cache_age < _huggingface_cache["ttl"],
                "total_cached_models": len(_huggingface_cache["data"]),
                "cached_model_ids": list(_huggingface_cache["data"].keys()),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to get Hugging Face cache status: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to get Hugging Face cache status"
        ) from e


@router.post("/admin/refresh-huggingface-cache", tags=["admin"])
async def admin_refresh_huggingface_cache(admin_user: dict = Depends(require_admin)):
    """Clear Hugging Face cache to force refresh on the next request"""
    try:
        _huggingface_cache["data"] = {}
        _huggingface_cache["timestamp"] = None

        return {
            "message": "Hugging Face cache cleared successfully",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to clear Hugging Face cache: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear Hugging Face cache") from e


@router.get("/admin/test-huggingface/{hugging_face_id}", tags=["admin"])
async def admin_test_huggingface(
    hugging_face_id: str = "openai/gpt-oss-120b", admin_user: dict = Depends(require_admin)
):
    """Test Hugging Face API response for debugging"""
    try:
        hf_data = fetch_huggingface_model(hugging_face_id)
        if not hf_data:
            raise HTTPException(
                status_code=404, detail=f"Hugging Face model {hugging_face_id} not found"
            )

        return {
            "hugging_face_id": hugging_face_id,
            "raw_response": hf_data,
            "author_data_extracted": {
                "has_author_data": bool(hf_data.get("author_data")),
                "author_data": hf_data.get("author_data"),
                "author": hf_data.get("author"),
                "extracted_author_data": {
                    "name": (
                        hf_data.get("author_data", {}).get("name")
                        if hf_data.get("author_data")
                        else None
                    ),
                    "fullname": (
                        hf_data.get("author_data", {}).get("fullname")
                        if hf_data.get("author_data")
                        else None
                    ),
                    "avatar_url": (
                        hf_data.get("author_data", {}).get("avatarUrl")
                        if hf_data.get("author_data")
                        else None
                    ),
                    "follower_count": (
                        hf_data.get("author_data", {}).get("followerCount", 0)
                        if hf_data.get("author_data")
                        else 0
                    ),
                },
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to test Hugging Face API for {hugging_face_id}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to test Hugging Face API: {str(e)}"
        ) from e


@router.get("/admin/debug-models", tags=["admin"])
async def admin_debug_models(admin_user: dict = Depends(require_admin)):
    """Debug models and providers data for troubleshooting"""
    try:
        # Get raw data
        models = get_cached_models()
        providers = get_cached_providers()

        # Sample some models and providers
        sample_models = models[:3] if models else []
        sample_providers = providers[:3] if providers else []

        # Test provider matching for a sample model
        provider_matching_test = []
        if sample_models and sample_providers:
            for model in sample_models[:2]:
                model_id = model.get("id", "")
                provider_slug = model_id.split("/")[0] if "/" in model_id else None

                matching_provider = None
                if provider_slug:
                    for provider in providers:
                        if provider.get("slug") == provider_slug:
                            matching_provider = provider
                            break

                provider_matching_test.append(
                    {
                        "model_id": model_id,
                        "provider_slug": provider_slug,
                        "found_provider": bool(matching_provider),
                        "provider_site_url": (
                            matching_provider.get("site_url") if matching_provider else None
                        ),
                        "provider_data": matching_provider,
                    }
                )

        return {
            "models_cache": {
                "total_models": len(models) if models else 0,
                "sample_models": sample_models,
                "cache_timestamp": _models_cache.get("timestamp"),
                "cache_age_seconds": (
                    (datetime.now(timezone.utc) - _models_cache["timestamp"]).total_seconds()
                    if _models_cache.get("timestamp")
                    else None
                ),
            },
            "providers_cache": {
                "total_providers": len(providers) if providers else 0,
                "sample_providers": sample_providers,
                "cache_timestamp": _provider_cache.get("timestamp"),
                "cache_age_seconds": (
                    (datetime.now(timezone.utc) - _provider_cache["timestamp"]).total_seconds()
                    if _provider_cache.get("timestamp")
                    else None
                ),
            },
            "provider_matching_test": provider_matching_test,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to debug models and providers: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to debug: {str(e)}") from e


@router.post("/admin/clear-rate-limit-cache", tags=["admin"])
async def admin_clear_rate_limit_cache(admin_user: dict = Depends(require_admin)):
    """Clear rate limit configuration cache to force reload from database"""
    try:
        from src.services.rate_limiting import get_rate_limit_manager

        # Clear the cached rate limit manager
        manager = get_rate_limit_manager()
        if manager:
            manager.key_configs.clear()
            logger.info("Cleared rate limit manager key_configs cache")

        # Clear the LRU cache by clearing the function cache
        get_rate_limit_manager.cache_clear()

        return {
            "status": "success",
            "message": "Rate limit cache cleared successfully. New requests will reload configuration.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to clear rate limit cache: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to clear rate limit cache: {str(e)}"
        ) from e


@router.get("/admin/trial/analytics", tags=["admin"])
async def get_trial_analytics_admin(admin_user: dict = Depends(require_admin)):
    """Get trial analytics and conversion metrics for admin"""
    try:
        analytics = get_trial_analytics()
        return {"success": True, "analytics": analytics}
    except Exception as e:
        logger.error(f"Error getting trial analytics: {e}")
        raise HTTPException(status_code=500, detail="Failed to get trial analytics") from e


@router.get("/admin/users/growth", tags=["admin"])
async def get_user_growth(
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze (1-365)"),
    admin_user: dict = Depends(require_admin),
):
    """
    Get user growth data over time (Admin only)

    **Purpose**: Provide timeseries data for user growth charts
    **Performance**: Optimized for chart rendering, not full user export
    **Returns**: Daily cumulative user counts over specified period

    **Parameters**:
    - `days`: Number of days to analyze (1-365, default: 30)

    **Response**:
    - Daily data points with cumulative user counts
    - Growth rate calculation
    - Total users at end of period
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Calculate date range
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=days - 1)

        # Get user registration data grouped by day
        # Use created_at field primarily, fallback to registration_date
        try:
            growth_query = (
                client.table("users")
                .select("created_at")
                .gte("created_at", start_date.isoformat())
                .lte("created_at", end_date.isoformat())
                .order("created_at", desc=False)  # Ascending order for cumulative calculation
            )

            growth_result = growth_query.execute()
            user_data = growth_result.data if growth_result.data else []

        except Exception as query_error:
            logger.warning(
                f"Error querying created_at field, trying registration_date: {query_error}"
            )

            # Fallback to registration_date if created_at fails
            try:
                growth_query = (
                    client.table("users")
                    .select("registration_date")
                    .gte("registration_date", start_date.isoformat())
                    .lte("registration_date", end_date.isoformat())
                    .order("registration_date", desc=False)
                )

                growth_result = growth_query.execute()
                user_data = growth_result.data if growth_result.data else []

                # Map registration_date to created_at for consistent processing
                user_data = [{"created_at": user.get("registration_date")} for user in user_data]

            except Exception as fallback_error:
                logger.error(
                    f"Both created_at and registration_date queries failed: {fallback_error}"
                )
                # Return empty growth data as fallback
                return {
                    "status": "success",
                    "days": days,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "data": [],
                    "total": 0,
                    "growth_rate": 0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }

        # Initialize daily data structure
        daily_data = {}
        current_date = start_date

        # Create entry for each day in the range
        while current_date <= end_date:
            daily_data[current_date.isoformat()] = 0
            current_date += timedelta(days=1)

        # Count users created each day
        for user in user_data:
            created_date_str = user.get("created_at")
            if created_date_str:
                try:
                    # Handle different date formats
                    if created_date_str.endswith("Z"):
                        # ISO format with Z suffix
                        created_date = datetime.fromisoformat(
                            created_date_str.replace("Z", "+00:00")
                        ).date()
                    else:
                        # Regular ISO format or other formats
                        created_date = datetime.fromisoformat(created_date_str).date()

                    date_key = created_date.isoformat()
                    if date_key in daily_data:
                        daily_data[date_key] += 1

                except (ValueError, TypeError) as date_error:
                    logger.debug(
                        f"Skipping invalid date format: {created_date_str}, error: {date_error}"
                    )
                    continue

        # Calculate cumulative counts
        cumulative_data = []
        cumulative_total = 0

        # Get total users before start date for proper cumulative calculation
        try:
            before_start_query = (
                client.table("users")
                .select("id", count="exact")
                .lt("created_at", start_date.isoformat())
            )

            before_start_result = before_start_query.execute()
            cumulative_total = (
                before_start_result.count if before_start_result.count is not None else 0
            )

        except Exception as count_error:
            logger.warning(f"Error getting count before start date: {count_error}")
            cumulative_total = 0

        # Build cumulative data
        for date_str in sorted(daily_data.keys()):
            new_users_today = daily_data[date_str]
            cumulative_total += new_users_today

            cumulative_data.append(
                {"date": date_str, "value": cumulative_total, "new_users": new_users_today}
            )

        # Calculate growth rate
        if len(cumulative_data) >= 2:
            start_value = cumulative_data[0]["value"]
            end_value = cumulative_data[-1]["value"]
            growth_rate = ((end_value - start_value) / start_value * 100) if start_value > 0 else 0
        else:
            growth_rate = 0

        return {
            "status": "success",
            "days": days,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "data": cumulative_data,
            "total": cumulative_total,
            "growth_rate": round(growth_rate, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Error getting user growth data: {e}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to get user growth data") from e


@router.get("/admin/users/count", tags=["admin"])
async def get_users_count(admin_user: dict = Depends(require_admin)):
    """
    Get total user count (Admin only)

    **ULTRA FAST** - Returns only the total count of all users in the system.
    Perfect for simple metrics and dashboard counters.

    **Response**:
    - `count`: Total number of users in the database

    **Performance**: ~5-20ms (pure COUNT query)
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Simple COUNT query - no data fetching
        count_query = client.table("users").select("id", count="exact")

        count_result = count_query.execute()
        total_count = count_result.count if count_result.count is not None else 0

        return {
            "count": total_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Error getting users count: {e}")
        raise HTTPException(status_code=500, detail="Failed to get users count") from e


@router.get("/admin/users/stats", tags=["admin"])
async def get_users_stats(
    # Optional filters (same as main endpoint)
    email: str | None = Query(None, description="Filter by email (case-insensitive partial match)"),
    api_key: str | None = Query(
        None, description="Filter by API key (case-insensitive partial match)"
    ),
    is_active: bool | None = Query(None, description="Filter by active status (true/false)"),
    # Auth
    admin_user: dict = Depends(require_admin),
):
    """
    Get user statistics without fetching user data (Admin only)

    **FAST & LIGHTWEIGHT** - Returns only counts and statistics, no user data.
    Perfect for dashboard cards and stats widgets.

    **Filters** (optional):
    - `email`: Filter stats by email pattern
    - `api_key`: Filter stats by API key pattern
    - `is_active`: Filter stats by active status

    **Response**:
    - Total user counts (filtered)
    - Active/Inactive breakdown
    - Role distribution
    - Credit statistics
    - Subscription breakdown

    **Performance**: ~10-50ms (vs 500ms+ for full user list)
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Simple partial match - searches anywhere in email address
        # This matches the intuitive behavior users expect
        email_pattern = f"%{email}%" if email else None
        logger.info(f"Email search pattern: {email_pattern}")

        # Build base count query
        if api_key:
            count_query = client.table("users").select(
                "id, api_keys_new!inner(api_key)", count="exact"
            )
        else:
            count_query = client.table("users").select("id", count="exact")

        # Apply filters to count query
        if email_pattern:
            count_query = count_query.ilike("email", email_pattern)

        if api_key:
            count_query = count_query.ilike("api_keys_new.api_key", f"%{api_key}%")

        if is_active is not None:
            count_query = count_query.eq("is_active", is_active)

        # Execute count query to get total users
        count_result = count_query.execute()
        total_users = count_result.count if count_result.count is not None else 0
        logger.info(
            f"Stats query - Total users: {total_users}, email_pattern: {email_pattern}, api_key: {api_key}, is_active: {is_active}"
        )

        # Build separate queries for role and subscription statistics
        # Role distribution
        if api_key:
            role_query = (
                client.table("users")
                .select("role, api_keys_new!inner(api_key)", count="exact")
                .limit(100000)
            )
        else:
            role_query = client.table("users").select("role", count="exact").limit(100000)

        # Apply same filters to role query
        if email_pattern:
            role_query = role_query.ilike("email", email_pattern)
        if api_key:
            role_query = role_query.ilike("api_keys_new.api_key", f"%{api_key}%")
        if is_active is not None:
            role_query = role_query.eq("is_active", is_active)

        role_result = role_query.execute()
        role_data = role_result.data if role_result.data else []

        # Count roles
        admin_users = sum(1 for u in role_data if u.get("role") == "admin")
        developer_users = sum(1 for u in role_data if u.get("role") == "developer")
        regular_users = sum(
            1 for u in role_data if u.get("role") == "user" or u.get("role") is None
        )

        # Active/Inactive status (we need this for the breakdown)
        if api_key:
            status_query = (
                client.table("users")
                .select("is_active, api_keys_new!inner(api_key)", count="exact")
                .limit(100000)
            )
        else:
            status_query = client.table("users").select("is_active", count="exact").limit(100000)

        # Apply same filters to status query
        if email_pattern:
            status_query = status_query.ilike("email", email_pattern)
        if api_key:
            status_query = status_query.ilike("api_keys_new.api_key", f"%{api_key}%")
        if is_active is not None:
            status_query = status_query.eq("is_active", is_active)

        status_result = status_query.execute()
        status_data = status_result.data if status_result.data else []

        # Count active/inactive - explicitly check for True (not None or False)
        active_users = sum(1 for u in status_data if u.get("is_active") is True)
        inactive_users = total_users - active_users
        logger.info(
            f"Status breakdown - Total: {total_users}, Active: {active_users}, Inactive: {inactive_users}, Data rows: {len(status_data)}"
        )

        # Credit statistics - fetch all filtered users to calculate accurate totals
        if api_key:
            credits_query = (
                client.table("users")
                .select("credits, api_keys_new!inner(api_key)", count="exact")
                .limit(100000)
            )
        else:
            credits_query = client.table("users").select("credits", count="exact").limit(100000)

        # Apply same filters to credits query
        if email_pattern:
            credits_query = credits_query.ilike("email", email_pattern)
        if api_key:
            credits_query = credits_query.ilike("api_keys_new.api_key", f"%{api_key}%")
        if is_active is not None:
            credits_query = credits_query.eq("is_active", is_active)

        credits_result = credits_query.execute()
        credits_data = credits_result.data if credits_result.data else []

        # Calculate credit statistics
        total_credits = sum(float(u.get("credits", 0)) for u in credits_data)
        avg_credits = round(total_credits / total_users, 2) if total_users > 0 else 0
        logger.info(
            f"Stats calculated - active: {active_users}, credits: {total_credits}, avg: {avg_credits}"
        )

        # Subscription breakdown
        if api_key:
            subscription_query = (
                client.table("users")
                .select("subscription_status, api_keys_new!inner(api_key)", count="exact")
                .limit(100000)
            )
        else:
            subscription_query = (
                client.table("users").select("subscription_status", count="exact").limit(100000)
            )

        # Apply same filters to subscription query
        if email_pattern:
            subscription_query = subscription_query.ilike("email", email_pattern)
        if api_key:
            subscription_query = subscription_query.ilike("api_keys_new.api_key", f"%{api_key}%")
        if is_active is not None:
            subscription_query = subscription_query.eq("is_active", is_active)

        subscription_result = subscription_query.execute()
        subscription_data = subscription_result.data if subscription_result.data else []

        # Get subscription breakdown
        subscription_stats = {}
        for user in subscription_data:
            status = user.get("subscription_status", "unknown")
            subscription_stats[status] = subscription_stats.get(status, 0) + 1

        return {
            "status": "success",
            "total_users": total_users,
            "filters_applied": {
                "email": email,
                "api_key": api_key,
                "is_active": is_active,
            },
            "statistics": {
                "active_users": active_users,
                "inactive_users": inactive_users,
                "admin_users": admin_users,
                "developer_users": developer_users,
                "regular_users": regular_users,
                "total_credits": round(total_credits, 2),
                "average_credits": avg_credits,
                "subscription_breakdown": subscription_stats,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Error getting users stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to get users statistics") from e


@router.get("/admin/users", tags=["admin"])
async def get_all_users_info(
    # Search filters
    email: str | None = Query(None, description="Filter by email (case-insensitive partial match)"),
    api_key: str | None = Query(
        None, description="Filter by API key (case-insensitive partial match)"
    ),
    is_active: bool | None = Query(None, description="Filter by active status (true/false)"),
    # Pagination
    limit: int = Query(
        100, ge=1, le=10000, description="Maximum number of users to return (increased to 10000)"
    ),
    offset: int = Query(0, ge=0, description="Number of users to skip (pagination)"),
    # Auth
    admin_user: dict = Depends(require_admin),
):
    """
    Get users information with search and pagination (Admin only)

    **OPTIMIZED FOR LARGE DATASETS** - This endpoint only fetches users, no statistics.
    Use `/admin/users/stats` for statistics if needed.

    **Search Parameters**:
    - `email`: Case-insensitive partial match (e.g., "john" matches "john@example.com")
    - `api_key`: Case-insensitive partial match (e.g., "gw_live" matches keys starting with "gw_live")
    - `is_active`: Filter by active status (true = active only, false = inactive only, null = all)

    **Pagination**:
    - `limit`: Records per page (1-10000, default: 100)
    - `offset`: Records to skip (default: 0)

    **Response**:
    - `total_users`: Total matching the filters (not total in database)
    - `has_more`: Whether more results exist beyond current page
    - `users`: Current page of filtered users
    - `pagination`: Pagination metadata
    - `filters_applied`: Applied filters

    **Note**: Statistics are NOT included. Use `/admin/users/stats` for statistics.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # OPTIMIZATION: Use PostgreSQL RPC function for email search to avoid PostgREST edge function issues
        if email and not api_key and is_active is None:
            # Use optimized RPC function for simple email-only searches
            logger.info(f"Using RPC function for email search: {email}")
            try:
                result = client.rpc(
                    "search_users_by_email",
                    {"search_term": email, "result_limit": limit, "result_offset": offset},
                ).execute()

                users_data = result.data if result.data else []

                # Extract total count from first row (all rows have same total_count)
                total_users = users_data[0]["total_count"] if users_data else 0

                # Clean up total_count from user objects
                users = []
                for user in users_data:
                    user_clean = {k: v for k, v in user.items() if k != "total_count"}
                    users.append(user_clean)

                # Calculate has_more for pagination
                has_more = (offset + limit) < total_users

                return {
                    "status": "success",
                    "total_users": total_users,
                    "has_more": has_more,
                    "pagination": {
                        "limit": limit,
                        "offset": offset,
                        "current_page": (offset // limit) + 1,
                        "total_pages": (total_users + limit - 1) // limit if total_users > 0 else 0,
                    },
                    "filters_applied": {
                        "email": email,
                        "api_key": api_key,
                        "is_active": is_active,
                    },
                    "users": users,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            except Exception as rpc_err:
                logger.error(f"RPC function failed for email search: {rpc_err}", exc_info=True)
                # Don't fallback for email-only searches - the standard query crashes on Cloudflare
                raise HTTPException(
                    status_code=500,
                    detail=f"Email search failed: {str(rpc_err)}. Please check if the search_users_by_email function exists in Supabase.",
                ) from rpc_err

        # Standard query method (for complex filters or RPC fallback)
        # Build count query first (without pagination, just filters)
        if api_key:
            count_query = client.table("users").select(
                "id, api_keys_new!inner(api_key)", count="exact"
            )
        else:
            count_query = client.table("users").select("id", count="exact")

        # Apply filters to count query
        if email:
            logger.info(f"Searching for email containing: {email}")
            count_query = count_query.ilike("email", f"%{email}%")

        if api_key:
            count_query = count_query.ilike("api_keys_new.api_key", f"%{api_key}%")

        if is_active is not None:
            count_query = count_query.eq("is_active", is_active)

        # Execute count query
        try:
            count_result = count_query.execute()
            total_users = count_result.count if count_result.count is not None else 0
        except Exception as count_err:
            logger.error(f"Error getting user count: {count_err}")
            logger.error(f"Filters - email: {email}, api_key: {api_key}, is_active: {is_active}")
            # Fallback to 0 if count fails
            total_users = 0

        # Build data query for actual user records
        if api_key:
            # Query with JOIN for API key search
            data_query = client.table("users").select(
                "id, username, email, credits, is_active, role, registration_date, "
                "auth_method, subscription_status, trial_expires_at, created_at, updated_at, "
                "api_keys_new!inner(api_key)"
            )
        else:
            # Query without JOIN for better performance when not searching by API key
            data_query = client.table("users").select(
                "id, username, email, credits, is_active, role, registration_date, "
                "auth_method, subscription_status, trial_expires_at, created_at, updated_at"
            )

        # Apply filters to data query
        if email:
            data_query = data_query.ilike("email", f"%{email}%")

        if api_key:
            data_query = data_query.ilike("api_keys_new.api_key", f"%{api_key}%")

        if is_active is not None:
            data_query = data_query.eq("is_active", is_active)

        # Apply sorting and pagination
        data_query = data_query.order("created_at", desc=True).range(offset, offset + limit - 1)

        # Execute data query
        try:
            result = data_query.execute()
            users_data = result.data if result.data else []
        except Exception as data_err:
            logger.error(f"Error fetching user data: {data_err}")
            logger.error(f"Filters - email: {email}, api_key: {api_key}, is_active: {is_active}")
            raise HTTPException(
                status_code=500, detail=f"Failed to fetch users: {str(data_err)}"
            ) from data_err

        # Clean up api_keys_new from response if it was included
        users = []
        for user in users_data:
            # Remove the api_keys_new join data from response
            user_clean = {k: v for k, v in user.items() if k != "api_keys_new"}
            users.append(user_clean)

        # Calculate has_more for pagination
        has_more = (offset + limit) < total_users
        return {
            "status": "success",
            "total_users": total_users,  # Total matching filters
            "has_more": has_more,  # Whether more results exist
            "pagination": {
                "limit": limit,
                "offset": offset,
                "current_page": (offset // limit) + 1,
                "total_pages": (total_users + limit - 1) // limit if total_users > 0 else 0,
            },
            "filters_applied": {
                "email": email,
                "api_key": api_key,
                "is_active": is_active,
            },
            "users": users,  # Current page of users
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        # Re-raise HTTP exceptions (like the one from data_err above)
        raise
    except Exception as e:
        logger.error(f"Error getting all users info: {e}")
        logger.error(f"Filters used - email: {email}, api_key: {api_key}, is_active: {is_active}")
        logger.error(f"Pagination - limit: {limit}, offset: {offset}")
        import traceback

        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500, detail=f"Failed to get users information: {str(e)}"
        ) from e


@router.get("/admin/credit-transactions", tags=["admin"])
async def get_all_credit_transactions_admin(
    limit: int = Query(50, ge=1, le=1000, description="Maximum number of transactions to return"),
    offset: int = Query(0, ge=0, description="Number of transactions to skip"),
    user_id: int = Query(None, description="Filter by specific user ID"),
    transaction_type: str = Query(
        None,
        description="Filter by transaction type (trial, purchase, api_usage, admin_credit, admin_debit, refund, bonus, transfer)",
    ),
    from_date: str = Query(None, description="Start date filter (YYYY-MM-DD or ISO format)"),
    to_date: str = Query(None, description="End date filter (YYYY-MM-DD or ISO format)"),
    min_amount: float = Query(None, description="Minimum transaction amount (absolute value)"),
    max_amount: float = Query(None, description="Maximum transaction amount (absolute value)"),
    direction: str = Query(
        None,
        description="Filter by direction: 'credit' (positive amounts) or 'charge' (negative amounts)",
    ),
    payment_id: int = Query(None, description="Filter by payment ID"),
    sort_by: str = Query(
        "created_at", description="Sort field: 'created_at', 'amount', or 'transaction_type'"
    ),
    sort_order: str = Query("desc", description="Sort order: 'asc' or 'desc'"),
    include_summary: bool = Query(False, description="Include summary analytics in response"),
    admin_user: dict = Depends(require_admin),
):
    """
    Get all credit transactions across all users (Admin only)

    This endpoint allows admins to view all credit transactions in the system with the same
    advanced filtering capabilities as the user endpoint.

    **Differences from user endpoint:**
    - Views ALL users' transactions (unless filtered by user_id)
    - Requires admin authentication
    - Optional user_id filter to view specific user's transactions

    **Filters:**
    - `user_id`: Filter by specific user (optional, if not provided shows all users)
    - `transaction_type`: Filter by type (trial, purchase, api_usage, etc.)
    - `from_date` / `to_date`: Date range filtering (YYYY-MM-DD format)
    - `min_amount` / `max_amount`: Amount range filtering
    - `direction`: 'credit' (additions) or 'charge' (deductions)
    - `payment_id`: Filter by specific payment
    - `sort_by`: Sort by date, amount, or type
    - `sort_order`: 'asc' or 'desc'

    **Response includes:**
    - Filtered transactions list (with user_id included)
    - Summary analytics (if include_summary=true)
    """
    try:
        # Validate direction filter
        if direction and direction.lower() not in ("credit", "charge"):
            raise HTTPException(status_code=400, detail="direction must be 'credit' or 'charge'")

        # Validate sort_by
        if sort_by not in ("created_at", "amount", "transaction_type"):
            raise HTTPException(
                status_code=400,
                detail="sort_by must be 'created_at', 'amount', or 'transaction_type'",
            )

        # Validate sort_order
        if sort_order.lower() not in ("asc", "desc"):
            raise HTTPException(status_code=400, detail="sort_order must be 'asc' or 'desc'")

        # Get all transactions with filters
        transactions = get_all_transactions(
            limit=limit,
            offset=offset,
            user_id=user_id,
            transaction_type=transaction_type,
            from_date=from_date,
            to_date=to_date,
            min_amount=min_amount,
            max_amount=max_amount,
            direction=direction,
            payment_id=payment_id,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        # Format transactions (include user_id for admin view)
        formatted_transactions = [
            {
                "id": txn["id"],
                "user_id": txn["user_id"],
                "amount": float(txn["amount"]),
                "transaction_type": txn["transaction_type"],
                "description": txn.get("description", ""),
                "balance_before": float(txn["balance_before"]),
                "balance_after": float(txn["balance_after"]),
                "created_at": txn["created_at"],
                "payment_id": txn.get("payment_id"),
                "metadata": txn.get("metadata", {}),
                "created_by": txn.get("created_by"),
            }
            for txn in transactions
        ]

        # Build response
        response = {
            "transactions": formatted_transactions,
            "pagination": {
                "total": len(formatted_transactions),
                "limit": limit,
                "offset": offset,
                "has_more": len(formatted_transactions) == limit,  # Best guess
            },
            "filters_applied": {
                "user_id": user_id,
                "transaction_type": transaction_type,
                "from_date": from_date,
                "to_date": to_date,
                "min_amount": min_amount,
                "max_amount": max_amount,
                "direction": direction,
                "payment_id": payment_id,
                "sort_by": sort_by,
                "sort_order": sort_order,
            },
        }

        # Include summary if requested (only if user_id is specified, otherwise too expensive)
        if include_summary and user_id is not None:
            summary = get_transaction_summary(
                user_id=user_id,
                from_date=from_date,
                to_date=to_date,
            )
            response["summary"] = summary
        elif include_summary and user_id is None:
            logger.warning(
                "Summary requested but user_id not specified - skipping summary for performance"
            )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting all credit transactions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/admin/users/by-api-key", tags=["admin"])
async def get_user_by_api_key(
    api_key: str = Query(..., description="Full API key (exact match required)"),
    admin_user: dict = Depends(require_admin),
):
    """
    Get user information by exact API key match (Admin only)

    **FAST EXACT MATCH** - Looks up which user owns a specific API key.
    Does NOT support partial matching - you must provide the complete API key.

    **Parameters**:
    - `api_key`: Complete API key (e.g., "gw_live_abc123...")

    **Response**:
    - User information only (id, username, email, credits, status, etc.)
    - 404 if API key doesn't exist

    **Performance**: ~10-20ms (indexed lookup)

    **Example**:
    ```
    GET /admin/users/by-api-key?api_key=gw_live_abc123xyz
    ```

    **Response format**:
    ```json
    {
      "status": "success",
      "user": {
        "id": 123,
        "username": "john_doe",
        "email": "john@example.com",
        "credits": 50.0,
        "is_active": true,
        "role": "user",
        "subscription_status": "active",
        "created_at": "2025-01-01T00:00:00Z"
      },
      "timestamp": "2026-01-05T10:30:00Z"
    }
    ```
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Use PostgreSQL RPC function for fast, indexed lookup
        logger.info(f"Looking up user by API key: {api_key[:20]}...")

        result = client.rpc("search_user_by_api_key", {"search_api_key": api_key}).execute()

        if not result.data or len(result.data) == 0:
            raise HTTPException(
                status_code=404, detail=f"No user found with API key: {api_key[:20]}..."
            )

        # Extract user and key info from function result
        user_data = result.data[0]

        # Build user object
        user = {
            "id": user_data.get("user_id"),
            "username": user_data.get("username"),
            "email": user_data.get("email"),
            "credits": user_data.get("credits", 0),
            "is_active": user_data.get("is_active", True),
            "role": user_data.get("role", "user"),
            "subscription_status": user_data.get("subscription_status", "trial"),
            "created_at": user_data.get("created_at"),
        }

        return {
            "status": "success",
            "user": user,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error looking up user by API key: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to lookup user by API key: {str(e)}"
        ) from e


@router.get("/admin/api-keys/{api_key_id}", tags=["admin", "api-keys"])
async def get_api_key_details_by_id(api_key_id: int, admin_user: dict = Depends(require_admin)):
    """
    Get complete API key details by ID including user information (Admin only).

    This endpoint returns comprehensive information about an API key:
    - Full API key details (including the actual key string)
    - User information (email, name, username)
    - Key metadata (name, environment, permissions, limits)
    - Usage statistics

    **Parameters**:
    - `api_key_id`: The numeric ID of the API key

    **Response**:
    - Complete API key details with nested user information
    - 404 if API key ID doesn't exist

    **Security**: Admin-only endpoint - handles sensitive data

    **Example**:
    ```
    GET /admin/api-keys/123
    ```

    **Response format**:
    ```json
    {
      "status": "success",
      "api_key": {
        "id": 123,
        "api_key": "gw_live_abc123xyz...",
        "key_name": "Production Key",
        "environment_tag": "live",
        "is_active": true,
        "is_primary": false,
        "scope_permissions": {"read": ["*"], "write": ["*"]},
        "max_requests": 10000,
        "requests_used": 523,
        "ip_allowlist": [],
        "domain_referrers": [],
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-05T12:00:00Z",
        "last_used_at": "2025-01-06T08:30:00Z",
        "expiration_date": null,
        "user": {
          "id": 456,
          "email": "user@example.com",
          "username": "john_doe",
          "credits": 50.0,
          "is_active": true,
          "role": "user",
          "subscription_status": "active",
          "created_at": "2024-12-01T00:00:00Z"
        }
      },
      "timestamp": "2026-01-06T10:00:00Z"
    }
    ```
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        logger.info(f"Admin {admin_user.get('id')} fetching API key details for ID: {api_key_id}")

        # Fetch API key with user information in a single query
        result = (
            client.table("api_keys_new")
            .select(
                "*, users!inner(id, email, username, credits, is_active, role, subscription_status, created_at)"
            )
            .eq("id", api_key_id)
            .execute()
        )

        if not result.data or len(result.data) == 0:
            raise HTTPException(status_code=404, detail=f"API key with ID {api_key_id} not found")

        api_key_data = result.data[0]
        user_data = api_key_data.pop("users")  # Extract nested user data

        # Build comprehensive response
        response_data = {
            "id": api_key_data.get("id"),
            "api_key": api_key_data.get("api_key"),  # Full API key string
            "key_name": api_key_data.get("key_name"),
            "environment_tag": api_key_data.get("environment_tag", "live"),
            "is_active": api_key_data.get("is_active", True),
            "is_primary": api_key_data.get("is_primary", False),
            "scope_permissions": api_key_data.get("scope_permissions", {}),
            "max_requests": api_key_data.get("max_requests"),
            "requests_used": api_key_data.get("requests_used", 0),
            "ip_allowlist": api_key_data.get("ip_allowlist", []),
            "domain_referrers": api_key_data.get("domain_referrers", []),
            "created_at": api_key_data.get("created_at"),
            "updated_at": api_key_data.get("updated_at"),
            "last_used_at": api_key_data.get("last_used_at"),
            "expiration_date": api_key_data.get("expiration_date"),
            "user": {
                "id": user_data.get("id"),
                "email": user_data.get("email"),
                "username": user_data.get("username"),
                "credits": user_data.get("credits"),
                "is_active": user_data.get("is_active"),
                "role": user_data.get("role"),
                "subscription_status": user_data.get("subscription_status"),
                "created_at": user_data.get("created_at"),
            },
        }

        return {
            "status": "success",
            "api_key": response_data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching API key details for ID {api_key_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to fetch API key details: {str(e)}"
        ) from e


@router.get("/admin/users/{user_id}", tags=["admin"])
async def get_user_info_by_id(user_id: int, admin_user: dict = Depends(require_admin)):
    """Get detailed information for a specific user (Admin only)"""
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Get user information
        user_result = client.table("users").select("*").eq("id", user_id).execute()

        if not user_result.data:
            raise HTTPException(status_code=404, detail="User not found")

        user = user_result.data[0]

        # Get user's API keys
        api_keys_result = client.table("api_keys_new").select("*").eq("user_id", user_id).execute()
        api_keys = api_keys_result.data if api_keys_result.data else []

        # Get user's usage records (if available)
        try:
            usage_result = (
                client.table("usage_records")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(10)
                .execute()
            )
            recent_usage = usage_result.data if usage_result.data else []
        except Exception:
            recent_usage = []

        # Get user's activity log (if available)
        try:
            activity_result = (
                client.table("activity_log")
                .select("*")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .limit(10)
                .execute()
            )
            recent_activity = activity_result.data if activity_result.data else []
        except Exception:
            recent_activity = []

        return {
            "status": "success",
            "user": user,
            "api_keys": api_keys,
            "recent_usage": recent_usage,
            "recent_activity": recent_activity,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user info for ID {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get user information") from e


@router.delete("/admin/users/by-domain/{domain}", tags=["admin"])
async def delete_users_by_domain(
    domain: str,
    dry_run: bool = Query(
        True, description="If true, only list users that would be deleted without actually deleting"
    ),
    admin_user: dict = Depends(require_admin),
):
    """
    Delete all users with emails from a specific domain (Admin only)

    **Purpose**: Remove accounts created from abusive or blocked email domains

    **Parameters**:
    - `domain`: Email domain to match (e.g., "spam-domain.org")
    - `dry_run`: If true (default), only shows users that would be deleted without deleting

    **Response**:
    - List of affected user IDs and emails
    - Count of users deleted (or would be deleted in dry_run mode)

    **Safety**:
    - Requires admin authentication
    - dry_run=true by default to prevent accidental deletion
    - Logs all deletions for audit trail
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Normalize domain
        domain = domain.lower().strip()

        # Prevent deleting users from common legitimate domains
        protected_domains = {
            "gmail.com",
            "yahoo.com",
            "outlook.com",
            "hotmail.com",
            "icloud.com",
            "protonmail.com",
        }
        if domain in protected_domains:
            raise HTTPException(
                status_code=400, detail=f"Cannot delete users from protected domain: {domain}"
            )

        # Find all users with emails matching the domain
        # Using ilike for case-insensitive matching with wildcard
        users_query = (
            client.table("users")
            .select("id, email, username, created_at, credits")
            .ilike("email", f"%@{domain}")
        )

        users_result = users_query.execute()
        users_to_delete = users_result.data if users_result.data else []

        if not users_to_delete:
            return {
                "status": "success",
                "message": f"No users found with email domain: {domain}",
                "dry_run": dry_run,
                "count": 0,
                "users": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        # Prepare user summary
        user_summary = [
            {
                "id": u["id"],
                "email": u.get("email"),
                "username": u.get("username"),
                "created_at": u.get("created_at"),
                "credits": u.get("credits", 0),
            }
            for u in users_to_delete
        ]

        if dry_run:
            logger.info(
                f"DRY RUN: Would delete {len(users_to_delete)} users from domain {domain} "
                f"(admin: {admin_user.get('id', 'unknown')})"
            )
            return {
                "status": "success",
                "message": f"DRY RUN: Would delete {len(users_to_delete)} users from domain: {domain}",
                "dry_run": True,
                "count": len(users_to_delete),
                "users": user_summary,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

        # Actually delete users
        deleted_count = 0
        failed_deletions = []

        for user in users_to_delete:
            try:
                client.table("users").delete().eq("id", user["id"]).execute()
                deleted_count += 1
                logger.info(
                    f"Deleted user {user['id']} (email: {user.get('email')}) "
                    f"from domain {domain} (admin: {admin_user.get('id', 'unknown')})"
                )
            except Exception as e:
                logger.error(f"Failed to delete user {user['id']}: {e}")
                failed_deletions.append({"id": user["id"], "error": str(e)})

        return {
            "status": "success",
            "message": f"Deleted {deleted_count} users from domain: {domain}",
            "dry_run": False,
            "count": deleted_count,
            "failed": failed_deletions,
            "users": user_summary,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting users by domain {domain}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete users by domain") from e


# ============================================================================
# ADMIN MONITORING - Chat Completion Requests Analytics
# ============================================================================
# These endpoints provide comprehensive chat completion request analytics
# for administrators. All endpoints require admin authentication.


@router.get("/admin/monitoring/chat-requests/by-api-key", tags=["admin", "monitoring"])
async def get_chat_completion_requests_by_api_key_admin(
    api_key: str = Query(..., description="API key to search for (exact match)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of requests to return"),
    offset: int = Query(0, ge=0, description="Number of requests to skip for pagination"),
    include_summary: bool = Query(False, description="Include summary statistics (DEPRECATED - use /summary endpoint)"),
    admin_user: dict = Depends(require_admin),
):
    """
    Get paginated chat completion requests for a specific API key (Admin only).

    **OPTIMIZED FOR PAGINATION** - Fast data fetching without summary overhead.

    **RECOMMENDED USAGE:**
    - Use THIS endpoint for browsing/paginating through requests
    - Use `/admin/monitoring/chat-requests/by-api-key/summary` for statistics

    **This endpoint allows administrators to:**
    - Browse all chat completion requests made with a specific API key
    - View detailed request information (tokens, timing, models, users)
    - Paginate through large result sets efficiently
    - Monitor API key activity and usage patterns

    **Query Parameters:**
    - `api_key`: Full API key string (exact match required) - e.g., "gw_live_abc123..."
    - `limit`: Maximum number of requests to return (1-1000, default: 100)
    - `offset`: Number of requests to skip for pagination (default: 0)
    - `include_summary`: ⚠️ DEPRECATED - Include summary statistics (use /summary endpoint instead)

    **Pagination Example:**
    ```
    # First 100 requests
    GET /admin/monitoring/chat-requests/by-api-key?api_key=gw_live_xxx&limit=100&offset=0

    # Next 100 requests (101-200)
    GET /admin/monitoring/chat-requests/by-api-key?api_key=gw_live_xxx&limit=100&offset=100

    # Next 100 requests (201-300)
    GET /admin/monitoring/chat-requests/by-api-key?api_key=gw_live_xxx&limit=100&offset=200
    ```

    **For Summary Statistics:**
    ```
    GET /admin/monitoring/chat-requests/by-api-key/summary?api_key=gw_live_xxx
    ```

    **Returns:**
    - `requests`: List of chat completion requests with model and user details
    - `total_count`: Total number of requests for this API key
    - `api_key_info`: Information about the API key
    - `pagination`: Pagination metadata
    - `summary`: (Optional, deprecated) Only included if include_summary=true
    - `api_key_info`: Information about the API key (id, name, user_id, etc.)
    - `pagination`: Pagination metadata (limit, offset, has_more, current_page, total_pages)

    **Response Format:**
    ```json
    {
      "requests": [...],
      "total_count": 523,
      "summary": {
        "total_requests": 523,
        "total_input_tokens": 125000,
        "total_output_tokens": 85000,
        "total_tokens": 210000,
        "avg_processing_time_ms": 1250.5,
        "completed_requests": 520,
        "failed_requests": 3
      },
      "api_key_info": {...},
      "limit": 100,
      "offset": 0,
      "pagination": {
        "limit": 100,
        "offset": 0,
        "has_more": true,
        "current_page": 1,
        "total_pages": 6
      }
    }
    ```
    """
    try:
        from src.db.api_keys import get_api_key_by_key

        # Look up API key by the actual key string
        api_key_data = get_api_key_by_key(api_key)

        if not api_key_data:
            raise HTTPException(
                status_code=404,
                detail=f"API key not found. Please verify the key is correct. Key preview: {api_key[:20]}...",
            )

        api_key_id = api_key_data.get("id")
        if not api_key_id:
            raise HTTPException(status_code=500, detail="API key found but missing ID field")

        # Fetch paginated chat completion requests
        result = get_chat_completion_requests_by_api_key(
            api_key_id=api_key_id, limit=limit, offset=offset
        )

        # Extract data from result
        requests = result.get("requests", [])
        total_count = result.get("total_count", 0)
        summary = result.get("summary", {})

        # Calculate pagination metadata
        has_more = (offset + limit) < total_count
        current_page = (offset // limit) + 1
        total_pages = (total_count + limit - 1) // limit if total_count > 0 else 0

        # Build API key info
        api_key_info = {
            "id": api_key_data.get("id"),
            "key_name": api_key_data.get("key_name"),
            "user_id": api_key_data.get("user_id"),
            "environment_tag": api_key_data.get("environment_tag"),
            "is_active": api_key_data.get("is_active", True),
            "created_at": api_key_data.get("created_at"),
        }

        # Build response (summary optional for backward compatibility)
        response = {
            "requests": requests,
            "total_count": total_count,
            "api_key_info": api_key_info,
            "limit": limit,
            "offset": offset,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "current_page": current_page,
                "total_pages": total_pages,
                "next_offset": offset + limit if has_more else None,
                "prev_offset": max(0, offset - limit) if offset > 0 else None,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Add summary only if requested (backward compatibility - DEPRECATED)
        if include_summary:
            logger.warning(
                f"include_summary parameter is deprecated for api_key_id={api_key_id}. "
                f"Use /admin/monitoring/chat-requests/by-api-key/summary endpoint instead for better performance."
            )
            response["summary"] = summary

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching chat completion requests by API key: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to fetch chat completion requests"
        ) from e


@router.get("/admin/monitoring/chat-requests/providers", tags=["admin", "monitoring"])
async def get_providers_with_requests_admin(admin_user: dict = Depends(require_admin)):
    """
    Get all providers that have models with chat completion requests (Admin only).

    Returns a list of providers that have at least one model with chat completion requests.
    Useful for building provider selection UI and analytics dashboards.

    Returns:
    - Provider information (id, name, slug)
    - Count of models with requests
    - Total requests across all models
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Try to use optimized RPC function first
        try:
            rpc_result = client.rpc("get_provider_request_stats").execute()
            if rpc_result.data:
                return {
                    "success": True,
                    "data": rpc_result.data,
                    "metadata": {
                        "total_providers": len(rpc_result.data),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "method": "rpc",
                    },
                }
        except Exception as rpc_error:
            logger.debug(f"RPC function not available, using fallback: {rpc_error}")

        # Fallback implementation
        distinct_result = (
            client.table("chat_completion_requests")
            .select("model_id, models!inner(provider_id, providers!inner(id, name, slug))")
            .execute()
        )

        if not distinct_result.data:
            return {
                "success": True,
                "data": [],
                "metadata": {
                    "total_providers": 0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }

        provider_stats = {}
        for record in distinct_result.data:
            model_id = record.get("model_id")
            provider_info = record.get("models", {}).get("providers", {})
            provider_id = provider_info.get("id")

            if provider_id and model_id:
                if provider_id not in provider_stats:
                    provider_stats[provider_id] = {
                        "provider_id": provider_id,
                        "name": provider_info.get("name"),
                        "slug": provider_info.get("slug"),
                        "model_ids": set(),
                    }
                provider_stats[provider_id]["model_ids"].add(model_id)

        providers_list = []
        for provider_id, stats in provider_stats.items():
            count_result = (
                client.table("chat_completion_requests")
                .select("id", count="exact", head=True)
                .in_("model_id", list(stats["model_ids"]))
                .execute()
            )

            providers_list.append(
                {
                    "provider_id": stats["provider_id"],
                    "name": stats["name"],
                    "slug": stats["slug"],
                    "models_with_requests": len(stats["model_ids"]),
                    "total_requests": count_result.count or 0,
                }
            )

        providers_list.sort(key=lambda x: x["total_requests"], reverse=True)
        return {
            "success": True,
            "data": providers_list,
            "metadata": {
                "total_providers": len(providers_list),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    except Exception as e:
        logger.error(f"Failed to get providers with requests: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get providers with requests: {str(e)}"
        )


@router.get("/admin/monitoring/chat-requests/counts", tags=["admin", "monitoring"])
async def get_request_counts_by_model_admin(admin_user: dict = Depends(require_admin)):
    """
    Get request counts for each model - lightweight endpoint (Admin only).

    Returns simple counts of requests per model, sorted by count (descending).
    This is lighter than /models when you only need counts.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        result = (
            client.table("chat_completion_requests")
            .select("model_id, models!inner(id, model_name, provider_model_id, providers!inner(name, slug))")
            .execute()
        )

        if not result.data:
            return {
                "success": True,
                "data": [],
                "metadata": {
                    "total_models": 0,
                    "total_requests": 0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }

        model_counts = {}
        for record in result.data:
            model_id = record.get("model_id")
            if model_id:
                model_info = record.get("models", {})
                if model_id not in model_counts:
                    model_counts[model_id] = {
                        "model_id": model_id,
                        "model_name": model_info.get("model_name"),
                        "model_identifier": model_info.get("model_id"),
                        "provider_name": model_info.get("providers", {}).get("name"),
                        "provider_slug": model_info.get("providers", {}).get("slug"),
                        "request_count": 0,
                    }
                model_counts[model_id]["request_count"] += 1

        counts_list = list(model_counts.values())
        counts_list.sort(key=lambda x: x["request_count"], reverse=True)

        return {
            "success": True,
            "data": counts_list,
            "metadata": {
                "total_models": len(counts_list),
                "total_requests": sum(m["request_count"] for m in counts_list),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    except Exception as e:
        logger.error(f"Failed to get request counts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get request counts: {str(e)}")


@router.get("/admin/monitoring/chat-requests/models", tags=["admin", "monitoring"])
async def get_models_with_requests_admin(
    provider_id: int | None = Query(None, description="Filter by provider ID"),
    admin_user: dict = Depends(require_admin),
):
    """
    Get all unique models that have chat completion requests (Admin only).

    Returns models with request statistics including token usage and performance metrics.
    Optionally filter by provider_id.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Try RPC first
        try:
            if provider_id is not None:
                rpc_result = client.rpc(
                    "get_models_with_requests_by_provider", {"p_provider_id": provider_id}
                ).execute()
            else:
                rpc_result = client.rpc("get_models_with_requests").execute()

            if rpc_result.data:
                return {
                    "success": True,
                    "data": rpc_result.data,
                    "metadata": {
                        "total_models": len(rpc_result.data),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "method": "rpc",
                    },
                }
        except Exception as rpc_error:
            logger.debug(f"RPC not available, using fallback: {rpc_error}")

        # Fallback implementation
        models_query = client.table("models").select(
            "id, model_id, model_name, provider_model_id, provider_id, providers!inner(id, name, slug)"
        )
        if provider_id is not None:
            models_query = models_query.eq("provider_id", provider_id)
        models_result = models_query.execute()

        if not models_result.data:
            return {
                "success": True,
                "data": [],
                "metadata": {
                    "total_models": 0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }

        models_data = []
        for model_info in models_result.data:
            model_id = model_info.get("id")
            if not model_id:
                continue

            try:
                # Try RPC for stats
                stats_rpc = client.rpc(
                    "get_model_request_stats", {"p_model_id": model_id}
                ).execute()
                if stats_rpc.data and len(stats_rpc.data) > 0:
                    stats_data = stats_rpc.data[0]
                    stats = {
                        "total_requests": int(stats_data.get("total_requests", 0)),
                        "total_input_tokens": int(stats_data.get("total_input_tokens", 0)),
                        "total_output_tokens": int(stats_data.get("total_output_tokens", 0)),
                        "total_tokens": int(stats_data.get("total_tokens", 0)),
                        "avg_processing_time_ms": round(
                            float(stats_data.get("avg_processing_time_ms", 0)), 2
                        ),
                    }
                else:
                    raise Exception("RPC returned no data")
            except Exception:
                # Fallback to count
                count_result = (
                    client.table("chat_completion_requests")
                    .select("id", count="exact", head=True)
                    .eq("model_id", model_id)
                    .execute()
                )
                total_requests = count_result.count or 0
                if total_requests == 0:
                    continue
                stats = {
                    "total_requests": total_requests,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_tokens": 0,
                    "avg_processing_time_ms": 0,
                }

            if stats["total_requests"] > 0:
                models_data.append(
                    {
                        "model_id": model_info["id"],
                        "model_identifier": model_info["model_id"],
                        "model_name": model_info["model_name"],
                        "provider_model_id": model_info["provider_model_id"],
                        "provider": model_info["providers"],
                        "stats": stats,
                    }
                )

        models_data.sort(key=lambda x: x["stats"]["total_requests"], reverse=True)
        return {
            "success": True,
            "data": models_data,
            "metadata": {
                "total_models": len(models_data),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    except Exception as e:
        logger.error(f"Failed to get models with requests: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get models with requests: {str(e)}")


@router.get("/admin/monitoring/chat-requests", tags=["admin", "monitoring"])
async def get_chat_completion_requests_admin(
    model_id: int | None = Query(None, description="Filter by model ID"),
    provider_id: int | None = Query(None, description="Filter by provider ID"),
    model_name: str | None = Query(None, description="Filter by model name (contains)"),
    start_date: str | None = Query(None, description="Filter by start date (ISO format)"),
    end_date: str | None = Query(None, description="Filter by end date (ISO format)"),
    limit: int = Query(100, ge=1, le=100000, description="Maximum records to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    admin_user: dict = Depends(require_admin),
):
    """
    Get chat completion requests with flexible filtering (Admin only).

    Allows fetching chat completion data for analytics with multiple filter options.
    Returns full request details including model, provider, tokens, and performance metrics.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        query = client.table("chat_completion_requests").select(
            "*, models!inner(id, model_id, model_name, provider_model_id, provider_id, providers!inner(id, name, slug))"
        )

        if model_id is not None:
            query = query.eq("model_id", model_id)
        if provider_id is not None:
            query = query.eq("models.provider_id", provider_id)
        if model_name is not None:
            query = query.ilike("models.model_name", f"%{model_name}%")
        if start_date is not None:
            query = query.gte("created_at", start_date)
        if end_date is not None:
            query = query.lte("created_at", end_date)

        query = query.order("created_at", desc=True).range(offset, offset + limit - 1)
        result = query.execute()

        # Get total count with all filters applied
        count_query = client.table("chat_completion_requests").select(
            "id, models!inner(id, model_id, model_name, provider_model_id, provider_id, providers!inner(id, name, slug))",
            count="exact",
            head=True,
        )
        if model_id is not None:
            count_query = count_query.eq("model_id", model_id)
        if provider_id is not None:
            count_query = count_query.eq("models.provider_id", provider_id)
        if model_name is not None:
            count_query = count_query.ilike("models.model_name", f"%{model_name}%")
        if start_date is not None:
            count_query = count_query.gte("created_at", start_date)
        if end_date is not None:
            count_query = count_query.lte("created_at", end_date)
        count_result = count_query.execute()
        total_count = count_result.count if count_result.count is not None else len(result.data)

        return {
            "success": True,
            "data": result.data or [],
            "metadata": {
                "total_count": total_count,
                "limit": limit,
                "offset": offset,
                "returned_count": len(result.data or []),
                "filters": {
                    "model_id": model_id,
                    "provider_id": provider_id,
                    "model_name": model_name,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }

    except Exception as e:
        logger.error(f"Failed to get chat completion requests: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get chat completion requests: {str(e)}"
        )


@router.get("/admin/monitoring/chat-requests/summary", tags=["admin", "monitoring"])
async def get_chat_requests_summary_admin(
    model_id: int | None = Query(None, description="Filter by model ID"),
    provider_id: int | None = Query(None, description="Filter by provider ID"),
    model_name: str | None = Query(None, description="Filter by model name (contains)"),
    start_date: str | None = Query(None, description="Filter by start date (ISO format)"),
    end_date: str | None = Query(None, description="Filter by end date (ISO format)"),
    admin_user: dict = Depends(require_admin),
):
    """
    Get aggregated summary statistics for chat completion requests (Admin only).

    **PURPOSE**: Analytics dashboard - shows aggregate metrics without fetching data.

    **OPTIMIZED**: Uses database-side aggregation with no data fetching required.

    **CACHED**: Results cached for 60 seconds in Redis for high performance.

    **Query Parameters:**
    - `model_id`: Filter by specific model ID
    - `provider_id`: Filter by provider ID
    - `model_name`: Filter by model name (partial match)
    - `start_date`: Filter by start date (ISO format: YYYY-MM-DDTHH:MM:SS)
    - `end_date`: Filter by end date (ISO format: YYYY-MM-DDTHH:MM:SS)

    **Returns:**
    ```json
    {
      "summary": {
        "total_requests": 50000,
        "total_input_tokens": 125000000,
        "total_output_tokens": 85000000,
        "total_tokens": 210000000,
        "avg_input_tokens": 2500.0,
        "avg_output_tokens": 1700.0,
        "avg_processing_time_ms": 1250.5,
        "completed_requests": 49800,
        "failed_requests": 200,
        "success_rate": 99.6,
        "first_request_at": "2025-01-01T00:00:00Z",
        "last_request_at": "2026-01-19T10:30:00Z",
        "total_cost_usd": 42.50
      },
      "filters": {
        "model_id": 4,
        "provider_id": null,
        "model_name": null,
        "start_date": "2026-01-12T00:00:00Z",
        "end_date": "2026-01-19T00:00:00Z"
      },
      "timestamp": "2026-01-19T13:00:00Z",
      "cached": false
    }
    ```

    **Performance:**
    - With database RPC: 30-50ms
    - With cache hit: 5-10ms

    **For browsing requests, use:** `/admin/monitoring/chat-requests?model_id=4&...`
    """
    try:
        from src.db.chat_completion_requests import get_chat_completion_summary_by_filters
        from src.config.redis_config import get_redis_client
        import json
        import hashlib

        # Create cache key based on filters
        filter_str = f"{model_id}:{provider_id}:{model_name}:{start_date}:{end_date}"
        filter_hash = hashlib.md5(filter_str.encode()).hexdigest()
        cache_key = f"chat_summary:filters:{filter_hash}"

        redis_client = get_redis_client()
        cached = False

        # Try to get from cache first
        if redis_client:
            try:
                cached_data = redis_client.get(cache_key)
                if cached_data:
                    logger.info(f"Cache HIT for chat summary: filter_hash={filter_hash}")
                    result = json.loads(cached_data)
                    result["cached"] = True
                    result["timestamp"] = datetime.now(timezone.utc).isoformat()
                    return result
            except Exception as cache_error:
                logger.warning(f"Redis cache read failed: {cache_error}")

        # Cache miss - fetch from database
        logger.info(f"Cache MISS for chat summary: filter_hash={filter_hash}, fetching from database")
        summary = get_chat_completion_summary_by_filters(
            model_id=model_id,
            provider_id=provider_id,
            model_name=model_name,
            start_date=start_date,
            end_date=end_date,
        )

        # Build response
        response = {
            "summary": summary,
            "filters": {
                "model_id": model_id,
                "provider_id": provider_id,
                "model_name": model_name,
                "start_date": start_date,
                "end_date": end_date,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cached": cached,
        }

        # Cache the result (60 second TTL)
        if redis_client:
            try:
                redis_client.setex(
                    cache_key,
                    60,  # 60 second TTL
                    json.dumps(response, default=str)
                )
                logger.info(f"Cached chat summary for filter_hash={filter_hash} (TTL: 60s)")
            except Exception as cache_error:
                logger.warning(f"Redis cache write failed: {cache_error}")

        return response

    except Exception as e:
        logger.error(f"Error fetching summary: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch chat request summary"
        ) from e


@router.get("/admin/monitoring/chat-requests/plot-data", tags=["admin", "monitoring"])
async def get_chat_requests_plot_data_admin(
    model_id: int | None = Query(None, description="Filter by model ID"),
    provider_id: int | None = Query(None, description="Filter by provider ID"),
    start_date: str | None = Query(None, description="Filter by start date (ISO format)"),
    end_date: str | None = Query(None, description="Filter by end date (ISO format)"),
    admin_user: dict = Depends(require_admin),
):
    """
    Get optimized chat completion request data for plotting graphs (Admin only).

    Returns:
    - recent_requests: Last 10 full requests for display
    - plot_data: ALL requests in compressed array format (tokens, latency, timestamps)

    Highly optimized for frontend plotting with minimal data transfer.
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Get last 10 full requests
        recent_query = client.table("chat_completion_requests").select(
            "id, request_id, model_id, input_tokens, output_tokens, processing_time_ms, status, error_message, created_at, models!inner(id, model_id, model_name, provider_model_id, providers!inner(id, name, slug))"
        )

        if model_id is not None:
            recent_query = recent_query.eq("model_id", model_id)
        if start_date is not None:
            recent_query = recent_query.gte("created_at", start_date)
        if end_date is not None:
            recent_query = recent_query.lte("created_at", end_date)

        recent_query = recent_query.order("created_at", desc=True).limit(10)
        recent_result = recent_query.execute()
        recent_requests = recent_result.data or []

        # Filter by provider if needed
        if provider_id is not None:
            recent_requests = [
                r
                for r in recent_requests
                if r.get("models", {}).get("providers", {}).get("id") == provider_id
            ]

        for req in recent_requests:
            req["total_tokens"] = req.get("input_tokens", 0) + req.get("output_tokens", 0)

        # Get ALL requests for plotting (lightweight - only 4 fields)
        plot_query = client.table("chat_completion_requests").select(
            "input_tokens, output_tokens, processing_time_ms, created_at"
        )

        if model_id is not None:
            plot_query = plot_query.eq("model_id", model_id)
        if start_date is not None:
            plot_query = plot_query.gte("created_at", start_date)
        if end_date is not None:
            plot_query = plot_query.lte("created_at", end_date)

        plot_query = plot_query.order("created_at", desc=False)
        plot_result = plot_query.execute()
        all_requests = plot_result.data or []

        # Compress into arrays for efficient transfer
        tokens_array = []
        latency_array = []
        timestamps_array = []

        for req in all_requests:
            total_tokens = req.get("input_tokens", 0) + req.get("output_tokens", 0)
            tokens_array.append(total_tokens)
            latency_array.append(req.get("processing_time_ms", 0))
            timestamps_array.append(req.get("created_at"))

        return {
            "success": True,
            "recent_requests": recent_requests[:10],
            "plot_data": {
                "tokens": tokens_array,
                "latency": latency_array,
                "timestamps": timestamps_array,
            },
            "metadata": {
                "recent_count": len(recent_requests[:10]),
                "total_count": len(all_requests),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "compression": "arrays",
                "format_version": "1.0",
            },
        }

    except Exception as e:
        logger.error(f"Failed to get plot data: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get plot data: {str(e)}")


@router.get("/admin/model-usage-analytics", tags=["admin"])
async def get_model_usage_analytics(
    admin_user: dict = Depends(require_admin),
    page: int = Query(1, ge=1, description="Page number (starts at 1)"),
    limit: int = Query(50, ge=1, le=500, description="Items per page (max 500)"),
    model_name: str = Query(None, description="Search by model name (partial match)"),
    sort_by: str = Query(
        "total_cost_usd", description="Sort by field (total_cost_usd, successful_requests, etc.)"
    ),
    sort_order: str = Query("desc", description="Sort order (asc or desc)"),
):
    """
    Admin endpoint to get model usage analytics with pagination and search.

    Returns data from the model_usage_analytics view including:
    - Model identification (name, provider, etc.)
    - Request counts
    - Token usage (input/output totals and averages)
    - Pricing per token
    - Cost calculations (total, input, output, per-request average)
    - Performance metrics
    - Model metadata

    Supports:
    - Pagination: ?page=1&limit=50
    - Search: ?model_name=gpt (partial match, case-insensitive)
    - Sorting: ?sort_by=total_cost_usd&sort_order=desc
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Calculate offset for pagination
        offset = (page - 1) * limit

        # Build the query
        query = client.table("model_usage_analytics").select("*", count="exact")

        # Apply search filter if provided
        if model_name:
            # Use ilike for case-insensitive partial match
            query = query.ilike("model_name", f"%{model_name}%")

        # Validate sort_by field (prevent SQL injection)
        valid_sort_fields = [
            "model_name",
            "provider_name",
            "successful_requests",
            "total_cost_usd",
            "avg_cost_per_request_usd",
            "total_input_tokens",
            "total_output_tokens",
            "total_tokens",
            "avg_processing_time_ms",
            "first_request_at",
            "last_request_at",
        ]
        if sort_by not in valid_sort_fields:
            sort_by = "total_cost_usd"

        # Validate sort order
        if sort_order.lower() not in ["asc", "desc"]:
            sort_order = "desc"

        # Apply sorting
        query = query.order(sort_by, desc=(sort_order.lower() == "desc"))

        # Apply pagination
        query = query.range(offset, offset + limit - 1)

        # Execute query
        result = query.execute()

        # Get total count from the query
        total_count = result.count if result.count is not None else 0

        # Calculate pagination metadata
        total_pages = (total_count + limit - 1) // limit if total_count > 0 else 0
        has_next = page < total_pages
        has_prev = page > 1

        return {
            "success": True,
            "data": result.data or [],
            "pagination": {
                "page": page,
                "limit": limit,
                "total_items": total_count,
                "total_pages": total_pages,
                "has_next": has_next,
                "has_prev": has_prev,
                "offset": offset,
            },
            "filters": {"model_name": model_name, "sort_by": sort_by, "sort_order": sort_order},
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "items_in_page": len(result.data or []),
            },
        }

    except Exception as e:
        logger.error(f"Failed to get model usage analytics: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get model usage analytics: {str(e)}"
        )


# ============================================================================
# ADMIN PRICING SCHEDULER - Automated Pricing Sync (Phase 2.5/Phase 3)
# ============================================================================
# Endpoints to monitor and control the automated pricing sync scheduler


# ============================================================================
# Pricing Sync Scheduler Endpoints - DEPRECATED (Phase 2)
# ============================================================================
# These endpoints were removed as part of the pricing sync deprecation (Issue #1062).
# Pricing is now updated automatically through model sync.
# - /admin/pricing/scheduler/status - REMOVED
# - /admin/pricing/scheduler/trigger - REMOVED
# - /admin/pricing/sync/{sync_id} - REMOVED
# - /admin/pricing/syncs/active - REMOVED
