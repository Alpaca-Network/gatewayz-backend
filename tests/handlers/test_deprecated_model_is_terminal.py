"""An upstream model that no longer exists is a 400, not a retryable 502.

Found by the daily serve probe on 2026-09-20 (#2354). OpenAI retired
gpt-4o-search-preview; our catalog kept advertising it; and the call came back:

    502  "Provider 'openai' returned an error for model 'gpt-4o-search-preview':
          Error code: 404 - ... has been deprecated ... 'code': 'model_not_found'"

502's own user-facing text in errors.py says "This is usually temporary." It is
not temporary. The model is gone, and no retry brings it back -- so every SDK
retry policy spent its budget on a model that cannot answer, and then reported
it to the caller as our outage.

The tell that this was a missing branch rather than a judgement call, exactly as
in #2355: map_provider_error ALREADY classified it 404. ChatHandler._call_provider
special-cased 429 and 402 and swept every other mapping into the generic 502.

400 rather than 404 is deliberate: an id that was never in the catalog already
gets a 400 from the pricing gate, and the caller's correct action is identical --
pick another model. One condition must not have two statuses.

Sixth instance of the family: #2291, #2292, #2296, #2297, #2355, this.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import openai
import pytest
from fastapi import HTTPException

from src.handlers.chat_handler import ChatInferenceHandler
from src.services.provider_failover import map_provider_error

DEPRECATION_BODY = {
    "error": {
        "message": (
            "The model `gpt-4o-search-preview` has been deprecated, learn more "
            "here: https://platform.openai.com/docs/deprecations"
        ),
        "type": "invalid_request_error",
        "param": None,
        "code": "model_not_found",
    }
}


def _deprecated_model_error() -> openai.NotFoundError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(404, request=request, json=DEPRECATION_BODY)
    return openai.NotFoundError(
        f"Error code: 404 - {DEPRECATION_BODY}", response=response, body=DEPRECATION_BODY
    )


def _handler() -> ChatInferenceHandler:
    handler = ChatInferenceHandler(api_key=None, request=None)
    handler.user = {"id": 1, "key_id": 7}
    handler.is_anonymous = False
    handler.request_id = "req_test"
    return handler


def _call_with_upstream(exc: Exception) -> HTTPException:
    """Drive _call_provider with a provider client that raises `exc`."""

    def _raise(*_args, **_kwargs):
        raise exc

    from src.handlers import provider_registry

    routing = {**provider_registry.PROVIDER_ROUTING, "openai": {"request": _raise}}
    with patch.object(provider_registry, "PROVIDER_ROUTING", routing):
        with pytest.raises(HTTPException) as caught:
            _handler()._call_provider(
                provider_name="openai",
                model_id="gpt-4o-search-preview",
                messages=[{"role": "user", "content": "hi"}],
            )
    return caught.value


def _error_body(exc: HTTPException) -> dict:
    detail = exc.detail
    assert isinstance(detail, dict), detail
    inner = detail.get("error")
    assert isinstance(inner, dict), detail
    return inner


def test_the_mapper_already_calls_a_deprecated_model_not_found():
    # The half that was already right, pinned so the two paths cannot drift
    # apart again in the other direction.
    mapped = map_provider_error("openai", "gpt-4o-search-preview", _deprecated_model_error())
    assert mapped.status_code == 404


def test_a_deprecated_model_is_a_terminal_400():
    exc = _call_with_upstream(_deprecated_model_error())
    assert exc.status_code == 400, f"got {exc.status_code}; a retryable status invites retries"


def test_the_envelope_agrees_with_its_own_http_status():
    # A body that says 404 inside a 400 response is how a client ends up
    # branching on the wrong one.
    body = _error_body(_call_with_upstream(_deprecated_model_error()))
    assert body.get("status") == 400
    assert body.get("code") == "MODEL_NOT_FOUND"


def test_the_caller_is_never_told_to_wait_and_retry():
    # "try again with a valid model ID" is fine -- that is a different action.
    # What must be absent is any advice to retry THIS request unchanged, which
    # is what the 502 body said ("This is usually temporary").
    exc = _call_with_upstream(_deprecated_model_error())
    text = str(exc.detail).lower()
    for phrase in ("temporary", "temporarily", "try again shortly", "try again later"):
        assert phrase not in text, phrase
    assert not (exc.headers or {}).get("Retry-After")


def test_the_upstream_url_is_not_echoed_to_the_caller():
    # The upstream body carries a docs URL; provider bodies also carry key ids
    # and internal hostnames. None of it belongs in a client-visible error.
    assert "platform.openai.com" not in str(_call_with_upstream(_deprecated_model_error()).detail)


def test_a_genuine_upstream_5xx_is_still_a_retryable_502():
    # The carve-out that keeps this fix from trading one wrong status for
    # another: a real provider outage must stay retryable.
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(500, request=request, json={"error": {"message": "server error"}})
    exc = _call_with_upstream(
        openai.InternalServerError("Error code: 500 - server error", response=response, body=None)
    )
    assert exc.status_code == 502


# --- the catalog half of #2354 ------------------------------------------------
#
# The status fix stops us LYING about a dead model. It does not stop us
# ADVERTISING one. The sweep that would have caught it never had grounds to:
# our own mapper buried the upstream 404 inside a 502, and a 5xx is deliberately
# never treated as evidence a model is dead (it hid nine live flagship models
# once already). So the deprecation was invisible from both ends.


def test_the_sweep_reads_upstream_deprecation_as_hard_evidence():
    from src.services.monitoring.model_health_sweep import classify_probe_result

    for error in (
        "The model `gpt-4o-search-preview` has been deprecated",
        "Error code: 404 - {'code': 'model_not_found'}",
    ):
        assert classify_probe_result("fail", 400, error) == "hard_fail", error
        assert classify_probe_result("error", None, error) == "hard_fail", error


def test_a_5xx_with_no_deprecation_text_is_still_soft():
    # The property that kept nine live models visible. Hiding a model asserts it
    # does not exist; a 5xx is about the moment, not the model.
    from src.services.monitoring.model_health_sweep import classify_probe_result

    assert classify_probe_result("fail", 502, "Provider 'openai' returned an error") == "soft"
    assert classify_probe_result("fail", 429, "rate limit exceeded") == "soft"
