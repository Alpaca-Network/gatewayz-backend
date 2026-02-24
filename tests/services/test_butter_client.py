"""
Tests for Butter.dev LLM Response Caching Client

These tests verify the caching logic, eligibility checks, and utility functions
for the Butter.dev integration.
"""

import os
import time
from unittest.mock import patch

# Set test environment before imports
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("APP_ENV", "testing")


class TestButterCompatibleProviders:
    """Test provider compatibility checks."""

    def test_compatible_providers_return_true(self):
        """Test that known compatible providers are identified correctly."""
        from src.services.butter_client import is_provider_butter_compatible

        compatible = [
            "openrouter",
            "featherless",
            "together",
            "fireworks",
            "deepinfra",
            "groq",
            "openai",
            "xai",
        ]

        for provider in compatible:
            assert (
                is_provider_butter_compatible(provider) is True
            ), f"{provider} should be compatible"

    def test_excluded_providers_return_false(self):
        """Test that excluded providers are identified correctly."""
        from src.services.butter_client import is_provider_butter_compatible

        excluded = ["google", "google-vertex", "anthropic", "fal", "stability"]

        for provider in excluded:
            assert (
                is_provider_butter_compatible(provider) is False
            ), f"{provider} should be excluded"

    def test_unknown_provider_returns_false(self):
        """Test that unknown providers default to not compatible."""
        from src.services.butter_client import is_provider_butter_compatible

        assert is_provider_butter_compatible("unknown-provider") is False
        assert is_provider_butter_compatible("") is False

    def test_case_insensitive_provider_matching(self):
        """Test that provider matching is case-insensitive."""
        from src.services.butter_client import is_provider_butter_compatible

        # The function lowercases the provider name
        assert is_provider_butter_compatible("OpenRouter") is True
        assert is_provider_butter_compatible("OPENROUTER") is True
        assert is_provider_butter_compatible("Google") is False


class TestShouldUseButterCache:
    """Test the main eligibility check function."""

    @patch("src.services.butter_client.Config")
    def test_system_disabled_returns_false(self, mock_config):
        """Test that disabled system returns False."""
        from src.services.butter_client import should_use_butter_cache

        mock_config.BUTTER_DEV_ENABLED = False

        user = {"id": 1, "preferences": {"enable_butter_cache": True}}
        use_cache, reason = should_use_butter_cache(user, "openrouter")

        assert use_cache is False
        assert reason == "system_disabled"

    @patch("src.services.butter_client.Config")
    def test_anonymous_user_returns_false(self, mock_config):
        """Test that anonymous users don't use caching."""
        from src.services.butter_client import should_use_butter_cache

        mock_config.BUTTER_DEV_ENABLED = True

        use_cache, reason = should_use_butter_cache(None, "openrouter")

        assert use_cache is False
        assert reason == "anonymous_user"

    @patch("src.services.butter_client.Config")
    def test_user_preference_disabled_returns_false(self, mock_config):
        """Test that users with caching disabled return False."""
        from src.services.butter_client import should_use_butter_cache

        mock_config.BUTTER_DEV_ENABLED = True

        # User with caching explicitly disabled
        user = {"id": 1, "preferences": {"enable_butter_cache": False}}
        use_cache, reason = should_use_butter_cache(user, "openrouter")

        assert use_cache is False
        assert reason == "user_preference_disabled"

        # User with no preferences (defaults to enabled)
        user = {"id": 1, "preferences": {}}
        use_cache, reason = should_use_butter_cache(user, "openrouter")

        assert use_cache is True
        assert reason == "enabled"

        # User with None preferences (defaults to enabled)
        user = {"id": 1, "preferences": None}
        use_cache, reason = should_use_butter_cache(user, "openrouter")

        assert use_cache is True
        assert reason == "enabled"

    @patch("src.services.butter_client.Config")
    def test_incompatible_provider_returns_false(self, mock_config):
        """Test that incompatible providers return False."""
        from src.services.butter_client import should_use_butter_cache

        mock_config.BUTTER_DEV_ENABLED = True

        user = {"id": 1, "preferences": {"enable_butter_cache": True}}
        use_cache, reason = should_use_butter_cache(user, "google")

        assert use_cache is False
        assert "provider_incompatible" in reason

    @patch("src.services.butter_client.Config")
    def test_all_conditions_met_returns_true(self, mock_config):
        """Test that all conditions met returns True."""
        from src.services.butter_client import should_use_butter_cache

        mock_config.BUTTER_DEV_ENABLED = True

        user = {"id": 1, "preferences": {"enable_butter_cache": True}}
        use_cache, reason = should_use_butter_cache(user, "openrouter")

        assert use_cache is True
        assert reason == "enabled"


