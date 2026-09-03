"""_resolve_billing_ref must source chat_completions' internal request_id from
request.state.billing_ref (server-minted by RequestIDMiddleware), never from
anything client-controlled — threat model L7/G4."""

import uuid
from types import SimpleNamespace

from src.routes.chat import _resolve_billing_ref


def test_uses_billing_ref_from_request_state():
    ref = str(uuid.uuid4())
    request = SimpleNamespace(state=SimpleNamespace(billing_ref=ref))
    assert _resolve_billing_ref(request) == ref


def test_falls_back_to_fresh_uuid_when_request_is_none():
    resolved = _resolve_billing_ref(None)
    assert uuid.UUID(resolved)  # valid UUID string


def test_falls_back_to_fresh_uuid_when_state_missing_billing_ref():
    request = SimpleNamespace(state=SimpleNamespace())
    resolved = _resolve_billing_ref(request)
    assert uuid.UUID(resolved)


def test_two_fallback_calls_differ():
    """Fallback must not be a fixed/predictable value."""
    assert _resolve_billing_ref(None) != _resolve_billing_ref(None)
