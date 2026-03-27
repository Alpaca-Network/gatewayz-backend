"""
Live Model Test endpoint — admin-only inference sweep across all catalog models.

Sends a minimal chat completion to each model (or a filtered subset),
bypassing billing, and returns per-provider/per-model pass/fail stats.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.security.deps import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/live-test", tags=["admin", "live-test"])

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

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
    "text-moderation",
)

SKIP_MODALITIES = {"image", "audio", "embedding", "tts", "stt", "moderation"}

TEST_MESSAGES = [{"role": "user", "content": "Say OK"}]
TEST_MAX_TOKENS = 5


class ModelTestResult(BaseModel):
    model_id: str
    gateway: str
    provider: str
    status: str = Field(description="pass, fail, skip, timeout, error")
    status_code: int | None = None
    latency_ms: float = 0.0
    error: str | None = None
    response_preview: str | None = None


class ProviderSummary(BaseModel):
    provider: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    timed_out: int = 0
    errored: int = 0
    skipped: int = 0
    avg_latency_ms: float = 0.0


class LiveTestReport(BaseModel):
    timestamp: str
    duration_s: float = 0.0
    total_models: int = 0
    tested: int = 0
    passed: int = 0
    failed: int = 0
    timed_out: int = 0
    errored: int = 0
    skipped: int = 0
    pass_rate: float = 0.0
    by_provider: list[ProviderSummary] = []
    failures: list[ModelTestResult] = []
    results: list[ModelTestResult] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_model_id(model: dict) -> str:
    """Resolve the canonical model ID that the chat endpoint understands.

    The catalog may store display names (e.g. "GPT-4O Mini") instead of
    canonical IDs (e.g. "openai/gpt-4o-mini").  Try multiple strategies:
    1. If the id already contains "/" it's likely canonical — use as-is.
    2. Extract from description pattern "Provider model-slug model."
    3. Construct from provider_slug + slugified display name.
    """
    raw_id = model.get("id", "unknown")

    # Already canonical (contains provider prefix)
    if "/" in raw_id:
        return raw_id

    # Try extracting from description: "OpenAI chatgpt-4o-latest model."
    desc = model.get("description", "")
    if desc:
        import re

        # Pattern: "{Provider} {model-id} model." or "{Provider} {model-id} ..."
        m = re.match(r"^\w[\w\s]* ([\w./-]+(?:-[\w./-]+)+)", desc)
        if m:
            candidate = m.group(1).rstrip(".")
            gateway = model.get("source_gateway", model.get("provider_slug", ""))
            if gateway and "/" not in candidate:
                return f"{gateway}/{candidate}"
            return candidate

    # Fallback: slugify the display name
    gateway = model.get("source_gateway", model.get("provider_slug", ""))
    slug = raw_id.lower().replace(" ", "-")
    if gateway:
        return f"{gateway}/{slug}"
    return slug


def _should_skip(model: dict) -> str | None:
    model_id = _resolve_model_id(model).lower()
    modality = model.get("modality", "").lower()
    if modality and modality in SKIP_MODALITIES:
        return f"non-chat modality: {modality}"
    for prefix in SKIP_MODEL_PREFIXES:
        if model_id.startswith(prefix) or f"/{prefix}" in model_id:
            return f"non-chat prefix: {prefix}"
    return None


async def _fetch_catalog(
    gateway: str | None,
    provider: str | None,
) -> list[dict]:
    """Fetch models from the internal catalog service."""
    from src.services.models import get_cached_models

    try:
        gw = gateway or "all"
        models = (
            await get_cached_models(gw)
            if asyncio.iscoroutinefunction(get_cached_models)
            else get_cached_models(gw)
        )
        if not models:
            models = []
    except Exception as exc:
        logger.warning("Failed to fetch catalog via get_cached_models: %s", exc)
        models = []

    # Fallback: try DB catalog
    if not models:
        try:
            from src.db.models_catalog_db import get_all_catalog_models

            result = await get_all_catalog_models(limit=10000)
            models = result if isinstance(result, list) else result.get("data", [])
        except Exception as exc:
            logger.warning("Failed to fetch catalog from DB: %s", exc)
            models = []

    if provider:
        p = provider.lower()
        models = [
            m
            for m in models
            if m.get("provider_slug", "").lower() == p or m.get("source_gateway", "").lower() == p
        ]

    return models


async def _test_single_model(
    client: httpx.AsyncClient,
    model: dict,
    timeout_s: float,
    semaphore: asyncio.Semaphore,
) -> ModelTestResult:
    """Send a minimal chat completion to one model via the gateway."""
    model_id = _resolve_model_id(model)
    gateway = model.get("source_gateway", model.get("provider_slug", "unknown"))
    provider = model.get("provider_slug", gateway)

    skip_reason = _should_skip(model)
    if skip_reason:
        return ModelTestResult(
            model_id=model_id,
            gateway=gateway,
            provider=provider,
            status="skip",
            error=skip_reason,
        )

    payload = {
        "model": model_id,
        "messages": TEST_MESSAGES,
        "max_tokens": TEST_MAX_TOKENS,
        "temperature": 0,
    }

    async with semaphore:
        start = time.monotonic()
        try:
            resp = await client.post(
                "/v1/chat/completions",
                json=payload,
                timeout=timeout_s,
            )
            latency = (time.monotonic() - start) * 1000

            if resp.status_code == 200:
                body = resp.json()
                content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
                return ModelTestResult(
                    model_id=model_id,
                    gateway=gateway,
                    provider=provider,
                    status="pass",
                    status_code=200,
                    latency_ms=round(latency, 1),
                    response_preview=content[:80] if content else "(empty)",
                )
            else:
                err = ""
                try:
                    eb = resp.json()
                    err = eb.get("error", {}).get("message", eb.get("detail", ""))
                except Exception:
                    err = resp.text[:200]
                return ModelTestResult(
                    model_id=model_id,
                    gateway=gateway,
                    provider=provider,
                    status="fail",
                    status_code=resp.status_code,
                    latency_ms=round(latency, 1),
                    error=f"HTTP {resp.status_code}: {err[:150]}",
                )

        except httpx.TimeoutException:
            latency = (time.monotonic() - start) * 1000
            return ModelTestResult(
                model_id=model_id,
                gateway=gateway,
                provider=provider,
                status="timeout",
                latency_ms=round(latency, 1),
                error=f"Timed out after {timeout_s}s",
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            return ModelTestResult(
                model_id=model_id,
                gateway=gateway,
                provider=provider,
                status="error",
                latency_ms=round(latency, 1),
                error=str(exc)[:200],
            )


def _build_provider_summaries(results: list[ModelTestResult]) -> list[ProviderSummary]:
    stats: dict[str, dict[str, Any]] = {}
    for r in results:
        p = r.provider
        if p not in stats:
            stats[p] = {
                "provider": p,
                "total": 0,
                "passed": 0,
                "failed": 0,
                "timed_out": 0,
                "errored": 0,
                "skipped": 0,
                "latencies": [],
            }
        s = stats[p]
        s["total"] += 1
        if r.status == "pass":
            s["passed"] += 1
            s["latencies"].append(r.latency_ms)
        elif r.status == "fail":
            s["failed"] += 1
        elif r.status == "timeout":
            s["timed_out"] += 1
        elif r.status == "error":
            s["errored"] += 1
        elif r.status == "skip":
            s["skipped"] += 1

    summaries = []
    for s in sorted(stats.values(), key=lambda x: x["passed"], reverse=True):
        lats = s.pop("latencies")
        s["avg_latency_ms"] = round(sum(lats) / len(lats), 1) if lats else 0.0
        summaries.append(ProviderSummary(**s))
    return summaries


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/run", response_model=LiveTestReport)
async def run_live_model_test(
    gateway: str | None = Query(None, description="Filter by gateway"),
    provider: str | None = Query(None, description="Filter by provider slug"),
    limit: int = Query(50, ge=0, le=5000, description="Max models to test (default 50, max 5000)"),
    concurrency: int = Query(10, ge=1, le=20, description="Parallel requests"),
    timeout: float = Query(15.0, ge=5, le=60, description="Per-model timeout (s)"),
    admin_user: dict = Depends(require_admin),
) -> LiveTestReport:
    """
    Run a live inference test against all (or filtered) catalog models.

    Sends a minimal 5-token chat completion to each model through the gateway,
    using the admin user's API key. Returns per-provider stats and failure details.

    **Warning**: This makes real inference calls. With 1000+ models at concurrency=5,
    expect ~10-30 minutes and a small credit cost (~$0.01-0.05).
    """
    import os

    from src.config.config import Config

    # Use the admin's own API key (already authenticated)
    admin_api_key = admin_user.get("api_key", "")
    if not admin_api_key:
        raise HTTPException(status_code=400, detail="Admin user has no API key")

    # Determine base URL for self-calls
    # Railway sets PORT; Vercel has its own routing
    port = os.environ.get("PORT", "8000")
    base_url = os.environ.get(
        "BASE_URL",
        getattr(Config, "BASE_URL", None) or f"http://localhost:{port}",
    )

    logger.info(
        "Admin %s triggered live model test (gateway=%s, provider=%s, limit=%s)",
        admin_user.get("email", "?"),
        gateway,
        provider,
        limit,
    )

    # 1. Fetch catalog
    models = await _fetch_catalog(gateway, provider)
    if limit and limit > 0:
        models = models[:limit]

    if not models:
        return LiveTestReport(
            timestamp=datetime.now(UTC).isoformat(),
            total_models=0,
        )

    # 2. Run tests
    semaphore = asyncio.Semaphore(concurrency)
    headers = {"Authorization": f"Bearer {admin_api_key}"}

    start_time = time.monotonic()

    async with httpx.AsyncClient(base_url=base_url, headers=headers) as client:
        tasks = [_test_single_model(client, m, timeout, semaphore) for m in models]
        results = await asyncio.gather(*tasks)

    duration = round(time.monotonic() - start_time, 1)

    # 3. Build report
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    timed_out = sum(1 for r in results if r.status == "timeout")
    errored = sum(1 for r in results if r.status == "error")
    skipped = sum(1 for r in results if r.status == "skip")
    tested = passed + failed + timed_out + errored

    # Log status distribution for debugging
    status_dist = {}
    for r in results:
        status_dist[r.status] = status_dist.get(r.status, 0) + 1
    logger.info("Live test status distribution: %s", status_dist)

    failures = [r for r in results if r.status in ("fail", "timeout", "error")]

    report = LiveTestReport(
        timestamp=datetime.now(UTC).isoformat(),
        duration_s=duration,
        total_models=len(models),
        tested=tested,
        passed=passed,
        failed=failed,
        timed_out=timed_out,
        errored=errored,
        skipped=skipped,
        pass_rate=round(passed / tested * 100, 1) if tested else 0.0,
        by_provider=_build_provider_summaries(results),
        failures=failures[:200],  # Cap to avoid huge payloads
        results=list(results)[:500],  # Cap detailed results
    )

    logger.info(
        "Live test complete: %d/%d passed (%.1f%%) in %.1fs",
        passed,
        tested,
        report.pass_rate,
        duration,
    )

    return report


@router.get("/status")
async def get_live_test_status(
    admin_user: dict = Depends(require_admin),
) -> dict:
    """Check if the live test endpoint is available."""
    return {
        "status": "available",
        "message": "POST /admin/live-test/run to start a test sweep",
        "timestamp": datetime.now(UTC).isoformat(),
    }
