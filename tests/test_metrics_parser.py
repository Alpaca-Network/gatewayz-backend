"""
Tests for the Prometheus metrics parser.
"""

import pytest

from src.services.metrics_parser import PrometheusMetricsParser


class TestPrometheusMetricsParser:
    """Test suite for PrometheusMetricsParser."""

    def test_parse_latency_metrics(self):
        """Test parsing of latency metrics."""
        metrics_text = """
# HELP http_request_latency_seconds_bucket HTTP request latency in seconds
# TYPE http_request_latency_seconds_bucket histogram
http_request_latency_seconds_bucket{endpoint="/api/test",le="0.01"} 10
http_request_latency_seconds_bucket{endpoint="/api/test",le="0.1"} 50
http_request_latency_seconds_bucket{endpoint="/api/test",le="1.0"} 95
http_request_latency_seconds_bucket{endpoint="/api/test",le="+Inf"} 100
http_request_latency_seconds_sum{endpoint="/api/test"} 25.5
http_request_latency_seconds_count{endpoint="/api/test"} 100
"""
        parser = PrometheusMetricsParser()
        result = parser.parse_metrics(metrics_text)

        assert "/api/test" in result["latency"]
        latency = result["latency"]["/api/test"]

        # Average should be sum/count = 25.5/100 = 0.255
        assert latency["avg"] == pytest.approx(0.255, rel=0.01)

        # Percentiles should be computed from buckets
        assert latency["p50"] is not None
        assert latency["p95"] is not None
        assert latency["p99"] is not None

    def test_parse_request_counts(self):
        """Test parsing of request count metrics."""
        metrics_text = """
# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total{endpoint="/api/test",method="GET"} 100
http_requests_total{endpoint="/api/test",method="POST"} 50
http_requests_total{endpoint="/api/other",method="GET"} 25
"""
        parser = PrometheusMetricsParser()
        result = parser.parse_metrics(metrics_text)

        assert "/api/test" in result["requests"]
        assert result["requests"]["/api/test"]["GET"] == 100
        assert result["requests"]["/api/test"]["POST"] == 50

        assert "/api/other" in result["requests"]
        assert result["requests"]["/api/other"]["GET"] == 25

    def test_parse_error_counts(self):
        """Test parsing of error count metrics."""
        metrics_text = """
# HELP http_request_errors_total Total HTTP request errors
# TYPE http_request_errors_total counter
http_request_errors_total{endpoint="/api/test",method="GET"} 5
http_request_errors_total{endpoint="/api/test",method="POST"} 2
http_request_errors_total{endpoint="/api/other",method="GET"} 1
"""
        parser = PrometheusMetricsParser()
        result = parser.parse_metrics(metrics_text)

        assert "/api/test" in result["errors"]
        assert result["errors"]["/api/test"]["GET"] == 5
        assert result["errors"]["/api/test"]["POST"] == 2

        assert "/api/other" in result["errors"]
        assert result["errors"]["/api/other"]["GET"] == 1

    def test_parse_combined_metrics(self):
        """Test parsing of all metric types together."""
        metrics_text = """
# HELP http_request_latency_seconds_bucket HTTP request latency in seconds
# TYPE http_request_latency_seconds_bucket histogram
http_request_latency_seconds_bucket{endpoint="/api/test",le="0.1"} 50
http_request_latency_seconds_bucket{endpoint="/api/test",le="1.0"} 95
http_request_latency_seconds_bucket{endpoint="/api/test",le="+Inf"} 100
http_request_latency_seconds_sum{endpoint="/api/test"} 25.5
http_request_latency_seconds_count{endpoint="/api/test"} 100

# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total{endpoint="/api/test",method="GET"} 100
http_requests_total{endpoint="/api/test",method="POST"} 50

# HELP http_request_errors_total Total HTTP request errors
# TYPE http_request_errors_total counter
http_request_errors_total{endpoint="/api/test",method="GET"} 5
http_request_errors_total{endpoint="/api/test",method="POST"} 2
"""
        parser = PrometheusMetricsParser()
        result = parser.parse_metrics(metrics_text)

        # Check latency
        assert "/api/test" in result["latency"]
        assert result["latency"]["/api/test"]["avg"] == pytest.approx(0.255, rel=0.01)

        # Check requests
        assert result["requests"]["/api/test"]["GET"] == 100
        assert result["requests"]["/api/test"]["POST"] == 50

        # Check errors
        assert result["errors"]["/api/test"]["GET"] == 5
        assert result["errors"]["/api/test"]["POST"] == 2

    def test_parse_empty_metrics(self):
        """Test parsing of empty metrics."""
        metrics_text = "# No metrics\n"
        parser = PrometheusMetricsParser()
        result = parser.parse_metrics(metrics_text)

        assert result["latency"] == {}
        assert result["requests"] == {}
        assert result["errors"] == {}

    def test_parse_metrics_with_comments(self):
        """Test that comments are properly ignored."""
        metrics_text = """
# This is a comment
# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
# Another comment
http_requests_total{endpoint="/api/test",method="GET"} 100
# Final comment
"""
        parser = PrometheusMetricsParser()
        result = parser.parse_metrics(metrics_text)

        assert result["requests"]["/api/test"]["GET"] == 100

    def test_percentile_calculation(self):
        """Test percentile calculation from histogram buckets."""
        # Create buckets with known distribution
        # 10 requests in [0, 0.01]
        # 40 more in (0.01, 0.1] (total 50)
        # 40 more in (0.1, 1.0] (total 90)
        # 10 more in (1.0, +Inf] (total 100)
        buckets = [
            (0.01, 10),
            (0.1, 50),
            (1.0, 90),
            (float("inf"), 100),
        ]

        parser = PrometheusMetricsParser()

        # p50 should be around 0.1 (50th percentile at bucket boundary)
        p50 = parser._calculate_percentile(buckets, 0.50)
        assert p50 is not None
        assert p50 <= 0.1

        # p95 should be around 0.9 (95th percentile in the 0.1-1.0 bucket)
        p95 = parser._calculate_percentile(buckets, 0.95)
        assert p95 is not None
        assert 0.1 <= p95 <= 1.0

        # p99 should be around 0.99 (99th percentile in the 0.1-1.0 bucket)
        p99 = parser._calculate_percentile(buckets, 0.99)
        assert p99 is not None
        assert 0.1 <= p99 <= 1.0

    def test_percentile_with_empty_buckets(self):
        """Test percentile calculation with empty bucket list."""
        parser = PrometheusMetricsParser()
        result = parser._calculate_percentile([], 0.50)
        assert result is None

    def test_percentile_with_zero_count(self):
        """Test percentile calculation with zero count."""
        buckets = [(0.1, 0), (1.0, 0), (float("inf"), 0)]
        parser = PrometheusMetricsParser()
        result = parser._calculate_percentile(buckets, 0.50)
        assert result is None

    def test_multiple_endpoints(self):
        """Test parsing metrics from multiple endpoints."""
        metrics_text = """
http_requests_total{endpoint="/api/users",method="GET"} 200
http_requests_total{endpoint="/api/users",method="POST"} 50
http_requests_total{endpoint="/api/posts",method="GET"} 150
http_requests_total{endpoint="/api/posts",method="DELETE"} 30

http_request_latency_seconds_sum{endpoint="/api/users"} 10.0
http_request_latency_seconds_count{endpoint="/api/users"} 250
http_request_latency_seconds_sum{endpoint="/api/posts"} 8.0
http_request_latency_seconds_count{endpoint="/api/posts"} 180
"""
        parser = PrometheusMetricsParser()
        result = parser.parse_metrics(metrics_text)

        # Check /api/users
        assert result["requests"]["/api/users"]["GET"] == 200
        assert result["requests"]["/api/users"]["POST"] == 50
        assert result["latency"]["/api/users"]["avg"] == pytest.approx(10.0 / 250, rel=0.01)

        # Check /api/posts
        assert result["requests"]["/api/posts"]["GET"] == 150
        assert result["requests"]["/api/posts"]["DELETE"] == 30
        assert result["latency"]["/api/posts"]["avg"] == pytest.approx(8.0 / 180, rel=0.01)
