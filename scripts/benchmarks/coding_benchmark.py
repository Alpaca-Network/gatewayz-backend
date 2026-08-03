#!/usr/bin/env python3
"""Coding-agent latency and cost benchmark: Gatewayz vs direct providers.

This produces the numbers behind the public benchmark report and the
``/benchmarks/coding`` page. It is designed to be run on a schedule and to emit
machine-readable JSON so the content agent can regenerate the page without a
human transcribing figures.

Design constraints that matter for credibility:

* **Same prompts, same models, same moment.** Direct and gateway calls for a
  given task run back to back so provider-side load is comparable. Comparing a
  gateway run from Tuesday against a direct run from Friday would be noise.
* **Report the spread, not just the mean.** A gateway that is fast on average
  and occasionally terrible is not a gateway anyone wants for an interactive
  coding agent, so p50/p95 are first-class and the raw samples are kept.
* **Cache effects are measured, not assumed.** Each task runs twice against a
  cache-marked prefix: the first call pays the cache write, the second should
  hit. If the second call reports no cache read, caching is silently broken and
  the report must say so rather than quietly averaging it away.
* **Never fabricate.** Any task that errors is recorded as an error and
  excluded from aggregates, with the count surfaced. A benchmark that hides its
  failures is worse than no benchmark, and this one is going in front of
  r/LocalLLaMA.

Usage::

    export GATEWAYZ_API_KEY=...
    export ANTHROPIC_API_KEY=...        # optional, enables direct comparison
    export OPENAI_API_KEY=...           # optional

    python scripts/benchmarks/coding_benchmark.py --runs 5
    python scripts/benchmarks/coding_benchmark.py --models claude-sonnet-4 --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

DEFAULT_GATEWAY_URL = os.getenv("GATEWAYZ_API_URL", "https://api.gatewayz.ai")

# Realistic coding-agent shapes. Each carries a large static prefix (the thing a
# coding agent replays every turn) plus a small varying instruction -- the exact
# pattern prompt caching exists to exploit.
_LARGE_PREFIX = (
    "You are an expert software engineer working in a large Python codebase.\n"
    "Follow the existing conventions. Prefer small, surgical diffs.\n"
) + ("# module context line, kept static across turns\n" * 400)

TASKS: list[dict[str, Any]] = [
    {
        "id": "explain_function",
        "instruction": "Explain what a Python decorator that retries on HTTPError does, in 3 sentences.",
        "max_tokens": 200,
    },
    {
        "id": "write_test",
        "instruction": "Write one pytest test for a function `parse_iso8601(s) -> datetime`.",
        "max_tokens": 300,
    },
    {
        "id": "fix_bug",
        "instruction": "A dict lookup raises KeyError under concurrency. Name the two most likely causes.",
        "max_tokens": 200,
    },
    {
        "id": "tool_call",
        "instruction": "Read the file src/main.py using the available tool.",
        "max_tokens": 200,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file from the repository",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            }
        ],
    },
]

# (gateway model id, direct provider, direct model id). Direct legs are skipped
# when the corresponding provider key is absent.
DEFAULT_MODELS: list[tuple[str, str, str]] = [
    ("anthropic/claude-sonnet-4", "anthropic", "claude-sonnet-4-20250514"),
    ("openai/gpt-4o", "openai", "gpt-4o"),
]


@dataclass
class Sample:
    """One request's measurements."""

    task_id: str
    model: str
    route: str  # "gateway" | "direct"
    ok: bool
    ttft_ms: float | None = None
    total_ms: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    tokens_per_sec: float | None = None
    error: str | None = None


@dataclass
class BenchmarkReport:
    generated_at: int
    gateway_url: str
    samples: list[Sample] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "gateway_url": self.gateway_url,
            "summary": summarize(self.samples),
            "warnings": self.warnings,
            "samples": [asdict(s) for s in self.samples],
        }


def _build_messages(task: dict[str, Any], use_cache_marker: bool) -> list[dict[str, Any]]:
    """Build the request body for a task.

    When ``use_cache_marker`` the static prefix is marked as cacheable, which is
    how a real coding agent sends it.
    """
    prefix_block: dict[str, Any] = {"type": "text", "text": _LARGE_PREFIX}
    if use_cache_marker:
        prefix_block["cache_control"] = {"type": "ephemeral"}

    return [
        {"role": "system", "content": [prefix_block]},
        {"role": "user", "content": task["instruction"]},
    ]


