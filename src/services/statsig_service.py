"""
Statsig Service
===============

Server-side Statsig analytics integration using statsig-python-core SDK.
Handles event logging and feature flag management.
"""

import logging
import os
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class StatsigService:
    """
    Statsig analytics service for server-side event logging.

    Uses the Statsig Python SDK to log events and manage feature flags.
    Falls back gracefully when STATSIG_SERVER_SECRET_KEY is not configured.
    """

    def __init__(self):
        self.statsig = None
        self._initialized = False
        self.enabled = False
        self.server_secret_key = os.environ.get('STATSIG_SERVER_SECRET_KEY')

        if not self.server_secret_key:
            logger.warning("⚠️  STATSIG_SERVER_SECRET_KEY not set - Statsig analytics disabled (using fallback)")
            logger.info("   To enable: Set STATSIG_SERVER_SECRET_KEY in your .env file")
            logger.info("   Get key from: https://console.statsig.com -> Project Settings -> API Keys -> Server Secret Key")

    async def initialize(self):
        """
        Initialize the Statsig SDK with server secret key.

        This must be called during application startup (async context).
        Falls back to logging-only mode if SDK is not available or key is missing.
        """
        if self._initialized:
            logger.debug("Statsig already initialized")
            return

        if not self.server_secret_key:
            logger.warning("Statsig service not available - using fallback (no server secret key)")
            self._initialized = True
            return

        try:
            # Import Statsig SDK
            from statsig import statsig, StatsigOptions, StatsigUser, StatsigEvent

            # Store reference to SDK classes
            self._statsig_module = statsig
            self._StatsigUser = StatsigUser
            self._StatsigEvent = StatsigEvent

            # Initialize Statsig with server secret key
            options = StatsigOptions(
                api=None,  # Use default API endpoint
                tier='production' if os.environ.get('APP_ENV') == 'production' else 'development'
            )

            statsig.initialize(self.server_secret_key, options)

            self.statsig = statsig
            self.enabled = True
            self._initialized = True

            logger.info("✅ Statsig SDK initialized successfully")
            logger.info(f"   Environment: {options.tier}")
            logger.info(f"   Server Key: {self.server_secret_key[:10]}...")

        except ImportError as e:
            logger.error(f"❌ Statsig SDK not installed: {e}")
            logger.error("   Install with: pip install statsig-python-core")
            logger.warning("   Falling back to logging-only mode")
            self._initialized = True

        except Exception as e:
            logger.error(f"❌ Failed to initialize Statsig: {e}")
            logger.warning("   Falling back to logging-only mode")
            self._initialized = True

    def log_event(
        self,
        user_id: str,
        event_name: str,
        value: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Log an event to Statsig.

        Args:
            user_id: User identifier (required)
            event_name: Name of the event (required)
            value: Optional event value
            metadata: Optional event metadata/properties

        Returns:
            True if logged successfully, False otherwise
        """
        try:
            if self.enabled and self.statsig:
                # Create Statsig user
                user = self._StatsigUser(user_id)

                # Create event
                event = self._StatsigEvent(user)
                event.event_name = event_name

                if value:
                    event.value = value

                if metadata:
                    event.metadata = metadata

                # Log event to Statsig
                self.statsig.log_event(event)

                logger.debug(f"📊 Statsig event logged: {event_name} (user: {user_id})")
                return True
            else:
                # Fallback: Just log to console
                logger.info(f"📊 [Fallback] Analytics event: {event_name} (user: {user_id})")
                if metadata:
                    logger.debug(f"   Metadata: {metadata}")
                return True

        except Exception as e:
            logger.error(f"❌ Failed to log Statsig event '{event_name}': {e}")
            return False

    def get_feature_flag(
        self,
        flag_name: str,
        user_id: str,
        default_value: bool = False
    ) -> bool:
        """
        Get a feature flag value for a user.

        Args:
            flag_name: Name of the feature flag
            user_id: User identifier
            default_value: Default value if flag is not found or SDK is disabled

        Returns:
            Feature flag value (bool)
        """
        try:
            if self.enabled and self.statsig:
                user = self._StatsigUser(user_id)
                return self.statsig.check_gate(user, flag_name)
            else:
                logger.debug(f"Feature flag '{flag_name}' -> {default_value} (fallback)")
                return default_value

        except Exception as e:
            logger.error(f"❌ Failed to check feature flag '{flag_name}': {e}")
            return default_value

    async def shutdown(self):
        """
        Gracefully shutdown Statsig SDK.

        Flushes any pending events before shutdown.
        """
        if self.enabled and self.statsig:
            try:
                logger.info("🛑 Shutting down Statsig SDK...")
                self.statsig.shutdown()
                logger.info("✅ Statsig shutdown complete")
            except Exception as e:
                logger.warning(f"⚠️  Statsig shutdown warning: {e}")

        self._initialized = False
        self.enabled = False


# Global singleton instance
statsig_service = StatsigService()
