"""
Security & Behavioral Rate Limiting Middleware

This middleware provides a front-line defense against DoS attacks, IP rotation,
and 499 timeout spikes. It implements:
1. Tiered IP Rate Limiting (Stricter for cloud/datacenter IPs)
2. Behavioral Fingerprinting (Detects bots rotating IPs but keeping headers)
3. Global Velocity Protection (System-wide shield during high error spikes)
"""

import asyncio
import hashlib
import logging
import time
from collections import Counter, deque

try:
    from redis.exceptions import RedisError as _RedisError
except ImportError:
    _RedisError = OSError  # type: ignore[misc,assignment]

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from src.services.prometheus_metrics import rate_limited_requests

logger = logging.getLogger(__name__)

# --- Configuration ---
# Standard Residential/Business limit
DEFAULT_IP_LIMIT = 300  # requests per minute (was 60 - too low for shared IPs/NAT)
# Cloud/Datacenter/VPN limit (Stricter)
STRICT_IP_LIMIT = 60   # requests per minute (was 10 - too restrictive)
# Fingerprint limit (Cross-IP detection)
FINGERPRINT_LIMIT = 100 # requests per minute across all IPs for 1 fingerprint

# --- Global Velocity Mode Configuration ---
# Activates when error rate exceeds threshold, tightens all limits system-wide
VELOCITY_ERROR_THRESHOLD = 0.25  # 25% error rate triggers velocity mode (was 0.10 - too aggressive)
VELOCITY_WINDOW_SECONDS = 60     # Look at errors in the last 60 seconds
VELOCITY_COOLDOWN_SECONDS = 180  # Stay in velocity mode for 3 minutes (was 600s - too long)
VELOCITY_LIMIT_MULTIPLIER = 0.5  # Reduce all limits to 50% during velocity mode (basic tier)
VELOCITY_MIN_REQUESTS = 100      # Minimum requests before calculating error rate (was 50 - better sample size)

# --- Tiered Velocity Mode Configuration ---
# Different multipliers based on user tier (pro/max users get less restriction)
VELOCITY_TIER_MULTIPLIERS = {
    "basic": 0.5,   # Basic tier: 50% reduction (most restrictive)
    "pro": 0.75,    # Pro tier: 25% reduction
    "max": 0.9,     # MAX tier: 10% reduction
    "admin": 1.0,   # Admin tier: No reduction (bypasses velocity mode)
}

# Known Datacenter/Proxy CIDR patterns (simplified for implementation)
# In production, this would be a more comprehensive list or GeoIP database
DATACENTER_KEYWORDS = ["aws", "amazon", "google", "digitalocean", "azure", "ovh", "linode", "proxy", "vpn"]

