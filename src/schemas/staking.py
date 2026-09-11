"""Request/response schemas for the staking-rewards admin API
(src/routes/admin_staking.py)."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator


class RewardRateInput(BaseModel):
    min_stake_wayz: int = Field(..., ge=0)
    credits_per_1k_wayz_per_day: float = Field(..., ge=0)
    note: str | None = None


class UpdateRewardRatesRequest(BaseModel):
    rates: list[RewardRateInput]

    @field_validator("rates")
    @classmethod
    def _non_empty(cls, v: list[RewardRateInput]) -> list[RewardRateInput]:
        if not v:
            raise ValueError("rates must not be empty")
        return v


class RunRewardsRequest(BaseModel):
    reward_date: date | None = None
