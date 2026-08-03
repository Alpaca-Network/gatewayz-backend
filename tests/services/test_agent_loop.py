"""Tests for the server-side agent loop.

The failure modes that matter are all about boundedness and honesty: the loop
must not run forever, must bill every turn, must not turn a tool failure into a
dead request, and must never let "ran out of turns" look like "finished".
"""

import json

import pytest

from src.services.agent_loop import (
    MAX_ITERATIONS_CEILING,
    MAX_TOOL_RESULT_CHARS,
    AgentLoopError,
    run_agent_loop,
)

TOOLS = [
    {
        "type": "function",
        "function": {"name": "read_file", "parameters": {"type": "object"}},
    }
]


def _tool_call(call_id="call_1", name="read_file", args='{"path": "a.py"}'):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def _response(content=None, tool_calls=None, prompt=10, completion=5, cost=0.001):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
        "gateway_usage": {"cost_usd": cost},
    }


def _scripted_inference(responses):
    """Return an inference callable that replays `responses` in order."""
    state = {"i": 0}

    async def _inference(**kwargs):
        i = state["i"]
        state["i"] += 1
        return responses[min(i, len(responses) - 1)]

    return _inference


async def _noop_executor(name, args):
    return {"ok": True, "name": name}


class TestCompletion:
    @pytest.mark.asyncio
    async def test_returns_immediately_when_no_tool_calls(self):
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "hi"}],
            model="m",
            tools=TOOLS,
            executor=_noop_executor,
            inference=_scripted_inference([_response(content="done")]),
        )
        assert result.content == "done"
        assert result.stop_reason == "completed"
        assert len(result.turns) == 1

    @pytest.mark.asyncio
    async def test_executes_a_tool_then_completes(self):
        responses = [
            _response(tool_calls=[_tool_call()]),
            _response(content="final answer"),
        ]
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "read it"}],
            model="m",
            tools=TOOLS,
            executor=_noop_executor,
            inference=_scripted_inference(responses),
        )
        assert result.content == "final answer"
        assert result.stop_reason == "completed"
        assert len(result.turns) == 2
        assert result.turns[0].tool_results[0]["role"] == "tool"

    @pytest.mark.asyncio
    async def test_parallel_tool_calls_all_execute(self):
        calls = [_tool_call("c1"), _tool_call("c2"), _tool_call("c3")]
        responses = [_response(tool_calls=calls), _response(content="done")]
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_noop_executor,
            inference=_scripted_inference(responses),
        )
        assert len(result.turns[0].tool_results) == 3

    @pytest.mark.asyncio
    async def test_tool_result_is_linked_to_its_call_id(self):
        responses = [_response(tool_calls=[_tool_call("abc")]), _response(content="d")]
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_noop_executor,
            inference=_scripted_inference(responses),
        )
        assert result.turns[0].tool_results[0]["tool_call_id"] == "abc"


class TestBoundedness:
    @pytest.mark.asyncio
    async def test_stops_at_max_iterations_without_pretending_to_finish(self):
        """A capped loop must be distinguishable from a completed one."""
        looping = _response(tool_calls=[_tool_call()])
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_noop_executor,
            inference=_scripted_inference([looping]),
            max_iterations=3,
        )
        assert result.stop_reason == "max_iterations"
        assert result.completed is False
        assert len(result.turns) == 3

    @pytest.mark.asyncio
    async def test_rejects_max_iterations_above_the_ceiling(self):
        with pytest.raises(AgentLoopError, match="ceiling"):
            await run_agent_loop(
                messages=[],
                model="m",
                tools=TOOLS,
                executor=_noop_executor,
                inference=_scripted_inference([_response(content="x")]),
                max_iterations=MAX_ITERATIONS_CEILING + 1,
            )

    @pytest.mark.asyncio
    async def test_rejects_zero_iterations(self):
        with pytest.raises(AgentLoopError):
            await run_agent_loop(
                messages=[],
                model="m",
                tools=TOOLS,
                executor=_noop_executor,
                inference=_scripted_inference([_response(content="x")]),
                max_iterations=0,
            )

    @pytest.mark.asyncio
    async def test_timeout_stops_the_loop(self):
        looping = _response(tool_calls=[_tool_call()])
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_noop_executor,
            inference=_scripted_inference([looping]),
            max_iterations=MAX_ITERATIONS_CEILING,
            timeout_seconds=0.0,
        )
        assert result.stop_reason == "timeout"

    @pytest.mark.asyncio
    async def test_capped_loop_still_returns_the_last_prose(self):
        """Running out of turns should not throw away what the model did say."""
        looping = _response(content="partial thinking", tool_calls=[_tool_call()])
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_noop_executor,
            inference=_scripted_inference([looping]),
            max_iterations=2,
        )
        assert result.content == "partial thinking"
        assert result.stop_reason == "max_iterations"


