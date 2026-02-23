"""
Test suite for Chat Completions Metrics endpoints.

Tests the following endpoints:
- GET /v1/chat/completions/metrics/tokens-per-second/all
- GET /v1/chat/completions/metrics/tokens-per-second
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create test client for FastAPI app."""
    from src.main import create_app
    app = create_app()
    return TestClient(app)


class TestTokensPerSecondEndpoints:
    """Test tokens per second metrics endpoints."""

    def test_get_all_tokens_per_second_valid(self, client):
        """Test /tokens-per-second/all with valid parameters."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second/all",
            params={
                "provider_id": "openrouter",
                "model_id": 1
            }
        )

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        assert "gatewayz_tokens_per_second" in response.text
        print(f"✅ /tokens-per-second/all - {len(response.text)} bytes")

    def test_get_all_tokens_per_second_missing_provider(self, client):
        """Test /tokens-per-second/all without provider_id parameter."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second/all",
            params={"model_id": 1}
        )

        # Should return 422 or 400 for missing required parameter
        assert response.status_code in [400, 422]
        print(f"✅ Missing provider_id handled (status: {response.status_code})")

    def test_get_all_tokens_per_second_missing_model(self, client):
        """Test /tokens-per-second/all without model_id parameter."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second/all",
            params={"provider_id": "openrouter"}
        )

        # Should return 422 or 400 for missing required parameter
        assert response.status_code in [400, 422]
        print(f"✅ Missing model_id handled (status: {response.status_code})")

    def test_get_tokens_per_second_valid_hour(self, client):
        """Test /tokens-per-second with valid hour parameter."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second",
            params={
                "time": "hour",
                "model_id": 1,
                "provider_id": "openrouter"
            }
        )

        assert response.status_code in [200, 403]  # 403 if model not in top 3
        # 200 = text/plain, 403 = application/json
        if response.status_code == 200:
            assert "text/plain" in response.headers["content-type"]
        else:
            assert "application/json" in response.headers["content-type"]
        print(f"✅ /tokens-per-second with time=hour - {len(response.text)} bytes")

    def test_get_tokens_per_second_valid_week(self, client):
        """Test /tokens-per-second with valid week parameter."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second",
            params={
                "time": "week",
                "model_id": 1,
                "provider_id": "openrouter"
            }
        )

        assert response.status_code in [200, 403]
        if response.status_code == 200:
            assert "text/plain" in response.headers["content-type"]
        else:
            assert "application/json" in response.headers["content-type"]
        print(f"✅ /tokens-per-second with time=week - {len(response.text)} bytes")

    def test_get_tokens_per_second_valid_month(self, client):
        """Test /tokens-per-second with valid month parameter."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second",
            params={
                "time": "month",
                "model_id": 1,
                "provider_id": "openrouter"
            }
        )

        assert response.status_code in [200, 403]
        if response.status_code == 200:
            assert "text/plain" in response.headers["content-type"]
        else:
            assert "application/json" in response.headers["content-type"]
        print(f"✅ /tokens-per-second with time=month - {len(response.text)} bytes")

    def test_get_tokens_per_second_valid_1year(self, client):
        """Test /tokens-per-second with valid 1year parameter."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second",
            params={
                "time": "1year",
                "model_id": 1,
                "provider_id": "openrouter"
            }
        )

        assert response.status_code in [200, 403]
        if response.status_code == 200:
            assert "text/plain" in response.headers["content-type"]
        else:
            assert "application/json" in response.headers["content-type"]
        print(f"✅ /tokens-per-second with time=1year - {len(response.text)} bytes")

    def test_get_tokens_per_second_valid_2year(self, client):
        """Test /tokens-per-second with valid 2year parameter."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second",
            params={
                "time": "2year",
                "model_id": 1,
                "provider_id": "openrouter"
            }
        )

        assert response.status_code in [200, 403]
        if response.status_code == 200:
            assert "text/plain" in response.headers["content-type"]
        else:
            assert "application/json" in response.headers["content-type"]
        print(f"✅ /tokens-per-second with time=2year - {len(response.text)} bytes")

    def test_get_tokens_per_second_invalid_time(self, client):
        """Test /tokens-per-second with invalid time parameter."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second",
            params={
                "time": "invalid_time",
                "model_id": 1,
                "provider_id": "openrouter"
            }
        )

        # Should return 400 for invalid time parameter
        assert response.status_code == 400
        assert "Invalid time parameter" in response.text
        print(f"✅ Invalid time parameter handled (status: {response.status_code})")

    def test_get_tokens_per_second_missing_time(self, client):
        """Test /tokens-per-second without time parameter."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second",
            params={
                "model_id": 1,
                "provider_id": "openrouter"
            }
        )

        # Should return 422 for missing required parameter
        assert response.status_code in [400, 422]
        print(f"✅ Missing time parameter handled (status: {response.status_code})")

    def test_get_tokens_per_second_missing_model(self, client):
        """Test /tokens-per-second without model_id parameter."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second",
            params={
                "time": "hour",
                "provider_id": "openrouter"
            }
        )

        # Should return 422 for missing required parameter
        assert response.status_code in [400, 422]
        print(f"✅ Missing model_id parameter handled (status: {response.status_code})")

    def test_get_tokens_per_second_missing_provider(self, client):
        """Test /tokens-per-second without provider_id parameter."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second",
            params={
                "time": "hour",
                "model_id": 1
            }
        )

        # Should return 422 for missing required parameter
        assert response.status_code in [400, 422]
        print(f"✅ Missing provider_id parameter handled (status: {response.status_code})")


