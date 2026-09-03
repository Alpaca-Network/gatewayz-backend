"""Tests for the Sentry before_send scrubbing hook.

Threat model G5 (docs/security/ANONYMITY_THREAT_MODEL.md): Sentry must never
receive request bodies, cookies, auth-bearing headers, or unbounded exception
text.
"""

from src.utils.sentry_scrub import strip_sensitive_event


def test_strips_request_body_and_cookies():
    event = {
        "request": {
            "data": {"messages": [{"role": "user", "content": "secret prompt"}]},
            "cookies": {"session": "abc123"},
            "headers": {"Content-Type": "application/json"},
        }
    }
    result = strip_sensitive_event(event, {})
    assert "data" not in result["request"]
    assert "cookies" not in result["request"]
    assert result["request"]["headers"]["Content-Type"] == "application/json"


def test_strips_auth_bearing_headers_case_insensitively():
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer sk-secret",
                "Cookie": "session=abc",
                "X-Api-Key": "gw_live_secret",
                "Content-Type": "application/json",
            }
        }
    }
    result = strip_sensitive_event(event, {})
    headers = result["request"]["headers"]
    assert "Authorization" not in headers
    assert "Cookie" not in headers
    assert "X-Api-Key" not in headers
    assert headers["Content-Type"] == "application/json"


def test_truncates_exception_message():
    long_message = "x" * 5000
    event = {"exception": {"values": [{"type": "ValueError", "value": long_message}]}}
    result = strip_sensitive_event(event, {})
    assert len(result["exception"]["values"][0]["value"]) <= 300


def test_passes_through_event_with_no_request_or_exception():
    event = {"message": "informational event"}
    result = strip_sensitive_event(event, {})
    assert result == event


def test_unrecognized_request_shape_passes_through_without_crashing():
    """Non-dict request/exception sections are left alone (nothing to scrub)
    rather than crashing — Sentry SDK internals control the real shape."""
    event = {"request": "not-a-dict", "message": "ok"}
    result = strip_sensitive_event(event, {})
    assert result == event


def test_never_raises_on_malformed_event_and_drops_it():
    """A scrubbing failure must fail closed (drop the event), never crash or
    let an un-scrubbed event through."""

    class ExplodingEvent(dict):
        def get(self, key, default=None):
            if key == "request":
                raise RuntimeError("simulated corruption")
            return super().get(key, default)

    event = ExplodingEvent(request={"data": "x"})
    result = strip_sensitive_event(event, {})
    assert result is None
