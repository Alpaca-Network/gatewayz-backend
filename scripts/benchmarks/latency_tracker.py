"""
Latency tracking and statistics for GLM-4.5-Air benchmark.

Tracks TTFB, TTFC, TPS, and provides percentile calculations.
"""

import statistics
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LatencyMetrics:
    """Latency metrics for a single request."""

    ttfb_seconds: float  # Time to first byte
    ttfc_seconds: float | None  # Time to first content (streaming only)
    total_duration_seconds: float
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int

    @property
    def tokens_per_second(self) -> float:
        """Calculate output tokens per second."""
        if self.total_duration_seconds > 0:
            return self.output_tokens / self.total_duration_seconds
        return 0.0

    @property
    def total_output_tokens(self) -> int:
        """Total output including reasoning tokens."""
        return self.output_tokens + self.reasoning_tokens

    @property
    def effective_tps(self) -> float:
        """TPS including reasoning tokens."""
        if self.total_duration_seconds > 0:
            return self.total_output_tokens / self.total_duration_seconds
        return 0.0


@dataclass
class LatencyStats:
    """Aggregated latency statistics."""

    # TTFB stats
    ttfb_mean: float
    ttfb_median: float
    ttfb_p50: float
    ttfb_p90: float
    ttfb_p95: float
    ttfb_p99: float
    ttfb_min: float
    ttfb_max: float
    ttfb_std: float

    # TTFC stats (streaming only)
    ttfc_mean: float | None
    ttfc_median: float | None
    ttfc_p50: float | None
    ttfc_p90: float | None
    ttfc_p95: float | None

    # Total duration stats
    duration_mean: float
    duration_median: float
    duration_p95: float

    # TPS stats
    tps_mean: float
    tps_median: float
    tps_p50: float
    tps_min: float
    tps_max: float

    # Token stats
    avg_input_tokens: float
    avg_output_tokens: float
    avg_reasoning_tokens: float
    total_tokens_processed: int

    # Sample size
    sample_count: int


