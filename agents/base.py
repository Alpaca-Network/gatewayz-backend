"""Shared runner for the GTM agent stack.

Every agent here sends its inference through Gatewayz itself. That is partly
dogfooding and partly the product-marketing asset described in the GTM plan:
"we run our entire go-to-market on N agents through our own gateway, here is
the bill" is only publishable if the bill is real and recorded, which is what
``UsageLedger`` is for.

Two design rules shape everything in this package:

1. **Draft, never publish.** Agents produce drafts for a human to post. Auto-
   posting to Reddit or Hacker News gets accounts banned and torches the
   founder-led credibility the whole motion depends on. The agent saves the
   writing time, not the showing-up time.
2. **Fail visibly.** An agent that silently produces nothing looks identical to
   one that had nothing to say. Every run records what it attempted, what it
   produced, and what it could not do.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GATEWAY_URL = os.getenv("GATEWAYZ_API_URL", "https://api.gatewayz.ai")
GATEWAY_KEY_ENV = "GATEWAYZ_AGENT_API_KEY"

# Where drafts and run records land. A human reviews everything here.
OUTPUT_ROOT = Path(os.getenv("AGENT_OUTPUT_DIR", "agent-output"))

DEFAULT_MODEL = os.getenv("AGENT_MODEL", "anthropic/claude-sonnet-4")


class AgentError(RuntimeError):
    """Raised when an agent cannot complete its run."""


@dataclass
class UsageRecord:
    """One inference call's cost, for the monthly dogfood report."""

    agent: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int
    cost_usd: float
    timestamp: int


@dataclass
class AgentRun:
    """The record of a single agent execution."""

    agent: str
    started_at: int
    outputs: list[dict[str, Any]] = field(default_factory=list)
    usage: list[UsageRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return sum(u.cost_usd for u in self.usage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "started_at": self.started_at,
            "outputs": self.outputs,
            "usage": [asdict(u) for u in self.usage],
            "total_cost_usd": round(self.total_cost_usd, 6),
            "errors": self.errors,
            "notes": self.notes,
        }


class UsageLedger:
    """Append-only record of what the agent stack costs to run.

    Feeds the monthly "here is our bill" post. Kept as a flat JSONL file rather
    than a database so the numbers are trivially auditable by whoever publishes
    them.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or (OUTPUT_ROOT / "usage-ledger.jsonl")

    def record(self, usage: UsageRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(usage)) + "\n")

    def summarize(self, since: int | None = None) -> dict[str, Any]:
        """Aggregate the ledger for the dogfood report."""
        if not self.path.exists():
            return {"records": 0, "total_cost_usd": 0.0, "by_agent": {}, "note": "no runs recorded"}

        by_agent: dict[str, dict[str, float]] = {}
        total_cost = 0.0
        total_tokens = 0
        cache_reads = 0
        count = 0

        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since and row.get("timestamp", 0) < since:
                    continue
                count += 1
                agent = row.get("agent", "unknown")
                bucket = by_agent.setdefault(agent, {"cost_usd": 0.0, "tokens": 0, "calls": 0})
                bucket["cost_usd"] += row.get("cost_usd", 0.0)
                tokens = row.get("prompt_tokens", 0) + row.get("completion_tokens", 0)
                bucket["tokens"] += tokens
                bucket["calls"] += 1
                total_cost += row.get("cost_usd", 0.0)
                total_tokens += tokens
                cache_reads += row.get("cache_read_tokens", 0)

        return {
            "records": count,
            "total_cost_usd": round(total_cost, 4),
            "total_tokens": total_tokens,
            "cache_read_tokens": cache_reads,
            "by_agent": {
                name: {**vals, "cost_usd": round(vals["cost_usd"], 4)}
                for name, vals in by_agent.items()
            },
        }


class GatewayzClient:
    """Minimal chat client pointed at Gatewayz.

    Deliberately not the OpenAI SDK: these agents exist partly to prove the
    gateway works for third-party clients, so they exercise the plain HTTP
    surface a stranger would use.
    """

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.api_key = api_key or os.getenv(GATEWAY_KEY_ENV)
        if not self.api_key:
            raise AgentError(
                f"{GATEWAY_KEY_ENV} is not set. The agent stack runs on Gatewayz; "
                "issue a key and export it before running."
            )
        self.model = model
        self._client = httpx.Client(timeout=180.0)

    def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 2000,
        cache_system: bool = True,
        response_json: bool = False,
    ) -> tuple[str, UsageRecord]:
        """One completion. Returns ``(text, usage)``.

        The system prompt is cache-marked by default: these agents run on a
        schedule with a stable system prompt, which is exactly the workload
        prompt caching is for. It also means the dogfood bill demonstrates the
        caching the product is sold on.
        """
        system_block: dict[str, Any] = {"type": "text", "text": system}
        if cache_system:
            system_block["cache_control"] = {"type": "ephemeral"}

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": [system_block]},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
        }
        if response_json:
            payload["response_format"] = {"type": "json_object"}

        response = self._client.post(
            f"{GATEWAY_URL.rstrip('/')}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        usage = data.get("usage", {}) or {}
        gateway_usage = data.get("gateway_usage", {}) or {}

        record = UsageRecord(
            agent="",  # filled in by the caller
            model=data.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0)
            or (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            or 0,
            cost_usd=float(gateway_usage.get("cost_usd", 0.0) or 0.0),
            timestamp=int(time.time()),
        )
        return content, record

    def close(self) -> None:
        self._client.close()


class Agent:
    """Base class. Subclasses implement ``run``."""

    name: str = "agent"
    system_prompt: str = ""

    def __init__(self, client: GatewayzClient | None = None, ledger: UsageLedger | None = None):
        self.client = client or GatewayzClient()
        self.ledger = ledger or UsageLedger()
        self.run_record = AgentRun(agent=self.name, started_at=int(time.time()))

    def think(self, user_prompt: str, *, max_tokens: int = 2000, response_json: bool = False) -> str:
        """One LLM call, with usage recorded against this agent."""
        text, usage = self.client.complete(
            self.system_prompt,
            user_prompt,
            max_tokens=max_tokens,
            response_json=response_json,
        )
        usage.agent = self.name
        self.ledger.record(usage)
        self.run_record.usage.append(usage)
        return text

    def emit(self, kind: str, payload: dict[str, Any]) -> None:
        """Record a draft for human review. Nothing here is published."""
        self.run_record.outputs.append({"kind": kind, **payload})

    def note(self, message: str) -> None:
        self.run_record.notes.append(message)

    def fail(self, message: str) -> None:
        """Record a failure without aborting the whole run."""
        logger.warning("[%s] %s", self.name, message)
        self.run_record.errors.append(message)

    def save(self) -> Path:
        """Write the run record where a human will find it."""
        out_dir = OUTPUT_ROOT / self.name
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.run_record.started_at}.json"
        path.write_text(json.dumps(self.run_record.to_dict(), indent=2), encoding="utf-8")
        return path

    def run(self) -> AgentRun:  # pragma: no cover - subclass responsibility
        raise NotImplementedError
