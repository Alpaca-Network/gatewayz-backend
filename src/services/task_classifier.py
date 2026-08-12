"""
Task Classifier.

Judges the parts of a routing decision that cannot be determined
deterministically from the request shape: the task category (one of
`quality_inference.TASK_TYPES`) and whether the request needs multi-step
reasoning. Tool/vision needs are already known for certain from the request
itself — see `capability_requirements.extract_required_capabilities` — so
this module composes with that instead of re-guessing them via an LLM call,
which would be strictly less reliable and more expensive for no benefit.

This is new infrastructure: no part of the codebase previously made an LLM
call for an internal, non-user-facing purpose (see gatewayz-backend#2213
investigation). The confidence score is the model's own self-report, not a
calibrated probability — real calibration against outcomes is a Phase 5
(rollout safety / eval harness) concern, not this module's.

Key properties:
  * Fail-open: any parse/timeout/API failure returns a neutral classification
    (task_type="unknown", confidence=0.0, error set) rather than raising, so
    a caller can safely skip auto-routing instead of blocking the request.
  * Synchronous, matching `make_openai_request`'s own style and the existing
    provider-client test-mocking convention (patch + MagicMock, no AsyncMock
    precedent exists in tests/services/). An async caller should wrap this
    with `asyncio.to_thread`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from openai import APITimeoutError

from src.config import Config
from src.services.capability_requirements import extract_required_capabilities
from src.services.providers.openai_client import make_openai_request
from src.services.quality_inference import TASK_TYPES

logger = logging.getLogger(__name__)

_VALID_TASK_TYPES = frozenset(TASK_TYPES)

_SYSTEM_PROMPT = (
    "Classify the user's LAST message. Respond with ONLY a JSON object of the form "
    '{"task_type": "<one of: ' + ", ".join(TASK_TYPES) + '>", '
    '"needs_reasoning": true|false, "confidence": <0.0-1.0>}. '
    "needs_reasoning is true only for requests that require multi-step reasoning, "
    "math, or planning to answer correctly."
)


@dataclass(frozen=True)
class TaskClassification:
    """Task category + required capabilities + confidence for one request."""

    task_type: str = "unknown"
    capability_names: frozenset[str] = field(default_factory=frozenset)
    confidence: float = 0.0
    error: str | None = None


def _last_user_text(messages: list[dict[str, Any]] | None) -> str:
    """The most recent user message's text, flattening multi-part content."""
    for message in reversed(messages or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            if parts:
                return " ".join(parts)
    return ""


def _parse_classification(raw_content: str | None) -> tuple[str, bool, float]:
    """Parse the model's JSON reply. Any malformed field falls back safely."""
    try:
        data = json.loads(raw_content or "")
    except (TypeError, ValueError):
        return "unknown", False, 0.0

    if not isinstance(data, dict):
        return "unknown", False, 0.0

    task_type = data.get("task_type")
    if task_type not in _VALID_TASK_TYPES:
        task_type = "unknown"

    needs_reasoning = bool(data.get("needs_reasoning"))

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return task_type, needs_reasoning, confidence


def classify_task(
    messages: list[dict[str, Any]] | None,
    tools: list[dict[str, Any]] | None = None,
    response_format: dict[str, Any] | None = None,
    timeout_seconds: float | None = None,
) -> TaskClassification:
    """
    Classify a chat request's task type and required capabilities.

    Combines the deterministic capability signals from
    `extract_required_capabilities` (tools/vision — no LLM call needed) with
    an LLM judgment of task_type and whether the request needs multi-step
    reasoning, the two signals that genuinely require semantic understanding.

    Args:
        messages: the request's message list.
        tools: the request's tool/function definitions, if any.
        response_format: the request's response_format field, if any.
        timeout_seconds: hard wall-clock ceiling for the classification call.
            Defaults to Config.TASK_CLASSIFIER_TIMEOUT_SECONDS.

    Returns:
        A TaskClassification. On any failure (timeout, API error, malformed
        response, or no user text to classify) returns a fallback with
        `error` set and the deterministic capabilities still populated —
        never raises.
    """
    deterministic = extract_required_capabilities(
        messages=messages, tools=tools, response_format=response_format
    )
    timeout = (
        timeout_seconds if timeout_seconds is not None else Config.TASK_CLASSIFIER_TIMEOUT_SECONDS
    )

    user_text = _last_user_text(messages)
    if not user_text:
        return TaskClassification(
            capability_names=deterministic.capability_names, error="no_user_text"
        )

    try:
        # `timeout` is forwarded to the OpenAI SDK's own httpx client, which
        # actually aborts the connection when it elapses — unlike wrapping
        # this call in a ThreadPoolExecutor.result(timeout=...), which would
        # stop waiting but leave the request and its worker thread running.
        response = make_openai_request(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            model=Config.TASK_CLASSIFIER_MODEL,
            temperature=0,
            max_tokens=100,
            response_format={"type": "json_object"},
            timeout=timeout,
        )
        raw_content = response.choices[0].message.content
    except APITimeoutError:
        logger.warning(f"task_classifier: classification timed out after {timeout}s")
        return TaskClassification(capability_names=deterministic.capability_names, error="timeout")
    except Exception as e:
        logger.warning(f"task_classifier: classification failed: {e}")
        return TaskClassification(capability_names=deterministic.capability_names, error=str(e))

    task_type, needs_reasoning, confidence = _parse_classification(raw_content)

    capability_names = set(deterministic.capability_names)
    if needs_reasoning:
        capability_names.add("reasoning")

    return TaskClassification(
        task_type=task_type,
        capability_names=frozenset(capability_names),
        confidence=confidence,
    )
