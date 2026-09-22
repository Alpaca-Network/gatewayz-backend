"""An expired credential of OURS is not the caller's authentication problem.

Found 2026-09-22 by a per-vendor serve probe. Two models in our own catalog
answered:

    502  "Provider 'openrouter' returned an error for model
          'meta/muse-spark-1.3': Error code: 401 - {'error': {'message':
          'API key expired.'}}"

Two defects, both familiar by now.

1. 502 is retryable. An expired key is cleared by a human rotating it, never
   by waiting -- and it is OUR key, so the caller cannot even do that. The
   catalog advertised both models throughout and nothing anywhere said a
   credential had lapsed.

2. FIVE code paths handled this one condition and gave THREE different
   statuses: the typed OpenAI and Cerebras branches said 401, the httpx
   branch said 500, and an untyped exception carrying "Error code: 401" fell
   through to the generic 502. Exactly one of the five alerted anybody. Which
   answer a caller got depended on which SDK happened to be in the path.

The message mattered as much as the status. "anthropic authentication error"
next to a 401 reads, to a partner, as "your key is bad" -- so their engineer
rotates a key that was never the problem. It now says whose credential failed.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import openai
import pytest

import src.services.provider_failover as pf
from src.services.provider_failover import map_provider_error


def _openai_error(cls, status, message="API key expired."):
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    body = {"error": {"message": message, "code": status}}
    response = httpx.Response(status, request=request, json=body)
    return cls(f"Error code: {status} - {body}", response=response, body=body)


# Every shape the same expired key can arrive as.
def _shapes():
    return {
        "typed_401": _openai_error(openai.AuthenticationError, 401),
        "typed_403": _openai_error(openai.PermissionDeniedError, 403),
        "untyped_text": RuntimeError(
            "Error code: 401 - {'error': {'message': 'API key expired.'}}"
        ),
        "httpx": httpx.HTTPStatusError(
            "401",
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/x"),
            response=httpx.Response(
                401,
                request=httpx.Request("POST", "https://openrouter.ai/api/v1/x"),
                json={"error": "expired"},
            ),
        ),
    }


def _map(exc):
    with patch.object(pf, "alert_provider_auth_failure"):
        return map_provider_error("openrouter", "meta/muse-spark-1.3", exc)


@pytest.mark.parametrize("name", sorted(_shapes()))
def test_one_condition_gets_one_status(name):
    exc = _shapes()[name]
    mapped = _map(exc)
    assert mapped.status_code == 401, f"{name} -> {mapped.status_code}"


@pytest.mark.parametrize("name", sorted(_shapes()))
def test_none_of_them_is_retryable(name):
    # 502/503/504 all invite a retry that cannot clear an expired key.
    assert _map(_shapes()[name]).status_code < 500


@pytest.mark.parametrize("name", sorted(_shapes()))
def test_every_shape_alerts_somebody(name):
    # Four of the five paths used to fail silently. An expired credential that
    # nobody is told about is the part that turns a rotation into an outage.
    with patch.object(pf, "alert_provider_auth_failure") as alert:
        map_provider_error("openrouter", "meta/muse-spark-1.3", _shapes()[name])
    assert alert.called, f"{name} raised no alert"


@pytest.mark.parametrize("name", sorted(_shapes()))
def test_the_caller_is_told_it_is_not_their_key(name):
    detail = str(_map(_shapes()[name]).detail).lower()
    assert "not a problem with your api key" in detail
    assert "gatewayz" in detail


@pytest.mark.parametrize("name", sorted(_shapes()))
def test_the_upstream_body_is_not_echoed(name):
    # Provider bodies carry key ids, dashboard URLs and internal hostnames.
    detail = str(_map(_shapes()[name]).detail)
    assert "API key expired" not in detail
    assert "openrouter.ai" not in detail


def test_a_genuine_upstream_outage_is_still_retryable():
    # The carve-out. This must not turn every provider failure terminal.
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/x")
    exc = openai.InternalServerError(
        "Error code: 500 - server error",
        response=httpx.Response(500, request=request, json={}),
        body=None,
    )
    assert _map(exc).status_code >= 500


def test_a_rate_limit_is_still_a_429():
    assert _map(RuntimeError("Error code: 429 - rate limit exceeded")).status_code == 429
