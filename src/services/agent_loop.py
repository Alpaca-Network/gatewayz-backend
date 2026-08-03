"""Server-side agent loop.

Normally the client runs the loop: it receives ``tool_calls``, executes them,
and sends the results back. Every coding agent Gatewayz targets does this, which
is why the gateway shipped without one.

This exists for callers that would rather not: a script, a cron job, a
non-agentic app that wants "answer this, using these tools" in one request. The
caller supplies tool *definitions* plus how to execute them, and gets back a
final answer with the full turn history.

Design constraints, all learned from how these loops fail in production:

* **Bounded.** A hard cap on iterations and on wall-clock. A model that calls
  the same tool forever is not hypothetical, and unbounded server-side loops
  turn one request into an unbillable eternity.
* **Every turn billed.** Each iteration is a real inference call and is charged
  as one. A loop that bills only the last turn is a hole in the ledger.
* **Failures are turns, not exceptions.** A tool that raises gets its error fed
  back to the model as a tool result, because recovering from a failed tool call
  is the main thing an agent loop is for. Only loop-level failures abort.
* **Never silently truncated.** Hitting the iteration cap returns what it has
  plus an explicit ``stop_reason``, so the caller can tell "finished" from
  "ran out of turns" — those need different handling and look identical if you
  only inspect the content.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# A coding-style task rarely needs more than a handful of tool round trips.
# Past this the model is usually looping, not working.
DEFAULT_MAX_ITERATIONS = 10
MAX_ITERATIONS_CEILING = 25

# Wall-clock ceiling for the whole loop.
DEFAULT_TIMEOUT_SECONDS = 300.0

# A tool result large enough to blow the context window is worse than a
# truncated one, because it fails the *next* turn rather than this one.
MAX_TOOL_RESULT_CHARS = 20000

ToolExecutor = Callable[[str, dict], Awaitable[Any]]


class AgentLoopError(RuntimeError):
    """Loop-level failure. Tool failures do not raise; they become turns."""


@dataclass
class LoopTurn:
    """One iteration: the model's output and any tool results it produced."""

    index: int
    content: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class LoopResult:
    """Outcome of a full loop."""

    content: str
    turns: list[LoopTurn]
    stop_reason: str  # "completed" | "max_iterations" | "timeout"
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_usd: float = 0.0

    @property
    def completed(self) -> bool:
        return self.stop_reason == "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "stop_reason": self.stop_reason,
            "iterations": len(self.turns),
            "usage": {
                "prompt_tokens": self.total_prompt_tokens,
                "completion_tokens": self.total_completion_tokens,
                "total_tokens": self.total_prompt_tokens + self.total_completion_tokens,
            },
            "cost_usd": round(self.total_cost_usd, 6),
            "turns": [
                {
                    "index": t.index,
                    "content": t.content,
                    "tool_calls": t.tool_calls,
                    "tool_results": t.tool_results,
                }
                for t in self.turns
            ],
        }


def _truncate_result(value: Any) -> str:
    """Render a tool result as a string the model can read, bounded in size."""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value)
        except (TypeError, ValueError):
            text = str(value)

    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text

    # Say that it was cut. A silently truncated result makes the model reason
    # confidently about data it never saw.
    kept = MAX_TOOL_RESULT_CHARS
    return (
        text[:kept]
        + f"\n\n[truncated: {len(text) - kept} more characters not shown]"
    )


async def _execute_one(
    executor: ToolExecutor,
    tool_call: dict[str, Any],
) -> dict[str, Any]:
    """Run a single tool call, turning any failure into a tool result.

    A raised exception here would abort the loop; feeding the error back gives
    the model a chance to correct itself, which is the point of the loop.
    """
    call_id = tool_call.get("id", "")
    fn = tool_call.get("function") or {}
    name = fn.get("name", "")
    raw_args = fn.get("arguments") or "{}"

    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    except json.JSONDecodeError as e:
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": f"Error: could not parse arguments as JSON ({e}). "
            f"Received: {str(raw_args)[:200]}",
        }

    try:
        result = await executor(name, args)
        content = _truncate_result(result)
    except Exception as e:
        logger.info("Tool '%s' failed inside agent loop: %s", name, e)
        content = f"Error: {type(e).__name__}: {e}"

    return {"role": "tool", "tool_call_id": call_id, "content": content}


