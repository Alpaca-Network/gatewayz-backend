"""Request schemas for the holdings-rewards API (src/routes/holdings.py).

Money fields are Decimal, not float: a tier floor is a USD amount stored as
numeric(38,18) and a rate is numeric(18,6), and neither survives a float
round trip intact.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

_CONTRACT_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class HoldingsRateInput(BaseModel):
    """One tier of the rate table: everything at or above `min_usd` (and
    below the next tier's floor) earns `credits_per_1k_usd_per_day`."""

    min_usd: Decimal = Field(..., ge=0)
    credits_per_1k_usd_per_day: Decimal = Field(..., ge=0)
    note: str | None = None


class UpdateHoldingsRatesRequest(BaseModel):
    rates: list[HoldingsRateInput]

    @field_validator("rates")
    @classmethod
    def _non_empty(cls, v: list[HoldingsRateInput]) -> list[HoldingsRateInput]:
        if not v:
            raise ValueError("rates must not be empty")
        return v


class CreateHoldingsTokenRequest(BaseModel):
    """Register one asset. `contract_address` is omitted (or null) for a
    chain's native coin -- ETH, BNB, POL, AVAX."""

    chain_id: int = Field(..., ge=1)
    contract_address: str | None = None
    symbol: str = Field(..., min_length=1, max_length=32)
    decimals: int = Field(..., ge=0, le=36)
    price_id: str = Field(..., min_length=1, max_length=128)
    is_enabled: bool = True

    @field_validator("contract_address")
    @classmethod
    def _valid_address(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _CONTRACT_ADDRESS_RE.match(v):
            raise ValueError("contract_address must be a 0x-prefixed 40-character hex address")
        return v.lower()


class UpdateHoldingsTokenRequest(BaseModel):
    """Patch one registered asset. Every field is optional; at least one
    must be supplied, so an empty body is a client error rather than a
    silent no-op."""

    contract_address: str | None = None
    symbol: str | None = Field(None, min_length=1, max_length=32)
    decimals: int | None = Field(None, ge=0, le=36)
    price_id: str | None = Field(None, min_length=1, max_length=128)
    is_enabled: bool | None = None

    @field_validator("contract_address")
    @classmethod
    def _valid_address(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _CONTRACT_ADDRESS_RE.match(v):
            raise ValueError("contract_address must be a 0x-prefixed 40-character hex address")
        return v.lower()

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UpdateHoldingsTokenRequest:
        if not self.model_dump(exclude_unset=True):
            raise ValueError("at least one field must be supplied")
        return self


class RunHoldingsRewardsRequest(BaseModel):
    reward_date: date | None = None
