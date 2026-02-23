"""
Comprehensive test suite for chat history edge cases and bug scenarios

Tests cover:
1. Duplicate message detection
2. Concurrent request handling
3. History injection and saving flow
4. Multimodal content handling
5. Race conditions
6. Retry scenarios
7. Session state validation
8. Message ordering and consistency
"""

import pytest
import asyncio
from datetime import datetime, timezone, UTC
from unittest.mock import Mock, patch, AsyncMock
from typing import List, Dict, Any


# =========================
# Test Fixtures
# =========================

@pytest.fixture
def mock_supabase_client():
    """Mock Supabase client with in-memory storage"""
    from collections import defaultdict

    class MockResult:
        def __init__(self, data=None, count=None):
            self.data = data or []
            self.count = count

        def execute(self):
            return self

    class MockQuery:
        def __init__(self, table_name, storage):
            self.table_name = table_name
            self.storage = storage
            self.filters = []
            self.order_by = None
            self.order_desc = False
            self.limit_val = None

        def select(self, *args, **kwargs):
            return self

        def insert(self, data):
            if isinstance(data, list):
                for item in data:
                    item['id'] = len(self.storage[self.table_name]) + 1
                    item['created_at'] = datetime.now(UTC).isoformat()
                    self.storage[self.table_name].append(item)
                return MockResult(data=data)
            else:
                data['id'] = len(self.storage[self.table_name]) + 1
                data['created_at'] = datetime.now(UTC).isoformat()
                self.storage[self.table_name].append(data)
                return MockResult(data=[data])

        def update(self, data):
            updated = []
            for item in self.storage[self.table_name]:
                if self._matches(item):
                    item.update(data)
                    updated.append(item)
            return MockResult(data=updated)

        def eq(self, field, value):
            self.filters.append(('eq', field, value))
            return self

        def gte(self, field, value):
            self.filters.append(('gte', field, value))
            return self

        def order(self, field, desc=False):
            self.order_by = field
            self.order_desc = desc
            return self

        def limit(self, val):
            self.limit_val = val
            return self

        def _matches(self, item):
            for op, field, value in self.filters:
                if op == 'eq' and item.get(field) != value:
                    return False
                if op == 'gte' and item.get(field, '') < value:
                    return False
            return True

        def execute(self):
            results = [item for item in self.storage[self.table_name] if self._matches(item)]

            if self.order_by:
                results.sort(key=lambda x: x.get(self.order_by, ''), reverse=self.order_desc)

            if self.limit_val:
                results = results[:self.limit_val]

            return MockResult(data=results, count=len(results))

    class MockSupabase:
        def __init__(self):
            self.storage = defaultdict(list)

        def table(self, name):
            return MockQuery(name, self.storage)

    return MockSupabase()


@pytest.fixture
def sample_user():
    """Sample user data"""
    return {
        'id': 1,
        'email': 'test@example.com',
        'username': 'testuser',
        'credits': 1000
    }


@pytest.fixture
def sample_session(mock_supabase_client, sample_user):
    """Create a sample chat session"""
    session_data = {
        'user_id': sample_user['id'],
        'title': 'Test Session',
        'model': 'openai/gpt-4',
        'is_active': True,
        'created_at': datetime.now(UTC).isoformat(),
        'updated_at': datetime.now(UTC).isoformat()
    }
    result = mock_supabase_client.table('chat_sessions').insert(session_data).execute()
    return result.data[0]


# =========================
# Test: Duplicate Detection
# =========================

