"""Unit tests for the Phase 4 context-assembly logic (pure, no I/O)."""

import pytest

from src.services.context_assembly import (
    MEMORY_PREAMBLE,
    SUMMARY_PREAMBLE,
    AssembledContext,
    ContextUsage,
    MemoryItem,
    assemble_context,
    estimate_tokens,
    should_summarize,
)


# Deterministic word-count counter for predictable budgeting in tests.
def wc(text):
    return len(text.split()) if text else 0


def turn(role, content):
    return {"role": role, "content": content}


# --------------------------------------------------------------------------- #
# estimate_tokens (default char/4 heuristic)
# --------------------------------------------------------------------------- #
def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0


def test_estimate_tokens_char_over_four():
    assert estimate_tokens("a" * 8) == 2
    assert estimate_tokens("abc") == 1  # min 1 for non-empty


# --------------------------------------------------------------------------- #
# Budget validation
# --------------------------------------------------------------------------- #
def test_assemble_rejects_nonpositive_budget():
    with pytest.raises(ValueError):
        assemble_context(recent_turns=[], token_budget=0)
    with pytest.raises(ValueError):
        assemble_context(recent_turns=[], token_budget=-5)


def test_should_summarize_rejects_nonpositive_budget():
    with pytest.raises(ValueError):
        should_summarize([], 0)


# --------------------------------------------------------------------------- #
# System message — always included, first
# --------------------------------------------------------------------------- #
def test_system_message_always_included_even_over_budget():
    ctx = assemble_context(
        recent_turns=[turn("user", "one two three")],
        system_message="sys a b c d e",  # 6 words > budget
        token_budget=3,
        token_counter=wc,
    )
    assert ctx.messages[0] == {"role": "system", "content": "sys a b c d e"}
    assert ctx.usage.system_tokens == 6
    # no room left for the turn
    assert ctx.usage.turns_included == 0
    assert ctx.usage.turns_dropped == 1


# --------------------------------------------------------------------------- #
# Recent turns — newest kept, oldest dropped, chronological order
# --------------------------------------------------------------------------- #
def test_keeps_newest_turns_drops_oldest():
    turns = [turn("user", "t1 a"), turn("assistant", "t2 b"), turn("user", "t3 c")]
    # each turn costs wc(content)=2 + wc(role)=1 = 3 tokens; budget 7 fits 2 turns
    ctx = assemble_context(recent_turns=turns, token_budget=7, token_counter=wc)
    contents = [m["content"] for m in ctx.messages]
    assert contents == ["t2 b", "t3 c"]  # chronological, oldest (t1) dropped
    assert ctx.usage.turns_included == 2
    assert ctx.usage.turns_dropped == 1
    assert ctx.usage.turn_tokens == 6


def test_all_turns_kept_when_budget_ample():
    turns = [turn("user", "a"), turn("assistant", "b"), turn("user", "c")]
    ctx = assemble_context(recent_turns=turns, token_budget=1000, token_counter=wc)
    assert [m["content"] for m in ctx.messages] == ["a", "b", "c"]
    assert ctx.usage.turns_dropped == 0


def test_turns_remain_chronological():
    turns = [turn("user", "first"), turn("assistant", "second"), turn("user", "third")]
    ctx = assemble_context(recent_turns=turns, token_budget=1000, token_counter=wc)
    assert [m["content"] for m in ctx.messages] == ["first", "second", "third"]


# --------------------------------------------------------------------------- #
# Rolling summary
# --------------------------------------------------------------------------- #
def test_summary_included_and_placed_before_turns():
    ctx = assemble_context(
        recent_turns=[turn("user", "hello")],
        rolling_summary="prior stuff happened",
        token_budget=1000,
        token_counter=wc,
    )
    roles_contents = [(m["role"], m["content"]) for m in ctx.messages]
    assert ("system", f"{SUMMARY_PREAMBLE} prior stuff happened") in roles_contents
    # summary precedes the user turn
    summary_idx = next(i for i, m in enumerate(ctx.messages) if SUMMARY_PREAMBLE in m["content"])
    turn_idx = next(i for i, m in enumerate(ctx.messages) if m["content"] == "hello")
    assert summary_idx < turn_idx
    assert ctx.usage.summary_included is True


def test_summary_dropped_when_no_room():
    # budget exactly fits the single turn (2+1=3); summary cannot fit.
    ctx = assemble_context(
        recent_turns=[turn("user", "keep me")],
        rolling_summary="a b c d e f g",
        token_budget=3,
        token_counter=wc,
    )
    assert ctx.usage.summary_included is False
    assert all(SUMMARY_PREAMBLE not in m["content"] for m in ctx.messages)
    assert ctx.usage.turns_included == 1


