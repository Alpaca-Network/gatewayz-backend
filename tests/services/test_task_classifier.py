"""Tests for src.services.task_classifier (gatewayz-backend#2213)."""

import json
import time
from unittest.mock import MagicMock, patch

from src.services.task_classifier import TaskClassification, classify_task


def _fake_response(task_type="code_generation", needs_reasoning=False, confidence=0.8):
    content = json.dumps(
        {"task_type": task_type, "needs_reasoning": needs_reasoning, "confidence": confidence}
    )
    return MagicMock(choices=[MagicMock(message=MagicMock(content=content))])


def test_no_user_message_returns_neutral_classification_without_calling_llm():
    with patch("src.services.task_classifier.make_openai_request") as mock_request:
        result = classify_task(messages=[{"role": "assistant", "content": "hi"}])

    assert result == TaskClassification(error="no_user_text")
    mock_request.assert_not_called()


def test_valid_response_populates_task_type_and_confidence():
    with patch("src.services.task_classifier.make_openai_request") as mock_request:
        mock_request.return_value = _fake_response(
            task_type="code_generation", needs_reasoning=False, confidence=0.9
        )
        result = classify_task(messages=[{"role": "user", "content": "write a fibonacci function"}])

    assert result.task_type == "code_generation"
    assert result.confidence == 0.9
    assert result.error is None


def test_needs_reasoning_adds_reasoning_capability():
    with patch("src.services.task_classifier.make_openai_request") as mock_request:
        mock_request.return_value = _fake_response(needs_reasoning=True)
        result = classify_task(messages=[{"role": "user", "content": "prove this theorem"}])

    assert "reasoning" in result.capability_names


def test_tools_param_adds_tools_capability_without_asking_llm():
    """tools capability is deterministic (extract_required_capabilities), not LLM-judged."""
    with patch("src.services.task_classifier.make_openai_request") as mock_request:
        mock_request.return_value = _fake_response()
        result = classify_task(
            messages=[{"role": "user", "content": "what's the weather"}],
            tools=[{"type": "function", "function": {"name": "get_weather"}}],
        )

    assert "tools" in result.capability_names


def test_malformed_json_falls_back_to_unknown():
    with patch("src.services.task_classifier.make_openai_request") as mock_request:
        mock_request.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="not json"))]
        )
        result = classify_task(messages=[{"role": "user", "content": "hi"}])

    assert result.task_type == "unknown"
    assert result.confidence == 0.0


def test_unrecognized_task_type_falls_back_to_unknown():
    with patch("src.services.task_classifier.make_openai_request") as mock_request:
        mock_request.return_value = _fake_response(task_type="not_a_real_task_type")
        result = classify_task(messages=[{"role": "user", "content": "hi"}])

    assert result.task_type == "unknown"


def test_confidence_is_clamped_to_valid_range():
    with patch("src.services.task_classifier.make_openai_request") as mock_request:
        mock_request.return_value = _fake_response(confidence=5.0)
        result = classify_task(messages=[{"role": "user", "content": "hi"}])

    assert result.confidence == 1.0


def test_provider_exception_fails_open():
    with patch("src.services.task_classifier.make_openai_request") as mock_request:
        mock_request.side_effect = RuntimeError("provider down")
        result = classify_task(messages=[{"role": "user", "content": "hi"}])

    assert result.task_type == "unknown"
    assert result.error == "provider down"


def test_timeout_fails_open():
    def _slow_call(*args, **kwargs):
        time.sleep(0.3)
        return _fake_response()

    with patch("src.services.task_classifier.make_openai_request", side_effect=_slow_call):
        result = classify_task(messages=[{"role": "user", "content": "hi"}], timeout_seconds=0.05)

    assert result.error == "timeout"
    assert result.task_type == "unknown"


def test_uses_configured_classifier_model():
    with (
        patch("src.services.task_classifier.make_openai_request") as mock_request,
        patch("src.services.task_classifier.Config") as mock_config,
    ):
        mock_config.TASK_CLASSIFIER_MODEL = "gpt-4o-mini"
        mock_config.TASK_CLASSIFIER_TIMEOUT_SECONDS = 5.0
        mock_request.return_value = _fake_response()

        classify_task(messages=[{"role": "user", "content": "hi"}])

    assert mock_request.call_args.kwargs["model"] == "gpt-4o-mini"


def test_uses_most_recent_user_message_in_multiturn_conversation():
    with patch("src.services.task_classifier.make_openai_request") as mock_request:
        mock_request.return_value = _fake_response()
        classify_task(
            messages=[
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
                {"role": "user", "content": "second question"},
            ]
        )

    sent_messages = mock_request.call_args.kwargs["messages"]
    assert sent_messages[-1]["content"] == "second question"


def test_flattens_multipart_text_content():
    with patch("src.services.task_classifier.make_openai_request") as mock_request:
        mock_request.return_value = _fake_response()
        classify_task(
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "part one"}],
                }
            ]
        )

    sent_messages = mock_request.call_args.kwargs["messages"]
    assert sent_messages[-1]["content"] == "part one"
