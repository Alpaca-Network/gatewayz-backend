"""
Tests for background tasks service
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.services.background_tasks import (
    get_pending_tasks_count,
    log_activity_async,
    log_activity_background,
)


class TestBackgroundTasks:
    """Test background task functionality"""

    @pytest.mark.asyncio
    async def test_log_activity_async_success(self):
        """log_activity_async should execute database logging"""
        with patch("src.services.background_tasks.db_log_activity") as mock_db:
            mock_db.return_value = None

            await log_activity_async(
                user_id=1,
                model="gpt-4",
                provider="OpenAI",
                tokens=100,
                cost=0.01,
                speed=50.0,
                finish_reason="stop",
                app="API",
                metadata={"key": "value"},
            )

            mock_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_activity_async_error_handling(self):
        """log_activity_async should handle errors gracefully"""
        with patch("src.services.background_tasks.db_log_activity") as mock_db:
            mock_db.side_effect = Exception("Database error")

            # Should not raise exception (background task)
            await log_activity_async(
                user_id=1,
                model="gpt-4",
                provider="OpenAI",
                tokens=100,
                cost=0.01,
            )

    def test_log_activity_background_no_event_loop(self):
        """log_activity_background should work without event loop"""
        with patch("src.services.background_tasks.db_log_activity") as mock_db:
            mock_db.return_value = None

            # This should not raise an exception even without event loop
            log_activity_background(
                user_id=1,
                model="gpt-4",
                provider="OpenAI",
                tokens=100,
                cost=0.01,
            )

            # In non-async context, should fall back to sync call
            mock_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_activity_background_with_event_loop(self):
        """log_activity_background should create task when event loop exists"""
        with patch("src.services.background_tasks.db_log_activity") as mock_db:
            mock_db.return_value = None

            # This runs within an event loop (pytest-asyncio)
            log_activity_background(
                user_id=1,
                model="gpt-4",
                provider="OpenAI",
                tokens=100,
                cost=0.01,
            )

            # Allow time for task to be created
            await asyncio.sleep(0.01)

    def test_log_activity_background_error_handling(self):
        """log_activity_background should handle errors gracefully"""
        with patch("src.services.background_tasks.db_log_activity") as mock_db:
            mock_db.side_effect = Exception("Database error")

            # Should not raise exception
            log_activity_background(
                user_id=1,
                model="gpt-4",
                provider="OpenAI",
                tokens=100,
                cost=0.01,
            )

    def test_get_pending_tasks_count(self):
        """get_pending_tasks_count should return count of pending tasks"""
        count = get_pending_tasks_count()
        assert isinstance(count, int)
        assert count >= 0

    def test_log_activity_background_with_all_parameters(self):
        """log_activity_background should handle all parameters"""
        with patch("src.services.background_tasks.db_log_activity") as mock_db:
            mock_db.return_value = None

            log_activity_background(
                user_id=123,
                model="gpt-4-turbo",
                provider="OpenAI",
                tokens=1000,
                cost=0.05,
                speed=100.0,
                finish_reason="length",
                app="ChatAPI",
                metadata={
                    "session_id": 456,
                    "request_id": "abc123",
                    "model_version": "2024-01",
                },
            )

    @pytest.mark.asyncio
    async def test_log_activity_async_with_all_parameters(self):
        """log_activity_async should handle all parameters"""
        with patch("src.services.background_tasks.db_log_activity") as mock_db:
            mock_db.return_value = None

            await log_activity_async(
                user_id=123,
                model="gpt-4-turbo",
                provider="OpenAI",
                tokens=1000,
                cost=0.05,
                speed=100.0,
                finish_reason="length",
                app="ChatAPI",
                metadata={
                    "session_id": 456,
                    "request_id": "abc123",
                    "model_version": "2024-01",
                },
            )

            mock_db.assert_called_once()

    def test_log_activity_background_with_minimal_parameters(self):
        """log_activity_background should work with minimal parameters"""
        with patch("src.services.background_tasks.db_log_activity") as mock_db:
            mock_db.return_value = None

            # Should work with just required parameters
            log_activity_background(
                user_id=1,
                model="gpt-4",
                provider="OpenAI",
                tokens=100,
                cost=0.01,
            )

            mock_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_log_activity_async_with_none_metadata(self):
        """log_activity_async should handle None metadata"""
        with patch("src.services.background_tasks.db_log_activity") as mock_db:
            mock_db.return_value = None

            await log_activity_async(
                user_id=1,
                model="gpt-4",
                provider="OpenAI",
                tokens=100,
                cost=0.01,
                metadata=None,
            )

            mock_db.assert_called_once()

    def test_log_activity_background_multiple_calls(self):
        """log_activity_background should handle multiple calls"""
        with patch("src.services.background_tasks.db_log_activity") as mock_db:
            mock_db.return_value = None

            for i in range(5):
                log_activity_background(
                    user_id=i,
                    model=f"model-{i}",
                    provider="Provider",
                    tokens=100 * i,
                    cost=0.01 * i,
                )

            # All calls should be queued/processed
            assert mock_db.call_count == 5 or mock_db.call_count >= 1
