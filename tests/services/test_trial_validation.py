# tests/services/test_trial_validation.py
import importlib
import math

import pytest

MODULE_PATH = "src.services.trial_validation"  # <- change if your module path differs


@pytest.fixture
def mod():
    return importlib.import_module(MODULE_PATH)


# ----------------- minimal fake Supabase client -----------------
class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, rows_by_key):
        # rows_by_key: dict[api_key] -> row dict
        self._rows = rows_by_key
        self._api_key = None
        self._update_values = None
        self._select_fields = None

    # select is ignored (we return whole rows), but we record that it was called
    def select(self, fields):
        self._select_fields = fields
        return self

    def update(self, values):
        self._update_values = values
        return self

    def eq(self, column, value):
        assert column == "api_key"
        self._api_key = value
        return self

    def execute(self):
        if self._update_values is None:
            # SELECT path
            row = self._rows.get(self._api_key)
            return _FakeResult([row] if row is not None else [])
        else:
            # UPDATE path
            row = self._rows.get(self._api_key)
            if row is None:
                return _FakeResult([])
            row.update(self._update_values)
            return _FakeResult([row])


class _FakeSupabase:
    def __init__(self, rows_by_key):
        self._rows = rows_by_key
        self._legacy_rows = {}  # For legacy users table

    def table(self, name):
        if name == "api_keys_new":
            return _FakeTable(self._rows)
        elif name == "users":
            return _FakeTable(self._legacy_rows)
        else:
            raise ValueError(f"Unexpected table: {name}")


# ----------------------------- tests: validate_trial_access -----------------------------


def test_validate_missing_key_returns_not_found(monkeypatch, mod):
    client = _FakeSupabase(rows_by_key={})
    monkeypatch.setattr(mod, "get_supabase_client", lambda: client)

    out = mod.validate_trial_access("sk-nope")
    assert out["is_valid"] is False
    assert out["is_trial"] is False
    # Error message indicates invalid/forbidden when key not found in either table
    assert "forbidden" in out["error"].lower() or "invalid" in out["error"].lower()


def test_validate_non_trial_key(monkeypatch, mod):
    rows = {"sk-live": {"api_key": "sk-live", "is_trial": False}}
    monkeypatch.setattr(mod, "get_supabase_client", lambda: _FakeSupabase(rows))

    out = mod.validate_trial_access("sk-live")
    assert out["is_valid"] is True
    assert out["is_trial"] is False
    assert "full access" in out.get("message", "").lower()


def test_validate_expired_iso_z_marked_expired(monkeypatch, mod):
    # Past ISO date with Z should be expired, but current code makes trial_end naive and 'now' aware,
    # leading to a comparison TypeError and falling back to 'not expired'.
    rows = {
        "sk-trial-expired": {
            "api_key": "sk-trial-expired",
            "is_trial": True,
            "trial_end_date": "2000-01-01T00:00:00Z",
            "trial_used_tokens": 0,
            "trial_used_requests": 0,
            "trial_used_credits": 0.0,
            "trial_max_tokens": 100,
            "trial_max_requests": 10,
            "trial_credits": 1.0,
        }
    }
    monkeypatch.setattr(mod, "get_supabase_client", lambda: _FakeSupabase(rows))

    out = mod.validate_trial_access("sk-trial-expired")
    assert out["is_valid"] is False
    assert out["is_trial"] is True
    assert out["is_expired"] is True
    assert "expired" in out["error"].lower()


def test_validate_tokens_cap_exceeded(monkeypatch, mod):
    rows = {
        "sk-trial-tokens": {
            "api_key": "sk-trial-tokens",
            "is_trial": True,
            "trial_used_tokens": 1000,
            "trial_max_tokens": 1000,
            "trial_used_requests": 3,
            "trial_max_requests": 10,
            "trial_used_credits": 0.5,
            "trial_credits": 1.0,
        }
    }
    monkeypatch.setattr(mod, "get_supabase_client", lambda: _FakeSupabase(rows))

    out = mod.validate_trial_access("sk-trial-tokens")
    assert out["is_valid"] is False
    assert out["is_trial"] is True
    assert "token limit" in out["error"].lower()
    assert out["remaining_tokens"] == 0
    assert out["remaining_requests"] == 7
    assert math.isclose(out["remaining_credits"], 0.5)


