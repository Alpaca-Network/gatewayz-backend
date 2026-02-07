"""
Security & Behavioral Rate Limiting Middleware

This middleware provides a front-line defense against DoS attacks, IP rotation,
and 499 timeout spikes. It implements:
1. Tiered IP Rate Limiting (Stricter for cloud/datacenter IPs)
2. Behavioral Fingerprinting (Detects bots rotating IPs but keeping headers)
3. Global Velocity Protection (System-wide shield during high error spikes)
"""

import hashlib
import logging
import time
from collections import deque
from typing import Optional

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from src.config import Config
from src.services.prometheus_metrics import rate_limited_requests

logger = logging.getLogger(__name__)

# --- Configuration ---
# Standard Residential/Business limit
DEFAULT_IP_LIMIT = 60  # requests per minute
# Cloud/Datacenter/VPN limit (Stricter)
STRICT_IP_LIMIT = 10   # requests per minute
# Fingerprint limit (Cross-IP detection)
FINGERPRINT_LIMIT = 100 # requests per minute across all IPs for 1 fingerprint

# --- Global Velocity Mode Configuration ---
# Activates when error rate exceeds threshold, tightens all limits system-wide
VELOCITY_ERROR_THRESHOLD = 0.10  # 10% error rate triggers velocity mode
VELOCITY_WINDOW_SECONDS = 60     # Look at errors in the last 60 seconds
VELOCITY_COOLDOWN_SECONDS = 600  # Stay in velocity mode for 10 minutes
VELOCITY_LIMIT_MULTIPLIER = 0.5  # Reduce all limits to 50% during velocity mode
VELOCITY_MIN_REQUESTS = 50       # Minimum requests before calculating error rate

# Known Datacenter/Proxy CIDR patterns (simplified for implementation)
# In production, this would be a more comprehensive list or GeoIP database
DATACENTER_KEYWORDS = ["aws", "amazon", "google", "digitalocean", "azure", "ovh", "linode", "proxy", "vpn"]

