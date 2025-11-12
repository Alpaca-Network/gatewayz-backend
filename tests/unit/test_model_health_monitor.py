import asyncio
from collections import defaultdict
from datetime import datetime, timezone

import pytest

from src.services.model_health_monitor import (
    HealthStatus,
    ModelHealthMetrics,
    ModelHealthMonitor,
)


@pytest.mark.asyncio
async def test_health_checks_respect_gateway_concurrency(monkeypatch):
    monitor = ModelHealthMonitor()
    monitor.concurrency_per_gateway = 2
    monitor.timeout = 5

    models = [
        {
            "id": f"model-a-{i}",
            "provider": "provider-a",
            "gateway": "gateway-a",
        }
        for i in range(10)
    ] + [
        {
            "id": f"model-b-{i}",
            "provider": "provider-b",
            "gateway": "gateway-a",
        }
        for i in range(10)
    ]

    async def fake_get_models_to_check(self):
        return models

    async def fake_update_provider_metrics(self):
        return None

    async def fake_update_system_metrics(self):
        return None

    active_counts = defaultdict(lambda: defaultdict(int))
    peak_counts = defaultdict(lambda: defaultdict(int))
    concurrent_providers = set()
    lock = asyncio.Lock()

    async def fake_check_model_health(self, model):
        gateway = model["gateway"]
        provider = model["provider"]

        async with lock:
            active_counts[gateway][provider] += 1
            peak_counts[gateway][provider] = max(
                peak_counts[gateway][provider], active_counts[gateway][provider]
            )
            if (
                active_counts[gateway]["provider-a"] > 0
                and active_counts[gateway]["provider-b"] > 0
            ):
                concurrent_providers.add(gateway)

        await asyncio.sleep(0.05)

        async with lock:
            active_counts[gateway][provider] -= 1

        return ModelHealthMetrics(
            model_id=model["id"],
            provider=model["provider"],
            gateway=model["gateway"],
            status=HealthStatus.HEALTHY,
            last_checked=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(
        ModelHealthMonitor, "_get_models_to_check", fake_get_models_to_check
    )
    monkeypatch.setattr(ModelHealthMonitor, "_update_provider_metrics", fake_update_provider_metrics)
    monkeypatch.setattr(ModelHealthMonitor, "_update_system_metrics", fake_update_system_metrics)
    monkeypatch.setattr(ModelHealthMonitor, "_check_model_health", fake_check_model_health)

    await monitor._perform_health_checks()

    for provider in ("provider-a", "provider-b"):
        assert (
            peak_counts["gateway-a"][provider] <= monitor.concurrency_per_gateway
        )
        # With more queued models than the limit, the semaphore should saturate at the configured limit
        assert (
            peak_counts["gateway-a"][provider] == monitor.concurrency_per_gateway
        )

    # Multiple providers on the same gateway should still run in parallel when capacity is available
    assert "gateway-a" in concurrent_providers


@pytest.mark.asyncio
async def test_concurrent_health_runs_share_gateway_provider_limits(monkeypatch):
    monitor = ModelHealthMonitor()
    monitor.concurrency_per_gateway = 1
    monitor.timeout = 5

    models = [
        {
            "id": "model-shared",
            "provider": "provider-a",
            "gateway": "gateway-a",
        }
    ]

    async def fake_get_models_to_check(self):
        return models

    async def fake_update_provider_metrics(self):
        return None

    async def fake_update_system_metrics(self):
        return None

    active_counts = 0
    peak_active = 0
    lock = asyncio.Lock()

    async def fake_check_model_health(self, model):
        nonlocal active_counts, peak_active
        async with lock:
            active_counts += 1
            peak_active = max(peak_active, active_counts)

        await asyncio.sleep(0.05)

        async with lock:
            active_counts -= 1

        return ModelHealthMetrics(
            model_id=model["id"],
            provider=model["provider"],
            gateway=model["gateway"],
            status=HealthStatus.HEALTHY,
            last_checked=datetime.now(timezone.utc),
        )

    monkeypatch.setattr(
        ModelHealthMonitor, "_get_models_to_check", fake_get_models_to_check
    )
    monkeypatch.setattr(ModelHealthMonitor, "_update_provider_metrics", fake_update_provider_metrics)
    monkeypatch.setattr(ModelHealthMonitor, "_update_system_metrics", fake_update_system_metrics)
    monkeypatch.setattr(ModelHealthMonitor, "_check_model_health", fake_check_model_health)

    await asyncio.gather(
        monitor._perform_health_checks(), monitor._perform_health_checks()
    )

    assert peak_active <= monitor.concurrency_per_gateway
