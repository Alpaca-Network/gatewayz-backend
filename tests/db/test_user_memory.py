"""
Tests for user memory database operations.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


# =========================
# In-memory Supabase stub
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
        self._order = []
        self._range = None
        self._limit = None

    def eq(self, field, value):
        self._filters.append(("eq", field, value))
        return self

    def ilike(self, field, pattern):
        self._filters.append(("ilike", field, pattern))
        return self

    def order(self, field, desc=False, nullsfirst=True):
        self._order.append((field, bool(desc)))
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _match(self, row):
        for op, f, v in self._filters:
            rv = row.get(f)
            if op == "eq":
                if rv != v:
                    return False
            elif op == "ilike":
                s = str(rv or "")
                pat = str(v or "")
                if pat.startswith("%") and pat.endswith("%"):
                    needle = pat[1:-1].lower()
                    if needle not in s.lower():
                        return False
                else:
                    if s.lower() != pat.lower():
                        return False
        return True

    def _apply_order_range_limit(self, rows):
        for field, desc in self._order:
            rows = sorted(rows, key=lambda r: (r.get(field) is None, r.get(field)), reverse=desc)
        if self._range:
            s, e = self._range
            rows = rows[s : e + 1]
        if self._limit:
            rows = rows[: self._limit]
        return rows


class _Select(_BaseQuery):
    def select(self, *_cols):
        return self

    def execute(self):
        rows = [r for r in self.store.get(self.table, []) if self._match(r)]
        rows = self._apply_order_range_limit(rows)
        return _Result(data=rows)


class _Insert(_BaseQuery):
    def __init__(self, store, table):
        super().__init__(store, table)
        self._insert_data = None

    def insert(self, data):
        self._insert_data = data
        return self

    def execute(self):
        if self.table not in self.store:
            self.store[self.table] = []

        # Auto-assign ID
        max_id = max([r.get("id", 0) for r in self.store[self.table]], default=0)
        new_data = dict(self._insert_data)
        new_data["id"] = max_id + 1

        self.store[self.table].append(new_data)
        return _Result(data=[new_data])


class _Update(_BaseQuery):
    def __init__(self, store, table):
        super().__init__(store, table)
        self._update_data = None

    def update(self, data):
        self._update_data = data
        return self

    def execute(self):
        updated = []
        for row in self.store.get(self.table, []):
            if self._match(row):
                row.update(self._update_data)
                updated.append(row)
        return _Result(data=updated)


class _Delete(_BaseQuery):
    def delete(self):
        return self

    def execute(self):
        deleted = []
        remaining = []
        for row in self.store.get(self.table, []):
            if self._match(row):
                deleted.append(row)
            else:
                remaining.append(row)
        self.store[self.table] = remaining
        return _Result(data=deleted)


class _Table:
    def __init__(self, store, name):
        self.store = store
        self.name = name

    def select(self, *cols):
        return _Select(self.store, self.name).select(*cols)

    def insert(self, data):
        return _Insert(self.store, self.name).insert(data)

    def update(self, data):
        return _Update(self.store, self.name).update(data)

    def delete(self):
        return _Delete(self.store, self.name).delete()


class InMemorySupabase:
    def __init__(self, store: dict):
        self.store = store

    def table(self, name):
        return _Table(self.store, name)


# =========================
# Fixtures
# =========================


@pytest.fixture
def memory_store():
    """Create an empty in-memory store for testing."""
    return {"user_memories": []}


@pytest.fixture
def populated_store():
    """Create a store with sample memories."""
    return {
        "user_memories": [
            {
                "id": 1,
                "user_id": 100,
                "category": "preference",
                "content": "Prefers TypeScript over JavaScript",
                "source_session_id": 10,
                "confidence": 0.9,
                "is_active": True,
                "access_count": 5,
                "last_accessed_at": "2024-01-01T12:00:00Z",
                "created_at": "2024-01-01T10:00:00Z",
                "updated_at": "2024-01-01T10:00:00Z",
            },
            {
                "id": 2,
                "user_id": 100,
                "category": "context",
                "content": "Works as a backend engineer",
                "source_session_id": 10,
                "confidence": 0.85,
                "is_active": True,
                "access_count": 3,
                "last_accessed_at": "2024-01-02T12:00:00Z",
                "created_at": "2024-01-02T10:00:00Z",
                "updated_at": "2024-01-02T10:00:00Z",
            },
            {
                "id": 3,
                "user_id": 100,
                "category": "fact",
                "content": "Using PostgreSQL for database",
                "source_session_id": 11,
                "confidence": 0.95,
                "is_active": False,  # Soft deleted
                "access_count": 1,
                "last_accessed_at": None,
                "created_at": "2024-01-03T10:00:00Z",
                "updated_at": "2024-01-03T10:00:00Z",
            },
            {
                "id": 4,
                "user_id": 200,  # Different user
                "category": "preference",
                "content": "Prefers Python",
                "source_session_id": 20,
                "confidence": 0.8,
                "is_active": True,
                "access_count": 2,
                "last_accessed_at": "2024-01-01T12:00:00Z",
                "created_at": "2024-01-01T10:00:00Z",
                "updated_at": "2024-01-01T10:00:00Z",
            },
        ]
    }


# =========================
# Tests
# =========================


class TestCreateUserMemory:
    def test_create_memory_success(self, memory_store):
        """Test successful memory creation."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(memory_store),
        ):
            from src.db.user_memory import create_user_memory

            result = create_user_memory(
                user_id=100,
                category="preference",
                content="Likes dark mode",
                source_session_id=10,
                confidence=0.9,
            )

            assert result["id"] == 1
            assert result["user_id"] == 100
            assert result["category"] == "preference"
            assert result["content"] == "Likes dark mode"
            assert result["confidence"] == 0.9
            assert result["is_active"] is True

    def test_create_memory_invalid_category(self, memory_store):
        """Test that invalid category raises ValueError."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(memory_store),
        ):
            from src.db.user_memory import create_user_memory

            with pytest.raises(ValueError, match="Invalid category"):
                create_user_memory(
                    user_id=100,
                    category="invalid_category",
                    content="Test content",
                )

    def test_create_memory_empty_content(self, memory_store):
        """Test that empty content raises ValueError."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(memory_store),
        ):
            from src.db.user_memory import create_user_memory

            with pytest.raises(ValueError, match="content cannot be empty"):
                create_user_memory(
                    user_id=100,
                    category="preference",
                    content="",
                )

    def test_create_memory_confidence_clamped(self, memory_store):
        """Test that confidence is clamped to 0.0-1.0 range."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(memory_store),
        ):
            from src.db.user_memory import create_user_memory

            # Test > 1.0
            result = create_user_memory(
                user_id=100,
                category="fact",
                content="Test fact",
                confidence=1.5,
            )
            assert result["confidence"] == 1.0

            # Test < 0.0
            result = create_user_memory(
                user_id=100,
                category="fact",
                content="Another fact",
                confidence=-0.5,
            )
            assert result["confidence"] == 0.0


class TestGetUserMemories:
    def test_get_all_active_memories(self, populated_store):
        """Test getting all active memories for a user."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import get_user_memories

            result = get_user_memories(user_id=100, active_only=True)

            # Should return 2 active memories for user 100
            assert len(result) == 2
            assert all(m["user_id"] == 100 for m in result)
            assert all(m["is_active"] is True for m in result)

    def test_get_memories_by_category(self, populated_store):
        """Test filtering memories by category."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import get_user_memories

            result = get_user_memories(user_id=100, category="preference")

            assert len(result) == 1
            assert result[0]["category"] == "preference"

    def test_get_memories_with_limit(self, populated_store):
        """Test limiting number of returned memories."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import get_user_memories

            result = get_user_memories(user_id=100, limit=1)

            assert len(result) == 1

    def test_get_memories_invalid_category(self, populated_store):
        """Test that invalid category raises ValueError."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import get_user_memories

            with pytest.raises(ValueError, match="Invalid category"):
                get_user_memories(user_id=100, category="invalid")


class TestDeleteUserMemory:
    def test_soft_delete_memory(self, populated_store):
        """Test soft deleting a memory."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import delete_user_memory, get_memory_by_id

            result = delete_user_memory(memory_id=1, user_id=100)

            assert result is True
            memory = get_memory_by_id(memory_id=1, user_id=100)
            assert memory["is_active"] is False

    def test_delete_memory_wrong_user(self, populated_store):
        """Test that users cannot delete other users' memories."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import delete_user_memory

            result = delete_user_memory(memory_id=1, user_id=200)  # Wrong user

            assert result is False

    def test_hard_delete_memory(self, populated_store):
        """Test permanently deleting a memory."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import hard_delete_user_memory, get_memory_by_id

            result = hard_delete_user_memory(memory_id=1, user_id=100)

            assert result is True
            memory = get_memory_by_id(memory_id=1, user_id=100)
            assert memory is None