class TestDuplicateDetection:
    """Tests for duplicate message scenarios"""

    def test_exact_duplicate_messages_same_content(self, mock_supabase_client, sample_session):
        """Test that exact duplicate messages are detected"""
        from src.db.chat_history import save_chat_message

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            # Save same message twice
            msg1 = save_chat_message(
                session_id=sample_session['id'],
                role='user',
                content='Hello, how are you?',
                model='gpt-4',
                tokens=0,
                user_id=sample_session['user_id']
            )

            msg2 = save_chat_message(
                session_id=sample_session['id'],
                role='user',
                content='Hello, how are you?',
                model='gpt-4',
                tokens=0,
                user_id=sample_session['user_id']
            )

            # BUG: Currently both messages are saved
            messages = mock_supabase_client.storage['chat_messages']
            assert len(messages) == 2, "EXPECTED FAILURE: Duplicate messages saved"

            # TODO: After fix, should be:
            # assert len(messages) == 1, "Should deduplicate exact same content"

    def test_duplicate_messages_different_timestamps(self, mock_supabase_client, sample_session):
        """Test duplicate messages with different timestamps (e.g., retry scenario)"""
        from src.db.chat_history import save_chat_message

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            # First save
            msg1 = save_chat_message(
                session_id=sample_session['id'],
                role='user',
                content='What is Python?',
                model='gpt-4',
                tokens=0,
                user_id=sample_session['user_id']
            )

            # Simulate delay (retry scenario)
            import time
            time.sleep(0.1)

            # Second save (retry)
            msg2 = save_chat_message(
                session_id=sample_session['id'],
                role='user',
                content='What is Python?',
                model='gpt-4',
                tokens=0,
                user_id=sample_session['user_id']
            )

            messages = mock_supabase_client.storage['chat_messages']
            assert len(messages) == 2, "EXPECTED FAILURE: Retry creates duplicate"

            # TODO: After fix with time-window deduplication:
            # assert len(messages) == 1, "Should deduplicate within time window"

    def test_similar_but_different_messages(self, mock_supabase_client, sample_session):
        """Test that similar but different messages are both saved"""
        from src.db.chat_history import save_chat_message

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            msg1 = save_chat_message(
                session_id=sample_session['id'],
                role='user',
                content='Hello',
                model='gpt-4',
                tokens=0,
                user_id=sample_session['user_id']
            )

            msg2 = save_chat_message(
                session_id=sample_session['id'],
                role='user',
                content='Hello!',
                model='gpt-4',
                tokens=0,
                user_id=sample_session['user_id']
            )

            messages = mock_supabase_client.storage['chat_messages']
            assert len(messages) == 2, "Different messages should both be saved"
            assert messages[0]['content'] != messages[1]['content']


# =========================
# Test: History Injection
# =========================

class TestHistoryInjection:
    """Tests for conversation history loading and injection"""

    def test_history_injection_prepends_correctly(self, mock_supabase_client, sample_session):
        """Test that history is prepended to new messages in correct order"""
        from src.db.chat_history import save_chat_message, get_chat_session

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            # Build up history
            save_chat_message(sample_session['id'], 'user', 'Hello', 'gpt-4', 0, sample_session['user_id'])
            save_chat_message(sample_session['id'], 'assistant', 'Hi there!', 'gpt-4', 10, sample_session['user_id'])
            save_chat_message(sample_session['id'], 'user', 'How are you?', 'gpt-4', 0, sample_session['user_id'])

            # Load session with history
            session = get_chat_session(sample_session['id'], sample_session['user_id'])

            assert session is not None
            assert len(session['messages']) == 3
            assert session['messages'][0]['content'] == 'Hello'
            assert session['messages'][1]['content'] == 'Hi there!'
            assert session['messages'][2]['content'] == 'How are you?'

    def test_empty_history_handling(self, mock_supabase_client, sample_session):
        """Test handling of session with no history"""
        from src.db.chat_history import get_chat_session

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            session = get_chat_session(sample_session['id'], sample_session['user_id'])

            assert session is not None
            assert session['messages'] == []

    def test_history_respects_user_ownership(self, mock_supabase_client, sample_session):
        """Test that users can only access their own chat history"""
        from src.db.chat_history import save_chat_message, get_chat_session

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            # Save message for user 1
            save_chat_message(sample_session['id'], 'user', 'Private message', 'gpt-4', 0, sample_session['user_id'])

            # Try to load as different user
            session = get_chat_session(sample_session['id'], user_id=999)

            assert session is None, "Should not return session for different user"


# =========================
# Test: Message Ordering
# =========================

