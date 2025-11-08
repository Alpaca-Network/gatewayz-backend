"""Integration tests for Google Vertex AI with id_token fallback

Tests that verify the entire flow works when Google's OAuth2 endpoint
returns an id_token instead of an access_token.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.services.google_vertex_client import get_google_vertex_access_token


class TestGoogleVertexIdTokenIntegration:
    """Integration tests for Google Vertex AI with id_token fallback"""

    @patch.dict("os.environ", {"GOOGLE_VERTEX_CREDENTIALS_JSON": '{"client_email": "test@test.iam.gserviceaccount.com", "private_key": "-----BEGIN RSA PRIVATE KEY-----\\nMIIEpAIBAAKCAQEA2a2rwplEf13eQSSIVVzj2H6B8ZWw1j5JvDWl0fvlV7+2pX+9\\nDgCJW0tNdW6OkU7ZO+pf3h5CmRDRlLwzE7Vq8OhKvLxXj3A9J8JKqZKb7N5cRvQQ\\nRMzKqKQ0KDgLkgQGjK/zzSqGMW7jYHx7q4HLiQGPBRxJF0PH2uNQpQIDAQABAoIB\\nACZh8zHgVf7VHpKEfsjJKFxM5A2ZHHnQ7PxOc5nfMjWdCfJVl1N0+1jM/8x8X7Qm\\nc5T2h0sZWXrCRzs1H3LnBrV7Y7VQVM2TXsLhQD0kJqDmGVKhVYlCvKWqVBlCfKvG\\nA7C1EqKQj7lw3dZJZGR8GwCVHX3J1zMzVEbPY0bCqTAoNHn8h3SzVlJGLQz6Zchs\\nf7CqXQc8W9dMqCR4SqVZJi6D5C+nCgYz5ZPz5jkQT5Vvq5K0dLVJCNyUFCEm8HhH\\nKqPgKJIGPOLTL1UKq+1QQ1j3Gr0pZPYQVQ9XVSl6LJ2dJZ7ZxN8nGZ5WEE9C5/Px\\nLZ9EXAECgYEA7nDQaHxYnM0L8PnGm4r0EjFQpfcM3Q8H0gzvKL0ZvUKCMpZ3GdZP\\nzMlE4qZGKKDXqYVDRlQjR2xFhqNjK6fZhHc5dYOLqcBFXLkJ5V0BgKYvLQe8PjVu\\nRvM4b9MJ9uXJKBCLLeCvYcsLVHxGhTvVNvV7MQHX3nqZPqf3h7ECgYEA6ZGlVRlG\\nQQ5R5b5VU3f3G9u5y6MWh2zMPWPKtSP3C4xvO5OBlqxzI5c3wZGkHHsGBGPMHqFe\\njvC+pLPAiS/BYeNMX+gEmJmjmVXlC8pKL6dkqQGbJNvDNmJVhqCzKuVKXm0VzMWv\\nyVN0cHPLtmZwJlKzEOAr+rR6Aj9CfKCEXQECgYBSJBLqLm3J8o/K8K6bh2lGCxHd\\nx2T1E7s7BvJAJ2K7Z3+2FlB0r7tBqvDkzLDj7a2B9AE0nDY8U6pXNHphfv5g5ufU\\nohzSzVzcwYk4Fw3VBYCHYYZqM4r4EZL5LfwQqQc8IYxVDMLh6qVwH5K3SqQw1Mct\\nnVYW5fQp/1i5EQKBgQCNzF8lIQD3QsQsAhjQBGpuHxQJ/K4jvbZQV0u3gIxTD1/r\\njGI0RU1bwODjGj6bnmVfVXDzHYNOwpYSvQXtNZhBLB8vF5E4wPYKqFcpuTq5MJVJ\\nzAWWyJCRDc+CjXKgJpRZmZSLqP7r5CtB0W8hQ9S3EXExQZmRJZmN1QEBAoGAWLk0\\n5Z6QS8xXLhX1dXgj/LAG4gPE9R2VvVlbfQIGwKjX9F1n9kKHBVKVTQqKN3c0CQAK\\nvTYSVGJOUwX0HsU7sXDXdcL9F5cQqVZEu6rN4TgzLVT1OhKvFP2DPjnRTM3W0fFU\\nW5v8z4HN8hCDZIL6DqCkHpGIX/cz9x8mWcI=\\n-----END RSA PRIVATE KEY-----"}'})
    def test_get_google_vertex_access_token_with_id_token(self):
        """Test that get_google_vertex_access_token works with id_token response"""

        # Mock the HTTP response to return only id_token (not access_token)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id_token": "test_id_token_jwt_value",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            # This should not raise an error even though id_token is returned
            token = get_google_vertex_access_token()

            # Verify the token is the id_token value
            assert token == "test_id_token_jwt_value"

    @patch.dict("os.environ", {"GOOGLE_VERTEX_CREDENTIALS_JSON": '{"client_email": "test@test.iam.gserviceaccount.com", "private_key": "-----BEGIN RSA PRIVATE KEY-----\\nMIIEpAIBAAKCAQEA2a2rwplEf13eQSSIVVzj2H6B8ZWw1j5JvDWl0fvlV7+2pX+9\\nDgCJW0tNdW6OkU7ZO+pf3h5CmRDRlLwzE7Vq8OhKvLxXj3A9J8JKqZKb7N5cRvQQ\\nRMzKqKQ0KDgLkgQGjK/zzSqGMW7jYHx7q4HLiQGPBRxJF0PH2uNQpQIDAQABAoIB\\nACZh8zHgVf7VHpKEfsjJKFxM5A2ZHHnQ7PxOc5nfMjWdCfJVl1N0+1jM/8x8X7Qm\\nc5T2h0sZWXrCRzs1H3LnBrV7Y7VQVM2TXsLhQD0kJqDmGVKhVYlCvKWqVBlCfKvG\\nA7C1EqKQj7lw3dZJZGR8GwCVHX3J1zMzVEbPY0bCqTAoNHn8h3SzVlJGLQz6Zchs\\nf7CqXQc8W9dMqCR4SqVZJi6D5C+nCgYz5ZPz5jkQT5Vvq5K0dLVJCNyUFCEm8HhH\\nKqPgKJIGPOLTL1UKq+1QQ1j3Gr0pZPYQVQ9XVSl6LJ2dJZ7ZxN8nGZ5WEE9C5/Px\\nLZ9EXAECgYEA7nDQaHxYnM0L8PnGm4r0EjFQpfcM3Q8H0gzvKL0ZvUKCMpZ3GdZP\\nzMlE4qZGKKDXqYVDRlQjR2xFhqNjK6fZhHc5dYOLqcBFXLkJ5V0BgKYvLQe8PjVu\\nRvM4b9MJ9uXJKBCLLeCvYcsLVHxGhTvVNvV7MQHX3nqZPqf3h7ECgYEA6ZGlVRlG\\nQQ5R5b5VU3f3G9u5y6MWh2zMPWPKtSP3C4xvO5OBlqxzI5c3wZGkHHsGBGPMHqFe\\njvC+pLPAiS/BYeNMX+gEmJmjmVXlC8pKL6dkqQGbJNvDNmJVhqCzKuVKXm0VzMWv\\nyVN0cHPLtmZwJlKzEOAr+rR6Aj9CfKCEXQECgYBSJBLqLm3J8o/K8K6bh2lGCxHd\\nx2T1E7s7BvJAJ2K7Z3+2FlB0r7tBqvDkzLDj7a2B9AE0nDY8U6pXNHphfv5g5ufU\\nohzSzVzcwYk4Fw3VBYCHYYZqM4r4EZL5LfwQqQc8IYxVDMLh6qVwH5K3SqQw1Mct\\nnVYW5fQp/1i5EQKBgQCNzF8lIQD3QsQsAhjQBGpuHxQJ/K4jvbZQV0u3gIxTD1/r\\njGI0RU1bwODjGj6bnmVfVXDzHYNOwpYSvQXtNZhBLB8vF5E4wPYKqFcpuTq5MJVJ\\nzAWWyJCRDc+CjXKgJpRZmZSLqP7r5CtB0W8hQ9S3EXExQZmRJZmN1QEBAoGAWLk0\\n5Z6QS8xXLhX1dXgj/LAG4gPE9R2VvVlbfQIGwKjX9F1n9kKHBVKVTQqKN3c0CQAK\\nvTYSVGJOUwX0HsU7sXDXdcL9F5cQqVZEu6rN4TgzLVT1OhKvFP2DPjnRTM3W0fFU\\nW5v8z4HN8hCDZIL6DqCkHpGIX/cz9x8mWcI=\\n-----END RSA PRIVATE KEY-----"}'})
    def test_get_google_vertex_access_token_with_access_token(self):
        """Test that get_google_vertex_access_token still works with normal access_token"""

        # Mock the HTTP response to return access_token (normal case)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "test_access_token_value",
            "token_type": "Bearer",
            "expires_in": 3600,
        }

        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client_class.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_response

            # This should work as before
            token = get_google_vertex_access_token()

            # Verify the token is the access_token value
            assert token == "test_access_token_value"

    def test_get_google_vertex_access_token_missing_credentials(self):
        """Test that proper error is raised when credentials are missing"""

        with patch.dict("os.environ", {}, clear=True):
            # Ensure GOOGLE_VERTEX_CREDENTIALS_JSON is not set
            if "GOOGLE_VERTEX_CREDENTIALS_JSON" in dict(os.environ):
                del os.environ["GOOGLE_VERTEX_CREDENTIALS_JSON"]

            with pytest.raises(ValueError) as exc_info:
                get_google_vertex_access_token()

            assert "GOOGLE_VERTEX_CREDENTIALS_JSON" in str(exc_info.value)


import os

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
