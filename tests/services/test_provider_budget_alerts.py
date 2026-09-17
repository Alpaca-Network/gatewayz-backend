"""Provider credit exhaustion becomes an operator-visible fact.

The incident this covers (2026-09-16): an unfunded Anthropic key took all 11 of its
models down in production and nobody knew, because ``is_provider_budget_error()``
correctly hides our billing state from users and nothing put it anywhere else. These
tests pin the three properties that make the replacement signal trustworthy:

1. Every detection site records.
2. Nothing derived from the upstream error text is ever recorded or returned.
3. A broken recorder cannot fail a user's request.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import src.services.provider_budget_alerts as alerts
from src.db.provider_budget_events import ProviderBudgetEventsUnavailable
from src.services.provider_failover import map_provider_error
from src.utils.errors import (
    PROVIDER_BUDGET_REASONS,
    classify_provider_budget_error,
    is_provider_budget_error,
)

# Captured at import, before tests/conftest.py's autouse fixture stubs the module
# attribute, so one test can still exercise the real background dispatch.
_REAL_SUBMIT = alerts._submit

# The real OpenRouter spend-limit error. The 64-char hex is a key id and the URL is a
# dashboard link -- the two things that must never be persisted.
REAL_402 = (
    "Error code: 402 - {'error': {'message': \"This request requires more credits, or fewer "
    "max_tokens. You requested up to 2000 tokens, but can only afford 897. To increase, visit "
    "https://openrouter.ai/workspaces/default/keys/"
    "f001429593544cd92610592c96fee5e341f53e759e3f07aa5089c82159c5ed03 and adjust the key's "
    "weekly limit\", 'code': 402}}"
)
KEY_ID = "f001429593544cd92610592c96fee5e341f53e759e3f07aa5089c82159c5ed03"

# The real Anthropic unfunded-account error: an HTTP 400 whose text is the only signal.
REAL_ANTHROPIC = (
    "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
    "'message': 'Your credit balance is too low to access the Anthropic API. "
    "Please go to Plans & Billing to upgrade or purchase credits.'}}"
)


@pytest.fixture
def flushes(monkeypatch):
    """Run the background write synchronously and capture every flush as a dict."""
    captured: list[dict] = []

    def _sync_submit(fn, *args):
        provider, reason, sample_model, count = args
        captured.append(
            {
                "provider": provider,
                "reason": reason,
                "sample_model": sample_model,
                "count": count,
            }
        )

    monkeypatch.setattr(alerts, "_submit", _sync_submit)
    return captured


# --------------------------------------------------------------------------------------
# Reason classification
# --------------------------------------------------------------------------------------


class TestReasonClassification:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (REAL_402, "credit_balance_low"),
            (REAL_ANTHROPIC, "credit_balance_low"),
            ("Your credit balance is too low", "credit_balance_low"),
            ("This request requires more credits", "credit_balance_low"),
            ("you can only afford 12 tokens", "credit_balance_low"),
            ("Error code: 429 - {'code': 'insufficient_quota'}", "quota_exhausted"),
            ("please adjust the key's weekly limit", "spend_limit_reached"),
            ("402 Payment Required", "payment_required"),
            ("Error code: 402 - upstream said nothing useful", "payment_required"),
        ],
    )
    def test_specific_causes_are_named(self, raw, expected):
        assert classify_provider_budget_error(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [None, "", "some random error", "Error code: 429 - rate limited", "connection reset"],
    )
    def test_non_budget_errors_classify_to_none(self, raw):
        assert classify_provider_budget_error(raw) is None

    def test_rate_limiting_is_not_a_budget_reason(self):
        # A 429 is a throughput limit that clears on its own; this surface exists for
        # conditions that need a human to spend money. Folding them together would make
        # "degraded" mean nothing.
        assert "rate_limited" not in PROVIDER_BUDGET_REASONS
        assert classify_provider_budget_error("Error code: 429 - too many requests") is None

    @pytest.mark.parametrize(
        "raw",
        [REAL_402, REAL_ANTHROPIC, "insufficient_quota", "payment required", "weekly limit"],
    )
    def test_every_reason_is_a_member_of_the_closed_set(self, raw):
        assert classify_provider_budget_error(raw) in PROVIDER_BUDGET_REASONS

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            REAL_402,
            REAL_ANTHROPIC,
            "insufficient_quota",
            "Error code: 402 - x",
            "adjust the key",
            "Error code: 429 - rate limited",
            "unrelated failure",
        ],
    )
    def test_detector_and_classifier_cannot_disagree(self, raw):
        # is_provider_budget_error() is defined in terms of the classifier precisely so a
        # future pattern can't be added to one and missed by the other.
        assert is_provider_budget_error(raw) is (classify_provider_budget_error(raw) is not None)


# --------------------------------------------------------------------------------------
# The upstream text never escapes
# --------------------------------------------------------------------------------------


class TestNoUpstreamTextIsRecorded:
    @pytest.mark.parametrize("raw", [REAL_402, REAL_ANTHROPIC])
    def test_recorded_payload_contains_no_slice_of_the_raw_error(self, flushes, raw):
        alerts.record_provider_budget_error("openrouter", "anthropic/claude-sonnet-5", raw)

        assert len(flushes) == 1
        blob = " ".join(str(v) for v in flushes[0].values())
        assert KEY_ID not in blob
        assert "openrouter.ai" not in blob
        assert "http" not in blob
        assert "credit balance is too low" not in blob
        assert "Plans & Billing" not in blob
        # What it *does* carry is specific, and every field is ours.
        assert flushes[0]["reason"] == "credit_balance_low"
        assert flushes[0]["sample_model"] == "anthropic/claude-sonnet-5"

    def test_an_unrecognized_reason_is_coerced_not_passed_through(self, flushes):
        # The migration deliberately carries no CHECK constraint, so this coercion is the
        # only thing standing between a future classifier bug and raw text in a column.
        with patch.object(
            alerts,
            "classify_provider_budget_error",
            return_value="raw upstream text https://x/keys/" + KEY_ID,
        ):
            alerts.record_provider_budget_error("openrouter", "m", REAL_402)

        assert flushes[0]["reason"] == "unknown"
        assert KEY_ID not in flushes[0]["reason"]

    def test_the_user_facing_402_still_leaks_nothing(self):
        # The masking this feature was built around must be untouched by it.
        result = map_provider_error("openrouter", "anthropic/claude-sonnet-5", Exception(REAL_402))
        detail = str(result.detail)
        assert result.status_code == 402
        assert KEY_ID not in detail
        assert "openrouter.ai" not in detail
        assert "credit balance" not in detail.lower()


# --------------------------------------------------------------------------------------
# Coalescing and counting
# --------------------------------------------------------------------------------------


class TestCoalescing:
    def test_the_first_detection_flushes_immediately(self, flushes):
        alerts.record_provider_budget_error("anthropic", "claude-sonnet-5", REAL_ANTHROPIC)
        assert flushes == [
            {
                "provider": "anthropic",
                "reason": "credit_balance_low",
                "sample_model": "claude-sonnet-5",
                "count": 1,
            }
        ]

    def test_detections_inside_the_interval_are_counted_not_written(self, flushes):
        for _ in range(10):
            alerts.record_provider_budget_error("anthropic", "claude-sonnet-5", REAL_ANTHROPIC)

        # One write for ten failures: this runs on a per-request failure path, and at the
        # scale of the incident (57 of ~65 models down) an unthrottled writer would issue
        # a Supabase round trip per failed request.
        assert len(flushes) == 1

    def test_throttling_defers_the_count_it_does_not_lose_it(self, flushes, monkeypatch):
        clock = {"t": 1000.0}
        monkeypatch.setattr(alerts.time, "monotonic", lambda: clock["t"])

        for _ in range(5):
            alerts.record_provider_budget_error("anthropic", "claude-sonnet-5", REAL_ANTHROPIC)
        assert [f["count"] for f in flushes] == [1]

        clock["t"] += alerts.FLUSH_INTERVAL_SECONDS + 1
        alerts.record_provider_budget_error("anthropic", "claude-sonnet-5", REAL_ANTHROPIC)

        # 4 deferred + the one that triggered the flush; 6 detections, 6 occurrences.
        assert [f["count"] for f in flushes] == [1, 5]
        assert sum(f["count"] for f in flushes) == 6

    def test_different_providers_and_reasons_are_separate_keys(self, flushes):
        alerts.record_provider_budget_error("anthropic", "claude-sonnet-5", REAL_ANTHROPIC)
        alerts.record_provider_budget_error("openai", "gpt-5", "insufficient_quota")
        alerts.record_provider_budget_error("anthropic", "claude-opus-5", "insufficient_quota")

        assert {(f["provider"], f["reason"]) for f in flushes} == {
            ("anthropic", "credit_balance_low"),
            ("openai", "quota_exhausted"),
            ("anthropic", "quota_exhausted"),
        }

    def test_one_exception_passing_two_detection_sites_counts_once(self, flushes, monkeypatch):
        # Asserting only on the flush would prove nothing: the second call is throttled
        # either way, so a double count would sit invisibly in the pending ledger. Drain
        # it by advancing past the interval, and read the total.
        clock = {"t": 2000.0}
        monkeypatch.setattr(alerts.time, "monotonic", lambda: clock["t"])

        exc = Exception(REAL_ANTHROPIC)  # one upstream failure, two detection sites
        alerts.record_provider_budget_error("anthropic", "claude-sonnet-5", str(exc), exc=exc)
        alerts.record_provider_budget_error("anthropic", "claude-sonnet-5", str(exc), exc=exc)

        clock["t"] += alerts.FLUSH_INTERVAL_SECONDS + 1
        second = Exception(REAL_ANTHROPIC)
        alerts.record_provider_budget_error("anthropic", "claude-sonnet-5", str(second), exc=second)

        # Two distinct failures, two occurrences -- not three.
        assert sum(f["count"] for f in flushes) == 2

    def test_distinct_failures_each_count_even_when_identically_worded(self, flushes, monkeypatch):
        clock = {"t": 500.0}
        monkeypatch.setattr(alerts.time, "monotonic", lambda: clock["t"])
        for _ in range(3):
            exc = Exception(REAL_ANTHROPIC)  # three separate failures, same text
            alerts.record_provider_budget_error("anthropic", "m", str(exc), exc=exc)

        clock["t"] += alerts.FLUSH_INTERVAL_SECONDS + 1
        exc = Exception(REAL_ANTHROPIC)
        alerts.record_provider_budget_error("anthropic", "m", str(exc), exc=exc)

        assert sum(f["count"] for f in flushes) == 4

    def test_an_exception_that_cannot_hold_the_latch_still_records(self, flushes):
        class Slotted(Exception):
            __slots__ = ()

        exc = Slotted(REAL_ANTHROPIC)
        assert alerts.record_provider_budget_error("anthropic", "m", REAL_ANTHROPIC, exc=exc)
        assert len(flushes) == 1

    def test_a_missing_provider_name_is_recorded_not_dropped(self, flushes):
        alerts.record_provider_budget_error(None, None, REAL_ANTHROPIC)
        assert flushes[0]["provider"] == "unknown"
        assert flushes[0]["sample_model"] is None

    def test_a_caller_that_detected_on_evidence_the_text_lacks_still_records(self, flushes):
        # e.g. an httpx 402 with an empty body. Dropping it on a classification miss
        # would lose a real outage.
        assert alerts.record_provider_budget_error("openrouter", "m", "")
        assert flushes[0]["reason"] == "unknown"


# --------------------------------------------------------------------------------------
# Recording can never break inference
# --------------------------------------------------------------------------------------


class TestRecordingCannotBreakTheRequest:
    def test_a_raising_classifier_does_not_escape(self):
        with patch.object(
            alerts, "classify_provider_budget_error", side_effect=RuntimeError("boom")
        ):
            assert alerts.record_provider_budget_error("anthropic", "m", REAL_ANTHROPIC) is False

    def test_a_raising_dispatch_does_not_escape(self, monkeypatch):
        def _explode(fn, *args):
            raise RuntimeError("executor is shut down")

        monkeypatch.setattr(alerts, "_submit", _explode)
        assert alerts.record_provider_budget_error("anthropic", "m", REAL_ANTHROPIC) is False

    def test_a_raising_database_write_does_not_escape_the_flush(self):
        with patch(
            "src.db.provider_budget_events.record_budget_event",
            side_effect=RuntimeError("supabase is down"),
        ):
            alerts._flush("anthropic", "credit_balance_low", "claude-sonnet-5", 3)  # must not raise

    def test_a_failed_write_requeues_its_occurrences(self, flushes):
        with patch(
            "src.db.provider_budget_events.record_budget_event",
            side_effect=RuntimeError("supabase is down"),
        ):
            alerts._flush("anthropic", "credit_balance_low", "claude-sonnet-5", 3)

        # The next detection must carry the lost 3 plus itself, and must not be throttled
        # away -- otherwise a transient Supabase blip silently deletes the alert.
        alerts.record_provider_budget_error("anthropic", "claude-sonnet-5", REAL_ANTHROPIC)
        assert flushes[0]["count"] == 4

    def test_the_real_dispatch_is_non_blocking_and_does_not_raise(self, monkeypatch):
        # The stub the global fixture installs would hide a broken executor, so exercise
        # the real one here with a flush that just signals.
        monkeypatch.setattr(alerts, "_submit", _REAL_SUBMIT)
        done = threading.Event()
        with patch.object(alerts, "_flush", lambda *a: done.set()):
            alerts.record_provider_budget_error("anthropic", "m", REAL_ANTHROPIC)
        assert done.wait(timeout=5), "the background writer never ran"


# --------------------------------------------------------------------------------------
# The /admin/status payload
# --------------------------------------------------------------------------------------


class TestProviderBudgetStatus:
    def test_no_recent_events_reads_ok(self):
        with patch("src.db.provider_budget_events.list_recent_budget_events", return_value=[]):
            assert alerts.provider_budget_status() == {
                "status": "ok",
                "window_hours": alerts.DEFAULT_WINDOW_HOURS,
                "exhausted": [],
            }

    def test_a_recent_event_reads_degraded_with_the_specifics(self):
        row = {
            "provider": "anthropic",
            "reason": "credit_balance_low",
            "first_seen_at": "2026-09-16T09:00:00+00:00",
            "last_seen_at": "2026-09-16T11:30:00+00:00",
            "occurrences": 12,
            "sample_model": "claude-sonnet-5",
        }
        with patch("src.db.provider_budget_events.list_recent_budget_events", return_value=[row]):
            block = alerts.provider_budget_status()

        assert block["status"] == "degraded"
        assert block["exhausted"] == [
            {
                "provider": "anthropic",
                "reason": "credit_balance_low",
                "first_seen": "2026-09-16T09:00:00+00:00",
                "last_seen": "2026-09-16T11:30:00+00:00",
                "occurrences": 12,
                "sample_model": "claude-sonnet-5",
            }
        ]

    def test_a_reason_the_database_should_not_hold_is_normalized_on_read(self):
        with patch(
            "src.db.provider_budget_events.list_recent_budget_events",
            return_value=[{"provider": "x", "reason": "see https://x/keys/" + KEY_ID}],
        ):
            block = alerts.provider_budget_status()
        assert block["exhausted"][0]["reason"] == "unknown"

    def test_a_broken_read_raises_rather_than_reading_healthy(self):
        # "No provider is out of credit" and "the query that would tell you is broken"
        # must not render identically. _safe_block turns this into {"error": ...}.
        with patch(
            "src.db.provider_budget_events.list_recent_budget_events",
            side_effect=ProviderBudgetEventsUnavailable("boom"),
        ):
            with pytest.raises(ProviderBudgetEventsUnavailable):
                alerts.provider_budget_status()


# --------------------------------------------------------------------------------------
# Detection site 1: src/services/provider_failover.py
# --------------------------------------------------------------------------------------


class TestFailoverDetectionSite:
    def test_a_budget_error_mapped_to_402_is_recorded(self, flushes):
        exc = Exception(REAL_402)
        result = map_provider_error("openrouter", "anthropic/claude-sonnet-5", exc)

        assert result.status_code == 402
        assert flushes == [
            {
                "provider": "openrouter",
                "reason": "credit_balance_low",
                "sample_model": "anthropic/claude-sonnet-5",
                "count": 1,
            }
        ]

    def test_an_anthropic_400_with_no_402_anywhere_is_recorded(self, flushes):
        map_provider_error("anthropic", "claude-sonnet-5", Exception(REAL_ANTHROPIC))
        assert flushes[0]["provider"] == "anthropic"
        assert flushes[0]["reason"] == "credit_balance_low"

    def test_a_byok_request_is_not_recorded(self, flushes):
        # The user's own key being empty is not a gateway outage. Recording it would put
        # a customer's billing problem on our ops dashboard as ours.
        result = map_provider_error("openrouter", "m", Exception(REAL_402), byok=True)
        assert result.status_code == 402
        assert flushes == []

    @pytest.mark.parametrize(
        "raw", ["Error code: 429 - rate limited", "Error code: 404 - no such model", "boom"]
    )
    def test_non_budget_errors_are_not_recorded(self, flushes, raw):
        map_provider_error("openrouter", "m", Exception(raw))
        assert flushes == []

    def test_an_already_mapped_httpexception_is_not_double_recorded(self, flushes):
        map_provider_error("openrouter", "m", HTTPException(status_code=402, detail="x"))
        assert flushes == []

    def test_a_recorder_that_somehow_raises_does_not_change_the_response(self):
        # record_provider_budget_error() cannot raise by construction, so this simulates
        # the failures that bypass that guarantee entirely: an ImportError at the call
        # site, a circular import, a module that failed to load. The user's request must
        # come back with the same 402 and the same masked text either way.
        with patch(
            "src.services.provider_budget_alerts.record_provider_budget_error",
            side_effect=RuntimeError("monitoring is down"),
        ):
            result = map_provider_error("openrouter", "m", Exception(REAL_402))

        assert result.status_code == 402
        assert KEY_ID not in str(result.detail)
        assert "capacity" in str(result.detail).lower()


# --------------------------------------------------------------------------------------
# Detection site 2: src/handlers/chat_handler.py
# --------------------------------------------------------------------------------------


def _handler():
    from src.handlers.chat_handler import ChatInferenceHandler

    h = ChatInferenceHandler(api_key=None, request=None)
    h.user = {"id": 1, "key_id": 7}
    h.is_anonymous = False
    return h


def _call_provider_expecting_failure(handler, raw_error, byok_token=None):
    """Drive ChatInferenceHandler._call_provider into its provider-error branch.

    "testprovider" is absent from PROVIDER_ROUTING, so the handler falls back to the
    OpenRouter client -- which is patched to raise.
    """
    with (
        patch(
            "src.handlers.chat_handler.make_openrouter_request_openai",
            side_effect=Exception(raw_error),
        ),
        patch.object(type(handler), "_bind_byok", lambda self, provider: byok_token),
    ):
        with pytest.raises(HTTPException) as excinfo:
            handler._call_provider("testprovider", "claude-sonnet-5", [{"role": "user", "c": "x"}])
    return excinfo.value


class TestChatHandlerDetectionSite:
    def test_a_budget_failure_is_recorded_once(self, flushes):
        exc = _call_provider_expecting_failure(_handler(), REAL_ANTHROPIC)

        # 503 + the friendly capacity text is the existing user-facing contract.
        assert exc.status_code == 503
        # map_provider_error() and the handler's own budget branch both call the
        # recorder; the exception latch means the operator sees one occurrence, not two.
        assert len(flushes) == 1
        assert flushes[0] == {
            "provider": "testprovider",
            "reason": "credit_balance_low",
            "sample_model": "claude-sonnet-5",
            "count": 1,
        }
        # ...and nothing is left uncounted in the ledger for a later flush to double up.
        # Without this the throttle would hide a second count rather than prevent it.
        assert alerts._pending[("testprovider", "credit_balance_low")].count == 0

    def test_the_user_still_sees_only_the_capacity_message(self):
        from src.utils.errors import PROVIDER_CAPACITY_MESSAGE

        exc = _call_provider_expecting_failure(_handler(), REAL_402)
        detail = str(exc.detail)
        assert PROVIDER_CAPACITY_MESSAGE in detail
        assert KEY_ID not in detail
        assert "openrouter.ai" not in detail
        assert "can only afford" not in detail

    def test_a_byok_failure_is_not_recorded(self, flushes):
        # The user's own key ran dry. Existing behaviour: no "our side" message. New
        # behaviour that must match it: nothing on the operator's budget dashboard.
        exc = _call_provider_expecting_failure(_handler(), REAL_402, byok_token="byok-token")
        assert exc.status_code == 502  # falls through to the sanitized provider path
        assert flushes == []

    def test_a_non_budget_failure_is_not_recorded(self, flushes):
        _call_provider_expecting_failure(_handler(), "Error code: 500 - upstream exploded")
        assert flushes == []

    def test_a_budget_error_the_mapper_does_not_call_402_is_still_recorded_here(self, flushes):
        # This is why the handler keeps its own recorder call rather than leaning on
        # map_provider_error(): an httpx.HTTPStatusError lands in the generic
        # `400 <= status < 500` branch of _map_provider_error_impl and comes back as a
        # 400, so the 402-keyed record in map_provider_error() never fires. The handler's
        # own branch keys off is_provider_budget_error(str(e)) instead and catches it.
        # Delete the call there and this provider's exhaustion goes unrecorded again.
        import httpx

        exc = httpx.HTTPStatusError(
            REAL_ANTHROPIC,
            request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
            response=httpx.Response(402, text="{}"),
        )
        handler = _handler()
        with (
            patch("src.handlers.chat_handler.make_openrouter_request_openai", side_effect=exc),
            patch.object(type(handler), "_bind_byok", lambda self, provider: None),
        ):
            with pytest.raises(HTTPException) as excinfo:
                handler._call_provider("testprovider", "claude-sonnet-5", [])

        from src.services.provider_failover import map_provider_error as _map

        assert _map("testprovider", "claude-sonnet-5", exc).status_code != 402
        assert excinfo.value.status_code == 503
        assert flushes[0]["reason"] == "credit_balance_low"

    def test_a_recorder_that_somehow_raises_does_not_break_the_request(self, flushes):
        with patch(
            "src.services.provider_budget_alerts.record_provider_budget_error",
            side_effect=RuntimeError("monitoring is down"),
        ):
            exc = _call_provider_expecting_failure(_handler(), REAL_ANTHROPIC)

        # Same status, same masked message: the user cannot tell monitoring broke.
        assert exc.status_code == 503
        assert KEY_ID not in str(exc.detail)


# --------------------------------------------------------------------------------------
# Detection site 3: src/routes/chat_streaming.py
# --------------------------------------------------------------------------------------


class _ExplodingStream:
    """A provider stream whose first chunk raises the upstream budget error."""

    def __init__(self, raw_error):
        self._raw_error = raw_error

    def __iter__(self):
        return self

    def __next__(self):
        raise Exception(self._raw_error)


async def _drain_stream(raw_error):
    from src.routes.chat_streaming import stream_generator

    chunks = []
    async for chunk in stream_generator(
        stream=_ExplodingStream(raw_error),
        user=None,
        api_key=None,
        model="claude-sonnet-5",
        trial={},
        environment_tag=None,
        session_id=None,
        messages=[{"role": "user", "content": "x"}],
        provider="anthropic",
        is_anonymous=True,
        request_id=None,  # skip the failed-request DB write; not what this tests
    ):
        chunks.append(chunk)
    return "".join(str(c) for c in chunks)


class TestChatStreamingDetectionSite:
    async def test_a_budget_failure_mid_stream_is_recorded(self, flushes):
        await _drain_stream(REAL_ANTHROPIC)

        assert flushes == [
            {
                "provider": "anthropic",
                "reason": "credit_balance_low",
                "sample_model": "claude-sonnet-5",
                "count": 1,
            }
        ]

    async def test_the_sse_error_carries_no_upstream_text(self, flushes):
        from src.utils.errors import PROVIDER_CAPACITY_MESSAGE

        body = await _drain_stream(REAL_402)
        assert PROVIDER_CAPACITY_MESSAGE in body
        assert KEY_ID not in body
        assert "openrouter.ai" not in body
        assert "can only afford" not in body

    async def test_a_non_budget_failure_is_not_recorded(self, flushes):
        await _drain_stream("Error code: 500 - upstream exploded")
        assert flushes == []

    async def test_a_recorder_that_somehow_raises_does_not_break_the_stream(self):
        from src.utils.errors import PROVIDER_CAPACITY_MESSAGE

        with patch(
            "src.services.provider_budget_alerts.record_provider_budget_error",
            side_effect=RuntimeError("monitoring is down"),
        ):
            body = await _drain_stream(REAL_ANTHROPIC)

        # The consumer still gets a well-formed capacity error, not a broken stream.
        assert PROVIDER_CAPACITY_MESSAGE in body
        assert KEY_ID not in body

    async def test_a_key_id_containing_429_is_not_mistaken_for_a_rate_limit(self, flushes):
        # Regression: the rate-limit check is a substring scan over the whole raw error,
        # and REAL_402's key id contains the literal "429". Before the branch reorder in
        # src/routes/chat_streaming.py this budget failure was reported to the user as
        # "Rate limit exceeded" and recorded nowhere -- the exact invisibility this
        # feature removes, reintroduced by a digit inside a credential.
        assert "429" in KEY_ID, "the regression this pins depends on the fixture"

        body = await _drain_stream(REAL_402)

        assert "rate_limit_error" not in body
        assert "capacity_error" in body
        assert flushes[0]["reason"] == "credit_balance_low"

    async def test_a_genuine_rate_limit_is_still_a_rate_limit(self, flushes):
        # The reorder must not swallow real 429s into the capacity path.
        body = await _drain_stream("Error code: 429 - rate limit exceeded, too many requests")
        assert "rate_limit_error" in body
        assert "capacity_error" not in body
        assert flushes == []
