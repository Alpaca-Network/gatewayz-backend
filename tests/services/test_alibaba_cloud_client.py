import types
from datetime import UTC
from unittest.mock import MagicMock

import pytest

from src.config import Config
from src.services import alibaba_cloud_client as acc


def _reset_region_state(monkeypatch):
    monkeypatch.setattr(acc, "_explicit_region", None, raising=False)
    monkeypatch.setattr(acc, "_inferred_region", None, raising=False)


def test_list_alibaba_models_retries_with_china_region(monkeypatch):
    """Ensure we fall back to the China endpoint when the default fails with auth errors."""

    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key")
    _reset_region_state(monkeypatch)

    call_order: list[str] = []

    def fake_get_client(region_override=None):
        region = region_override or "international"
        call_order.append(region)

        failing = MagicMock()
        failing.models.list.side_effect = Exception("Incorrect API key provided (401)")

        success_response = types.SimpleNamespace(data=[{"id": "qwen-max"}])
        succeeding = MagicMock()
        succeeding.models.list.return_value = success_response

        return failing if region == "international" else succeeding

    monkeypatch.setattr(acc, "get_alibaba_cloud_client", fake_get_client)

    response = acc.list_alibaba_models()

    assert response.data[0]["id"] == "qwen-max"
    assert call_order == ["international", "china"], "should retry in china region"
    assert acc._inferred_region == "china"


def test_chat_request_prefers_cached_region(monkeypatch):
    """Subsequent requests should re-use the inferred working region to avoid extra failures."""

    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key")
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None, raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", None, raising=False)
    _reset_region_state(monkeypatch)
    monkeypatch.setattr(acc, "_inferred_region", "china", raising=False)

    captured_regions: list[str | None] = []

    class FakeChat:
        def __init__(self):
            self.completions = MagicMock()
            self.completions.create.return_value = {"ok": True}

    fake_client = MagicMock()
    fake_client.chat = FakeChat()

    def fake_get_client(region_override=None):
        captured_regions.append(region_override)
        return fake_client

    monkeypatch.setattr(acc, "get_alibaba_cloud_client", fake_get_client)

    result = acc.make_alibaba_cloud_request_openai(messages=[], model="qwen-max")

    assert result == {"ok": True}
    assert captured_regions == ["china"], "should use inferred region without retry"
    fake_client.chat.completions.create.assert_called_once()


def test_get_client_uses_region_specific_keys(monkeypatch):
    """Ensure region-specific API keys override the default key."""

    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "default-key")
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", "china-key")
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", "intl-key")

    captured_keys: list[str] = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured_keys.append(kwargs["api_key"])

    monkeypatch.setattr(acc, "OpenAI", FakeOpenAI)

    acc.get_alibaba_cloud_client(region_override="china")
    acc.get_alibaba_cloud_client(region_override="international")

    assert captured_keys == ["china-key", "intl-key"]


def test_region_attempt_order_uses_available_key_for_all_regions(monkeypatch):
    """When only one region-specific key is set, all regions should still be attempted.

    This enables failover when the user has misconfigured which key goes with which region.
    """

    _reset_region_state(monkeypatch)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", None, raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None, raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", "intl-key", raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_REGION", "china", raising=False)
    monkeypatch.setattr(acc, "_inferred_region", None, raising=False)

    attempts = acc._region_attempt_order()

    # Both regions should be included because the intl-key will be used as fallback for china
    # This enables failover if user misconfigured which key goes with which region
    assert attempts == ["china", "international"], "should include all regions with fallback key"


def test_failover_with_misconfigured_region_key(monkeypatch):
    """Test that failover works when user sets international key but it only works for China.

    This is the scenario from the bug report: user sets ALIBABA_CLOUD_API_KEY_INTERNATIONAL
    with a key that actually only works for the China endpoint.
    """

    _reset_region_state(monkeypatch)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", None, raising=False)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY_CHINA", None, raising=False)
    # User mistakenly put their China key in the INTERNATIONAL variable
    monkeypatch.setattr(
        Config, "ALIBABA_CLOUD_API_KEY_INTERNATIONAL", "china-key-in-wrong-var", raising=False
    )
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_REGION", "international", raising=False)

    call_order: list[str] = []

    def fake_get_client(region_override=None):
        region = region_override or "international"
        call_order.append(region)

        failing = MagicMock()
        failing.models.list.side_effect = Exception("Error code: 401 - Incorrect API key provided")

        success_response = types.SimpleNamespace(data=[{"id": "qwen-max"}])
        succeeding = MagicMock()
        succeeding.models.list.return_value = success_response

        # The key only works for China, not international
        return failing if region == "international" else succeeding

    monkeypatch.setattr(acc, "get_alibaba_cloud_client", fake_get_client)

    response = acc.list_alibaba_models()

    assert response.data[0]["id"] == "qwen-max"
    assert call_order == ["international", "china"], "should failover from international to china"
    assert acc._inferred_region == "china"