def test_validate_requests_cap_exceeded(monkeypatch, mod):
    rows = {
        "sk-trial-reqs": {
            "api_key": "sk-trial-reqs",
            "is_trial": True,
            "trial_used_tokens": 100,
            "trial_max_tokens": 1000,
            "trial_used_requests": 10,
            "trial_max_requests": 10,
            "trial_used_credits": 0.2,
            "trial_credits": 1.0,
        }
    }
    monkeypatch.setattr(mod, "get_supabase_client", lambda: _FakeSupabase(rows))

    out = mod.validate_trial_access("sk-trial-reqs")
    assert out["is_valid"] is False
    assert out["is_trial"] is True
    assert "request limit" in out["error"].lower()
    assert out["remaining_requests"] == 0
    assert out["remaining_tokens"] == 900
    assert math.isclose(out["remaining_credits"], 0.8)


def test_validate_credits_cap_exceeded(monkeypatch, mod):
    rows = {
        "sk-trial-credits": {
            "api_key": "sk-trial-credits",
            "is_trial": True,
            "trial_used_tokens": 100,
            "trial_max_tokens": 1000,
            "trial_used_requests": 1,
            "trial_max_requests": 10,
            "trial_used_credits": 1.0,
            "trial_credits": 1.0,
        }
    }
    monkeypatch.setattr(mod, "get_supabase_client", lambda: _FakeSupabase(rows))

    out = mod.validate_trial_access("sk-trial-credits")
    assert out["is_valid"] is False
    assert out["is_trial"] is True
    assert "credit limit" in out["error"].lower()
    assert out["remaining_tokens"] == 900
    assert out["remaining_requests"] == 9
    assert out["remaining_credits"] == 0


def test_validate_valid_trial(monkeypatch, mod):
    rows = {
        "sk-trial-ok": {
            "api_key": "sk-trial-ok",
            "is_trial": True,
            "trial_used_tokens": 100,
            "trial_max_tokens": 1000,
            "trial_used_requests": 3,
            "trial_max_requests": 10,
            "trial_used_credits": 0.4,
            "trial_credits": 1.0,
            "trial_end_date": "2100-12-31",  # future
        }
    }
    monkeypatch.setattr(mod, "get_supabase_client", lambda: _FakeSupabase(rows))

    out = mod.validate_trial_access("sk-trial-ok")
    assert out["is_valid"] is True
    assert out["is_trial"] is True
    assert out["is_expired"] is False
    assert out["remaining_tokens"] == 900
    assert out["remaining_requests"] == 7
    assert math.isclose(out["remaining_credits"], 0.6)
    assert out["trial_end_date"] == "2100-12-31"


def test_validate_handles_exception(monkeypatch, mod):
    def boom():
        raise RuntimeError("supabase down")

    monkeypatch.setattr(mod, "get_supabase_client", boom)

    out = mod.validate_trial_access("sk-any")
    assert out["is_valid"] is False
    assert out["is_trial"] is False
    # Error message includes "error occurred" and the original exception message
    assert "error occurred" in out["error"].lower() or "supabase down" in out["error"].lower()


# ----------------------------- tests: track_trial_usage -----------------------------


def test_track_usage_success_updates(monkeypatch, mod):
    rows = {
        "sk-trial": {
            "api_key": "sk-trial",
            "trial_used_tokens": 10,
            "trial_used_requests": 1,
            "trial_used_credits": 0.0002,
        }
    }
    client = _FakeSupabase(rows)
    monkeypatch.setattr(mod, "get_supabase_client", lambda: client)

    ok = mod.track_trial_usage("sk-trial", tokens_used=100, requests_used=2)
    assert ok is True

    # Credits: 100 * 0.00002 = 0.002
    updated = rows["sk-trial"]
    assert updated["trial_used_tokens"] == 10 + 100
    assert updated["trial_used_requests"] == 1 + 2
    assert math.isclose(updated["trial_used_credits"], 0.0002 + 0.002, rel_tol=1e-9)


def test_track_usage_key_not_found(monkeypatch, mod):
    client = _FakeSupabase(rows_by_key={})
    monkeypatch.setattr(mod, "get_supabase_client", lambda: client)

    ok = mod.track_trial_usage("sk-missing", tokens_used=50, requests_used=1)
    assert ok is False


def test_track_usage_handles_exception(monkeypatch, mod):
    def boom():
        raise RuntimeError("supabase down")

    monkeypatch.setattr(mod, "get_supabase_client", boom)

    ok = mod.track_trial_usage("sk-any", tokens_used=10, requests_used=1)
    assert ok is False


