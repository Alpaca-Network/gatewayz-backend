"""/v1/messages must hand Anthropic SDKs a readable error, not a JSON blob.

Production returned the gateway's whole `detail` dict json.dumps'd INTO the
Anthropic envelope's `message` field, so an SDK traceback read:

    {"type":"error","error":{"type":"api_error",
     "message":"{\\"error\\": {\\"message\\": \\"Pricing for model ...\\"}}"}}
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from src.services.anthropic_messages import errors as anthropic_errors


def test_gate_detail_is_flattened_not_double_encoded():
    exc = HTTPException(
        status_code=400,
        detail={
            "error": {
                "message": "Model 'nope' does not exist.",
                "type": "invalid_request_error",
                "code": "model_not_found",
            }
        },
    )
    message = anthropic_errors.detail_message(exc)
    assert message == "Model 'nope' does not exist."
    with pytest.raises(json.JSONDecodeError):
        json.loads(message)


def test_plain_string_detail_passes_through():
    exc = HTTPException(status_code=400, detail="plain text reason")
    assert anthropic_errors.detail_message(exc) == "plain text reason"


def test_detail_without_a_message_falls_back_to_a_string():
    exc = HTTPException(status_code=500, detail={"unexpected": "shape"})
    assert anthropic_errors.detail_message(exc)


def test_detail_code_is_extracted_when_present():
    exc = HTTPException(
        status_code=400,
        detail={"error": {"message": "m", "code": "model_ambiguous"}},
    )
    assert anthropic_errors.detail_code(exc) == "model_ambiguous"
    assert anthropic_errors.detail_code(HTTPException(status_code=400, detail="x")) is None


def test_model_not_found_maps_to_anthropic_invalid_request_error():
    assert anthropic_errors.anthropic_error_type(400, "model_not_found") == ("invalid_request_error")


def test_pricing_not_configured_is_not_advertised_as_retryable():
    # Anthropic SDKs retry overloaded_error; a missing pricing row is
    # deterministic, so retrying just multiplies the failure.
    assert anthropic_errors.anthropic_error_type(503, "pricing_not_configured") == "api_error"


def test_genuine_upstream_capacity_503_stays_overloaded_error():
    assert anthropic_errors.anthropic_error_type(503, None) == "overloaded_error"


def test_unmapped_status_falls_back_to_api_error():
    assert anthropic_errors.anthropic_error_type(418, None) == "api_error"
