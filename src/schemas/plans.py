from datetime import datetime

from pydantic import BaseModel, model_validator

from src.schemas.common import SubscriptionStatus

# Almost every column on the `plans` table is nullable in Postgres, and rows
# predating a column's introduction hold SQL NULL. A pydantic default only
# applies when a key is absent -- an explicit None still fails validation -- so
# a NULL column would 500 the public `/plans` endpoints. Coerce NULL to the
# column's database default at the boundary instead of widening the public
# response contract to `| None`, which would push the problem onto every client.
_PLAN_RESPONSE_NULL_DEFAULTS: dict[str, object] = {
    "description": "",
    "plan_type": "free",
    "daily_request_limit": 1000,
    "monthly_request_limit": 30000,
    "daily_token_limit": 100000,
    "monthly_token_limit": 3000000,
    "price_per_month": 0.0,
    "is_pay_as_you_go": False,
    "max_concurrent_requests": 5,
    "is_active": True,
}


class PlanResponse(BaseModel):
    id: int
    name: str
    description: str
    plan_type: str = "free"
    daily_request_limit: int
    monthly_request_limit: int
    daily_token_limit: int
    monthly_token_limit: int
    price_per_month: float
    yearly_price: float | None = None
    price_per_token: float | None = None
    is_pay_as_you_go: bool = False
    max_concurrent_requests: int = 5
    features: list[str]
    is_active: bool

    @model_validator(mode="before")
    @classmethod
    def _coerce_nullable_columns(cls, data: object) -> object:
        """Normalize a raw `plans` row so NULL columns cannot 500 the endpoint."""
        if not isinstance(data, dict):
            return data

        coerced = dict(data)
        for field, default in _PLAN_RESPONSE_NULL_DEFAULTS.items():
            if coerced.get(field) is None:
                coerced[field] = default

        # `features` is jsonb: historically a list, but some rows hold an object.
        features = coerced.get("features")
        if isinstance(features, dict):
            coerced["features"] = list(features.keys())
        elif not isinstance(features, list):
            coerced["features"] = []

        return coerced


# NOTE: `SubscriptionPlan` and `SubscriptionPlansResponse` used to live here.
# They were the response models for GET /subscription/plans, which has returned
# HTTP 410 since #2180 moved billing to credits-only, and they had no other
# caller anywhere in the tree. They carried the only reference to the
# `PlanType` enum, whose vocabulary (free/dev/team/customize) never matched the
# production `plans.plan_type` column; both were deleted together rather than
# keeping a second, contradictory plan vocabulary alive in dead code.


class SubscriptionHistory(BaseModel):
    """Subscription history model"""

    id: int | None = None
    api_key_id: int
    plan_name: str
    status: SubscriptionStatus
    start_date: datetime
    end_date: datetime | None = None
    price_paid: float = 0.0
    payment_method: str | None = None
    created_at: datetime | None = None


class UserPlanResponse(BaseModel):
    user_plan_id: int
    user_id: int
    plan_id: int
    plan_name: str
    plan_description: str
    daily_request_limit: int
    monthly_request_limit: int
    daily_token_limit: int
    monthly_token_limit: int
    price_per_month: float
    features: list[str]
    start_date: str
    end_date: str
    is_active: bool


class AssignPlanRequest(BaseModel):
    user_id: int
    plan_id: int
    duration_months: int = 1


class PlanUsageResponse(BaseModel):
    plan_name: str
    usage: dict[str, int]
    limits: dict[str, int]
    remaining: dict[str, int]
    at_limit: dict[str, bool]


class PlanEntitlementsResponse(BaseModel):
    has_plan: bool
    plan_name: str
    daily_request_limit: int
    monthly_request_limit: int
    daily_token_limit: int
    monthly_token_limit: int
    features: list[str]
    can_access_feature: bool
    plan_expires: str | None = None
    plan_expired: bool | None = None
