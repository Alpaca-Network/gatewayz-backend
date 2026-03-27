#!/usr/bin/env python3
"""
Live Model Inference Test — verify every displayed model can generate a response.

Fetches the full catalog from the Gatewayz API, sends a minimal chat completion
to each model, and reports pass / fail / skip per model with timing.

Usage:
    # Test against production
    python scripts/test_all_models.py --base-url https://api.gatewayz.ai --api-key $KEY

    # Test against local dev server
    python scripts/test_all_models.py --api-key $KEY

    # Filter by gateway or provider
    python scripts/test_all_models.py --api-key $KEY --gateway openrouter
    python scripts/test_all_models.py --api-key $KEY --provider fireworks

    # Limit concurrency and number of models (useful for quick checks)
    python scripts/test_all_models.py --api-key $KEY --limit 20 --concurrency 3

    # Export results to JSON
    python scripts/test_all_models.py --api-key $KEY --output results.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

try:
    import httpx
except ImportError:
    print("ERROR: httpx is required. Install with: pip install httpx")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ModelResult:
    model_id: str
    gateway: str
    provider: str
    status: str  # "pass", "fail", "skip", "timeout", "error"
    status_code: int | None = None
    latency_ms: float = 0.0
    error: str | None = None
    response_preview: str | None = None


@dataclass
class TestReport:
    timestamp: str = ""
    base_url: str = ""
    total_models: int = 0
    tested: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    timed_out: int = 0
    errored: int = 0
    duration_s: float = 0.0
    results: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Catalog fetcher
# ---------------------------------------------------------------------------

SKIP_MODALITIES = {"image", "audio", "embedding", "tts", "stt"}

# Models known to be non-chat (image gen, embeddings, etc.)
SKIP_MODEL_PREFIXES = (
    "dall-e",
    "stable-diffusion",
    "sdxl",
    "flux",
    "midjourney",
    "text-embedding",
    "whisper",
    "tts-",
    "jina-embeddings",
    "nomic-embed",
    "bge-",
)


async def fetch_catalog(
    client: httpx.AsyncClient,
    base_url: str,
    gateway: str | None,
    provider: str | None,
) -> list[dict]:
    """Fetch models from the catalog endpoint."""
    params: dict = {"limit": 10000}
    if gateway:
        params["gateway"] = gateway

    resp = await client.get(f"{base_url}/models", params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # The endpoint returns {"data": [...]} or a list directly
    models = data.get("data", data) if isinstance(data, dict) else data

    # Filter by provider if specified
    if provider:
        provider_lower = provider.lower()
        models = [
            m
            for m in models
            if m.get("provider_slug", "").lower() == provider_lower
            or m.get("source_gateway", "").lower() == provider_lower
        ]

    return models


def should_skip(model: dict) -> str | None:
    """Return a skip reason if this model shouldn't be inference-tested, else None."""
    model_id = model.get("id", "").lower()

    # Skip non-chat modalities
    modality = model.get("modality", "").lower()
    if modality and modality in SKIP_MODALITIES:
        return f"non-chat modality: {modality}"

    # Skip known non-chat model prefixes
    for prefix in SKIP_MODEL_PREFIXES:
        if model_id.startswith(prefix) or f"/{prefix}" in model_id:
            return f"non-chat model prefix: {prefix}"

    return None


# ---------------------------------------------------------------------------
# Inference tester
# ---------------------------------------------------------------------------

TEST_PAYLOAD = {
    "messages": [{"role": "user", "content": "Say OK"}],
    "max_tokens": 5,
    "temperature": 0,
}


