"""
Email Verification Service using Emailable API

This module provides email verification functionality to detect:
- Disposable/temporary email addresses
- Invalid email addresses
- Risky email addresses

Usage:
    from src.services.email_verification import EmailVerificationService, EmailVerificationResult

    service = EmailVerificationService()
    result = await service.verify_email("user@example.com")

    if result.should_block:
        # Block registration
    elif result.is_bot:
        # Mark as bot
    else:
        # Allow registration

Environment Variables:
    EMAILABLE_API_KEY - API key from https://app.emailable.com/api
    EMAILABLE_ENABLED - Set to "true" to enable verification (default: false)
    EMAILABLE_TIMEOUT - Request timeout in seconds (default: 5)
"""

import json
import logging
import os
from dataclasses import dataclass
from enum import Enum

import httpx

logger = logging.getLogger(__name__)

# Cache TTL in seconds (1 hour)
EMAIL_VERIFICATION_CACHE_TTL = 3600
EMAIL_VERIFICATION_CACHE_PREFIX = "email_verification:"


def _get_redis_client():
    """Get Redis client instance with error handling."""
    try:
        from src.config.redis_config import get_redis_client

        return get_redis_client()
    except Exception as e:
        logger.warning(f"Failed to get Redis client for email verification cache: {e}")
        return None


class EmailState(str, Enum):  # noqa: UP042
    """Email verification states from Emailable API."""

    DELIVERABLE = "deliverable"
    UNDELIVERABLE = "undeliverable"
    RISKY = "risky"
    UNKNOWN = "unknown"


class EmailReason(str, Enum):  # noqa: UP042
    """Detailed reasons for email verification results."""

    # Deliverable reasons
    ACCEPTED_EMAIL = "accepted_email"

    # Undeliverable reasons
    REJECTED_EMAIL = "rejected_email"
    INVALID_DOMAIN = "invalid_domain"
    INVALID_EMAIL = "invalid_email"
    INVALID_SMTP = "invalid_smtp"
    UNAVAILABLE_SMTP = "unavailable_smtp"

    # Risky reasons
    LOW_QUALITY = "low_quality"
    LOW_DELIVERABILITY = "low_deliverability"
    ACCEPT_ALL = "accept_all"  # Catch-all domain

    # Unknown reasons
    TIMEOUT = "timeout"
    UNEXPECTED_ERROR = "unexpected_error"
    NO_CONNECT = "no_connect"
    THROTTLED = "throttled"

    # Custom (not from API)
    API_ERROR = "api_error"
    NOT_VERIFIED = "not_verified"


@dataclass
class EmailVerificationResult:
    """Result of email verification."""

    email: str
    state: EmailState
    reason: EmailReason
    score: int  # 0-100, higher is better
    is_disposable: bool
    is_free: bool
    is_role: bool  # e.g., info@, support@, admin@
    domain: str
    did_you_mean: str | None = None  # Suggested correction

    @property
    def is_bot(self) -> bool:
        """Determine if this email should be marked as a bot account."""
        # Disposable emails are always bots
        if self.is_disposable:
            return True

        # Risky emails with low score are bots
        if self.state == EmailState.RISKY and self.score < 50:
            return True

        # Unknown emails that timed out - give benefit of doubt
        if self.state == EmailState.UNKNOWN:
            return False

        return False

    @property
    def should_block(self) -> bool:
        """Determine if this email should be blocked from registration."""
        # Block undeliverable emails
        if self.state == EmailState.UNDELIVERABLE:
            return True

        # Block invalid domains/emails
        if self.reason in (
            EmailReason.INVALID_DOMAIN,
            EmailReason.INVALID_EMAIL,
            EmailReason.INVALID_SMTP,
        ):
            return True

        return False

    @property
    def subscription_status(self) -> str:
        """Get the subscription status to assign to this user."""
        if self.is_bot:
            return "bot"
        return "trial"

    def to_cache_dict(self) -> dict:
        """Convert result to dict for caching."""
        return {
            "email": self.email,
            "state": self.state.value,
            "reason": self.reason.value,
            "score": self.score,
            "is_disposable": self.is_disposable,
            "is_free": self.is_free,
            "is_role": self.is_role,
            "domain": self.domain,
            "did_you_mean": self.did_you_mean,
        }

    @classmethod
    def from_cache_dict(cls, data: dict) -> "EmailVerificationResult":
        """Create result from cached dict."""
        return cls(
            email=data["email"],
            state=EmailState(data["state"]),
            reason=EmailReason(data["reason"]),
            score=data["score"],
            is_disposable=data["is_disposable"],
            is_free=data["is_free"],
            is_role=data["is_role"],
            domain=data["domain"],
            did_you_mean=data.get("did_you_mean"),
        )


