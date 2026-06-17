"""Region header middleware (Gatewayz One Phase 5, rollout phase 1).

Adds an ``X-Gatewayz-Region`` response header naming the region that served the
request, and ``X-Gatewayz-Region-Selected`` (the region the router would pick for
this instance's home). Observability only — no routing decision is made here and
behavior is unchanged in single-region mode. Mirrors ``RequestIDMiddleware``.
"""

from __future__ import annotations

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


class RegionHeaderMiddleware(BaseHTTPMiddleware):
    """Stamp the serving/selected region onto every response (never raises)."""

    def __init__(self, app: ASGIApp):
        super().__init__(app)
        try:
            from src.services.region_service import region_status

            logger.info("RegionHeaderMiddleware initialized: %s", region_status())
        except Exception:  # never block startup over an observability header
            logger.debug("RegionHeaderMiddleware init: region_status unavailable", exc_info=True)

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        try:
            from src.services.region_service import current_region, selected_region

            response.headers["X-Gatewayz-Region"] = current_region()
            sel = selected_region()
            if sel is not None:
                response.headers["X-Gatewayz-Region-Selected"] = sel.name
        except Exception:  # header is best-effort; never affect the response
            logger.debug("RegionHeaderMiddleware: could not stamp region header", exc_info=True)
        return response
