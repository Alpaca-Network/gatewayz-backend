"""Tests for api_key_lookup utilities."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.utils.api_key_lookup import get_api_key_id_with_retry, mask_api_key_for_logging


class TestGetApiKeyIdWithRetry:
    """Test cases for get_api_key_id_with_retry function."""

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_key(self):
        """Test that empty API key returns None."""
        result = await get_api_key_id_with_retry("")
        assert result is None

        result = await get_api_key_id_with_retry(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_special_keys(self):
        """Test that special keys like local-dev-bypass-key return None."""
        result = await get_api_key_id_with_retry("local-dev-bypass-key")
        assert result is None

        result = await get_api_key_id_with_retry("anonymous")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_api_key_id_on_success(self):
        """Test successful API key lookup."""
        mock_record = {"id": 123, "key": "test-key"}

        with patch("src.db.api_keys.get_api_key_by_key", return_value=mock_record):
            result = await get_api_key_id_with_retry("test-key")
            assert result == 123

    @pytest.mark.asyncio
    async def test_returns_none_when_key_not_found(self):
        """Test that None is returned when key is not found."""
        with patch("src.db.api_keys.get_api_key_by_key", return_value=None):
            result = await get_api_key_id_with_retry("nonexistent-key")
            assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_record_missing_id(self):
        """Test that None is returned when record exists but has no id."""
        mock_record = {"key": "test-key"}  # Missing 'id' field

        with patch("src.db.api_keys.get_api_key_by_key", return_value=mock_record):
            result = await get_api_key_id_with_retry("test-key")
            assert result is None

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self):
        """Test that function retries on transient errors."""
        call_count = 0

        def mock_get_key(key):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Connection error")
            return {"id": 456, "key": key}

        with patch("src.db.api_keys.get_api_key_by_key", side_effect=mock_get_key):
            result = await get_api_key_id_with_retry("test-key", max_retries=3, retry_delay=0.01)
            assert result == 456
            assert call_count == 3

    @pytest.mark.asyncio
    async def test_returns_none_after_max_retries_exhausted(self):
        """Test that None is returned after all retries are exhausted."""
        with patch("src.db.api_keys.get_api_key_by_key", side_effect=Exception("Persistent error")):
            result = await get_api_key_id_with_retry("test-key", max_retries=2, retry_delay=0.01)
            assert result is None


class TestMaskApiKeyForLogging:
    """Test cases for mask_api_key_for_logging function."""

    def test_returns_none_string_for_none(self):
        """Test that None input returns 'None' string."""
        assert mask_api_key_for_logging(None) == "None"

    def test_returns_none_string_for_empty(self):
        """Test that empty string returns 'None' string."""
        assert mask_api_key_for_logging("") == "None"

    def test_returns_stars_for_short_key(self):
        """Test that short keys are fully masked."""
        assert mask_api_key_for_logging("12345678") == "***"
        assert mask_api_key_for_logging("short") == "***"

    def test_masks_long_key_properly(self):
        """Test that long keys show first and last 4 chars."""
        result = mask_api_key_for_logging("sk-1234567890abcdef")
        assert result == "sk-1...cdef"
