"""
Staging environment security middleware.

This middleware protects the staging environment by requiring admin authentication
for all routes except health checks and documentation.

Only applies when APP_ENV=staging.
"""

import logging
import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette import status

from src.config.config import Config

logger = logging.getLogger(__name__)


class StagingSecurityMiddleware(BaseHTTPMiddleware):
    """
    Security middleware for staging environment.

    Enforces admin-only access to staging environment:
    - ALL routes except /health require ADMIN_API_KEY to access the backend
    - This is a gateway layer - user routes still require valid user API keys after passing this check
    - Uses ADMIN_API_KEY for verification (same as admin endpoints)

    Usage:
        # All endpoints require admin key to access in staging
        curl https://staging.api.com/v1/chat/completions \\
            -H "Authorization: Bearer <ADMIN_API_KEY>" \\
            -H "Content-Type: application/json" \\
            -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}'
    """

    # Paths that bypass admin authentication (only /health in staging)
    ALLOWED_PATHS = {
        "/health",
    }

    def __init__(self, app):
        super().__init__(app)
        self.admin_api_key = os.getenv("ADMIN_API_KEY")

        # Log security configuration on startup
        if Config.APP_ENV == "staging":
            if self.admin_api_key:
                logger.info(
                    "🔒 Staging security enabled: Admin-only access "
                    "(all routes require ADMIN_API_KEY except /health)"
                )
            else:
                logger.warning(
                    "⚠️  Staging security WARNING: ADMIN_API_KEY not set! "
                    "Staging environment is currently open to everyone."
                )

    async def dispatch(self, request: Request, call_next):
        """Process request and enforce admin-only staging security."""

        # Only apply in staging environment
        if Config.APP_ENV != "staging":
            return await call_next(request)

        # Skip authentication for allowed paths (health checks, docs)
        if request.url.path in self.ALLOWED_PATHS:
            return await call_next(request)

        # Require admin authentication for all other routes in staging
        if not self.admin_api_key:
            # No admin key configured - allow through (but log warning)
            logger.warning(f"Staging access without admin key check: {request.url.path}")
            return await call_next(request)

        # Extract Authorization header
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return self._access_denied_response("Missing or invalid Authorization header")

        # Extract the token
        provided_key = auth_header.replace("Bearer ", "").strip()

        # Validate admin key using constant-time comparison
        if not provided_key or not secrets.compare_digest(provided_key, self.admin_api_key):
            logger.warning(
                f"Staging access denied: Invalid admin key",
                extra={
                    "path": request.url.path,
                    "ip": self._get_client_ip(request),
                },
            )
            return self._access_denied_response("Invalid admin API key")

        # Admin key valid - allow request
        return await call_next(request)

    def _get_client_ip(self, request: Request) -> str:
        """
        Get the real client IP address.

        Checks X-Forwarded-For header first (for proxied requests),
        then falls back to direct connection IP.
        """
        # Check X-Forwarded-For header (Railway/proxy sets this)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # X-Forwarded-For can be comma-separated, take the first IP
            return forwarded_for.split(",")[0].strip()

        # Fall back to direct connection IP
        return request.client.host if request.client else "unknown"

    def _access_denied_response(self, reason: str) -> JSONResponse:
        """Return access denied response."""
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={
                "error": "Authentication Required",
                "message": f"Staging environment requires admin authentication: {reason}",
                "hint": "Use 'Authorization: Bearer <ADMIN_API_KEY>' header to access staging.",
            },
            headers={
                "WWW-Authenticate": "Bearer",
                "X-Environment": "staging",
            },
        )