class TestMessageOrdering:
    """Tests for message ordering and sequencing"""

    def test_messages_ordered_by_created_at(self, mock_supabase_client, sample_session):
        """Test that messages are returned in chronological order"""
        from src.db.chat_history import save_chat_message, get_chat_session

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            # Save messages in order
            for i in range(5):
                save_chat_message(
                    sample_session['id'],
                    'user' if i % 2 == 0 else 'assistant',
                    f'Message {i}',
                    'gpt-4',
                    10,
                    sample_session['user_id']
                )
                # Small delay to ensure different timestamps
                import time
                time.sleep(0.01)

            session = get_chat_session(sample_session['id'], sample_session['user_id'])

            # Verify ordering
            messages = session['messages']
            for i in range(len(messages) - 1):
                assert messages[i]['created_at'] <= messages[i + 1]['created_at'], \
                    "Messages should be in chronological order"

    def test_alternating_user_assistant_pattern(self, mock_supabase_client, sample_session):
        """Test proper conversation flow alternating between user and assistant"""
        from src.db.chat_history import save_chat_message, get_chat_session

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            # Save alternating messages
            conversation = [
                ('user', 'Hello'),
                ('assistant', 'Hi there!'),
                ('user', 'How are you?'),
                ('assistant', 'I am doing well, thanks!'),
            ]

            for role, content in conversation:
                save_chat_message(sample_session['id'], role, content, 'gpt-4', 10, sample_session['user_id'])

            session = get_chat_session(sample_session['id'], sample_session['user_id'])
            messages = session['messages']

            # Verify alternating pattern
            assert len(messages) == 4
            assert messages[0]['role'] == 'user'
            assert messages[1]['role'] == 'assistant'
            assert messages[2]['role'] == 'user'
            assert messages[3]['role'] == 'assistant'


# =========================
# Test: Edge Cases
# =========================

class TestEdgeCases:
    """Tests for edge cases and unusual scenarios"""

    def test_empty_message_content(self, mock_supabase_client, sample_session):
        """Test handling of empty message content"""
        from src.db.chat_history import save_chat_message

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            msg = save_chat_message(
                sample_session['id'],
                'user',
                '',  # Empty content
                'gpt-4',
                0,
                sample_session['user_id']
            )

            assert msg is not None
            assert msg['content'] == ''

    def test_very_long_message_content(self, mock_supabase_client, sample_session):
        """Test handling of very long messages"""
        from src.db.chat_history import save_chat_message

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            long_content = 'x' * 100000  # 100k characters

            msg = save_chat_message(
                sample_session['id'],
                'user',
                long_content,
                'gpt-4',
                0,
                sample_session['user_id']
            )

            assert msg is not None
            assert len(msg['content']) == 100000

    def test_special_characters_in_content(self, mock_supabase_client, sample_session):
        """Test handling of special characters and unicode"""
        from src.db.chat_history import save_chat_message

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            special_content = "Hello 👋 世界 🌍 \n\t Test \"quotes\" and 'apostrophes' <html>"

            msg = save_chat_message(
                sample_session['id'],
                'user',
                special_content,
                'gpt-4',
                0,
                sample_session['user_id']
            )

            assert msg is not None
            assert msg['content'] == special_content

    def test_null_model_field(self, mock_supabase_client, sample_session):
        """Test handling when model field is None"""
        from src.db.chat_history import save_chat_message

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            msg = save_chat_message(
                sample_session['id'],
                'user',
                'Test message',
                model=None,
                tokens=0,
                user_id=sample_session['user_id']
            )

            assert msg is not None
            assert msg['model'] is None

    def test_zero_and_negative_tokens(self, mock_supabase_client, sample_session):
        """Test handling of zero and negative token counts"""
        from src.db.chat_history import save_chat_message

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            msg1 = save_chat_message(sample_session['id'], 'user', 'Test', 'gpt-4', 0, sample_session['user_id'])
            msg2 = save_chat_message(sample_session['id'], 'assistant', 'Response', 'gpt-4', -10, sample_session['user_id'])

            assert msg1['tokens'] == 0
            assert msg2['tokens'] == -10


# =========================
# Test: Multimodal Content
# =========================

class TestMultimodalContent:
    """Tests for multimodal message content handling"""

    def test_text_only_content(self, mock_supabase_client, sample_session):
        """Test standard text-only message"""
        from src.db.chat_history import save_chat_message

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            msg = save_chat_message(
                sample_session['id'],
                'user',
                'Simple text message',
                'gpt-4',
                0,
                sample_session['user_id']
            )

            assert isinstance(msg['content'], str)
            assert msg['content'] == 'Simple text message'

    def test_json_string_content(self, mock_supabase_client, sample_session):
        """Test handling of JSON-like string content"""
        from src.db.chat_history import save_chat_message
        import json

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            json_content = json.dumps({"message": "Hello", "data": [1, 2, 3]})

            msg = save_chat_message(
                sample_session['id'],
                'user',
                json_content,
                'gpt-4',
                0,
                sample_session['user_id']
            )

            assert isinstance(msg['content'], str)
            # Should be saved as-is, not parsed
            assert json.loads(msg['content']) == {"message": "Hello", "data": [1, 2, 3]}


