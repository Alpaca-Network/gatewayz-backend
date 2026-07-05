"""Unit tests for the context-assembly bridge (Phase 4 wiring, item 3)."""

from __future__ import annotations

from src.services.context_assembly import MemoryItem
from src.services.context_assembly_bridge import (
    apply_context_budget,
    split_messages,
)

# --------------------------------------------------------------------------- #
# split_messages
# --------------------------------------------------------------------------- #


def test_split_separates_system_from_turns():
    msgs = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    system, turns = split_messages(msgs)
    assert system == "be brief"
    assert turns == msgs[1:]


def test_split_merges_multiple_system_messages():
    msgs = [
        {"role": "system", "content": "rule 1"},
        {"role": "system", "content": "rule 2"},
        {"role": "user", "content": "hi"},
    ]
    system, turns = split_messages(msgs)
    assert system == "rule 1\n\nrule 2"
    assert len(turns) == 1


def test_split_no_system_returns_none():
    msgs = [{"role": "user", "content": "hi"}]
    system, turns = split_messages(msgs)
    assert system is None
    assert turns == msgs


def test_split_flattens_multimodal_system_content():
    msgs = [{"role": "system", "content": [{"type": "text", "text": "hello"}, {"type": "image"}]}]
    system, _ = split_messages(msgs)
    assert system == "hello"


# --------------------------------------------------------------------------- #
# apply_context_budget
# --------------------------------------------------------------------------- #


def test_empty_messages_passthrough():
    assert apply_context_budget([]) == []


def test_ample_budget_no_memory_preserves_turn_order():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
    ]
    out = apply_context_budget(msgs, token_budget=100000, memory_items=[])
    assert out[0] == {"role": "system", "content": "sys"}
    # turns preserved in order after the system message
    assert [m["content"] for m in out[1:]] == ["q1", "a1", "q2"]


def test_tight_budget_drops_oldest_turns():
    msgs = [
        {"role": "user", "content": "x" * 400},  # ~100 tokens
        {"role": "user", "content": "y" * 400},  # ~100 tokens
        {"role": "user", "content": "z" * 40},  # ~10 tokens (newest)
    ]
    out = apply_context_budget(msgs, token_budget=60, memory_items=[])
    contents = [m["content"] for m in out]
    # newest kept, oldest dropped
    assert "z" * 40 in contents
    assert "x" * 400 not in contents


def test_memory_items_injected_as_system_note():
    msgs = [{"role": "user", "content": "hi"}]
    mem = [MemoryItem(content="user likes Python", salience=0.9)]
    out = apply_context_budget(msgs, token_budget=100000, memory_items=mem)
    joined = " ".join(m.get("content", "") for m in out if isinstance(m.get("content"), str))
    assert "user likes Python" in joined


def test_failure_returns_original_messages(monkeypatch):
    msgs = [{"role": "user", "content": "hi"}]

    def boom(*a, **k):
        raise RuntimeError("budget calc failed")

    monkeypatch.setattr("src.services.context_assembly_bridge.assemble_context", boom)
    # memory_items=[] avoids the DB path; the assemble step raises → original returned
    assert apply_context_budget(msgs, token_budget=100, memory_items=[]) == msgs


def test_rolling_summary_included_when_provided():
    msgs = [{"role": "user", "content": "hi"}]
    out = apply_context_budget(
        msgs, token_budget=100000, memory_items=[], rolling_summary="earlier we discussed X"
    )
    joined = " ".join(m.get("content", "") for m in out if isinstance(m.get("content"), str))
    assert "earlier we discussed X" in joined
