"""Tests for src.security.inference_gates.enforce_community_auth_gate
(gatewayz-backend#2262 #2265, M4 spec §1: community requires auth).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.security.inference_gates import enforce_community_auth_gate


def test_anonymous_community_request_is_rejected():
    with pytest.raises(HTTPException) as exc:
        enforce_community_auth_gate(True, model_id="community/llama-3.1-8b-instruct")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"]["code"] == "community_requires_auth"


def test_authenticated_community_request_is_allowed():
    enforce_community_auth_gate(False, model_id="community/llama-3.1-8b-instruct")  # no raise


def test_anonymous_non_community_request_is_allowed():
    enforce_community_auth_gate(True, model_id="openrouter/auto")  # no raise
    enforce_community_auth_gate(True, model_id="gpt-4o-mini")  # no raise


def test_anonymous_with_no_model_id_is_allowed():
    # Other gates (pricing, etc.) own rejecting a missing model id; this gate
    # only cares about the community/ prefix.
    enforce_community_auth_gate(True, model_id=None)  # no raise


def test_gate_is_independent_of_anonymous_enabled_flag(monkeypatch):
    # Unlike enforce_anonymous_gate, this blocks community for anonymous
    # callers regardless of Config.ANONYMOUS_ENABLED.
    from src.config import Config

    monkeypatch.setattr(Config, "ANONYMOUS_ENABLED", True, raising=False)
    with pytest.raises(HTTPException) as exc:
        enforce_community_auth_gate(True, model_id="community/some-model")
    assert exc.value.status_code == 403

    monkeypatch.setattr(Config, "ANONYMOUS_ENABLED", False, raising=False)
    with pytest.raises(HTTPException) as exc:
        enforce_community_auth_gate(True, model_id="community/some-model")
    assert exc.value.status_code == 403


def test_prefix_match_requires_exact_slash():
    # "communityx/..." must not match "community/" prefix.
    enforce_community_auth_gate(True, model_id="communityx/some-model")  # no raise