class TestToolFailures:
    @pytest.mark.asyncio
    async def test_tool_exception_becomes_a_tool_result_not_a_crash(self):
        """Recovering from a failed tool call is the point of the loop."""

        async def _boom(name, args):
            raise RuntimeError("disk on fire")

        responses = [_response(tool_calls=[_tool_call()]), _response(content="recovered")]
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_boom,
            inference=_scripted_inference(responses),
        )
        assert result.stop_reason == "completed"
        assert "disk on fire" in result.turns[0].tool_results[0]["content"]

    @pytest.mark.asyncio
    async def test_malformed_arguments_are_reported_back_to_the_model(self):
        responses = [
            _response(tool_calls=[_tool_call(args="not json{")]),
            _response(content="ok"),
        ]
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_noop_executor,
            inference=_scripted_inference(responses),
        )
        content = result.turns[0].tool_results[0]["content"]
        assert "could not parse arguments" in content

    @pytest.mark.asyncio
    async def test_one_failing_tool_does_not_block_its_siblings(self):
        async def _selective(name, args):
            if args.get("path") == "bad.py":
                raise ValueError("nope")
            return "fine"

        calls = [
            _tool_call("c1", args='{"path": "bad.py"}'),
            _tool_call("c2", args='{"path": "good.py"}'),
        ]
        responses = [_response(tool_calls=calls), _response(content="done")]
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_selective,
            inference=_scripted_inference(responses),
        )
        contents = [r["content"] for r in result.turns[0].tool_results]
        assert any("nope" in c for c in contents)
        assert any("fine" in c for c in contents)


class TestResultTruncation:
    @pytest.mark.asyncio
    async def test_oversized_tool_result_is_truncated_and_says_so(self):
        """Silent truncation makes the model reason about data it never saw."""

        async def _huge(name, args):
            return "x" * (MAX_TOOL_RESULT_CHARS + 5000)

        responses = [_response(tool_calls=[_tool_call()]), _response(content="done")]
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_huge,
            inference=_scripted_inference(responses),
        )
        content = result.turns[0].tool_results[0]["content"]
        assert "[truncated:" in content
        assert len(content) < MAX_TOOL_RESULT_CHARS + 200

    @pytest.mark.asyncio
    async def test_small_result_is_untouched(self):
        async def _small(name, args):
            return "tiny"

        responses = [_response(tool_calls=[_tool_call()]), _response(content="d")]
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_small,
            inference=_scripted_inference(responses),
        )
        assert result.turns[0].tool_results[0]["content"] == "tiny"

    @pytest.mark.asyncio
    async def test_dict_result_is_json_encoded(self):
        async def _dict(name, args):
            return {"a": 1}

        responses = [_response(tool_calls=[_tool_call()]), _response(content="d")]
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_dict,
            inference=_scripted_inference(responses),
        )
        assert json.loads(result.turns[0].tool_results[0]["content"]) == {"a": 1}


class TestBilling:
    @pytest.mark.asyncio
    async def test_every_turn_is_billed_not_just_the_last(self):
        """A loop that bills only the final turn is a hole in the ledger."""
        responses = [
            _response(tool_calls=[_tool_call()], prompt=100, completion=10, cost=0.01),
            _response(content="done", prompt=200, completion=20, cost=0.02),
        ]
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_noop_executor,
            inference=_scripted_inference(responses),
        )
        assert result.total_prompt_tokens == 300
        assert result.total_completion_tokens == 30
        assert result.total_cost_usd == pytest.approx(0.03)

    @pytest.mark.asyncio
    async def test_capped_loop_still_bills_the_turns_it_used(self):
        looping = _response(tool_calls=[_tool_call()], prompt=50, completion=5, cost=0.005)
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_noop_executor,
            inference=_scripted_inference([looping]),
            max_iterations=4,
        )
        assert result.total_cost_usd == pytest.approx(0.02)

    @pytest.mark.asyncio
    async def test_serializes_with_stop_reason_visible(self):
        result = await run_agent_loop(
            messages=[{"role": "user", "content": "x"}],
            model="m",
            tools=TOOLS,
            executor=_noop_executor,
            inference=_scripted_inference([_response(content="d")]),
        )
        payload = result.to_dict()
        assert payload["stop_reason"] == "completed"
        assert payload["iterations"] == 1
        assert "usage" in payload
