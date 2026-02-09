"""
Partner Trial API Endpoints

These endpoints manage partner-specific trial activations and status checks.
Partner trials (like Redbeard) offer extended trial periods with enhanced benefits.
"""

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.security.deps import get_api_key, get_user_id as get_current_user_id
from src.services.partner_trial_service import PartnerTrialService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/partner-trials", tags=["Partner Trials"])


# ==================== Request/Response Models ====================


class StartPartnerTrialRequest(BaseModel):
    partner_code: str = Field(..., description="Partner code (e.g., REDBEARD)")
    signup_source: str | None = Field(None, description="UTM or referral source")


class PartnerTrialResponse(BaseModel):
    success: bool
    partner_code: str
    partner_name: str | None = None
    trial_tier: str
    trial_credits_usd: float
    trial_duration_days: int
    trial_expires_at: str
    daily_usage_limit_usd: float


class PartnerTrialStatusResponse(BaseModel):
    has_partner_trial: bool
    partner_code: str | None = None
    trial_status: str | None = None
    is_expired: bool | None = None
    days_remaining: int | None = None
    trial_started_at: str | None = None
    trial_expires_at: str | None = None
    credits_used: float | None = None
    tokens_used: int | None = None
    requests_made: int | None = None
    converted: bool | None = None


class PartnerConfigResponse(BaseModel):
    partner_code: str
    partner_name: str
    trial_duration_days: int
    trial_tier: str
    trial_credits_usd: float
    daily_usage_limit_usd: float


class PartnerAnalyticsResponse(BaseModel):
    partner_code: str
    total_trials: int
    active_trials: int
    converted_trials: int
    expired_trials: int
    conversion_rate_percent: float
    total_revenue_usd: float
    avg_revenue_per_conversion: float


# ==================== Public Endpoints ====================


@router.get("/config/{partner_code}", response_model=PartnerConfigResponse)
async def get_partner_config(partner_code: str):
    """
    Get public configuration for a partner trial.

    Used by frontend to display trial benefits on landing pages.
    This endpoint is public and does not require authentication.
    """
    config = PartnerTrialService.get_partner_config(partner_code)

    if not config:
        raise HTTPException(status_code=404, detail="Partner not found or inactive")

    return PartnerConfigResponse(
        partner_code=config["partner_code"],
        partner_name=config["partner_name"],
        trial_duration_days=config["trial_duration_days"],
        trial_tier=config["trial_tier"],
        trial_credits_usd=float(config["trial_credits_usd"]),
        daily_usage_limit_usd=float(config["daily_usage_limit_usd"]),
    )


@router.get("/check/{code}")
async def check_partner_code(code: str):
    """
    Check if a code is a valid partner code.

    Returns whether the code is a partner code (vs a user referral code).
    This endpoint is public and does not require authentication.
    """
    is_partner = PartnerTrialService.is_partner_code(code)
    config = PartnerTrialService.get_partner_config(code) if is_partner else None

    return {
        "code": code.upper(),
        "is_partner_code": is_partner,
        "partner_name": config["partner_name"] if config else None,
        "trial_duration_days": config["trial_duration_days"] if config else None,
        "trial_tier": config["trial_tier"] if config else None,
    }


# ==================== Authenticated Endpoints ====================


@router.post("/start", response_model=PartnerTrialResponse)
async def start_partner_trial(
    request: StartPartnerTrialRequest,
    api_key: str = Depends(get_api_key),
    user_id: int = Depends(get_current_user_id),
):
    """
    Start a partner trial for the current user.

    This endpoint is called when a user signs up through a partner landing page
    (e.g., /redbeard) and needs to activate their extended trial.

    Requires authentication.
    """
    try:
        result = PartnerTrialService.start_partner_trial(
            user_id=user_id,
            api_key=api_key,
            partner_code=request.partner_code,
            signup_source=request.signup_source,
        )

        return PartnerTrialResponse(
            success=result["success"],
            partner_code=result["partner_code"],
            partner_name=result.get("partner_name"),
            trial_tier=result["trial_tier"],
            trial_credits_usd=result["trial_credits_usd"],
            trial_duration_days=result["trial_duration_days"],
            trial_expires_at=result["trial_expires_at"],
            daily_usage_limit_usd=result["daily_usage_limit_usd"],
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting partner trial: {e}")
        raise HTTPException(status_code=500, detail="Failed to start partner trial")


@router.get("/status", response_model=PartnerTrialStatusResponse)
async def get_partner_trial_status(
    user_id: int = Depends(get_current_user_id),
):
    """
    Get the current user's partner trial status.

    Returns information about the user's active or expired partner trial,
    including remaining days, credits used, and conversion status.

    Requires authentication.
    """
    status = PartnerTrialService.get_partner_trial_status(user_id)
    return PartnerTrialStatusResponse(**status)


@router.get("/daily-limit")
async def get_daily_limit(
    user_id: int = Depends(get_current_user_id),
):
    """
    Get the daily usage limit for the current user.

    Partner trial users may have different daily limits than standard users.

    Requires authentication.
    """
    limit = PartnerTrialService.get_user_daily_limit(user_id)

    return {
        "user_id": user_id,
        "daily_limit_usd": limit if limit != float("inf") else None,
        "unlimited": limit == float("inf"),
    }


# ==================== Admin Endpoints ====================


@router.get("/analytics/{partner_code}", response_model=PartnerAnalyticsResponse)
async def get_partner_analytics(
    partner_code: str,
    start_date: datetime | None = Query(None, description="Filter start date"),
    end_date: datetime | None = Query(None, description="Filter end date"),
    api_key: str = Depends(get_api_key),
):
    """
    Get analytics for a specific partner's trials.

    Returns conversion rates, revenue, and trial status breakdown.

    Requires admin privileges (TODO: add admin check).
    """
    # TODO: Add admin role check
    # For now, any authenticated user can access analytics

    analytics = PartnerTrialService.get_partner_analytics(
        partner_code=partner_code,
        start_date=start_date,
        end_date=end_date,
    )

    if "error" in analytics:
        raise HTTPException(status_code=500, detail=analytics["error"])

    return PartnerAnalyticsResponse(**analytics)


@router.post("/expire/{target_user_id}")
async def force_expire_trial(
    target_user_id: int,
    api_key: str = Depends(get_api_key),
):
    """
    Force expire a user's partner trial.

    This is an admin endpoint for manually expiring trials.

    Requires admin privileges (TODO: add admin check).
    """
    # TODO: Add admin role check

    result = PartnerTrialService.expire_partner_trial(target_user_id)

    if not result.get("success"):
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to expire trial"))

    return result
