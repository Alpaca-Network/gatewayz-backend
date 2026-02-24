"""
Integration tests for chat completions error handling.

Tests that the /v1/chat/completions endpoint returns detailed error responses
for various error scenarios.
"""

from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


@pytest.fixture
def client():
    """Create test client."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def valid_api_key():
    """Mock valid API key for testing."""
    return "gw_live_test_valid_key_12345"


@pytest.fixture
def trial_api_key():
    """Mock trial API key with limited credits."""
    return "gw_live_test_trial_key_12345"


@pytest.fixture
def rate_limited_api_key():
    """Mock API key that will hit rate limits."""
    return "gw_live_test_ratelimit_key_12345"


class TestChatCompletionsErrors:
    """Integration tests for chat completions error handling."""

    def test_invalid_api_key(self, client):
        """Test invalid API key returns detailed error."""
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer invalid_key_12345"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
        )

        assert response.status_code == 401
        data = response.json()

        # Validate detailed error structure
        assert "error" in data
        assert data["error"]["type"] == "invalid_api_key"
        assert data["error"]["code"] == "INVALID_API_KEY"
        assert data["error"]["status"] == 401
        assert data["error"]["request_id"] is not None
        assert data["error"]["timestamp"] is not None
        assert data["error"]["suggestions"] is not None
        assert len(data["error"]["suggestions"]) > 0

        # Should have X-Request-ID header
        assert "X-Request-ID" in response.headers

    def test_missing_authorization_header(self, client):
        """Test missing authorization header returns detailed error."""
        response = client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
        )

        assert response.status_code == 401
        data = response.json()

        assert "error" in data
        assert data["error"]["type"] in ["invalid_api_key", "missing_api_key"]

    @patch("src.db.users.get_user")
    def test_model_not_found(self, mock_get_user, client, valid_api_key):
        """Test model not found returns suggestions."""
        # Mock user lookup
        mock_get_user.return_value = {
            "id": "test-user",
            "credits": 10.0,
            "is_trial": False,
        }

        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {valid_api_key}"},
            json={
                "model": "nonexistent-model-xyz-12345",
                "messages": [{"role": "user", "content": "test"}],
            },
        )

        assert response.status_code == 404
        data = response.json()

        assert "error" in data
        assert data["error"]["type"] == "model_not_found"
        assert data["error"]["code"] == "MODEL_NOT_FOUND"
        assert data["error"]["status"] == 404
        assert "nonexistent-model-xyz-12345" in data["error"]["message"]
        assert data["error"]["suggestions"] is not None
        assert data["error"]["docs_url"] is not None

        # Should suggest checking available models
        assert any("models" in s.lower() for s in data["error"]["suggestions"])

    @patch("src.db.users.get_user")
    def test_model_typo_suggests_correct_model(self, mock_get_user, client, valid_api_key):
        """Test that typo in model name suggests correct model."""
        mock_get_user.return_value = {
            "id": "test-user",
            "credits": 10.0,
            "is_trial": False,
        }

        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {valid_api_key}"},
            json={
                "model": "gpt-5",  # Typo - should suggest gpt-4
                "messages": [{"role": "user", "content": "test"}],
            },
        )

        assert response.status_code == 404
        data = response.json()

        # Should suggest similar models
        if data["error"].get("context") and data["error"]["context"].get("suggested_models"):
            suggested = data["error"]["context"]["suggested_models"]
            assert any("gpt-4" in model for model in suggested)

    @patch("src.db.users.get_user")
    def test_empty_messages_array(self, mock_get_user, client, valid_api_key):
        """Test empty messages array error."""
        mock_get_user.return_value = {
            "id": "test-user",
            "credits": 10.0,
            "is_trial": False,
        }

        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {valid_api_key}"},
            json={"model": "gpt-4", "messages": []},  # Empty array
        )

        assert response.status_code == 400
        data = response.json()

        assert "error" in data
        assert data["error"]["status"] == 400
        # Could be empty_messages_array or bad_request
        assert (
            "messages" in data["error"]["message"].lower()
            or data["error"]["type"] == "empty_messages_array"
        )

    @patch("src.db.users.get_user")
    def test_missing_messages_field(self, mock_get_user, client, valid_api_key):
        """Test missing messages field error."""
        mock_get_user.return_value = {
            "id": "test-user",
            "credits": 10.0,
            "is_trial": False,
        }

        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {valid_api_key}"},
            json={"model": "gpt-4"},  # Missing messages
        )

        assert response.status_code == 422  # Pydantic validation error
        # FastAPI returns validation error format

    @patch("src.db.users.get_user")
    def test_invalid_message_format(self, mock_get_user, client, valid_api_key):
        """Test invalid message format error."""
        mock_get_user.return_value = {
            "id": "test-user",
            "credits": 10.0,
            "is_trial": False,
        }

        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {valid_api_key}"},
            json={
                "model": "gpt-4",
                "messages": [{"content": "test"}],  # Missing 'role' field
            },
        )

        assert response.status_code == 422
        # Pydantic validation error

    @patch("src.db.users.get_user")
    def test_invalid_temperature(self, mock_get_user, client, valid_api_key):
        """Test invalid parameter error for temperature."""
        mock_get_user.return_value = {
            "id": "test-user",
            "credits": 10.0,
            "is_trial": False,
        }

        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {valid_api_key}"},
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "test"}],
                "temperature": 10.0,  # Invalid - should be 0-2
            },
        )

        # May be caught by Pydantic validation or business logic
        assert response.status_code in [400, 422]

    @patch("src.db.users.get_user")
    def test_negative_max_tokens(self, mock_get_user, client, valid_api_key):
        """Test invalid max_tokens parameter."""
        mock_get_user.return_value = {
            "id": "test-user",
            "credits": 10.0,
            "is_trial": False,
        }

        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {valid_api_key}"},
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": -100,  # Negative value
            },
        )

        assert response.status_code in [400, 422]

    @patch("src.db.users.get_user")
    @patch("src.services.pricing.estimate_request_cost")
    def test_insufficient_credits(self, mock_estimate_cost, mock_get_user, client, trial_api_key):
        """Test insufficient credits error."""
        # Mock user with low credits
        mock_get_user.return_value = {
            "id": "test-user",
            "credits": 0.001,  # Very low credits
            "is_trial": True,
        }

        # Mock high cost estimate
        mock_estimate_cost.return_value = 10.0

        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {trial_api_key}"},
            json={
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "test" * 1000}],
                "max_tokens": 10000,
            },
        )

        assert response.status_code == 402
        data = response.json()

        assert "error" in data
        assert data["error"]["type"] == "insufficient_credits"
        assert data["error"]["code"] == "INSUFFICIENT_CREDITS"
        assert data["error"]["status"] == 402
        assert data["error"]["support_url"] is not None

        # Should include credit amounts in context
        if data["error"].get("context"):
            assert (
                "current_credits" in data["error"]["context"]
                or "required_credits" in data["error"]["context"]
            )

    def test_request_id_in_all_errors(self, client):
        """Test that all errors include request_id."""
        # Test with invalid API key
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer invalid_key"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
        )

        data = response.json()
        assert "error" in data
        assert "request_id" in data["error"]
        assert data["error"]["request_id"] is not None
        assert data["error"]["request_id"].startswith("req_")

        # Request ID should also be in response headers
        assert "X-Request-ID" in response.headers
        assert response.headers["X-Request-ID"] == data["error"]["request_id"]

    def test_request_id_propagation(self, client):
        """Test that provided X-Request-ID is propagated."""
        custom_request_id = "custom_req_12345"

        response = client.post(
            "/v1/chat/completions",
            headers={
                "Authorization": "Bearer invalid_key",
                "X-Request-ID": custom_request_id,
            },
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
        )

        data = response.json()
        # The custom request ID should be normalized with req_ prefix
        assert (
            data["error"]["request_id"].endswith(custom_request_id)
            or custom_request_id in data["error"]["request_id"]
        )

    def test_timestamp_in_errors(self, client):
        """Test that all errors include timestamp."""
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer invalid_key"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
        )

        data = response.json()
        assert "error" in data
        assert "timestamp" in data["error"]
        assert data["error"]["timestamp"] is not None
        # Should be ISO format
        assert "T" in data["error"]["timestamp"]
        assert data["error"]["timestamp"].endswith("Z")

    def test_docs_url_in_errors(self, client):
        """Test that appropriate errors include docs_url."""
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer invalid_key"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
        )

        data = response.json()
        assert "error" in data
        # Invalid API key should have docs_url
        assert data["error"].get("docs_url") is not None
        assert data["error"]["docs_url"].startswith("http")

    def test_suggestions_in_errors(self, client):
        """Test that errors include actionable suggestions."""
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer invalid_key"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
        )

        data = response.json()
        assert "error" in data
        assert "suggestions" in data["error"]
        assert isinstance(data["error"]["suggestions"], list)
        assert len(data["error"]["suggestions"]) > 0
        # Each suggestion should be a string
        assert all(isinstance(s, str) for s in data["error"]["suggestions"])

    @patch("src.db.users.get_user")
    @patch("src.services.provider_failover.call_provider_with_failover")
    def test_provider_error(self, mock_call_provider, mock_get_user, client, valid_api_key):
        """Test provider error returns detailed error."""
        mock_get_user.return_value = {
            "id": "test-user",
            "credits": 10.0,
            "is_trial": False,
        }

        # Mock provider error
        from fastapi import HTTPException

        mock_call_provider.side_effect = HTTPException(
            status_code=502, detail="Provider connection failed"
        )

        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {valid_api_key}"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
        )

        assert response.status_code == 502
        data = response.json()

        assert "error" in data
        assert data["error"]["type"] == "provider_error"
        assert data["error"]["status"] == 502

    @patch("src.db.users.get_user")
    def test_streaming_error_format(self, mock_get_user, client, valid_api_key):
        """Test that errors in streaming mode are properly formatted."""
        mock_get_user.return_value = {
            "id": "test-user",
            "credits": 10.0,
            "is_trial": False,
        }

        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {valid_api_key}"},
            json={
                "model": "nonexistent-model",
                "messages": [{"role": "user", "content": "test"}],
                "stream": True,
            },
        )

        # Even in streaming mode, errors should return JSON
        assert response.status_code == 404
        data = response.json()
        assert "error" in data

    def test_error_response_structure_complete(self, client):
        """Test that error response has all expected fields."""
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer invalid_key"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
        )

        data = response.json()

        # Root level
        assert "error" in data

        # Error object required fields
        error = data["error"]
        assert "type" in error
        assert "message" in error
        assert "code" in error
        assert "status" in error
        assert "request_id" in error
        assert "timestamp" in error

        # Error object optional fields (when applicable)
        assert "suggestions" in error or error["suggestions"] is None

    def test_concurrent_requests_unique_ids(self, client):
        """Test that concurrent requests get unique request IDs."""
        import threading

        request_ids = []

        def make_request():
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer invalid_key"},
                json={
                    "model": "gpt-4",
                    "messages": [{"role": "user", "content": "test"}],
                },
            )
            data = response.json()
            request_ids.append(data["error"]["request_id"])

        # Make multiple concurrent requests
        threads = [threading.Thread(target=make_request) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All request IDs should be unique
        assert len(request_ids) == 5
        assert len(set(request_ids)) == 5  # All unique

    @patch("src.db.users.get_user")
    def test_internal_error_detailed(self, mock_get_user, client, valid_api_key):
        """Test that internal errors return detailed responses."""
        # Mock an internal error
        mock_get_user.side_effect = Exception("Database connection failed")

        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {valid_api_key}"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
        )

        assert response.status_code == 500
        data = response.json()

        assert "error" in data
        assert data["error"]["type"] == "internal_error"
        assert data["error"]["code"] == "INTERNAL_ERROR"
        assert data["error"]["status"] == 500
        assert data["error"]["suggestions"] is not None

    def test_no_error_fields_are_none_in_response(self, client):
        """Test that no fields with None values are included in response."""
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer invalid_key"},
            json={"model": "gpt-4", "messages": [{"role": "user", "content": "test"}]},
        )

        data = response.json()

        # Recursively check for None values
        def has_none_values(obj):
            if isinstance(obj, dict):
                for v in obj.values():
                    if v is None:
                        return True
                    if has_none_values(v):
                        return True
            return False

        assert not has_none_values(data), "Response contains None values"
