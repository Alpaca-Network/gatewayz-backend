"""
Deprecation middleware for legacy chat endpoints.

Adds RFC 8594 compliant deprecation headers to legacy endpoints.
"""

import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)


class DeprecationMiddleware(BaseHTTPMiddleware):
    """
    Add deprecation warnings to legacy endpoint responses.

    Implements RFC 8594 deprecation headers:
    - Deprecation: true
    - Sunset: Date when endpoint will be removed
    - Link: Alternate endpoint URL
    """

    # Map of deprecated endpoints to their replacements
    DEPRECATED_ENDPOINTS = {
        "/v1/chat/completions": {
            "replacement": "/v1/chat",
            "sunset_date": "2025-06-01",  # 4 months from Feb 2025
            "migration_url": "https://docs.gatewayz.com/migration/unified-chat",
            "reason": "Use /v1/chat for unified API with auto-format detection"
        },
        "/v1/messages": {
            "replacement": "/v1/chat",
            "sunset_date": "2025-06-01",
            "migration_url": "https://docs.gatewayz.com/migration/unified-chat",
            "reason": "Use /v1/chat for unified API with auto-format detection"
        },
        "/v1/responses": {
            "replacement": "/v1/chat",
            "sunset_date": "2025-06-01",
            "migration_url": "https://docs.gatewayz.com/migration/unified-chat",
            "reason": "Use /v1/chat for unified API with auto-format detection"
        },
        "/api/chat/ai-sdk": {
            "replacement": "/v1/chat",
            "sunset_date": "2025-06-01",
            "migration_url": "https://docs.gatewayz.com/migration/unified-chat",
            "reason": "Use /v1/chat for unified API"
        },
        "/api/chat/ai-sdk-completions": {
            "replacement": "/v1/chat",
            "sunset_date": "2025-06-01",
            "migration_url": "https://docs.gatewayz.com/migration/unified-chat",
            "reason": "Use /v1/chat for unified API"
        },
    }

    async def dispatch(self, request: Request, call_next):
        # Get response
        response = await call_next(request)

        # Check if this is a deprecated endpoint
        path = request.url.path

        if path in self.DEPRECATED_ENDPOINTS:
            deprecation_info = self.DEPRECATED_ENDPOINTS[path]

            # Add RFC 8594 deprecation headers
            response.headers["Deprecation"] = "true"
            response.headers["Sunset"] = deprecation_info["sunset_date"]
            response.headers["Link"] = f'<{deprecation_info["replacement"]}>; rel="alternate"'

            # Custom headers for additional context
            response.headers["X-Deprecation-Migration-Url"] = deprecation_info["migration_url"]
            response.headers["X-API-Warn"] = (
                f"Endpoint deprecated. {deprecation_info['reason']}. "
                f"Sunset: {deprecation_info['sunset_date']}"
            )

            # Log deprecation usage (can be used for tracking)
            logger.info(
                f"Deprecated endpoint used: {path} by {request.client.host if request.client else 'unknown'}"
            )

        return response
