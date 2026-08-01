"""A1 — the Publisher.

Drafts editorial content aimed at coding-agent cost and setup queries, and
proposes new ``/use/[tool]`` entries as the agent-tool landscape moves.

Everything it produces is a draft written to disk for human review and manual
merge. It does not open pull requests and does not publish. The GTM plan budgets
20 minutes a day of founder review for exactly this, and content going out under
Joaquim's name needs to have been read by Joaquim.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from agents.base import Agent, AgentRun

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You write technical content for Gatewayz, an AI inference gateway
positioned specifically for coding agents (Claude Code, Cline, Aider, OpenCode, Continue).

Audience: working developers spending $100-500/month on coding-agent API calls,
actively looking for cheaper inference. They are technical, sceptical, and
allergic to marketing language.

Rules:
- Lead with the concrete thing they came for: a command, a number, a config file.
- Never claim a capability without qualifying it. If something only works under
  a condition, state the condition in the same sentence.
- Never invent a benchmark figure, price, or latency number. If a number would
  strengthen the piece, write [NEEDS DATA: what to measure] and move on.
- Acknowledge competitors accurately. OpenRouter is a good product with a bigger
  catalog; pretending otherwise destroys credibility with this audience.
- No superlatives, no "revolutionary", no "game-changing".
- Short paragraphs. Code blocks over prose where a code block will do.
"""


@dataclass
class ContentBrief:
    """One piece of content to draft."""

    slug: str
    kind: str  # "editorial" | "use-page" | "comparison"
    title: str
    target_query: str
    angle: str


# The editorial calendar. Each brief targets a query a coding-agent user
# actually types when their bill arrives.
DEFAULT_BRIEFS: list[ContentBrief] = [
    ContentBrief(
        slug="claude-code-api-costs",
        kind="editorial",
        title="What Claude Code actually costs, and where the money goes",
        target_query="claude code api costs",
        angle=(
            "Break down a real coding session by token class: system prompt, file "
            "context, output. Show why cache reads dominate the bill on long sessions "
            "and what that implies for model choice."
        ),
    ),
    ContentBrief(
        slug="prompt-caching-for-coding-agents",
        kind="editorial",
        title="Prompt caching is the only cost lever that matters for coding agents",
        target_query="prompt caching coding agent",
        angle=(
            "Explain cache writes vs cache reads, the minimum cacheable prefix, and "
            "why a coding agent's replayed context is the ideal shape for it. Include "
            "the arithmetic."
        ),
    ),
    ContentBrief(
        slug="cheapest-way-to-run-aider",
        kind="editorial",
        title="The cheapest way to run Aider in 2026",
        target_query="cheapest way to run aider",
        angle=(
            "Compare model choices for Aider by cost per completed edit rather than "
            "cost per token. Be honest that the cheapest model is often the wrong one."
        ),
    ),
    ContentBrief(
        slug="rate-limited-on-claude-pro",
        kind="editorial",
        title="Hitting rate limits on a Claude subscription? Here are your options",
        target_query="claude code rate limit",
        angle=(
            "Lay out the real options: wait, upgrade the subscription, go direct on "
            "API pricing, or use a gateway. Give the honest break-even point for each."
        ),
    ),
]


class Publisher(Agent):
    name = "publisher"
    system_prompt = SYSTEM_PROMPT

    def __init__(self, *args, briefs: list[ContentBrief] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.briefs = briefs if briefs is not None else DEFAULT_BRIEFS

    def draft(self, brief: ContentBrief) -> dict[str, Any] | None:
        prompt = f"""Draft this piece.

Title: {brief.title}
Target search query: {brief.target_query}
Angle: {brief.angle}

Return markdown. Open with the concrete answer — no throat-clearing introduction.
Aim for 600-900 words. Use [NEEDS DATA: ...] wherever a figure is required that
you do not have.
"""
        try:
            body = self.think(prompt, max_tokens=2500)
        except Exception as e:
            self.fail(f"Draft failed for '{brief.slug}': {e}")
            return None

        needs_data = body.count("[NEEDS DATA")
        return {
            "slug": brief.slug,
            "kind": brief.kind,
            "title": brief.title,
            "target_query": brief.target_query,
            "body": body,
            "needs_data_count": needs_data,
            "status": "draft-awaiting-review",
        }

    def run(self) -> AgentRun:
        for brief in self.briefs:
            draft = self.draft(brief)
            if draft:
                self.emit("draft", draft)
                if draft["needs_data_count"]:
                    self.note(
                        f"'{brief.slug}' has {draft['needs_data_count']} [NEEDS DATA] "
                        "placeholder(s) — fill these before publishing."
                    )

        if not self.run_record.outputs:
            self.note("No drafts produced. Check the errors above before assuming a quiet week.")

        return self.run_record


def main() -> int:
    agent = Publisher()
    run = agent.run()
    path = agent.save()
    print(f"Publisher run written to {path}")
    print(f"{len(run.outputs)} draft(s), cost ${run.total_cost_usd:.4f}")
    print("Nothing is published. Review the drafts, then merge by hand.")
    for note in run.notes:
        print(f"NOTE: {note}")
    for error in run.errors:
        print(f"ERROR: {error}")
    return 1 if run.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
