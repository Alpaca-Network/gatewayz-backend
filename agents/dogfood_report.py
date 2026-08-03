"""The monthly dogfood report — "here is our bill".

GTM plan section 2.3: publish what the agent stack costs to run through our own
gateway. It is content, product proof, and the seed of the agent-platform
direction all at once.

It only works if the numbers are real, so this reads the usage ledger and
reports exactly what is in it. No projections, no annualised figures, no
"equivalent to" comparisons against a headcount.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from agents.base import OUTPUT_ROOT, UsageLedger


def render_markdown(summary: dict, period_label: str) -> str:
    """Render the ledger summary as a publishable markdown post."""
    if summary.get("records", 0) == 0:
        return (
            f"# Agent stack usage — {period_label}\n\n"
            "No agent runs recorded for this period. Nothing to report.\n"
        )

    lines = [
        f"# Agent stack usage — {period_label}",
        "",
        "Everything below ran through Gatewayz, on the same API any customer uses.",
        "",
        f"- **Total cost:** ${summary['total_cost_usd']:.2f}",
        f"- **Total tokens:** {summary['total_tokens']:,}",
        f"- **Inference calls:** {summary['records']:,}",
    ]

    cache_reads = summary.get("cache_read_tokens", 0)
    if cache_reads:
        share = cache_reads / summary["total_tokens"] * 100 if summary["total_tokens"] else 0
        lines.append(
            f"- **Tokens served from cache:** {cache_reads:,} ({share:.0f}% of all tokens)"
        )
    else:
        # Say so rather than omitting the line — an absent cache figure in a post
        # about caching is the first thing a sceptical reader notices.
        lines.append(
            "- **Tokens served from cache:** 0 — these agents run on a stable system "
            "prompt, so this should be non-zero. Worth investigating before the next report."
        )

    lines += ["", "## By agent", "", "| Agent | Calls | Tokens | Cost |", "|---|---:|---:|---:|"]

    for name, vals in sorted(
        summary["by_agent"].items(), key=lambda kv: kv[1]["cost_usd"], reverse=True
    ):
        lines.append(
            f"| {name} | {vals['calls']:,} | {vals['tokens']:,} | ${vals['cost_usd']:.2f} |"
        )

    lines += [
        "",
        "## Notes",
        "",
        "- These are gross inference costs at our own published rates. No internal discount.",
        "- Figures come straight from the usage ledger each agent writes on every call.",
        "- No projections or annualised numbers. This is what the month cost.",
    ]

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30, help="Look-back window (default 30)")
    parser.add_argument("--label", default=None, help="Period label for the heading")
    parser.add_argument("--out", default=None, help="Write markdown here")
    args = parser.parse_args()

    since = int(time.time()) - args.days * 86400
    summary = UsageLedger().summarize(since=since)
    label = args.label or f"last {args.days} days"

    markdown = render_markdown(summary, label)

    out_path = Path(args.out) if args.out else OUTPUT_ROOT / "dogfood-report.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")

    print(markdown)
    print(f"\nWritten to {out_path}")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
