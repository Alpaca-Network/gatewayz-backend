"""Gateway errors in Anthropic's error envelope.

Anthropic SDKs only understand ``{"type": "error", "error": {"type", "message"}}``
and decide whether to retry from ``error.type``, so every error leaving
``/v1/messages`` goes through here.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import HTTPException


def anthropic_error(status_code: int, error_type: str, message: str) -> HTTPException:
    """Errors in Anthropic's envelope, so Anthropic SDKs parse them correctly."""
    return HTTPException(
        status_code=status_code,
        detail={"type": "error", "error": {"type": error_type, "message": message}},
    )


def detail_message(exc: HTTPException) -> str:
    """The human-readable string out of an HTTPException detail.

    Our gates raise `detail={"error": {"message": ..., "code": ...}}`. Passing
    that dict through `json.dumps` put a JSON blob inside the Anthropic
    envelope's own `message` field — unreadable in an SDK traceback.
    """
    detail = exc.detail
    if isinstance(detail, dict):
        inner = detail.get("error")
        if isinstance(inner, dict) and inner.get("message"):
            return str(inner["message"])
        if detail.get("message"):
            return str(detail["message"])
        return json.dumps(detail)
    return str(detail) if detail else "request failed"


# Any URL except our own: a top-up or docs link in a gateway message is kept.
_URL_RE = re.compile(r"https?://(?![\w.-]*gatewayz\.ai)\S+")
_GENERIC_UPSTREAM_ERROR = "The model provider returned an error."
_GENERIC_GATEWAY_ERROR = "The gateway could not process the provider's response."


def safe_error_message(message: Any) -> str:
    """An error message with internal details removed.

    Provider and library errors reach this route as raw exception text: the
    upstream URL from an HTTP client error, or a validation dump naming our
    internal classes. Neither helps the caller and both describe our internals.
    """
    text = str(message or "").strip()
    if not text:
        return _GENERIC_UPSTREAM_ERROR
    if "validation error" in text and " for " in text:
        return _GENERIC_GATEWAY_ERROR
    lines = [
        line
        for line in text.splitlines()
        if not line.strip().lower().startswith("for more information")
    ]
    text = _URL_RE.sub("", " ".join(lines))
    text = re.sub(r"\s+for url\s*'?'?", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" :'")
    if not text:
        return _GENERIC_UPSTREAM_ERROR
    return text[:500]


_PLAN_LIMIT_PREFIX = "Plan limit exceeded:"


def is_plan_period_limit(status_code: int, message: str) -> bool:
    """True for a spent daily or monthly plan allowance.

    These arrive as 429, which Anthropic clients retry with backoff. Retrying
    cannot help before the period resets, so the caller is told once instead of
    waiting through minutes of retries. Per-minute limits stay 429.
    """
    if status_code != 429 or not message.startswith(_PLAN_LIMIT_PREFIX):
        return False
    lowered = message.lower()
    return "daily" in lowered or "monthly" in lowered


def plan_limit_message(message: str) -> str:
    reason = message[len(_PLAN_LIMIT_PREFIX) :].strip()
    return (
        f"Gatewayz plan limit reached: {reason}. Retrying will not help until the "
        "limit resets or the plan is raised."
    )


def detail_code(exc: HTTPException) -> str | None:
    """The gateway error code (`model_not_found`, ...) when the detail carries one."""
    detail = exc.detail
    if isinstance(detail, dict):
        inner = detail.get("error")
        if isinstance(inner, dict):
            return inner.get("code")
    return None


def anthropic_error_type(status_code: int, code: str | None) -> str:
    """Map our status + error code onto an Anthropic error type."""
    base = {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_error",
        404: "not_found_error",
        429: "rate_limit_error",
        # Provider budget exhaustion is 402 and terminal: an SDK must not
        # retry it, and an operator must be told to top up rather than wait.
        402: "invalid_request_error",
        # 503 stays overloaded_error for GENUINE upstream capacity, which is
        # the one case where retrying is the right advice.
        503: "overloaded_error",
    }.get(status_code, "api_error")
    # A 503 for missing pricing config is deterministic, not transient —
    # don't invite SDK retries on it.
    if base == "overloaded_error" and code == "pricing_not_configured":
        return "api_error"
    return base


# Gateway/stream error types -> Anthropic SSE error types
# (https://docs.anthropic.com/en/api/errors). Anything unmapped is an api_error.
STREAM_ERROR_TYPE_MAP = {
    "rate_limit_error": "rate_limit_error",
    "plan_limit_exceeded": "rate_limit_error",
    "auth_error": "authentication_error",
    "not_found_error": "not_found_error",
    # NOT overloaded_error. `capacity_error` is emitted from exactly one place --
    # chat_streaming's is_provider_budget_error() branch -- so it always means a
    # spending limit on OUR provider account, never genuine upstream capacity.
    # overloaded_error is the one Anthropic type whose documented advice is to
    # back off and retry, which is the advice this condition can never satisfy.
    #
    # This is #2355's defect coming through the streaming door: that PR moved the
    # non-stream path from 503 to a terminal 402, and the mid-stream event for the
    # identical cause kept telling the SDK to retry.
    "capacity_error": "invalid_request_error",
}

# Error-shaped chunks that are advisories emitted AFTER content was fully
# delivered (chat_streaming continues to [DONE] after them). Terminating on
# these would discard a delivered-and-billed response.
#
# plan_limit_exceeded is the post-usage check in chat_streaming: this response
# crossed the plan allowance, and it was already delivered and billed. The NEXT
# request is refused before it starts; this one must end normally.
NON_FATAL_STREAM_ERROR_TYPES = frozenset({"stream_normalization_warning", "plan_limit_exceeded"})


def to_anthropic_error(exc: HTTPException) -> HTTPException:
    """A gateway error from the chat pipeline, in Anthropic's envelope.

    A spent daily or monthly plan allowance becomes a 402 (SDKs do not retry
    it); everything else keeps its status with an Anthropic error type and a
    message cleaned of internal details.
    """
    message = detail_message(exc)
    if is_plan_period_limit(exc.status_code, message):
        return anthropic_error(402, "invalid_request_error", plan_limit_message(message))
    return anthropic_error(
        exc.status_code,
        anthropic_error_type(exc.status_code, detail_code(exc)),
        safe_error_message(message),
    )