def test_track_usage_with_model_specific_pricing(monkeypatch, mod):
    """Test that track_trial_usage uses model-specific pricing when model info is provided"""
    rows = {
        "sk-trial": {
            "api_key": "sk-trial",
            "trial_used_tokens": 0,
            "trial_used_requests": 0,
            "trial_used_credits": 0.0,
        }
    }
    client = _FakeSupabase(rows)
    monkeypatch.setattr(mod, "get_supabase_client", lambda: client)

    # Mock get_model_pricing to return pricing for a known model
    # Claude Opus: ~$15/1M input, ~$75/1M output
    def mock_get_model_pricing(model_id):
        return {"prompt": 15.0, "completion": 75.0, "found": True}

    monkeypatch.setattr("src.services.pricing.get_model_pricing", mock_get_model_pricing)

    ok = mod.track_trial_usage(
        "sk-trial",
        tokens_used=2000,  # Total tokens (used as fallback)
        requests_used=1,
        model_id="anthropic/claude-3-opus",
        prompt_tokens=1000,
        completion_tokens=1000,
    )
    assert ok is True

    # Expected cost: (1000 * 15 / 1M) + (1000 * 75 / 1M) = 0.015 + 0.075 = 0.09
    updated = rows["sk-trial"]
    assert updated["trial_used_tokens"] == 2000
    assert updated["trial_used_requests"] == 1
    # With model pricing: ~$0.09 (much more than flat rate of $0.04)
    assert math.isclose(updated["trial_used_credits"], 0.09, rel_tol=1e-9)


def test_track_usage_fallback_without_model_info(monkeypatch, mod):
    """Test that track_trial_usage falls back to flat rate when model info is not provided"""
    rows = {
        "sk-trial": {
            "api_key": "sk-trial",
            "trial_used_tokens": 0,
            "trial_used_requests": 0,
            "trial_used_credits": 0.0,
        }
    }
    client = _FakeSupabase(rows)
    monkeypatch.setattr(mod, "get_supabase_client", lambda: client)

    ok = mod.track_trial_usage(
        "sk-trial",
        tokens_used=1000,
        requests_used=1,
        # No model_id, prompt_tokens, completion_tokens provided
    )
    assert ok is True

    # Expected cost: 1000 * 0.00002 = 0.02 (flat rate)
    updated = rows["sk-trial"]
    assert updated["trial_used_tokens"] == 1000
    assert updated["trial_used_requests"] == 1
    assert math.isclose(updated["trial_used_credits"], 0.02, rel_tol=1e-9)


def test_track_usage_unknown_model_uses_flat_rate(monkeypatch, mod):
    """Test that unknown models (not in catalog) use flat rate instead of near-zero pricing"""
    rows = {
        "sk-trial": {
            "api_key": "sk-trial",
            "trial_used_tokens": 0,
            "trial_used_requests": 0,
            "trial_used_credits": 0.0,
        }
    }
    client = _FakeSupabase(rows)
    monkeypatch.setattr(mod, "get_supabase_client", lambda: client)

    # Mock get_model_pricing to return "not found" for unknown model
    def mock_get_model_pricing(model_id):
        # Model not in catalog - return default pricing with found=False
        return {"prompt": 0.00002, "completion": 0.00002, "found": False}

    monkeypatch.setattr("src.services.pricing.get_model_pricing", mock_get_model_pricing)

    ok = mod.track_trial_usage(
        "sk-trial",
        tokens_used=2000,
        requests_used=1,
        model_id="unknown/model-xyz",
        prompt_tokens=1000,
        completion_tokens=1000,
    )
    assert ok is True

    # Should use flat rate: 2000 * 0.00002 = 0.04
    # NOT the near-zero pricing: (1000 * 0.00002 / 1M) + (1000 * 0.00002 / 1M) = 0.00000004
    updated = rows["sk-trial"]
    assert updated["trial_used_tokens"] == 2000
    assert updated["trial_used_requests"] == 1
    # Flat rate should be $0.04, not near-zero
    assert math.isclose(updated["trial_used_credits"], 0.04, rel_tol=1e-9)


# ----------------------------- tests: trial validation cache -----------------------------


