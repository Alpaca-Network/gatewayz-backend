"""
Analytics service - Provides business intelligence and operational analytics.

This service aggregates data from multiple sources:
- Redis (real-time metrics, last 24 hours)
- Database (historical data, trends)
- Model health tracking
- Provider statistics

Provides analytics for:
- Trial funnels and conversion
- Cost analysis by provider/model
- Latency trends and performance
- Error rates and reliability
- Token efficiency metrics
- Provider comparison
"""

import logging
from datetime import datetime, timedelta, UTC
from typing import Any

from src.config.supabase_config import get_supabase_client
from src.services.redis_metrics import get_redis_metrics

logger = logging.getLogger(__name__)


class AnalyticsService:
    """
    Comprehensive analytics service for business and operational metrics.
    """

    def __init__(self, supabase_client=None, redis_metrics=None):
        """Initialize analytics service"""
        self.supabase = supabase_client or get_supabase_client()
        self.redis_metrics = redis_metrics or get_redis_metrics()

    def get_trial_analytics(self) -> dict[str, Any]:
        """
        Get trial funnel metrics (signups, activations, conversions).

        Returns:
            Dictionary with trial funnel statistics
        """
        try:
            # Get total signups (users with trial status)
            signups_result = (
                self.supabase.table("users")
                .select("id", count="exact")
                .eq("subscription_status", "trial")
                .execute()
            )
            signups = signups_result.count or 0

            # Get users who actually started using (made at least one request)
            started_trial_result = (
                self.supabase.table("users")
                .select("id", count="exact")
                .eq("subscription_status", "trial")
                .gt("api_usage_count", 0)
                .execute()
            )
            started_trial = started_trial_result.count or 0

            # Get converted users (moved from trial to active subscription)
            converted_result = (
                self.supabase.table("users")
                .select("id", count="exact")
                .in_("subscription_status", ["active", "premium"])
                .is_not("trial_expires_at", "null")  # Had a trial before
                .execute()
            )
            converted = converted_result.count or 0

            # Calculate conversion rate
            conversion_rate = (converted / signups * 100) if signups > 0 else 0.0

            # Get average time to conversion (trial start to first purchase)
            avg_time_to_conversion = self._get_avg_time_to_conversion()

            return {
                "signups": signups,
                "started_trial": started_trial,
                "converted": converted,
                "conversion_rate": conversion_rate,
                "avg_time_to_conversion_days": avg_time_to_conversion,
                "activation_rate": (started_trial / signups * 100) if signups > 0 else 0.0,
            }

        except Exception as e:
            logger.error(f"Failed to get trial analytics: {e}", exc_info=True)
            return {
                "signups": 0,
                "started_trial": 0,
                "converted": 0,
                "conversion_rate": 0.0,
                "error": str(e),
            }

    def _get_avg_time_to_conversion(self) -> float:
        """Calculate average time from trial start to conversion in days"""
        try:
            # Query users who converted
            result = (
                self.supabase.table("users")
                .select("registration_date,updated_at")
                .in_("subscription_status", ["active", "premium"])
                .is_not("trial_expires_at", "null")
                .limit(1000)
                .execute()
            )

            if not result.data:
                return 0.0

            total_days = 0
            count = 0

            for user in result.data:
                try:
                    reg_date = datetime.fromisoformat(user["registration_date"].replace("Z", "+00:00"))
                    updated_at = datetime.fromisoformat(user["updated_at"].replace("Z", "+00:00"))
                    days = (updated_at - reg_date).days
                    total_days += days
                    count += 1
                except Exception:
                    continue

            return total_days / count if count > 0 else 0.0

        except Exception as e:
            logger.warning(f"Failed to calculate avg time to conversion: {e}")
            return 0.0

    async def get_cost_by_provider(
        self, start_date: datetime | None = None, end_date: datetime | None = None
    ) -> dict[str, Any]:
        """
        Get cost breakdown by provider for a date range.

        Args:
            start_date: Start of date range (default: 7 days ago)
            end_date: End of date range (default: now)

        Returns:
            Dictionary with cost breakdown by provider
        """
        if not start_date:
            start_date = datetime.now(UTC) - timedelta(days=7)
        if not end_date:
            end_date = datetime.now(UTC)

        try:
            # Query aggregated metrics
            result = (
                self.supabase.table("metrics_hourly_aggregates")
                .select("provider,total_cost_credits,total_requests")
                .gte("hour", start_date.isoformat())
                .lte("hour", end_date.isoformat())
                .execute()
            )

            # Aggregate by provider
            provider_costs = {}
            for row in result.data:
                provider = row["provider"]
                if provider not in provider_costs:
                    provider_costs[provider] = {
                        "total_cost": 0.0,
                        "total_requests": 0,
                    }

                provider_costs[provider]["total_cost"] += float(row["total_cost_credits"])
                provider_costs[provider]["total_requests"] += int(row["total_requests"])

            # Calculate cost per request
            for provider, data in provider_costs.items():
                if data["total_requests"] > 0:
                    data["cost_per_request"] = data["total_cost"] / data["total_requests"]
                else:
                    data["cost_per_request"] = 0.0

            # Sort by cost descending
            sorted_providers = sorted(
                provider_costs.items(), key=lambda x: x[1]["total_cost"], reverse=True
            )

            return {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "providers": dict(sorted_providers),
                "total_cost": sum(p["total_cost"] for p in provider_costs.values()),
                "total_requests": sum(p["total_requests"] for p in provider_costs.values()),
            }

        except Exception as e:
            logger.error(f"Failed to get cost by provider: {e}", exc_info=True)
            return {"error": str(e), "providers": {}}

    async def get_latency_trends(
        self, provider: str, hours: int = 24
    ) -> dict[str, Any]:
        """
        Get latency trends for a provider over time.

        Args:
            provider: Provider name
            hours: Number of hours to analyze

        Returns:
            Dictionary with latency trends
        """
        try:
            # Get hourly aggregates
            cutoff = datetime.now(UTC) - timedelta(hours=hours)

            result = (
                self.supabase.table("metrics_hourly_aggregates")
                .select("hour,avg_latency_ms,p95_latency_ms,p99_latency_ms")
                .eq("provider", provider)
                .gte("hour", cutoff.isoformat())
                .order("hour")
                .execute()
            )

            hourly_data = result.data

            if not hourly_data:
                return {"provider": provider, "hours": hours, "data": []}

            # Calculate overall statistics
            avg_latencies = [h["avg_latency_ms"] for h in hourly_data if h["avg_latency_ms"]]
            p95_latencies = [h["p95_latency_ms"] for h in hourly_data if h["p95_latency_ms"]]

            overall_avg = sum(avg_latencies) / len(avg_latencies) if avg_latencies else 0
            overall_p95 = sum(p95_latencies) / len(p95_latencies) if p95_latencies else 0

            return {
                "provider": provider,
                "hours": hours,
                "overall_avg_latency_ms": overall_avg,
                "overall_p95_latency_ms": overall_p95,
                "hourly_data": hourly_data,
            }

        except Exception as e:
            logger.error(f"Failed to get latency trends: {e}", exc_info=True)
            return {"error": str(e), "provider": provider}

    async def get_error_rate_by_model(self, hours: int = 24) -> dict[str, Any]:
        """
        Get error rates broken down by model.

        Args:
            hours: Number of hours to analyze

        Returns:
            Dictionary with error rates by model
        """
        try:
            cutoff = datetime.now(UTC) - timedelta(hours=hours)

            result = (
                self.supabase.table("metrics_hourly_aggregates")
                .select("model,provider,total_requests,failed_requests,error_rate")
                .gte("hour", cutoff.isoformat())
                .execute()
            )

            # Aggregate by model
            model_stats = {}
            for row in result.data:
                model = row["model"]
                if model not in model_stats:
                    model_stats[model] = {
                        "total_requests": 0,
                        "failed_requests": 0,
                        "providers": set(),
                    }

                model_stats[model]["total_requests"] += row["total_requests"]
                model_stats[model]["failed_requests"] += row["failed_requests"]
                model_stats[model]["providers"].add(row["provider"])

            # Calculate error rates and convert sets to lists
            for model, stats in model_stats.items():
                if stats["total_requests"] > 0:
                    stats["error_rate"] = stats["failed_requests"] / stats["total_requests"]
                else:
                    stats["error_rate"] = 0.0
                stats["providers"] = list(stats["providers"])

            # Sort by error rate descending
            sorted_models = sorted(
                model_stats.items(), key=lambda x: x[1]["error_rate"], reverse=True
            )

            return {
                "hours": hours,
                "models": dict(sorted_models),
            }

        except Exception as e:
            logger.error(f"Failed to get error rate by model: {e}", exc_info=True)
            return {"error": str(e)}

    async def get_token_efficiency(
        self, provider: str, model: str
    ) -> dict[str, Any]:
        """
        Get token efficiency metrics (cost per token, tokens per request).

        Args:
            provider: Provider name
            model: Model name

        Returns:
            Dictionary with efficiency metrics
        """
        try:
            # Get last 7 days of data
            cutoff = datetime.now(UTC) - timedelta(days=7)

            result = (
                self.supabase.table("metrics_hourly_aggregates")
                .select("*")
                .eq("provider", provider)
                .eq("model", model)
                .gte("hour", cutoff.isoformat())
                .execute()
            )

            if not result.data:
                return {"provider": provider, "model": model, "data": None}

            # Aggregate totals
            total_cost = sum(row["total_cost_credits"] for row in result.data)
            total_tokens_in = sum(row["total_tokens_input"] for row in result.data)
            total_tokens_out = sum(row["total_tokens_output"] for row in result.data)
            total_requests = sum(row["total_requests"] for row in result.data)

            total_tokens = total_tokens_in + total_tokens_out

            # Calculate efficiency metrics
            cost_per_token = total_cost / total_tokens if total_tokens > 0 else 0
            tokens_per_request = total_tokens / total_requests if total_requests > 0 else 0
            cost_per_request = total_cost / total_requests if total_requests > 0 else 0

            return {
                "provider": provider,
                "model": model,
                "total_cost": total_cost,
                "total_tokens": total_tokens,
                "total_requests": total_requests,
                "cost_per_token": cost_per_token,
                "tokens_per_request": tokens_per_request,
                "cost_per_request": cost_per_request,
                "avg_input_tokens": total_tokens_in / total_requests if total_requests > 0 else 0,
                "avg_output_tokens": total_tokens_out / total_requests if total_requests > 0 else 0,
            }

        except Exception as e:
            logger.error(f"Failed to get token efficiency: {e}", exc_info=True)
            return {"error": str(e)}

    async def get_provider_comparison(self) -> list[dict[str, Any]]:
        """
        Compare all providers across key metrics.

        Returns:
            List of provider statistics sorted by total requests
        """
        try:
            # Use materialized view for fast access
            result = self.supabase.table("provider_stats_24h").select("*").execute()

            providers = []
            for row in result.data:
                providers.append({
                    "provider": row["provider"],
                    "total_requests": row["total_requests"],
                    "successful_requests": row["successful_requests"],
                    "failed_requests": row["failed_requests"],
                    "avg_latency_ms": float(row["avg_latency_ms"]) if row["avg_latency_ms"] else 0,
                    "total_cost": float(row["total_cost"]),
                    "total_tokens": row["total_tokens"],
                    "avg_error_rate": float(row["avg_error_rate"]) if row["avg_error_rate"] else 0,
                    "unique_models": row["unique_models"],
                    "success_rate": (
                        row["successful_requests"] / row["total_requests"]
                        if row["total_requests"] > 0
                        else 0
                    ),
                })

            # Sort by total requests descending
            providers.sort(key=lambda x: x["total_requests"], reverse=True)

            return providers

        except Exception as e:
            logger.error(f"Failed to get provider comparison: {e}", exc_info=True)
            return []

    async def detect_anomalies(self) -> list[dict[str, Any]]:
        """
        Detect anomalies in metrics (cost spikes, latency spikes, error rate increases).

        Returns:
            List of detected anomalies
        """
        anomalies = []

        try:
            # Get last 24 hours of data
            cutoff = datetime.now(UTC) - timedelta(hours=24)

            result = (
                self.supabase.table("metrics_hourly_aggregates")
                .select("*")
                .gte("hour", cutoff.isoformat())
                .execute()
            )

            # Group by provider
            provider_data = {}
            for row in result.data:
                provider = row["provider"]
                if provider not in provider_data:
                    provider_data[provider] = []
                provider_data[provider].append(row)

            # Detect anomalies per provider
            for provider, hours in provider_data.items():
                # Sort by hour
                hours.sort(key=lambda x: x["hour"])

                # Check for cost spikes (>200% of average)
                costs = [h["total_cost_credits"] for h in hours]
                avg_cost = sum(costs) / len(costs) if costs else 0
                for i, hour_data in enumerate(hours):
                    if hour_data["total_cost_credits"] > avg_cost * 2 and avg_cost > 0:
                        anomalies.append({
                            "type": "cost_spike",
                            "provider": provider,
                            "hour": hour_data["hour"],
                            "value": hour_data["total_cost_credits"],
                            "expected": avg_cost,
                            "severity": "warning",
                        })

                # Check for latency spikes
                latencies = [h["avg_latency_ms"] for h in hours if h["avg_latency_ms"]]
                avg_latency = sum(latencies) / len(latencies) if latencies else 0
                for hour_data in hours:
                    if (
                        hour_data["avg_latency_ms"]
                        and hour_data["avg_latency_ms"] > avg_latency * 2
                        and avg_latency > 0
                    ):
                        anomalies.append({
                            "type": "latency_spike",
                            "provider": provider,
                            "hour": hour_data["hour"],
                            "value": hour_data["avg_latency_ms"],
                            "expected": avg_latency,
                            "severity": "warning",
                        })

                # Check for error rate increases (>10%)
                for hour_data in hours:
                    if hour_data["error_rate"] and hour_data["error_rate"] > 0.10:
                        anomalies.append({
                            "type": "high_error_rate",
                            "provider": provider,
                            "hour": hour_data["hour"],
                            "value": hour_data["error_rate"],
                            "expected": 0.05,  # Normal is <5%
                            "severity": "critical" if hour_data["error_rate"] > 0.25 else "warning",
                        })

            return anomalies

        except Exception as e:
            logger.error(f"Failed to detect anomalies: {e}", exc_info=True)
            return []


# Global instance
_analytics = None


def get_analytics_service() -> AnalyticsService:
    """Get global analytics service instance"""
    global _analytics
    if _analytics is None:
        _analytics = AnalyticsService()
    return _analytics


# Backwards compatibility
def get_trial_analytics():
    """Backwards compatible function for existing code"""
    analytics = get_analytics_service()
    return analytics.get_trial_analytics()
