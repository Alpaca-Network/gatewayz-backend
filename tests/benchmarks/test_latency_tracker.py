"""Tests for latency tracker module."""

import sys
from pathlib import Path

import pytest

# Add benchmark scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts" / "benchmarks"))

from latency_tracker import (
    CategoryLatencyTracker,
    LatencyMetrics,
    LatencyStats,
    LatencyTracker,
    format_latency_report,
)


class TestLatencyMetrics:
    """Tests for LatencyMetrics dataclass."""

    def test_tokens_per_second(self):
        """Test TPS calculation."""
        metrics = LatencyMetrics(
            ttfb_seconds=0.5,
            ttfc_seconds=0.6,
            total_duration_seconds=2.0,
            input_tokens=100,
            output_tokens=400,
            reasoning_tokens=100,
        )

        assert metrics.tokens_per_second == 200.0  # 400 / 2.0

    def test_total_output_tokens(self):
        """Test total output tokens including reasoning."""
        metrics = LatencyMetrics(
            ttfb_seconds=0.5,
            ttfc_seconds=0.6,
            total_duration_seconds=2.0,
            input_tokens=100,
            output_tokens=400,
            reasoning_tokens=100,
        )

        assert metrics.total_output_tokens == 500  # 400 + 100

    def test_effective_tps(self):
        """Test effective TPS including reasoning tokens."""
        metrics = LatencyMetrics(
            ttfb_seconds=0.5,
            ttfc_seconds=0.6,
            total_duration_seconds=2.0,
            input_tokens=100,
            output_tokens=400,
            reasoning_tokens=100,
        )

        assert metrics.effective_tps == 250.0  # (400 + 100) / 2.0

    def test_zero_duration_tps(self):
        """Test TPS with zero duration returns 0."""
        metrics = LatencyMetrics(
            ttfb_seconds=0.0,
            ttfc_seconds=None,
            total_duration_seconds=0.0,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
        )

        assert metrics.tokens_per_second == 0.0
        assert metrics.effective_tps == 0.0


class TestLatencyTracker:
    """Tests for LatencyTracker class."""

    def test_record_metrics(self):
        """Test recording metrics."""
        tracker = LatencyTracker()

        metrics = LatencyMetrics(
            ttfb_seconds=0.5,
            ttfc_seconds=0.6,
            total_duration_seconds=2.0,
            input_tokens=100,
            output_tokens=200,
            reasoning_tokens=50,
        )

        tracker.record(metrics)
        assert len(tracker.metrics) == 1
        assert tracker.metrics[0] == metrics

    def test_record_from_response(self):
        """Test recording from response data."""
        tracker = LatencyTracker()

        result = tracker.record_from_response(
            ttfb_seconds=0.3,
            total_duration_seconds=1.5,
            input_tokens=50,
            output_tokens=100,
            reasoning_tokens=25,
            ttfc_seconds=0.4,
        )

        assert len(tracker.metrics) == 1
        assert result.ttfb_seconds == 0.3
        assert result.ttfc_seconds == 0.4

    def test_get_stats(self):
        """Test getting aggregated stats."""
        tracker = LatencyTracker()

        # Add some metrics
        for i in range(10):
            tracker.record_from_response(
                ttfb_seconds=0.3 + i * 0.1,
                total_duration_seconds=1.5 + i * 0.2,
                input_tokens=50 + i * 10,
                output_tokens=100 + i * 20,
                reasoning_tokens=25,
            )

        stats = tracker.get_stats()

        assert stats.sample_count == 10
        assert stats.ttfb_min == 0.3
        assert stats.ttfb_max == pytest.approx(1.2, rel=0.01)
        assert stats.ttfb_mean > 0
        assert stats.tps_mean > 0

    def test_get_stats_empty_raises(self):
        """Test that getting stats with no data raises error."""
        tracker = LatencyTracker()

        with pytest.raises(ValueError, match="No metrics recorded"):
            tracker.get_stats()

    def test_percentile_calculation(self):
        """Test percentile calculations."""
        tracker = LatencyTracker()

        # Add 100 metrics with known distribution
        for i in range(100):
            tracker.record_from_response(
                ttfb_seconds=i / 100.0,  # 0.00 to 0.99
                total_duration_seconds=1.0,
                input_tokens=100,
                output_tokens=100,
                reasoning_tokens=0,
            )

        stats = tracker.get_stats()

        # P50 should be around 0.50
        assert stats.ttfb_p50 == pytest.approx(0.50, abs=0.02)
        # P95 should be around 0.95
        assert stats.ttfb_p95 == pytest.approx(0.95, abs=0.02)

    def test_session_duration(self):
        """Test session duration tracking."""
        tracker = LatencyTracker()

        # No session started
        assert tracker.get_session_duration() is None

        tracker.start_session()
        import time

        time.sleep(0.1)
        duration = tracker.get_session_duration()

        assert duration is not None
        assert duration >= 0.1

    def test_to_dict(self):
        """Test exporting to dictionary."""
        tracker = LatencyTracker()
        tracker.start_session()

        tracker.record_from_response(
            ttfb_seconds=0.5,
            total_duration_seconds=2.0,
            input_tokens=100,
            output_tokens=200,
            reasoning_tokens=50,
        )

        data = tracker.to_dict()

        assert "metrics" in data
        assert "session_duration" in data
        assert len(data["metrics"]) == 1
        assert data["metrics"][0]["ttfb_seconds"] == 0.5


