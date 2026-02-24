"""
Model Health Monitoring Service

This service provides comprehensive monitoring of model availability, performance,
and health status across all providers and gateways.
"""

import asyncio
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from src.config import Config
from src.utils.sentry_context import capture_model_health_error

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):  # noqa: UP042
    """Health status enumeration"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    MAINTENANCE = "maintenance"


class ProviderStatus(str, Enum):  # noqa: UP042
    """Provider status enumeration"""

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"


@dataclass
class ModelHealthMetrics:
    """Health metrics for a specific model"""

    model_id: str
    provider: str
    gateway: str
    status: HealthStatus
    response_time_ms: float | None = None
    success_rate: float = 0.0
    last_checked: datetime | None = None
    last_success: datetime | None = None
    last_failure: datetime | None = None
    error_count: int = 0
    total_requests: int = 0
    avg_response_time_ms: float | None = None
    uptime_percentage: float = 0.0
    error_message: str | None = None


@dataclass
class ProviderHealthMetrics:
    """Health metrics for a provider"""

    provider: str
    gateway: str
    status: ProviderStatus
    total_models: int = 0
    healthy_models: int = 0
    degraded_models: int = 0
    unhealthy_models: int = 0
    avg_response_time_ms: float | None = None
    overall_uptime: float = 0.0
    last_checked: datetime | None = None
    error_message: str | None = None


@dataclass
class SystemHealthMetrics:
    """Overall system health metrics"""

    overall_status: HealthStatus
    total_providers: int = 0
    healthy_providers: int = 0
    degraded_providers: int = 0
    unhealthy_providers: int = 0
    total_models: int = 0
    healthy_models: int = 0
    degraded_models: int = 0
    unhealthy_models: int = 0
    system_uptime: float = 0.0
    last_updated: datetime | None = None


class ModelHealthMonitor:
    """Main health monitoring service"""

    def __init__(
        self,
        *,
        check_interval: int = 300,
        batch_size: int = 20,
        batch_interval: float = 0.0,
        fetch_chunk_size: int = 100,
    ):
        self.health_data: dict[str, ModelHealthMetrics] = {}
        self.provider_data: dict[str, ProviderHealthMetrics] = {}
        self.system_data: SystemHealthMetrics | None = None
        self.monitoring_active = False
        self.check_interval = check_interval  # seconds
        self.timeout = 30  # 30 seconds
        self.health_threshold = 0.95  # 95% success rate threshold
        self.response_time_threshold = 10000  # 10 seconds
        self.batch_size = max(1, batch_size)
        self.batch_interval = max(0.0, batch_interval)
        self.fetch_chunk_size = max(1, fetch_chunk_size)
        self._monitoring_task: asyncio.Task | None = None

        # Test payload for health checks
        self.test_payload = {
            "messages": [{"role": "user", "content": "Health check - respond with 'OK'"}],
            "max_tokens": 10,
            "temperature": 0.1,
        }

    async def start_monitoring(self, run_initial_check: bool = True):
        """Start the health monitoring service

        Args:
            run_initial_check: If True, performs an initial health check synchronously
                               before starting the background loop. This ensures system_data
                               is populated immediately rather than waiting for the first
                               check_interval (default 5 minutes).
        """
        if self.monitoring_active:
            logger.warning("Health monitoring is already active")
            return

        self.monitoring_active = True
        logger.info("Starting model health monitoring service")

        # Perform initial health check synchronously to populate system_data immediately
        # This prevents the race condition where /health/dashboard returns UNKNOWN status
        # because no health checks have completed yet
        if run_initial_check:
            try:
                logger.info("Running initial health check to populate system metrics...")
                await self._perform_initial_health_check()
                logger.info("Initial health check completed - system metrics available")
            except Exception as e:
                logger.warning(f"Initial health check failed, will retry in background: {e}")
                # Initialize with empty but valid system_data to avoid UNKNOWN status
                await self._initialize_empty_system_data()

        # Start monitoring loop for periodic checks and store task reference for cleanup
        self._monitoring_task = asyncio.create_task(self._monitoring_loop())

    async def stop_monitoring(self):
        """Stop the health monitoring service and wait for background task to complete"""
        self.monitoring_active = False
        logger.info("Stopping model health monitoring service...")

        # Cancel and await the monitoring task to ensure clean shutdown
        if self._monitoring_task is not None:
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass  # Expected when task is cancelled
            self._monitoring_task = None

        logger.info("Model health monitoring service stopped")

    async def _perform_initial_health_check(self):
        """Perform a lightweight initial health check to populate system_data.

        This is a faster version of _perform_health_checks() that:
        1. Only checks a small sample of models (first few from each gateway)
        2. Has a shorter timeout
        3. Prioritizes populating system_data over comprehensive coverage

        The full health check will run in the background loop afterward.
        """
        logger.info("Performing initial health check (lightweight)")

        # Get models but limit to a small sample for fast startup
        models_to_check = await self._get_models_to_check()

        if not models_to_check:
            logger.warning("No models available for initial health check")
            # Still initialize system_data with zeros rather than leaving it None
            await self._initialize_empty_system_data()
            return

        # Take a sample of models (max 10) for quick initial check
        sample_size = min(10, len(models_to_check))
        sample_models = models_to_check[:sample_size]

        logger.info(f"Initial check: sampling {sample_size} of {len(models_to_check)} models")

        # Check the sample models with shorter timeout
        results = await asyncio.gather(
            *(self._check_model_health(model) for model in sample_models),
            return_exceptions=True,
        )

        for model, result in zip(sample_models, results, strict=False):
            if isinstance(result, Exception):
                logger.debug(f"Initial health check failed for {model.get('id')}: {result}")
                continue
            if result:
                self._update_health_data(result)

        # Update provider and system metrics even with partial data
        await self._update_provider_metrics()
        await self._update_system_metrics()

        logger.info(
            f"Initial health check complete: {len(self.health_data)} models, "
            f"{len(self.provider_data)} providers tracked"
        )

    async def _initialize_empty_system_data(self):
        """Initialize system_data with empty/zero values.

        This is called when no models are available or initial check fails,
        to ensure system_data is not None and endpoints return valid responses
        instead of UNKNOWN status.
        """
        self.system_data = SystemHealthMetrics(
            overall_status=HealthStatus.UNKNOWN,  # No checks performed yet
            total_providers=0,
            healthy_providers=0,
            degraded_providers=0,
            unhealthy_providers=0,
            total_models=0,
            healthy_models=0,
            degraded_models=0,
            unhealthy_models=0,
            system_uptime=0.0,  # No data yet
            last_updated=datetime.now(UTC),
        )
        logger.info("Initialized empty system health data (no models checked yet)")

    async def _monitoring_loop(self):
        """Main monitoring loop"""
        while self.monitoring_active:
            try:
                await self._perform_health_checks()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}", exc_info=True)
                await asyncio.sleep(60)  # Wait 1 minute before retrying

    async def _perform_health_checks(self):
        """Perform health checks on all models"""
        logger.info("Performing health checks on all models")

        # Get all available models from different gateways
        models_to_check = await self._get_models_to_check()

        if not models_to_check:
            logger.info("No models available for health checks")
            return

        total_models = len(models_to_check)
        processed = 0

        for start in range(0, total_models, self.batch_size):
            batch = models_to_check[start : start + self.batch_size]
            results = await asyncio.gather(
                *(self._check_model_health(model) for model in batch),
                return_exceptions=True,
            )

            for model, result in zip(batch, results, strict=False):
                if isinstance(result, Exception):
                    logger.error("Health check failed for model %s: %s", model.get("id"), result)
                    continue

                if result:
                    self._update_health_data(result)

            processed += len(batch)

            if self.batch_interval and processed < total_models:
                await asyncio.sleep(self.batch_interval)

        # Update provider and system metrics
        await self._update_provider_metrics()
        await self._update_system_metrics()

        logger.info("Health checks completed. Checked %s models", total_models)

    async def _get_models_to_check(self) -> list[dict[str, Any]]:
        """Get list of models to check for health monitoring"""
        models = []

        try:
            # Import here to avoid circular imports
            from src.services.models import get_cached_models

            # Get models from different gateways
            gateways = [
                "openrouter",
                "featherless",
                "deepinfra",
                "huggingface",
                "groq",
                "fireworks",
                "together",
                "xai",
                "novita",
                "chutes",
                "aimo",
                "near",
                "fal",
                "google-vertex",
                "cerebras",
                "nebius",
                "helicone",
                "aihubmix",
                "anannas",
                "onerouter",
                "cloudflare-workers-ai",
                "vercel-ai-gateway",
                "openai",
                "anthropic",
                "clarifai",
                "alibaba",
                "simplismart",
                "modelz",
            ]

            for gateway in gateways:
                try:
                    logger.debug(f"Fetching models from {gateway}...")
                    gateway_models = get_cached_models(gateway)
                    logger.debug(
                        f"Got {len(gateway_models) if gateway_models else 0} models from {gateway}"
                    )
                    if gateway_models:
                        for chunk in self._chunk_list(gateway_models, self.fetch_chunk_size):
                            for model in chunk:
                                models.append(
                                    {
                                        "id": model.get("id"),
                                        "provider": model.get("provider_slug", "unknown"),
                                        "gateway": gateway,
                                        "name": model.get("name", model.get("id")),
                                    }
                                )
                except Exception as e:
                    logger.warning(f"Failed to get models from {gateway}: {e}")

        except Exception as e:
            logger.error(f"Failed to get models for health checking: {e}")

        logger.info(f"Total models collected for health checking: {len(models)}")
        return models

    @staticmethod
    def _chunk_list(items: list[dict[str, Any]], size: int):
        """Yield successive chunks from a list."""
        if size <= 0:
            size = len(items) or 1

        for index in range(0, len(items), size):
            yield items[index : index + size]

    async def _check_model_health(self, model: dict[str, Any]) -> ModelHealthMetrics | None:
        """Check health of a specific model"""
        model_id = model["id"]
        provider = model["provider"]
        gateway = model["gateway"]

        start_time = time.time()
        status = HealthStatus.UNKNOWN
        response_time_ms = None
        error_message = None

        try:
            # Perform a simple health check request
            health_check_result = await self._perform_model_request(model_id, gateway)

            if health_check_result["success"]:
                status = HealthStatus.HEALTHY
                response_time_ms = (time.time() - start_time) * 1000
            else:
                status = HealthStatus.UNHEALTHY
                error_message = health_check_result.get("error", "Unknown error")
                status_code = health_check_result.get("status_code")

                # Only capture specific error types to Sentry (not rate limits or expected failures)
                should_capture = self._should_capture_error(status_code, error_message)

                if should_capture:
                    # Capture non-functional model to Sentry
                    error = Exception(f"Model health check failed: {error_message}")
                    capture_model_health_error(
                        error,
                        model_id=model_id,
                        provider=provider,
                        gateway=gateway,
                        operation="health_check",
                        status="unhealthy",
                        response_time_ms=health_check_result.get("response_time"),
                        details={
                            "status_code": status_code,
                            "error_message": error_message,
                        },
                    )

        except Exception as e:
            status = HealthStatus.UNHEALTHY
            error_message = str(e)
            logger.warning(f"Health check failed for {model_id}: {e}")

            # Capture exception to Sentry
            capture_model_health_error(
                e,
                model_id=model_id,
                provider=provider,
                gateway=gateway,
                operation="health_check",
                status="unhealthy",
                details={
                    "error_message": error_message,
                },
            )

        # Create health metrics
        health_metrics = ModelHealthMetrics(
            model_id=model_id,
            provider=provider,
            gateway=gateway,
            status=status,
            response_time_ms=response_time_ms,
            last_checked=datetime.now(UTC),
            error_message=error_message,
        )

        return health_metrics

    def _should_capture_error(self, status_code: int | None, error_message: str | None) -> bool:
        """
        Determine if an error should be captured to Sentry.

        Filter out expected/transient errors like:
        - Rate limits (429)
        - Data policy restrictions (404 with policy message)
        - Invalid parameters for specific models (400 with known issues)
        - Temporary service unavailability (503)
        - Non-serverless model access errors (400)
        - Model access permission errors (404 with specific patterns)
        """
        if not status_code:
            return True  # Capture unknown errors

        # Don't capture rate limits - these are expected for free tier models
        if status_code == 429:
            return False

        # Don't capture data policy restrictions or specific model access permission errors
        if status_code == 404 and error_message:
            lower_msg = error_message.lower()
            # Data policy restrictions - user configuration issue
            if "data policy" in lower_msg:
                return False
            # Model access permission errors - only filter specific access/permission patterns
            # Don't filter generic "not found" errors which could be genuine issues
            if (
                "does not exist" in lower_msg and ("team" in lower_msg or "access" in lower_msg)
            ) or ("no access" in lower_msg and "model" in lower_msg):
                return False

        # Don't capture temporary service unavailability
        if status_code == 503 and error_message and "unavailable" in error_message.lower():
            return False

        # Don't capture known parameter validation issues for specific providers
        if status_code == 400 and error_message:
            lower_msg = error_message.lower()
            # Google Vertex AI max_output_tokens validation (already fixed in code)
            if "max_output_tokens" in lower_msg and "minimum value" in lower_msg:
                return False
            # Audio-only model requirements
            if "audio" in lower_msg and "modality" in lower_msg:
                return False
            # Non-serverless model access errors - plan/configuration issue
            if "non-serverless" in lower_msg or "unable to access" in lower_msg:
                return False

        # Don't capture authentication issues - configuration problem
        if status_code == 403 and error_message and "key" in error_message.lower():
            return False

        # Capture all other errors (including generic 500 errors and most 404s)
        return True

    def _get_api_key_for_gateway(self, gateway: str) -> str | None:
        """Get the API key for a specific gateway from configuration."""
        api_key_mapping = {
            "openrouter": Config.OPENROUTER_API_KEY,
            "featherless": Config.FEATHERLESS_API_KEY,
            "deepinfra": Config.DEEPINFRA_API_KEY,
            "huggingface": Config.HUG_API_KEY,
            "groq": Config.GROQ_API_KEY,
            "fireworks": Config.FIREWORKS_API_KEY,
            "together": Config.TOGETHER_API_KEY,
            "xai": Config.XAI_API_KEY,
            "novita": Config.NOVITA_API_KEY,
            "chutes": Config.CHUTES_API_KEY,
            "aimo": Config.AIMO_API_KEY,
            "nebius": Config.NEBIUS_API_KEY,
            "cerebras": Config.CEREBRAS_API_KEY,
        }
        return api_key_mapping.get(gateway)

    async def _perform_model_request(self, model_id: str, gateway: str) -> dict[str, Any]:
        """Perform a real test request to a model"""
        try:
            if os.getenv("TESTING", "").lower() == "true":
                return {
                    "success": True,
                    "status_code": 200,
                    "response_time": 0.0,
                    "response_data": None,
                }

            import httpx

            # Create a simple test request based on the gateway
            test_payload = {
                "model": model_id,
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 10,
                "temperature": 0.1,
            }

            # Get the appropriate endpoint URL based on gateway
            endpoint_urls = {
                "openrouter": "https://openrouter.ai/api/v1/chat/completions",
                "featherless": "https://api.featherless.ai/v1/chat/completions",
                "deepinfra": "https://api.deepinfra.com/v1/openai/chat/completions",
                "huggingface": "https://router.huggingface.co/v1/chat/completions",
                "groq": "https://api.groq.com/openai/v1/chat/completions",
                "fireworks": "https://api.fireworks.ai/inference/v1/chat/completions",
                "together": "https://api.together.xyz/v1/chat/completions",
                "xai": "https://api.x.ai/v1/chat/completions",
                "novita": "https://api.novita.ai/v3/openai/chat/completions",
            }

            url = endpoint_urls.get(gateway)
            if not url:
                return {
                    "success": False,
                    "error": f"Unknown gateway: {gateway}",
                    "status_code": 400,
                }

            # Get API key for this gateway
            api_key = self._get_api_key_for_gateway(gateway)
            if not api_key:
                return {
                    "success": False,
                    "error": f"No API key configured for gateway: {gateway}",
                    "status_code": 401,
                }

            # Set up headers based on gateway with authentication
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "HealthMonitor/1.0",
                "Authorization": f"Bearer {api_key}",
            }

            # HuggingFace now uses OpenAI-compatible format via router.huggingface.co
            # No special payload format needed - the default OpenAI format works

            # Perform the actual HTTP request
            async with httpx.AsyncClient(timeout=30.0) as client:
                start_time = time.time()

                try:
                    response = await client.post(url, headers=headers, json=test_payload)

                    response_time = (time.time() - start_time) * 1000

                    if response.status_code == 200:
                        return {
                            "success": True,
                            "response_time": response_time,
                            "status_code": response.status_code,
                            "response_data": response.json() if response.content else None,
                        }
                    else:
                        return {
                            "success": False,
                            "error": f"HTTP {response.status_code}: {response.text[:200]}",
                            "status_code": response.status_code,
                            "response_time": response_time,
                        }

                except httpx.TimeoutException:
                    return {
                        "success": False,
                        "error": "Request timeout",
                        "status_code": 408,
                        "response_time": (time.time() - start_time) * 1000,
                    }
                except httpx.RequestError as e:
                    return {
                        "success": False,
                        "error": f"Request error: {str(e)}",
                        "status_code": 500,
                        "response_time": (time.time() - start_time) * 1000,
                    }

        except Exception as e:
            logger.error(f"Health check request failed for {model_id} via {gateway}: {e}")
            return {"success": False, "error": str(e), "status_code": 500}

    def _update_health_data(self, health_metrics: ModelHealthMetrics):
        """Update health data for a model"""
        if not health_metrics:
            return

        model_key = f"{health_metrics.gateway}:{health_metrics.model_id}"

        # Update or create health data
        if model_key in self.health_data:
            existing = self.health_data[model_key]

            # Update metrics
            existing.status = health_metrics.status
            existing.response_time_ms = health_metrics.response_time_ms
            existing.last_checked = health_metrics.last_checked
            existing.error_message = health_metrics.error_message

            # Update success/failure tracking
            if health_metrics.status == HealthStatus.HEALTHY:
                existing.last_success = health_metrics.last_checked
                existing.total_requests += 1
            else:
                existing.last_failure = health_metrics.last_checked
                existing.error_count += 1
                existing.total_requests += 1

            # Calculate success rate
            if existing.total_requests > 0:
                existing.success_rate = (
                    existing.total_requests - existing.error_count
                ) / existing.total_requests

            # Calculate uptime percentage
            existing.uptime_percentage = existing.success_rate * 100

            # Update average response time
            if health_metrics.response_time_ms:
                if existing.avg_response_time_ms:
                    existing.avg_response_time_ms = (
                        existing.avg_response_time_ms + health_metrics.response_time_ms
                    ) / 2
                else:
                    existing.avg_response_time_ms = health_metrics.response_time_ms

        else:
            # Create new health data
            self.health_data[model_key] = health_metrics

    async def _update_provider_metrics(self):
        """Update provider-level health metrics"""
        provider_stats = {}

        for _model_key, health_data in self.health_data.items():
            provider = health_data.provider
            gateway = health_data.gateway
            provider_key = f"{gateway}:{provider}"

            if provider_key not in provider_stats:
                provider_stats[provider_key] = {
                    "provider": provider,
                    "gateway": gateway,
                    "total_models": 0,
                    "healthy_models": 0,
                    "degraded_models": 0,
                    "unhealthy_models": 0,
                    "response_times": [],
                    "success_rates": [],
                }

            stats = provider_stats[provider_key]
            stats["total_models"] += 1

            if health_data.status == HealthStatus.HEALTHY:
                stats["healthy_models"] += 1
            elif health_data.status == HealthStatus.DEGRADED:
                stats["degraded_models"] += 1
            else:
                stats["unhealthy_models"] += 1

            if health_data.response_time_ms:
                stats["response_times"].append(health_data.response_time_ms)

            stats["success_rates"].append(health_data.success_rate)

        # Create provider health metrics
        for provider_key, stats in provider_stats.items():
            # Calculate overall status
            if stats["unhealthy_models"] == 0:
                status = ProviderStatus.ONLINE
            elif stats["unhealthy_models"] < stats["total_models"] * 0.5:
                status = ProviderStatus.DEGRADED
            else:
                status = ProviderStatus.OFFLINE

            # Calculate average response time
            avg_response_time = None
            if stats["response_times"]:
                avg_response_time = sum(stats["response_times"]) / len(stats["response_times"])

            # Calculate overall uptime
            overall_uptime = 0.0
            if stats["success_rates"]:
                overall_uptime = sum(stats["success_rates"]) / len(stats["success_rates"]) * 100

            provider_metrics = ProviderHealthMetrics(
                provider=stats["provider"],
                gateway=stats["gateway"],
                status=status,
                total_models=stats["total_models"],
                healthy_models=stats["healthy_models"],
                degraded_models=stats["degraded_models"],
                unhealthy_models=stats["unhealthy_models"],
                avg_response_time_ms=avg_response_time,
                overall_uptime=overall_uptime,
                last_checked=datetime.now(UTC),
            )

            self.provider_data[provider_key] = provider_metrics

    async def _update_system_metrics(self):
        """Update system-level health metrics"""
        total_providers = len(self.provider_data)
        healthy_providers = sum(
            1 for p in self.provider_data.values() if p.status == ProviderStatus.ONLINE
        )
        degraded_providers = sum(
            1 for p in self.provider_data.values() if p.status == ProviderStatus.DEGRADED
        )
        unhealthy_providers = sum(
            1 for p in self.provider_data.values() if p.status == ProviderStatus.OFFLINE
        )

        total_models = len(self.health_data)
        healthy_models = sum(
            1 for m in self.health_data.values() if m.status == HealthStatus.HEALTHY
        )
        degraded_models = sum(
            1 for m in self.health_data.values() if m.status == HealthStatus.DEGRADED
        )
        unhealthy_models = sum(
            1 for m in self.health_data.values() if m.status == HealthStatus.UNHEALTHY
        )

        # Calculate overall system status
        if unhealthy_providers == 0:
            overall_status = HealthStatus.HEALTHY
        elif unhealthy_providers < total_providers * 0.5:
            overall_status = HealthStatus.DEGRADED
        else:
            overall_status = HealthStatus.UNHEALTHY

        # Calculate system uptime
        system_uptime = 0.0
        if total_models > 0:
            system_uptime = (healthy_models / total_models) * 100

        self.system_data = SystemHealthMetrics(
            overall_status=overall_status,
            total_providers=total_providers,
            healthy_providers=healthy_providers,
            degraded_providers=degraded_providers,
            unhealthy_providers=unhealthy_providers,
            total_models=total_models,
            healthy_models=healthy_models,
            degraded_models=degraded_models,
            unhealthy_models=unhealthy_models,
            system_uptime=system_uptime,
            last_updated=datetime.now(UTC),
        )

        # Publish health data to Redis cache for consumption by main API
        await self._publish_health_to_cache()

    async def _publish_health_to_cache(self):
        """Publish health data to Redis cache for consumption by main API"""
        try:
            from src.config.redis_config import get_redis_config
            from src.services.simple_health_cache import simple_health_cache

            # Debug log to check Redis connection
            redis_config = get_redis_config()
            redis_host = redis_config.redis_host
            logger.info(
                f"Attempting to publish health data to Redis from simple monitor (host: {redis_host})"
            )

            # Cache system health
            if self.system_data:
                simple_health_cache.cache_system_health(asdict(self.system_data))
                logger.debug("Published system health to Redis cache")

            # Cache providers health
            providers_data = [asdict(p) for p in self.provider_data.values()]
            if providers_data:
                simple_health_cache.cache_providers_health(providers_data)
                logger.debug(f"Published {len(providers_data)} providers health to Redis cache")

            # Cache models health
            models_data = [asdict(m) for m in self.health_data.values()]
            if models_data:
                simple_health_cache.cache_models_health(models_data)
                logger.debug(f"Published {len(models_data)} models health to Redis cache")

            # Cache gateways health
            # Build gateway health from provider data
            gateway_health = {}
            for provider_data in self.provider_data.values():
                gateway_name = provider_data.gateway
                if gateway_name not in gateway_health:
                    gateway_health[gateway_name] = {
                        "healthy": False,
                        "status": "offline",
                        "latency_ms": 0,
                        "available": False,
                        "last_check": None,
                        "error": None,
                    }

                if provider_data.status == "online":
                    gateway_health[gateway_name]["healthy"] = True
                    gateway_health[gateway_name]["status"] = "online"
                    gateway_health[gateway_name]["available"] = True
                    # Extract latency from avg_response_time (format: "123ms")
                    avg_response = (
                        provider_data.avg_response_time.replace("ms", "")
                        if provider_data.avg_response_time
                        else "0"
                    )
                    try:
                        gateway_health[gateway_name]["latency_ms"] = int(avg_response)
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Failed to parse latency for {gateway_name}: {e}")
                        gateway_health[gateway_name]["latency_ms"] = 0
                    gateway_health[gateway_name]["last_check"] = provider_data.last_checked
                elif (
                    provider_data.status == "degraded"
                    and gateway_health[gateway_name]["status"] == "offline"
                ):
                    gateway_health[gateway_name]["status"] = "degraded"

            # Add all gateways from GATEWAY_CONFIG that aren't tracked
            # Check cache and API key status to determine appropriate status
            try:
                from src.services.gateway_health_service import GATEWAY_CONFIG

                for gateway_name, gateway_config in GATEWAY_CONFIG.items():
                    if gateway_name not in gateway_health:
                        # Check if API key is configured (static_catalog is valid for some gateways)
                        api_key = gateway_config.get("api_key")
                        url = gateway_config.get("url")
                        # Gateway is considered configured if it has an API key OR doesn't need one (url is None)
                        has_api_key = api_key is not None and api_key != ""
                        needs_api_key = (
                            url is not None
                        )  # Gateways with url=None use static catalogs

                        # Check if cache has models
                        cache = gateway_config.get("cache", {})
                        cache_data = cache.get("data") if cache else None
                        has_cached_models = (
                            cache_data is not None and len(cache_data) > 0 if cache_data else False
                        )
                        model_count = len(cache_data) if has_cached_models else 0

                        # Determine status and error based on configuration state
                        if needs_api_key and not has_api_key:
                            status = "unconfigured"
                            error_msg = f"API key not configured. Set {gateway_config.get('api_key_env', 'API_KEY')} environment variable."
                            is_healthy = False
                        elif has_cached_models:
                            # Has models in cache - gateway is available
                            status = "healthy"
                            error_msg = None
                            is_healthy = True
                        elif not needs_api_key:
                            # Static catalog gateway without cached models yet
                            status = "pending"
                            error_msg = "Static catalog not yet loaded. Models will appear on first request."
                            is_healthy = False
                        else:
                            # Has API key but no models in cache - needs sync
                            status = "pending"
                            error_msg = (
                                "Models not yet synced. They will appear when first accessed."
                            )
                            is_healthy = False

                        gateway_health[gateway_name] = {
                            "healthy": is_healthy,
                            "status": status,
                            "latency_ms": None,
                            "available": is_healthy,
                            "last_check": datetime.now(UTC).isoformat(),
                            "error": error_msg,
                            "total_models": model_count,
                            "configured": has_api_key or not needs_api_key,
                        }
            except Exception as e:
                logger.warning(f"Failed to add unconfigured gateways: {e}")

            if gateway_health:
                simple_health_cache.cache_gateways_health(gateway_health)
                logger.debug(f"Published {len(gateway_health)} gateways health to Redis cache")

            logger.info("Health data published to Redis cache successfully")

        except Exception as e:
            logger.warning(f"Failed to publish health data to Redis cache: {e}")
            # Don't fail the health check if cache publish fails

    def get_model_health(self, model_id: str, gateway: str = None) -> ModelHealthMetrics | None:
        """Get health metrics for a specific model"""
        if gateway:
            model_key = f"{gateway}:{model_id}"
            return self.health_data.get(model_key)
        else:
            # Search across all gateways
            for _key, health_data in self.health_data.items():
                if health_data.model_id == model_id:
                    return health_data
            return None

    def get_provider_health(
        self, provider: str, gateway: str = None
    ) -> ProviderHealthMetrics | None:
        """Get health metrics for a specific provider"""
        if gateway:
            provider_key = f"{gateway}:{provider}"
            return self.provider_data.get(provider_key)
        else:
            # Search across all gateways
            for _key, provider_data in self.provider_data.items():
                if provider_data.provider == provider:
                    return provider_data
            return None

    def get_system_health(self) -> SystemHealthMetrics | None:
        """Get overall system health metrics"""
        return self.system_data

    def get_all_models_health(self, gateway: str = None) -> list[ModelHealthMetrics]:
        """Get health metrics for all models"""
        if gateway:
            return [h for h in self.health_data.values() if h.gateway == gateway]
        else:
            return list(self.health_data.values())

    def get_all_providers_health(self, gateway: str = None) -> list[ProviderHealthMetrics]:
        """Get health metrics for all providers"""
        if gateway:
            return [p for p in self.provider_data.values() if p.gateway == gateway]
        else:
            return list(self.provider_data.values())

    def get_health_summary(self) -> dict[str, Any]:
        """Get a comprehensive health summary"""
        return {
            "system": asdict(self.system_data) if self.system_data else None,
            "providers": [asdict(p) for p in self.provider_data.values()],
            "models": [asdict(m) for m in self.health_data.values()],
            "monitoring_active": self.monitoring_active,
            "last_check": datetime.now(UTC).isoformat(),
        }


# Global health monitor instance with rate-limited configuration
# HEALTH FIX #1094: Reduced batch size and increased interval to prevent rate limits
# This prevents hitting provider rate limits by:
# - Checking models in small batches (10 at a time) - REDUCED from 20
# - Adding 30-second delay between batches (2 batches/min max) - INCREASED from 15s
# - Running checks every 5 minutes by default
# This is especially important for OpenRouter's 4 model switches/minute limit
health_monitor = ModelHealthMonitor(
    check_interval=300,  # Check every 5 minutes
    batch_size=10,  # CHANGED: 20 → 10 (process 10 models per batch)
    batch_interval=30.0,  # CHANGED: 15s → 30s (30 second delay between batches)
    fetch_chunk_size=100,  # Fetch models in chunks of 100
)
