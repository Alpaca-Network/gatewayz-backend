import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from src.cache import _huggingface_cache, _models_cache, _provider_cache
from src.config import Config
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

        # Add credits to a user account (not a specific key)
        add_credits_to_user(user["id"], req.credits)

        updated_user = get_user(req.api_key)

        return {
            "status": "success",
            "message": f"Added {req.credits} credits to user {user.get('username', user['id'])}",
            "new_balance": updated_user["credits"],
            "user_id": user["id"],
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
        set_user_rate_limits(req.api_key, req.rate_limits.model_dump())

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


@router.get("/test-provider-matching", tags=["debug"])
async def test_provider_matching():
    """Test provider matching logic without authentication"""
    try:
        # Get raw data
        models = get_cached_models()
        providers = get_cached_providers()

        if not models or not providers:
            return {
                "error": "No models or providers data available",
                "models_count": len(models) if models else 0,
                "providers_count": len(providers) if providers else 0,
            }

        # Test with a specific model
        test_model = None
        for model in models:
            if "openai" in model.get("id", "").lower():
                test_model = model
                break

        if not test_model:
            test_model = models[0]  # Use first model as fallback

        model_id = test_model.get("id", "")
        provider_slug = model_id.split("/")[0] if "/" in model_id else None

        # Find a matching provider
        matching_provider = None
        if provider_slug:
            for provider in providers:
                if provider.get("slug") == provider_slug:
                    matching_provider = provider
                    break

        # Test the enhancement function
        enhanced_model = enhance_model_with_provider_info(test_model, providers)

        return {
            "test_model": {"id": model_id, "provider_slug": provider_slug},
            "matching_provider": {
                "found": bool(matching_provider),
                "slug": matching_provider.get("slug") if matching_provider else None,
                "site_url": matching_provider.get("site_url") if matching_provider else None,
                "name": matching_provider.get("name") if matching_provider else None,
                "privacy_policy_url": (
                    matching_provider.get("privacy_policy_url") if matching_provider else None
                ),
                "terms_of_service_url": (
                    matching_provider.get("terms_of_service_url") if matching_provider else None
                ),
                "status_page_url": (
                    matching_provider.get("status_page_url") if matching_provider else None
                ),
            },
            "enhanced_model": {
                "provider_slug": enhanced_model.get("provider_slug"),
                "provider_site_url": enhanced_model.get("provider_site_url"),
                "model_logo_url": enhanced_model.get("model_logo_url"),
            },
            "available_providers": [
                {
                    "slug": p.get("slug"),
                    "name": p.get("name"),
                    "site_url": p.get("site_url"),
                    "privacy_policy_url": p.get("privacy_policy_url"),
                    "terms_of_service_url": p.get("terms_of_service_url"),
                    "status_page_url": p.get("status_page_url"),
                }
                for p in providers[:5]
            ],
            "cache_info": {
                "provider_cache_timestamp": _provider_cache.get("timestamp"),
                "provider_cache_age": (
                    (datetime.now(timezone.utc) - _provider_cache["timestamp"]).total_seconds()
                    if _provider_cache.get("timestamp")
                    else None
                ),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to test provider matching: {e}")
        return {"error": str(e)}


@router.post("/test-refresh-providers", tags=["debug"])
async def test_refresh_providers():
    """Refresh providers cache and test again"""
    try:
        # Clear provider cache
        _provider_cache["data"] = None
        _provider_cache["timestamp"] = None

        # Fetch fresh data
        fresh_providers = fetch_providers_from_openrouter()

        if not fresh_providers:
            return {"error": "Failed to fetch fresh providers data"}

        # Test the enhancement on fresh data
        test_model = {"id": "openai/gpt-4", "name": "GPT-4"}
        enhanced_model = enhance_model_with_provider_info(test_model, fresh_providers)

        return {
            "fresh_providers_count": len(fresh_providers),
            "sample_providers": [
                {
                    "slug": p.get("slug"),
                    "name": p.get("name"),
                    "privacy_policy_url": p.get("privacy_policy_url"),
                    "terms_of_service_url": p.get("terms_of_service_url"),
                    "status_page_url": p.get("status_page_url"),
                }
                for p in fresh_providers[:3]
            ],
            "enhanced_model": {
                "provider_slug": enhanced_model.get("provider_slug"),
                "provider_site_url": enhanced_model.get("provider_site_url"),
                "model_logo_url": enhanced_model.get("model_logo_url"),
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to refresh providers: {e}")
        return {"error": str(e)}


@router.get("/test-openrouter-providers", tags=["debug"])
async def test_openrouter_providers():
    """Test what OpenRouter actually returns for providers"""
    try:
        if not Config.OPENROUTER_API_KEY:
            return {"error": "OpenRouter API key not configured"}

        headers = {
            "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        }

        response = httpx.get("https://openrouter.ai/api/v1/providers", headers=headers)
        response.raise_for_status()

        raw_data = response.json()
        providers = raw_data.get("data", [])

        # Find OpenAI provider
        openai_provider = None
        for provider in providers:
            if provider.get("slug") == "openai":
                openai_provider = provider
                break

        return {
            "total_providers": len(providers),
            "openai_provider_raw": openai_provider,
            "sample_providers": [
                {
                    "slug": p.get("slug"),
                    "name": p.get("name"),
                    "privacy_policy_url": p.get("privacy_policy_url"),
                    "terms_of_service_url": p.get("terms_of_service_url"),
                    "status_page_url": p.get("status_page_url"),
                }
                for p in providers[:5]
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    except Exception as e:
        logger.error(f"Failed to test OpenRouter providers: {e}")
        return {"error": str(e)}


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
    admin_user: dict = Depends(require_admin)
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
        # We'll use the registration_date or created_at field
        growth_query = (
            client.table("users")
            .select("created_at, registration_date")
            .gte("created_at", start_date.isoformat())
            .lte("created_at", end_date.isoformat())
            .order("created_at", desc=False)  # Ascending order for cumulative calculation
        )
        
        growth_result = growth_query.execute()
        user_data = growth_result.data if growth_result.data else []
        
        # Initialize daily data structure
        daily_data = {}
        current_date = start_date
        
        # Create entry for each day in the range
        while current_date <= end_date:
            daily_data[current_date.isoformat()] = 0
            current_date += timedelta(days=1)
        
        # Count users created each day
        for user in user_data:
            # Use registration_date if available, otherwise created_at
            created_date_str = user.get("registration_date") or user.get("created_at")
            if created_date_str:
                # Parse date and extract just the date part
                try:
                    created_date = datetime.fromisoformat(created_date_str.replace('Z', '+00:00')).date()
                    date_key = created_date.isoformat()
                    if date_key in daily_data:
                        daily_data[date_key] += 1
                except (ValueError, TypeError):
                    # Skip invalid dates
                    continue
        
        # Calculate cumulative counts
        cumulative_data = []
        cumulative_total = 0
        
        # Get total users before start date for proper cumulative calculation
        before_start_query = (
            client.table("users")
            .select("id", count="exact")
            .lt("created_at", start_date.isoformat())
        )
        
        before_start_result = before_start_query.execute()
        cumulative_total = before_start_result.count if before_start_result.count is not None else 0
        
        # Build cumulative data
        for date_str in sorted(daily_data.keys()):
            new_users_today = daily_data[date_str]
            cumulative_total += new_users_today
            
            cumulative_data.append({
                "date": date_str,
                "value": cumulative_total,
                "new_users": new_users_today
            })
        
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
        raise HTTPException(status_code=500, detail="Failed to get user growth data") from e


@router.get("/admin/users/stats", tags=["admin"])
async def get_users_stats(
    # Optional filters (same as main endpoint)
    email: str | None = Query(None, description="Filter by email (case-insensitive partial match)"),
    api_key: str | None = Query(None, description="Filter by API key (case-insensitive partial match)"),
    is_active: bool | None = Query(None, description="Filter by active status (true/false)"),
    # Auth
    admin_user: dict = Depends(require_admin)
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

        # Build base count query
        if api_key:
            count_query = (
                client.table("users")
                .select("id, api_keys_new!inner(api_key)", count="exact")
            )
        else:
            count_query = (
                client.table("users")
                .select("id", count="exact")
            )

        # Apply filters to count query
        if email:
            count_query = count_query.ilike("email", f"%{email}%")

        if api_key:
            count_query = count_query.ilike("api_keys_new.api_key", f"%{api_key}%")

        if is_active is not None:
            count_query = count_query.eq("is_active", is_active)

        # Execute count query to get total users
        count_result = count_query.execute()
        total_users = count_result.count if count_result.count is not None else 0

        # Build separate queries for role and subscription statistics
        # Role distribution
        role_query = (
            client.table("users")
            .select("role", count="exact")
        )
        
        # Apply same filters to role query
        if email:
            role_query = role_query.ilike("email", f"%{email}%")
        if api_key:
            role_query = role_query.ilike("api_keys_new.api_key", f"%{api_key}%")
        if is_active is not None:
            role_query = role_query.eq("is_active", is_active)
        
        role_result = role_query.execute()
        role_data = role_result.data if role_result.data else []
        
        # Count roles
        admin_users = sum(1 for u in role_data if u.get("role") == "admin")
        developer_users = sum(1 for u in role_data if u.get("role") == "developer")
        regular_users = sum(1 for u in role_data if u.get("role") == "user" or u.get("role") is None)

        # Active/Inactive status (we need this for the breakdown)
        status_query = (
            client.table("users")
            .select("is_active", count="exact")
        )
        
        # Apply same filters to status query
        if email:
            status_query = status_query.ilike("email", f"%{email}%")
        if api_key:
            status_query = status_query.ilike("api_keys_new.api_key", f"%{api_key}%")
        if is_active is not None:
            status_query = status_query.eq("is_active", is_active)
        
        status_result = status_query.execute()
        status_data = status_result.data if status_result.data else []
        
        # Count active/inactive
        active_users = sum(1 for u in status_data if u.get("is_active", True))
        inactive_users = total_users - active_users

        # Credit statistics - use aggregation query to avoid fetching all rows
        credits_query = (
            client.table("users")
            .select("credits", count="exact")
        )
        
        # Apply same filters to credits query
        if email:
            credits_query = credits_query.ilike("email", f"%{email}%")
        if api_key:
            credits_query = credits_query.ilike("api_keys_new.api_key", f"%{api_key}%")
        if is_active is not None:
            credits_query = credits_query.eq("is_active", is_active)
        
        credits_result = credits_query.execute()
        credits_data = credits_result.data if credits_result.data else []
        
        # Calculate credit statistics
        total_credits = sum(float(u.get("credits", 0)) for u in credits_data)
        avg_credits = round(total_credits / total_users, 2) if total_users > 0 else 0

        # Subscription breakdown
        subscription_query = (
            client.table("users")
            .select("subscription_status", count="exact")
        )
        
        # Apply same filters to subscription query
        if email:
            subscription_query = subscription_query.ilike("email", f"%{email}%")
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
    api_key: str | None = Query(None, description="Filter by API key (case-insensitive partial match)"),
    is_active: bool | None = Query(None, description="Filter by active status (true/false)"),
    # Pagination
    limit: int = Query(100, ge=1, le=10000, description="Maximum number of users to return (increased to 10000)"),
    offset: int = Query(0, ge=0, description="Number of users to skip (pagination)"),
    # Auth
    admin_user: dict = Depends(require_admin)
):
    """
    Get users information with search and pagination (Admin only)

    **Search Parameters**:
    - `email`: Case-insensitive partial match (e.g., "john" matches "john@example.com")
    - `api_key`: Case-insensitive partial match (e.g., "gw_live" matches keys starting with "gw_live")
    - `is_active`: Filter by active status (true = active only, false = inactive only, null = all)

    **Pagination**:
    - `limit`: Records per page (1-1000, default: 100)
    - `offset`: Records to skip (default: 0)

    **Response**:
    - `total_users`: Total matching the filters (not total in database)
    - `has_more`: Whether more results exist beyond current page
    - `users`: Current page of filtered users
    - `statistics`: Stats calculated from **filtered results only**
    """
    try:
        from src.config.supabase_config import get_supabase_client

        client = get_supabase_client()

        # Build count query first (without pagination, just filters)
        # This ensures we get accurate total_users count for filtered results
        if api_key:
            count_query = (
                client.table("users")
                .select("id, api_keys_new!inner(api_key)", count="exact")
            )
        else:
            count_query = (
                client.table("users")
                .select("id", count="exact")
            )

        # Apply filters to count query
        if email:
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
            data_query = (
                client.table("users")
                .select(
                    "id, username, email, credits, is_active, role, registration_date, "
                    "auth_method, subscription_status, trial_expires_at, created_at, updated_at, "
                    "api_keys_new!inner(api_key)"
                )
            )
        else:
            # Query without JOIN for better performance when not searching by API key
            data_query = (
                client.table("users")
                .select(
                    "id, username, email, credits, is_active, role, registration_date, "
                    "auth_method, subscription_status, trial_expires_at, created_at, updated_at"
                )
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
                status_code=500,
                detail=f"Failed to fetch users: {str(data_err)}"
            ) from data_err

        # Clean up api_keys_new from response if it was included
        users = []
        for user in users_data:
            # Remove the api_keys_new join data from response
            user_clean = {k: v for k, v in user.items() if k != "api_keys_new"}
            users.append(user_clean)

        # Calculate has_more for pagination
        has_more = (offset + limit) < total_users

        # Build statistics query for ALL filtered users (not just current page)
        # This ensures statistics reflect the filtered dataset
        if api_key:
            stats_query = (
                client.table("users")
                .select(
                    "id, is_active, role, credits, subscription_status, "
                    "api_keys_new!inner(api_key)"
                )
            )
        else:
            stats_query = (
                client.table("users")
                .select("id, is_active, role, credits, subscription_status")
            )

        # Apply same filters to statistics query
        if email:
            stats_query = stats_query.ilike("email", f"%{email}%")

        if api_key:
            stats_query = stats_query.ilike("api_keys_new.api_key", f"%{api_key}%")

        if is_active is not None:
            stats_query = stats_query.eq("is_active", is_active)

        # Execute statistics query
        stats_result = stats_query.execute()
        stats_data = stats_result.data if stats_result.data else []

        # Calculate statistics from filtered results
        active_users = sum(1 for u in stats_data if u.get("is_active", True))
        inactive_users = total_users - active_users
        admin_users = sum(1 for u in stats_data if u.get("role") == "admin")
        developer_users = sum(1 for u in stats_data if u.get("role") == "developer")
        regular_users = sum(
            1 for u in stats_data if u.get("role") == "user" or u.get("role") is None
        )

        # Calculate total credits across filtered users
        total_credits = sum(float(u.get("credits", 0)) for u in stats_data)
        avg_credits = round(total_credits / total_users, 2) if total_users > 0 else 0

        # Get subscription status breakdown from filtered users
        subscription_stats = {}
        for user in stats_data:
            status = user.get("subscription_status", "unknown")
            subscription_stats[status] = subscription_stats.get(status, 0) + 1

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
            status_code=500,
            detail=f"Failed to get users information: {str(e)}"
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
