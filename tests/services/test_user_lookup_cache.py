"""
Tests for user lookup cache service

NOTE: user_lookup_cache now delegates to db.users which has the cache.
Tests patch at the db.users level to test caching behavior.
"""

from unittest.mock import Mock, patch

import pytest

from src.services.user_lookup_cache import (
    clear_cache,
    get_cache_stats,
    get_user,
    invalidate_user,
    set_cache_ttl,
)


class TestUserLookupCache:
    """Test user lookup caching functionality

    Note: The cache is now in db.users, so we patch _get_user_uncached there.
    """

    def setup_method(self):
        """Clear cache before each test"""
        clear_cache()

    def test_cache_get_user_first_call_hits_database(self):
        """First call to get_user should hit the database"""
        test_user = {"id": 1, "email": "test@example.com", "credits": 100}

        with patch("src.db.users._get_user_uncached") as mock_db:
            mock_db.return_value = test_user

            result = get_user("test_api_key")

            assert result == test_user
            assert mock_db.call_count == 1

    def test_cache_get_user_second_call_uses_cache(self):
        """Second call with same API key should use cache (not hit database)"""
        test_user = {"id": 1, "email": "test@example.com", "credits": 100}

        with patch("src.db.users._get_user_uncached") as mock_db:
            mock_db.return_value = test_user

            # First call
            result1 = get_user("test_api_key")
            # Second call
            result2 = get_user("test_api_key")

            assert result1 == test_user
            assert result2 == test_user
            # Database should only be called once
            assert mock_db.call_count == 1

    def test_cache_get_user_different_keys_separate_cache_entries(self):
        """Different API keys should have separate cache entries"""
        user1 = {"id": 1, "email": "user1@example.com"}
        user2 = {"id": 2, "email": "user2@example.com"}

        with patch("src.db.users._get_user_uncached") as mock_db:
            mock_db.side_effect = [user1, user2]

            result1 = get_user("api_key_1")
            result2 = get_user("api_key_2")

            assert result1 == user1
            assert result2 == user2
            # Database called twice (different keys)
            assert mock_db.call_count == 2

    def test_cache_returns_none_when_user_not_found(self):
        """Cache should handle None returns from database (None is NOT cached)"""
        with patch("src.db.users._get_user_uncached") as mock_db:
            mock_db.return_value = None

            result1 = get_user("invalid_key")
            result2 = get_user("invalid_key")

            assert result1 is None
            assert result2 is None
            # Note: None is NOT cached (to avoid caching typos/invalid keys)
            # So DB is called twice
            assert mock_db.call_count == 2

    def test_clear_cache_all(self):
        """clear_cache() with no arguments should clear entire cache"""
        test_user = {"id": 1, "email": "test@example.com"}

        with patch("src.db.users._get_user_uncached") as mock_db:
            mock_db.return_value = test_user

            # First call (hits DB)
            get_user("test_key")
            assert mock_db.call_count == 1

            # Clear cache
            clear_cache()

            # Second call should hit DB again
            get_user("test_key")
            assert mock_db.call_count == 2

    def test_clear_cache_specific_key(self):
        """clear_cache(api_key) should clear only that key"""
        user1 = {"id": 1, "email": "user1@example.com"}
        user2 = {"id": 2, "email": "user2@example.com"}

        with patch("src.db.users._get_user_uncached") as mock_db:
            mock_db.side_effect = [user1, user2, user1]

            # Cache both keys
            get_user("key1")
            get_user("key2")
            assert mock_db.call_count == 2

            # Clear only key1
            clear_cache("key1")

            # Get key1 again (should hit DB)
            get_user("key1")
            assert mock_db.call_count == 3

            # Get key2 again (should use cache)
            get_user("key2")
            assert mock_db.call_count == 3  # No increase

    def test_invalidate_user(self):
        """invalidate_user should clear cache for specific user"""
        test_user = {"id": 1, "email": "test@example.com"}

        with patch("src.db.users._get_user_uncached") as mock_db:
            mock_db.return_value = test_user

            get_user("test_key")
            invalidate_user("test_key")
            get_user("test_key")

            # Should be called twice (once before, once after invalidate)
            assert mock_db.call_count == 2

    def test_get_cache_stats(self):
        """get_cache_stats should return cache statistics"""
        test_user = {"id": 1, "email": "test@example.com"}

        with patch("src.db.users._get_user_uncached") as mock_db:
            mock_db.return_value = test_user

            # Cache some users
            get_user("key1")
            get_user("key2")

            stats = get_cache_stats()

            assert "cached_users" in stats
            assert "ttl_seconds" in stats
            assert stats["cached_users"] == 2
            assert stats["ttl_seconds"] == 300  # Default TTL

    def test_set_cache_ttl(self):
        """set_cache_ttl should update cache TTL"""
        original_stats = get_cache_stats()
        assert original_stats["ttl_seconds"] == 300

        set_cache_ttl(600)
        updated_stats = get_cache_stats()
        assert updated_stats["ttl_seconds"] == 600

        # Reset to original
        set_cache_ttl(300)

    def test_cache_statistics_empty(self):
        """get_cache_stats should work on empty cache"""
        clear_cache()
        stats = get_cache_stats()

        assert stats["cached_users"] == 0
        assert stats["ttl_seconds"] == 300
