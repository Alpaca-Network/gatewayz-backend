"""Unit tests for the user_memory store (Phase 4) with a mocked Supabase client."""

from __future__ import annotations

import sys
import types

import pytest

import src.db.user_memory as um


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    """Minimal chainable stand-in for the Supabase query builder."""

    def __init__(self, store, table):
        self.store = store
        self.table = table
        self._op = None
        self._insert = None
        self._filters = {}

    def select(self, *a, **k):
        self._op = "select"
        return self

    def insert(self, row):
        self._op = "insert"
        self._insert = row
        return self

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self._op == "select":
            out = [r for r in rows if all(r.get(c) == v for c, v in self._filters.items())]
            return _Resp(sorted(out, key=lambda r: r.get("salience", 0), reverse=True))
        if self._op == "insert":
            row = dict(self._insert)
            row["id"] = len(rows) + 1
            rows.append(row)
            return _Resp([row])
        if self._op == "delete":
            removed = [r for r in rows if all(r.get(c) == v for c, v in self._filters.items())]
            self.store[self.table] = [r for r in rows if r not in removed]
            return _Resp(removed)
        return _Resp([])


class _Client:
    def __init__(self):
        self.store = {}

    def table(self, name):
        return _Query(self.store, name)


@pytest.fixture
def client(monkeypatch):
    c = _Client()
    fake_mod = types.ModuleType("src.config.supabase_config")
    fake_mod.get_supabase_client = lambda: c
    monkeypatch.setitem(sys.modules, "src.config.supabase_config", fake_mod)
    um._cache.clear()
    return c


def test_add_then_get(client):
    um.add_memory(7, "likes Python", salience=0.9)
    um.add_memory(7, "prefers metric units", salience=0.4)
    items = um.get_memories(7, use_cache=False)
    assert [i["content"] for i in items] == [
        "likes Python",
        "prefers metric units",
    ]  # salience desc


def test_get_scoped_to_user(client):
    um.add_memory(1, "fact A")
    um.add_memory(2, "fact B")
    assert [i["content"] for i in um.get_memories(1, use_cache=False)] == ["fact A"]


def test_add_rejects_empty_content(client):
    with pytest.raises(ValueError):
        um.add_memory(1, "   ")


def test_add_clamps_salience(client):
    um.add_memory(1, "x", salience=5.0)
    assert um.get_memories(1, use_cache=False)[0]["salience"] == 1.0


def test_delete_scoped_and_invalidates(client):
    um.add_memory(1, "keep")
    um.add_memory(1, "drop")
    rows = um.get_memories(1, use_cache=False)
    drop_id = next(r["id"] for r in rows if r["content"] == "drop")
    assert um.delete_memory(drop_id, 1) is True
    assert [r["content"] for r in um.get_memories(1, use_cache=False)] == ["keep"]
    # deleting another user's id does nothing
    assert um.delete_memory(999, 1) is False


def test_clear(client):
    um.add_memory(3, "a")
    um.add_memory(3, "b")
    assert um.clear_memories(3) == 2
    assert um.get_memories(3, use_cache=False) == []


def test_read_cache_hit_avoids_second_query(client, monkeypatch):
    um.add_memory(5, "cached fact")
    first = um.get_memories(5)  # populates cache
    # Break the client so a cache miss would error; cache hit must still return.
    monkeypatch.setattr(um, "get_supabase_client", None, raising=False)
    again = um.get_memories(5)
    assert again == first


def test_get_returns_empty_on_error(monkeypatch):
    um._cache.clear()
    fake_mod = types.ModuleType("src.config.supabase_config")

    def boom():
        raise RuntimeError("db down")

    fake_mod.get_supabase_client = boom
    monkeypatch.setitem(sys.modules, "src.config.supabase_config", fake_mod)
    assert um.get_memories(1, use_cache=False) == []


def test_get_none_user_is_empty():
    assert um.get_memories(None) == []
