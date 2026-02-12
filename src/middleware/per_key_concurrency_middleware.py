"""
Per-API-Key Concurrency Limiter Middleware

Limits the number of concurrent requests per API key on inference endpoints.
This prevents a single key (e.g. a bot) from monopolizing all server capacity,
which causes latency spikes and 499 errors for other users.

Returns 429 Too Many Requests (not 503) because this is per-key limiting.
Pure ASGI middleware for minimal overhead and proper streaming support.
"""

import asyncio
import json
import logging
import time
from collections import OrderedDict

from prometheus_client import Counter, Gauge
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Prometheus metrics for per-key concurrency monitoring
per_key_concurrency_active = Gauge(
    "per_key_concurrency_active_requests",
    "Active concurrent requests for a specific API key",
    ["key_prefix"],
)
per_key_concurrency_rejected = Counter(
    "per_key_concurrency_rejected_total",
    "Total requests rejected due to per-key concurrency limit",
)

# Inference paths that require per-key concurrency limiting
CONCURRENCY_LIMITED_PATHS = frozenset({
    "/v1/chat/completions",
    "/v1/messages",
    "/ai-sdk/chat/completions",
    "/v1/images/generations",
})


class PerKeyConcurrencyMiddleware:
    """
    Per-API-key concurrency gate for inference endpoints.

    Behavior:
    - Request to non-inference path: pass through immediately
    - Request without auth: pass through (handled by anonymous rate limiter)
    - Slots available for key: acquire and process
    - All slots consumed for key: immediate 429
    """

    def __init__(
        self,
        app: ASGIApp,
        max_concurrent_per_key: int = 5,
        max_tracked_keys: int = 2000,
    ):
        self.app = app
        self.max_concurrent = max_concurrent_per_key
        self.max_tracked_keys = max_tracked_keys
        # OrderedDict for LRU eviction: key -> (asyncio.Semaphore, last_access_time)
        self._semaphores: OrderedDict[str, asyncio.Semaphore] = OrderedDict()
        self._lock = asyncio.Lock()
        logger.info(
            f"Per-key concurrency middleware initialized "
            f"(limit={max_concurrent_per_key}, max_keys={max_tracked_keys})"
        )

    def _extract_api_key(self, scope: Scope) -> str | None:
        """Extract API key from Authorization header in ASGI scope."""
        headers = scope.get("headers", [])
        for name, value in headers:
            if name == b"authorization":
                auth_value = value.decode("utf-8", errors="replace")
                # Strip "Bearer " prefix if present
                key = auth_value.replace("Bearer ", "").strip()
                return key if len(key) > 10 else None
        return None

    async def _get_semaphore(self, key: str) -> asyncio.Semaphore:
        """Get or create a semaphore for the given key with LRU eviction."""
        async with self._lock:
            if key in self._semaphores:
                self._semaphores.move_to_end(key)
                return self._semaphores[key]

            # Evict oldest idle entries if at capacity
            while len(self._semaphores) >= self.max_tracked_keys:
                oldest_key, oldest_sem = self._semaphores.popitem(last=False)
                # Only evict if no active requests on this key
                if oldest_sem._value < self.max_concurrent:
                    # Key has active requests, put it back and stop evicting
                    self._semaphores[oldest_key] = oldest_sem
                    self._semaphores.move_to_end(oldest_key, last=False)
                    break

            sem = asyncio.Semaphore(self.max_concurrent)
            self._semaphores[key] = sem
            return sem

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Only limit inference endpoints
        if path not in CONCURRENCY_LIMITED_PATHS:
            await self.app(scope, receive, send)
            return

        api_key = self._extract_api_key(scope)
        if not api_key:
            # Anonymous requests pass through (handled by anonymous rate limiter)
            await self.app(scope, receive, send)
            return

        sem = await self._get_semaphore(api_key)
        key_prefix = api_key[:10]

        # Non-blocking check: if all slots consumed, reject immediately
        if sem._value <= 0:
            per_key_concurrency_rejected.inc()
            method = scope.get("method", "UNKNOWN")
            logger.warning(
                f"Per-key concurrency REJECT: {key_prefix}... "
                f"({self.max_concurrent}/{self.max_concurrent} slots in use) "
                f"on {method} {path}"
            )
            await self._send_429(scope, send)
            return

        # Acquire slot and process request
        await sem.acquire()
        per_key_concurrency_active.labels(key_prefix=key_prefix).inc()
        try:
            await self.app(scope, receive, send)
        finally:
            sem.release()
            per_key_concurrency_active.labels(key_prefix=key_prefix).dec()

    @staticmethod
    async def _send_429(scope: Scope, send: Send) -> None:
        """Send a 429 Too Many Requests response."""
        body = json.dumps({
            "error": {
                "message": "Too many concurrent requests for this API key. Please reduce parallelism.",
                "type": "rate_limit_error",
                "code": 429,
            }
        }).encode()

        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"retry-after", b"2"),
                (b"x-ratelimit-reason", b"per_key_concurrency"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })
