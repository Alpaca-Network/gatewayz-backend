"""Spend ceilings must be tellable apart by machine, not by substring.

A partner integrating against us has to route three conditions to three
different behaviours:

  rate limit          -> wait and retry        (transient, ours will clear)
  request-cap spent   -> escalate a cap raise  (terminal until a human acts)
  credits exhausted   -> escalate a top-up     (terminal until a human acts)

Before this, cap exhaustion returned **429 with a bare `{"detail": "..."}`
string** — the same status as a rate limit. That is the same defect shape as
the model-id 503 we fixed on 2026-09-08: a terminal client condition wearing a
transient status. Every SDK with a retry policy (Anthropic's included) retries
429 with backoff, so an exhausted cap was retried until the budget ran out and
then reported as an outage. Telling it from a real rate limit required
matching on the message text, which no careful integrator will do.

Both terminal conditions now return 402 with a machine-readable `code`.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.security.deps import ceiling_http_exception


def test_request_cap_exhaustion_is_terminal_not_a_rate_limit():
    exc = ceiling_http_exception("API key request limit reached")
    assert exc.status_code == 402
    assert exc.detail["error"]["code"] == "request_cap_exhausted"
    # Must NOT be confusable with a rate limit.
    assert exc.status_code != 429
    assert exc.detail["error"]["type"] != "rate_limit_error"


def test_request_cap_envelope_names_the_action_a_human_must_take():
    exc = ceiling_http_exception("API key request limit reached")
    msg = exc.detail["error"]["message"].lower()
    # It must say retrying is futile and name the cap as the thing to change,
    # because the status alone can't carry "stop retrying, go ask someone".
    assert "will not clear" in msg
    assert "cap" in msg


def test_credit_exhaustion_carries_its_own_code():
    exc = ceiling_http_exception("Insufficient credits. Please add credits to continue.")
    assert exc.status_code == 402
    assert exc.detail["error"]["code"] == "insufficient_credits"


def test_the_two_terminal_ceilings_share_a_status_but_never_a_code():
    cap = ceiling_http_exception("API key request limit reached")
    credits = ceiling_http_exception("Insufficient credits. Please add credits to continue.")
    assert cap.status_code == credits.status_code == 402
    assert cap.detail["error"]["code"] != credits.detail["error"]["code"]


@pytest.mark.parametrize(
    ("message", "status"),
    [
        ("API key is inactive", 401),
        ("API key has expired", 401),
        ("IP address not allowed for this API key", 403),
        ("Domain not allowed", 403),
    ],
)
def test_non_ceiling_auth_failures_keep_their_existing_status(message, status):
    assert ceiling_http_exception(message).status_code == status


def test_unrecognised_validation_failure_still_defaults_to_401():
    assert ceiling_http_exception("something else entirely").status_code == 401


def test_every_envelope_is_a_dict_not_a_bare_string():
    # The bare `{"detail": "..."}` string was the other half of the problem:
    # there was no code to pin, so a partner had to match on prose.
    for msg in (
        "API key request limit reached",
        "Insufficient credits. Please add credits to continue.",
        "API key has expired",
    ):
        detail = ceiling_http_exception(msg).detail
        assert isinstance(detail, dict)
        assert set(detail["error"]) >= {"message", "type", "code"}


def test_raising_helper_produces_the_same_envelope():
    with pytest.raises(HTTPException) as exc:
        raise ceiling_http_exception("API key request limit reached")
    assert exc.value.detail["error"]["code"] == "request_cap_exhausted"