def test_trial_cache_hit_skips_database(monkeypatch, mod):
    """Second call with same API key should use cache (not hit database)"""
    # Clear cache before test
    mod.clear_trial_cache()

    call_count = 0
    rows = {
        "sk-cached": {
            "api_key": "sk-cached",
            "is_trial": False,
        }
    }

    def counting_client():
        nonlocal call_count
        call_count += 1
        return _FakeSupabase(rows)

    monkeypatch.setattr(mod, "get_supabase_client", counting_client)

    # First call (should hit database)
    result1 = mod.validate_trial_access("sk-cached")
    assert result1["is_valid"] is True
    assert call_count == 1

    # Second call (should use cache)
    result2 = mod.validate_trial_access("sk-cached")
    assert result2["is_valid"] is True
    assert call_count == 1  # No additional database call


def test_trial_cache_different_keys_separate_entries(monkeypatch, mod):
    """Different API keys should have separate cache entries"""
    mod.clear_trial_cache()

    call_count = 0
    rows = {
        "sk-key1": {"api_key": "sk-key1", "is_trial": False},
        "sk-key2": {"api_key": "sk-key2", "is_trial": False},
    }

    def counting_client():
        nonlocal call_count
        call_count += 1
        return _FakeSupabase(rows)

    monkeypatch.setattr(mod, "get_supabase_client", counting_client)

    mod.validate_trial_access("sk-key1")
    mod.validate_trial_access("sk-key2")

    # Both keys should hit database
    assert call_count == 2


def test_clear_trial_cache_all(monkeypatch, mod):
    """clear_trial_cache() with no arguments should clear entire cache"""
    mod.clear_trial_cache()

    call_count = 0
    rows = {"sk-clear": {"api_key": "sk-clear", "is_trial": False}}

    def counting_client():
        nonlocal call_count
        call_count += 1
        return _FakeSupabase(rows)

    monkeypatch.setattr(mod, "get_supabase_client", counting_client)

    mod.validate_trial_access("sk-clear")
    assert call_count == 1

    mod.clear_trial_cache()

    mod.validate_trial_access("sk-clear")
    assert call_count == 2  # Should hit database again


def test_invalidate_trial_cache_specific_key(monkeypatch, mod):
    """invalidate_trial_cache should clear cache for specific key"""
    mod.clear_trial_cache()

    call_count = 0
    rows = {
        "sk-key1": {"api_key": "sk-key1", "is_trial": False},
        "sk-key2": {"api_key": "sk-key2", "is_trial": False},
    }

    def counting_client():
        nonlocal call_count
        call_count += 1
        return _FakeSupabase(rows)

    monkeypatch.setattr(mod, "get_supabase_client", counting_client)

    mod.validate_trial_access("sk-key1")
    mod.validate_trial_access("sk-key2")
    assert call_count == 2

    # Invalidate only key1
    mod.invalidate_trial_cache("sk-key1")

    mod.validate_trial_access("sk-key1")  # Should hit database
    assert call_count == 3

    mod.validate_trial_access("sk-key2")  # Should use cache
    assert call_count == 3


def test_get_trial_cache_stats(mod):
    """get_trial_cache_stats should return cache statistics"""
    mod.clear_trial_cache()

    stats = mod.get_trial_cache_stats()
    assert "cached_trials" in stats
    assert "ttl_seconds" in stats
    assert stats["cached_trials"] == 0
    assert stats["ttl_seconds"] == 60  # Default TTL for trial cache


def test_track_usage_invalidates_cache(monkeypatch, mod):
    """track_trial_usage should invalidate cache after successful update"""
    mod.clear_trial_cache()

    call_count = 0
    rows = {
        "sk-trial": {
            "api_key": "sk-trial",
            "is_trial": True,
            "trial_used_tokens": 10,
            "trial_used_requests": 1,
            "trial_used_credits": 0.0,
            "trial_max_tokens": 1000,
            "trial_max_requests": 100,
            "trial_credits": 5.0,
            "trial_end_date": "2100-12-31",
        }
    }

    def counting_client():
        nonlocal call_count
        call_count += 1
        return _FakeSupabase(rows)

    monkeypatch.setattr(mod, "get_supabase_client", counting_client)

    # First validation (hits DB, caches result)
    mod.validate_trial_access("sk-trial")
    initial_calls = call_count

    # Second validation (should use cache)
    mod.validate_trial_access("sk-trial")
    assert call_count == initial_calls  # No new DB call

    # Track usage (should invalidate cache)
    mod.track_trial_usage("sk-trial", tokens_used=50, requests_used=1)

    # Next validation should hit DB again (cache was invalidated)
    mod.validate_trial_access("sk-trial")
    assert call_count > initial_calls  # New DB call for fresh data