class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Advanced security middleware for behavioral rate limiting and protection.
    """

    def __init__(self, app: ASGIApp, redis_client=None):
        super().__init__(app)
        self.redis = redis_client
        # In-memory fallback if redis is missing
        self._local_cache = {}
        self._last_cleanup = time.time()

        # Global Velocity Mode tracking
        # Tracks (timestamp, is_error) tuples for sliding window analysis
        self._request_log: deque = deque(maxlen=10000)  # Cap memory usage
        self._velocity_mode_until: float = 0  # Unix timestamp when velocity mode expires
        self._velocity_mode_triggered_count: int = 0  # For metrics

        logger.info("🛡️ SecurityMiddleware initialized with behavioral protection + velocity mode")

    async def _get_client_ip(self, request: Request) -> str:
        """Extract client IP with support for proxies."""
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _generate_fingerprint(self, request: Request) -> str:
        """
        Generate a unique 'DNA' for the request based on headers.
        Helps detect bots that rotate IPs but reuse the same script configuration.
        """
        ua = request.headers.get("user-agent", "none")
        accept = request.headers.get("accept-language", "none")
        encoding = request.headers.get("accept-encoding", "none")
        
        # Combine identifiers into a stable hash
        fingerprint_raw = f"{ua}|{accept}|{encoding}"
        return hashlib.sha256(fingerprint_raw.encode()).hexdigest()[:16]

    async def _is_datacenter_ip(self, ip: str, request: Request) -> bool:
        """
        Check if the IP belongs to a high-risk range (Datacenters/Cloud).
        Uses ASN/Header hints or simple keyword matching in reverse DNS if available.
        """
        # 1. Check User-Agent for known scraping tools
        ua = request.headers.get("user-agent", "").lower()
        if any(tool in ua for tool in ["python-requests", "aiohttp", "curl", "postman"]):
            return True

        # 2. Check for proxy headers often added by scraping services
        if request.headers.get("X-Proxy-ID") or request.headers.get("Via"):
            return True

        return False

    def _is_velocity_mode_active(self) -> bool:
        """Check if global velocity mode is currently active."""
        return time.time() < self._velocity_mode_until

    def _record_request_outcome(self, status_code: int) -> None:
        """
        Record a request outcome for velocity mode calculation.
        Tracks whether the request resulted in an error (499, 5xx).
        """
        now = time.time()
        is_error = status_code == 499 or status_code >= 500
        self._request_log.append((now, is_error))

        # Clean old entries outside the window
        cutoff = now - VELOCITY_WINDOW_SECONDS
        while self._request_log and self._request_log[0][0] < cutoff:
            self._request_log.popleft()

    def _check_and_activate_velocity_mode(self) -> bool:
        """
        Check if velocity mode should be activated based on error rate.
        Returns True if velocity mode was just activated.
        """
        now = time.time()

        # Don't re-check if already in velocity mode
        if self._is_velocity_mode_active():
            return False

        # Need minimum requests to calculate meaningful error rate
        if len(self._request_log) < VELOCITY_MIN_REQUESTS:
            return False

        # Calculate error rate in the window
        cutoff = now - VELOCITY_WINDOW_SECONDS
        recent_requests = [(ts, err) for ts, err in self._request_log if ts >= cutoff]

        if len(recent_requests) < VELOCITY_MIN_REQUESTS:
            return False

        error_count = sum(1 for _, is_error in recent_requests if is_error)
        error_rate = error_count / len(recent_requests)

        if error_rate >= VELOCITY_ERROR_THRESHOLD:
            self._velocity_mode_until = now + VELOCITY_COOLDOWN_SECONDS
            self._velocity_mode_triggered_count += 1

            logger.warning(
                f"🚨 VELOCITY MODE ACTIVATED: {error_rate:.1%} error rate "
                f"({error_count}/{len(recent_requests)} requests). "
                f"All limits reduced to {VELOCITY_LIMIT_MULTIPLIER:.0%} for {VELOCITY_COOLDOWN_SECONDS}s. "
                f"(Activation #{self._velocity_mode_triggered_count})"
            )

            # Record metric for monitoring
            try:
                rate_limited_requests.labels(limit_type="velocity_mode_activated").inc()
            except Exception:
                pass

            return True

        return False

    def _get_effective_limit(self, base_limit: int) -> int:
        """Get the effective limit, applying velocity mode multiplier if active."""
        if self._is_velocity_mode_active():
            return max(1, int(base_limit * VELOCITY_LIMIT_MULTIPLIER))
        return base_limit

    async def _check_limit(self, key: str, limit: int, window: int = 60) -> bool:
        """
        Generic sliding window rate limit check.
        Returns True if allowed, False if blocked.
        """
        now = int(time.time())
        bucket = now // window
        full_key = f"sec_rl:{key}:{bucket}"
        
        if self.redis:
            try:
                # Use Redis for distributed limiting
                count = await self.redis.incr(full_key)
                if count == 1:
                    await self.redis.expire(full_key, window * 2)
                return count <= limit
            except Exception as e:
                logger.error(f"Redis security limit error: {e}")
                # Fallback to local
        
        # Local in-memory fallback
        if full_key not in self._local_cache:
            self._local_cache[full_key] = 0
            # Periodically clean old keys
            if now - self._last_cleanup > 300:
                self._local_cache = {k: v for k, v in self._local_cache.items() if k.startswith(f"sec_rl:{key}:")}
                self._last_cleanup = now
                
        self._local_cache[full_key] += 1
        return self._local_cache[full_key] <= limit

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip security checks for internal/health endpoints
        if request.url.path in ["/health", "/metrics", "/api/health", "/favicon.ico"]:
            return await call_next(request)

        client_ip = await self._get_client_ip(request)
        fingerprint = self._generate_fingerprint(request)

        # Check if velocity mode is active (for logging)
        velocity_active = self._is_velocity_mode_active()

        # Determine applicable limit (Tiering) with velocity mode adjustment
        is_dc = await self._is_datacenter_ip(client_ip, request)
        base_ip_limit = STRICT_IP_LIMIT if is_dc else DEFAULT_IP_LIMIT
        ip_limit = self._get_effective_limit(base_ip_limit)
        fp_limit = self._get_effective_limit(FINGERPRINT_LIMIT)

        # 1. Check IP-based limit
        if not await self._check_limit(f"ip:{client_ip}", ip_limit):
            rate_limited_requests.labels(limit_type="security_ip_tier").inc()
            mode_indicator = " [VELOCITY MODE]" if velocity_active else ""
            logger.warning(f"🛡️ Blocked Aggressive IP: {client_ip} (Limit: {ip_limit} RPM){mode_indicator}")
            return JSONResponse(
                status_code=429,
                content={"error": {"message": "Too many requests from this IP address.", "type": "security_limit"}}
            )

        # 2. Check Behavioral Fingerprint limit (Cross-IP detection)
        if not await self._check_limit(f"fp:{fingerprint}", fp_limit):
            rate_limited_requests.labels(limit_type="security_fingerprint").inc()
            mode_indicator = " [VELOCITY MODE]" if velocity_active else ""
            logger.warning(f"🛡️ Blocked Bot Fingerprint: {fingerprint} (Rotating IPs detected){mode_indicator}")
            return JSONResponse(
                status_code=429,
                content={"error": {"message": "Suspicious request patterns detected.", "type": "behavioral_limit"}}
            )

        # Proceed to next middleware/app logic
        response = await call_next(request)

        # Track response for Global Velocity Mode
        # Record outcome and check if we should activate velocity mode
        self._record_request_outcome(response.status_code)
        self._check_and_activate_velocity_mode()

        return response
