"""A6 — the Producer.

Generates ad variants (copy plus static-creative specs) for a paid channel.

**Conditional by design.** The GTM plan gates this agent on a channel having
earned budget in month-2 testing: it only exists "if organic + the webinar prove
a CAC worth scaling". So the agent refuses to produce creative for a channel
that has not cleared its CAC target, and says why.

That refusal is the useful part. Generating ads for an unproven channel is how a
$300 experiment line becomes a $3,000 one without anybody deciding to spend it —
the creative exists, so it gets run. Making the agent decline until the numbers
justify it keeps the decision explicit and with the founder.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from typing import Any

from agents.base import Agent, AgentRun

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You write paid-acquisition creative for Gatewayz, an AI
inference gateway for coding agents.

Audience: developers spending $100-500/month on coding-agent API calls. They are
technical, ad-averse, and will punish anything that smells like marketing.

Rules:
- Lead with the specific, checkable claim. A number or a config line beats an
  adjective every time.
- Never state a benchmark figure, price or saving that was not given to you. If
  a claim needs a number you do not have, write [NEEDS DATA: what to measure].
- No superlatives. No "revolutionary", "game-changing", "10x".
- Do not disparage competitors by name. OpenRouter is a good product; attacking
  it reads as insecurity to this audience.
- Respect the platform's character limits exactly. Copy that gets truncated
  mid-claim is worse than shorter copy.

For each variant, state the single hypothesis it tests. Variants that test the
same thing in different words are not variants — they are noise, and they make
the test unreadable.
"""

# Max CAC, in USD per paying account, at which a channel is worth scaling.
# From the GTM budget section: "Scale only on proven CAC < $30/payer".
CAC_TARGET_USD = 30.0

# Minimum conversions before a CAC number means anything. Below this the
# estimate is dominated by noise and scaling on it is gambling.
MIN_CONVERSIONS_FOR_SIGNAL = 10


@dataclass
class ChannelResult:
    """Measured outcome of a paid-channel experiment."""

    channel: str
    spend_usd: float
    paying_accounts: int

    @property
    def cac_usd(self) -> float | None:
        if self.paying_accounts <= 0:
            return None
        return round(self.spend_usd / self.paying_accounts, 2)

    @property
    def has_signal(self) -> bool:
        return self.paying_accounts >= MIN_CONVERSIONS_FOR_SIGNAL

    @property
    def earned_budget(self) -> bool:
        cac = self.cac_usd
        return bool(self.has_signal and cac is not None and cac < CAC_TARGET_USD)


PLATFORM_SPECS: dict[str, dict[str, Any]] = {
    "reddit": {
        "headline_chars": 300,
        "body_chars": 40000,
        "creative": "1200x628 or 1080x1080",
        "note": "Promoted posts sit in-feed next to organic. Match the register.",
    },
    "google_search": {
        "headline_chars": 30,
        "body_chars": 90,
        "creative": "text only",
        "note": "3 headlines x 2 descriptions minimum for RSA asset coverage.",
    },
    "x": {
        "headline_chars": 100,
        "body_chars": 280,
        "creative": "1600x900",
        "note": "First line must work with the image suppressed.",
    },
}


