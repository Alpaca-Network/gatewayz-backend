"""Privacy regression tests for AutoSentryMiddleware (threat model G5/L5):

- Sentry request context must never include the client's real IP.
- Sentry user context must be {"id": user_id} only — never email.
- api_key_hash and billing_ref are extracted independently, as tag material,
  not folded into the user object.

Unit-tested directly against the extraction methods (matching the existing
_determine_endpoint_type/_categorize_http_error tests in
test_auto_sentry_middleware.py) rather than through TestClient: the ASGI
middleware reads scope state before calling downstream app, so request.state
set inside a route handler is not yet visible at extraction time — these
methods are the real unit under test regardless.
"""

from unittest.mock import Mock

from src.middleware.auto_sentry_middleware import AutoSentryMiddleware


def _middleware():
    return AutoSentryMiddleware(app=Mock())


def _scope(state=None, headers=None):
    return {
        "type": "http",
        "path": "/boom",
        "method": "GET",
        "state": state,
        "headers": headers or [],
        "client": ("203.0.113.77", 12345),
        "query_string": b"",
    }


class _State:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_request_context_has_no_client_host():
    middleware = _middleware()
    context = middleware._extract_request_context(_scope())
    assert "client_host" not in context


def test_user_context_is_id_only_no_email():
    middleware = _middleware()
    scope = _scope(state=_State(user_id=424242, email="canary-424242@example.test"))
    user_context = middleware._extract_user_context(scope)
    assert user_context == {"id": 424242}


def test_user_context_none_when_unauthenticated():
    middleware = _middleware()
    scope = _scope(state=_State())
    assert middleware._extract_user_context(scope) is None


def test_billing_ref_extracted_as_tag_material():
    middleware = _middleware()
    scope = _scope(state=_State(billing_ref="billing-ref-canary"))
    assert middleware._extract_billing_ref(scope) == "billing-ref-canary"


def test_billing_ref_none_when_absent():
    middleware = _middleware()
    scope = _scope(state=_State())
    assert middleware._extract_billing_ref(scope) is None


def test_api_key_hash_extracted_independent_of_user_object():
    middleware = _middleware()
    headers = [(b"authorization", b"Bearer gw_live_secret")]
    scope = _scope(state=_State(user_id=1, email="user@example.test"), headers=headers)

    api_key_hash = middleware._extract_api_key_hash(scope)
    user_context = middleware._extract_user_context(scope)

    assert api_key_hash is not None
    assert len(api_key_hash) == 16
    # Never leaked into the user object itself.
    assert "api_key_hash" not in user_context
    assert "email" not in user_context
    assert user_context == {"id": 1}