def test_is_quota_error_detects_quota_exceeded():
    """Test that _is_quota_error detects various quota error messages."""
    quota_errors = [
        Exception("Error code: 429 - insufficient_quota"),
        Exception("You exceeded your current quota, please check your plan"),
        Exception("quota exceeded"),
        Exception("rate_limit_exceeded"),
        Exception("Error code: 429"),
        Exception("status code: 429"),
    ]

    for err in quota_errors:
        assert acc._is_quota_error(err) is True, f"Should detect quota error: {err}"


def test_is_quota_error_does_not_match_auth_errors():
    """Test that _is_quota_error doesn't match auth errors."""
    auth_errors = [
        Exception("Error code: 401 - invalid_api_key"),
        Exception("Incorrect API key provided"),
        Exception("unauthorized"),
    ]

    for err in auth_errors:
        assert acc._is_quota_error(err) is False, f"Should not match auth error: {err}"


def test_quota_error_raises_quota_exceeded_error(monkeypatch):
    """Test that quota errors are converted to QuotaExceededError."""
    _reset_region_state(monkeypatch)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key")

    def fake_get_client(region_override=None):
        client = MagicMock()
        client.models.list.side_effect = Exception(
            "Error code: 429 - {'error': {'message': 'You exceeded your current quota', "
            "'type': 'insufficient_quota', 'code': 'insufficient_quota'}}"
        )
        return client

    monkeypatch.setattr(acc, "get_alibaba_cloud_client", fake_get_client)

    with pytest.raises(acc.QuotaExceededError):
        acc.list_alibaba_models()


def test_quota_error_does_not_retry_other_regions(monkeypatch):
    """Test that quota errors don't trigger region failover."""
    _reset_region_state(monkeypatch)
    monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key")

    call_order: list[str] = []

    def fake_get_client(region_override=None):
        region = region_override or "international"
        call_order.append(region)
        client = MagicMock()
        client.models.list.side_effect = Exception("Error code: 429 - insufficient_quota")
        return client

    monkeypatch.setattr(acc, "get_alibaba_cloud_client", fake_get_client)

    with pytest.raises(acc.QuotaExceededError):
        acc.list_alibaba_models()

    # Should only try once since quota errors don't benefit from region failover
    assert call_order == ["international"], "should not retry with other regions for quota errors"


