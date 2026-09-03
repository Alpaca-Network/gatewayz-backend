"""End-to-end coverage for W-C's internal-channel fixes (M3, #2258 #2260):

1. Server-minted billing_ref — set by RequestIDMiddleware, sourced by
   chat.py's _resolve_billing_ref, and the idempotency invariant that
   the same billing_ref used twice produces exactly one deduction.
2. Sentry: no email/client IP/bodies in what gets sent.
3. chat_completion_requests.error_message never echoes raw exception text.
6. credit_transactions/activity_log free-form columns never carry content.

See docs/security/ANONYMITY_THREAT_MODEL.md §5 (L5-L8) and §6 for the
canonical sentinel values and what each test must prove.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.db.users import deduct_credits
from src.handlers.error_persistence import format_error_for_persistence
from src.middleware.auto_sentry_middleware import AutoSentryMiddleware
from src.middleware.request_id_middleware import RequestIDMiddleware
from src.routes.chat import _resolve_billing_ref
from src.utils.sentry_scrub import strip_sensitive_event

# Sentinel values from the threat model's canary vocabulary.
SENTINEL_USER_ID = 424242
SENTINEL_EMAIL = "canary-424242@example.test"
SENTINEL_CLIENT_IP = "203.0.113.77"
SENTINEL_PROMPT_FRAGMENT = "canary prompt fragment 424242"
CLIENT_REQUEST_ID = "client-controlled-canary-id"


# ============================================================================
# 1. Billing ref: server-minted, not the client X-Request-ID; idempotent.
# ============================================================================


class TestBillingRefEndToEnd:
    def test_middleware_mints_billing_ref_independent_of_client_request_id(self):
        """RequestIDMiddleware -> chat._resolve_billing_ref: the value chat.py
        uses for billing idempotency/persistence must never equal, or be
        derived from, the client-supplied X-Request-ID."""
        app = FastAPI()
        app.add_middleware(RequestIDMiddleware)

        @app.get("/probe")
        async def probe(request: Request):
            return {"billing_ref": _resolve_billing_ref(request)}

        response = TestClient(app).get("/probe", headers={"X-Request-ID": CLIENT_REQUEST_ID})

        billing_ref = response.json()["billing_ref"]
        header_ref = response.headers["X-Gatewayz-Request-Id"]

        assert billing_ref != CLIENT_REQUEST_ID
        assert CLIENT_REQUEST_ID not in billing_ref
        assert billing_ref == header_ref

    def test_idempotency_same_billing_ref_twice_yields_one_deduction(self):
        """The idempotency check (get_transaction_by_request_id) must
        short-circuit deduct_credits before touching the database — this is
        the mechanism that makes 'same billing_ref twice -> one deduction'
        hold (the DB unique index on credit_transactions.request_id is the
        second, storage-level layer; this proves the app-level guard)."""
        existing_transaction = {"id": 99, "amount": -0.02}

        with (
            patch(
                "src.db.credit_transactions.get_transaction_by_request_id",
                return_value=existing_transaction,
            ),
            patch("src.db.users.get_supabase_client") as mock_get_client,
        ):
            deduct_credits(api_key="gw_test_key", tokens=0.02, request_id="billing-ref-canary")

        # Skipped entirely — no second deduction attempt reaches the DB layer.
        mock_get_client.assert_not_called()

    def test_idempotency_guard_does_not_skip_a_genuinely_new_request(self):
        """Companion to the above: proves the guard only skips when a prior
        transaction actually exists — it isn't a no-op that always returns
        early regardless of request_id."""
        with (
            patch(
                "src.db.credit_transactions.get_transaction_by_request_id",
                return_value=None,
            ),
            patch("src.db.users.get_supabase_client") as mock_get_client,
        ):
            mock_get_client.side_effect = RuntimeError("reached the DB layer")
            with pytest.raises(RuntimeError, match="reached the DB layer"):
                deduct_credits(api_key="gw_test_key", tokens=0.02, request_id="new-billing-ref")

        mock_get_client.assert_called_once()


# ============================================================================
# 2. Sentry: no email / client IP / bodies.
# ============================================================================


class TestSentryScopeEndToEnd:
    def test_scope_built_from_a_sentinel_carrying_request_has_no_email_or_ip(self):
        """Drive AutoSentryMiddleware's extraction against a scope carrying
        every threat-model sentinel, then run the resulting event through the
        real before_send hook — neither layer may let email/IP through."""
        middleware = AutoSentryMiddleware(app=MagicMock())

        class _State:
            user_id = SENTINEL_USER_ID
            email = SENTINEL_EMAIL
            billing_ref = "billing-ref-for-sentry-test"

        scope = {
            "type": "http",
            "path": "/v1/chat/completions",
            "method": "POST",
            "state": _State(),
            "headers": [(b"authorization", b"Bearer gw_live_canary")],
            "client": (SENTINEL_CLIENT_IP, 443),
            "query_string": b"",
        }

        request_context = middleware._extract_request_context(scope)
        user_context = middleware._extract_user_context(scope)

        assert SENTINEL_CLIENT_IP not in str(request_context)
        assert "client_host" not in request_context
        assert user_context == {"id": SENTINEL_USER_ID}
        assert SENTINEL_EMAIL not in str(user_context)

        # Second, independent layer: before_send scrubbing on a raw event that
        # (hypothetically) still carried a body/cookie/long exception text.
        event = {
            "user": user_context,
            "request": {
                "data": {"prompt": SENTINEL_PROMPT_FRAGMENT},
                "cookies": {"session": "abc"},
                "headers": {"Authorization": "Bearer gw_live_canary"},
            },
            "exception": {"values": [{"type": "ValueError", "value": "x" * 5000}]},
        }
        scrubbed = strip_sensitive_event(event, {})

        assert "data" not in scrubbed["request"]
        assert "cookies" not in scrubbed["request"]
        assert "Authorization" not in scrubbed["request"]["headers"]
        assert len(scrubbed["exception"]["values"][0]["value"]) <= 300
        # user context is untouched by before_send (already minimal) but still
        # carries no email — belt-and-suspenders check.
        assert "email" not in scrubbed["user"]


# ============================================================================
# 3. error_message never echoes raw exception/prompt content.
# ============================================================================


class TestErrorMessageScrubbingEndToEnd:
    def test_sentinel_prompt_fragment_never_persisted(self):
        """Exactly the threat model's canary scenario: an exception whose
        message contains a sentinel prompt fragment must not have that
        fragment reach chat_completion_requests.error_message."""
        err = ValueError(f"could not parse request: {SENTINEL_PROMPT_FRAGMENT}")
        persisted = format_error_for_persistence(err)
        assert SENTINEL_PROMPT_FRAGMENT not in persisted

    def test_sentinel_email_and_ip_never_persisted_via_generic_exception(self):
        err = RuntimeError(f"failed for user {SENTINEL_EMAIL} from {SENTINEL_CLIENT_IP}")
        persisted = format_error_for_persistence(err)
        assert SENTINEL_EMAIL not in persisted
        assert SENTINEL_CLIENT_IP not in persisted


# ============================================================================
# 6. Free-form columns (credit_transactions/activity_log) never carry content.
# ============================================================================


class TestFreeFormColumnsEndToEnd:
    def test_credit_transactions_metadata_drops_sentinel_content(self):
        from src.db.credit_transactions import _sanitize_metadata

        sanitized = _sanitize_metadata(
            {"model": "gpt-4o", "prompt": SENTINEL_PROMPT_FRAGMENT, "error_message": "leak"}
        )
        assert sanitized == {"model": "gpt-4o"}

    def test_activity_log_metadata_drops_sentinel_content(self):
        from src.db.activity import _sanitize_metadata

        sanitized = _sanitize_metadata(
            {"prompt_tokens": 10, "prompt": SENTINEL_PROMPT_FRAGMENT}
        )
        assert sanitized == {"prompt_tokens": 10}