class TestCategoryLatencyTracker:
    """Tests for CategoryLatencyTracker class."""

    def test_track_by_category(self):
        """Test tracking metrics by category."""
        tracker = CategoryLatencyTracker()

        # Record for different categories
        tracker.record(
            category="code_generation",
            ttfb_seconds=0.5,
            total_duration_seconds=2.0,
            input_tokens=100,
            output_tokens=200,
            reasoning_tokens=50,
        )

        tracker.record(
            category="debugging",
            ttfb_seconds=0.3,
            total_duration_seconds=1.5,
            input_tokens=50,
            output_tokens=100,
            reasoning_tokens=25,
        )

        assert len(tracker.trackers) == 2
        assert len(tracker.get_tracker("code_generation").metrics) == 1
        assert len(tracker.get_tracker("debugging").metrics) == 1

    def test_get_all_stats(self):
        """Test getting stats for all categories."""
        tracker = CategoryLatencyTracker()

        for category in ["code_generation", "debugging", "reasoning"]:
            for i in range(5):
                tracker.record(
                    category=category,
                    ttfb_seconds=0.3 + i * 0.1,
                    total_duration_seconds=1.5,
                    input_tokens=100,
                    output_tokens=200,
                    reasoning_tokens=50,
                )

        all_stats = tracker.get_all_stats()

        assert len(all_stats) == 3
        assert "code_generation" in all_stats
        assert "debugging" in all_stats
        assert "reasoning" in all_stats
        assert all_stats["code_generation"].sample_count == 5

    def test_get_overall_stats(self):
        """Test getting combined stats across categories."""
        tracker = CategoryLatencyTracker()

        for category in ["code_generation", "debugging"]:
            for i in range(5):
                tracker.record(
                    category=category,
                    ttfb_seconds=0.3,
                    total_duration_seconds=1.5,
                    input_tokens=100,
                    output_tokens=200,
                    reasoning_tokens=50,
                )

        overall = tracker.get_overall_stats()

        assert overall.sample_count == 10  # 5 * 2 categories


class TestFormatLatencyReport:
    """Tests for latency report formatting."""

    def test_format_report(self):
        """Test formatting a latency report."""
        tracker = LatencyTracker()

        for i in range(20):
            tracker.record_from_response(
                ttfb_seconds=0.3 + i * 0.05,
                total_duration_seconds=2.0 + i * 0.1,
                input_tokens=100 + i * 10,
                output_tokens=200 + i * 20,
                reasoning_tokens=50,
                ttfc_seconds=0.4 + i * 0.05,
            )

        stats = tracker.get_stats()
        report = format_latency_report(stats)

        assert "LATENCY REPORT" in report
        assert "TIME TO FIRST BYTE" in report
        assert "TIME TO FIRST CONTENT" in report
        assert "THROUGHPUT" in report
        assert "TOKEN USAGE" in report
        assert "Sample Size: 20 requests" in report

    def test_format_report_without_ttfc(self):
        """Test formatting report without TTFC data."""
        tracker = LatencyTracker()

        for i in range(10):
            tracker.record_from_response(
                ttfb_seconds=0.3,
                total_duration_seconds=2.0,
                input_tokens=100,
                output_tokens=200,
                reasoning_tokens=50,
                ttfc_seconds=None,  # No TTFC
            )

        stats = tracker.get_stats()
        report = format_latency_report(stats)

        # Should not have TTFC section
        assert "TIME TO FIRST BYTE" in report
        assert "THROUGHPUT" in report
