"""The model list Claude Code's ``/model`` picker reads from a gateway.

Claude Code can list a gateway's models (``GET /v1/models?limit=1000``, sent
with an ``anthropic-version`` header), but its picker only shows entries whose
id contains ``claude``. Every other model the gateway serves stays invisible
there, even though the session can run on it when the id is typed by hand.

So the listing given to that client names each non-Claude model under
``claude-code/<catalog id>``, with the model's real name as its display name.
``/v1/messages`` strips the prefix again before anything else looks at the id.
A model's catalog id keeps working unchanged everywhere.

Only models an agent can use are listed: tool calling is required, and models
known to have a small context window are left out.
"""

from __future__ import annotations

from typing import Any

CLAUDE_CODE_PREFIX = "claude-code/"

# An agent request carries roughly 20k tokens of instructions and tool
# definitions before any work. Below this a session cannot get anywhere.
MIN_CONTEXT_TOKENS = 64_000

_EPOCH = "2026-01-01T00:00:00Z"


def strip_claude_code_prefix(model: str | None) -> str | None:
    """``claude-code/openai/gpt-5.5`` -> ``openai/gpt-5.5``; other ids unchanged."""
    if isinstance(model, str) and model.startswith(CLAUDE_CODE_PREFIX):
        return model[len(CLAUDE_CODE_PREFIX) :]
    return model


def picker_id(model_id: str) -> str:
    """The id to list a model under so that Claude Code's picker shows it."""
    if "claude" in model_id.lower():
        return model_id
    return f"{CLAUDE_CODE_PREFIX}{model_id}"


def _supports_tools(model: dict[str, Any]) -> bool:
    capabilities = model.get("capabilities")
    if isinstance(capabilities, dict) and capabilities.get("tools"):
        return True
    return "tools" in (model.get("supported_parameters") or [])


def is_agent_capable(model: dict[str, Any]) -> bool:
    if not model.get("id") or model.get("is_active") is False:
        return False
    if not _supports_tools(model):
        return False
    context = model.get("context_length") or 0
    # 0 means the catalog does not know the window, not that it is small.
    return context == 0 or context >= MIN_CONTEXT_TOKENS


def claude_code_model_listing(models: list[dict[str, Any]]) -> dict[str, Any]:
    """Catalog models as an Anthropic-shaped model list for Claude Code."""
    data: list[dict[str, Any]] = []
    seen: set[str] = set()
    for model in models:
        if not isinstance(model, dict) or not is_agent_capable(model):
            continue
        listed_id = picker_id(model["id"])
        if listed_id in seen:
            continue
        seen.add(listed_id)
        entry: dict[str, Any] = {
            "type": "model",
            "id": listed_id,
            "display_name": model.get("name") or model["id"],
            "created_at": _EPOCH,
        }
        if model.get("context_length"):
            entry["max_input_tokens"] = model["context_length"]
        data.append(entry)
    # Claude models first, then the rest, each group by id.
    data.sort(key=lambda m: (m["id"].startswith(CLAUDE_CODE_PREFIX), m["id"].lower()))
    return {
        "data": data,
        "has_more": False,
        "first_id": data[0]["id"] if data else None,
        "last_id": data[-1]["id"] if data else None,
    }
