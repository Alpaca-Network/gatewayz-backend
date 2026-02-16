"""
Intelligent Health Monitoring Service for 10,000+ Models

This service implements a scalable, tiered approach to health monitoring:
- Tier 1 (Critical): Top 5% models by usage - checked every 5 minutes
- Tier 2 (Popular): Next 20% models - checked every 30 minutes
- Tier 3 (Standard): Remaining 75% - checked every 2-4 hours
- Tier 4 (On-Demand): Checked when actually requested by users

Features:
- Database-backed persistence
- Intelligent scheduling with priority queues
- Circuit breaker pattern
- Distributed coordination via Redis
- Historical tracking and analytics
- Incident management
- Automatic failover
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MonitoringTier(str, Enum):
    """Model monitoring tiers"""

    CRITICAL = "critical"  # Top 5% - every 5 minutes
    POPULAR = "popular"  # Next 20% - every 30 minutes
    STANDARD = "standard"  # Remaining - every 2 hours
    ON_DEMAND = "on_demand"  # Only when requested


class HealthCheckStatus(str, Enum):
    """Health check result status"""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"


class CircuitBreakerState(str, Enum):
    """Circuit breaker states"""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, block requests
    HALF_OPEN = "half_open"  # Testing recovery


class IncidentSeverity(str, Enum):
    """Incident severity levels"""

    CRITICAL = "critical"  # Complete outage
    HIGH = "high"  # Severe degradation
    MEDIUM = "medium"  # Partial issues
    LOW = "low"  # Minor issues


@dataclass
class HealthCheckResult:
    """Result of a health check"""

    provider: str
    model: str
    gateway: str
    status: HealthCheckStatus
    response_time_ms: float | None
    error_message: str | None
    http_status_code: int | None
    checked_at: datetime
    metadata: dict[str, Any] | None = None


@dataclass
class ModelHealthConfig:
    """Configuration for a model's health monitoring"""

    provider: str
    model: str
    gateway: str
    tier: MonitoringTier
    check_interval_seconds: int
    next_check_at: datetime
    is_enabled: bool
    priority_score: float


