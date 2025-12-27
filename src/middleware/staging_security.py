"""
Staging environment security middleware.

This middleware protects the staging environment by requiring admin authentication
for all routes except health checks and documentation.

Only applies when APP_ENV=staging.
"""

import asyncio
import logging
import os
import secrets

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette import status

from src.config.config import Config
from src.services.user_lookup_cache import get_user

logger = logging.getLogger(__name__)


class StagingSecurityMiddleware(BaseHTTPMiddleware):
    """
    Security middleware for staging environment.

    Enforces admin-only access to staging environment:
    - ALL routes except /health require an admin user API key
    - Validates that the provided API key belongs to a user with admin privileges
    - Same API key is used for both staging access and endpoint authentication

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

    def __init__(self, app):
        super().__init__(app)

        # Log security configuration on startup
        if Config.APP_ENV == "staging":
            logger.info(
                "🔒 Staging security enabled: Admin-only access "
                "(all routes require admin user API key except /health)"
            )

    async def dispatch(self, request: Request, call_next):
        """Process request and enforce admin-only staging security."""

        # Only apply in staging environment
        if Config.APP_ENV != "staging":
            return await call_next(request)

        # Skip authentication for allowed paths (health checks, docs)
        if request.url.path in self.ALLOWED_PATHS:
            return await call_next(request)

        # Extract Authorization header
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return self._access_denied_response("Missing or invalid Authorization header")

        # Extract the API key
        api_key = auth_header.replace("Bearer ", "").strip()

        if not api_key:
            return self._access_denied_response("Empty API key")

        # Validate that the API key belongs to an admin user
        try:
            # Run get_user in a thread since it's a sync function
            user = await asyncio.to_thread(get_user, api_key)

            if not user:
                logger.warning(
                    "Staging access denied: Invalid API key",
                    extra={
                        "path": request.url.path,
                        "ip": self._get_client_ip(request),
                    },
                )
                return self._access_denied_response("Invalid API key")

            # Check if user has admin privileges
            is_admin = user.get("is_admin", False) or user.get("role") in ["admin", "superadmin"]

            if not is_admin:
                logger.warning(
                    "Staging access denied: Non-admin user attempted access",
                    extra={
                        "user_id": user.get("id"),
                        "role": user.get("role"),
                        "path": request.url.path,
                        "ip": self._get_client_ip(request),
                    },
                )
                return self._access_denied_response("Admin privileges required")

            # User is admin - allow request
            logger.debug(
                f"Admin user {user.get('id')} accessing staging: {request.url.path}"
            )
            return await call_next(request)

        except Exception as e:
            logger.error(
                f"Error validating admin access: {e}",
                extra={
                    "path": request.url.path,
                    "ip": self._get_client_ip(request),
                },
            )
            return self._access_denied_response("Authentication error")

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
                "message": f"Staging environment requires admin user authentication: {reason}",
                "hint": "Use 'Authorization: Bearer <ADMIN_USER_API_KEY>' header with an API key from a user with admin privileges.",
            },
            headers={
                "WWW-Authenticate": "Bearer",
                "X-Environment": "staging",
            },
        )
