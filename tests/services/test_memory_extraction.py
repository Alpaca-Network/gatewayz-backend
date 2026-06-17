"""Unit tests for heuristic user-memory capture (Phase 4 populate)."""

from __future__ import annotations

import sys
import types

import pytest

import src.services.memory_extraction as me


def _user(text):
    return {"role": "user", "content": text}


# --------------------------------------------------------------------------- #
# extract_candidate_facts (pure)
# --------------------------------------------------------------------------- #

def test_extracts_name():
    facts = me.extract_candidate_facts([_user("Hello, my name is Ada Lovelace.")])
    contents = [c for c, _, _ in facts]
    assert "User's name is Ada Lovelace" in contents


def test_extracts_remember_statement():
    facts = me.extract_candidate_facts([_user("Please remember that I deploy on Fridays")])
    assert any("deploy on Fridays" in c for c, _, _ in facts)


def test_extracts_preference():
    facts = me.extract_candidate_facts([_user("I prefer Python over Java")])
    assert any(c.startswith("User prefers Python") for c, _, _ in facts)


def test_ignores_assistant_and_system_messages():
    msgs = [
        {"role": "system", "content": "my name is System"},
        {"role": "assistant", "content": "my name is Claude"},
        _user("just a normal question with no facts?"),
    ]
    assert me.extract_candidate_facts(msgs) == []


def test_dedupes_repeated_facts():
    facts = me.extract_candidate_facts([_user("my name is Sam"), _user("my name is Sam")])
    names = [c for c, _, _ in facts if "name is Sam" in c]
    assert len(names) == 1


def test_caps_candidates():
    text = " ".join(f"remember that fact number {i} is true" for i in range(20))
    facts = me.extract_candidate_facts([_user(text)])
    assert len(facts) <= me._MAX_CANDIDATES_PER_CALL


def test_handles_multimodal_content():
    msg = {"role": "user", "content": [{"type": "text", "text": "my name is Grace"}, {"type": "image"}]}
    facts = me.extract_candidate_facts([msg])
    assert any("Grace" in c for c, _, _ in facts)


def test_empty_messages():
    assert me.extract_candidate_facts([]) == []


# --------------------------------------------------------------------------- #
# capture_user_memory (I/O, mocked store)
# --------------------------------------------------------------------------- #

def _patch_store(monkeypatch, existing):
    store = {"rows": list(existing)}

    def get_memories(user_id, limit=200, **k):
        return store["rows"]

    def add_memory(user_id, content, *, kind="fact", salience=0.5):
        row = {"content": content, "kind": kind, "salience": salience}
        store["rows"].append(row)
        return row

    fake = types.ModuleType("src.db.user_memory")
    fake.get_memories = get_memories
    fake.add_memory = add_memory
    monkeypatch.setitem(sys.modules, "src.db.user_memory", fake)
    return store


def test_capture_stores_new_facts(monkeypatch):
    store = _patch_store(monkeypatch, existing=[])
    n = me.capture_user_memory(1, [_user("my name is Ada and remember that I like tea")])
    assert n >= 1
    assert any("Ada" in r["content"] for r in store["rows"])


def test_capture_skips_existing(monkeypatch):
    _patch_store(monkeypatch, existing=[{"content": "User's name is Ada"}])
    n = me.capture_user_memory(1, [_user("my name is Ada")])
    assert n == 0


def test_capture_respects_cap(monkeypatch):
    from src.config import Config

    monkeypatch.setattr(Config, "MEMORY_MAX_PER_USER", 0, raising=False)
    n = me.capture_user_memory(1, [_user("my name is Ada")])
    assert n == 0


def test_capture_none_user_is_noop(monkeypatch):
    _patch_store(monkeypatch, existing=[])
    assert me.capture_user_memory(None, [_user("my name is Ada")]) == 0


def test_capture_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    fake = types.ModuleType("src.db.user_memory")
    fake.get_memories = boom
    fake.add_memory = boom
    monkeypatch.setitem(sys.modules, "src.db.user_memory", fake)
    # extraction yields a candidate, but the store blows up → swallowed, returns 0
    assert me.capture_user_memory(1, [_user("my name is Ada")]) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