# =========================
# Test: Session Management
# =========================

class TestSessionManagement:
    """Tests for session creation, updates, and deletion"""

    def test_create_session_with_defaults(self, mock_supabase_client, sample_user):
        """Test session creation with default values"""
        from src.db.chat_history import create_chat_session

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            session = create_chat_session(
                user_id=sample_user['id'],
                title=None,  # Should get auto-generated title
                model=None   # Should get default model
            )

            assert session is not None
            assert session['user_id'] == sample_user['id']
            assert session['title'] is not None  # Auto-generated
            assert 'Chat' in session['title']
            assert session['model'] == 'openai/gpt-3.5-turbo'  # Default
            assert session['is_active'] is True

    def test_update_session_title(self, mock_supabase_client, sample_session):
        """Test updating session title"""
        from src.db.chat_history import update_chat_session, get_chat_session

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            success = update_chat_session(
                sample_session['id'],
                sample_session['user_id'],
                title='Updated Title'
            )

            assert success is True

            session = get_chat_session(sample_session['id'], sample_session['user_id'])
            assert session['title'] == 'Updated Title'

    def test_delete_session_soft_delete(self, mock_supabase_client, sample_session):
        """Test that delete is a soft delete (sets is_active=False)"""
        from src.db.chat_history import delete_chat_session, get_chat_session

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            success = delete_chat_session(sample_session['id'], sample_session['user_id'])

            assert success is True

            # Should not be returned by get_chat_session
            session = get_chat_session(sample_session['id'], sample_session['user_id'])
            assert session is None

            # But should still exist in storage with is_active=False
            stored = mock_supabase_client.storage['chat_sessions'][0]
            assert stored['is_active'] is False

    def test_get_user_sessions_excludes_deleted(self, mock_supabase_client, sample_user):
        """Test that deleted sessions are not returned"""
        from src.db.chat_history import create_chat_session, delete_chat_session, get_user_chat_sessions

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            # Create 3 sessions
            s1 = create_chat_session(sample_user['id'], 'Session 1')
            s2 = create_chat_session(sample_user['id'], 'Session 2')
            s3 = create_chat_session(sample_user['id'], 'Session 3')

            # Delete one
            delete_chat_session(s2['id'], sample_user['id'])

            # Get all active sessions
            sessions = get_user_chat_sessions(sample_user['id'])

            assert len(sessions) == 2
            session_ids = [s['id'] for s in sessions]
            assert s1['id'] in session_ids
            assert s2['id'] not in session_ids
            assert s3['id'] in session_ids


# =========================
# Test: Pagination
# =========================

class TestPagination:
    """Tests for session and message pagination"""

    def test_session_pagination(self, mock_supabase_client, sample_user):
        """Test pagination of user sessions"""
        from src.db.chat_history import create_chat_session, get_user_chat_sessions

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            # Create 10 sessions
            for i in range(10):
                create_chat_session(sample_user['id'], f'Session {i}')

            # Get first page
            page1 = get_user_chat_sessions(sample_user['id'], limit=5, offset=0)
            assert len(page1) == 5

            # Get second page
            page2 = get_user_chat_sessions(sample_user['id'], limit=5, offset=5)
            assert len(page2) == 5

            # Ensure no overlap
            ids1 = {s['id'] for s in page1}
            ids2 = {s['id'] for s in page2}
            assert ids1.isdisjoint(ids2)

    def test_session_ordering_by_updated_at(self, mock_supabase_client, sample_user):
        """Test that sessions are ordered by updated_at desc"""
        from src.db.chat_history import create_chat_session, save_chat_message, get_user_chat_sessions

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            # Create sessions
            s1 = create_chat_session(sample_user['id'], 'Session 1')
            import time
            time.sleep(0.01)
            s2 = create_chat_session(sample_user['id'], 'Session 2')
            time.sleep(0.01)
            s3 = create_chat_session(sample_user['id'], 'Session 3')

            sessions = get_user_chat_sessions(sample_user['id'])

            # Most recent first
            assert sessions[0]['id'] == s3['id']
            assert sessions[1]['id'] == s2['id']
            assert sessions[2]['id'] == s1['id']


# =========================
# Test: Search Functionality
# =========================

