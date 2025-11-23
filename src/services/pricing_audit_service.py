"""
Pricing Audit Service

Tracks pricing changes over time and provides audit reports.
Enables detection of pricing anomalies and discrepancies across gateways.

Features:
- Track historical pricing data
- Detect price changes and anomalies
- Compare pricing across gateways
- Generate audit reports
- Calculate cost impact of price changes
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from collections import defaultdict

from src.config.supabase_config import supabase_client

logger = logging.getLogger(__name__)

# Path to store pricing history
PRICING_HISTORY_DIR = Path("/root/repo/src/data/pricing_history")
PRICING_HISTORY_DIR.mkdir(exist_ok=True)


@dataclass
class PricingRecord:
    """Single pricing record for a model on a gateway"""

    gateway: str
    model_id: str
    prompt_price: float
    completion_price: float
    request_price: float = 0.0
    image_price: float = 0.0
    context_length: Optional[int] = None
    pricing_model: Optional[str] = None
    timestamp: str = ""  # ISO format datetime
    source: str = "manual"  # "manual", "api", "cached"

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.utcnow().isoformat()


@dataclass
class PricingComparison:
    """Comparison result for same model across gateways"""

    model_id: str
    gateway_a: str
    gateway_b: str
    prompt_difference: float
    prompt_variance_pct: float
    completion_difference: float
    completion_variance_pct: float
    is_anomaly: bool
    variance_severity: str  # "minor", "moderate", "major", "critical"


@dataclass
class PricingChangeAlert:
    """Alert for significant pricing changes"""

    gateway: str
    model_id: str
    field: str  # "prompt", "completion", "request", "image"
    old_price: float
    new_price: float
    change_pct: float
    timestamp: str
    impact_type: str  # "increase", "decrease"


class PricingAuditService:
    """Service for auditing and tracking pricing changes"""

    def __init__(self):
        self.history_file = PRICING_HISTORY_DIR / "pricing_history.jsonl"
        self.anomalies_file = PRICING_HISTORY_DIR / "pricing_anomalies.json"
        self.alerts_file = PRICING_HISTORY_DIR / "pricing_alerts.jsonl"
        self.comparisons_file = PRICING_HISTORY_DIR / "pricing_comparisons.json"
        self.snapshot_dir = PRICING_HISTORY_DIR / "snapshots"
        self.snapshot_dir.mkdir(exist_ok=True)

    def record_pricing_snapshot(self, pricing_data: Dict[str, Any]) -> str:
        """
        Record a complete pricing snapshot with timestamp.

        Args:
            pricing_data: Pricing dict from manual_pricing.json

        Returns:
            Snapshot filename
        """
        timestamp = datetime.utcnow()
        filename = f"pricing_snapshot_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"
        filepath = self.snapshot_dir / filename

        # Add metadata
        snapshot = {
            "timestamp": timestamp.isoformat(),
            "pricing": pricing_data,
            "gateway_count": len(pricing_data) - 1,  # Exclude _metadata
            "model_count": sum(
                len(models) for gw, models in pricing_data.items() if gw != "_metadata"
            ),
        }

        with open(filepath, "w") as f:
            json.dump(snapshot, f, indent=2)

        logger.info(f"Recorded pricing snapshot: {filename}")
        return filename

    def log_pricing_record(self, record: PricingRecord) -> None:
        """Log individual pricing record to history."""
        with open(self.history_file, "a") as f:
            f.write(json.dumps(asdict(record)) + "\n")

    def batch_log_records(self, records: List[PricingRecord]) -> None:
        """Log multiple pricing records efficiently."""
        with open(self.history_file, "a") as f:
            for record in records:
                f.write(json.dumps(asdict(record)) + "\n")

    def record_all_pricing(self, pricing_data: Dict[str, Any]) -> int:
        """
        Record all pricing from manual_pricing.json as individual records.

        Args:
            pricing_data: Complete pricing dict

        Returns:
            Number of records logged
        """
        records = []
        count = 0

        for gateway, models in pricing_data.items():
            if gateway == "_metadata":
                continue

            for model_id, pricing in models.items():
                record = PricingRecord(
                    gateway=gateway,
                    model_id=model_id,
                    prompt_price=float(pricing.get("prompt", 0) or 0),
                    completion_price=float(pricing.get("completion", 0) or 0),
                    request_price=float(pricing.get("request", 0) or 0),
                    image_price=float(pricing.get("image", 0) or 0),
                    context_length=pricing.get("context_length"),
                    pricing_model=pricing.get("pricing_model"),
                    source="manual",
                )
                records.append(record)
                count += 1

        self.batch_log_records(records)
        logger.info(f"Recorded {count} pricing records")
        return count

    def get_pricing_history(
        self, gateway: Optional[str] = None, model_id: Optional[str] = None
    ) -> List[PricingRecord]:
        """
        Retrieve pricing history with optional filters.

        Args:
            gateway: Filter by gateway (optional)
            model_id: Filter by model ID (optional)

        Returns:
            List of pricing records matching criteria
        """
        records = []

        if not self.history_file.exists():
            return records

        with open(self.history_file, "r") as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    record = PricingRecord(**data)

                    # Apply filters
                    if gateway and record.gateway.lower() != gateway.lower():
                        continue
                    if model_id and record.model_id.lower() != model_id.lower():
                        continue

                    records.append(record)
                except (json.JSONDecodeError, TypeError):
                    continue

        return records

    def detect_price_changes(
        self, gateway: str, model_id: str, threshold_pct: float = 5.0
    ) -> List[PricingChangeAlert]:
        """
        Detect significant price changes for a model.

        Args:
            gateway: Gateway name
            model_id: Model ID
            threshold_pct: Minimum percentage change to flag

        Returns:
            List of price change alerts
        """
        history = self.get_pricing_history(gateway=gateway, model_id=model_id)

        if len(history) < 2:
            return []

        alerts = []
        history.sort(key=lambda x: x.timestamp)

        for i in range(1, len(history)):
            prev = history[i - 1]
            curr = history[i]

            # Check each price field
            for field in ["prompt_price", "completion_price", "request_price", "image_price"]:
                prev_val = getattr(prev, field)
                curr_val = getattr(curr, field)

                if prev_val == 0:
                    continue

                change_pct = abs((curr_val - prev_val) / prev_val) * 100

                if change_pct >= threshold_pct:
                    impact_type = "increase" if curr_val > prev_val else "decrease"
                    alert = PricingChangeAlert(
                        gateway=gateway,
                        model_id=model_id,
                        field=field.replace("_price", ""),
                        old_price=prev_val,
                        new_price=curr_val,
                        change_pct=change_pct,
                        timestamp=curr.timestamp,
                        impact_type=impact_type,
                    )
                    alerts.append(alert)

        return alerts

    def compare_gateway_pricing(
        self, model_id: str, variance_threshold_pct: float = 10.0
    ) -> List[PricingComparison]:
        """
        Compare pricing for same model across different gateways.

        Args:
            model_id: Model to compare
            variance_threshold_pct: Threshold for anomaly detection

        Returns:
            List of pricing comparisons
        """
        # Get latest price for each gateway
        history = self.get_pricing_history(model_id=model_id)

        if not history:
            return []

        # Group by gateway, get most recent
        gateway_latest = {}
        for record in history:
            gateway = record.gateway.lower()
            if (
                gateway not in gateway_latest
                or record.timestamp > gateway_latest[gateway].timestamp
            ):
                gateway_latest[gateway] = record

        if len(gateway_latest) < 2:
            return []

        gateways = list(gateway_latest.keys())
        comparisons = []

        for i in range(len(gateways)):
            for j in range(i + 1, len(gateways)):
                gw_a = gateways[i]
                gw_b = gateways[j]
                rec_a = gateway_latest[gw_a]
                rec_b = gateway_latest[gw_b]

                # Compare prompt prices
                if rec_a.prompt_price > 0 and rec_b.prompt_price > 0:
                    prompt_diff = abs(rec_a.prompt_price - rec_b.prompt_price)
                    prompt_var_pct = (
                        prompt_diff / min(rec_a.prompt_price, rec_b.prompt_price)
                    ) * 100

                    # Compare completion prices
                    if rec_a.completion_price > 0 and rec_b.completion_price > 0:
                        comp_diff = abs(
                            rec_a.completion_price - rec_b.completion_price
                        )
                        comp_var_pct = (
                            comp_diff / min(rec_a.completion_price, rec_b.completion_price)
                        ) * 100
                    else:
                        comp_diff = 0
                        comp_var_pct = 0

                    is_anomaly = (
                        prompt_var_pct >= variance_threshold_pct
                        or comp_var_pct >= variance_threshold_pct
                    )
                    severity = self._calculate_variance_severity(prompt_var_pct)

                    comparison = PricingComparison(
                        model_id=model_id,
                        gateway_a=gw_a,
                        gateway_b=gw_b,
                        prompt_difference=prompt_diff,
                        prompt_variance_pct=prompt_var_pct,
                        completion_difference=comp_diff,
                        completion_variance_pct=comp_var_pct,
                        is_anomaly=is_anomaly,
                        variance_severity=severity,
                    )
                    comparisons.append(comparison)

        return comparisons

    def _calculate_variance_severity(self, variance_pct: float) -> str:
        """Calculate severity level based on variance percentage."""
        if variance_pct > 500:
            return "critical"
        elif variance_pct > 100:
            return "major"
        elif variance_pct > 50:
            return "moderate"
        else:
            return "minor"

    def find_pricing_anomalies(
        self, variance_threshold_pct: float = 50.0
    ) -> Dict[str, List[PricingComparison]]:
        """
        Find all pricing anomalies (large variances for same model).

        Args:
            variance_threshold_pct: Threshold for anomaly detection

        Returns:
            Dict mapping model_id to comparisons with anomalies
        """
        # Get all recent models
        history = self.get_pricing_history()

        if not history:
            return {}

        # Get unique model IDs
        model_ids = set(record.model_id for record in history)

        anomalies = {}

        for model_id in model_ids:
            comparisons = self.compare_gateway_pricing(
                model_id, variance_threshold_pct
            )
            anomalous = [c for c in comparisons if c.is_anomaly]

            if anomalous:
                anomalies[model_id] = anomalous

        return anomalies

    def generate_audit_report(self, days: int = 30) -> Dict[str, Any]:
        """
        Generate comprehensive audit report for specified period.

        Args:
            days: Number of days to include in report

        Returns:
            Audit report dict
        """
        cutoff = datetime.utcnow() - timedelta(days=days)

        # Get records from period
        all_records = self.get_pricing_history()
        recent_records = [
            r
            for r in all_records
            if datetime.fromisoformat(r.timestamp) >= cutoff
        ]

        # Calculate statistics
        gateway_stats = defaultdict(lambda: {"model_count": 0, "changes_detected": 0})
        total_anomalies = 0

        for record in recent_records:
            gateway_stats[record.gateway]["model_count"] += 1

        # Detect anomalies
        anomalies = self.find_pricing_anomalies()
        total_anomalies = sum(len(v) for v in anomalies.values())

        # Worst anomalies
        worst_anomalies = sorted(
            [c for anomaly_list in anomalies.values() for c in anomaly_list],
            key=lambda x: x.prompt_variance_pct,
            reverse=True,
        )[:10]

        report = {
            "report_type": "pricing_audit",
            "generated_at": datetime.utcnow().isoformat(),
            "period_days": days,
            "summary": {
                "total_records": len(recent_records),
                "unique_gateways": len(gateway_stats),
                "unique_models": len(set(r.model_id for r in recent_records)),
                "total_anomalies": total_anomalies,
                "worst_variance": (
                    worst_anomalies[0].prompt_variance_pct
                    if worst_anomalies
                    else 0
                ),
            },
            "gateway_stats": dict(gateway_stats),
            "worst_anomalies": [asdict(a) for a in worst_anomalies],
            "recommendations": self._generate_recommendations(worst_anomalies),
        }

        return report

    def _generate_recommendations(
        self, anomalies: List[PricingComparison]
    ) -> List[str]:
        """Generate recommendations based on anomalies."""
        recommendations = []

        if not anomalies:
            recommendations.append("✅ No major pricing anomalies detected")
            return recommendations

        critical = [a for a in anomalies if a.variance_severity == "critical"]
        if critical:
            recommendations.append(
                f"🔴 CRITICAL: {len(critical)} critical anomalies found (>500% variance). "
                "Review smart routing immediately."
            )

        major = [a for a in anomalies if a.variance_severity == "major"]
        if major:
            model_savings = sum(
                a.prompt_difference * 1_000_000 for a in major if a.prompt_difference > 0
            )
            recommendations.append(
                f"🟠 MAJOR: {len(major)} major anomalies. "
                f"Potential savings: ${model_savings:,.0f} per billion tokens"
            )

        high_variance_models = set(a.model_id for a in anomalies if a.variance_severity in ["critical", "major"])
        if len(high_variance_models) >= 3:
            recommendations.append(
                f"⚠️  {len(high_variance_models)} models show high pricing variance. "
                "Implement smart routing to cheapest provider per model."
            )

        return recommendations

    def save_audit_report(
        self, report: Dict[str, Any], filename: Optional[str] = None
    ) -> str:
        """
        Save audit report to file.

        Args:
            report: Report dict
            filename: Custom filename (optional)

        Returns:
            Filename saved to
        """
        if not filename:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"audit_report_{timestamp}.json"

        filepath = PRICING_HISTORY_DIR / filename

        with open(filepath, "w") as f:
            json.dump(report, f, indent=2)

        logger.info(f"Saved audit report: {filename}")
        return filename

    def get_cost_impact_analysis(
        self, model_id: str, monthly_tokens: int = 1_000_000_000
    ) -> Dict[str, Any]:
        """
        Calculate cost impact of pricing differences.

        Args:
            model_id: Model to analyze
            monthly_tokens: Monthly token usage for calculation

        Returns:
            Cost impact analysis
        """
        comparisons = self.compare_gateway_pricing(model_id, variance_threshold_pct=0)

        if not comparisons:
            return {"error": "No pricing data found for model"}

        # Use first comparison as baseline
        baseline = comparisons[0]
        analysis = {
            "model_id": model_id,
            "monthly_token_volume": monthly_tokens,
            "assumptions": {
                "prompt_tokens_pct": 40,
                "completion_tokens_pct": 60,
            },
            "monthly_cost_comparison": [],
            "monthly_savings_opportunity": 0,
            "annual_savings_opportunity": 0,
        }

        prompt_tokens = monthly_tokens * 0.4
        completion_tokens = monthly_tokens * 0.6

        # Calculate costs for each comparison
        history = self.get_pricing_history(model_id=model_id)
        gateway_latest = {}

        for record in history:
            gateway = record.gateway.lower()
            if (
                gateway not in gateway_latest
                or record.timestamp > gateway_latest[gateway].timestamp
            ):
                gateway_latest[gateway] = record

        max_cost = 0
        min_cost = float("inf")
        costs_by_gateway = {}

        for gateway, record in gateway_latest.items():
            monthly_cost = (
                (prompt_tokens * record.prompt_price) / 1_000_000
                + (completion_tokens * record.completion_price) / 1_000_000
            )
            costs_by_gateway[gateway] = monthly_cost
            max_cost = max(max_cost, monthly_cost)
            min_cost = min(min_cost, monthly_cost)

        # Sort by cost
        sorted_costs = sorted(
            costs_by_gateway.items(), key=lambda x: x[1]
        )

        for gateway, cost in sorted_costs:
            analysis["monthly_cost_comparison"].append(
                {
                    "gateway": gateway,
                    "monthly_cost": cost,
                    "annual_cost": cost * 12,
                }
            )

        if min_cost < float("inf"):
            analysis["monthly_savings_opportunity"] = max_cost - min_cost
            analysis["annual_savings_opportunity"] = (
                analysis["monthly_savings_opportunity"] * 12
            )
            analysis["cheapest_provider"] = sorted_costs[0][0]
            analysis["most_expensive_provider"] = sorted_costs[-1][0]

        return analysis

    def export_audit_data(self, format: str = "json") -> str:
        """
        Export all audit data in specified format.

        Args:
            format: Export format ("json", "csv")

        Returns:
            Exported data as string
        """
        if format == "json":
            return self._export_json()
        elif format == "csv":
            return self._export_csv()
        else:
            raise ValueError(f"Unsupported format: {format}")

    def _export_json(self) -> str:
        """Export audit data as JSON."""
        history = self.get_pricing_history()
        return json.dumps([asdict(r) for r in history], indent=2, default=str)

    def _export_csv(self) -> str:
        """Export audit data as CSV."""
        history = self.get_pricing_history()

        if not history:
            return ""

        lines = []
        header = [
            "timestamp",
            "gateway",
            "model_id",
            "prompt_price",
            "completion_price",
            "request_price",
            "image_price",
            "context_length",
        ]
        lines.append(",".join(header))

        for record in history:
            row = [
                record.timestamp,
                record.gateway,
                record.model_id,
                str(record.prompt_price),
                str(record.completion_price),
                str(record.request_price),
                str(record.image_price),
                str(record.context_length or ""),
            ]
            lines.append(",".join(row))

        return "\n".join(lines)


# Singleton instance
_audit_service = None


def get_pricing_audit_service() -> PricingAuditService:
    """Get or create singleton instance."""
    global _audit_service
    if _audit_service is None:
        _audit_service = PricingAuditService()
    return _audit_service