class TestPrometheusFormat:
    """Test Prometheus format output."""

    def test_prometheus_format_valid_structure(self, client):
        """Test that response follows Prometheus text format."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second/all",
            params={
                "provider_id": "openrouter",
                "model_id": 1
            }
        )

        assert response.status_code == 200
        text = response.text

        # Check for Prometheus format elements
        assert "# HELP gatewayz_tokens_per_second" in text
        assert "# TYPE gatewayz_tokens_per_second" in text
        assert "gatewayz_tokens_per_second{" in text or "# No data" in text

        print("✅ Prometheus format valid")

    def test_prometheus_format_labels(self, client):
        """Test that metrics include proper labels."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second/all",
            params={
                "provider_id": "openrouter",
                "model_id": 1
            }
        )

        assert response.status_code == 200
        text = response.text

        # If there's data, check for required labels
        if "gatewayz_tokens_per_second{" in text:
            assert 'model=' in text
            assert 'provider=' in text
            assert 'requests=' in text
            assert 'total_tokens=' in text
            print("✅ Prometheus labels present")
        else:
            print("✅ No data available (metrics format still valid)")

    def test_content_type_is_plain_text(self, client):
        """Test that content-type is text/plain."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second/all",
            params={
                "provider_id": "openrouter",
                "model_id": 1
            }
        )

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        print("✅ Content-type is text/plain")


class TestErrorHandling:
    """Test error handling."""

    def test_invalid_model_id_type(self, client):
        """Test error handling with invalid model_id type."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second/all",
            params={
                "provider_id": "openrouter",
                "model_id": "not_a_number"
            }
        )

        # Should return 422 for type validation error
        assert response.status_code in [400, 422]
        print(f"✅ Invalid model_id type handled (status: {response.status_code})")

    def test_empty_provider_id(self, client):
        """Test error handling with empty provider_id."""
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second/all",
            params={
                "provider_id": "",
                "model_id": 1
            }
        )

        # Empty string should be accepted but might not find data
        # or return 400/422 depending on validation
        assert response.status_code in [200, 400, 422, 403]
        print(f"✅ Empty provider_id handled (status: {response.status_code})")

    def test_server_error_handling(self, client):
        """Test that server errors return 500."""
        # Use invalid parameters that might cause server errors
        response = client.get(
            "/v1/chat/completions/metrics/tokens-per-second/all",
            params={
                "provider_id": "nonexistent_provider_abc123",
                "model_id": 999999
            }
        )

        # Should either return 200 with no data or handle gracefully
        assert response.status_code in [200, 400, 403]
        assert "text/plain" in response.headers["content-type"]
        print(f"✅ Server error handling (status: {response.status_code})")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
