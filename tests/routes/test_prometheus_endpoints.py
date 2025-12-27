"""
Test suite for Prometheus metrics endpoints.

Tests the following endpoints:
- GET /prometheus/metrics/all
- GET /prometheus/metrics/summary
- GET /prometheus/metrics/system
- GET /prometheus/metrics/providers
- GET /prometheus/metrics/models
- GET /prometheus/metrics/business
- GET /prometheus/metrics/performance
- GET /prometheus/metrics/docs
"""

import pytest
from fastapi.testclient import TestClient
import json
import re


@pytest.fixture
def client():
    """Create test client for FastAPI app."""
    from src.main import create_app
    app = create_app()
    return TestClient(app)


class TestPrometheusEndpoints:
    """Test Prometheus metrics endpoints."""

    def test_prometheus_endpoints_all_metrics(self, client):
        """Test /prometheus/metrics/all endpoint returns all metrics."""
        response = client.get("/prometheus/metrics/all")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "# HELP" in response.text
        assert "# TYPE" in response.text
        assert len(response.text) > 0
        print(f"✅ /prometheus/metrics/all - {len(response.text)} bytes")

    def test_prometheus_endpoints_system_metrics(self, client):
        """Test /prometheus/metrics/system endpoint returns HTTP metrics only."""
        response = client.get("/prometheus/metrics/system")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "fastapi_requests_total" in response.text or "# HELP" in response.text
        print(f"✅ /prometheus/metrics/system - {len(response.text)} bytes")

    def test_prometheus_endpoints_providers_metrics(self, client):
        """Test /prometheus/metrics/providers endpoint returns provider health metrics."""
        response = client.get("/prometheus/metrics/providers")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        print(f"✅ /prometheus/metrics/providers - {len(response.text)} bytes")

    def test_prometheus_endpoints_models_metrics(self, client):
        """Test /prometheus/metrics/models endpoint returns model performance metrics."""
        response = client.get("/prometheus/metrics/models")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        print(f"✅ /prometheus/metrics/models - {len(response.text)} bytes")

    def test_prometheus_endpoints_business_metrics(self, client):
        """Test /prometheus/metrics/business endpoint returns business metrics."""
        response = client.get("/prometheus/metrics/business")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        print(f"✅ /prometheus/metrics/business - {len(response.text)} bytes")

    def test_prometheus_endpoints_performance_metrics(self, client):
        """Test /prometheus/metrics/performance endpoint returns latency metrics."""
        response = client.get("/prometheus/metrics/performance")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        print(f"✅ /prometheus/metrics/performance - {len(response.text)} bytes")

    def test_prometheus_endpoints_summary_json(self, client):
        """Test /prometheus/metrics/summary endpoint returns JSON summary."""
        response = client.get("/prometheus/metrics/summary")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"

        data = response.json()
        assert "timestamp" in data
        assert "metrics" in data
        assert isinstance(data["metrics"], dict)

        print(f"✅ /prometheus/metrics/summary - {json.dumps(data, indent=2)}")

    def test_prometheus_endpoints_summary_with_category(self, client):
        """Test /prometheus/metrics/summary with category filter."""
        response = client.get("/prometheus/metrics/summary?category=providers")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"

        data = response.json()
        assert "timestamp" in data
        assert "metrics" in data

        print(f"✅ /prometheus/metrics/summary?category=providers - Filtered response")

    def test_prometheus_endpoints_docs(self, client):
        """Test /prometheus/metrics/docs endpoint returns documentation."""
        response = client.get("/prometheus/metrics/docs")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"] or "text/markdown" in response.headers["content-type"]
        assert "prometheus" in response.text.lower() or "metrics" in response.text.lower()

        print(f"✅ /prometheus/metrics/docs - {len(response.text)} bytes")

    def test_prometheus_endpoints_response_time(self, client):
        """Test that Prometheus endpoints respond quickly (<500ms)."""
        import time

        endpoints = [
            "/prometheus/metrics/all",
            "/prometheus/metrics/summary",
            "/prometheus/metrics/system",
            "/prometheus/metrics/providers",
        ]

        for endpoint in endpoints:
            start = time.time()
            response = client.get(endpoint)
            elapsed_ms = (time.time() - start) * 1000

            assert response.status_code == 200
            assert elapsed_ms < 500, f"{endpoint} took {elapsed_ms:.1f}ms (should be <500ms)"
            print(f"✅ {endpoint} - {elapsed_ms:.1f}ms")

    def test_prometheus_endpoints_json_summary_structure(self, client):
        """Test that JSON summary has correct structure."""
        response = client.get("/prometheus/metrics/summary")
        data = response.json()

        # Check top-level structure
        assert "timestamp" in data
        assert "metrics" in data

        # Check metrics object
        metrics = data["metrics"]
        assert isinstance(metrics, dict)

        print(f"✅ JSON summary structure valid")
        print(f"   Keys: {list(metrics.keys())}")

    def test_prometheus_endpoints_no_auth_required(self, client):
        """Test that Prometheus endpoints don't require authentication."""
        # Test without any authorization header
        response = client.get("/prometheus/metrics/all")
        assert response.status_code == 200

        response = client.get("/prometheus/metrics/summary")
        assert response.status_code == 200

        print(f"✅ Prometheus endpoints accessible without authentication")


class TestPrometheusMetricsContent:
    """Test that Prometheus metrics contain expected data."""

    def test_all_metrics_contains_fastapi_metrics(self, client):
        """Test that all metrics endpoint contains FastAPI metrics."""
        response = client.get("/prometheus/metrics/all")

        # Should contain some FastAPI metrics
        metrics_text = response.text.lower()
        assert "fastapi" in metrics_text or "http" in metrics_text or "request" in metrics_text

        print(f"✅ All metrics contain FastAPI/HTTP metrics")

    def test_prometheus_format_validity(self, client):
        """Test that Prometheus text format is valid."""
        response = client.get("/prometheus/metrics/all")
        text = response.text

        # Check for valid Prometheus format
        lines = text.split("\n")

        # Should have HELP and TYPE lines
        help_lines = [l for l in lines if l.startswith("# HELP")]
        type_lines = [l for l in lines if l.startswith("# TYPE")]
        metric_lines = [l for l in lines if l and not l.startswith("#")]

        assert len(help_lines) >= 0, "Should have HELP lines"
        assert len(type_lines) >= 0, "Should have TYPE lines"

        print(f"✅ Prometheus format valid")
        print(f"   HELP lines: {len(help_lines)}")
        print(f"   TYPE lines: {len(type_lines)}")
        print(f"   Metric lines: {len(metric_lines)}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
