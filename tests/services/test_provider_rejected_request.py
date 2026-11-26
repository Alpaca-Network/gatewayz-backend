"""A provider's "this request is invalid" must reach the caller as is.

Production 2026-11-26: GPT-5 requests with a parameter OpenAI rejects
(logprobs, stop, logit_bias, ...) came back as "Model 'openai/gpt-5.5' not
found". The upstream 400 was re-labelled 502, the selector failed over, and the
next provider's "not found" replaced the real reason. It also counted the 400
against a working provider's circuit breaker.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.services.provider_failover import ProviderRejectedRequest
from src.services.provider_selector import ProviderSelector


def _selector(provider_names):
    selector = ProviderSelector.__new__(ProviderSelector)
    providers = []
    for i, name in enumerate(provider_names):
        p = MagicMock()
        p.name = name
        p.model_id = f"{name}-model"
        p.priority = i
        providers.append(p)
    registry = MagicMock()
    registry.get_model.return_value = object()
    registry.select_provider.return_value = providers[0]
    registry.get_fallback_providers.return_value = providers[1:]
    selector.registry = registry
    tracker = MagicMock()
    tracker.is_available.return_value = True
    tracker.record_failure.return_value = False
    selector.health_tracker = tracker
    return selector


def test_rejected_request_is_raised_without_failover():
    selector = _selector(["openai", "anthropic"])
    called = []

    def execute(provider, model):
        called.append(provider)
        if provider == "openai":
            raise ProviderRejectedRequest(status_code=400, detail="Unsupported parameter: logprobs")
        raise HTTPException(status_code=400, detail="Model 'openai/gpt-5.5' not found")

    with pytest.raises(ProviderRejectedRequest) as exc:
        selector.execute_with_failover(model_id="openai/gpt-5.5", execute_fn=execute)

    assert called == ["openai"]
    assert "logprobs" in str(exc.value.detail)
    selector.health_tracker.record_failure.assert_not_called()


def test_provider_failures_still_fail_over():
    selector = _selector(["openai", "anthropic"])
    called = []

    def execute(provider, model):
        called.append(provider)
        if provider == "openai":
            raise HTTPException(status_code=502, detail="upstream down")
        return {"ok": True}

    result = selector.execute_with_failover(model_id="m", execute_fn=execute)

    assert called == ["openai", "anthropic"]
    assert result["success"] is True
    assert result["provider"] == "anthropic"
    selector.health_tracker.record_failure.assert_called_once_with("m", "openai")


def test_rejected_request_is_an_http_400():
    exc = ProviderRejectedRequest(status_code=400, detail="bad")
    assert isinstance(exc, HTTPException)
    assert exc.status_code == 400


def _handler():
    from src.handlers import chat_handler as ch

    handler = ch.ChatInferenceHandler.__new__(ch.ChatInferenceHandler)
    handler.request_id = "req_test"
    handler.user = {"id": 7}
    handler._bind_byok = lambda provider: None
    handler._unbind_byok = lambda token: None
    return handler


def _call_with_upstream_error(handler, mapped_status, mapped_detail):
    from unittest.mock import patch

    upstream = RuntimeError(f"Error code: {mapped_status} - upstream said no")

    def provider_request(messages, model_id, **kwargs):
        raise upstream

    with (
        patch(
            "src.handlers.provider_registry.PROVIDER_ROUTING",
            {"openai": {"request": provider_request}},
        ),
        patch(
            "src.services.provider_failover.map_provider_error",
            return_value=HTTPException(status_code=mapped_status, detail=mapped_detail),
        ),
    ):
        handler._call_provider("openai", "openai/gpt-5.5", [{"role": "user", "content": "hi"}])


def test_handler_raises_rejected_request_for_an_upstream_400():
    with pytest.raises(ProviderRejectedRequest) as exc:
        _call_with_upstream_error(
            _handler(),
            400,
            "Unsupported parameter: 'logprobs' is not supported with this model.",
        )
    assert exc.value.status_code == 400
    error = exc.value.detail["error"]
    assert error["status"] == 400
    assert "logprobs" in error["message"]
    assert "openai" in error["message"]


def test_handler_still_returns_502_for_other_provider_errors():
    with pytest.raises(HTTPException) as exc:
        _call_with_upstream_error(_handler(), 500, "upstream exploded")
    assert not isinstance(exc.value, ProviderRejectedRequest)
    assert exc.value.status_code == 502


def test_upstream_message_is_taken_from_the_error_body():
    from src.services.provider_failover import _upstream_error_message

    class SdkError(Exception):
        body = {
            "error": {
                "message": "Unsupported parameter: 'logprobs' is not supported with this model.",
                "type": "invalid_request_error",
            }
        }

    assert _upstream_error_message(SdkError("Error code: 400 - {...}")) == (
        "Unsupported parameter: 'logprobs' is not supported with this model."
    )
    assert _upstream_error_message(RuntimeError("plain text")) == "plain text"


def test_handler_message_has_no_duplicated_prefix():
    detail = (
        "Provider 'openai' rejected request for model 'openai/gpt-5.5': Unsupported 'logprobs'."
    )
    with pytest.raises(ProviderRejectedRequest) as exc:
        _call_with_upstream_error(_handler(), 400, detail)
    message = exc.value.detail["error"]["message"]
    assert message.count("Provider 'openai'") == 1
    assert message.endswith("Unsupported 'logprobs'.")
