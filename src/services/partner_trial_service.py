"""
Partner Trial Service - Handles partner-specific trial logic

This service manages extended trials for partner signups (e.g., Redbeard 14-day Pro trial).
Partner trials offer different trial durations, tiers, and credit limits compared to
the standard 3-day basic trial.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.config.supabase_config import get_supabase_client

logger = logging.getLogger(__name__)


class PartnerTrialService:
    """Service for managing partner-specific trials like Redbeard"""

    # Cache partner configs for 5 minutes to reduce DB queries
    _config_cache: dict[str, tuple[dict[str, Any], datetime]] = {}
    CACHE_TTL_SECONDS = 300

    @classmethod
    def _get_cached_config(cls, partner_code: str) -> dict[str, Any] | None:
        """Get partner config from cache if not expired"""
        cache_key = partner_code.upper()
        if cache_key in cls._config_cache:
            config, cached_at = cls._config_cache[cache_key]
            if (datetime.now(UTC) - cached_at).total_seconds() < cls.CACHE_TTL_SECONDS:
                return config
            # Cache expired, remove it
            del cls._config_cache[cache_key]
        return None

    @classmethod
    def _set_cached_config(cls, partner_code: str, config: dict[str, Any]) -> None:
        """Cache partner config"""
        cls._config_cache[partner_code.upper()] = (config, datetime.now(UTC))

    @classmethod
    def invalidate_cache(cls, partner_code: str | None = None) -> None:
        """Invalidate cache for a specific partner or all partners"""
        if partner_code:
            cls._config_cache.pop(partner_code.upper(), None)
        else:
            cls._config_cache.clear()

    @staticmethod
    def get_partner_config(partner_code: str) -> dict[str, Any] | None:
        """
        Fetch partner trial configuration.

        Args:
            partner_code: Partner identifier (e.g., 'REDBEARD')

        Returns:
            Partner configuration dict or None if not found/inactive
        """
        if not partner_code:
            return None

        partner_code = partner_code.upper()

        # Check cache first
        cached = PartnerTrialService._get_cached_config(partner_code)
        if cached is not None:
            return cached

        try:
            client = get_supabase_client()
            result = (
                client.table("partner_trials")
                .select("*")
                .eq("partner_code", partner_code)
                .eq("is_active", True)
                .limit(1)
                .execute()
            )

            if result.data and len(result.data) > 0:
                config = result.data[0]
                PartnerTrialService._set_cached_config(partner_code, config)
                return config

            return None

        except Exception as e:
            logger.error(f"Error fetching partner config for {partner_code}: {e}")
            return None

    @staticmethod
    def is_partner_code(code: str) -> bool:
        """
        Check if a code is a valid partner code (vs a user referral code).

        Partner codes are configured in the partner_trials table.
        User referral codes are 8-character alphanumeric codes stored on users.

        Args:
            code: The code to check

        Returns:
            True if it's a partner code, False otherwise
        """
        if not code:
            return False
        config = PartnerTrialService.get_partner_config(code)
        return config is not None

    @staticmethod
    def start_partner_trial(
        user_id: int,
        api_key: str,
        partner_code: str,
        signup_source: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Start a partner-specific trial for a user.

        This applies the partner's trial configuration (duration, tier, credits)
        to the user and their API key.

        Args:
            user_id: User's ID
            api_key: User's API key
            partner_code: Partner identifier (e.g., 'REDBEARD')
            signup_source: UTM or referral source tracking
            metadata: Additional tracking metadata

        Returns:
            Dict with trial details

        Raises:
            ValueError: If partner not found or user already has active trial
        """
        partner_code = partner_code.upper()
        partner_config = PartnerTrialService.get_partner_config(partner_code)

        if not partner_config:
            raise ValueError(f"Partner '{partner_code}' not found or inactive")

        try:
            client = get_supabase_client()
            now = datetime.now(UTC)
            trial_end = now + timedelta(days=partner_config["trial_duration_days"])

            # Update user with partner trial info
            user_update = {
                "partner_code": partner_code,
                "partner_trial_id": partner_config["id"],
                "partner_signup_timestamp": now.isoformat(),
                "partner_metadata": metadata or {},
                "subscription_status": "trial",
                "tier": partner_config["trial_tier"],
                "trial_expires_at": trial_end.isoformat(),
                "credits": float(partner_config["trial_credits_usd"]),
            }

            client.table("users").update(user_update).eq("id", user_id).execute()

            # Update API key with partner trial info
            api_key_update = {
                "is_trial": True,
                "trial_start_date": now.isoformat(),
                "trial_end_date": trial_end.isoformat(),
                "trial_credits": float(partner_config["trial_credits_usd"]),
                "trial_max_tokens": partner_config["trial_max_tokens"],
                "trial_max_requests": partner_config["trial_max_requests"],
                "trial_used_tokens": 0,
                "trial_used_requests": 0,
                "trial_used_credits": 0,
                "trial_converted": False,
                "partner_code": partner_code,
                "partner_trial_tier": partner_config["trial_tier"],
                "partner_trial_credits": float(partner_config["trial_credits_usd"]),
            }

            client.table("api_keys_new").update(api_key_update).eq("api_key", api_key).execute()

            # Create analytics record
            analytics_record = {
                "partner_code": partner_code,
                "user_id": user_id,
                "trial_started_at": now.isoformat(),
                "trial_expires_at": trial_end.isoformat(),
                "trial_status": "active",
                "signup_source": signup_source,
                "metadata": metadata or {},
            }

            client.table("partner_trial_analytics").insert(analytics_record).execute()

            logger.info(
                f"Started {partner_code} trial for user {user_id}: "
                f"{partner_config['trial_duration_days']} days, "
                f"${partner_config['trial_credits_usd']} credits, "
                f"{partner_config['trial_tier']} tier"
            )

            return {
                "success": True,
                "partner_code": partner_code,
                "partner_name": partner_config["partner_name"],
                "trial_tier": partner_config["trial_tier"],
                "trial_credits_usd": float(partner_config["trial_credits_usd"]),
                "trial_duration_days": partner_config["trial_duration_days"],
                "trial_expires_at": trial_end.isoformat(),
                "daily_usage_limit_usd": float(partner_config["daily_usage_limit_usd"]),
            }

        except Exception as e:
            logger.error(f"Error starting partner trial for user {user_id}: {e}")
            raise

    @staticmethod
    def get_partner_trial_status(user_id: int) -> dict[str, Any]:
        """
        Check the status of a user's partner trial.

        Args:
            user_id: User's ID

        Returns:
            Dict with trial status details
        """
        try:
            client = get_supabase_client()

            result = (
                client.table("partner_trial_analytics")
                .select("*")
                .eq("user_id", user_id)
                .order("trial_started_at", desc=True)
                .limit(1)
                .execute()
            )

            if not result.data:
                return {"has_partner_trial": False}

            trial = result.data[0]
            now = datetime.now(UTC)

            # Parse trial expiration
            expires_at_str = trial["trial_expires_at"]
            if expires_at_str:
                if expires_at_str.endswith("Z"):
                    expires_at_str = expires_at_str.replace("Z", "+00:00")
                expires_at = datetime.fromisoformat(expires_at_str)
            else:
                expires_at = now

            is_expired = now > expires_at
            days_remaining = max(0, (expires_at - now).days) if not is_expired else 0

            return {
                "has_partner_trial": True,
                "partner_code": trial["partner_code"],
                "trial_status": trial["trial_status"],
                "is_expired": is_expired,
                "days_remaining": days_remaining,
                "trial_started_at": trial["trial_started_at"],
                "trial_expires_at": trial["trial_expires_at"],
                "credits_used": float(trial["total_credits_used"] or 0),
                "tokens_used": trial["total_tokens_used"] or 0,
                "requests_made": trial["total_requests_made"] or 0,
                "converted": trial["trial_status"] == "converted",
            }

        except Exception as e:
            logger.error(f"Error getting partner trial status for user {user_id}: {e}")
            return {"has_partner_trial": False, "error": str(e)}

    @staticmethod
    def get_user_daily_limit(user_id: int) -> float:
        """
        Get the daily usage limit for a user, considering partner trials.

        Args:
            user_id: User's ID

        Returns:
            Daily limit in USD (float('inf') for unlimited)
        """
        try:
            client = get_supabase_client()

            # Get user info
            user_result = (
                client.table("users")
                .select("partner_code, subscription_status, tier")
                .eq("id", user_id)
                .limit(1)
                .execute()
            )

            if not user_result.data:
                return 1.00  # Default $1/day for unknown users

            user = user_result.data[0]

            # Paid subscribers have no daily limit
            if user.get("subscription_status") == "active":
                return float("inf")

            # Check for partner-specific limit
            partner_code = user.get("partner_code")
            if partner_code:
                partner_config = PartnerTrialService.get_partner_config(partner_code)
                if partner_config:
                    return float(partner_config["daily_usage_limit_usd"])

            # Default for standard trial users
            return 1.00

        except Exception as e:
            logger.error(f"Error getting daily limit for user {user_id}: {e}")
            return 1.00  # Safe default

    @staticmethod
    def convert_partner_trial(
        user_id: int,
        new_tier: str,
        stripe_subscription_id: str,
        revenue_usd: float,
    ) -> dict[str, Any]:
        """
        Convert a partner trial to a paid subscription.

        Args:
            user_id: User's ID
            new_tier: Tier being subscribed to
            stripe_subscription_id: Stripe subscription ID
            revenue_usd: Monthly revenue amount

        Returns:
            Conversion result dict
        """
        try:
            client = get_supabase_client()
            now = datetime.now(UTC)

            # Update analytics record
            client.table("partner_trial_analytics").update(
                {
                    "trial_status": "converted",
                    "trial_converted_at": now.isoformat(),
                    "converted_to_tier": new_tier,
                    "conversion_revenue_usd": revenue_usd,
                    "updated_at": now.isoformat(),
                }
            ).eq("user_id", user_id).eq("trial_status", "active").execute()

            # Update API key trial status
            client.table("api_keys_new").update(
                {
                    "trial_converted": True,
                    "is_trial": False,
                }
            ).eq("user_id", user_id).execute()

            logger.info(
                f"Partner trial converted for user {user_id} to {new_tier}, revenue: ${revenue_usd}"
            )

            return {
                "success": True,
                "converted_at": now.isoformat(),
                "new_tier": new_tier,
                "revenue_usd": revenue_usd,
            }

        except Exception as e:
            logger.error(f"Error converting partner trial for user {user_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def expire_partner_trial(user_id: int) -> dict[str, Any]:
        """
        Expire a partner trial and downgrade user to free tier.

        Args:
            user_id: User's ID

        Returns:
            Expiration result dict
        """
        try:
            client = get_supabase_client()
            now = datetime.now(UTC)

            # Update analytics
            client.table("partner_trial_analytics").update(
                {
                    "trial_status": "expired",
                    "updated_at": now.isoformat(),
                }
            ).eq("user_id", user_id).eq("trial_status", "active").execute()

            # Downgrade user to free tier
            client.table("users").update(
                {
                    "subscription_status": "expired",
                    "tier": "basic",
                    "updated_at": now.isoformat(),
                }
            ).eq("id", user_id).execute()

            # Clear trial flags on API key
            client.table("api_keys_new").update(
                {
                    "is_trial": False,
                    "trial_converted": False,
                }
            ).eq("user_id", user_id).execute()

            logger.info(f"Partner trial expired for user {user_id}")

            return {
                "success": True,
                "expired_at": now.isoformat(),
            }

        except Exception as e:
            logger.error(f"Error expiring partner trial for user {user_id}: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def get_partner_analytics(
        partner_code: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Get analytics for a partner's trials.

        Args:
            partner_code: Partner identifier
            start_date: Filter start date
            end_date: Filter end date

        Returns:
            Analytics summary dict
        """
        try:
            client = get_supabase_client()
            partner_code = partner_code.upper()

            query = (
                client.table("partner_trial_analytics").select("*").eq("partner_code", partner_code)
            )

            if start_date:
                query = query.gte("trial_started_at", start_date.isoformat())
            if end_date:
                query = query.lte("trial_started_at", end_date.isoformat())

            result = query.execute()
            trials = result.data or []

            total_trials = len(trials)
            active_trials = sum(1 for t in trials if t["trial_status"] == "active")
            converted_trials = sum(1 for t in trials if t["trial_status"] == "converted")
            expired_trials = sum(1 for t in trials if t["trial_status"] == "expired")

            total_revenue = sum(
                float(t["conversion_revenue_usd"] or 0)
                for t in trials
                if t["trial_status"] == "converted"
            )

            conversion_rate = (converted_trials / total_trials * 100) if total_trials > 0 else 0

            return {
                "partner_code": partner_code,
                "total_trials": total_trials,
                "active_trials": active_trials,
                "converted_trials": converted_trials,
                "expired_trials": expired_trials,
                "conversion_rate_percent": round(conversion_rate, 2),
                "total_revenue_usd": round(total_revenue, 2),
                "avg_revenue_per_conversion": (
                    round(total_revenue / converted_trials, 2) if converted_trials > 0 else 0
                ),
            }

        except Exception as e:
            logger.error(f"Error getting partner analytics for {partner_code}: {e}")
            return {
                "partner_code": partner_code,
                "error": str(e),
                "total_trials": 0,
            }


# Module-level functions for convenience
def get_partner_config(partner_code: str) -> dict[str, Any] | None:
    """Get partner trial configuration"""
    return PartnerTrialService.get_partner_config(partner_code)


def is_partner_code(code: str) -> bool:
    """Check if a code is a valid partner code"""
    return PartnerTrialService.is_partner_code(code)


def start_partner_trial(
    user_id: int,
    api_key: str,
    partner_code: str,
    signup_source: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a partner-specific trial for a user"""
    return PartnerTrialService.start_partner_trial(
        user_id, api_key, partner_code, signup_source, metadata
    )


def get_user_daily_limit(user_id: int) -> float:
    """Get the daily usage limit for a user"""
    return PartnerTrialService.get_user_daily_limit(user_id)
