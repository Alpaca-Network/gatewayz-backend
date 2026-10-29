"""Per-request phase timing for the inference routes, exposed as Server-Timing.

Inference requests spent 1.3-1.8 s inside Gatewayz on top of the provider's
own time (measured 2026-10-27: claude-haiku 0.5 s direct vs 2.0 s through the
gateway). Production logs at WARNING, so nothing said where. This records how
long each awaited step of a request takes and:

- returns it in a ``Server-Timing`` response header (visible in browser
  devtools and to any client), and
- logs one WARNING line with the breakdown when a request is slow.

Recording is a no-op outside an instrumented request, and never raises.
"""

from __future__ import annotations

import functools
import inspect
import logging
import time
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

# The middleware puts a list here; handlers append (name, ms) to the SAME list,
# so the middleware sees the entries even though the endpoint may run in a
# copied context.
_phases: ContextVar[list[tuple[str, float]] | None] = ContextVar("request_phases", default=None)

INSTRUMENTED_PREFIXES = (
    "/v1/chat/completions",
    "/chat/completions",
    "/v1/messages",
    "/v1/completions",
)
SLOW_REQUEST_MS = 1500.0


def record(name: str, ms: float) -> None:
    phases = _phases.get()
    if phases is not None:
        phases.append((name, ms))


class phase:
    """``with phase("name"):`` records the block's duration (sync code too)."""

    __slots__ = ("name", "start")

    def __init__(self, name: str) -> None:
        self.name = name
        self.start = 0.0

    def __enter__(self) -> phase:
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        record(self.name, (time.perf_counter() - self.start) * 1000)


def timed(fn: Any, name: str | None = None) -> Any:
    """Wrap a coroutine function so each call records its duration."""
    if not inspect.iscoroutinefunction(fn) or getattr(fn, "__request_timed__", False):
        return fn
    label = name or getattr(fn, "__name__", "call").lstrip("_")

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return await fn(*args, **kwargs)
        finally:
            record(label, (time.perf_counter() - start) * 1000)

    wrapper.__request_timed__ = True  # type: ignore[attr-defined]
    return wrapper


def timed_to_thread(to_thread: Any) -> Any:
    """Wrap a ``to_thread(func, *args)`` helper; each call is named after func."""
    if getattr(to_thread, "__request_timed__", False):
        return to_thread

    @functools.wraps(to_thread)
    async def wrapper(func: Any, *args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return await to_thread(func, *args, **kwargs)
        finally:
            record(
                getattr(func, "__name__", "thread").lstrip("_"),
                (time.perf_counter() - start) * 1000,
            )

    wrapper.__request_timed__ = True  # type: ignore[attr-defined]
    return wrapper


def server_timing_value(phases: list[tuple[str, float]], total_ms: float) -> str:
    """'name;dur=12.3' entries, repeated names summed, slowest first, plus total."""
    sums: dict[str, float] = {}
    for name, ms in phases:
        sums[name] = sums.get(name, 0.0) + ms
    parts = [f"{_token(n)};dur={ms:.1f}" for n, ms in sorted(sums.items(), key=lambda x: -x[1])]
    parts.append(f"total;dur={total_ms:.1f}")
    return ", ".join(parts)


def _token(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:60] or "step"


class RequestTimingMiddleware:
    """Pure ASGI middleware: collects phases for inference routes and emits them."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith(
            INSTRUMENTED_PREFIXES
        ):
            await self.app(scope, receive, send)
            return

        phases: list[tuple[str, float]] = []
        token = _phases.set(phases)
        start = time.perf_counter()
        # Background tasks run after the response is sent but before this call
        # returns; measure what the client waited for, not the bookkeeping after.
        done: dict[str, Any] = {}

        async def send_with_timing(message: dict) -> None:
            if message.get("type") == "http.response.start":
                try:
                    total = (time.perf_counter() - start) * 1000
                    headers = list(message.get("headers") or [])
                    headers.append(
                        (b"server-timing", server_timing_value(phases, total).encode("latin-1"))
                    )
                    message = {**message, "headers": headers}
                except Exception:  # noqa: BLE001 - timing must never break a response
                    pass
            await send(message)
            if message.get("type") == "http.response.body" and not message.get("more_body", False):
                if "ms" not in done:
                    done["ms"] = (time.perf_counter() - start) * 1000
                    done["phases"] = list(phases)

        try:
            await self.app(scope, receive, send_with_timing)
        finally:
            total = done.get("ms", (time.perf_counter() - start) * 1000)
            if total >= SLOW_REQUEST_MS:
                try:
                    logger.warning(
                        "slow inference request path=%s total_ms=%.0f phases=%s",
                        scope.get("path"),
                        total,
                        server_timing_value(done.get("phases", phases), total),
                    )
                except Exception:  # noqa: BLE001
                    pass
            _phases.reset(token)
