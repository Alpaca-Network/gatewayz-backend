import asyncio
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
        {"id": f"model-{i}", "provider": "provider-a", "gateway": "gateway-a"}
        for i in range(10)
    ]

    async def fake_get_models_to_check(self):
        return models

    async def fake_update_provider_metrics(self):
        return None

    async def fake_update_system_metrics(self):
        return None

    active_counts = {"gateway-a": 0}
    peak_counts = {"gateway-a": 0}
    lock = asyncio.Lock()

    async def fake_check_model_health(self, model):
        async with lock:
            active_counts[model["gateway"]] += 1
            peak_counts[model["gateway"]] = max(
                peak_counts[model["gateway"]], active_counts[model["gateway"]]
            )

        await asyncio.sleep(0.05)

        async with lock:
            active_counts[model["gateway"]] -= 1

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

    assert peak_counts["gateway-a"] <= monitor.concurrency_per_gateway
    # With more queued models than the limit, the semaphore should saturate at the configured limit
    assert peak_counts["gateway-a"] == monitor.concurrency_per_gateway
