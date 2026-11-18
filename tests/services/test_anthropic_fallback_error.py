"""The compat fallback must not hide the native call's real 4xx.

On 2026-11-18 every Claude 4.7+/5.x model failed with "Model not found":
the native call was rejected with 400 (sampling params), the compat endpoint
then answered 404 for a model it does not know, and that 404 reached users.
"""

from unittest.mock import patch

import httpx
import pytest

from src.services.providers import anthropic_client


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(code, request=request, text='{"error":"x"}')
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


class CompatNotFound(Exception):
    pass


MESSAGES = [{"role": "user", "content": "Say OK"}]
NATIVE = "src.services.providers.anthropic_native_client"


@pytest.fixture(autouse=True)
def _native_on(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_NATIVE_TRANSPORT", "true")


@pytest.mark.parametrize(
    "entry,native_fn,compat_fn",
    [
        (
            "make_anthropic_request",
            "make_anthropic_native_request",
            "_make_anthropic_request_compat",
        ),
        (
            "make_anthropic_request_stream",
            "make_anthropic_native_request_stream",
            "_make_anthropic_request_stream_compat",
        ),
    ],
)
class TestFallbackErrors:
    def test_native_4xx_wins_when_compat_also_fails(self, entry, native_fn, compat_fn):
        native_exc = _status_error(400)
        with (
            patch(f"{NATIVE}.{native_fn}", side_effect=native_exc),
            patch.object(anthropic_client, compat_fn, side_effect=CompatNotFound("404")),
        ):
            with pytest.raises(httpx.HTTPStatusError) as caught:
                getattr(anthropic_client, entry)(MESSAGES, "claude-opus-5-5")
        assert caught.value is native_exc

    def test_compat_success_still_rescues_native_4xx(self, entry, native_fn, compat_fn):
        with (
            patch(f"{NATIVE}.{native_fn}", side_effect=_status_error(400)),
            patch.object(anthropic_client, compat_fn, return_value="ok"),
        ):
            assert getattr(anthropic_client, entry)(MESSAGES, "claude-opus-5-5") == "ok"

    def test_native_5xx_keeps_compat_error(self, entry, native_fn, compat_fn):
        with (
            patch(f"{NATIVE}.{native_fn}", side_effect=_status_error(503)),
            patch.object(anthropic_client, compat_fn, side_effect=CompatNotFound("404")),
        ):
            with pytest.raises(CompatNotFound):
                getattr(anthropic_client, entry)(MESSAGES, "claude-opus-5-5")