class IntelligentHealthMonitor:
    """
    Scalable health monitoring service for 10,000+ models

    Uses intelligent scheduling, tiered monitoring, and database persistence
    to efficiently monitor large numbers of models.
    """

    def __init__(
        self,
        batch_size: int = 50,
        max_concurrent_checks: int = 20,
        redis_coordination: bool = True,
    ):
        self.batch_size = batch_size
        self.max_concurrent_checks = max_concurrent_checks
        self.redis_coordination = redis_coordination
        self.monitoring_active = False
        self._worker_id = None
        self._semaphore = asyncio.Semaphore(max_concurrent_checks)
        self._monitoring_tasks: list[asyncio.Task] = []

        # Configuration for each tier
        # HEALTH FIX #1094: Increased timeouts to reduce false timeout failures
        # Some models have cold start delays (serverless) and network latency
        self.tier_config = {
            MonitoringTier.CRITICAL: {
                "interval_seconds": 300,  # 5 minutes
                "timeout_seconds": 30,  # CHANGED: 15 → 30 seconds
                "max_tokens": 10,
            },
            MonitoringTier.POPULAR: {
                "interval_seconds": 1800,  # 30 minutes
                "timeout_seconds": 45,  # CHANGED: 20 → 45 seconds
                "max_tokens": 10,
            },
            MonitoringTier.STANDARD: {
                "interval_seconds": 7200,  # 2 hours
                "timeout_seconds": 60,  # CHANGED: 30 → 60 seconds
                "max_tokens": 5,
            },
            MonitoringTier.ON_DEMAND: {
                "interval_seconds": 14400,  # 4 hours
                "timeout_seconds": 60,  # CHANGED: 30 → 60 seconds
                "max_tokens": 5,
            },
        }

    async def start_monitoring(self):
        """Start the intelligent health monitoring service"""
        if self.monitoring_active:
            logger.warning("Intelligent health monitoring is already active")
            return

        self.monitoring_active = True
        self._worker_id = f"worker-{id(self)}-{int(time.time())}"

        logger.info(f"Starting intelligent health monitoring service (worker: {self._worker_id})")

        # Start background tasks and store references for cleanup
        self._monitoring_tasks = [
            asyncio.create_task(self._monitoring_loop()),
            asyncio.create_task(self._tier_update_loop()),
            asyncio.create_task(self._aggregate_metrics_loop()),
            asyncio.create_task(self._incident_resolution_loop()),
        ]

    async def stop_monitoring(self):
        """Stop the health monitoring service and wait for all background tasks to complete"""
        self.monitoring_active = False
        logger.info(f"Stopping intelligent health monitoring service (worker: {self._worker_id})...")

        # Cancel and await all monitoring tasks to ensure clean shutdown
        for task in self._monitoring_tasks:
            task.cancel()

        # Wait for all tasks to complete cancellation
        if self._monitoring_tasks:
            await asyncio.gather(*self._monitoring_tasks, return_exceptions=True)
            self._monitoring_tasks = []

        logger.info(f"Intelligent health monitoring service stopped (worker: {self._worker_id})")

    async def _monitoring_loop(self):
        """Main monitoring loop with intelligent scheduling"""
        while self.monitoring_active:
            try:
                # Get models that need checking
                models_to_check = await self._get_models_for_checking()

                if not models_to_check:
                    logger.debug("No models need checking at this time")
                    # Still publish cached health data to keep cache fresh
                    await self._publish_health_to_cache()
                    await asyncio.sleep(60)
                    continue

                logger.info(f"Found {len(models_to_check)} models to check")

                # Process in batches
                for i in range(0, len(models_to_check), self.batch_size):
                    batch = models_to_check[i : i + self.batch_size]

                    # Check if we should continue
                    if not self.monitoring_active:
                        break

                    # Perform health checks concurrently with semaphore limiting
                    results = await asyncio.gather(
                        *[self._check_model_health_with_limit(model) for model in batch],
                        return_exceptions=True,
                    )

                    # Process results
                    for model, result in zip(batch, results, strict=False):
                        if isinstance(result, Exception):
                            logger.error(f"Health check failed for {model['model']}: {result}")
                            continue

                        if result:
                            await self._process_health_check_result(result)

                    # Small delay between batches to avoid overwhelming the system
                    await asyncio.sleep(1)

                # Publish health data to Redis cache for main API consumption
                await self._publish_health_to_cache()

                # Sleep before next iteration
                await asyncio.sleep(30)

            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def _check_model_health_with_limit(self, model: dict[str, Any]) -> HealthCheckResult | None:
        """Check model health with concurrency limiting"""
        async with self._semaphore:
            return await self._check_model_health(model)

    async def _get_models_for_checking(self) -> list[dict[str, Any]]:
        """
        Get list of models that need health checking

        Uses priority-based scheduling with Redis coordination to avoid
        duplicate checks in distributed deployments.
        """
        try:
            from src.config.supabase_config import supabase

            # Query models that are due for checking, ordered by priority
            response = (
                supabase.table("model_health_tracking")
                .select("*")
                .eq("is_enabled", True)
                .lte("next_check_at", datetime.now(timezone.utc).isoformat())
                .order("priority_score", desc=True)
                .order("next_check_at", desc=False)
                .limit(self.batch_size * 2)  # Get more than we need for filtering
                .execute()
            )

            models = response.data or []

            if self.redis_coordination:
                # Filter out models being checked by other workers
                models = await self._filter_with_redis_locks(models)

            return models[:self.batch_size]

        except Exception as e:
            logger.error(f"Failed to get models for checking: {e}")
            return []

    async def _filter_with_redis_locks(
        self, models: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Filter models using Redis locks to coordinate across workers

        Each worker tries to acquire a lock before checking a model.
        This prevents duplicate checks in distributed deployments.
        """
        try:
            from src.config.redis_config import get_redis_client

            redis_client = get_redis_client()
            if not redis_client:
                return models

            filtered_models = []
            for model in models:
                lock_key = f"health_check_lock:{model['provider']}:{model['model']}:{model['gateway']}"

                # Try to acquire lock (60 second expiry)
                # Note: redis_client is synchronous, so we use asyncio.to_thread
                acquired = await asyncio.to_thread(
                    redis_client.set, lock_key, self._worker_id, ex=60, nx=True
                )

                if acquired:
                    filtered_models.append(model)

                if len(filtered_models) >= self.batch_size:
                    break

            return filtered_models

        except Exception as e:
            logger.warning(f"Redis coordination failed, proceeding without locks: {e}")
            return models

    async def _check_model_health(self, model: dict[str, Any]) -> HealthCheckResult | None:
        """
        Perform health check for a specific model

        Sends a minimal test request to the model and records the result.
        """
        provider = model["provider"]
        model_id = model["model"]
        gateway = model["gateway"]
        # Handle None values from database - use "standard" as fallback
        tier_value = model.get("monitoring_tier") or "standard"
        tier = MonitoringTier(tier_value)

        tier_settings = self.tier_config[tier]
        timeout = tier_settings["timeout_seconds"]
        max_tokens = tier_settings["max_tokens"]

        start_time = time.time()
        status = HealthCheckStatus.ERROR
        error_message = None
        http_status_code = None
        response_time_ms = None

        try:
            # Build test payload
            test_payload = {
                "model": model_id,
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": max_tokens,
                "temperature": 0.1,
            }

            # Get endpoint URL for gateway
            endpoint_url = self._get_gateway_endpoint(gateway)
            if not endpoint_url:
                return None

            # Get authentication headers
            headers = await self._get_auth_headers(gateway)
            headers["Content-Type"] = "application/json"
            headers["User-Agent"] = "GatewayzHealthMonitor/2.0"

            # Perform the request
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(endpoint_url, headers=headers, json=test_payload)

                response_time_ms = (time.time() - start_time) * 1000
                http_status_code = response.status_code

                if response.status_code == 200:
                    status = HealthCheckStatus.SUCCESS
                elif response.status_code == 429:
                    status = HealthCheckStatus.RATE_LIMITED
                    error_message = "Rate limit exceeded"
                elif response.status_code == 401 or response.status_code == 403:
                    status = HealthCheckStatus.UNAUTHORIZED
                    error_message = "Authentication failed"
                elif response.status_code == 404:
                    status = HealthCheckStatus.NOT_FOUND
                    error_message = "Model not found"
                else:
                    status = HealthCheckStatus.ERROR
                    error_message = f"HTTP {response.status_code}: {response.text[:200]}"

        except httpx.TimeoutException:
            # TRANSIENT FAILURE FIX: Classify timeout errors more granularly
            status = HealthCheckStatus.TIMEOUT
            error_message = f"Request timeout after {timeout}s"
            response_time_ms = timeout * 1000
            # Note: Timeouts are often transient (cold starts, network congestion)
            # Don't immediately mark model as unhealthy on first timeout
        except httpx.ConnectError as e:
            # TRANSIENT FAILURE FIX: Connection errors are usually transient
            status = HealthCheckStatus.ERROR
            error_message = f"Connection error (transient): {str(e)[:100]}"
            response_time_ms = (time.time() - start_time) * 1000
            logger.debug(f"Transient connection error for {model_id}: {e}")
        except httpx.RequestError as e:
            # TRANSIENT FAILURE FIX: Distinguish between transient and persistent errors
            error_str = str(e).lower()
            is_transient = any(
                pattern in error_str
                for pattern in [
                    "connection",
                    "timeout",
                    "network",
                    "reset",
                    "broken pipe",
                    "503",
                    "502",
                    "504",
                ]
            )

            status = HealthCheckStatus.ERROR
            error_type = "transient" if is_transient else "persistent"
            error_message = f"Request error ({error_type}): {str(e)[:100]}"
            response_time_ms = (time.time() - start_time) * 1000

            if is_transient:
                logger.debug(f"Transient request error for {model_id}: {e}")
            else:
                logger.warning(f"Persistent request error for {model_id}: {e}")
        except Exception as e:
            # TRANSIENT FAILURE FIX: Handle unexpected errors gracefully
            status = HealthCheckStatus.ERROR
            error_message = f"Unexpected error: {str(e)[:100]}"
            response_time_ms = (time.time() - start_time) * 1000
            logger.error(f"Unexpected error checking {model_id}: {type(e).__name__}: {e}")

        return HealthCheckResult(
            provider=provider,
            model=model_id,
            gateway=gateway,
            status=status,
            response_time_ms=response_time_ms,
            error_message=error_message,
            http_status_code=http_status_code,
            checked_at=datetime.now(timezone.utc),
        )

    def _get_gateway_endpoint(self, gateway: str) -> str | None:
        """Get the API endpoint URL for a gateway"""
        endpoints = {
            "openrouter": "https://openrouter.ai/api/v1/chat/completions",
            "featherless": "https://api.featherless.ai/v1/chat/completions",
            "deepinfra": "https://api.deepinfra.com/v1/openai/chat/completions",
            "groq": "https://api.groq.com/openai/v1/chat/completions",
            "fireworks": "https://api.fireworks.ai/inference/v1/chat/completions",
            "together": "https://api.together.xyz/v1/chat/completions",
            "xai": "https://api.x.ai/v1/chat/completions",
            "novita": "https://api.novita.ai/v3/openai/chat/completions",
            "cerebras": "https://api.cerebras.ai/v1/chat/completions",
            "huggingface": None,  # Different per model
            "portkey": "https://api.portkey.ai/v1/chat/completions",
        }
        return endpoints.get(gateway)

    async def _get_auth_headers(self, gateway: str) -> dict[str, str]:
        """Get authentication headers for a gateway"""
        from src.config import Config

        headers = {}

        # Map gateways to their API keys (using getattr for safety)
        key_mapping = {
            "openrouter": getattr(Config, "OPENROUTER_API_KEY", None),
            "featherless": getattr(Config, "FEATHERLESS_API_KEY", None),
            "deepinfra": getattr(Config, "DEEPINFRA_API_KEY", None),
            "groq": getattr(Config, "GROQ_API_KEY", None),
            "fireworks": getattr(Config, "FIREWORKS_API_KEY", None),
            "together": getattr(Config, "TOGETHER_API_KEY", None),
            "xai": getattr(Config, "XAI_API_KEY", None),
            "cerebras": getattr(Config, "CEREBRAS_API_KEY", None),
        }

        api_key = key_mapping.get(gateway)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        return headers

    async def _process_health_check_result(self, result: HealthCheckResult):
        """
        Process health check result and update database

        Updates:
        - model_health_tracking table
        - model_health_history table
        - Creates/updates incidents
        - Updates circuit breaker state
        - Schedules next check
        """
        try:
            from src.config.supabase_config import supabase

            is_success = result.status == HealthCheckStatus.SUCCESS

            # TRANSIENT FAILURE FIX: Get current health data with improved retry logic
            current = None
            max_retries = 3  # Increased from 2 to 3
            retry_delays = [0.5, 1.0, 2.0]  # Exponential backoff

            for attempt in range(max_retries):
                try:
                    current = (
                        supabase.table("model_health_tracking")
                        .select("*")
                        .eq("provider", result.provider)
                        .eq("model", result.model)
                        .maybe_single()
                        .execute()
                    )
                    break  # Success, exit retry loop
                except Exception as query_error:
                    # TRANSIENT FAILURE FIX: Check if error is transient before retrying
                    error_str = str(query_error).lower()
                    is_transient = any(
                        pattern in error_str
                        for pattern in [
                            "timeout",
                            "connection",
                            "network",
                            "503",
                            "502",
                            "unavailable",
                        ]
                    )

                    if is_transient and attempt < max_retries - 1:
                        delay = retry_delays[attempt] if attempt < len(retry_delays) else 2.0
                        logger.debug(
                            f"Transient error querying health tracking for {result.model} "
                            f"(attempt {attempt + 1}/{max_retries}), retrying in {delay}s..."
                        )
                        await asyncio.sleep(delay)
                        continue

                    # Non-transient error or final retry
                    if attempt == max_retries - 1:
                        logger.debug(
                            f"Health tracking query failed for {result.model} after {max_retries} attempts: "
                            f"{type(query_error).__name__}: {str(query_error)[:100]}"
                        )
                    return

            # Handle case where response is None (e.g., Supabase client not initialized or table doesn't exist)
            if current is None:
                # Log at debug level - this happens frequently during initial setup
                # or when model_health_tracking table is not configured
                logger.debug(
                    f"Supabase query returned None for health tracking on {result.model}. "
                    "This may indicate the model_health_tracking table is not configured."
                )
                return

            current_data = current.data if current.data else {}

            # Calculate new values
            call_count = current_data.get("call_count", 0) + 1
            success_count = (
                current_data.get("success_count", 0) + 1 if is_success else current_data.get("success_count", 0)
            )
            error_count = (
                current_data.get("error_count", 0) if is_success else current_data.get("error_count", 0) + 1
            )

            consecutive_failures = 0 if is_success else current_data.get("consecutive_failures", 0) + 1
            consecutive_successes = (
                current_data.get("consecutive_successes", 0) + 1 if is_success else 0
            )

            # Update average response time
            current_avg = current_data.get("average_response_time_ms", 0) or 0
            if result.response_time_ms:
                new_avg = ((current_avg * (call_count - 1)) + result.response_time_ms) / call_count
            else:
                new_avg = current_avg

            # Calculate circuit breaker state
            circuit_breaker_state = self._calculate_circuit_breaker_state(
                current_data.get("circuit_breaker_state", "closed"),
                consecutive_failures,
                consecutive_successes,
            )

            # Calculate next check time based on tier and current state
            tier = MonitoringTier(current_data.get("monitoring_tier", "standard"))
            interval = self.tier_config[tier]["interval_seconds"]

            # If failing, check more frequently
            if not is_success and consecutive_failures > 1:
                interval = min(interval, 300)  # Max 5 minutes for failing models

            next_check_at = datetime.now(timezone.utc) + timedelta(seconds=interval)

            # Preserve existing uptime percentages - they are calculated from actual history
            # by the _aggregate_hourly_metrics background task. Only set initial defaults
            # for new models that don't have values yet.
            existing_uptime_24h = current_data.get("uptime_percentage_24h")
            existing_uptime_7d = current_data.get("uptime_percentage_7d")
            existing_uptime_30d = current_data.get("uptime_percentage_30d")

            # Update model_health_tracking
            update_data = {
                "provider": result.provider,
                "model": result.model,
                "gateway": result.gateway,
                "last_status": result.status.value,
                "last_response_time_ms": result.response_time_ms,
                "last_called_at": result.checked_at.isoformat(),
                "call_count": call_count,
                "success_count": success_count,
                "error_count": error_count,
                "average_response_time_ms": new_avg,
                "consecutive_failures": consecutive_failures,
                "consecutive_successes": consecutive_successes,
                "circuit_breaker_state": circuit_breaker_state.value,
                "last_error_message": result.error_message if not is_success else None,
                "last_success_at": result.checked_at.isoformat() if is_success else current_data.get("last_success_at"),
                "last_failure_at": result.checked_at.isoformat() if not is_success else current_data.get("last_failure_at"),
                "next_check_at": next_check_at.isoformat(),
                # Preserve existing uptime percentages (calculated by aggregate task)
                # Default to 100.0 for new models - they will be updated by the aggregate task
                "uptime_percentage_24h": existing_uptime_24h if existing_uptime_24h is not None else 100.0,
                "uptime_percentage_7d": existing_uptime_7d if existing_uptime_7d is not None else 100.0,
                "uptime_percentage_30d": existing_uptime_30d if existing_uptime_30d is not None else 100.0,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            # Upsert to database
            supabase.table("model_health_tracking").upsert(update_data).execute()

            # Insert into health history
            history_data = {
                "provider": result.provider,
                "model": result.model,
                "gateway": result.gateway,
                "checked_at": result.checked_at.isoformat(),
                "status": result.status.value,
                "response_time_ms": result.response_time_ms,
                "error_message": result.error_message,
                "http_status_code": result.http_status_code,
                "circuit_breaker_state": circuit_breaker_state.value,
            }
            supabase.table("model_health_history").insert(history_data).execute()

            # Handle incidents
            if not is_success:
                await self._create_or_update_incident(result, consecutive_failures)
            elif consecutive_successes >= 3:
                await self._resolve_incidents(result)

            logger.debug(
                f"Processed health check for {result.model}: {result.status.value} "
                f"({result.response_time_ms:.0f}ms)" if result.response_time_ms else f"Processed health check for {result.model}: {result.status.value}"
            )

        except Exception as e:
            logger.error(f"Failed to process health check result for {result.model}: {e}", exc_info=True)

    def _calculate_circuit_breaker_state(
        self,
        current_state: str,
        consecutive_failures: int,
        consecutive_successes: int,
    ) -> CircuitBreakerState:
        """
        Calculate circuit breaker state based on failure patterns.

        TRANSIENT FAILURE FIX: More lenient thresholds to avoid false positives
        from transient network issues and cold starts.

        Circuit breaker logic:
        - CLOSED: Normal operation, failures < 8
        - OPEN: Too many failures (≥8), block requests temporarily
        - HALF_OPEN: Testing recovery, allow limited traffic

        Args:
            current_state: Current circuit breaker state
            consecutive_failures: Number of consecutive failures
            consecutive_successes: Number of consecutive successes

        Returns:
            New circuit breaker state
        """
        current = CircuitBreakerState(current_state)

        # TRANSIENT FAILURE FIX: Increased threshold from 5 to 8 consecutive failures
        # Many models have occasional timeouts due to cold starts or network issues
        # We don't want to trip the circuit breaker on transient failures
        FAILURE_THRESHOLD = 8  # Increased from 5
        SUCCESS_THRESHOLD = 3  # Keep at 3 for quick recovery

        if current == CircuitBreakerState.CLOSED:
            if consecutive_failures >= FAILURE_THRESHOLD:
                logger.warning(
                    f"Circuit breaker opening after {consecutive_failures} consecutive failures "
                    f"(threshold: {FAILURE_THRESHOLD})"
                )
                return CircuitBreakerState.OPEN
            return CircuitBreakerState.CLOSED

        elif current == CircuitBreakerState.OPEN:
            # TRANSIENT FAILURE FIX: Auto-transition to HALF_OPEN after being OPEN
            # This allows the system to test recovery automatically
            # In a production system, this would be time-based (e.g., after 60s)
            logger.info("Circuit breaker transitioning from OPEN to HALF_OPEN for recovery test")
            return CircuitBreakerState.HALF_OPEN

        elif current == CircuitBreakerState.HALF_OPEN:
            if consecutive_successes >= SUCCESS_THRESHOLD:
                logger.info(
                    f"Circuit breaker closing after {consecutive_successes} consecutive successes "
                    f"(threshold: {SUCCESS_THRESHOLD})"
                )
                return CircuitBreakerState.CLOSED
            if consecutive_failures >= 1:
                logger.warning("Circuit breaker reopening after failure in HALF_OPEN state")
                return CircuitBreakerState.OPEN
            return CircuitBreakerState.HALF_OPEN

        return CircuitBreakerState.CLOSED

    async def _create_or_update_incident(self, result: HealthCheckResult, consecutive_failures: int):
        """Create or update incident for failing model"""
        try:
            from src.config.supabase_config import supabase

            # TRANSIENT FAILURE FIX: Check for active incident with improved retry logic
            active = None
            max_retries = 3  # Increased from 2 to 3
            retry_delays = [0.5, 1.0, 2.0]  # Exponential backoff

            for attempt in range(max_retries):
                try:
                    active = (
                        supabase.table("model_health_incidents")
                        .select("*")
                        .eq("provider", result.provider)
                        .eq("model", result.model)
                        .eq("gateway", result.gateway)
                        .eq("status", "active")
                        .order("started_at", desc=True)
                        .limit(1)
                        .maybe_single()
                        .execute()
                    )
                    break  # Success, exit retry loop
                except Exception as query_error:
                    # TRANSIENT FAILURE FIX: Check if error is transient before retrying
                    error_str = str(query_error).lower()
                    is_transient = any(
                        pattern in error_str
                        for pattern in [
                            "timeout",
                            "connection",
                            "network",
                            "503",
                            "502",
                            "unavailable",
                        ]
                    )

                    if is_transient and attempt < max_retries - 1:
                        delay = retry_delays[attempt] if attempt < len(retry_delays) else 2.0
                        logger.debug(
                            f"Transient error querying incidents for {result.model} "
                            f"(attempt {attempt + 1}/{max_retries}), retrying in {delay}s..."
                        )
                        await asyncio.sleep(delay)
                        continue

                    # Non-transient error or final retry
                    if attempt == max_retries - 1:
                        logger.debug(
                            f"Incident query failed for {result.model} after {max_retries} attempts: "
                            f"{type(query_error).__name__}: {str(query_error)[:100]}"
                        )
                    return

            # Handle case where response is None (e.g., Supabase client not initialized or table doesn't exist)
            if active is None:
                # Log at debug level - this happens frequently and is usually due to
                # the model_health_incidents table not existing or RLS policy issues
                logger.debug(
                    f"Supabase query returned None for incident check on {result.model}. "
                    "This may indicate the model_health_incidents table is not configured."
                )
                return

            if active.data:
                # Update existing incident
                incident_id = active.data["id"]
                supabase.table("model_health_incidents").update(
                    {
                        "error_count": active.data["error_count"] + 1,
                        "error_message": result.error_message,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                ).eq("id", incident_id).execute()
            else:
                # Create new incident
                severity = self._determine_incident_severity(consecutive_failures)
                incident_type = self._map_status_to_incident_type(result.status)

                supabase.table("model_health_incidents").insert(
                    {
                        "provider": result.provider,
                        "model": result.model,
                        "gateway": result.gateway,
                        "incident_type": incident_type,
                        "severity": severity.value,
                        "started_at": result.checked_at.isoformat(),
                        "error_message": result.error_message,
                        "status": "active",
                    }
                ).execute()

        except Exception as e:
            logger.error(f"Failed to create/update incident: {e}")

    async def _resolve_incidents(self, result: HealthCheckResult):
        """Resolve active incidents for a now-healthy model"""
        try:
            from src.config.supabase_config import supabase

            now = datetime.now(timezone.utc)

            supabase.table("model_health_incidents").update(
                {
                    "resolved_at": now.isoformat(),
                    "status": "resolved",
                    "resolution_notes": "Model recovered and passed health checks",
                    "updated_at": now.isoformat(),
                }
            ).eq("provider", result.provider).eq("model", result.model).eq("gateway", result.gateway).eq(
                "status", "active"
            ).execute()

        except Exception as e:
            logger.error(f"Failed to resolve incidents: {e}")

    def _determine_incident_severity(self, consecutive_failures: int) -> IncidentSeverity:
        """Determine incident severity based on failure count"""
        if consecutive_failures >= 10:
            return IncidentSeverity.CRITICAL
        elif consecutive_failures >= 5:
            return IncidentSeverity.HIGH
        elif consecutive_failures >= 3:
            return IncidentSeverity.MEDIUM
        else:
            return IncidentSeverity.LOW

    def _map_status_to_incident_type(self, status: HealthCheckStatus) -> str:
        """Map health check status to incident type"""
        mapping = {
            HealthCheckStatus.ERROR: "outage",
            HealthCheckStatus.TIMEOUT: "timeout",
            HealthCheckStatus.RATE_LIMITED: "rate_limit",
            HealthCheckStatus.UNAUTHORIZED: "authentication",
            HealthCheckStatus.NOT_FOUND: "unavailable",
        }
        return mapping.get(status, "unknown")

    async def _publish_health_to_cache(self):
        """Publish aggregated health data to Redis cache for main API consumption"""
        try:
            from src.config.redis_config import get_redis_config
            from src.config.supabase_config import supabase
            from src.services.simple_health_cache import simple_health_cache

            # Debug log to check Redis connection
            redis_config = get_redis_config()
            redis_host = redis_config.redis_host
            logger.info(f"Attempting to publish health data to Redis (host: {redis_host})")

            # Query aggregated health data from database
            # Get model health data
            models_response = (
                supabase.table("model_health_tracking")
                .select("provider, model, gateway, last_status, last_response_time_ms, uptime_percentage_24h, error_count, call_count, last_called_at")
                .eq("is_enabled", True)
                .order("last_called_at", desc=True)
                .limit(500)  # Limit for cache size
                .execute()
            )

            models_data = []
            for m in models_response.data or []:
                # The 'provider' column in model_health_tracking actually stores the gateway name
                # (e.g., openrouter, featherless, fireworks) due to how record_model_call works.
                # Use 'provider' as fallback for 'gateway' when gateway is not set.
                stored_provider = m.get("provider") or "unknown"
                stored_gateway = m.get("gateway")
                # If gateway is not set, use provider as gateway since that's what's stored
                effective_gateway = stored_gateway if stored_gateway else stored_provider
                models_data.append({
                    "model_id": m.get("model") or "unknown",
                    "provider": stored_provider,
                    "gateway": effective_gateway,
                    "status": "healthy" if m.get("last_status") == "success" else "unhealthy",
                    "response_time_ms": m.get("last_response_time_ms"),
                    "avg_response_time_ms": m.get("last_response_time_ms"),
                    "uptime_percentage": m.get("uptime_percentage_24h", 0.0),
                    "error_count": m.get("error_count", 0),
                    "total_requests": m.get("call_count", 0),
                    "last_checked": m.get("last_called_at"),
                })

            if models_data:
                simple_health_cache.cache_models_health(models_data)
                logger.debug(f"Published {len(models_data)} models health to Redis cache")

            # Aggregate provider data
            providers_map = {}
            for m in models_data:
                provider = m.get("provider", "unknown")
                gateway = m.get("gateway", "unknown")
                key = f"{gateway}:{provider}"
                if key not in providers_map:
                    providers_map[key] = {
                        "provider": provider,
                        "gateway": gateway,
                        "status": "online",
                        "total_models": 0,
                        "healthy_models": 0,
                        "degraded_models": 0,
                        "unhealthy_models": 0,
                        "avg_response_time_ms": 0,
                        "overall_uptime": 0,
                        "response_times": [],
                    }
                p = providers_map[key]
                p["total_models"] += 1
                if m.get("status") == "healthy":
                    p["healthy_models"] += 1
                else:
                    p["unhealthy_models"] += 1
                if m.get("response_time_ms"):
                    p["response_times"].append(m["response_time_ms"])

            # Calculate averages and status
            providers_data = []
            for p in providers_map.values():
                if p["response_times"]:
                    p["avg_response_time_ms"] = sum(p["response_times"]) / len(p["response_times"])
                if p["total_models"] > 0:
                    p["overall_uptime"] = (p["healthy_models"] / p["total_models"]) * 100
                if p["unhealthy_models"] > p["total_models"] * 0.5:
                    p["status"] = "offline"
                elif p["unhealthy_models"] > 0:
                    p["status"] = "degraded"
                del p["response_times"]  # Remove temp data
                providers_data.append(p)

            if providers_data:
                simple_health_cache.cache_providers_health(providers_data)
                logger.debug(f"Published {len(providers_data)} providers health to Redis cache")

            # Get total counts from openrouter_models table (not just tracked)
            # NOTE: We get total counts FIRST so we can properly calculate healthy/unhealthy
            try:
                catalog_models_response = supabase.table("openrouter_models").select("id", count="exact", head=True).execute()
                total_models = catalog_models_response.count if catalog_models_response.count is not None else 0
                logger.info(f"Got {total_models} total models from openrouter_models table")
            except Exception as e:
                logger.warning(f"Failed to get models from 'openrouter_models' table: {e}, trying models table")
                try:
                    catalog_models_response = supabase.table("models").select("id", count="exact", head=True).execute()
                    total_models = catalog_models_response.count if catalog_models_response.count is not None else 0
                    logger.info(f"Got {total_models} total models from models table")
                except Exception as e2:
                    logger.warning(f"Failed to get models catalog count: {e2}")
                    total_models = 0

            try:
                # Get all providers from providers table
                providers_response = supabase.table("providers").select("id", count="exact", head=True).execute()
                total_providers = providers_response.count if providers_response.count is not None else 0
                logger.info(f"Got {total_providers} total providers from providers table")
            except Exception as e:
                logger.warning(f"Failed to get providers catalog count: {e}")
                total_providers = 0

            # Get gateway count from GATEWAY_CONFIG (not a database table)
            try:
                from src.services.gateway_health_service import GATEWAY_CONFIG
                total_gateways = len(GATEWAY_CONFIG)
            except Exception:
                total_gateways = 0

            # Calculate system health from tracked models
            # NOTE: tracked counts are from models we actually checked health for
            tracked_models = len(models_data)
            tracked_healthy_models = sum(1 for m in models_data if m.get("status") == "healthy")
            tracked_unhealthy_models = tracked_models - tracked_healthy_models
            tracked_providers = len(providers_data)
            healthy_providers = sum(1 for p in providers_data if p.get("status") == "online")
            degraded_providers = sum(1 for p in providers_data if p.get("status") == "degraded")
            unhealthy_providers = sum(1 for p in providers_data if p.get("status") == "offline")

            # IMPORTANT: healthy_models and unhealthy_models must be based on total_models
            # Report only what we actually know from tracked data
            # Otherwise, report 0 healthy until we have actual health data
            if tracked_models > 0 and total_models > 0:
                # For models we haven't tracked, we don't know their status
                # Report only what we actually know: healthy = tracked healthy, unhealthy = tracked unhealthy
                # Untracked models are in "unknown" state (not counted as healthy or unhealthy)
                healthy_models = tracked_healthy_models
                unhealthy_models = tracked_unhealthy_models
                # Ensure healthy_models never exceeds total_models (data consistency)
                healthy_models = min(healthy_models, total_models)
                unhealthy_models = min(unhealthy_models, total_models - healthy_models)
            else:
                # No tracking data available
                healthy_models = 0
                unhealthy_models = 0

            # Determine overall status based on tracked data
            if tracked_models == 0:
                overall_status = "unknown"
            elif unhealthy_providers == 0 and degraded_providers == 0 and tracked_healthy_models == tracked_models:
                overall_status = "healthy"
            elif tracked_providers > 0 and unhealthy_providers >= tracked_providers * 0.5:
                overall_status = "unhealthy"
            elif unhealthy_providers > 0 or degraded_providers > 0 or tracked_unhealthy_models > 0:
                overall_status = "degraded"
            else:
                overall_status = "healthy"

            # Calculate system uptime from tracked models
            system_uptime = (tracked_healthy_models / tracked_models * 100) if tracked_models > 0 else 0.0

            # Calculate healthy gateways based on provider health data
            # A gateway is considered healthy if at least one of its providers is online
            gateway_health = {}
            for p in providers_data:
                gw = p.get("gateway", "unknown")
                if gw not in gateway_health:
                    gateway_health[gw] = {
                        "healthy": False,
                        "status": "offline",
                        "latency_ms": 0,
                        "available": False,
                        "last_check": None,
                        "error": None
                    }
                if p.get("status") == "online":
                    gateway_health[gw]["healthy"] = True
                    gateway_health[gw]["status"] = "online"
                    gateway_health[gw]["available"] = True
                    gateway_health[gw]["latency_ms"] = p.get("avg_response_time_ms", 0)
                    gateway_health[gw]["last_check"] = p.get("last_checked")
                elif p.get("status") == "degraded" and gateway_health[gw]["status"] == "offline":
                    gateway_health[gw]["status"] = "degraded"

            # Add all gateways from GATEWAY_CONFIG that aren't tracked yet
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
                        needs_api_key = url is not None  # Gateways with url=None use static catalogs

                        # Check if cache has models
                        cache = gateway_config.get("cache", {})
                        cache_data = cache.get("data") if cache else None
                        has_cached_models = cache_data is not None and len(cache_data) > 0 if cache_data else False
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
                            error_msg = "Models not yet synced. They will appear when first accessed."
                            is_healthy = False

                        gateway_health[gateway_name] = {
                            "healthy": is_healthy,
                            "status": status,
                            "latency_ms": None,
                            "available": is_healthy,
                            "last_check": datetime.now(timezone.utc).isoformat(),
                            "error": error_msg,
                            "total_models": model_count,
                            "configured": has_api_key or not needs_api_key,
                        }
            except Exception as e:
                logger.warning(f"Failed to add unconfigured gateways: {e}")

            healthy_gateways = sum(1 for g in gateway_health.values() if g.get("healthy", False))

            system_data = {
                "overall_status": overall_status,
                "total_providers": total_providers,
                "healthy_providers": healthy_providers,
                "degraded_providers": degraded_providers,
                "unhealthy_providers": unhealthy_providers,
                "total_models": total_models,
                "healthy_models": healthy_models,
                "degraded_models": 0,  # Not tracked - models are either healthy or unhealthy
                "unhealthy_models": unhealthy_models,
                "total_gateways": total_gateways,
                "healthy_gateways": healthy_gateways,
                "tracked_models": tracked_models,
                "tracked_providers": tracked_providers,
                "system_uptime": system_uptime,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }

            simple_health_cache.cache_system_health(system_data)
            simple_health_cache.cache_gateways_health(gateway_health)
            # HEALTH FIX #1094: Improved logging to clarify tracked vs catalog discrepancy
            logger.info(
                f"Published health data to Redis cache: "
                f"{total_models} models in catalog ({healthy_models} healthy, {unhealthy_models} unhealthy), "
                f"{total_providers} providers, {total_gateways} gateways ({healthy_gateways} healthy / {len(gateway_health)} cached), "
                f"tracked: {tracked_models} models "
                f"(Note: tracked count includes models from all {total_gateways} gateways + historical models, not just catalog)"
            )

            # HEALTH FIX #1094: Check for health degradation and send Sentry alerts
            await self._check_health_threshold_and_alert(
                total_models=total_models,
                healthy_models=healthy_models,
                unhealthy_models=unhealthy_models,
                system_uptime=system_uptime,
            )

        except Exception as e:
            # TRANSIENT FAILURE FIX: Better error handling for Redis cache failures
            error_str = str(e).lower()
            is_redis_transient = any(
                pattern in error_str
                for pattern in [
                    "timeout",
                    "connection",
                    "redis",
                    "upstash",
                    "network",
                    "unavailable",
                ]
            )

            if is_redis_transient:
                logger.warning(
                    f"Transient Redis cache publish failure (will retry next cycle): "
                    f"{type(e).__name__}: {str(e)[:100]}"
                )
            else:
                logger.error(
                    f"Failed to publish health data to Redis cache: "
                    f"{type(e).__name__}: {str(e)[:200]}"
                )
            # Don't fail the monitoring if cache publish fails - graceful degradation

    async def _check_health_threshold_and_alert(
        self,
        total_models: int,
        healthy_models: int,
        unhealthy_models: int,
        system_uptime: float,
    ):
        """
        Check if health has degraded below threshold and send Sentry alerts.

        Part of fix for issue #1094 - Model Health Degradation.

        Alerts when:
        - Overall health drops below 90%
        - Critical degradation below 85%

        Args:
            total_models: Total number of models in catalog
            healthy_models: Number of healthy models
            unhealthy_models: Number of unhealthy models
            system_uptime: System uptime percentage
        """
        try:
            # Calculate health percentage
            if total_models == 0:
                return

            health_pct = (healthy_models / total_models) * 100

            # Check threshold - alert if below 90%
            HEALTH_THRESHOLD = 90.0

            if health_pct < HEALTH_THRESHOLD:
                # Determine severity
                if health_pct < 85.0:
                    severity = "critical"
                    emoji = "\ud83d\udea8"
                elif health_pct < 90.0:
                    severity = "error"
                    emoji = "\u26a0\ufe0f"
                else:
                    severity = "warning"
                    emoji = "\u26a0\ufe0f"

                error_message = (
                    f"{emoji} HEALTH ALERT: Model health degraded to {health_pct:.1f}% "
                    f"(threshold: {HEALTH_THRESHOLD}%)\n"
                    f"Healthy: {healthy_models}/{total_models} models\n"
                    f"Unhealthy: {unhealthy_models} models\n"
                    f"System Uptime: {system_uptime:.1f}%\n"
                    f"Issue: GitHub #1094 - Model Health Degradation"
                )

                logger.error(error_message)

                # Send to Sentry
                try:
                    import sentry_sdk
                    sentry_sdk.capture_message(
                        error_message,
                        level=severity,
                        extras={
                            "health_percentage": health_pct,
                            "healthy_models": healthy_models,
                            "unhealthy_models": unhealthy_models,
                            "total_models": total_models,
                            "system_uptime": system_uptime,
                            "threshold": HEALTH_THRESHOLD,
                            "github_issue": "#1094",
                            "fix_docs": "QUICK_START_HEALTH_FIX.md",
                        },
                        tags={
                            "health_alert": "true",
                            "severity": severity,
                            "issue": "1094",
                        },
                    )
                    logger.info(f"Sent health degradation alert to Sentry (severity: {severity})")
                except Exception as sentry_error:
                    logger.warning(f"Failed to send Sentry alert: {sentry_error}")

        except Exception as e:
            logger.error(f"Error checking health threshold: {e}")

    async def _tier_update_loop(self):
        """Periodically update model tiers based on usage patterns"""
        while self.monitoring_active:
            try:
                await asyncio.sleep(3600)  # Every hour

                from src.config.supabase_config import supabase

                # Call the database function to update tiers
                try:
                    supabase.rpc("update_model_tier").execute()
                    logger.info("Updated model monitoring tiers based on usage")
                except Exception as rpc_error:
                    # Handle database function not found or schema cache issues
                    error_msg = str(rpc_error)
                    if "PGRST202" in error_msg or "Could not find the function" in error_msg:
                        logger.warning(
                            f"Database function 'update_model_tier' not found in schema cache. "
                            f"This may indicate the migration hasn't been applied or PostgREST needs a schema reload. "
                            f"Error: {error_msg}"
                        )
                        # Skip this iteration and try again next hour
                        continue
                    else:
                        # Re-raise other errors for proper logging
                        raise

            except Exception as e:
                logger.error(f"Error in tier update loop: {e}", exc_info=True)
                await asyncio.sleep(3600)

    async def _aggregate_metrics_loop(self):
        """Periodically aggregate metrics for performance"""
        while self.monitoring_active:
            try:
                await asyncio.sleep(300)  # Every 5 minutes

                # Aggregate hourly metrics
                await self._aggregate_hourly_metrics()

                logger.debug("Aggregated health metrics")

            except Exception as e:
                logger.error(f"Error in aggregate metrics loop: {e}", exc_info=True)
                await asyncio.sleep(300)

    async def _aggregate_hourly_metrics(self):
        """Aggregate hourly health metrics and update uptime percentages from actual history data"""
        try:
            from src.config.supabase_config import supabase

            # Calculate 24h uptime from model_health_history for all models
            # This uses actual success/failure counts from the last 24 hours
            now = datetime.now(timezone.utc)
            twenty_four_hours_ago = now - timedelta(hours=24)
            seven_days_ago = now - timedelta(days=7)
            thirty_days_ago = now - timedelta(days=30)

            # Get all tracked models
            tracked_models = (
                supabase.table("model_health_tracking")
                .select("provider, model, gateway")
                .eq("is_enabled", True)
                .execute()
            )

            if not tracked_models.data:
                logger.debug("No tracked models found for uptime aggregation")
                return

            # Process models in batches to avoid overwhelming the database
            batch_size = 50
            updated_count = 0

            for i in range(0, len(tracked_models.data), batch_size):
                batch = tracked_models.data[i : i + batch_size]

                for model_data in batch:
                    try:
                        provider = model_data["provider"]
                        model = model_data["model"]

                        # Calculate 24h uptime from history
                        history_24h = (
                            supabase.table("model_health_history")
                            .select("status")
                            .eq("provider", provider)
                            .eq("model", model)
                            .gte("checked_at", twenty_four_hours_ago.isoformat())
                            .execute()
                        )

                        if history_24h.data and len(history_24h.data) > 0:
                            total_checks_24h = len(history_24h.data)
                            success_checks_24h = len(
                                [h for h in history_24h.data if h.get("status") == "success"]
                            )
                            uptime_24h = (success_checks_24h / total_checks_24h * 100) if total_checks_24h > 0 else 100.0
                        else:
                            # No history data in 24h - use last_status as indicator
                            # If no checks, assume healthy (new model)
                            uptime_24h = 100.0

                        # Calculate 7d uptime from history
                        history_7d = (
                            supabase.table("model_health_history")
                            .select("status")
                            .eq("provider", provider)
                            .eq("model", model)
                            .gte("checked_at", seven_days_ago.isoformat())
                            .execute()
                        )

                        if history_7d.data and len(history_7d.data) > 0:
                            total_checks_7d = len(history_7d.data)
                            success_checks_7d = len(
                                [h for h in history_7d.data if h.get("status") == "success"]
                            )
                            uptime_7d = (success_checks_7d / total_checks_7d * 100) if total_checks_7d > 0 else 100.0
                        else:
                            uptime_7d = 100.0

                        # Calculate 30d uptime from history
                        history_30d = (
                            supabase.table("model_health_history")
                            .select("status")
                            .eq("provider", provider)
                            .eq("model", model)
                            .gte("checked_at", thirty_days_ago.isoformat())
                            .execute()
                        )

                        if history_30d.data and len(history_30d.data) > 0:
                            total_checks_30d = len(history_30d.data)
                            success_checks_30d = len(
                                [h for h in history_30d.data if h.get("status") == "success"]
                            )
                            uptime_30d = (success_checks_30d / total_checks_30d * 100) if total_checks_30d > 0 else 100.0
                        else:
                            uptime_30d = 100.0

                        # Update the model_health_tracking table with calculated uptime
                        supabase.table("model_health_tracking").update(
                            {
                                "uptime_percentage_24h": round(uptime_24h, 2),
                                "uptime_percentage_7d": round(uptime_7d, 2),
                                "uptime_percentage_30d": round(uptime_30d, 2),
                                "updated_at": now.isoformat(),
                            }
                        ).eq("provider", provider).eq("model", model).execute()

                        updated_count += 1

                    except Exception as e:
                        logger.warning(
                            f"Failed to aggregate metrics for {model_data.get('provider')}/{model_data.get('model')}: {e}"
                        )
                        continue

                # Small delay between batches to avoid rate limiting
                await asyncio.sleep(0.1)

            logger.info(f"Aggregated uptime metrics for {updated_count} models")

        except Exception as e:
            logger.error(f"Failed to aggregate hourly metrics: {e}", exc_info=True)

    async def _incident_resolution_loop(self):
        """Check for auto-resolvable incidents"""
        while self.monitoring_active:
            try:
                await asyncio.sleep(600)  # Every 10 minutes

                # Check for incidents that should be auto-resolved
                # (e.g., active for > 24h with no recent failures)

            except Exception as e:
                logger.error(f"Error in incident resolution loop: {e}", exc_info=True)
                await asyncio.sleep(600)

    async def check_model_on_demand(
        self, provider: str, model: str, gateway: str
    ) -> HealthCheckResult | None:
        """
        Perform an immediate health check for a specific model

        Used for on-demand checking when a user requests a model.
        """
        model_data = {
            "provider": provider,
            "model": model,
            "gateway": gateway,
            "monitoring_tier": "on_demand",
        }

        result = await self._check_model_health(model_data)

        if result:
            await self._process_health_check_result(result)

        return result


# Global instance
intelligent_health_monitor = IntelligentHealthMonitor()