class TestDeleteAllUserMemories:
    def test_soft_delete_all_memories(self, populated_store):
        """Test soft deleting all memories for a user."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import delete_all_user_memories, get_user_memories

            deleted_count = delete_all_user_memories(user_id=100, hard_delete=False)

            assert deleted_count == 2  # 2 active memories for user 100

            # Check all are now inactive
            memories = get_user_memories(user_id=100, active_only=True)
            assert len(memories) == 0

    def test_hard_delete_all_memories(self, populated_store):
        """Test permanently deleting all memories for a user."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import delete_all_user_memories, get_user_memories

            deleted_count = delete_all_user_memories(user_id=100, hard_delete=True)

            assert deleted_count == 3  # All 3 memories for user 100 (including inactive)

            # Check all are gone
            memories = get_user_memories(user_id=100, active_only=False)
            assert len(memories) == 0

            # Other user's memories should still exist
            other_memories = get_user_memories(user_id=200)
            assert len(other_memories) == 1


class TestGetUserMemoryStats:
    def test_get_stats(self, populated_store):
        """Test getting memory statistics."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import get_user_memory_stats

            stats = get_user_memory_stats(user_id=100)

            assert stats["total_memories"] == 2  # Only active memories
            assert "preference" in stats["by_category"]
            assert stats["by_category"]["preference"] == 1


class TestSearchUserMemories:
    def test_search_memories(self, populated_store):
        """Test searching memories by content."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import search_user_memories

            results = search_user_memories(user_id=100, query="TypeScript")

            assert len(results) == 1
            assert "TypeScript" in results[0]["content"]

    def test_search_case_insensitive(self, populated_store):
        """Test that search is case-insensitive."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import search_user_memories

            results = search_user_memories(user_id=100, query="typescript")

            assert len(results) == 1


class TestCheckDuplicateMemory:
    def test_find_exact_duplicate(self, populated_store):
        """Test finding an exact duplicate memory."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import check_duplicate_memory

            result = check_duplicate_memory(
                user_id=100, content="Prefers TypeScript over JavaScript"
            )

            assert result is not None
            assert result["id"] == 1

    def test_no_duplicate_found(self, populated_store):
        """Test when no duplicate exists."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import check_duplicate_memory

            result = check_duplicate_memory(user_id=100, content="Completely new content")

            assert result is None


class TestUpdateMemoryAccess:
    def test_update_access_count(self, populated_store):
        """Test updating access count for a memory."""
        with patch(
            "src.db.user_memory.get_supabase_client",
            return_value=InMemorySupabase(populated_store),
        ):
            from src.db.user_memory import update_memory_access, get_memory_by_id

            initial = get_memory_by_id(memory_id=1, user_id=100)
            initial_count = initial["access_count"]

            result = update_memory_access(memory_id=1)

            assert result is True
            updated = get_memory_by_id(memory_id=1, user_id=100)
            assert updated["access_count"] == initial_count + 1
            assert updated["last_accessed_at"] is not None
