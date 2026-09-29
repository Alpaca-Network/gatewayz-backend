"""The daily usage limit must be enforced BEFORE the provider call (once per request)."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.routes.chat import _enforce_daily_limit_preflight as real_preflight
from src.services.billing.daily_usage_limiter import (
    DailyUsageLimitExceeded,
    DailyUsageUnavailable,
)

_LIMITER = "src.services.billing.daily_usage_limiter.check_daily_limit_preflight"


@pytest.mark.asyncio
async def test_over_limit_user_gets_429():
    with patch(_LIMITER, side_effect=DailyUsageLimitExceeded("Daily usage limit exceeded")):
        with pytest.raises(HTTPException) as exc:
            await real_preflight({"id": 7})
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_lookup_failure_fails_closed_with_503():
    with patch(_LIMITER, side_effect=DailyUsageUnavailable("db down")):
        with pytest.raises(HTTPException) as exc:
            await real_preflight({"id": 7})
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_under_limit_passes():
    with patch(_LIMITER, return_value=None) as m:
        await real_preflight({"id": 7})
    m.assert_called_once_with(7)
