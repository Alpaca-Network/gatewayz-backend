"""
Unit tests for chat history message deduplication functionality.

Tests the duplicate detection code paths in save_chat_message to ensure proper coverage.
Uses the same in-memory Supabase stub as test_chat_history.py for consistency.
"""

import pytest
from datetime import datetime, timezone, timezone, UTC

# =========================
# In-memory Supabase stub (same as test_chat_history.py)
# =========================

class _Result:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count
    def execute(self):
        return self

class _BaseQuery:
    def __init__(self, store, table):
        self.store = store
        self.table = table
        self._filters = []
        self._order = None
        self._limit = None

    def eq(self, field, value):
        self._filters.append(("eq", field, value)); return self
    def gte(self, field, value):
        self._filters.append(("gte", field, value)); return self
    def order(self, field, desc=False):
        self._order = (field, bool(desc)); return self
    def limit(self, n):
        self._limit = n; return self

    def _match(self, row):
        for op, f, v in self._filters:
            rv = row.get(f)
            if op == "eq" and rv != v:
                return False
            elif op == "gte" and (rv is None or rv < v):
                return False
        return True

    def _apply_order_limit(self, rows):
        if self._order:
            field, desc = self._order
            rows = sorted(rows, key=lambda r: r.get(field, ""), reverse=desc)
        if self._limit:
            rows = rows[:self._limit]
        return rows

class _Select(_BaseQuery):
    def __init__(self, store, table):
        super().__init__(store, table)
    def select(self, *_cols, count=None):
        return self
    def execute(self):
        rows = [r.copy() for r in self.store[self.table] if self._match(r)]
        rows = self._apply_order_limit(rows)
        return _Result(rows)

class _Insert:
    def __init__(self, store, table, payload):
        self.store = store
        self.table = table
        self.payload = payload
    def execute(self):
        inserted = []
        items = self.payload if isinstance(self.payload, list) else [self.payload]
        next_id = max([r.get("id", 0) for r in self.store[self.table]] or [0]) + 1
        for item in items:
            row = item.copy()
            if "id" not in row:
                row["id"] = next_id
                next_id += 1
            # Ensure created_at is set if not provided
            if "created_at" not in row:
                row["created_at"] = datetime.now(UTC).isoformat()
            self.store[self.table].append(row)
            inserted.append(row.copy())
        return _Result(inserted)

class _Update(_BaseQuery):
    def __init__(self, store, table, payload):
        super().__init__(store, table)
        self.payload = payload
    def execute(self):
        updated = []
        for r in self.store[self.table]:
            if self._match(r):
                r.update(self.payload)
                updated.append(r.copy())
        return _Result(updated)

class SupabaseStub:
    def __init__(self):
        from collections import defaultdict
        self.tables = defaultdict(list)

    def table(self, name):
        class _Shim:
            def __init__(self, outer, table):
                self.outer = outer
                self.table = table
            def select(self, *cols, count=None):
                return _Select(self.outer.tables, self.table).select(*cols, count=count)
            def insert(self, payload):
                return _Insert(self.outer.tables, self.table, payload)
            def update(self, payload):
                return _Update(self.outer.tables, self.table, payload)
        return _Shim(self, name)


@pytest.fixture()
def sb(monkeypatch):
    import src.db.chat_history as ch
    stub = SupabaseStub()
    monkeypatch.setattr(ch, "get_supabase_client", lambda: stub)
    return stub


# =========================
# Deduplication Tests
# =========================

def test_save_message_no_duplicate_normal_path(sb):
    """Test normal save when no duplicate exists"""
    import src.db.chat_history as ch

    # Create session first
    ch.create_chat_session(user_id=1, title="Test")

    # Save a message
    msg = ch.save_chat_message(
        session_id=1,
        role='user',
        content='Hello world',
        model='gpt-4',
        tokens=5,
        user_id=1
    )

    # Verify message was saved
    assert msg is not None
    assert msg['content'] == 'Hello world'
    assert msg['role'] == 'user'
    assert msg['session_id'] == 1

    # Verify it's in the database
    messages = sb.tables['chat_messages']
    assert len(messages) == 1
    assert messages[0]['content'] == 'Hello world'


def test_save_message_duplicate_detected_returns_existing(sb):
    """Test that duplicate message returns existing instead of creating new"""
    import src.db.chat_history as ch

    # Create a session first (required for session update to work)
    ch.create_chat_session(user_id=1, title="Test Session")

    # Save first message
    msg1 = ch.save_chat_message(
        session_id=1,
        role='user',
        content='Duplicate test',
        model='gpt-4',
        tokens=5,
        user_id=1
    )
    first_id = msg1['id']

    # Try to save same message again (within 5 minute window)
    msg2 = ch.save_chat_message(
        session_id=1,
        role='user',
        content='Duplicate test',
        model='gpt-4',
        tokens=5,
        user_id=1
    )

    # Should return the existing message
    assert msg2['id'] == first_id, f"Expected same ID {first_id}, got {msg2['id']}. Messages in DB: {len(sb.tables['chat_messages'])}"
    assert msg2['content'] == 'Duplicate test'

    # Verify only one message in database
    messages = sb.tables['chat_messages']
    assert len(messages) == 1