class SecurityMiddleware(BaseHTTPMiddleware):
    """
    Advanced security middleware for behavioral rate limiting and protection.

    KNOWN LIMITATION — BaseHTTPMiddleware and SSE/Streaming:
    =========================================================
    This middleware is built on Starlette's BaseHTTPMiddleware, which works by
    awaiting the full response from `call_next` before returning it to the client.
    Under the hood, BaseHTTPMiddleware wraps the response body in an anyio memory
    stream and iterates over it completely, which means:

      1. Streaming responses (Server-Sent Events / SSE, chunked transfer) are fully
         buffered in memory before being forwarded to the client.
      2. The client does not receive any tokens until the *entire* LLM response has
         been generated, effectively breaking the streaming UX.
      3. For very long completions this can also increase memory pressure.

    The correct long-term fix is to rewrite this middleware as a pure ASGI callable
    (no BaseHTTPMiddleware) so that response chunks can be forwarded directly.
    That is intentionally deferred as a larger refactor.

    SHORT-TERM MITIGATION implemented below:
    For requests that are identified as SSE streaming requests (i.e. POST to
    /v1/chat/completions with `stream=true` in the body, or an `Accept:
    text/event-stream` header), we still run all inbound security checks (IP rate
    limit, fingerprint limit) but we call `call_next` and return its response
    immediately without any additional processing of the response body.  This
    restores streaming behaviour for chat endpoints while preserving all
    rate-limiting protection.
    """

    # Paths that may carry SSE / chunked streaming responses.
    STREAMING_PATHS = {
        "/v1/chat/completions",
        "/chat/completions",
        "/v1/responses",
    }

    def __init__(self, app: ASGIApp, redis_client=None):
        super().__init__(app)
        self.redis = redis_client
        # In-memory fallback if redis is missing
        self._local_cache = {}
        self._last_cleanup = time.time()

        # Global Velocity Mode tracking
        # Tracks (timestamp, is_error, status_code) tuples for sliding window analysis
        self._request_log: deque = deque(maxlen=10000)  # Cap memory usage
        self._velocity_mode_until: float = 0  # Unix timestamp when velocity mode expires
        self._velocity_mode_triggered_count: int = 0  # For metrics
        self._current_velocity_event_id: str | None = None  # DB event ID for current activation
        self._last_velocity_check_time: float = 0  # Track last deactivation check

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
        Uses CIDR/ASN-based detection (fast, accurate) + header-based hints.
        """
        # 1. Check CIDR ranges first (fast, accurate)
        # This catches known datacenter IPs like AWS, GCP, Azure, Huawei Cloud, etc.
        from src.services.ip_classification import is_datacenter_ip_fast
        if await is_datacenter_ip_fast(ip):
            return True

        # 2. Check User-Agent for known scraping tools (secondary signal)
        ua = request.headers.get("user-agent", "").lower()
        if any(tool in ua for tool in ["python-requests", "aiohttp", "curl", "postman"]):
            return True

        # 3. Check for proxy headers often added by scraping services (secondary signal)
        if request.headers.get("X-Proxy-ID") or request.headers.get("Via"):
            return True

        return False

    def _is_authenticated_request(self, request: Request) -> bool:
        """
        Check if request has valid authentication (API key or Bearer token).
        Authenticated users should bypass IP-based rate limiting since they're
        already rate-limited by their API key in the application layer.

        Returns:
            True if request appears to have authentication credentials
        """
        auth_header = request.headers.get("Authorization", "")
        if not auth_header:
            return False

        # Check for Bearer token format (JWT tokens, etc.)
        if auth_header.startswith("Bearer ") and len(auth_header) > 20:
            return True

        # Check for API key format (gw_ prefix for Gatewayz keys)
        if auth_header.startswith("gw_") and len(auth_header) > 30:
            return True

        # Check for other API key formats (raw key without Bearer)
        # Most API keys are at least 20 characters
        if len(auth_header) > 20 and not auth_header.startswith("Bearer "):
            return True

        return False

    async def _get_user_tier_from_request(self, request: Request) -> str:
        """
        Get user tier from request (basic, pro, max, admin).
        Returns 'basic' as default for unauthenticated or unknown users.

        Returns:
            User tier string (basic, pro, max, admin)
        """
        try:
            # Try to extract API key from Authorization header
            auth_header = request.headers.get("Authorization", "")
            if not auth_header:
                return "basic"

            # Extract the actual key (remove Bearer prefix if present)
            api_key = auth_header.replace("Bearer ", "").strip()

            # Only try database lookup for valid-looking keys (starts with gw_)
            if not api_key.startswith("gw_"):
                return "basic"

            # Quick database lookup for user tier
            # Import here to avoid circular dependencies
            from src.db.users import get_user

            # Wrap synchronous DB call to avoid blocking the event loop
            user = await asyncio.to_thread(get_user, api_key)
            if user:
                tier = user.get("tier", "basic")
                logger.debug(f"User tier for {api_key[:10]}...: {tier}")
                return tier

            return "basic"

        except (TimeoutError, RuntimeError, OSError) as e:
            # Don't fail the request if tier lookup fails (connection/timeout errors)
            logger.warning(f"Failed to get user tier from request: {e}")
            return "basic"
        except Exception as e:
            # Catch-all for unexpected errors (e.g. malformed DB response)
            logger.warning(f"Unexpected error getting user tier from request: {e}", exc_info=True)
            return "basic"

    def _is_velocity_mode_active(self) -> bool:
        """Check if global velocity mode is currently active."""
        return time.time() < self._velocity_mode_until

    def _record_request_outcome(self, status_code: int, request_duration: float = 0) -> None:
        """
        Record a request outcome for velocity mode calculation.
        Only counts server-side failures (5xx) and sustained client timeouts (499 > 5s).

        Args:
            status_code: HTTP status code of the response
            request_duration: How long the request took in seconds
        """
        now = time.time()

        # Only count true server errors:
        # - 5xx: Server errors (our fault)
        # - 499: Client timeout, but only if request took >5s (likely our slowness)
        # Note: 4xx errors are client's fault (bad auth, invalid request, etc.) - don't trigger velocity mode
        is_error = False
        if status_code >= 500:
            is_error = True
        elif status_code == 499 and request_duration > 5.0:
            # Only count 499 if request was slow on our end (>5s indicates server slowness)
            is_error = True

        self._request_log.append((now, is_error, status_code))

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
        recent_requests = [(ts, err, status) for ts, err, status in self._request_log if ts >= cutoff]

        if len(recent_requests) < VELOCITY_MIN_REQUESTS:
            return False

        error_count = sum(1 for _, is_error, _ in recent_requests if is_error)
        error_rate = error_count / len(recent_requests)

        if error_rate >= VELOCITY_ERROR_THRESHOLD:
            self._velocity_mode_until = now + VELOCITY_COOLDOWN_SECONDS
            self._velocity_mode_triggered_count += 1

            # Calculate error breakdown by status code
            error_statuses = [status for _, is_error, status in recent_requests if is_error]
            error_details = dict(Counter(error_statuses))

            # Format error breakdown for logging
            error_breakdown = ", ".join([f"{code}: {count}" for code, count in sorted(error_details.items())])

            logger.warning(
                f"🚨 VELOCITY MODE ACTIVATED: {error_rate:.1%} error rate "
                f"({error_count}/{len(recent_requests)} requests). "
                f"Error breakdown by status code: [{error_breakdown}]. "
                f"All limits reduced to {VELOCITY_LIMIT_MULTIPLIER:.0%} for {VELOCITY_COOLDOWN_SECONDS}s. "
                f"(Activation #{self._velocity_mode_triggered_count})"
            )

            # Record metrics for monitoring
            try:
                from src.services.prometheus_metrics import record_velocity_mode_activation

                record_velocity_mode_activation(
                    error_rate=error_rate,
                    total_requests=len(recent_requests),
                    error_count=error_count,
                )
                rate_limited_requests.labels(limit_type="velocity_mode_activated").inc()
            except ImportError as e:
                logger.warning(f"Prometheus metrics module unavailable, skipping velocity activation metrics: {e}")
            except RuntimeError as e:
                logger.warning(f"Failed to record velocity mode activation metrics: {e}")

            # Log to database (async operation, don't block on it)
            try:
                import asyncio
                from src.db.velocity_mode_events import create_velocity_event

                def _log_event():
                    event = create_velocity_event(
                        error_rate=error_rate,
                        total_requests=len(recent_requests),
                        error_count=error_count,
                        error_details=error_details,
                        trigger_reason="error_threshold_exceeded",
                        metadata={
                            "activation_count": self._velocity_mode_triggered_count,
                            "cooldown_seconds": VELOCITY_COOLDOWN_SECONDS,
                            "threshold": VELOCITY_ERROR_THRESHOLD,
                        },
                    )
                    if event:
                        self._current_velocity_event_id = event.get("id")

                # Run in thread pool to avoid blocking
                asyncio.create_task(asyncio.to_thread(_log_event))
            except ImportError as e:
                logger.warning(f"DB module unavailable, skipping velocity mode event log: {e}")
            except RuntimeError as e:
                logger.warning(f"Failed to schedule velocity mode event log: {e}")

            return True

        return False

    def _check_velocity_mode_deactivation(self) -> None:
        """
        Check if velocity mode has expired and log deactivation to database.
        Only checks periodically to avoid excessive database calls.
        """
        now = time.time()

        # Only check every 10 seconds to reduce overhead
        if now - self._last_velocity_check_time < 10:
            return

        self._last_velocity_check_time = now

        # If we have an active event and velocity mode has expired, deactivate it
        if self._current_velocity_event_id and not self._is_velocity_mode_active():
            try:
                import asyncio
                from src.db.velocity_mode_events import deactivate_velocity_event
                from src.services.prometheus_metrics import record_velocity_mode_deactivation

                # Calculate duration (approximation, actual duration is in DB)
                duration = VELOCITY_COOLDOWN_SECONDS

                # Record Prometheus metrics
                try:
                    record_velocity_mode_deactivation(duration)
                except (ImportError, RuntimeError) as e:
                    logger.warning(f"Failed to record velocity mode deactivation metrics: {e}")

                def _deactivate_event():
                    deactivate_velocity_event(self._current_velocity_event_id)
                    logger.info(f"✅ VELOCITY MODE DEACTIVATED (event: {self._current_velocity_event_id})")

                # Run in thread pool to avoid blocking
                asyncio.create_task(asyncio.to_thread(_deactivate_event))
                self._current_velocity_event_id = None
            except ImportError as e:
                logger.warning(f"DB/metrics module unavailable, skipping velocity mode deactivation log: {e}")
            except RuntimeError as e:
                logger.warning(f"Failed to schedule velocity mode deactivation: {e}")

    def _get_effective_limit(self, base_limit: int, user_tier: str = "basic") -> int:
        """
        Get the effective limit, applying tier-based velocity mode multiplier if active.

        Args:
            base_limit: Base rate limit (requests per minute)
            user_tier: User's subscription tier (basic, pro, max, admin)

        Returns:
            Effective rate limit after applying velocity mode multiplier
        """
        if self._is_velocity_mode_active():
            # Get tier-specific multiplier (defaults to basic tier if unknown)
            multiplier = VELOCITY_TIER_MULTIPLIERS.get(user_tier, VELOCITY_LIMIT_MULTIPLIER)
            effective_limit = max(1, int(base_limit * multiplier))

            logger.debug(
                f"Velocity mode active: tier={user_tier}, "
                f"base_limit={base_limit}, multiplier={multiplier}, "
                f"effective_limit={effective_limit}"
            )

            return effective_limit

        return base_limit

    def _is_streaming_request(self, request: Request) -> bool:
        """
        Heuristically detect whether this request expects a streaming (SSE) response.

        Detection rules (any one is sufficient):
          1. The path is a known streaming endpoint AND the query-string contains
             ``stream=true`` (clients that pass stream as a query param).
          2. The path is a known streaming endpoint AND the ``Accept`` header
             contains ``text/event-stream``.

        Note: We intentionally do NOT parse the JSON request body here — doing so
        would consume the body stream and break the downstream handler.  Instead we
        rely on the query-string and headers, which are always present for well-
        behaved streaming clients (e.g. the OpenAI SDK sets Accept: text/event-stream
        when stream=True).  Clients that pass ``stream`` only in the JSON body will
        still be buffered; that is an acceptable trade-off for this lightweight fix.
        """
        if request.url.path not in self.STREAMING_PATHS:
            return False

        # Check Accept header (most reliable signal — OpenAI SDK always sets this)
        accept = request.headers.get("Accept", "")
        if "text/event-stream" in accept:
            return True

        # Check query-string fallback (less common but valid)
        if request.query_params.get("stream", "").lower() == "true":
            return True

        return False

    def _validate_content_type(self, request: Request) -> JSONResponse | None:
        """
        Validate the Content-Type header for mutating requests on /v1/* endpoints.

        Rules (MW-M3):
        - Only applies to POST, PUT, and PATCH methods on paths under /v1/.
        - If the Content-Type header is absent the request is allowed through
          unchanged (backwards compatibility with clients that omit it).
        - If the Content-Type header is present it must be one of:
            - application/json               (standard API calls)
            - multipart/form-data            (audio/image file uploads)
            - application/x-www-form-urlencoded  (form submissions)
        - Any other value causes an immediate HTTP 415 Unsupported Media Type
          response before the request reaches any route handler.

        Returns:
            None if the request passes validation.
            A JSONResponse(415) if the Content-Type is present but unsupported.
        """
        if request.method not in ("POST", "PUT", "PATCH"):
            return None

        if not request.url.path.startswith("/v1/"):
            return None

        content_type_raw = request.headers.get("content-type", "")
        if not content_type_raw:
            # Missing Content-Type — pass through for backwards compatibility
            return None

        # Strip parameters such as "; charset=utf-8" or "; boundary=----..."
        content_type = content_type_raw.split(";")[0].strip().lower()

        ALLOWED_CONTENT_TYPES = {
            "application/json",
            "multipart/form-data",
            "application/x-www-form-urlencoded",
        }

        if content_type not in ALLOWED_CONTENT_TYPES:
            logger.warning(
                "Rejected unsupported Content-Type '%s' for %s %s",
                content_type_raw,
                request.method,
                request.url.path,
            )
            return JSONResponse(
                status_code=415,
                content={
                    "error": {
                        "message": (
                            f"Unsupported Media Type: '{content_type_raw}'. "
                            "Content-Type must be 'application/json' or 'multipart/form-data'."
                        ),
                        "type": "unsupported_media_type",
                        "code": 415,
                    }
                },
                headers={"Accept": "application/json, multipart/form-data"},
            )

        return None

    async def _check_limit(self, key: str, limit: int, window: int = 60) -> bool:
        """
        Generic sliding window rate limit check.
        Returns True if allowed, False if blocked.

        Note: Redis client is synchronous, so we use asyncio.to_thread to avoid
        blocking the event loop.
        """
        now = int(time.time())
        bucket = now // window
        full_key = f"sec_rl:{key}:{bucket}"

        if self.redis:
            try:
                # Use Redis for distributed limiting
                # Wrap synchronous Redis calls in to_thread to avoid blocking event loop
                count = await asyncio.to_thread(self.redis.incr, full_key)
                if count == 1:
                    await asyncio.to_thread(self.redis.expire, full_key, window * 2)
                return count <= limit
            except _RedisError as e:
                logger.error(f"Redis security limit error (falling back to local): {e}")
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

        # MW-M3: Validate Content-Type for mutating requests on /v1/* endpoints.
        # Returns 415 if Content-Type is present but not application/json or
        # multipart/form-data. Missing Content-Type is allowed for backwards
        # compatibility.
        content_type_error = self._validate_content_type(request)
        if content_type_error is not None:
            return content_type_error

        client_ip = await self._get_client_ip(request)

        # Check if IP is whitelisted (bypasses all rate limiting)
        try:
            from src.db.ip_whitelist import is_ip_whitelisted

            # TODO: Extract user_id from request if available for user-specific whitelists
            # For now, only check global whitelists
            if await asyncio.to_thread(is_ip_whitelisted, ip_address=client_ip, user_id=None):
                logger.debug(f"🟢 IP {client_ip} is whitelisted - bypassing rate limiting")
                # Still track response for velocity mode statistics
                start_time = time.time()
                response = await call_next(request)
                request_duration = time.time() - start_time
                self._record_request_outcome(response.status_code, request_duration)
                self._check_and_activate_velocity_mode()
                self._check_velocity_mode_deactivation()
                return response
        except (ImportError, OSError, RuntimeError) as e:
            # Don't fail the request if whitelist check fails - just log and continue
            logger.warning(f"IP whitelist check failed for {client_ip}: {e}")
        except Exception as e:
            # Catch-all for unexpected errors from the whitelist DB lookup
            logger.error(f"Unexpected error in IP whitelist check for {client_ip}: {e}", exc_info=True)

        fingerprint = self._generate_fingerprint(request)

        # Check if velocity mode is active (for logging)
        velocity_active = self._is_velocity_mode_active()

        # Check if request is authenticated (has API key or Bearer token)
        is_authenticated = self._is_authenticated_request(request)

        # Get user tier for tiered velocity mode (basic, pro, max, admin)
        user_tier = await self._get_user_tier_from_request(request)

        # Determine applicable limit (Tiering) with velocity mode adjustment
        is_dc = await self._is_datacenter_ip(client_ip, request)
        base_ip_limit = STRICT_IP_LIMIT if is_dc else DEFAULT_IP_LIMIT
        ip_limit = self._get_effective_limit(base_ip_limit, user_tier)
        fp_limit = self._get_effective_limit(FINGERPRINT_LIMIT, user_tier)

        # 1. Check IP-based limit (skip for authenticated users - they have API key rate limiting)
        if not is_authenticated and not await self._check_limit(f"ip:{client_ip}", ip_limit):
            rate_limited_requests.labels(limit_type="security_ip_tier").inc()
            mode_indicator = " [VELOCITY MODE]" if velocity_active else ""
            logger.warning(f"🛡️ Blocked Aggressive IP: {client_ip} (Limit: {ip_limit} RPM){mode_indicator}")

            _ip_reset_ts = str(int(time.time()) + 60)
            headers = {
                # IETF draft standard headers (RateLimit-*); RateLimit-Reset is seconds until reset
                "RateLimit-Limit": str(ip_limit),
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": "60",
                # Retry-After (RFC 7231)
                "Retry-After": "60",
                # Legacy X-RateLimit-* headers kept for backwards compatibility
                "X-RateLimit-Limit": str(ip_limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": _ip_reset_ts,
                "X-RateLimit-Reason": "ip_limit" if not velocity_active else "velocity_mode_ip_limit",
                "X-RateLimit-Mode": "velocity" if velocity_active else "normal",
                "X-Velocity-Mode-Active": str(velocity_active).lower(),
                "X-User-Tier": user_tier,
            }

            if velocity_active:
                headers["X-Velocity-Mode-Until"] = str(int(self._velocity_mode_until))
                headers["X-Velocity-Mode-Multiplier"] = str(VELOCITY_TIER_MULTIPLIERS.get(user_tier, VELOCITY_LIMIT_MULTIPLIER))

            return JSONResponse(
                status_code=429,
                content={"error": {"message": "Too many requests from this IP address.", "type": "security_limit"}},
                headers=headers
            )

        # 2. Check Behavioral Fingerprint limit (Cross-IP detection)
        #    Skip for authenticated users — they are already rate-limited by API key
        if not is_authenticated and not await self._check_limit(f"fp:{fingerprint}", fp_limit):
            rate_limited_requests.labels(limit_type="security_fingerprint").inc()
            mode_indicator = " [VELOCITY MODE]" if velocity_active else ""
            logger.warning(f"🛡️ Blocked Bot Fingerprint: {fingerprint} (Rotating IPs detected){mode_indicator}")

            _fp_reset_ts = str(int(time.time()) + 60)
            headers = {
                # IETF draft standard headers (RateLimit-*); RateLimit-Reset is seconds until reset
                "RateLimit-Limit": str(fp_limit),
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": "60",
                # Retry-After (RFC 7231)
                "Retry-After": "60",
                # Legacy X-RateLimit-* headers kept for backwards compatibility
                "X-RateLimit-Limit": str(fp_limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": _fp_reset_ts,
                "X-RateLimit-Reason": "fingerprint_limit" if not velocity_active else "velocity_mode_fingerprint_limit",
                "X-RateLimit-Mode": "velocity" if velocity_active else "normal",
                "X-Velocity-Mode-Active": str(velocity_active).lower(),
                "X-User-Tier": user_tier,
            }

            if velocity_active:
                headers["X-Velocity-Mode-Until"] = str(int(self._velocity_mode_until))
                headers["X-Velocity-Mode-Multiplier"] = str(VELOCITY_TIER_MULTIPLIERS.get(user_tier, VELOCITY_LIMIT_MULTIPLIER))

            return JSONResponse(
                status_code=429,
                content={"error": {"message": "Suspicious request patterns detected.", "type": "behavioral_limit"}},
                headers=headers
            )

        # --- SSE / Streaming bypass ---
        # BaseHTTPMiddleware buffers the entire response body before forwarding it
        # to the client, which breaks Server-Sent Events (SSE) streaming.  For
        # detected streaming requests we skip post-response processing and return
        # the response object directly so Starlette can stream chunks as they
        # arrive.  All inbound security checks above have already been applied.
        # See the class docstring for a full explanation of this limitation.
        if self._is_streaming_request(request):
            return await call_next(request)

        # Proceed to next middleware/app logic
        start_time = time.time()
        response = await call_next(request)
        request_duration = time.time() - start_time

        # Track response for Global Velocity Mode
        # Record outcome and check if we should activate velocity mode
        self._record_request_outcome(response.status_code, request_duration)
        self._check_and_activate_velocity_mode()

        # Check if velocity mode should be deactivated and logged
        self._check_velocity_mode_deactivation()

        return response
