"""Validator (no-logging) mode: one test pair per persisted surface.

For every place a request leaves a trace, a validator key must leave none and
a general key must leave exactly what it always did. Each pair runs the REAL
function with only its storage client faked, so deleting the guard flips the
validator test red (mutation-checked: see the PR description).

Billing is the deliberate exception and is asserted as such: a validator key
is still debited and still gets its usage_records row, because that is the
record its credits are deducted against.
"""

from __future__ import annotations

import asyncio
import contextvars
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.services import key_purpose as kp


def _run(coro):
    """Run a coroutine on a private loop.

    Not asyncio.run(): that unsets the thread's current event loop on exit,
    which breaks later tests in the same worker that call get_event_loop().
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


VALIDATOR_USER = {"id": 77, "key_id": 9, kp.USER_FIELD: "validator"}
GENERAL_USER = {"id": 78, "key_id": 10, kp.USER_FIELD: None}


def run_in_request(user, fn, *args, **kwargs):
    """Run fn inside a fresh request context bound the way get_api_key binds it."""

    def _call():
        kp.bind_request_key_purpose(user)
        return fn(*args, **kwargs)

    return contextvars.copy_context().run(_call)


class RecordingClient:
    """Supabase-client stand-in that records every table touched and every insert."""

    def __init__(self):
        self.tables: list[str] = []
        self.inserts: list[tuple[str, dict]] = []

    def table(self, name):
        self.tables.append(name)
        chain = MagicMock(name=f"table({name})")
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.ilike.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = SimpleNamespace(data=[{"id": 123}])

        def _insert(payload):
            self.inserts.append((name, payload))
            return chain

        chain.insert.side_effect = _insert
        return chain


# --- 1. chat_completion_requests (analytics/rankings/arrivals/tag rollup) ---


class TestChatCompletionRequestsRow:
    KW = {
        "request_id": "req-1",
        "model_name": "openai/gpt-4o",
        "input_tokens": 10,
        "output_tokens": 5,
        "processing_time_ms": 120,
        "user_id": 77,
        "api_key_id": 9,
        "model_id": 42,
    }

    def _save_with_cost(self, user):
        from src.db import chat_completion_requests as ccr

        client = RecordingClient()
        with patch.object(ccr, "get_supabase_client", return_value=client):
            run_in_request(
                user,
                ccr.save_chat_completion_request_with_cost,
                cost_usd=0.01,
                input_cost_usd=0.006,
                output_cost_usd=0.004,
                metadata={"tag": "genlayer"},
                **self.KW,
            )
        return client

    def test_validator_key_writes_no_row(self):
        client = self._save_with_cost(VALIDATOR_USER)
        assert client.tables == []
        assert client.inserts == []

    def test_general_key_row_is_unchanged(self):
        client = self._save_with_cost(GENERAL_USER)
        assert [t for t, _ in client.inserts] == ["chat_completion_requests"]
        payload = client.inserts[0][1]
        assert payload["api_key_id"] == 9 and payload["metadata"] == {"tag": "genlayer"}

    def _save_plain(self, user):
        from src.db import chat_completion_requests as ccr

        client = RecordingClient()
        with patch.object(ccr, "get_supabase_client", return_value=client):
            run_in_request(user, ccr.save_chat_completion_request, **self.KW)
        return client

    def test_validator_key_writes_no_row_legacy_writer(self):
        assert self._save_plain(VALIDATOR_USER).inserts == []

    def test_general_key_row_legacy_writer(self):
        assert [t for t, _ in self._save_plain(GENERAL_USER).inserts] == [
            "chat_completion_requests"
        ]


# --- 2. activity_log --------------------------------------------------------


class TestActivityLog:
    def _log(self, user):
        from src.db import activity

        client = RecordingClient()
        with patch.object(activity, "get_supabase_client", return_value=client):
            run_in_request(
                user,
                activity.log_activity,
                user_id=user["id"],
                model="openai/gpt-4o",
                provider="openai",
                tokens=15,
                cost=0.01,
                metadata={"prompt_tokens": 10, "completion_tokens": 5},
            )
        return client

    def test_validator_key_writes_nothing(self):
        assert self._log(VALIDATOR_USER).inserts == []

    def test_general_key_is_logged(self):
        assert [t for t, _ in self._log(GENERAL_USER).inserts] == ["activity_log"]


# --- 3. chat history: non-streaming turn -----------------------------------


class TestConversationTurn:
    PROCESSED = {"choices": [{"message": {"role": "assistant", "content": "the answer"}}]}
    MESSAGES = [{"role": "user", "content": "the prompt"}]

    def _persist(self, user):
        from src.routes import chat_context

        saver = Mock(return_value={"id": 1})
        with (
            patch.object(chat_context, "get_chat_session", Mock(return_value={"id": 5})),
            patch.object(chat_context, "save_chat_message", saver),
        ):
            _run(
                chat_context.persist_conversation_turn(
                    5, False, user, self.MESSAGES, "openai/gpt-4o", self.PROCESSED, 15
                )
            )
        return saver

    def test_validator_key_stores_no_content_even_with_session_id(self):
        assert self._persist(VALIDATOR_USER).call_count == 0

    def test_general_key_stores_both_turns(self):
        saver = self._persist(GENERAL_USER)
        assert [c.args[1] for c in saver.call_args_list] == ["user", "assistant"]


# --- 4. chat history + activity + request row: streaming post-processing ---


class TestStreamPostProcessing:
    def _run(self, user):
        from src.handlers import post_processing as pp

        saver = Mock(return_value={"id": 1})
        credits = AsyncMock(return_value=(0.01, True))
        client = RecordingClient()
        with (
            patch.object(pp, "calculate_cost_async", AsyncMock(return_value=0.01)),
            patch.object(pp, "_handle_credits_and_usage_with_fallback", credits),
            patch.object(pp, "increment_api_key_usage", Mock()),
            patch.object(pp, "_record_inference_metrics_and_health", AsyncMock()),
            patch.object(pp, "capture_model_health", AsyncMock()),
            patch.object(pp, "get_chat_session", Mock(return_value={"id": 5})),
            patch.object(pp, "save_chat_message", saver),
            patch.object(pp, "get_provider_from_model", Mock(return_value="openai")),
            patch("src.db.activity.get_supabase_client", return_value=client),
            patch("src.db.chat_completion_requests.get_supabase_client", return_value=client),
            patch("src.db.chat_completion_requests.get_model_id_by_name", return_value=42),
            patch(
                "src.services.pricing.get_model_pricing",
                Mock(return_value={"prompt": 0.001, "completion": 0.002}),
            ),
        ):
            # Leaf writers are the REAL ones, bound into post_processing at import.
            run_in_request(
                user,
                _run,
                pp._process_stream_completion_background(
                    user=user,
                    api_key="gw_live_x",
                    model="openai/gpt-4o",
                    trial={"is_trial": False},
                    environment_tag="live",
                    session_id=5,
                    messages=[{"role": "user", "content": "the prompt"}],
                    accumulated_content="the answer",
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    elapsed=0.5,
                    provider="openai",
                    request_id="req-s",
                    api_key_id=9,
                ),
            )
        return saver, credits, client

    def test_validator_key_is_billed_and_nothing_else(self):
        saver, credits, client = self._run(VALIDATOR_USER)
        assert credits.await_count == 1  # billing still happens
        assert saver.call_count == 0  # no chat history
        assert client.inserts == []  # no activity_log, no chat_completion_requests

    def test_general_key_keeps_every_write(self):
        saver, credits, client = self._run(GENERAL_USER)
        assert credits.await_count == 1
        assert saver.call_count == 2
        assert sorted(t for t, _ in client.inserts) == [
            "activity_log",
            "chat_completion_requests",
        ]


# --- 5. per-request audit line (client IP + user agent) + context binding ---


class TestAuthAuditLine:
    def _auth(self, user):
        from src.security import deps

        audit = Mock()
        request = SimpleNamespace(
            client=SimpleNamespace(host="203.0.113.9"),
            headers={"user-agent": "GenVM/1.0"},
            url=SimpleNamespace(path="/v1/chat/completions"),
        )
        creds = SimpleNamespace(credentials="gw_live_validatorkey")

        async def call():
            await deps.get_api_key(creds, request)
            return kp.validator_mode_active()

        with (
            patch.object(deps, "validate_api_key_security", return_value="gw_live_validatorkey"),
            patch.object(deps, "get_user", return_value=dict(user)),
            patch.object(deps.audit_logger, "log_api_key_usage", audit),
        ):
            bound = contextvars.copy_context().run(_run, call())
        return audit, bound

    def test_validator_key_no_ip_line_and_context_bound(self):
        audit, bound = self._auth(VALIDATOR_USER)
        assert audit.call_count == 0
        assert bound is True

    def test_general_key_logged_as_before(self):
        audit, bound = self._auth(GENERAL_USER)
        audit.assert_called_once()
        assert audit.call_args.kwargs["ip_address"] == "203.0.113.9"
        assert bound is False


# --- 6. the purpose reaches the user dict from the KEY row ------------------


class TestUserLookupCarriesPurpose:
    def _lookup(self, key_row_purpose):
        from src.db import users

        key_row = {
            "id": 9,
            "user_id": 77,
            "is_active": True,
            "key_name": "k",
            "purpose": key_row_purpose,
        }

        class Client:
            def table(self, name):
                chain = MagicMock()
                chain.select.return_value = chain
                chain.eq.return_value = chain
                data = [key_row] if name == "api_keys_new" else [{"id": 77}]
                chain.execute.return_value = SimpleNamespace(data=data)
                return chain

        with (
            patch.object(users, "get_supabase_client", return_value=Client()),
            patch.object(users, "_migrate_legacy_credit_balance", Mock()),
        ):
            return users._get_user_uncached("gw_live_lookup")

    def test_validator(self):
        assert kp.is_validator_key(self._lookup("validator"))

    @pytest.mark.parametrize("purpose", [None, "general"])
    def test_general(self, purpose):
        assert not kp.is_validator_key(self._lookup(purpose))


# --- 7. billing is the deliberate exception ---------------------------------


class TestBillingStillRecorded:
    def test_usage_records_row_written_for_validator_key(self):
        """usage_records (user, key id, model, tokens, cost, timestamp) is the
        billing record; validator mode must NOT suppress it."""
        from src.db import users

        client = RecordingClient()
        with (
            patch.object(users, "get_supabase_client", return_value=client),
            patch.object(users, "get_api_key_by_key", Mock(return_value={"id": 9})),
        ):
            run_in_request(
                VALIDATOR_USER, users.record_usage, 77, "gw_live_x", "openai/gpt-4o", 15, 0.01
            )
        assert [t for t, _ in client.inserts] == ["usage_records"]
        row = client.inserts[0][1]
        assert {k: row[k] for k in ("user_id", "api_key_id", "model", "tokens_used", "cost")} == {
            "user_id": 77,
            "api_key_id": 9,
            "model": "openai/gpt-4o",
            "tokens_used": 15,
            "cost": 0.01,
        }