async def run_agent_loop(
    *,
    messages: list[dict[str, Any]],
    model: str,
    tools: list[dict[str, Any]],
    executor: ToolExecutor,
    inference: Callable[..., Awaitable[dict[str, Any]]],
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    **inference_kwargs: Any,
) -> LoopResult:
    """Drive a tool-calling conversation to completion.

    Args:
        messages: Opening conversation, OpenAI shape.
        model: Model identifier.
        tools: Tool definitions, OpenAI shape.
        executor: ``async (name, args) -> result``. Runs one tool.
        inference: ``async (messages, model, tools, **kw) -> openai_response``.
            Injected so the loop is testable and so it reuses whatever
            inference path the caller already has, rather than opening a second
            one that could bill differently.
        max_iterations: Hard cap on model turns.
        timeout_seconds: Wall-clock cap for the whole loop.

    Returns:
        LoopResult. Check ``stop_reason`` — "completed" means the model
        finished, anything else means it was cut short.
    """
    if max_iterations < 1:
        raise AgentLoopError("max_iterations must be at least 1")
    if max_iterations > MAX_ITERATIONS_CEILING:
        raise AgentLoopError(
            f"max_iterations {max_iterations} exceeds the ceiling of "
            f"{MAX_ITERATIONS_CEILING}. An unbounded server-side loop turns one "
            "request into an open-ended bill."
        )

    conversation = list(messages)
    turns: list[LoopTurn] = []
    started = time.monotonic()
    stop_reason = "max_iterations"
    final_content = ""

    for index in range(max_iterations):
        if time.monotonic() - started > timeout_seconds:
            stop_reason = "timeout"
            break

        response = await inference(
            messages=conversation,
            model=model,
            tools=tools,
            **inference_kwargs,
        )

        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        tool_calls = message.get("tool_calls") or []

        usage = response.get("usage") or {}
        gateway_usage = response.get("gateway_usage") or {}
        turn = LoopTurn(
            index=index,
            content=content,
            tool_calls=tool_calls,
            prompt_tokens=usage.get("prompt_tokens", 0) or 0,
            completion_tokens=usage.get("completion_tokens", 0) or 0,
            cost_usd=float(gateway_usage.get("cost_usd", 0.0) or 0.0),
        )

        # Record the assistant turn before running tools, so an executor crash
        # still leaves an accurate history.
        assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_message["tool_calls"] = tool_calls
        conversation.append(assistant_message)

        if not tool_calls:
            final_content = content or ""
            turns.append(turn)
            stop_reason = "completed"
            break

        # Tool calls in a single turn are independent by construction, so run
        # them concurrently rather than serially.
        results = await asyncio.gather(
            *(_execute_one(executor, call) for call in tool_calls)
        )
        turn.tool_results = list(results)
        conversation.extend(results)
        turns.append(turn)

        # Carry the last non-empty content forward, so a loop that runs out of
        # turns still returns the model's most recent prose rather than "".
        if content:
            final_content = content

    if stop_reason == "max_iterations":
        logger.warning(
            "Agent loop hit the %d-iteration cap for model %s without completing",
            max_iterations,
            model,
        )
    elif stop_reason == "timeout":
        logger.warning(
            "Agent loop timed out after %.0fs for model %s", timeout_seconds, model
        )

    return LoopResult(
        content=final_content,
        turns=turns,
        stop_reason=stop_reason,
        total_prompt_tokens=sum(t.prompt_tokens for t in turns),
        total_completion_tokens=sum(t.completion_tokens for t in turns),
        total_cost_usd=sum(t.cost_usd for t in turns),
    )