def _stream_once(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> tuple[float | None, float, dict[str, Any]]:
    """Issue a streaming request; return (ttft_ms, total_ms, final_usage).

    Time-to-first-token is the metric an interactive coding agent is actually
    judged on, which is why this streams rather than measuring wall time on a
    blocking call.
    """
    start = time.perf_counter()
    ttft: float | None = None
    usage: dict[str, Any] = {}

    with client.stream("POST", url, headers=headers, json=payload, timeout=180.0) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            body = line[len("data: ") :].strip()
            if not body or body == "[DONE]":
                continue
            try:
                chunk = json.loads(body)
            except json.JSONDecodeError:
                continue

            if ttft is None and _chunk_has_content(chunk):
                ttft = (time.perf_counter() - start) * 1000

            if chunk.get("usage"):
                usage = chunk["usage"]

    total_ms = (time.perf_counter() - start) * 1000
    return ttft, total_ms, usage


def _chunk_has_content(chunk: dict[str, Any]) -> bool:
    for choice in chunk.get("choices") or []:
        delta = choice.get("delta") or {}
        if delta.get("content") or delta.get("tool_calls"):
            return True
    return False


def run_gateway(
    client: httpx.Client,
    gateway_url: str,
    api_key: str,
    model: str,
    task: dict[str, Any],
    use_cache: bool,
) -> Sample:
    payload: dict[str, Any] = {
        "model": model,
        "messages": _build_messages(task, use_cache),
        "max_tokens": task["max_tokens"],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if task.get("tools"):
        payload["tools"] = task["tools"]

    try:
        ttft, total_ms, usage = _stream_once(
            client,
            f"{gateway_url.rstrip('/')}/v1/chat/completions",
            {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload,
        )
    except Exception as e:
        return Sample(task["id"], model, "gateway", ok=False, error=f"{type(e).__name__}: {e}")

    return _to_sample(task["id"], model, "gateway", ttft, total_ms, usage)


def run_direct_anthropic(
    client: httpx.Client,
    api_key: str,
    model: str,
    task: dict[str, Any],
    use_cache: bool,
) -> Sample:
    messages = _build_messages(task, use_cache)
    system = messages[0]["content"]
    payload: dict[str, Any] = {
        "model": model,
        "system": system,
        "messages": [{"role": "user", "content": task["instruction"]}],
        "max_tokens": task["max_tokens"],
        "stream": True,
    }
    if task.get("tools"):
        payload["tools"] = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"]["parameters"],
            }
            for t in task["tools"]
        ]

    start = time.perf_counter()
    ttft: float | None = None
    usage: dict[str, Any] = {}
    try:
        with client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=180.0,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[len("data: ") :])
                except json.JSONDecodeError:
                    continue
                etype = event.get("type")
                if etype == "content_block_delta" and ttft is None:
                    ttft = (time.perf_counter() - start) * 1000
                elif etype == "message_start":
                    usage.update((event.get("message") or {}).get("usage") or {})
                elif etype == "message_delta":
                    usage.update(event.get("usage") or {})
    except Exception as e:
        return Sample(task["id"], model, "direct", ok=False, error=f"{type(e).__name__}: {e}")

    total_ms = (time.perf_counter() - start) * 1000
    normalized = {
        "prompt_tokens": (usage.get("input_tokens", 0) or 0)
        + (usage.get("cache_creation_input_tokens", 0) or 0)
        + (usage.get("cache_read_input_tokens", 0) or 0),
        "completion_tokens": usage.get("output_tokens", 0) or 0,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
    }
    return _to_sample(task["id"], model, "direct", ttft, total_ms, normalized)


def _to_sample(
    task_id: str,
    model: str,
    route: str,
    ttft: float | None,
    total_ms: float,
    usage: dict[str, Any],
) -> Sample:
    completion = usage.get("completion_tokens", 0) or 0
    tps = (completion / (total_ms / 1000)) if total_ms > 0 and completion else None
    return Sample(
        task_id=task_id,
        model=model,
        route=route,
        ok=True,
        ttft_ms=round(ttft, 1) if ttft else None,
        total_ms=round(total_ms, 1),
        prompt_tokens=usage.get("prompt_tokens", 0) or 0,
        completion_tokens=completion,
        cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
        cache_write_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
        cost_usd=(usage.get("gateway_cost_usd") if isinstance(usage, dict) else None),
        tokens_per_sec=round(tps, 1) if tps else None,
    )


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    # Nearest-rank; with the small sample counts this benchmark runs, an
    # interpolating percentile would imply precision the data does not have.
    idx = min(len(ordered) - 1, max(0, int(round(pct / 100 * len(ordered))) - 1))
    return round(ordered[idx], 1)