class TestSearch:
    """Tests for chat session search"""

    def test_search_by_title(self, mock_supabase_client, sample_user):
        """Test searching sessions by title"""
        from src.db.chat_history import create_chat_session, search_chat_sessions

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            s1 = create_chat_session(sample_user['id'], 'Python Tutorial')
            s2 = create_chat_session(sample_user['id'], 'JavaScript Guide')
            s3 = create_chat_session(sample_user['id'], 'Python Best Practices')

            results = search_chat_sessions(sample_user['id'], 'python')

            assert len(results) == 2
            result_ids = {r['id'] for r in results}
            assert s1['id'] in result_ids
            assert s3['id'] in result_ids
            assert s2['id'] not in result_ids

    def test_search_by_message_content(self, mock_supabase_client, sample_user):
        """Test searching sessions by message content"""
        from src.db.chat_history import create_chat_session, save_chat_message, search_chat_sessions

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            s1 = create_chat_session(sample_user['id'], 'Session A')
            s2 = create_chat_session(sample_user['id'], 'Session B')

            save_chat_message(s1['id'], 'user', 'How do I use Docker?', 'gpt-4', 0, sample_user['id'])
            save_chat_message(s2['id'], 'user', 'What is Kubernetes?', 'gpt-4', 0, sample_user['id'])

            results = search_chat_sessions(sample_user['id'], 'docker')

            assert len(results) == 1
            assert results[0]['id'] == s1['id']

    def test_search_case_insensitive(self, mock_supabase_client, sample_user):
        """Test that search is case-insensitive"""
        from src.db.chat_history import create_chat_session, search_chat_sessions

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            s1 = create_chat_session(sample_user['id'], 'PYTHON Tutorial')

            results_lower = search_chat_sessions(sample_user['id'], 'python')
            results_upper = search_chat_sessions(sample_user['id'], 'PYTHON')
            results_mixed = search_chat_sessions(sample_user['id'], 'PyThOn')

            assert len(results_lower) == 1
            assert len(results_upper) == 1
            assert len(results_mixed) == 1


# =========================
# Test: Statistics
# =========================

class TestStatistics:
    """Tests for chat statistics"""

    def test_session_stats_accuracy(self, mock_supabase_client, sample_user):
        """Test that session stats are calculated correctly"""
        from src.db.chat_history import create_chat_session, save_chat_message, get_chat_session_stats

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            # Create 2 active sessions
            s1 = create_chat_session(sample_user['id'], 'Session 1')
            s2 = create_chat_session(sample_user['id'], 'Session 2')

            # Add messages
            save_chat_message(s1['id'], 'user', 'Hello', 'gpt-4', 5, sample_user['id'])
            save_chat_message(s1['id'], 'assistant', 'Hi', 'gpt-4', 10, sample_user['id'])
            save_chat_message(s2['id'], 'user', 'Test', 'gpt-4', 3, sample_user['id'])

            stats = get_chat_session_stats(sample_user['id'])

            assert stats['total_sessions'] == 2
            assert stats['total_messages'] == 3
            assert stats['total_tokens'] == 18  # 5 + 10 + 3

    def test_stats_exclude_inactive_sessions(self, mock_supabase_client, sample_user):
        """Test that stats exclude deleted (inactive) sessions"""
        from src.db.chat_history import create_chat_session, save_chat_message, delete_chat_session, get_chat_session_stats

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            s1 = create_chat_session(sample_user['id'], 'Active')
            s2 = create_chat_session(sample_user['id'], 'To Delete')

            save_chat_message(s1['id'], 'user', 'Message 1', 'gpt-4', 10, sample_user['id'])
            save_chat_message(s2['id'], 'user', 'Message 2', 'gpt-4', 20, sample_user['id'])

            # Delete s2
            delete_chat_session(s2['id'], sample_user['id'])

            stats = get_chat_session_stats(sample_user['id'])

            assert stats['total_sessions'] == 1
            assert stats['total_messages'] == 1
            assert stats['total_tokens'] == 10


# =========================
# Test: Concurrency
# =========================

