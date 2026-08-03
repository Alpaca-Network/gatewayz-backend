"""A3 — the Prospector.

Scores buying signals (people publicly complaining about coding-agent costs or
rate limits) and drafts a reply for each. The founder sends the replies from
their own account.

The scoring step is what makes this useful. A raw keyword feed from Reddit and X
is mostly noise; the job is to surface the ten threads a founder should
personally answer today, ranked, with a draft that is genuinely helpful whether
or not the person ever becomes a customer.

Signal sources are pluggable. This module ships the scoring and drafting; the
fetchers are thin adapters supplied by the caller so the agent can be tested and
run without live API credentials.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from typing import Any, Callable

from agents.base import Agent, AgentRun

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You triage public posts for the founder of Gatewayz, an AI
inference gateway for coding agents.

For each post, decide whether the founder should personally reply, and draft the
reply if so.

Reply only when there is something genuinely useful to say. A reply that exists
to mention Gatewayz is worse than no reply: it gets downvoted, it gets the
account flagged, and it costs the founder the credibility that makes this
channel work at all.

Draft rules:
- Answer the person's actual question first, completely, as if Gatewayz did not
  exist.
- Mention Gatewayz only if it is directly relevant, only once, and only after
  the useful part. Disclose the affiliation in the same breath.
- If the best answer is "use a competitor" or "stay on your subscription", say
  that. Being right in public is the whole strategy.
- Match the register of the platform. No corporate voice.

Return strict JSON:
{"should_reply": bool, "score": 0-100, "reason": "...", "draft": "..."}
score reflects how much the person would benefit from a reply, not how likely
they are to convert.
"""


@dataclass
class Signal:
    """One candidate post."""

    source: str  # "reddit" | "x" | "hn" | "discord"
    url: str
    author: str
    text: str
    matched_terms: list[str]


# Terms that correlate with someone actively shopping for cheaper inference.
DEFAULT_KEYWORDS = (
    "claude code expensive",
    "claude code cost",
    "rate limited claude",
    "cheapest way to run aider",
    "openrouter latency",
    "cline api cost",
    "coding agent api bill",
    "anthropic api expensive",
)

SignalFetcher = Callable[[tuple[str, ...]], list[Signal]]


class Prospector(Agent):
    name = "prospector"
    system_prompt = SYSTEM_PROMPT

    def __init__(
        self,
        *args,
        fetchers: list[SignalFetcher] | None = None,
        keywords: tuple[str, ...] = DEFAULT_KEYWORDS,
        max_replies: int = 20,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.fetchers = fetchers or []
        self.keywords = keywords
        self.max_replies = max_replies

    def gather(self) -> list[Signal]:
        signals: list[Signal] = []
        if not self.fetchers:
            self.fail(
                "No signal fetchers configured. Supply Reddit/X/HN adapters; this agent "
                "scores and drafts, it does not scrape."
            )
            return signals

        for fetcher in self.fetchers:
            try:
                signals.extend(fetcher(self.keywords))
            except Exception as e:
                self.fail(f"Fetcher {getattr(fetcher, '__name__', fetcher)} failed: {e}")

        # Deduplicate by URL — the same thread often matches several keywords.
        seen: set[str] = set()
        unique: list[Signal] = []
        for signal in signals:
            if signal.url in seen:
                continue
            seen.add(signal.url)
            unique.append(signal)
        return unique

    def triage(self, signal: Signal) -> dict[str, Any] | None:
        prompt = f"""Post from {signal.source} by {signal.author}:
{signal.url}

---
{signal.text}
---

Matched terms: {', '.join(signal.matched_terms)}

Should the founder reply? Return the JSON object.
"""
        try:
            raw = self.think(prompt, max_tokens=900, response_json=True)
            verdict = json.loads(raw)
        except json.JSONDecodeError:
            self.fail(f"Triage returned unparseable JSON for {signal.url}")
            return None
        except Exception as e:
            self.fail(f"Triage failed for {signal.url}: {e}")
            return None

        return {
            "signal": asdict(signal),
            "should_reply": bool(verdict.get("should_reply")),
            "score": int(verdict.get("score", 0) or 0),
            "reason": verdict.get("reason", ""),
            "draft": verdict.get("draft", ""),
        }

    def run(self) -> AgentRun:
        signals = self.gather()
        if not signals:
            self.note("No signals gathered this run.")
            return self.run_record

        triaged = [t for t in (self.triage(s) for s in signals) if t]
        worth_replying = sorted(
            (t for t in triaged if t["should_reply"]),
            key=lambda t: t["score"],
            reverse=True,
        )

        # Surface what was dropped. A silently truncated list reads as "that was
        # everything", which is how good threads get missed.
        if len(worth_replying) > self.max_replies:
            self.note(
                f"{len(worth_replying)} threads worth replying to; showing the top "
                f"{self.max_replies}. {len(worth_replying) - self.max_replies} not shown."
            )

        for item in worth_replying[: self.max_replies]:
            self.emit("reply_draft", item)

        skipped = len(triaged) - len(worth_replying)
        if skipped:
            self.note(f"{skipped} post(s) judged not worth a reply — deliberate, not an error.")

        self.note(
            "Send these from the founder's personal account. Brand-account replies "
            "convert an order of magnitude worse in these communities."
        )
        return self.run_record


def main() -> int:
    agent = Prospector()
    run = agent.run()
    path = agent.save()
    print(f"Prospector run written to {path}")
    print(f"{len(run.outputs)} reply draft(s), cost ${run.total_cost_usd:.4f}")
    for note in run.notes:
        print(f"NOTE: {note}")
    for error in run.errors:
        print(f"ERROR: {error}")
    return 1 if run.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
