from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr


class AuthMethod(str, Enum):
    EMAIL = "email"
    WALLET = "wallet"
    GOOGLE = "google"
    GITHUB = "github"

class PaymentMethod(str, Enum):
    MASTERCARD = "mastercard"
    PACA_TOKEN = "paca_token"

class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    TRIAL = "trial"

class PlanType(str, Enum):
    """Plan type enumeration"""
    FREE = "free"
    DEV = "dev"
    TEAM = "team"
    CUSTOMIZE = "customize"
