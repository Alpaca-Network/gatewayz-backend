"""
Staging environment security middleware.

This middleware protects the staging environment by requiring admin authentication
for all routes except health checks and documentation.

Only applies when APP_ENV=staging.

This is a pure ASGI middleware (not BaseHTTPMiddleware) to properly support
streaming responses without the "No response returned" error.
"""

import asyncio
import json
import logging

from starlette.types import ASGIApp, Receive, Scope, Send

from src.config.config import Config
from src.services.user_lookup_cache import get_user

logger = logging.getLogger(__name__)


class StagingSecurityMiddleware:
    """
    Security middleware for staging environment.

    Enforces admin-only access to staging environment:
    - ALL routes except /health require an admin user API key
    - Validates that the provided API key belongs to a user with admin privileges
    - Same API key is used for both staging access and endpoint authentication

    This is a pure ASGI middleware to properly support streaming responses.

    Usage:
        # All endpoints require admin user API key to access staging
        curl https://staging.api.com/v1/chat/completions \\
            -H "Authorization: Bearer <ADMIN_USER_API_KEY>" \\
            -H "Content-Type: application/json" \\
            -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}'
    """

    # Paths that bypass admin authentication (only /health in staging)
    ALLOWED_PATHS = {
        "/health",
    }

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

        # Log security configuration on startup
        if Config.IS_STAGING:
            logger.info(
                "🔒 Staging security enabled: Admin-only access "
                "(all routes require admin user API key except /health)"
            )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only process HTTP requests
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Only apply in staging environment
        if not Config.IS_STAGING:
            await self.app(scope, receive, send)
            return

        path = scope["path"]

        # Skip authentication for allowed paths (health checks, docs)
        if path in self.ALLOWED_PATHS:
            await self.app(scope, receive, send)
            return

        # Extract headers from scope
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()

        if not auth_header.startswith("Bearer "):
            await self._send_access_denied(send, "Missing or invalid Authorization header", scope)
            return

        # Extract the API key
        api_key = auth_header.replace("Bearer ", "").strip()

        if not api_key:
            await self._send_access_denied(send, "Empty API key", scope)
            return

        # Validate that the API key belongs to an admin user
        try:
            # Run get_user in a thread since it's a sync function
            user = await asyncio.to_thread(get_user, api_key)

            if not user:
                logger.warning(
                    "Staging access denied: Invalid API key",
                    extra={
                        "path": path,
                        "ip": self._get_client_ip(scope),
                    },
                )
                await self._send_access_denied(send, "Invalid API key", scope)
                return

            # Check if user has admin privileges
            is_admin = user.get("is_admin", False) or user.get("role") in [
                "admin",
                "superadmin",
            ]

            if not is_admin:
                logger.warning(
                    "Staging access denied: Non-admin user attempted access",
                    extra={
                        "user_id": user.get("id"),
                        "role": user.get("role"),
                        "path": path,
                        "ip": self._get_client_ip(scope),
                    },
                )
                await self._send_access_denied(send, "Admin privileges required", scope)
                return

        except Exception as e:
            logger.error(
                f"Error validating admin access: {e}",
                extra={
                    "path": path,
                    "ip": self._get_client_ip(scope),
                },
            )
            await self._send_access_denied(send, "Authentication error", scope)
            return

        # User is admin - allow request
        # NOTE: We call the app outside the try/except to avoid sending a duplicate
        # http.response.start if the downstream app raises after starting a response
        logger.debug(f"Admin user {user.get('id')} accessing staging: {path}")
        await self.app(scope, receive, send)

    def _get_client_ip(self, scope: Scope) -> str:
        """
        Get the real client IP address.

        Checks X-Forwarded-For header first (for proxied requests),
        then falls back to direct connection IP.
        """
        headers = dict(scope.get("headers", []))

        # Check X-Forwarded-For header (Railway/proxy sets this)
        forwarded_for = headers.get(b"x-forwarded-for", b"").decode()
        if forwarded_for:
            # X-Forwarded-For can be comma-separated, take the first IP
            # Defensive bounds checking to prevent potential issues
            parts = forwarded_for.split(",")
            if parts:  # Defensive check (split always returns at least [''])
                return parts[0].strip()

        # Fall back to direct connection IP
        client = scope.get("client")
        return client[0] if client else "unknown"

    async def _send_access_denied(self, send: Send, reason: str, scope: Scope) -> None:
        """Send access denied response."""
        body = json.dumps(
            {
                "error": "Authentication Required",
                "message": f"Staging environment requires admin user authentication: {reason}",
                "hint": "Use 'Authorization: Bearer <ADMIN_USER_API_KEY>' header with an API key from a user with admin privileges.",
            }
        ).encode()

        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                    (b"x-environment", b"staging"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )
