"""A 503 must not discard the reason the route gave.

GET /v1/usage/export/key answered, in production, for weeks:

    503  "The service is temporarily unavailable due to maintenance or high
          load. Please try again shortly."

The real cause was one unset environment variable. The route said so
precisely -- `HTTPException(503, "Signing unavailable: USAGE_SIGNING_KEY is
not set")` -- and the shared error handler threw that sentence away and
substituted a claim about traffic.

Two failures in one: a deterministic condition wearing a transient status's
prose, and a precise cause replaced by a vague one. The second is what made
the first expensive -- nobody investigates load. The fix was thirty seconds
once anybody could see what it was.

Same family as #2291/#2292/#2296/#2297/#2354/#2355, from the other end: those
were the wrong STATUS for a real cause; this is the right status with the
cause deleted.
"""

from __future__ import annotations

from fastapi import HTTPException

from src.utils.error_handlers import _map_http_exception_to_detailed_error

CONFIG_503 = "Signing unavailable: USAGE_SIGNING_KEY is not set"


def _map(detail, status=503):
    return _map_http_exception_to_detailed_error(HTTPException(status_code=status, detail=detail))


def test_the_stated_reason_reaches_the_caller():
    body = _map(CONFIG_503).error
    assert CONFIG_503 in body.message


def test_the_caller_is_not_told_to_wait_for_a_config_gap():
    body = _map(CONFIG_503).error
    text = (
        body.message + " " + (body.detail or "") + " " + " ".join(body.suggestions or "")
    ).lower()
    assert "high load" not in text
    assert "maintenance" not in text
    assert "try again shortly" not in text


def test_the_advice_says_retrying_will_not_help():
    assert any("not help" in s.lower() for s in (_map(CONFIG_503).error.suggestions or []))


def test_a_bare_503_keeps_the_generic_message():
    # The carve-out. A route that names no cause has nothing to preserve, and
    # must not start claiming a config problem it knows nothing about.
    for detail in ("", "   ", None):
        body = _map(detail).error
        assert "temporarily unavailable" in body.message.lower(), detail


def test_a_provider_503_is_untouched():
    # Upstream bodies carry key ids and internal URLs; that branch sanitizes and
    # must keep owning them. Passing a reason through is only safe because these
    # details are ones WE wrote.
    body = _map("Provider 'anthropic' is unavailable").error
    assert body.code == "PROVIDER_ERROR"


def test_a_default_status_phrase_is_not_a_reason():
    # Starlette fills detail with "Service Unavailable" when a route raises
    # HTTPException(503) alone. Treating that as a diagnosis would replace a
    # helpful generic message with a useless one.
    body = _map(None).error
    assert "temporarily unavailable" in body.message.lower()
    assert body.suggestions and "not help" not in " ".join(body.suggestions).lower()


def test_the_signing_endpoints_real_cause_is_now_visible():
    # The concrete case: the string the route raises names the variable an
    # operator has to set. That is the whole point of preserving it.
    body = _map(CONFIG_503).error
    assert "USAGE_SIGNING_KEY" in body.message
