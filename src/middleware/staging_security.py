"""
Staging environment security middleware.

This middleware protects the staging/test environment from unauthorized access.
It can enforce:
- Custom authentication token (X-Staging-Access-Token header)
- IP whitelisting
- Combined security checks

Only applies when APP_ENV=staging.
"""

import logging
import os
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette import status

from src.config.config import Config

logger = logging.getLogger(__name__)


class StagingSecurityMiddleware(BaseHTTPMiddleware):
    """
    Security middleware for staging environment.

    Provides multiple layers of protection:
    1. IP whitelisting (if STAGING_ALLOWED_IPS is set)
    2. Access token verification (if STAGING_ACCESS_TOKEN is set)

    Configuration via environment variables:
    - STAGING_ACCESS_TOKEN: Required token in X-Staging-Access-Token header
    - STAGING_ALLOWED_IPS: Comma-separated list of allowed IP addresses

    Examples:
        # Via Railway CLI
        railway variables set STAGING_ACCESS_TOKEN="staging_abc123..."
        railway variables set STAGING_ALLOWED_IPS="203.0.113.1,198.51.100.1"

        # Usage
        curl https://staging.api.com/v1/models \\
            -H "X-Staging-Access-Token: staging_abc123..."
    """

    # Paths that bypass security checks
    ALLOWED_PATHS = {"/health", "/", "/ping", "/docs", "/redoc", "/openapi.json"}

    def __init__(self, app):
        super().__init__(app)
        self.staging_token = os.getenv("STAGING_ACCESS_TOKEN")
        self.allowed_ips = self._parse_allowed_ips()

        # Log security configuration on startup
        if Config.APP_ENV == "staging":
            security_features = []
            if self.staging_token:
                security_features.append("Access Token")
            if self.allowed_ips:
                security_features.append(f"IP Whitelist ({len(self.allowed_ips)} IPs)")

            if security_features:
                logger.info(f"Staging security enabled: {', '.join(security_features)}")
            else:
                logger.warning(
                    "Staging security not configured! "
                    "Set STAGING_ACCESS_TOKEN or STAGING_ALLOWED_IPS to protect staging."
                )

    def _parse_allowed_ips(self) -> Optional[set[str]]:
        """Parse allowed IPs from environment variable."""
        ips_str = os.getenv("STAGING_ALLOWED_IPS", "").strip()
        if not ips_str:
            return None

        # Split by comma and clean up
        ips = {ip.strip() for ip in ips_str.split(",") if ip.strip()}
        return ips if ips else None

    async def dispatch(self, request: Request, call_next):
        """Process request and enforce staging security."""

        # Only apply in staging environment
        if Config.APP_ENV != "staging":
            return await call_next(request)

        # Skip security for allowed paths (health checks, docs)
        if request.url.path in self.ALLOWED_PATHS:
            return await call_next(request)

        # Check 1: IP Whitelist (if configured)
        if self.allowed_ips:
            client_ip = self._get_client_ip(request)
            if client_ip not in self.allowed_ips:
                logger.warning(
                    f"Staging access denied: IP not whitelisted",
                    extra={
                        "client_ip": client_ip,
                        "path": request.url.path,
                        "user_agent": request.headers.get("user-agent", "unknown"),
                    },
                )
                return self._access_denied_response(
                    reason="IP address not whitelisted",
                    client_ip=client_ip,
                )

        # Check 2: Access Token (if configured)
        if self.staging_token:
            auth_header = request.headers.get("X-Staging-Access-Token")

            if not auth_header:
                logger.warning(
                    f"Staging access denied: Missing access token",
                    extra={
                        "client_ip": self._get_client_ip(request),
                        "path": request.url.path,
                    },
                )
                return self._access_denied_response(
                    reason="Missing X-Staging-Access-Token header"
                )

            if auth_header != self.staging_token:
                logger.warning(
                    f"Staging access denied: Invalid access token",
                    extra={
                        "client_ip": self._get_client_ip(request),
                        "path": request.url.path,
                        "token_prefix": auth_header[:10] + "..." if len(auth_header) > 10 else auth_header,
                    },
                )
                return self._access_denied_response(reason="Invalid access token")

        # All security checks passed
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

    def _access_denied_response(
        self, reason: str, client_ip: Optional[str] = None
    ) -> JSONResponse:
        """Return access denied response."""
        content = {
            "error": "Staging Access Denied",
            "message": f"Access to this staging/test environment is restricted: {reason}",
            "environment": "staging",
            "hint": "This is a test environment. Contact your team administrator for access credentials.",
        }

        # Include client IP in development mode (helps with debugging)
        if client_ip and not Config.IS_PRODUCTION:
            content["your_ip"] = client_ip

        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=content,
            headers={
                "X-Environment": "staging",
                "X-Access-Denied-Reason": reason,
            },
        )
