"""
Pricing Health Monitoring Service

Monitors the health of the pricing system to detect stale data and missing pricing.

Features:
- Pricing staleness detection (24h threshold)
- Default pricing usage tracking
- Provider sync health monitoring
- Prometheus metrics exposure
- Sentry alerting for critical issues

Created: 2026-02-03
Part of pricing system audit improvements (Issue #1038)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


class PricingHealthStatus:
    """Health status constants"""
    HEALTHY = "healthy"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class PricingHealthMonitor:
    """Monitor pricing system health"""

    def __init__(self):
        self.staleness_threshold_hours = 24
        self.critical_staleness_hours = 72

    def check_pricing_staleness(self) -> dict[str, Any]:
        """
        Check if pricing data is stale.

        Returns:
            Dict with staleness status and details
        """
        try:
            from src.config.supabase_config import get_supabase_client

            client = get_supabase_client()

            # Get most recent pricing update timestamp
            result = (
                client.table("model_pricing")
                .select("last_updated")
                .order("last_updated", desc=True)
                .limit(1)
                .execute()
            )

            if not result.data:
                return {
                    "status": PricingHealthStatus.CRITICAL,
                    "message": "No pricing data found in database",
                    "last_updated": None,
                    "hours_since_update": None
                }

            last_updated_str = result.data[0]["last_updated"]
            last_updated = datetime.fromisoformat(last_updated_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            hours_since_update = (now - last_updated).total_seconds() / 3600

            # Determine status
            if hours_since_update > self.critical_staleness_hours:
                status = PricingHealthStatus.CRITICAL
                message = f"Pricing data critically stale ({hours_since_update:.1f}h old)"
                self._alert_critical_staleness(hours_since_update, last_updated)
            elif hours_since_update > self.staleness_threshold_hours:
                status = PricingHealthStatus.WARNING
                message = f"Pricing data is stale ({hours_since_update:.1f}h old)"
                self._alert_staleness_warning(hours_since_update, last_updated)
            else:
                status = PricingHealthStatus.HEALTHY
                message = f"Pricing data is fresh ({hours_since_update:.1f}h old)"

            # Track metric
            try:
                from src.services.prometheus_metrics import pricing_staleness_hours
                pricing_staleness_hours.set(hours_since_update)
            except (ImportError, AttributeError):
                pass

            return {
                "status": status,
                "message": message,
                "last_updated": last_updated.isoformat(),
                "hours_since_update": round(hours_since_update, 2),
                "threshold_hours": self.staleness_threshold_hours,
                "critical_threshold_hours": self.critical_staleness_hours
            }

        except Exception as e:
            logger.error(f"Error checking pricing staleness: {e}")
            return {
                "status": PricingHealthStatus.UNKNOWN,
                "message": f"Health check failed: {e}",
                "last_updated": None,
                "hours_since_update": None
            }

    def check_default_pricing_usage(self) -> dict[str, Any]:
        """
        Check usage of default pricing (indicates missing pricing data).

        Returns:
            Dict with default pricing usage stats
        """
        try:
            from src.services.pricing import get_default_pricing_stats

            stats = get_default_pricing_stats()
            models_using_default = stats.get("models_using_default", 0)

            # Determine status based on count
            if models_using_default > 100:
                status = PricingHealthStatus.CRITICAL
                message = f"{models_using_default} models using default pricing (critical)"
            elif models_using_default > 20:
                status = PricingHealthStatus.WARNING
                message = f"{models_using_default} models using default pricing (warning)"
            elif models_using_default > 0:
                status = PricingHealthStatus.WARNING
                message = f"{models_using_default} models using default pricing"
            else:
                status = PricingHealthStatus.HEALTHY
                message = "No models using default pricing"

            # Track metric
            try:
                from src.services.prometheus_metrics import models_using_default_pricing
                models_using_default_pricing.set(models_using_default)
            except (ImportError, AttributeError):
                pass

            return {
                "status": status,
                "message": message,
                "models_using_default": models_using_default,
                "details": stats.get("details", {})
            }

        except Exception as e:
            logger.error(f"Error checking default pricing usage: {e}")
            return {
                "status": PricingHealthStatus.UNKNOWN,
                "message": f"Health check failed: {e}",
                "models_using_default": None
            }

    def check_provider_sync_health(self) -> dict[str, Any]:
        """
        Check health of provider pricing syncs.

        Returns:
            Dict with sync health status per provider
        """
        try:
            from src.config.supabase_config import get_supabase_client
            from src.config.config import Config

            client = get_supabase_client()
            providers = Config.PRICING_SYNC_PROVIDERS

            # Get last sync status for each provider
            result = (
                client.table("pricing_sync_log")
                .select("provider_slug, sync_started_at, sync_completed_at, status, errors")
                .in_("provider_slug", providers)
                .order("sync_started_at", desc=True)
                .limit(len(providers) * 2)  # Get last 2 for each provider
                .execute()
            )

            provider_health = {}
            now = datetime.now(timezone.utc)

            for provider in providers:
                # Find most recent sync for this provider
                provider_syncs = [
                    s for s in result.data
                    if s["provider_slug"] == provider
                ]

                if not provider_syncs:
                    provider_health[provider] = {
                        "status": PricingHealthStatus.WARNING,
                        "message": "No sync history found",
                        "last_sync": None,
                        "hours_since_sync": None
                    }
                    continue

                last_sync = provider_syncs[0]
                sync_time = datetime.fromisoformat(
                    last_sync["sync_started_at"].replace('Z', '+00:00')
                )
                hours_since_sync = (now - sync_time).total_seconds() / 3600

                # Determine health
                if last_sync["status"] == "failed":
                    status = PricingHealthStatus.CRITICAL
                    message = f"Last sync failed ({last_sync.get('errors', 0)} errors)"
                elif hours_since_sync > 24:
                    status = PricingHealthStatus.WARNING
                    message = f"Sync is stale ({hours_since_sync:.1f}h old)"
                else:
                    status = PricingHealthStatus.HEALTHY
                    message = f"Last sync successful ({hours_since_sync:.1f}h ago)"

                provider_health[provider] = {
                    "status": status,
                    "message": message,
                    "last_sync": sync_time.isoformat(),
                    "hours_since_sync": round(hours_since_sync, 2),
                    "last_status": last_sync["status"]
                }

            # Overall status
            statuses = [p["status"] for p in provider_health.values()]
            if PricingHealthStatus.CRITICAL in statuses:
                overall_status = PricingHealthStatus.CRITICAL
            elif PricingHealthStatus.WARNING in statuses:
                overall_status = PricingHealthStatus.WARNING
            else:
                overall_status = PricingHealthStatus.HEALTHY

            return {
                "status": overall_status,
                "providers": provider_health,
                "total_providers": len(providers)
            }

        except Exception as e:
            logger.error(f"Error checking provider sync health: {e}")
            return {
                "status": PricingHealthStatus.UNKNOWN,
                "message": f"Health check failed: {e}",
                "providers": {}
            }

    def get_overall_health(self) -> dict[str, Any]:
        """
        Get overall pricing system health.

        Combines all health checks into single status.

        Returns:
            Dict with overall health status
        """
        staleness = self.check_pricing_staleness()
        default_usage = self.check_default_pricing_usage()
        sync_health = self.check_provider_sync_health()

        # Determine overall status (worst of all checks)
        statuses = [
            staleness["status"],
            default_usage["status"],
            sync_health["status"]
        ]

        if PricingHealthStatus.CRITICAL in statuses:
            overall_status = PricingHealthStatus.CRITICAL
        elif PricingHealthStatus.WARNING in statuses:
            overall_status = PricingHealthStatus.WARNING
        elif PricingHealthStatus.UNKNOWN in statuses:
            overall_status = PricingHealthStatus.UNKNOWN
        else:
            overall_status = PricingHealthStatus.HEALTHY

        return {
            "status": overall_status,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {
                "staleness": staleness,
                "default_pricing_usage": default_usage,
                "provider_sync_health": sync_health
            }
        }

    def _alert_staleness_warning(self, hours: float, last_updated: datetime):
        """Send warning alert for stale pricing"""
        try:
            import sentry_sdk
            sentry_sdk.capture_message(
                f"Pricing data is stale ({hours:.1f} hours old)",
                level="warning",
                extras={
                    "hours_since_update": hours,
                    "last_updated": last_updated.isoformat(),
                    "threshold_hours": self.staleness_threshold_hours
                }
            )
        except Exception:
            logger.warning(
                f"[PRICING_HEALTH] Pricing data is stale: {hours:.1f}h old "
                f"(last updated: {last_updated})"
            )

    def _alert_critical_staleness(self, hours: float, last_updated: datetime):
        """Send critical alert for very stale pricing"""
        try:
            import sentry_sdk
            sentry_sdk.capture_message(
                f"Pricing data is critically stale ({hours:.1f} hours old)",
                level="error",
                extras={
                    "hours_since_update": hours,
                    "last_updated": last_updated.isoformat(),
                    "critical_threshold_hours": self.critical_staleness_hours
                }
            )
        except Exception:
            logger.error(
                f"[PRICING_HEALTH_CRITICAL] Pricing data is critically stale: {hours:.1f}h old "
                f"(last updated: {last_updated})"
            )


# Singleton instance
_health_monitor: PricingHealthMonitor | None = None


def get_pricing_health_monitor() -> PricingHealthMonitor:
    """Get singleton instance of pricing health monitor"""
    global _health_monitor
    if _health_monitor is None:
        _health_monitor = PricingHealthMonitor()
    return _health_monitor


def check_pricing_health() -> dict[str, Any]:
    """
    Convenience function to check overall pricing health.

    Returns:
        Dict with health status
    """
    monitor = get_pricing_health_monitor()
    return monitor.get_overall_health()