async def test_model(
    client: httpx.AsyncClient,
    base_url: str,
    model: dict,
    timeout_s: float,
    semaphore: asyncio.Semaphore,
) -> ModelResult:
    """Send a minimal chat completion and check the response."""
    model_id = model.get("id", "unknown")
    gateway = model.get("source_gateway", model.get("provider_slug", "unknown"))
    provider = model.get("provider_slug", gateway)

    # Check if we should skip
    skip_reason = should_skip(model)
    if skip_reason:
        return ModelResult(
            model_id=model_id,
            gateway=gateway,
            provider=provider,
            status="skip",
            error=skip_reason,
        )

    payload = {**TEST_PAYLOAD, "model": model_id}

    async with semaphore:
        start = time.monotonic()
        try:
            resp = await client.post(
                f"{base_url}/v1/chat/completions",
                json=payload,
                timeout=timeout_s,
            )
            latency = (time.monotonic() - start) * 1000

            if resp.status_code == 200:
                body = resp.json()
                content = (
                    body.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                return ModelResult(
                    model_id=model_id,
                    gateway=gateway,
                    provider=provider,
                    status="pass",
                    status_code=200,
                    latency_ms=round(latency, 1),
                    response_preview=content[:80] if content else "(empty)",
                )
            else:
                error_detail = ""
                try:
                    err_body = resp.json()
                    error_detail = err_body.get("error", {}).get(
                        "message", err_body.get("detail", "")
                    )
                except Exception:
                    error_detail = resp.text[:200]

                return ModelResult(
                    model_id=model_id,
                    gateway=gateway,
                    provider=provider,
                    status="fail",
                    status_code=resp.status_code,
                    latency_ms=round(latency, 1),
                    error=f"HTTP {resp.status_code}: {error_detail[:150]}",
                )

        except httpx.TimeoutException:
            latency = (time.monotonic() - start) * 1000
            return ModelResult(
                model_id=model_id,
                gateway=gateway,
                provider=provider,
                status="timeout",
                latency_ms=round(latency, 1),
                error=f"Timed out after {timeout_s}s",
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return ModelResult(
                model_id=model_id,
                gateway=gateway,
                provider=provider,
                status="error",
                latency_ms=round(latency, 1),
                error=str(exc)[:200],
            )


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

STATUS_ICONS = {
    "pass": "\033[32m PASS\033[0m",
    "fail": "\033[31m FAIL\033[0m",
    "skip": "\033[90m SKIP\033[0m",
    "timeout": "\033[33m TIME\033[0m",
    "error": "\033[31m ERR \033[0m",
}


def print_result(idx: int, total: int, r: ModelResult) -> None:
    icon = STATUS_ICONS.get(r.status, " ??? ")
    latency = f"{r.latency_ms:7.0f}ms" if r.latency_ms else "       -"
    code = f"{r.status_code}" if r.status_code else "   "
    detail = ""
    if r.status == "pass":
        detail = r.response_preview or ""
    elif r.error:
        detail = r.error[:80]

    print(f"  [{idx:4d}/{total}] {icon}  {code}  {latency}  {r.model_id[:55]:<55}  {detail}")


def print_summary(report: TestReport) -> None:
    print()
    print("=" * 100)
    print("RESULTS SUMMARY")
    print("=" * 100)
    print(f"  Catalog models:  {report.total_models}")
    print(f"  Tested:          {report.tested}")
    print(f"  Passed:          \033[32m{report.passed}\033[0m")
    print(f"  Failed:          \033[31m{report.failed}\033[0m")
    print(f"  Timed out:       \033[33m{report.timed_out}\033[0m")
    print(f"  Errors:          \033[31m{report.errored}\033[0m")
    print(f"  Skipped:         {report.skipped}")
    print(f"  Duration:        {report.duration_s:.1f}s")

    if report.tested > 0:
        pass_rate = report.passed / report.tested * 100
        color = "\033[32m" if pass_rate >= 90 else "\033[33m" if pass_rate >= 70 else "\033[31m"
        print(f"  Pass rate:       {color}{pass_rate:.1f}%\033[0m")
    print("=" * 100)

    # Breakdown by gateway
    gateway_stats: dict[str, dict] = {}
    for r in report.results:
        gw = r.get("gateway", "unknown")
        if gw not in gateway_stats:
            gateway_stats[gw] = {"pass": 0, "fail": 0, "timeout": 0, "error": 0, "skip": 0}
        gateway_stats[gw][r["status"]] = gateway_stats[gw].get(r["status"], 0) + 1

    if gateway_stats:
        print("\nPer-gateway breakdown:")
        print(f"  {'Gateway':<25} {'Pass':>6} {'Fail':>6} {'Time':>6} {'Err':>6} {'Skip':>6}")
        print(f"  {'-' * 25} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 6}")
        for gw, stats in sorted(gateway_stats.items()):
            print(
                f"  {gw:<25} {stats.get('pass', 0):>6} {stats.get('fail', 0):>6} "
                f"{stats.get('timeout', 0):>6} {stats.get('error', 0):>6} {stats.get('skip', 0):>6}"
            )

    # Show failures
    failures = [r for r in report.results if r["status"] in ("fail", "timeout", "error")]
    if failures:
        print(f"\nFailed models ({len(failures)}):")
        for r in failures[:50]:
            print(f"  \033[31m{r['status'].upper():>7}\033[0m  {r['model_id']:<55}  {(r.get('error') or '')[:60]}")
        if len(failures) > 50:
            print(f"  ... and {len(failures) - 50} more")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run(args: argparse.Namespace) -> TestReport:
    base_url = args.base_url.rstrip("/")
    api_key = args.api_key

    headers = {"Authorization": f"Bearer {api_key}"}
    semaphore = asyncio.Semaphore(args.concurrency)

    report = TestReport(
        timestamp=datetime.now(UTC).isoformat(),
        base_url=base_url,
    )

    async with httpx.AsyncClient(headers=headers) as client:
        # 1. Fetch catalog
        print(f"Fetching model catalog from {base_url}/models ...")
        models = await fetch_catalog(client, base_url, args.gateway, args.provider)
        report.total_models = len(models)
        print(f"Found {len(models)} models in catalog")

        if not models:
            print("No models found. Check --base-url and --api-key.")
            return report

        # Apply limit
        if args.limit and args.limit < len(models):
            models = models[: args.limit]
            print(f"Limited to first {args.limit} models")

        # 2. Test all models concurrently
        print(f"\nTesting {len(models)} models (concurrency={args.concurrency}, timeout={args.timeout}s)...")
        print("-" * 100)

        start_time = time.monotonic()

        tasks = [test_model(client, base_url, m, args.timeout, semaphore) for m in models]
        results: list[ModelResult] = await asyncio.gather(*tasks)

        report.duration_s = round(time.monotonic() - start_time, 1)

        # 3. Print results as they complete
        for idx, r in enumerate(results, 1):
            print_result(idx, len(results), r)
            report.results.append(asdict(r))
            if r.status == "pass":
                report.passed += 1
                report.tested += 1
            elif r.status == "fail":
                report.failed += 1
                report.tested += 1
            elif r.status == "timeout":
                report.timed_out += 1
                report.tested += 1
            elif r.status == "error":
                report.errored += 1
                report.tested += 1
            elif r.status == "skip":
                report.skipped += 1

    print_summary(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test all displayed models by sending a minimal chat completion to each."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", "http://localhost:8000"),
        help="API base URL (default: $BASE_URL or http://localhost:8000)",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("TEST_API_KEY"),
        help="API key for authentication (default: $TEST_API_KEY)",
    )
    parser.add_argument(
        "--gateway",
        default=None,
        help="Filter models by gateway (e.g., openrouter, fireworks)",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Filter models by provider slug",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of models to test",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max concurrent requests (default: 5)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Export results to JSON file",
    )

    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: --api-key or $TEST_API_KEY is required")
        sys.exit(1)

    report = asyncio.run(run(args))

    # Export JSON if requested
    if args.output:
        output_path = Path(args.output)
        with open(output_path, "w") as f:
            json.dump(asdict(report), f, indent=2, default=str)
        print(f"\nResults exported to {output_path}")

    # Exit with failure if pass rate < 70%
    if report.tested > 0 and (report.passed / report.tested) < 0.7:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
