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


class PlanType(str, Enum):  # noqa: UP042
    """Plan type enumeration"""

    FREE = "free"
    DEV = "dev"
    TEAM = "team"
    CUSTOMIZE = "customize"