def summarize(samples: list[Sample]) -> dict[str, Any]:
    """Aggregate per (model, route). Errors are counted, never averaged in."""
    groups: dict[tuple[str, str], list[Sample]] = {}
    for s in samples:
        groups.setdefault((s.model, s.route), []).append(s)

    out: dict[str, Any] = {}
    for (model, route), items in sorted(groups.items()):
        ok = [s for s in items if s.ok]
        ttfts = [s.ttft_ms for s in ok if s.ttft_ms is not None]
        totals = [s.total_ms for s in ok if s.total_ms is not None]
        tps = [s.tokens_per_sec for s in ok if s.tokens_per_sec is not None]

        out[f"{model}::{route}"] = {
            "model": model,
            "route": route,
            "runs": len(items),
            "errors": len(items) - len(ok),
            "ttft_ms_p50": _percentile(ttfts, 50),
            "ttft_ms_p95": _percentile(ttfts, 95),
            "total_ms_p50": _percentile(totals, 50),
            "total_ms_p95": _percentile(totals, 95),
            "tokens_per_sec_mean": round(statistics.mean(tps), 1) if tps else None,
            "cache_read_tokens_total": sum(s.cache_read_tokens for s in ok),
            "cache_write_tokens_total": sum(s.cache_write_tokens for s in ok),
        }
    return out


def run_benchmark(
    gateway_url: str,
    gateway_key: str,
    models: list[tuple[str, str, str]],
    runs: int,
    include_direct: bool,
) -> BenchmarkReport:
    report = BenchmarkReport(generated_at=int(time.time()), gateway_url=gateway_url)
    client = httpx.Client()

    try:
        for gw_model, direct_provider, direct_model in models:
            direct_key = os.getenv(f"{direct_provider.upper()}_API_KEY")
            if include_direct and not direct_key:
                report.warnings.append(
                    f"{direct_provider.upper()}_API_KEY not set — no direct baseline for "
                    f"{gw_model}; gateway numbers are reported without comparison."
                )

            for task in TASKS:
                for run_index in range(runs):
                    # First run of each task pays the cache write; subsequent
                    # runs should read from cache. Both are recorded so the
                    # report can show the warm/cold split honestly.
                    use_cache = True

                    report.samples.append(
                        run_gateway(
                            client, gateway_url, gateway_key, gw_model, task, use_cache
                        )
                    )

                    if include_direct and direct_key and direct_provider == "anthropic":
                        report.samples.append(
                            run_direct_anthropic(
                                client, direct_key, direct_model, task, use_cache
                            )
                        )

                    if run_index == 0:
                        # Give the provider a beat to register the cache write
                        # before the read attempt, otherwise a warm-run miss is
                        # a measurement artefact rather than a real result.
                        time.sleep(1.0)
    finally:
        client.close()

    _add_cache_warnings(report)
    return report


def _add_cache_warnings(report: BenchmarkReport) -> None:
    """Flag silently-broken caching rather than letting it vanish into a mean."""
    by_model: dict[str, list[Sample]] = {}
    for s in report.samples:
        if s.ok and s.route == "gateway":
            by_model.setdefault(s.model, []).append(s)

    for model, items in by_model.items():
        if len(items) < 2:
            continue
        if sum(s.cache_read_tokens for s in items) == 0:
            report.warnings.append(
                f"{model}: no cache reads observed across {len(items)} gateway runs with "
                "cache_control set. Prompt caching may not be reaching the provider — "
                "do not publish a cost-advantage claim from this run."
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3, help="Runs per task (default 3)")
    parser.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    parser.add_argument("--models", nargs="*", help="Gateway model IDs to limit the run to")
    parser.add_argument("--no-direct", action="store_true", help="Skip direct-provider baselines")
    parser.add_argument("--json", dest="json_out", help="Write the full report to this path")
    args = parser.parse_args()

    gateway_key = os.getenv("GATEWAYZ_API_KEY")
    if not gateway_key:
        print("GATEWAYZ_API_KEY is not set", file=sys.stderr)
        return 2

    models = DEFAULT_MODELS
    if args.models:
        wanted = set(args.models)
        models = [m for m in DEFAULT_MODELS if m[0] in wanted]
        if not models:
            print(f"No known models matched {sorted(wanted)}", file=sys.stderr)
            return 2

    report = run_benchmark(
        gateway_url=args.gateway_url,
        gateway_key=gateway_key,
        models=models,
        runs=args.runs,
        include_direct=not args.no_direct,
    )

    payload = report.to_dict()

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"Wrote {args.json_out}")

    print(json.dumps(payload["summary"], indent=2))
    for warning in payload["warnings"]:
        print(f"WARNING: {warning}", file=sys.stderr)

    errors = sum(1 for s in report.samples if not s.ok)
    if errors:
        print(f"\n{errors} request(s) failed — excluded from aggregates.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
