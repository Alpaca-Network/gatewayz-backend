"""429 responses name the limit that was hit and keep the real reason.

Every 429 used to say "Rate limit exceeded: request_rate" with generic advice,
and the "Wait N seconds" suggestion was appended to a shared list, so it piled
up across responses (a production error carried seven copies).
"""

import pytest
from fastapi import HTTPException

from src.utils import error_handlers
from src.utils.errors import DetailedErrorFactory, ErrorCode, get_suggestions

MAP = error_handlers._map_http_exception_to_detailed_error


def _map(detail, headers=None):
    return MAP(HTTPException(status_code=429, detail=detail, headers=headers)).error


@pytest.mark.parametrize(
    "detail, kind",
    [
        (
            "The model 'x' is temporarily rate limited upstream due to high demand.",
            "provider_rate_limit",
        ),
        ("Rate limit exceeded: provider_rate_limit", "provider_rate_limit"),
        ("Minute request limit exceeded", "requests_per_minute"),
        ("Minute token limit exceeded", "tokens_per_minute"),
        ("Too many concurrent requests", "concurrent_requests"),
        ("Anonymous daily limit exceeded (3 requests/day).", "anonymous_limit"),
        ("Something we have not seen", "request_rate"),
    ],
)
def test_the_limit_is_named(detail, kind):
    error = _map(detail)
    assert error.context.limit_type == kind
    assert error.message == f"Rate limit exceeded: {kind}"
    assert error.detail == detail


def test_plan_limits_get_the_plan_limit_error():
    error = _map("Plan limit exceeded: Daily token limit exceeded (500000) for live environment")
    assert error.code == ErrorCode.PLAN_LIMIT_REACHED


def test_wait_suggestion_does_not_pile_up():
    for _ in range(5):
        DetailedErrorFactory.rate_limit_exceeded(limit_type="request_rate", retry_after=1)
    error = DetailedErrorFactory.rate_limit_exceeded(limit_type="request_rate", retry_after=1)
    assert error.error.suggestions.count("Wait 1 seconds before retrying") == 1
    assert "Wait 1 seconds before retrying" not in get_suggestions(ErrorCode.RATE_LIMIT_EXCEEDED)


def test_suggestions_are_a_copy():
    first = get_suggestions(ErrorCode.RATE_LIMIT_EXCEEDED)
    first.append("mutated")
    assert "mutated" not in get_suggestions(ErrorCode.RATE_LIMIT_EXCEEDED)
