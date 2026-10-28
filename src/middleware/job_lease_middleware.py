"""
Job Lease Middleware

Releases a job key's in-flight lease (migration 20261028130000) once the request
is over: after the last byte of the response, including a stream's, or when the
request fails or the client disconnects. Key validation takes the lease and
leaves it on ``request.state.job_lease``; every other request passes through.

Pure ASGI (not BaseHTTPMiddleware) so a streaming response is finished, not just
started, when the release runs.
"""

import asyncio
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

from src.services.job_usage import release_job_lease

logger = logging.getLogger(__name__)


class JobLeaseMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            lease = (scope.get("state") or {}).get("job_lease")
            if lease:
                # Shielded: a cancelled request (disconnect, timeout) still frees its slot.
                await asyncio.shield(asyncio.to_thread(release_job_lease, *lease))
