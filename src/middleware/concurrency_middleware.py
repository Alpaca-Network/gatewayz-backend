"""
Concurrency Control Middleware

Global server-level admission gate using asyncio.Semaphore.
Limits the number of requests processed concurrently with a bounded queue
for overflow, preventing resource exhaustion under bot/attack traffic.

Returns 503 Service Unavailable (not 429) because this is server capacity
protection, not rate limiting. Existing rate limiters handle per-key 429s.

This is a pure ASGI middleware (not BaseHTTPMiddleware) for minimal overhead
and proper streaming support.
"""

import asyncio
import json
import logging
import time

from prometheus_client import Counter, Gauge
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Prometheus metrics for concurrency monitoring
concurrency_active = Gauge(
    "concurrency_active_requests",
    "Number of requests currently being processed",
)
concurrency_queued = Gauge(
    "concurrency_queued_requests",
    "Number of requests waiting in the admission queue",
)
concurrency_rejected = Counter(
    "concurrency_rejected_total",
    "Total requests rejected due to server overload",
    ["reason"],
)

# Paths exempt from concurrency control (monitoring must always work)
CONCURRENCY_EXEMPT_PATHS = frozenset({
    "/health",
    "/metrics",
    "/ready",
})


class ConcurrencyMiddleware:
    """
    Global concurrency gate that limits simultaneous request processing.

    Behavior:
    - Slot available: acquire immediately and process
    - Slots full, queue has room: wait up to queue_timeout seconds
    - Slots full, queue full: immediate 503
    - Queue timeout exceeded: 503
    """

    def __init__(
        self,
        app: ASGIApp,
        limit: int = 20,
        queue_size: int = 50,
        queue_timeout: float = 10.0,
    ):
        self.app = app
        self.semaphore = asyncio.Semaphore(limit)
        self.limit = limit
        self.queue_size = queue_size
        self.queue_timeout = queue_timeout
        self._waiting = 0
        logger.info(
            f"Concurrency middleware initialized "
            f"(limit={limit}, queue={queue_size}, timeout={queue_timeout}s)"
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Exempt monitoring endpoints
        if path in CONCURRENCY_EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        # Also exempt paths that start with exempt prefixes (e.g. /health/detailed)
        if any(path.startswith(p) for p in CONCURRENCY_EXEMPT_PATHS):
            await self.app(scope, receive, send)
            return

        # Fast path: try non-blocking acquire
        acquired = self.semaphore._value > 0
        if acquired:
            try:
                self.semaphore._value -= 1
                concurrency_active.inc()
                try:
                    await self.app(scope, receive, send)
                finally:
                    self.semaphore.release()
                    concurrency_active.dec()
                return
            except Exception:
                # If the fast-path manipulation failed, fall through to normal path
                self.semaphore._value += 1
                concurrency_active.dec()

        # Queue is full — reject immediately
        if self._waiting >= self.queue_size:
            method = scope.get("method", "UNKNOWN")
            concurrency_rejected.labels(reason="queue_full").inc()
            logger.warning(
                f"Concurrency gate REJECT (queue full): {method} {path} "
                f"(active={self.limit - self.semaphore._value}, queued={self._waiting})"
            )
            await self._send_503(scope, send, "Server at capacity, please retry")
            return

        # Queue the request with timeout
        self._waiting += 1
        concurrency_queued.inc()
        wait_start = time.monotonic()

        try:
            await asyncio.wait_for(
                self.semaphore.acquire(),
                timeout=self.queue_timeout,
            )
        except asyncio.TimeoutError:
            self._waiting -= 1
            concurrency_queued.dec()
            method = scope.get("method", "UNKNOWN")
            wait_time = time.monotonic() - wait_start
            concurrency_rejected.labels(reason="queue_timeout").inc()
            logger.warning(
                f"Concurrency gate REJECT (queue timeout {wait_time:.1f}s): "
                f"{method} {path}"
            )
            await self._send_503(scope, send, "Server busy, please retry")
            return

        # Acquired after waiting — process the request
        self._waiting -= 1
        concurrency_queued.dec()
        concurrency_active.inc()

        try:
            await self.app(scope, receive, send)
        finally:
            self.semaphore.release()
            concurrency_active.dec()

    @staticmethod
    async def _send_503(scope: Scope, send: Send, message: str) -> None:
        """Send a 503 Service Unavailable response."""
        body = json.dumps({
            "error": {
                "message": message,
                "type": "server_overload",
                "code": 503,
            }
        }).encode()

        await send({
            "type": "http.response.start",
            "status": 503,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"retry-after", b"5"),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })
