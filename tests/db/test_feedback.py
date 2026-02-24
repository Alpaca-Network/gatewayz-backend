"""
Tests for the message feedback database module.

Tests cover:
- Saving feedback (thumbs up, thumbs down, regenerate)
- Retrieving user feedback with filters
- Updating and deleting feedback
- Feedback statistics aggregation
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

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
        self._filters = []  # tuples (op, field, value)
        self._order = None  # (field, desc)
        self._range = None  # (start, end)

    def eq(self, field, value):
        self._filters.append(("eq", field, value))
        return self

    def gte(self, field, value):
        self._filters.append(("gte", field, value))
        return self

    def lt(self, field, value):
        self._filters.append(("lt", field, value))
        return self

    def in_(self, field, values):
        self._filters.append(("in", field, list(values)))
        return self

    def order(self, field, desc=False):
        self._order = (field, bool(desc))
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def limit(self, n):
        if self._range is None:
            self._range = (0, n - 1)
        return self

    def _match(self, row):
        for op, f, v in self._filters:
            rv = row.get(f)
            if op == "eq":
                if rv != v:
                    return False
            elif op == "gte":
                if rv is None or rv < v:
                    return False
            elif op == "lt":
                if rv is None or rv >= v:
                    return False
            elif op == "in":
                if rv not in v:
                    return False
        return True

    def _apply_order_range(self, rows):
        if self._order:
            field, desc = self._order
            rows = sorted(rows, key=lambda r: r.get(field) or "", reverse=desc)
        if self._range:
            s, e = self._range
            rows = rows[s : e + 1]
        return rows


class _Select(_BaseQuery):
    def __init__(self, store, table):
        super().__init__(store, table)
        self._count = None

    def select(self, *_cols, count=None):
        self._count = count
        return self

    def execute(self):
        rows = []
        for r in self.store[self.table]:
            if self._match(r):
                rows.append(r.copy())
        rows = self._apply_order_range(rows)
        cnt = len(rows) if self._count == "exact" else None
        return _Result(rows, cnt)


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


class _Delete(_BaseQuery):
    def execute(self):
        kept, deleted = [], []
        for r in self.store[self.table]:
            (deleted if self._match(r) else kept).append(r)
        self.store[self.table][:] = kept
        return _Result(deleted)


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

            def delete(self):
                return _Delete(self.outer.tables, self.table)

        return _Shim(self, name)


# =========================
# Fixtures / monkeypatch
# =========================


@pytest.fixture()
def sb(monkeypatch):
    import src.db.feedback as fb

    stub = SupabaseStub()
    # Patch in the module where it's actually used
    monkeypatch.setattr(fb, "get_supabase_client", lambda: stub)
    return stub


def iso_now():
    return datetime.now(UTC).isoformat()


# =========================
# Tests: Save Feedback
# =========================


def test_save_thumbs_up_feedback(sb):
    import src.db.feedback as fb

    feedback = fb.save_message_feedback(
        user_id=1,
        feedback_type="thumbs_up",
        session_id=100,
        message_id=200,
        model="openai/gpt-4o",
    )
    assert feedback["user_id"] == 1
    assert feedback["feedback_type"] == "thumbs_up"
    assert feedback["session_id"] == 100
    assert feedback["message_id"] == 200
    assert feedback["model"] == "openai/gpt-4o"
    assert "id" in feedback


def test_save_thumbs_down_feedback_with_comment(sb):
    import src.db.feedback as fb

    feedback = fb.save_message_feedback(
        user_id=1,
        feedback_type="thumbs_down",
        comment="The response was not helpful",
        rating=2,
    )
    assert feedback["feedback_type"] == "thumbs_down"
    assert feedback["comment"] == "The response was not helpful"
    assert feedback["rating"] == 2


def test_save_regenerate_feedback_with_metadata(sb):
    import src.db.feedback as fb

    metadata = {
        "original_response": "Some text",
        "prompt": "What is AI?",
        "response_time_ms": 450,
    }
    feedback = fb.save_message_feedback(
        user_id=1,
        feedback_type="regenerate",
        metadata=metadata,
    )
    assert feedback["feedback_type"] == "regenerate"
    assert feedback["metadata"] == metadata


def test_save_feedback_invalid_type_raises_error(sb):
    import src.db.feedback as fb

    with pytest.raises(ValueError) as exc_info:
        fb.save_message_feedback(user_id=1, feedback_type="invalid_type")
    assert "Invalid feedback_type" in str(exc_info.value)


def test_save_feedback_invalid_rating_raises_error(sb):
    import src.db.feedback as fb

    with pytest.raises(ValueError) as exc_info:
        fb.save_message_feedback(user_id=1, feedback_type="thumbs_up", rating=6)
    assert "Rating must be between 1 and 5" in str(exc_info.value)

    with pytest.raises(ValueError) as exc_info:
        fb.save_message_feedback(user_id=1, feedback_type="thumbs_up", rating=0)
    assert "Rating must be between 1 and 5" in str(exc_info.value)


# =========================
# Tests: Get Feedback
# =========================


def test_get_user_feedback_all(sb):
    import src.db.feedback as fb

    # Create multiple feedback records
    fb.save_message_feedback(user_id=1, feedback_type="thumbs_up")
    fb.save_message_feedback(user_id=1, feedback_type="thumbs_down")
    fb.save_message_feedback(user_id=2, feedback_type="thumbs_up")

    # Get user 1's feedback
    feedback_list = fb.get_user_feedback(user_id=1)
    assert len(feedback_list) == 2
    assert all(f["user_id"] == 1 for f in feedback_list)


def test_get_user_feedback_by_type(sb):
    import src.db.feedback as fb

    fb.save_message_feedback(user_id=1, feedback_type="thumbs_up")
    fb.save_message_feedback(user_id=1, feedback_type="thumbs_down")
    fb.save_message_feedback(user_id=1, feedback_type="thumbs_up")

    # Filter by thumbs_up
    feedback_list = fb.get_user_feedback(user_id=1, feedback_type="thumbs_up")
    assert len(feedback_list) == 2
    assert all(f["feedback_type"] == "thumbs_up" for f in feedback_list)


def test_get_user_feedback_by_session(sb):
    import src.db.feedback as fb

    fb.save_message_feedback(user_id=1, feedback_type="thumbs_up", session_id=100)
    fb.save_message_feedback(user_id=1, feedback_type="thumbs_up", session_id=200)
    fb.save_message_feedback(user_id=1, feedback_type="thumbs_down", session_id=100)

    # Filter by session 100
    feedback_list = fb.get_user_feedback(user_id=1, session_id=100)
    assert len(feedback_list) == 2
    assert all(f["session_id"] == 100 for f in feedback_list)


def test_get_user_feedback_by_model(sb):
    import src.db.feedback as fb

    fb.save_message_feedback(user_id=1, feedback_type="thumbs_up", model="gpt-4")
    fb.save_message_feedback(user_id=1, feedback_type="thumbs_up", model="claude-3")
    fb.save_message_feedback(user_id=1, feedback_type="thumbs_down", model="gpt-4")

    # Filter by gpt-4
    feedback_list = fb.get_user_feedback(user_id=1, model="gpt-4")
    assert len(feedback_list) == 2
    assert all(f["model"] == "gpt-4" for f in feedback_list)


def test_get_user_feedback_pagination(sb):
    import src.db.feedback as fb

    # Create 5 feedback records
    for i in range(5):
        fb.save_message_feedback(user_id=1, feedback_type="thumbs_up")

    # Get with pagination
    page1 = fb.get_user_feedback(user_id=1, limit=2, offset=0)
    page2 = fb.get_user_feedback(user_id=1, limit=2, offset=2)

    assert len(page1) == 2
    assert len(page2) == 2

    # No overlap
    ids1 = {f["id"] for f in page1}
    ids2 = {f["id"] for f in page2}
    assert ids1.isdisjoint(ids2)


def test_get_feedback_by_session(sb):
    import src.db.feedback as fb

    fb.save_message_feedback(user_id=1, feedback_type="thumbs_up", session_id=100)
    fb.save_message_feedback(user_id=1, feedback_type="thumbs_down", session_id=100)
    fb.save_message_feedback(user_id=2, feedback_type="thumbs_up", session_id=100)

    # Get user 1's feedback for session 100
    feedback_list = fb.get_feedback_by_session(session_id=100, user_id=1)
    assert len(feedback_list) == 2
    assert all(f["user_id"] == 1 for f in feedback_list)
    assert all(f["session_id"] == 100 for f in feedback_list)


def test_get_feedback_by_message(sb):
    import src.db.feedback as fb

    fb.save_message_feedback(user_id=1, feedback_type="thumbs_up", message_id=500)
    fb.save_message_feedback(user_id=1, feedback_type="thumbs_down", message_id=500)
    fb.save_message_feedback(user_id=2, feedback_type="thumbs_up", message_id=500)

    # Get all feedback for message 500
    feedback_list = fb.get_feedback_by_message(message_id=500)
    assert len(feedback_list) == 3

    # Get only user 1's feedback for message 500
    feedback_list = fb.get_feedback_by_message(message_id=500, user_id=1)
    assert len(feedback_list) == 2
    assert all(f["user_id"] == 1 for f in feedback_list)


# =========================
# Tests: Update Feedback
# =========================


def test_update_feedback_type(sb):
    import src.db.feedback as fb

    feedback = fb.save_message_feedback(user_id=1, feedback_type="thumbs_up")
    feedback_id = feedback["id"]

    updated = fb.update_feedback(feedback_id=feedback_id, user_id=1, feedback_type="thumbs_down")
    assert updated is not None
    assert updated["feedback_type"] == "thumbs_down"


def test_update_feedback_rating(sb):
    import src.db.feedback as fb

    feedback = fb.save_message_feedback(user_id=1, feedback_type="thumbs_up", rating=3)

    updated = fb.update_feedback(feedback_id=feedback["id"], user_id=1, rating=5)
    assert updated["rating"] == 5


def test_update_feedback_comment(sb):
    import src.db.feedback as fb

    feedback = fb.save_message_feedback(user_id=1, feedback_type="thumbs_up")

    updated = fb.update_feedback(
        feedback_id=feedback["id"], user_id=1, comment="Changed my mind, it was great!"
    )
    assert updated["comment"] == "Changed my mind, it was great!"


def test_update_feedback_wrong_user_returns_none(sb):
    import src.db.feedback as fb

    feedback = fb.save_message_feedback(user_id=1, feedback_type="thumbs_up")

    # Try to update with wrong user
    updated = fb.update_feedback(feedback_id=feedback["id"], user_id=2, rating=5)
    assert updated is None


def test_update_feedback_invalid_type_raises_error(sb):
    import src.db.feedback as fb

    feedback = fb.save_message_feedback(user_id=1, feedback_type="thumbs_up")

    with pytest.raises(ValueError):
        fb.update_feedback(feedback_id=feedback["id"], user_id=1, feedback_type="invalid")


def test_update_feedback_invalid_rating_raises_error(sb):
    import src.db.feedback as fb

    feedback = fb.save_message_feedback(user_id=1, feedback_type="thumbs_up")

    with pytest.raises(ValueError):
        fb.update_feedback(feedback_id=feedback["id"], user_id=1, rating=6)


# =========================
# Tests: Delete Feedback
# =========================


def test_delete_feedback_success(sb):
    import src.db.feedback as fb

    feedback = fb.save_message_feedback(user_id=1, feedback_type="thumbs_up")
    feedback_id = feedback["id"]

    result = fb.delete_feedback(feedback_id=feedback_id, user_id=1)
    assert result is True

    # Verify it's gone
    feedback_list = fb.get_user_feedback(user_id=1)
    assert len(feedback_list) == 0


def test_delete_feedback_wrong_user_returns_false(sb):
    import src.db.feedback as fb

    feedback = fb.save_message_feedback(user_id=1, feedback_type="thumbs_up")

    # Try to delete with wrong user
    result = fb.delete_feedback(feedback_id=feedback["id"], user_id=2)
    assert result is False

    # Verify it still exists
    feedback_list = fb.get_user_feedback(user_id=1)
    assert len(feedback_list) == 1


def test_delete_feedback_nonexistent_returns_false(sb):
    import src.db.feedback as fb

    result = fb.delete_feedback(feedback_id=99999, user_id=1)
    assert result is False


# =========================
# Tests: Feedback Statistics
# =========================


def test_get_feedback_stats_basic(sb):
    import src.db.feedback as fb

    # Create feedback records with recent timestamps
    base_time = datetime.now(UTC)

    # Insert directly with controlled timestamps
    for i, ftype in enumerate(["thumbs_up", "thumbs_up", "thumbs_down", "regenerate"]):
        sb.table("message_feedback").insert(
            {
                "user_id": 1,
                "feedback_type": ftype,
                "created_at": (base_time - timedelta(days=i)).isoformat(),
            }
        ).execute()

    stats = fb.get_feedback_stats(user_id=1, days=30)
    assert stats["total_feedback"] == 4
    assert stats["thumbs_up"] == 2
    assert stats["thumbs_down"] == 1
    assert stats["regenerate"] == 1
    assert stats["thumbs_up_rate"] == 50.0
    assert stats["thumbs_down_rate"] == 25.0


def test_get_feedback_stats_with_ratings(sb):
    import src.db.feedback as fb

    base_time = datetime.now(UTC)

    # Insert with ratings
    for i, rating in enumerate([5, 4, 3]):
        sb.table("message_feedback").insert(
            {
                "user_id": 1,
                "feedback_type": "thumbs_up",
                "rating": rating,
                "created_at": (base_time - timedelta(days=i)).isoformat(),
            }
        ).execute()

    stats = fb.get_feedback_stats(user_id=1, days=30)
    assert stats["average_rating"] == 4.0  # (5+4+3)/3


def test_get_feedback_stats_by_model(sb):
    import src.db.feedback as fb

    base_time = datetime.now(UTC)

    # Insert feedback for different models
    models = ["gpt-4", "gpt-4", "claude-3", "gpt-4"]
    for i, model in enumerate(models):
        sb.table("message_feedback").insert(
            {
                "user_id": 1,
                "feedback_type": "thumbs_up" if i % 2 == 0 else "thumbs_down",
                "model": model,
                "created_at": (base_time - timedelta(days=i)).isoformat(),
            }
        ).execute()

    stats = fb.get_feedback_stats(user_id=1, days=30)

    assert "by_model" in stats
    assert "gpt-4" in stats["by_model"]
    assert stats["by_model"]["gpt-4"]["total"] == 3
    assert "claude-3" in stats["by_model"]
    assert stats["by_model"]["claude-3"]["total"] == 1


def test_get_feedback_stats_filter_by_model(sb):
    import src.db.feedback as fb

    base_time = datetime.now(UTC)

    # Insert feedback for different models
    for i, model in enumerate(["gpt-4", "claude-3"]):
        sb.table("message_feedback").insert(
            {
                "user_id": 1,
                "feedback_type": "thumbs_up",
                "model": model,
                "created_at": (base_time - timedelta(days=i)).isoformat(),
            }
        ).execute()

    stats = fb.get_feedback_stats(user_id=1, model="gpt-4", days=30)
    assert stats["total_feedback"] == 1


def test_get_feedback_stats_empty(sb):
    import src.db.feedback as fb

    stats = fb.get_feedback_stats(user_id=999, days=30)
    assert stats["total_feedback"] == 0
    assert stats["thumbs_up"] == 0
    assert stats["thumbs_down"] == 0
    assert stats["average_rating"] is None
