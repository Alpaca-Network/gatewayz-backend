"""The tag reaches the usage record, and an untagged call is unchanged.

Testing extract_request_tag alone would prove the parser works and nothing
about whether the value is ever written down -- which is the entire feature.
This exercises _save_request_record and inspects what it hands the persistence
layer.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from src.handlers.chat_handler import ChatInferenceHandler


class _Req:
    """Enough of a Request for the handler: headers plus the `state` it reads
    when building a billing reference."""

    def __init__(self, headers):
        self.headers = headers
        self.state = SimpleNamespace()


def _handler(headers=None):
    h = ChatInferenceHandler(api_key=None, request=_Req(headers) if headers is not None else None)
    h.user = {"id": 1, "key_id": 7}
    h.is_anonymous = False
    return h


def _saved_kwargs(handler):
    with patch("src.handlers.chat_handler.save_chat_completion_request_with_cost") as mock_save:
        handler._save_request_record(
            model_name="anthropic/claude-haiku-4-5-20251001",
            provider_name="anthropic",
            input_tokens=10,
            output_tokens=5,
        )
    assert mock_save.called, "the usage record was never written"
    return mock_save.call_args.kwargs


def test_the_tag_reaches_the_usage_record():
    kwargs = _saved_kwargs(_handler({"x-gatewayz-tag": "init/orbital-refi-q3"}))
    assert kwargs["metadata"] == {"tag": "init/orbital-refi-q3"}


def test_an_untagged_call_writes_no_metadata_key_at_all():
    # Not `metadata: None`, not `{}` -- absent. An untagged caller's row must be
    # what it was before this feature existed, or "no behaviour change" is a
    # claim rather than a fact.
    assert "metadata" not in _saved_kwargs(_handler({}))


def test_a_handler_with_no_request_still_saves():
    # Anonymous and internal paths construct handlers without a Request.
    kwargs = _saved_kwargs(_handler(None))
    assert "metadata" not in kwargs
    assert kwargs["input_tokens"] == 10


def test_an_unusable_tag_costs_the_tag_not_the_record():
    kwargs = _saved_kwargs(_handler({"x-tag": "<script>alert(1)</script>"}))
    assert "metadata" not in kwargs
    assert kwargs["status"] == "completed", "the call itself must still be recorded"


def test_a_failed_call_still_carries_its_tag():
    # Failures cost real compute. The partner plan counts them separately rather
    # than folding them into an average, which is only possible if a failed call
    # is attributed at all.
    handler = _handler({"x-tag": "init/abc"})
    with patch("src.handlers.chat_handler.save_chat_completion_request_with_cost") as mock_save:
        handler._save_request_record(
            model_name="m",
            provider_name="p",
            input_tokens=0,
            output_tokens=0,
            status="failed",
            error_message="upstream exploded",
        )
    kwargs = mock_save.call_args.kwargs
    assert kwargs["metadata"] == {"tag": "init/abc"}
    assert kwargs["status"] == "failed"
