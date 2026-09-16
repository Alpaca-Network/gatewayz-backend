from enum import Enum


class AuthMethod(str, Enum):  # noqa: UP042
    EMAIL = "email"
    PHONE = "phone"
    WALLET = "wallet"
    GOOGLE = "google"
    GITHUB = "github"


class PaymentMethod(str, Enum):  # noqa: UP042
    MASTERCARD = "mastercard"
    PACA_TOKEN = "paca_token"


class SubscriptionStatus(str, Enum):  # noqa: UP042
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    TRIAL = "trial"


# NOTE: a `PlanType` enum (free/dev/team/customize) used to live here. It was
# deleted in the cleanup that followed 20260915164500_backfill_plans_plan_type.sql:
# none of its four values except "free" has ever existed in the production
# `plans.plan_type` column, and its only consumer was the discontinued
# SubscriptionPlan model. The live vocabulary is documented on the column itself
# (COMMENT ON COLUMN public.plans.plan_type) and is derived from plans.name, not
# from an application enum.
