"""A standing, per-model latency benchmark — capped, gated, and honest about gaps.

Board Series 02 asks Gatewayz to "productize the benchmark harness... as a
standing, per-model surface, not a one-off". Doing that means real inference
against every served model on a schedule, which is recurring real spend. So the
shape here is deliberate:

  DRY-RUN BY DEFAULT   Prints the plan and the estimated cost. Sends nothing.
  --execute REQUIRED   A human decides to spend, per invocation.
  HARD CAP IN CODE     Estimated over budget refuses to start; actual over
                       budget stops mid-run. The cap is not a flag you can
                       forget -- there is no way to run uncapped.
  THREE STATES         measured / failed / not-attempted are distinct in the
                       output. A model that was never probed must never render
                       as a zero, because a reader who cannot tell those apart
                       learns to trust a number that is not there.

The partner plan's own rule, which this follows: never ship a surface that
prints "no measured runs yet" beside a shell. Measure first, publish second.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime

API = os.environ.get("GATEWAYZ_API", "https://api.gatewayz.ai")
DEFAULT_BUDGET_USD = 2.00
DEFAULT_SAMPLES = 3
# Small and identical across models: this measures the gateway and the
# provider, not the prompt. A long prompt would make the figure a statement
# about our test data instead.
PROMPT = "Reply with the single word: ok"
MAX_TOKENS = 8


@dataclass
class ModelResult:
    model: str
    state: str  # measured | failed | not-attempted
    samples: list[float] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None

    def stats(self) -> dict:
        if not self.samples:
            return {}
        s = sorted(self.samples)
        return {
            "n": len(s),
            "min_s": round(s[0], 3),
            "median_s": round(statistics.median(s), 3),
            "max_s": round(s[-1], 3),
        }


def _key() -> str:
    path = os.path.expanduser("~/.gatewayz-flashy.env")
    if os.path.exists(path):
        for line in open(path):
            if line.startswith("GATEWAYZ_API_KEY="):
                return line.split("=", 1)[1].strip()
    k = os.environ.get("GATEWAYZ_API_KEY", "").strip()
    if not k:
        raise SystemExit("No API key: set GATEWAYZ_API_KEY or ~/.gatewayz-flashy.env")
    return k


def served_models(key: str) -> list[dict]:
    req = urllib.request.Request(f"{API}/v1/models", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode()).get("data") or []


def estimate_cost(models: list[dict], samples: int) -> float:
    """Price the plan BEFORE spending, from the catalog's own pricing."""
    total = 0.0
    for m in models:
        p = m.get("pricing") or {}
        prompt_rate = float(p.get("prompt") or 0)
        completion_rate = float(p.get("completion") or 0)
        # ~8 prompt tokens, MAX_TOKENS completion, per sample.
        total += samples * (8 * prompt_rate + MAX_TOKENS * completion_rate)
    return total


def probe(model: str, key: str, timeout: int = 60) -> tuple[float, dict] | None:
    body = json.dumps(
        {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "messages": [{"role": "user", "content": PROMPT}],
        }
    ).encode()
    req = urllib.request.Request(
        f"{API}/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            # Tagged so the run is attributable in our own usage rollup --
            # the harness is a caller like any other and should show up.
            "x-gatewayz-tag": "bench/standing-harness",
        },
    )
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read().decode())
    return time.monotonic() - t0, payload.get("usage") or {}


def run(models: list[dict], key: str, samples: int, budget: float) -> list[ModelResult]:
    results: list[ModelResult] = []
    spent = 0.0
    for m in models:
        mid = str(m.get("id"))
        res = ModelResult(model=mid, state="not-attempted")
        if spent >= budget:
            # Stop, but keep the row. A model we never reached is NOT a zero.
            res.error = "budget reached before this model"
            results.append(res)
            continue
        try:
            for _ in range(samples):
                elapsed, usage = probe(mid, key)
                res.samples.append(elapsed)
                res.input_tokens += int(usage.get("input_tokens") or 0)
                res.output_tokens += int(usage.get("output_tokens") or 0)
            p = m.get("pricing") or {}
            res.cost_usd = round(
                res.input_tokens * float(p.get("prompt") or 0)
                + res.output_tokens * float(p.get("completion") or 0),
                6,
            )
            spent += res.cost_usd
            res.state = "measured"
        except urllib.error.HTTPError as e:
            res.state = "failed"
            res.error = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            res.state = "failed"
            res.error = type(e).__name__
        results.append(res)
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--execute",
        action="store_true",
        help="actually send requests. Without it, nothing is spent.",
    )
    ap.add_argument("--budget-usd", type=float, default=DEFAULT_BUDGET_USD)
    ap.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    ap.add_argument("--limit", type=int, default=0, help="probe at most N models")
    ap.add_argument("--out", default="", help="write the JSON record here")
    args = ap.parse_args()

    key = _key()
    models = served_models(key)
    if args.limit:
        models = models[: args.limit]
    est = estimate_cost(models, args.samples)

    print(f"models: {len(models)}  samples each: {args.samples}")
    print(f"estimated cost: ${est:.4f}   budget: ${args.budget_usd:.2f}")

    if est > args.budget_usd:
        print(f"REFUSING: the estimate exceeds the budget. Raise --budget-usd deliberately.")
        return 2

    if not args.execute:
        print("\nDRY RUN — nothing sent. Re-run with --execute to spend.")
        return 0

    results = run(models, key, args.samples, args.budget_usd)
    measured = [r for r in results if r.state == "measured"]
    record = {
        "benchmark": "1",
        "generated": datetime.now(UTC).isoformat(),
        "prompt_tokens_approx": 8,
        "max_tokens": MAX_TOKENS,
        "samples_per_model": args.samples,
        "measured": len(measured),
        "failed": sum(1 for r in results if r.state == "failed"),
        "not_attempted": sum(1 for r in results if r.state == "not-attempted"),
        "total_cost_usd": round(sum(r.cost_usd for r in results), 6),
        "results": [
            {
                "model": r.model,
                "state": r.state,
                "error": r.error,
                "cost_usd": r.cost_usd,
                **r.stats(),
            }
            for r in results
        ],
    }

    print(
        f"\nmeasured {record['measured']} · failed {record['failed']} · "
        f"not attempted {record['not_attempted']} · spent ${record['total_cost_usd']:.4f}"
    )
    if not measured:
        # Said in a sentence, never as a grid of zeros.
        print("no measured runs — nothing is published from this run.")
    if args.out:
        with open(args.out, "w") as f:
            json.dump(record, f, indent=2)
        print(f"record written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