# ----------------------------- tests: HTTP/2 connection error handling -----------------------------


def test_validate_retries_on_http2_connection_error(monkeypatch, mod):
    """HTTP/2 connection errors should trigger retry with client refresh"""
    mod.clear_trial_cache()

    call_count = 0
    refresh_called = False
    rows = {"sk-retry": {"api_key": "sk-retry", "is_trial": False}}

    def failing_then_succeeding_client():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First call fails with HTTP/2 connection error
            raise Exception("LocalProtocolError: StreamIDTooLowError: 173 is lower than 193")
        return _FakeSupabase(rows)

    def mock_refresh():
        nonlocal refresh_called
        refresh_called = True

    monkeypatch.setattr(mod, "get_supabase_client", failing_then_succeeding_client)
    monkeypatch.setattr(mod, "refresh_supabase_client", mock_refresh)

    result = mod.validate_trial_access("sk-retry")

    # Should succeed after retry
    assert result["is_valid"] is True
    assert result["is_trial"] is False
    assert call_count == 2  # Initial call + 1 retry
    assert refresh_called is True  # Client should have been refreshed


def test_validate_retries_on_connection_terminated_error(monkeypatch, mod):
    """ConnectionTerminated errors should trigger retry with client refresh"""
    mod.clear_trial_cache()

    call_count = 0
    refresh_called = False
    rows = {"sk-retry-ct": {"api_key": "sk-retry-ct", "is_trial": False}}

    def failing_then_succeeding_client():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception(
                "<ConnectionTerminated error_code:9, last_stream_id:191, additional_data:None>"
            )
        return _FakeSupabase(rows)

    def mock_refresh():
        nonlocal refresh_called
        refresh_called = True

    monkeypatch.setattr(mod, "get_supabase_client", failing_then_succeeding_client)
    monkeypatch.setattr(mod, "refresh_supabase_client", mock_refresh)

    result = mod.validate_trial_access("sk-retry-ct")

    assert result["is_valid"] is True
    assert call_count == 2
    assert refresh_called is True


def test_validate_retries_on_send_headers_state_error(monkeypatch, mod):
    """SEND_HEADERS state errors should trigger retry with client refresh"""
    mod.clear_trial_cache()

    call_count = 0
    refresh_called = False
    rows = {"sk-retry-sh": {"api_key": "sk-retry-sh", "is_trial": False}}

    def failing_then_succeeding_client():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Invalid input StreamInputs.SEND_HEADERS in state 5")
        return _FakeSupabase(rows)

    def mock_refresh():
        nonlocal refresh_called
        refresh_called = True

    monkeypatch.setattr(mod, "get_supabase_client", failing_then_succeeding_client)
    monkeypatch.setattr(mod, "refresh_supabase_client", mock_refresh)

    result = mod.validate_trial_access("sk-retry-sh")

    assert result["is_valid"] is True
    assert call_count == 2
    assert refresh_called is True


def test_validate_max_retries_exceeded(monkeypatch, mod):
    """Should fail after max retries are exhausted"""
    mod.clear_trial_cache()

    call_count = 0

    def always_failing_client():
        nonlocal call_count
        call_count += 1
        raise Exception("LocalProtocolError: StreamIDTooLowError: connection broken")

    def mock_refresh():
        pass  # Do nothing

    monkeypatch.setattr(mod, "get_supabase_client", always_failing_client)
    monkeypatch.setattr(mod, "refresh_supabase_client", mock_refresh)

    result = mod.validate_trial_access("sk-fail")

    # Should fail after max retries (initial + 2 retries = 3 calls)
    assert result["is_valid"] is False
    assert "error" in result
    assert call_count == 3  # Initial call + 2 retries (MAX_RETRIES=2)


def test_validate_no_retry_on_non_connection_error(monkeypatch, mod):
    """Non-connection errors should not trigger retry logic"""
    mod.clear_trial_cache()

    call_count = 0

    def failing_client():
        nonlocal call_count
        call_count += 1
        # Non-connection error (e.g., authentication failure)
        raise Exception("Invalid authentication credentials")

    monkeypatch.setattr(mod, "get_supabase_client", failing_client)

    result = mod.validate_trial_access("sk-fail")

    # Should fail immediately without retry
    assert result["is_valid"] is False
    assert call_count == 1  # Only initial call, no retries