class TestCacheHitDetection:
    """Test cache hit detection heuristics."""

    def test_fast_response_is_cache_hit(self):
        """Test that fast responses are detected as cache hits."""
        from src.services.butter_client import detect_cache_hit

        # Very fast response (< 0.5s) should be a cache hit
        assert detect_cache_hit(0.05) is True
        assert detect_cache_hit(0.1) is True
        assert detect_cache_hit(0.3) is True
        assert detect_cache_hit(0.49) is True

    def test_slow_response_is_cache_miss(self):
        """Test that slow responses are detected as cache misses."""
        from src.services.butter_client import detect_cache_hit

        # Slow response (>= 0.5s) should be a cache miss
        assert detect_cache_hit(0.5) is False
        assert detect_cache_hit(1.0) is False
        assert detect_cache_hit(5.0) is False

    def test_custom_threshold(self):
        """Test that custom threshold works."""
        from src.services.butter_client import detect_cache_hit

        # With custom threshold of 1.0s
        assert detect_cache_hit(0.5, threshold=1.0) is True
        assert detect_cache_hit(0.99, threshold=1.0) is True
        assert detect_cache_hit(1.0, threshold=1.0) is False
        assert detect_cache_hit(1.5, threshold=1.0) is False


class TestButterCacheTimer:
    """Test the cache timer context manager."""

    def test_timer_measures_elapsed_time(self):
        """Test that timer correctly measures elapsed time."""
        from src.services.butter_client import ButterCacheTimer

        with ButterCacheTimer() as timer:
            time.sleep(0.1)

        # Allow timing variance for CI environments (sleep may be imprecise)
        assert timer.elapsed_seconds >= 0.08  # Allow some variance below
        assert timer.elapsed_seconds < 0.3  # Allow generous upper bound
        assert timer.elapsed_ms >= 80
        assert timer.elapsed_ms < 300

    def test_timer_detects_cache_hit(self):
        """Test that timer correctly detects cache hit based on latency."""
        from src.services.butter_client import ButterCacheTimer

        # Fast "request" should be cache hit
        with ButterCacheTimer() as timer:
            time.sleep(0.01)

        assert timer.is_cache_hit is True

    def test_timer_detects_cache_miss(self):
        """Test that timer correctly detects cache miss based on latency."""
        from src.services.butter_client import ButterCacheTimer

        # Slow "request" should be cache miss
        with ButterCacheTimer(hit_threshold=0.05) as timer:
            time.sleep(0.1)

        assert timer.is_cache_hit is False

    def test_timer_custom_threshold(self):
        """Test that timer respects custom threshold."""
        from src.services.butter_client import ButterCacheTimer

        with ButterCacheTimer(hit_threshold=0.5) as timer:
            time.sleep(0.2)

        assert timer.is_cache_hit is True

        with ButterCacheTimer(hit_threshold=0.1) as timer:
            time.sleep(0.2)

        assert timer.is_cache_hit is False


class TestButterRequestMetadata:
    """Test metadata generation for request tracking."""

    def test_cache_hit_metadata(self):
        """Test metadata generation for cache hit."""
        from src.services.butter_client import get_butter_request_metadata

        metadata = get_butter_request_metadata(
            is_cache_hit=True,
            actual_cost_usd=0.001234,
            response_time_ms=50.5,
            provider="openrouter",
        )

        assert metadata["butter_cache_hit"] is True
        assert metadata["butter_provider"] == "openrouter"
        assert metadata["butter_response_time_ms"] == 50.5
        assert metadata["actual_cost_usd"] == 0.001234

    def test_cache_miss_metadata(self):
        """Test metadata generation for cache miss."""
        from src.services.butter_client import get_butter_request_metadata

        metadata = get_butter_request_metadata(
            is_cache_hit=False,
            actual_cost_usd=0.001234,
            response_time_ms=1500.0,
            provider="fireworks",
        )

        assert metadata["butter_cache_hit"] is False
        assert metadata["butter_provider"] == "fireworks"
        assert metadata["butter_response_time_ms"] == 1500.0
        # actual_cost_usd should not be included for cache misses
        assert "actual_cost_usd" not in metadata


class TestGetUserCachePreference:
    """Test user preference extraction."""

    def test_returns_true_when_enabled(self):
        """Test that True is returned when caching is enabled."""
        from src.services.butter_client import get_user_cache_preference

        user = {"id": 1, "preferences": {"enable_butter_cache": True}}
        assert get_user_cache_preference(user) is True

    def test_returns_false_when_disabled(self):
        """Test that False is returned when caching is disabled."""
        from src.services.butter_client import get_user_cache_preference

        user = {"id": 1, "preferences": {"enable_butter_cache": False}}
        assert get_user_cache_preference(user) is False

    def test_returns_true_for_missing_preference(self):
        """Test that True is returned when preference is not set (enabled by default)."""
        from src.services.butter_client import get_user_cache_preference

        # No preferences - defaults to enabled
        assert get_user_cache_preference({"id": 1}) is True
        assert get_user_cache_preference({"id": 1, "preferences": {}}) is True
        assert get_user_cache_preference({"id": 1, "preferences": None}) is True

    def test_returns_false_for_none_user(self):
        """Test that False is returned for None user."""
        from src.services.butter_client import get_user_cache_preference

        assert get_user_cache_preference(None) is False
