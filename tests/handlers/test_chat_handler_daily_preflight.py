"""The daily usage limit must be enforced BEFORE the provider call."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.handlers.chat_handler import ChatInferenceHandler
from src.services.billing.daily_usage_limiter import (
    DailyUsageLimitExceeded,
    DailyUsageUnavailable,
)

_MSGS = [{"role": "user", "content": "hi"}]


def _handler(anonymous=False):
    h = ChatInferenceHandler(api_key="gw_key", background_tasks=None, request=None)
    h.user = {"id": 7, "subscription_allowance": 5.0, "purchased_credits": 5.0}
    h.is_anonymous = anonymous
    return h


@pytest.mark.asyncio
async def test_over_limit_user_gets_429_before_inference():
    with patch(
        "src.services.billing.daily_usage_limiter.check_daily_limit_preflight",
        side_effect=DailyUsageLimitExceeded("Daily usage limit exceeded"),
    ):
        with pytest.raises(HTTPException) as exc:
            await _handler()._check_credit_sufficiency("openai/gpt-4o", _MSGS, 50)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_lookup_failure_fails_closed_with_503():
    with patch(
        "src.services.billing.daily_usage_limiter.check_daily_limit_preflight",
        side_effect=DailyUsageUnavailable("db down"),
    ):
        with pytest.raises(HTTPException) as exc:
            await _handler()._check_credit_sufficiency("openai/gpt-4o", _MSGS, 50)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_free_and_anonymous_skip_preflight():
    with patch(
        "src.services.billing.daily_usage_limiter.check_daily_limit_preflight",
        side_effect=AssertionError("must not be called"),
    ):
        assert await _handler()._check_credit_sufficiency("x/y:free", _MSGS, 50) == 50
        assert await _handler(anonymous=True)._check_credit_sufficiency("openai/gpt-4o", _MSGS, 50) == 50