class EmailVerificationService:
    """Service for verifying email addresses using Emailable API."""

    API_BASE_URL = "https://api.emailable.com/v1"

    def __init__(
        self,
        api_key: str | None = None,
        enabled: bool | None = None,
        timeout: int | None = None,
    ):
        """
        Initialize the email verification service.

        Args:
            api_key: Emailable API key (or set EMAILABLE_API_KEY env var)
            enabled: Whether verification is enabled (or set EMAILABLE_ENABLED env var)
            timeout: Request timeout in seconds (or set EMAILABLE_TIMEOUT env var)
        """
        self.api_key = api_key or os.getenv("EMAILABLE_API_KEY")
        self.enabled = enabled if enabled is not None else os.getenv("EMAILABLE_ENABLED", "false").lower() == "true"
        self.timeout = timeout or int(os.getenv("EMAILABLE_TIMEOUT", "5"))

        if self.enabled and not self.api_key:
            logger.warning("EMAILABLE_ENABLED is true but EMAILABLE_API_KEY is not set. Verification disabled.")
            self.enabled = False

    async def verify_email(self, email: str) -> EmailVerificationResult:
        """
        Verify an email address.

        Args:
            email: Email address to verify

        Returns:
            EmailVerificationResult with verification details
        """
        email = email.lower().strip()
        domain = email.split("@")[-1] if "@" in email else ""

        # If verification is disabled, return a default result
        if not self.enabled:
            logger.debug(f"Email verification disabled, skipping verification for domain={domain}")
            return EmailVerificationResult(
                email=email,
                state=EmailState.UNKNOWN,
                reason=EmailReason.NOT_VERIFIED,
                score=50,  # Neutral score
                is_disposable=False,
                is_free=False,
                is_role=False,
                domain=domain,
            )

        # Check cache first
        cached_result = self._get_cached_result(email)
        if cached_result:
            logger.debug(f"Email verification cache hit for domain={domain}")
            return cached_result

        # Make API call
        result = await self._verify_email_uncached(email, domain)

        # Cache the result (only for successful API calls, not errors)
        if result.reason not in (EmailReason.API_ERROR, EmailReason.THROTTLED, EmailReason.TIMEOUT):
            self._cache_result(email, result)

        return result

    async def _verify_email_uncached(self, email: str, domain: str) -> EmailVerificationResult:
        """Make the actual API call to verify an email (no caching)."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.API_BASE_URL}/verify",
                    params={
                        "email": email,
                        "api_key": self.api_key,
                        "timeout": self.timeout,
                    },
                )

                # Handle rate limiting
                if response.status_code == 429:
                    logger.warning(f"Emailable rate limit exceeded for domain={domain}")
                    return self._create_unknown_result(email, domain, EmailReason.THROTTLED)

                # Handle insufficient credits
                if response.status_code == 402:
                    logger.error("Emailable API credits exhausted")
                    return self._create_unknown_result(email, domain, EmailReason.API_ERROR)

                # Handle other errors
                if response.status_code != 200:
                    logger.error(f"Emailable API error: {response.status_code} for domain={domain}")
                    return self._create_unknown_result(email, domain, EmailReason.API_ERROR)

                data = response.json()
                return self._parse_response(email, data)

        except httpx.TimeoutException:
            logger.warning(f"Emailable API timeout for domain={domain}")
            return self._create_unknown_result(email, domain, EmailReason.TIMEOUT)

        except Exception as e:
            logger.error(f"Emailable API unexpected error for domain={domain}: {type(e).__name__}")
            return self._create_unknown_result(email, domain, EmailReason.UNEXPECTED_ERROR)

    def _get_cached_result(self, email: str) -> EmailVerificationResult | None:
        """Get cached verification result for an email."""
        try:
            redis_client = _get_redis_client()
            if not redis_client:
                return None

            cache_key = f"{EMAIL_VERIFICATION_CACHE_PREFIX}{email.lower()}"
            cached_data = redis_client.get(cache_key)

            if cached_data:
                data = json.loads(cached_data)
                return EmailVerificationResult.from_cache_dict(data)
        except Exception as e:
            logger.warning(f"Failed to get cached email verification result: {e}")
        return None

    def _cache_result(self, email: str, result: EmailVerificationResult) -> bool:
        """Cache verification result for an email."""
        try:
            redis_client = _get_redis_client()
            if not redis_client:
                return False

            cache_key = f"{EMAIL_VERIFICATION_CACHE_PREFIX}{email.lower()}"
            cache_data = json.dumps(result.to_cache_dict())
            redis_client.setex(cache_key, EMAIL_VERIFICATION_CACHE_TTL, cache_data)
            logger.debug(f"Cached email verification result for domain={result.domain}")
            return True
        except Exception as e:
            logger.warning(f"Failed to cache email verification result: {e}")
        return False

    def _parse_response(self, email: str, data: dict) -> EmailVerificationResult:
        """Parse the Emailable API response into a result object."""
        state_str = data.get("state", "unknown").lower()
        reason_str = data.get("reason", "unexpected_error").lower()

        # Map state string to enum
        try:
            state = EmailState(state_str)
        except ValueError:
            logger.warning(f"Unknown email state: {state_str}")
            state = EmailState.UNKNOWN

        # Map reason string to enum
        try:
            reason = EmailReason(reason_str)
        except ValueError:
            logger.warning(f"Unknown email reason: {reason_str}")
            reason = EmailReason.UNEXPECTED_ERROR

        return EmailVerificationResult(
            email=email,
            state=state,
            reason=reason,
            score=data.get("score", 0),
            is_disposable=data.get("disposable", False),
            is_free=data.get("free", False),
            is_role=data.get("role", False),
            domain=data.get("domain", ""),
            did_you_mean=data.get("did_you_mean"),
        )

    def _create_unknown_result(
        self, email: str, domain: str, reason: EmailReason
    ) -> EmailVerificationResult:
        """Create an unknown result for error cases."""
        return EmailVerificationResult(
            email=email,
            state=EmailState.UNKNOWN,
            reason=reason,
            score=50,  # Neutral score - don't penalize for API errors
            is_disposable=False,
            is_free=False,
            is_role=False,
            domain=domain,
        )

    async def is_disposable(self, email: str) -> bool:
        """Quick check if an email is disposable."""
        result = await self.verify_email(email)
        return result.is_disposable

    async def should_block(self, email: str) -> bool:
        """Quick check if an email should be blocked."""
        result = await self.verify_email(email)
        return result.should_block

    async def get_subscription_status(self, email: str) -> str:
        """Get the subscription status for an email."""
        result = await self.verify_email(email)
        return result.subscription_status


# Singleton instance for convenience
_service: EmailVerificationService | None = None


def get_email_verification_service() -> EmailVerificationService:
    """Get or create the singleton email verification service."""
    global _service
    if _service is None:
        _service = EmailVerificationService()
    return _service


async def verify_email(email: str) -> EmailVerificationResult:
    """Convenience function to verify an email using the singleton service."""
    return await get_email_verification_service().verify_email(email)
