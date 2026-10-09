"""Request schemas for the delegated-staking API (src/routes/delegation.py).

Rates are Decimal, not float: they are numeric(18,6) in the database and do
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
    job: Literal["measure", "accrue", "reconcile"]
    # accrue: the reward date (default yesterday UTC). Ignored otherwise.
    reward_date: date | None = None


class ResumeDelegationRequest(BaseModel):
    asset: DelegationAsset