class Producer(Agent):
    name = "producer"
    system_prompt = SYSTEM_PROMPT

    def __init__(
        self,
        *args,
        results: list[ChannelResult] | None = None,
        variants: int = 3,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.results = results or []
        self.variants = variants

    def eligible_channels(self) -> list[ChannelResult]:
        """Channels whose measured CAC justifies scaling.

        Everything excluded is reported, with the reason — a silently empty
        run looks identical to "nothing qualified", and those need different
        responses from the founder.
        """
        eligible: list[ChannelResult] = []
        for result in self.results:
            if result.earned_budget:
                eligible.append(result)
            elif not result.has_signal:
                self.note(
                    f"{result.channel}: only {result.paying_accounts} paying account(s) "
                    f"from ${result.spend_usd:.0f} — below the {MIN_CONVERSIONS_FOR_SIGNAL} "
                    "needed for a CAC estimate to mean anything. Keep testing or stop; "
                    "do not scale."
                )
            else:
                self.note(
                    f"{result.channel}: CAC ${result.cac_usd:.2f} exceeds the "
                    f"${CAC_TARGET_USD:.0f} target. No creative generated — per the "
                    "plan, kill anything above target without sentiment."
                )
        return eligible

    def generate_for(self, result: ChannelResult) -> dict[str, Any] | None:
        spec = PLATFORM_SPECS.get(result.channel)
        if spec is None:
            self.fail(
                f"No platform spec for '{result.channel}'. Add one before generating "
                "creative — copy written without the real character limits gets "
                "truncated mid-claim."
            )
            return None

        prompt = f"""Channel: {result.channel}
Measured CAC: ${result.cac_usd:.2f} per paying account over ${result.spend_usd:.0f} spend
Platform limits: headline <= {spec['headline_chars']} chars, body <= {spec['body_chars']} chars
Creative: {spec['creative']}
Platform note: {spec['note']}

Write {self.variants} ad variants. Each must test a DIFFERENT hypothesis about
why this audience would switch (e.g. cost, setup friction, model choice,
rate limits).

Return strict JSON:
{{"variants": [{{"hypothesis": "...", "headline": "...", "body": "...",
"creative_brief": "...", "cta": "..."}}]}}
"""
        try:
            raw = self.think(prompt, max_tokens=2000, response_json=True)
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.fail(f"Unparseable JSON generating creative for {result.channel}")
            return None
        except Exception as e:
            self.fail(f"Creative generation failed for {result.channel}: {e}")
            return None

        variants = payload.get("variants", []) or []

        # Enforce the limits rather than trusting the model to have respected
        # them. Truncated copy on a paid impression is money spent on a
        # half-sentence.
        overlong = [
            v.get("headline", "")
            for v in variants
            if len(v.get("headline", "")) > spec["headline_chars"]
        ]
        if overlong:
            self.note(
                f"{result.channel}: {len(overlong)} headline(s) exceed "
                f"{spec['headline_chars']} chars and need trimming before upload."
            )

        return {
            "channel": result.channel,
            "measured_cac_usd": result.cac_usd,
            "platform_spec": spec,
            "variants": variants,
            "status": "draft-review-before-upload",
        }

    def run(self) -> AgentRun:
        if not self.results:
            self.fail(
                "No channel results supplied. This agent is gated on measured CAC — "
                "it does not generate creative for channels that have not been tested."
            )
            return self.run_record

        eligible = self.eligible_channels()
        if not eligible:
            self.note(
                "No channel cleared the CAC target, so no creative was generated. "
                "This is the intended outcome, not a failure — creative for an "
                "unproven channel is how a test budget becomes a scaled budget "
                "without anyone deciding to spend it."
            )
            return self.run_record

        for result in eligible:
            creative = self.generate_for(result)
            if creative:
                self.emit("ad_creative", creative)

        self.note(
            "Review before upload. Every claim needs a number behind it that "
            "survives a click-through to the benchmark page."
        )
        return self.run_record


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, help="JSON file of channel results")
    parser.add_argument("--variants", type=int, default=3)
    args = parser.parse_args()

    with open(args.results, encoding="utf-8") as fh:
        rows = json.load(fh)

    results = [
        ChannelResult(
            channel=r["channel"],
            spend_usd=float(r["spend_usd"]),
            paying_accounts=int(r["paying_accounts"]),
        )
        for r in rows
    ]

    agent = Producer(results=results, variants=args.variants)
    run = agent.run()
    path = agent.save()
    print(f"Producer run written to {path}")
    print(f"{len(run.outputs)} channel(s) with creative, cost ${run.total_cost_usd:.4f}")
    for note in run.notes:
        print(f"NOTE: {note}")
    for error in run.errors:
        print(f"ERROR: {error}")
    return 1 if run.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
