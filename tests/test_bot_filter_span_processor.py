"""
Tests for BotFilterSpanProcessor.

Verifies that:
- Bot/scanner spans are correctly identified and dropped
- Legitimate API spans are NOT dropped
- Edge cases (missing attributes, no OTel) are handled gracefully
"""

import sys
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Minimal ReadableSpan stub (avoids requiring opentelemetry-sdk in CI)
# ---------------------------------------------------------------------------


class _FakeSpan:
    """Lightweight stand-in for opentelemetry.sdk.trace.ReadableSpan."""

    def __init__(self, attributes: dict):
        self.attributes = {k: str(v) for k, v in attributes.items()}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_processor():
    """Import and instantiate BotFilterSpanProcessor with OTel mocked."""
    # Ensure the module is freshly imported for each test
    mods_to_remove = [k for k in sys.modules if "bot_filter_span_processor" in k]
    for m in mods_to_remove:
        del sys.modules[m]

    # Patch opentelemetry.sdk.trace so the import succeeds without the real SDK
    fake_sdk = MagicMock()
    fake_sdk.SpanProcessor = object
    fake_sdk.ReadableSpan = object

    with patch.dict(
        "sys.modules",
        {"opentelemetry.sdk.trace": fake_sdk},
    ):
        from src.utils.bot_filter_span_processor import BotFilterSpanProcessor

        return BotFilterSpanProcessor()


def _classify(processor, **attrs):
    """Call processor._classify with a fake span built from keyword attrs."""
    span = _FakeSpan(attrs)
    return processor._classify(span)


# ---------------------------------------------------------------------------
# Tests — should DROP
# ---------------------------------------------------------------------------


class TestBotFilterSpanProcessorDrops:
    def setup_method(self):
        self.proc = _make_processor()

    def test_drop_scanner_route_env(self):
        reason = _classify(self.proc, **{"http.route": "/.env", "http.status_code": "200"})
        assert reason == "scanner_route", f"Expected scanner_route, got {reason!r}"

    def test_drop_scanner_route_git(self):
        reason = _classify(self.proc, **{"http.route": "/.git/config", "http.status_code": "200"})
        assert reason == "scanner_route"

    def test_drop_scanner_route_wp_login(self):
        reason = _classify(self.proc, **{"http.route": "/wp-login.php", "http.status_code": "200"})
        assert reason == "scanner_route"

    def test_drop_scanner_route_actuator(self):
        reason = _classify(
            self.proc, **{"http.route": "/actuator/health", "http.status_code": "200"}
        )
        assert reason == "scanner_route"

    def test_drop_scanner_route_phpmyadmin(self):
        reason = _classify(self.proc, **{"http.route": "/phpmyadmin/", "http.status_code": "200"})
        assert reason == "scanner_route"

    def test_drop_bot_user_agent_sqlmap(self):
        reason = _classify(
            self.proc,
            **{
                "http.route": "/v1/models",
                "http.user_agent": "sqlmap/1.7.8#stable (https://sqlmap.org)",
                "http.status_code": "200",
            },
        )
        assert reason == "bot_user_agent"

    def test_drop_bot_user_agent_nuclei(self):
        reason = _classify(
            self.proc,
            **{
                "http.route": "/v1/chat/completions",
                "http.user_agent": "nuclei/2.9.1 (linux; amd64)",
                "http.status_code": "200",
            },
        )
        assert reason == "bot_user_agent"

    def test_drop_bot_user_agent_zgrab(self):
        reason = _classify(
            self.proc,
            **{"http.route": "/", "http.user_agent": "zgrab/0.x", "http.status_code": "200"},
        )
        assert reason == "bot_user_agent"

    def test_drop_unauth_401_flood(self):
        # 401 with no customer/user attributes → unauth 4xx flood
        reason = _classify(
            self.proc, **{"http.route": "/v1/chat/completions", "http.status_code": "401"}
        )
        assert reason == "unauth_4xx_flood"

    def test_drop_unauth_403_flood(self):
        reason = _classify(self.proc, **{"http.route": "/v1/models", "http.status_code": "403"})
        assert reason == "unauth_4xx_flood"

    def test_drop_unauth_429_spam(self):
        # 429 with no customer.id / user.id → key-scanning pattern
        reason = _classify(
            self.proc, **{"http.route": "/v1/chat/completions", "http.status_code": "429"}
        )
        assert reason == "unauth_429_spam"


# ---------------------------------------------------------------------------
# Tests — should NOT DROP (legitimate traffic)
# ---------------------------------------------------------------------------


class TestBotFilterSpanProcessorAllows:
    def setup_method(self):
        self.proc = _make_processor()

    def test_allow_legit_chat_completions(self):
        reason = _classify(
            self.proc,
            **{
                "http.route": "/v1/chat/completions",
                "http.user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                "http.status_code": "200",
                "customer.id": "cust_abc123",
            },
        )
        assert reason is None, f"Should not drop legit span, got {reason!r}"

    def test_allow_authenticated_429(self):
        # Authenticated user hitting rate limit — their trace is valuable (shows our limit works)
        reason = _classify(
            self.proc,
            **{
                "http.route": "/v1/chat/completions",
                "http.status_code": "429",
                "customer.id": "cust_xyz789",
            },
        )
        assert reason is None

    def test_allow_authenticated_401(self):
        # Authenticated user getting 401 (e.g. expired key) — keep the trace for debugging
        reason = _classify(
            self.proc,
            **{
                "http.route": "/v1/models",
                "http.status_code": "401",
                "user.id": "user_abc",
            },
        )
        assert reason is None

    def test_allow_health_check(self):
        reason = _classify(
            self.proc,
            **{"http.route": "/health", "http.status_code": "200"},
        )
        assert reason is None

    def test_allow_models_endpoint(self):
        reason = _classify(
            self.proc,
            **{
                "http.route": "/v1/models",
                "http.status_code": "200",
                "customer.id": "cust_abc123",
            },
        )
        assert reason is None

    def test_allow_span_with_no_attributes(self):
        # Empty span (e.g. internal/library span) — don't crash or drop it
        reason = _classify(self.proc)
        assert reason is None


# ---------------------------------------------------------------------------
# Tests — dropped_count increments
# ---------------------------------------------------------------------------


class TestBotFilterSpanProcessorCounter:
    def setup_method(self):
        self.proc = _make_processor()

    def test_dropped_count_increments_on_drop(self):
        initial = self.proc.dropped_count

        # Simulate on_end with a bot span (no downstream to call)
        with patch("src.utils.bot_filter_span_processor._increment_dropped_counter"):
            span = _FakeSpan({"http.route": "/.env", "http.status_code": "200"})
            self.proc.on_end(span)

        assert self.proc.dropped_count == initial + 1

    def test_dropped_count_unchanged_for_legit_span(self):
        initial = self.proc.dropped_count

        span = _FakeSpan(
            {
                "http.route": "/v1/chat/completions",
                "http.status_code": "200",
                "customer.id": "cust_abc123",
            }
        )
        self.proc.on_end(span)

        assert self.proc.dropped_count == initial