class LatencyTracker:
    """Tracks and aggregates latency metrics across multiple requests."""

    def __init__(self):
        self.metrics: list[LatencyMetrics] = []
        self._start_time: float | None = None

    def start_session(self) -> None:
        """Mark the start of a benchmark session."""
        self._start_time = time.perf_counter()
        self.metrics = []

    def record(self, metrics: LatencyMetrics) -> None:
        """Record latency metrics for a single request."""
        self.metrics.append(metrics)

    def record_from_response(
        self,
        ttfb_seconds: float,
        total_duration_seconds: float,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
        ttfc_seconds: float | None = None,
    ) -> LatencyMetrics:
        """Create and record metrics from response data."""
        metrics = LatencyMetrics(
            ttfb_seconds=ttfb_seconds,
            ttfc_seconds=ttfc_seconds,
            total_duration_seconds=total_duration_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        self.record(metrics)
        return metrics

    def get_stats(self) -> LatencyStats:
        """Calculate aggregated statistics from recorded metrics."""
        if not self.metrics:
            raise ValueError("No metrics recorded")

        # Extract values
        ttfb_values = [m.ttfb_seconds for m in self.metrics]
        ttfc_values = [m.ttfc_seconds for m in self.metrics if m.ttfc_seconds is not None]
        duration_values = [m.total_duration_seconds for m in self.metrics]
        tps_values = [m.tokens_per_second for m in self.metrics if m.tokens_per_second > 0]
        input_tokens = [m.input_tokens for m in self.metrics]
        output_tokens = [m.output_tokens for m in self.metrics]
        reasoning_tokens = [m.reasoning_tokens for m in self.metrics]

        # Calculate TTFB stats
        ttfb_stats = self._calculate_percentile_stats(ttfb_values)

        # Calculate TTFC stats if available
        ttfc_stats = None
        if ttfc_values:
            ttfc_stats = self._calculate_percentile_stats(ttfc_values)

        # Calculate duration stats
        duration_stats = self._calculate_percentile_stats(duration_values)

        # Calculate TPS stats
        tps_stats = self._calculate_percentile_stats(tps_values) if tps_values else None

        return LatencyStats(
            # TTFB
            ttfb_mean=ttfb_stats["mean"],
            ttfb_median=ttfb_stats["median"],
            ttfb_p50=ttfb_stats["p50"],
            ttfb_p90=ttfb_stats["p90"],
            ttfb_p95=ttfb_stats["p95"],
            ttfb_p99=ttfb_stats["p99"],
            ttfb_min=ttfb_stats["min"],
            ttfb_max=ttfb_stats["max"],
            ttfb_std=ttfb_stats["std"],
            # TTFC
            ttfc_mean=ttfc_stats["mean"] if ttfc_stats else None,
            ttfc_median=ttfc_stats["median"] if ttfc_stats else None,
            ttfc_p50=ttfc_stats["p50"] if ttfc_stats else None,
            ttfc_p90=ttfc_stats["p90"] if ttfc_stats else None,
            ttfc_p95=ttfc_stats["p95"] if ttfc_stats else None,
            # Duration
            duration_mean=duration_stats["mean"],
            duration_median=duration_stats["median"],
            duration_p95=duration_stats["p95"],
            # TPS
            tps_mean=tps_stats["mean"] if tps_stats else 0.0,
            tps_median=tps_stats["median"] if tps_stats else 0.0,
            tps_p50=tps_stats["p50"] if tps_stats else 0.0,
            tps_min=tps_stats["min"] if tps_stats else 0.0,
            tps_max=tps_stats["max"] if tps_stats else 0.0,
            # Tokens
            avg_input_tokens=statistics.mean(input_tokens),
            avg_output_tokens=statistics.mean(output_tokens),
            avg_reasoning_tokens=statistics.mean(reasoning_tokens),
            total_tokens_processed=sum(input_tokens) + sum(output_tokens) + sum(reasoning_tokens),
            # Count
            sample_count=len(self.metrics),
        )

    def _calculate_percentile_stats(self, values: list[float]) -> dict[str, float]:
        """Calculate comprehensive percentile statistics."""
        if not values:
            return {
                "mean": 0.0,
                "median": 0.0,
                "p50": 0.0,
                "p90": 0.0,
                "p95": 0.0,
                "p99": 0.0,
                "min": 0.0,
                "max": 0.0,
                "std": 0.0,
            }

        sorted_values = sorted(values)
        n = len(sorted_values)

        def percentile(p: float) -> float:
            """Calculate percentile value."""
            if n == 1:
                return sorted_values[0]
            k = (n - 1) * p / 100.0
            f = int(k)
            c = f + 1 if f + 1 < n else f
            return sorted_values[f] + (k - f) * (sorted_values[c] - sorted_values[f])

        return {
            "mean": statistics.mean(values),
            "median": statistics.median(values),
            "p50": percentile(50),
            "p90": percentile(90),
            "p95": percentile(95),
            "p99": percentile(99),
            "min": min(values),
            "max": max(values),
            "std": statistics.stdev(values) if len(values) > 1 else 0.0,
        }

    def get_session_duration(self) -> float | None:
        """Get total session duration in seconds."""
        if self._start_time is None:
            return None
        return time.perf_counter() - self._start_time

    def to_dict(self) -> dict[str, Any]:
        """Export all metrics as a dictionary for serialization."""
        return {
            "metrics": [
                {
                    "ttfb_seconds": m.ttfb_seconds,
                    "ttfc_seconds": m.ttfc_seconds,
                    "total_duration_seconds": m.total_duration_seconds,
                    "input_tokens": m.input_tokens,
                    "output_tokens": m.output_tokens,
                    "reasoning_tokens": m.reasoning_tokens,
                    "tokens_per_second": m.tokens_per_second,
                }
                for m in self.metrics
            ],
            "session_duration": self.get_session_duration(),
        }


@dataclass
class CategoryLatencyTracker:
    """Track latency metrics by test category."""

    trackers: dict[str, LatencyTracker] = field(default_factory=dict)

    def get_tracker(self, category: str) -> LatencyTracker:
        """Get or create tracker for a category."""
        if category not in self.trackers:
            self.trackers[category] = LatencyTracker()
        return self.trackers[category]

    def record(
        self,
        category: str,
        ttfb_seconds: float,
        total_duration_seconds: float,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int = 0,
        ttfc_seconds: float | None = None,
    ) -> LatencyMetrics:
        """Record metrics for a specific category."""
        return self.get_tracker(category).record_from_response(
            ttfb_seconds=ttfb_seconds,
            total_duration_seconds=total_duration_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            ttfc_seconds=ttfc_seconds,
        )

    def get_all_stats(self) -> dict[str, LatencyStats]:
        """Get stats for all categories."""
        return {
            category: tracker.get_stats()
            for category, tracker in self.trackers.items()
            if tracker.metrics
        }

    def get_overall_stats(self) -> LatencyStats:
        """Get combined stats across all categories.

        Raises:
            ValueError: If no metrics have been recorded across any category.
        """
        overall_tracker = LatencyTracker()
        for tracker in self.trackers.values():
            for metric in tracker.metrics:
                overall_tracker.record(metric)

        if not overall_tracker.metrics:
            raise ValueError("No metrics recorded across any category")

        return overall_tracker.get_stats()


def format_latency_report(stats: LatencyStats) -> str:
    """Format latency stats as a human-readable report."""
    lines = [
        "=" * 60,
        "LATENCY REPORT",
        "=" * 60,
        "",
        f"Sample Size: {stats.sample_count} requests",
        "",
        "TIME TO FIRST BYTE (TTFB):",
        f"  Mean:   {stats.ttfb_mean * 1000:.1f} ms",
        f"  Median: {stats.ttfb_median * 1000:.1f} ms",
        f"  P90:    {stats.ttfb_p90 * 1000:.1f} ms",
        f"  P95:    {stats.ttfb_p95 * 1000:.1f} ms",
        f"  P99:    {stats.ttfb_p99 * 1000:.1f} ms",
        f"  Min:    {stats.ttfb_min * 1000:.1f} ms",
        f"  Max:    {stats.ttfb_max * 1000:.1f} ms",
        "",
    ]

    if stats.ttfc_mean is not None:
        ttfc_lines = [
            "TIME TO FIRST CONTENT (TTFC):",
            f"  Mean:   {stats.ttfc_mean * 1000:.1f} ms",
            f"  Median: {stats.ttfc_median * 1000:.1f} ms",
        ]
        if stats.ttfc_p90 is not None:
            ttfc_lines.append(f"  P90:    {stats.ttfc_p90 * 1000:.1f} ms")
        if stats.ttfc_p95 is not None:
            ttfc_lines.append(f"  P95:    {stats.ttfc_p95 * 1000:.1f} ms")
        ttfc_lines.append("")
        lines.extend(ttfc_lines)

    lines.extend([
        "TOTAL DURATION:",
        f"  Mean:   {stats.duration_mean:.2f} s",
        f"  Median: {stats.duration_median:.2f} s",
        f"  P95:    {stats.duration_p95:.2f} s",
        "",
        "THROUGHPUT (tokens/second):",
        f"  Mean:   {stats.tps_mean:.1f} tps",
        f"  Median: {stats.tps_median:.1f} tps",
        f"  Min:    {stats.tps_min:.1f} tps",
        f"  Max:    {stats.tps_max:.1f} tps",
        "",
        "TOKEN USAGE:",
        f"  Avg Input:     {stats.avg_input_tokens:.0f} tokens",
        f"  Avg Output:    {stats.avg_output_tokens:.0f} tokens",
        f"  Avg Reasoning: {stats.avg_reasoning_tokens:.0f} tokens",
        f"  Total:         {stats.total_tokens_processed:,} tokens",
        "",
        "=" * 60,
    ])

    return "\n".join(lines)


def test_latency_tracker():
    """Test the latency tracker."""
    tracker = LatencyTracker()
    tracker.start_session()

    # Simulate some requests
    import random

    for _ in range(20):
        tracker.record_from_response(
            ttfb_seconds=random.uniform(0.3, 0.8),
            total_duration_seconds=random.uniform(2.0, 5.0),
            input_tokens=random.randint(100, 500),
            output_tokens=random.randint(200, 800),
            reasoning_tokens=random.randint(50, 200),
            ttfc_seconds=random.uniform(0.4, 1.0),
        )

    stats = tracker.get_stats()
    print(format_latency_report(stats))


if __name__ == "__main__":
    test_latency_tracker()
