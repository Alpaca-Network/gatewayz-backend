"""
Deprecation middleware for legacy chat endpoints.

Adds RFC 8594 compliant deprecation headers to legacy endpoints.

This is a pure ASGI middleware (not BaseHTTPMiddleware) to properly support
streaming responses without the "No response returned" error.
"""

import logging
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)


class DeprecationMiddleware:
    """
    Add deprecation warnings to legacy endpoint responses.

    Implements RFC 8594 deprecation headers:
    - Deprecation: true
    - Sunset: Date when endpoint will be removed
    - Link: Alternate endpoint URL

    This is a pure ASGI middleware to properly support streaming responses.
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

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only process HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        deprecation_info = self.DEPRECATED_ENDPOINTS.get(path)

        # If not a deprecated endpoint, pass through without modification
        if not deprecation_info:
            await self.app(scope, receive, send)
            return

        # Log deprecation usage at debug level to reduce log noise
        client = scope.get("client")
        client_host = client[0] if client else "unknown"
        logger.debug(f"Deprecated endpoint used: {path} by {client_host}")

        # Create a send wrapper that adds deprecation headers
        async def send_with_deprecation_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Get existing headers and convert to mutable list
                headers = list(message.get("headers", []))

                # Add RFC 8594 deprecation headers
                headers.extend([
                    (b"deprecation", b"true"),
                    (b"sunset", deprecation_info["sunset_date"].encode()),
                    (
                        b"link",
                        f'<{deprecation_info["replacement"]}>; rel="alternate"'.encode()
                    ),
                    (
                        b"x-deprecation-migration-url",
                        deprecation_info["migration_url"].encode()
                    ),
                    (
                        b"x-api-warn",
                        f"Endpoint deprecated. {deprecation_info['reason']}. Sunset: {deprecation_info['sunset_date']}".encode()
                    ),
                ])

                # Create new message with updated headers
                message = {**message, "headers": headers}

            await send(message)

        await self.app(scope, receive, send_with_deprecation_headers)
