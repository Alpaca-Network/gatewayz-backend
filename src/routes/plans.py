import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from src.db.plans import (
    assign_user_plan,
    check_plan_entitlements,
    get_all_plans,
    get_plan_by_id,
    get_user_plan,
    get_user_usage_within_plan_limits,
)
from src.db.rate_limits import get_environment_usage_summary
from src.schemas import (
    AssignPlanRequest,
    PlanEntitlementsResponse,
    PlanResponse,
    PlanUsageResponse,
    UserPlanResponse,
)
from src.security.deps import get_api_key, require_admin
from src.services.user_lookup_cache import get_user

# Initialize logging
logger = logging.getLogger(__name__)

router = APIRouter()


# Plan Management Endpoints
@router.get("/plans", response_model=list[PlanResponse], tags=["plans"])
async def get_plans():
    """Get all available subscription plans"""
    try:
        logger.info("Attempting to get all plans...")
        plans = get_all_plans()
        logger.info(f"Successfully retrieved {len(plans) if plans else 0} plans")

        if not plans:
            logger.warning("No plans found in database")
            return []

        # Convert to PlanResponse format. PlanResponse normalizes NULL columns
        # (see src/schemas/plans.py), so a null-heavy row degrades to defaults
        # rather than failing response serialization with a 500.
        plan_responses: list[PlanResponse] = []
        for plan in plans:
            try:
                plan_responses.append(PlanResponse.model_validate(plan))
            except Exception as plan_error:
                logger.error(f"Error processing plan {plan.get('id', 'unknown')}: {plan_error}")
                continue

        # Sort plans by type (Free, Dev, Team, Customize); unknown types sort last.
        plan_order = {"free": 0, "dev": 1, "team": 2, "customize": 3}
        plan_responses.sort(key=lambda p: plan_order.get(p.plan_type, 999))

        logger.info(f"Returning {len(plan_responses)} plan responses")
        return plan_responses

    except Exception as e:
        logger.error(f"Error getting plans: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") from e


@router.get("/plans/{plan_id}", response_model=PlanResponse, tags=["plans"])
async def get_plan(plan_id: int):
    """Get a specific plan by ID"""
    try:
        plan = get_plan_by_id(plan_id)
        if not plan:
            raise HTTPException(status_code=404, detail="Plan not found")
        # Same NULL-column normalization as GET /plans.
        return PlanResponse.model_validate(plan)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting plan {plan_id}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/user/plan", response_model=UserPlanResponse, tags=["authentication"])
async def get_user_plan_endpoint(api_key: str = Depends(get_api_key)):
    """Get current user's plan"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        user_plan = get_user_plan(user["id"])
        if not user_plan:
            raise HTTPException(status_code=404, detail="No active plan found")

        return user_plan

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user plan: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/user/plan/usage", response_model=PlanUsageResponse, tags=["authentication"])
async def get_user_plan_usage(api_key: str = Depends(get_api_key)):
    """Get user's plan usage and limits"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        usage_data = get_user_usage_within_plan_limits(user["id"])
        if not usage_data:
            raise HTTPException(status_code=500, detail="Failed to retrieve usage data")

        return usage_data

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user plan usage: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get(
    "/user/plan/entitlements", response_model=PlanEntitlementsResponse, tags=["authentication"]
)
async def get_user_plan_entitlements(
    api_key: str = Depends(get_api_key), feature: str | None = Query(None)
):
    """Check user's plan entitlements"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        entitlements = check_plan_entitlements(user["id"], feature)
        return entitlements

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking user plan entitlements: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.post("/admin/assign-plan", tags=["admin"])
async def assign_plan_to_user(
    request: AssignPlanRequest, admin_user: dict = Depends(require_admin)
):
    """Assign a plan to a user (Admin only)"""
    try:
        success = assign_user_plan(request.user_id, request.plan_id, request.duration_months)

        if not success:
            raise HTTPException(status_code=500, detail="Failed to assign plan")

        return {
            "status": "success",
            "message": f"Plan {request.plan_id} assigned to user {request.user_id} for {request.duration_months} months",
            "user_id": request.user_id,
            "plan_id": request.plan_id,
            "duration_months": request.duration_months,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Error assigning plan: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/user/environment-usage", tags=["authentication"])
async def get_user_environment_usage(api_key: str = Depends(get_api_key)):
    """Get user's usage breakdown by environment"""
    try:
        user = get_user(api_key)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid API key")

        env_usage = get_environment_usage_summary(user["id"])

        return {
            "status": "success",
            "user_id": user["id"],
            "environment_usage": env_usage,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting environment usage: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


# Trial Status Endpoint (Simplified)


@router.get("/trial/status", tags=["trial"])
async def get_trial_status(api_key: str = Depends(get_api_key)):
    """Get the current trial status for the authenticated API key"""
    try:
        from src.services.trial_validation import validate_trial_access

        trial_status = validate_trial_access(api_key)

        return {
            "success": True,
            "trial_status": trial_status,
            "message": "Trial status retrieved successfully",
        }
    except Exception as e:
        logger.error(f"Error getting trial status: {e}")
        raise HTTPException(status_code=500, detail="Internal server error") from e


@router.get("/subscription/plans", tags=["subscription"])
async def get_subscription_plans():
    """Subscriptions have been discontinued; this endpoint is disabled."""
    raise HTTPException(
        status_code=410,
        detail="Subscriptions have been discontinued. Please use credit top-ups instead.",
    )
