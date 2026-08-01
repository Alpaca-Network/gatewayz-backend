"""A4 — the Scorekeeper.

Monday-morning agent. Pulls the payer metrics, renders the weekly scorecard
from the GTM operating cadence, and writes a short read of what changed and
what the single lever for the week should be.

Ordering matters here: the numbers come from ``payer_metrics`` (deterministic
SQL-backed arithmetic), and the model is only asked to *interpret* them. An LLM
is never asked to compute or restate a figure, because a hallucinated metric in
an investor update is unrecoverable in a way that a weak interpretation is not.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents.base import Agent, AgentRun

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a revenue analyst for Gatewayz, an AI inference gateway.

You will be given a weekly scorecard containing exact, already-computed figures.

Your job:
1. Say what changed versus last week, in two sentences.
2. Name the single highest-leverage action for the coming week, and why.
3. Flag anything that looks like a data problem rather than a business problem.

Hard rules:
- NEVER restate, recompute, round, or invent a number. Refer to metrics by name.
- If a metric is null, say it is unavailable. Do not guess what it might be.
- If the data is too thin to support a conclusion, say so plainly. "Not enough
  data yet" is a valid and useful answer.
- Be terse. This is read on a Monday morning before coffee.
"""


class Scorekeeper(Agent):
    name = "scorekeeper"
    system_prompt = SYSTEM_PROMPT

    def __init__(self, *args, scorecard: dict[str, Any] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._injected_scorecard = scorecard

    def _load_scorecard(self) -> dict[str, Any] | None:
        if self._injected_scorecard is not None:
            return self._injected_scorecard
        try:
            from src.services.payer_metrics import build_weekly_scorecard

            return build_weekly_scorecard().to_dict()
        except Exception as e:
            self.fail(f"Could not build the weekly scorecard: {e}")
            return None

    def run(self) -> AgentRun:
        scorecard = self._load_scorecard()
        if scorecard is None:
            self.note("No scorecard produced. Nothing was sent for interpretation.")
            return self.run_record

        self.emit("scorecard", {"data": scorecard})

        # A scorecard with no payers at all is a data/plumbing question, not an
        # analysis question — asking the model to interpret zeros produces
        # confident nonsense.
        if scorecard.get("total_paying_accounts", 0) == 0:
            self.note(
                "Zero paying accounts on record. Skipping interpretation — verify the "
                "payments table and Stripe webhook before treating this as a business signal."
            )
            return self.run_record

        try:
            commentary = self.think(
                "Here is this week's scorecard. Interpret it under the rules.\n\n"
                + json.dumps(scorecard, indent=2),
                max_tokens=600,
            )
            self.emit("commentary", {"text": commentary})
        except Exception as e:
            self.fail(f"Interpretation call failed: {e}")

        return self.run_record


def main() -> int:
    agent = Scorekeeper()
    run = agent.run()
    path = agent.save()
    print(f"Scorekeeper run written to {path}")
    print(f"Cost: ${run.total_cost_usd:.4f}")
    for error in run.errors:
        print(f"ERROR: {error}")
    for note in run.notes:
        print(f"NOTE: {note}")
    return 1 if run.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