# --------------------------------------------------------------------------- #
# Memory — salience ordering + partial inclusion + placement
# --------------------------------------------------------------------------- #
def test_memory_included_by_salience_and_placed_after_system():
    ctx = assemble_context(
        recent_turns=[turn("user", "hi")],
        system_message="sys",
        memory_items=[
            MemoryItem("low salience fact", salience=0.1),
            MemoryItem("high", salience=0.9),
        ],
        token_budget=1000,
        token_counter=wc,
    )
    # system first, then the memory note
    assert ctx.messages[0]["content"] == "sys"
    assert ctx.messages[1]["content"].startswith(MEMORY_PREAMBLE)
    # both memory items present; high-salience listed first
    mem = ctx.messages[1]["content"]
    assert mem.index("high") < mem.index("low salience fact")
    assert ctx.usage.memory_included == 2


def test_memory_partial_inclusion_prefers_high_salience():
    # budget leaves room for only one memory item after the turn.
    ctx = assemble_context(
        recent_turns=[turn("user", "x")],  # 1+1 = 2 tokens
        memory_items=[
            MemoryItem("aaa bbb ccc", salience=0.2),  # 3 tokens
            MemoryItem("kept", salience=0.95),  # 1 token
        ],
        token_budget=4,  # 2 for turn, room for the 1-token high-salience item only
        token_counter=wc,
    )
    assert ctx.usage.memory_included == 1
    mem_msgs = [m for m in ctx.messages if m["content"].startswith(MEMORY_PREAMBLE)]
    assert "kept" in mem_msgs[0]["content"]
    assert "aaa bbb ccc" not in mem_msgs[0]["content"]


def test_no_memory_message_when_none_fit_or_given():
    ctx = assemble_context(recent_turns=[turn("user", "hi")], token_budget=100, token_counter=wc)
    assert all(not m["content"].startswith(MEMORY_PREAMBLE) for m in ctx.messages)
    assert ctx.usage.memory_included == 0


# --------------------------------------------------------------------------- #
# Final ordering: system, memory, summary, turns
# --------------------------------------------------------------------------- #
def test_full_ordering():
    ctx = assemble_context(
        recent_turns=[turn("user", "u1"), turn("assistant", "a1")],
        system_message="SYS",
        rolling_summary="older",
        memory_items=[MemoryItem("fact", salience=0.8)],
        token_budget=1000,
        token_counter=wc,
    )
    kinds = []
    for m in ctx.messages:
        c = m["content"]
        if c == "SYS":
            kinds.append("system")
        elif c.startswith(MEMORY_PREAMBLE):
            kinds.append("memory")
        elif c.startswith(SUMMARY_PREAMBLE):
            kinds.append("summary")
        else:
            kinds.append("turn")
    assert kinds == ["system", "memory", "summary", "turn", "turn"]


# --------------------------------------------------------------------------- #
# Multimodal content
# --------------------------------------------------------------------------- #
def test_multimodal_content_estimated_from_text_parts():
    multimodal = turn(
        "user",
        [
            {"type": "text", "text": "describe this"},
            {"type": "image_url", "image_url": {"url": "..."}},
        ],
    )
    ctx = assemble_context(recent_turns=[multimodal], token_budget=1000, token_counter=wc)
    assert ctx.usage.turns_included == 1
    # text part "describe this" = 2 words + role "user" = 1 -> 3
    assert ctx.usage.turn_tokens == 3


# --------------------------------------------------------------------------- #
# Usage totals are internally consistent
# --------------------------------------------------------------------------- #
def test_usage_total_equals_sum_of_parts():
    ctx = assemble_context(
        recent_turns=[turn("user", "a b"), turn("assistant", "c d e")],
        system_message="sys here",
        rolling_summary="sum text",
        memory_items=[MemoryItem("mem fact", salience=0.7)],
        token_budget=1000,
        token_counter=wc,
    )
    u = ctx.usage
    assert u.total_tokens == (u.system_tokens + u.memory_tokens + u.summary_tokens + u.turn_tokens)


def test_empty_everything_produces_empty_context():
    ctx = assemble_context(recent_turns=[], token_budget=100, token_counter=wc)
    assert ctx.messages == []
    assert ctx.usage == ContextUsage()
    assert isinstance(ctx, AssembledContext)


# --------------------------------------------------------------------------- #
# should_summarize
# --------------------------------------------------------------------------- #
def test_should_summarize_triggers_above_ratio():
    # 4 turns * 3 tokens = 12; budget 10, ratio 0.7 -> threshold 7; 12 > 7 -> True
    turns = [turn("user", f"t{i} x") for i in range(4)]
    assert should_summarize(turns, 10, token_counter=wc, trigger_ratio=0.7) is True


def test_should_summarize_false_below_ratio():
    turns = [turn("user", "t1 x")]  # 3 tokens; threshold 7 -> False
    assert should_summarize(turns, 10, token_counter=wc, trigger_ratio=0.7) is False


def test_should_summarize_empty_thread_false():
    assert should_summarize([], 10, token_counter=wc) is False