class TestAlibabaQuotaErrorCaching:
    """Tests for quota error caching in fetch_models_from_alibaba."""

    def test_quota_error_caches_failure_state(self, monkeypatch):
        """Test that QuotaExceededError triggers caching of failure state."""
        from datetime import datetime, timezone

        from src.cache import _alibaba_models_cache
        from src.services import models

        _reset_region_state(monkeypatch)
        monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key")

        # Reset cache state
        _alibaba_models_cache["quota_error"] = False
        _alibaba_models_cache["quota_error_timestamp"] = None
        _alibaba_models_cache["data"] = None
        _alibaba_models_cache["timestamp"] = None

        def fake_list_alibaba_models():
            raise acc.QuotaExceededError("quota exceeded")

        monkeypatch.setattr(acc, "list_alibaba_models", fake_list_alibaba_models)

        result = models.fetch_models_from_alibaba()

        assert result == []
        assert _alibaba_models_cache["quota_error"] is True
        assert _alibaba_models_cache["quota_error_timestamp"] is not None
        assert _alibaba_models_cache["data"] == []
        # timestamp should NOT be set - this ensures the cache appears stale
        # so that fetch_models_from_alibaba is called to check quota_error_backoff
        assert _alibaba_models_cache["timestamp"] is None

    def test_quota_error_backoff_skips_api_calls(self, monkeypatch):
        """Test that subsequent calls during backoff period skip API calls."""
        from datetime import datetime, timezone

        from src.cache import _alibaba_models_cache
        from src.services import models

        _reset_region_state(monkeypatch)
        monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key")

        # Set up quota error state (recently occurred)
        _alibaba_models_cache["quota_error"] = True
        _alibaba_models_cache["quota_error_timestamp"] = datetime.now(UTC)
        _alibaba_models_cache["data"] = []
        _alibaba_models_cache["timestamp"] = datetime.now(UTC)

        call_count = 0

        def fake_list_alibaba_models():
            nonlocal call_count
            call_count += 1
            raise acc.QuotaExceededError("quota exceeded")

        monkeypatch.setattr(acc, "list_alibaba_models", fake_list_alibaba_models)

        result = models.fetch_models_from_alibaba()

        assert result == []
        assert call_count == 0, "should skip API call during backoff period"

    def test_quota_error_backoff_expires(self, monkeypatch):
        """Test that backoff expires after the configured duration."""
        from datetime import datetime, timedelta, timezone

        from src.cache import _alibaba_models_cache
        from src.services import models

        _reset_region_state(monkeypatch)
        monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key")

        # Set up quota error state that has expired (more than 15 minutes ago)
        backoff_seconds = _alibaba_models_cache.get("quota_error_backoff", 900)
        _alibaba_models_cache["quota_error"] = True
        _alibaba_models_cache["quota_error_timestamp"] = datetime.now(UTC) - timedelta(
            seconds=backoff_seconds + 60
        )
        _alibaba_models_cache["data"] = []
        _alibaba_models_cache["timestamp"] = datetime.now(UTC)

        call_count = 0

        def fake_list_alibaba_models():
            nonlocal call_count
            call_count += 1
            return types.SimpleNamespace(data=[])

        monkeypatch.setattr(acc, "list_alibaba_models", fake_list_alibaba_models)

        models.fetch_models_from_alibaba()

        assert call_count == 1, "should retry API call after backoff expires"

    def test_successful_fetch_clears_quota_error(self, monkeypatch):
        """Test that a successful fetch clears the quota error state."""
        from datetime import datetime, timedelta, timezone

        from src.cache import _alibaba_models_cache
        from src.services import models

        _reset_region_state(monkeypatch)
        monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key")

        # Set up expired quota error state
        backoff_seconds = _alibaba_models_cache.get("quota_error_backoff", 900)
        _alibaba_models_cache["quota_error"] = True
        _alibaba_models_cache["quota_error_timestamp"] = datetime.now(UTC) - timedelta(
            seconds=backoff_seconds + 60
        )
        _alibaba_models_cache["data"] = []
        _alibaba_models_cache["timestamp"] = datetime.now(UTC)

        def fake_list_alibaba_models():
            return types.SimpleNamespace(data=[types.SimpleNamespace(id="qwen-max")])

        monkeypatch.setattr(acc, "list_alibaba_models", fake_list_alibaba_models)

        result = models.fetch_models_from_alibaba()

        assert len(result) == 1
        assert _alibaba_models_cache["quota_error"] is False
        assert _alibaba_models_cache["quota_error_timestamp"] is None

    def test_quota_error_backoff_not_overridden_by_cache_ttl(self, monkeypatch):
        """Test that quota error backoff (15 min) is not overridden by cache TTL (1 hour).

        This tests the fix for the bug where setting timestamp during quota error
        would cause the cache to appear "fresh" for 1 hour, bypassing the 15-minute
        backoff period.
        """
        from datetime import datetime, timedelta, timezone

        from src.cache import _alibaba_models_cache
        from src.services import models

        _reset_region_state(monkeypatch)
        monkeypatch.setattr(Config, "ALIBABA_CLOUD_API_KEY", "test-key")

        # Simulate a quota error that occurred 20 minutes ago (backoff should have expired)
        backoff_seconds = _alibaba_models_cache.get("quota_error_backoff", 900)
        twenty_mins_ago = datetime.now(UTC) - timedelta(seconds=1200)

        _alibaba_models_cache["quota_error"] = True
        _alibaba_models_cache["quota_error_timestamp"] = twenty_mins_ago
        _alibaba_models_cache["data"] = []
        # timestamp is None since we don't set it on quota error
        _alibaba_models_cache["timestamp"] = None

        call_count = 0

        def fake_list_alibaba_models():
            nonlocal call_count
            call_count += 1
            return types.SimpleNamespace(data=[types.SimpleNamespace(id="qwen-max")])

        monkeypatch.setattr(acc, "list_alibaba_models", fake_list_alibaba_models)

        # This should trigger a new API call because:
        # 1. Cache has no timestamp, so appears stale to get_cached_models
        # 2. Quota error backoff (15 min) has expired (20 min ago)
        result = models.fetch_models_from_alibaba()

        assert call_count == 1, "should call API after quota error backoff expires"
        assert len(result) == 1
