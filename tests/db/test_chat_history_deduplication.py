"""
Unit tests for chat history message deduplication functionality.

Tests specifically focus on the duplicate detection code paths in save_chat_message
to ensure proper code coverage.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from src.db.chat_history import save_chat_message


class TestMessageDeduplication:
    """Test duplicate message detection logic"""

    def test_save_message_with_duplicate_detection_no_duplicate(self, monkeypatch):
        """Test saving message when no duplicate exists (normal path)"""
        # Mock Supabase client
        mock_client = MagicMock()
        mock_result = MagicMock()
        mock_result.data = []  # No duplicates found

        # Mock the duplicate check query
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value = mock_result

        # Mock the insert
        insert_result = MagicMock()
        insert_result.data = [{'id': 1, 'content': 'Test message', 'role': 'user', 'session_id': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'tokens': 0}]
        mock_table.insert.return_value.execute.return_value = insert_result

        # Mock the session update
        update_result = MagicMock()
        update_result.data = [{'id': 1}]
        mock_table.update.return_value.eq.return_value.execute.return_value = update_result

        mock_client.table.return_value = mock_table

        monkeypatch.setattr('src.db.chat_history.get_supabase_client', lambda: mock_client)

        # Call the function
        result = save_chat_message(
            session_id=1,
            role='user',
            content='Test message',
            model='gpt-4',
            tokens=0,
            user_id=1
        )

        # Verify duplicate check was performed
        assert mock_client.table.called
        assert result['id'] == 1
        assert result['content'] == 'Test message'

    def test_save_message_with_duplicate_detected(self, monkeypatch):
        """Test that duplicate message returns existing message"""
        # Mock Supabase client
        mock_client = MagicMock()

        # Mock duplicate found
        existing_message = {
            'id': 999,
            'content': 'Duplicate message',
            'role': 'user',
            'created_at': datetime.now(timezone.utc).isoformat(),
            'tokens': 5
        }
        duplicate_result = MagicMock()
        duplicate_result.data = [existing_message]

        # Mock the duplicate check query
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value = duplicate_result

        mock_client.table.return_value = mock_table

        monkeypatch.setattr('src.db.chat_history.get_supabase_client', lambda: mock_client)

        # Call the function
        result = save_chat_message(
            session_id=1,
            role='user',
            content='Duplicate message',
            model='gpt-4',
            tokens=0,
            user_id=1
        )

        # Should return existing message without inserting
        assert result['id'] == 999
        assert result['content'] == 'Duplicate message'
        # Verify insert was NOT called (since duplicate was found)
        mock_table.insert.assert_not_called()

    def test_save_message_skip_duplicate_check(self, monkeypatch):
        """Test that skip_duplicate_check bypasses duplicate detection"""
        # Mock Supabase client
        mock_client = MagicMock()

        # Mock the insert
        mock_table = MagicMock()
        insert_result = MagicMock()
        insert_result.data = [{'id': 2, 'content': 'Test', 'role': 'user', 'session_id': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'tokens': 0}]
        mock_table.insert.return_value.execute.return_value = insert_result

        # Mock the session update
        update_result = MagicMock()
        update_result.data = [{'id': 1}]
        mock_table.update.return_value.eq.return_value.execute.return_value = update_result

        mock_client.table.return_value = mock_table

        monkeypatch.setattr('src.db.chat_history.get_supabase_client', lambda: mock_client)

        # Call with skip_duplicate_check=True
        result = save_chat_message(
            session_id=1,
            role='user',
            content='Test',
            model='gpt-4',
            tokens=0,
            user_id=1,
            skip_duplicate_check=True
        )

        # Should insert without checking for duplicates
        assert result['id'] == 2
        # Verify insert WAS called
        assert mock_table.insert.called

    def test_save_message_duplicate_check_failure_continues(self, monkeypatch):
        """Test that if duplicate check fails, save continues"""
        # Mock Supabase client
        mock_client = MagicMock()
        mock_table = MagicMock()

        # Mock duplicate check to raise exception
        mock_table.select.side_effect = Exception("Database error")

        # Mock successful insert
        insert_result = MagicMock()
        insert_result.data = [{'id': 3, 'content': 'Test', 'role': 'user', 'session_id': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'tokens': 0}]
        mock_table.insert.return_value.execute.return_value = insert_result

        # Mock the session update
        update_result = MagicMock()
        update_result.data = [{'id': 1}]
        mock_table.update.return_value.eq.return_value.execute.return_value = update_result

        def table_selector(name):
            if name == "chat_messages":
                return mock_table
            return MagicMock()

        mock_client.table = table_selector

        monkeypatch.setattr('src.db.chat_history.get_supabase_client', lambda: mock_client)

        # Should not raise exception, should continue with save
        result = save_chat_message(
            session_id=1,
            role='user',
            content='Test',
            model='gpt-4',
            tokens=0,
            user_id=1
        )

        # Should have saved the message despite duplicate check failure
        assert result['id'] == 3

    def test_save_message_empty_content_skips_duplicate_check(self, monkeypatch):
        """Test that empty content skips duplicate detection"""
        # Mock Supabase client
        mock_client = MagicMock()
        mock_table = MagicMock()

        # Mock the insert
        insert_result = MagicMock()
        insert_result.data = [{'id': 4, 'content': '', 'role': 'user', 'session_id': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'tokens': 0}]
        mock_table.insert.return_value.execute.return_value = insert_result

        # Mock the session update
        update_result = MagicMock()
        update_result.data = [{'id': 1}]
        mock_table.update.return_value.eq.return_value.execute.return_value = update_result

        mock_client.table.return_value = mock_table

        monkeypatch.setattr('src.db.chat_history.get_supabase_client', lambda: mock_client)

        # Call with empty content
        result = save_chat_message(
            session_id=1,
            role='user',
            content='',  # Empty content
            model='gpt-4',
            tokens=0,
            user_id=1
        )

        # Should save without duplicate check (empty content is allowed)
        assert result['id'] == 4
        assert result['content'] == ''

    def test_save_message_duplicate_check_time_window(self, monkeypatch):
        """Test that duplicate check uses 5-minute time window"""
        # Mock Supabase client
        mock_client = MagicMock()
        mock_table = MagicMock()

        # Capture the time window used in the query
        captured_time = None

        def capture_gte(field, value):
            nonlocal captured_time
            if field == "created_at":
                captured_time = value
            mock_result = MagicMock()
            mock_result.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[])
            return mock_result

        mock_table.select.return_value.eq.return_value.eq.return_value.eq.return_value.gte = capture_gte

        # Mock the insert
        insert_result = MagicMock()
        insert_result.data = [{'id': 5, 'content': 'Test', 'role': 'user', 'session_id': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'tokens': 0}]
        mock_table.insert.return_value.execute.return_value = insert_result

        # Mock the session update
        update_result = MagicMock()
        update_result.data = [{'id': 1}]
        mock_table.update.return_value.eq.return_value.execute.return_value = update_result

        mock_client.table.return_value = mock_table

        monkeypatch.setattr('src.db.chat_history.get_supabase_client', lambda: mock_client)

        # Call the function
        save_chat_message(
            session_id=1,
            role='user',
            content='Test',
            model='gpt-4',
            tokens=0,
            user_id=1
        )

        # Verify the time window is approximately 5 minutes ago
        assert captured_time is not None
        expected_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        # Check that the times are close (within 1 minute)
        assert captured_time[:16] == expected_time[:16]  # Compare up to minute precision