class TestConcurrency:
    """Tests for concurrent operations"""

    @pytest.mark.asyncio
    async def test_concurrent_message_saves(self, mock_supabase_client, sample_session):
        """Test saving messages concurrently to same session"""
        from src.db.chat_history import save_chat_message

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            async def save_message(i):
                return save_chat_message(
                    sample_session['id'],
                    'user',
                    f'Message {i}',
                    'gpt-4',
                    10,
                    sample_session['user_id']
                )

            # Save 10 messages concurrently
            tasks = [save_message(i) for i in range(10)]
            results = await asyncio.gather(*tasks)

            assert len(results) == 10
            messages = mock_supabase_client.storage['chat_messages']
            assert len(messages) == 10

    @pytest.mark.asyncio
    async def test_concurrent_session_creation(self, mock_supabase_client, sample_user):
        """Test creating sessions concurrently"""
        from src.db.chat_history import create_chat_session

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            async def create_session(i):
                return create_chat_session(sample_user['id'], f'Session {i}')

            tasks = [create_session(i) for i in range(5)]
            results = await asyncio.gather(*tasks)

            assert len(results) == 5
            sessions = mock_supabase_client.storage['chat_sessions']
            assert len(sessions) == 5


# =========================
# Test: Error Handling
# =========================

class TestErrorHandling:
    """Tests for error scenarios"""

    def test_get_nonexistent_session(self, mock_supabase_client):
        """Test getting a session that doesn't exist"""
        from src.db.chat_history import get_chat_session

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            session = get_chat_session(session_id=99999, user_id=1)

            assert session is None

    def test_update_nonexistent_session(self, mock_supabase_client):
        """Test updating a session that doesn't exist"""
        from src.db.chat_history import update_chat_session

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            success = update_chat_session(
                session_id=99999,
                user_id=1,
                title='New Title'
            )

            assert success is False

    def test_delete_nonexistent_session(self, mock_supabase_client):
        """Test deleting a session that doesn't exist"""
        from src.db.chat_history import delete_chat_session

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            success = delete_chat_session(session_id=99999, user_id=1)

            assert success is False


# =========================
# Test: Real-World Scenarios
# =========================

class TestRealWorldScenarios:
    """Tests for real-world usage patterns"""

    def test_full_conversation_flow(self, mock_supabase_client, sample_user):
        """Test complete conversation lifecycle"""
        from src.db.chat_history import (
            create_chat_session,
            save_chat_message,
            get_chat_session,
            update_chat_session,
            delete_chat_session
        )

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            # 1. Create session
            session = create_chat_session(sample_user['id'], 'Help with Python')

            # 2. Add conversation
            save_chat_message(session['id'], 'user', 'How do I read a file?', 'gpt-4', 5, sample_user['id'])
            save_chat_message(session['id'], 'assistant', 'Use open() function', 'gpt-4', 8, sample_user['id'])
            save_chat_message(session['id'], 'user', 'Can you show an example?', 'gpt-4', 4, sample_user['id'])
            save_chat_message(session['id'], 'assistant', 'with open("file.txt") as f:', 'gpt-4', 12, sample_user['id'])

            # 3. Retrieve full conversation
            loaded = get_chat_session(session['id'], sample_user['id'])
            assert len(loaded['messages']) == 4

            # 4. Update title based on conversation
            update_chat_session(session['id'], sample_user['id'], title='Python File I/O Help')

            # 5. Verify update
            updated = get_chat_session(session['id'], sample_user['id'])
            assert updated['title'] == 'Python File I/O Help'

            # 6. Clean up
            delete_chat_session(session['id'], sample_user['id'])
            deleted = get_chat_session(session['id'], sample_user['id'])
            assert deleted is None

    def test_multi_session_user(self, mock_supabase_client, sample_user):
        """Test user with multiple concurrent sessions"""
        from src.db.chat_history import create_chat_session, save_chat_message, get_user_chat_sessions

        with patch('src.db.chat_history.get_supabase_client', return_value=mock_supabase_client):
            # User has 3 different conversations
            python_session = create_chat_session(sample_user['id'], 'Python Help')
            js_session = create_chat_session(sample_user['id'], 'JavaScript Questions')
            docker_session = create_chat_session(sample_user['id'], 'Docker Setup')

            # Add messages to each
            save_chat_message(python_session['id'], 'user', 'Python question', 'gpt-4', 5, sample_user['id'])
            save_chat_message(js_session['id'], 'user', 'JS question', 'gpt-4', 5, sample_user['id'])
            save_chat_message(docker_session['id'], 'user', 'Docker question', 'gpt-4', 5, sample_user['id'])

            # Get all sessions
            all_sessions = get_user_chat_sessions(sample_user['id'])

            assert len(all_sessions) == 3
            titles = {s['title'] for s in all_sessions}
            assert 'Python Help' in titles
            assert 'JavaScript Questions' in titles
            assert 'Docker Setup' in titles


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
