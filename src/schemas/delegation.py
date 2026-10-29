"""Request schemas for the delegated-staking API (src/routes/delegation.py).

Rates and amounts are Decimal, not float: they are numeric(18,6) in the database and do
not survive a float round trip intact.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

DelegationAsset = Literal["eth", "ada"]


class DelegationRateInput(BaseModel):
    asset: DelegationAsset
    credits_per_1k_usd_per_day: Decimal = Field(..., ge=0, le=Decimal("1000000"))
    # False deactivates the asset's current rate (it then earns nothing).
    is_active: bool = True
    note: str | None = Field(None, max_length=500)


class UpdateDelegationRatesRequest(BaseModel):
    rates: list[DelegationRateInput]

    @field_validator("rates")
    @classmethod
    def _non_empty_unique(cls, v: list[DelegationRateInput]) -> list[DelegationRateInput]:
        if not v:
            raise ValueError("rates must not be empty")
        assets = [r.asset for r in v]
        if len(assets) != len(set(assets)):
            raise ValueError("at most one rate per asset")
        return v


class RunDelegationRequest(BaseModel):
    # attribute: the v3 reward-share attribution (default payout model).
    job: Literal["measure", "accrue", "reconcile", "attribute"]
    # accrue: the reward date (default yesterday UTC). attribute: the run
    # date its 30-day window ends on (default today UTC). Ignored otherwise.
    reward_date: date | None = None


class DelegationEligibilityRequest(BaseModel):
    # "I am not a resident or citizen of the United States or Canada." Must be
    # true; anything else is refused with 451 attestation_required.
    not_us_ca_person: bool = False


class ResumeDelegationRequest(BaseModel):
    asset: DelegationAsset


class PauseDelegationRequest(BaseModel):
    asset: DelegationAsset
    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason must not be blank")
        return v.strip()


class AdjustDiscountRequest(BaseModel):
    """A signed correction to an account's inference discount balance, in
    USD of list price (6 dp). Never takes the balance below zero."""

    amount_usd: Decimal = Field(..., ge=Decimal("-1000000"), le=Decimal("1000000"))
    reason: str = Field(..., min_length=3, max_length=500)

    @field_validator("amount_usd")
    @classmethod
    def _non_zero_6dp(cls, v: Decimal) -> Decimal:
        if not v.is_finite() or v == 0:
            raise ValueError("amount_usd must be a non-zero number")
        if v != v.quantize(Decimal("0.000001")):
            raise ValueError("amount_usd has at most 6 decimal places")
        return v

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if len(v.strip()) < 3:
            raise ValueError("reason must be at least 3 characters")
        return v.strip()