def test_save_message_skip_duplicate_check_creates_new(sb):
    """Test that skip_duplicate_check=True bypasses duplicate detection"""
    import src.db.chat_history as ch

    # Create session first
    ch.create_chat_session(user_id=1, title="Test")

    # Save first message
    msg1 = ch.save_chat_message(
        session_id=1,
        role='user',
        content='Skip check test',
        model='gpt-4',
        tokens=5,
        user_id=1
    )

    # Save again with skip_duplicate_check=True
    msg2 = ch.save_chat_message(
        session_id=1,
        role='user',
        content='Skip check test',
        model='gpt-4',
        tokens=5,
        user_id=1,
        skip_duplicate_check=True  # Bypass duplicate detection
    )

    # Should create a new message
    assert msg2['id'] != msg1['id']

    # Verify two messages in database
    messages = sb.tables['chat_messages']
    assert len(messages) == 2
    assert all(m['content'] == 'Skip check test' for m in messages)


def test_save_message_empty_content_allowed(sb):
    """Test that empty content is allowed and saved"""
    import src.db.chat_history as ch

    # Create session first
    ch.create_chat_session(user_id=1, title="Test")

    # Save message with empty content
    msg = ch.save_chat_message(
        session_id=1,
        role='user',
        content='',
        model='gpt-4',
        tokens=0,
        user_id=1
    )

    # Should save successfully
    assert msg is not None
    assert msg['content'] == ''

    # Verify in database
    messages = sb.tables['chat_messages']
    assert len(messages) == 1
    assert messages[0]['content'] == ''


def test_save_message_different_sessions_not_duplicate(sb):
    """Test that same content in different sessions is not considered duplicate"""
    import src.db.chat_history as ch

    # Create two sessions
    ch.create_chat_session(user_id=1, title="Session 1")
    ch.create_chat_session(user_id=1, title="Session 2")

    # Save message to session 1
    msg1 = ch.save_chat_message(
        session_id=1,
        role='user',
        content='Same content',
        model='gpt-4',
        tokens=5,
        user_id=1
    )

    # Save same content to session 2
    msg2 = ch.save_chat_message(
        session_id=2,
        role='user',
        content='Same content',
        model='gpt-4',
        tokens=5,
        user_id=1
    )

    # Should create separate messages
    assert msg1['id'] != msg2['id']
    assert msg1['session_id'] == 1
    assert msg2['session_id'] == 2

    # Verify two messages in database
    messages = sb.tables['chat_messages']
    assert len(messages) == 2


def test_save_message_different_roles_not_duplicate(sb):
    """Test that same content with different roles is not considered duplicate"""
    import src.db.chat_history as ch

    # Create session first
    ch.create_chat_session(user_id=1, title="Test")

    # Save user message
    msg1 = ch.save_chat_message(
        session_id=1,
        role='user',
        content='Same text',
        model='gpt-4',
        tokens=5,
        user_id=1
    )

    # Save assistant message with same content
    msg2 = ch.save_chat_message(
        session_id=1,
        role='assistant',
        content='Same text',
        model='gpt-4',
        tokens=5,
        user_id=1
    )

    # Should create separate messages
    assert msg1['id'] != msg2['id']
    assert msg1['role'] == 'user'
    assert msg2['role'] == 'assistant'

    # Verify two messages in database
    messages = sb.tables['chat_messages']
    assert len(messages) == 2


def test_save_message_updates_session_timestamp(sb):
    """Test that saving a message updates session updated_at"""
    import src.db.chat_history as ch

    # Create a session first
    session = ch.create_chat_session(user_id=1, title="Test Session")
    original_updated_at = session['updated_at']

    # Small delay to ensure timestamp changes
    import time
    time.sleep(0.01)

    # Save a message
    ch.save_chat_message(
        session_id=session['id'],
        role='user',
        content='Update test',
        model='gpt-4',
        tokens=5,
        user_id=1
    )

    # Get updated session
    sessions = sb.tables['chat_sessions']
    updated_session = [s for s in sessions if s['id'] == session['id']][0]

    # Verify timestamp was updated
    assert updated_session['updated_at'] >= original_updated_at


def test_save_message_with_model_updates_session_model(sb):
    """Test that saving a message with model updates session model"""
    import src.db.chat_history as ch

    # Create a session
    session = ch.create_chat_session(user_id=1, title="Test Session", model="gpt-3.5-turbo")

    # Save message with different model
    ch.save_chat_message(
        session_id=session['id'],
        role='user',
        content='Model test',
        model='gpt-4',  # Different model
        tokens=5,
        user_id=1
    )

    # Get updated session
    sessions = sb.tables['chat_sessions']
    updated_session = [s for s in sessions if s['id'] == session['id']][0]

    # Verify model was updated
    assert updated_session['model'] == 'gpt-4'
