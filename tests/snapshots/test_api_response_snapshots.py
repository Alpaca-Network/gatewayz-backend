"""
API Response Snapshot Tests

Captures and verifies the structure of API responses to detect unintended changes.

When API response structures change:
1. Review the diff to ensure it's intentional
2. Update snapshots if the change is correct: pytest --snapshot-update
3. Commit the updated snapshots to version control

Run tests:
    pytest tests/snapshots/ -v

Update snapshots after intentional changes:
    pytest tests/snapshots/ --snapshot-update
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, AsyncMock
from tests.helpers.mocks import create_test_db_fixture, mock_rate_limiter
from tests.helpers.data_generators import UserGenerator, ModelGenerator, APIKeyGenerator
import os

# Set test environment
os.environ['API_GATEWAY_SALT'] = 'test-salt-for-hashing-keys-minimum-16-chars'
os.environ['SUPABASE_SERVICE_ROLE_KEY'] = 'test-service-role-key'
os.environ['SUPABASE_URL'] = 'https://test.supabase.co'
os.environ['ADMIN_KEY'] = 'test-admin-key-12345'


@pytest.fixture
def app():
    """Create FastAPI app instance"""
    from src.app import app
    return app


@pytest.fixture
def client(app):
    """Create test client"""
    return TestClient(app)


@pytest.fixture
def mock_db_with_models():
    """Create mock database with sample models"""
    db = create_test_db_fixture()

    # Add sample models
    models = [
        {
            "id": "openai/gpt-4",
            "provider": "openai",
            "name": "gpt-4",
            "context_length": 8192,
            "max_output_tokens": 4096,
            "pricing": {
                "prompt": 0.00003,
                "completion": 0.00006
            },
            "capabilities": {
                "streaming": True,
                "function_calling": True
            }
        },
        {
            "id": "anthropic/claude-3-sonnet",
            "provider": "anthropic",
            "name": "claude-3-sonnet",
            "context_length": 200000,
            "max_output_tokens": 4096,
            "pricing": {
                "prompt": 0.000003,
                "completion": 0.000015
            },
            "capabilities": {
                "streaming": True,
                "function_calling": False
            }
        }
    ]

    for model in models:
        db.insert("models", model)

    return db


# ============================================================================
# Models Endpoint Snapshots
# ============================================================================

class TestModelsEndpointSnapshots:
    """Snapshot tests for /v1/models endpoint"""

    @pytest.mark.unit
    def test_models_list_response_structure(self, client, mock_db_with_models, snapshot):
        """Snapshot: Models list response structure"""
        with patch("src.security.deps.get_supabase_client", return_value=mock_db_with_models):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test123"}
                response = client.get("/v1/models", headers=headers)

                # Capture response structure (status code + keys)
                snapshot_data = {
                    "status_code": response.status_code,
                    "has_data_key": "data" in response.json() if response.status_code == 200 else False,
                    "response_keys": list(response.json().keys()) if response.status_code == 200 else []
                }

                assert snapshot_data == snapshot

    @pytest.mark.unit
    def test_single_model_response_structure(self, client, mock_db_with_models, snapshot):
        """Snapshot: Single model response structure"""
        with patch("src.security.deps.get_supabase_client", return_value=mock_db_with_models):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test123"}
                response = client.get("/v1/models/gpt-4", headers=headers)

                # Capture structure if endpoint exists
                if response.status_code == 200:
                    model_data = response.json()
                    snapshot_data = {
                        "status_code": response.status_code,
                        "has_required_fields": all(
                            key in model_data
                            for key in ["id", "provider", "name"]
                        ),
                        "field_types": {
                            k: type(v).__name__
                            for k, v in model_data.items()
                        }
                    }
                else:
                    snapshot_data = {"status_code": response.status_code}

                assert snapshot_data == snapshot


# ============================================================================
# Chat Completions Endpoint Snapshots
# ============================================================================

class TestChatCompletionsSnapshots:
    """Snapshot tests for /v1/chat/completions endpoint"""

    @pytest.mark.unit
    def test_chat_completion_success_structure(self, client, mock_db_with_models, snapshot):
        """Snapshot: Successful chat completion response structure"""
        # Mock provider response
        mock_provider_response = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-3.5-turbo",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello! How can I help you today?"
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 9,
                "total_tokens": 19
            }
        }

        with patch("src.security.deps.get_supabase_client", return_value=mock_db_with_models):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
                    mock_response = Mock()
                    mock_response.status_code = 200
                    mock_response.json = Mock(return_value=mock_provider_response)
                    mock_post.return_value = mock_response

                    headers = {"X-API-Key": "gw_live_test123"}
                    payload = {
                        "model": "gpt-3.5-turbo",
                        "messages": [
                            {"role": "user", "content": "Hello"}
                        ]
                    }

                    response = client.post("/v1/chat/completions", headers=headers, json=payload)

                    if response.status_code == 200:
                        data = response.json()
                        snapshot_data = {
                            "status_code": response.status_code,
                            "has_id": "id" in data,
                            "has_choices": "choices" in data,
                            "has_usage": "usage" in data,
                            "response_structure": {
                                key: type(value).__name__
                                for key, value in data.items()
                            }
                        }
                    else:
                        snapshot_data = {
                            "status_code": response.status_code,
                            "error_structure": list(response.json().keys()) if response.text else []
                        }

                    assert snapshot_data == snapshot

    @pytest.mark.unit
    def test_chat_completion_error_structure(self, client, mock_db_with_models, snapshot):
        """Snapshot: Chat completion error response structure"""
        with patch("src.security.deps.get_supabase_client", return_value=mock_db_with_models):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test123"}

                # Invalid request (missing required fields)
                payload = {
                    "model": "gpt-3.5-turbo"
                    # Missing messages field
                }

                response = client.post("/v1/chat/completions", headers=headers, json=payload)

                snapshot_data = {
                    "status_code": response.status_code,
                    "has_error": "error" in response.json() if response.text else False,
                    "response_keys": list(response.json().keys()) if response.text else []
                }

                assert snapshot_data == snapshot


# ============================================================================
# Error Response Snapshots
# ============================================================================

class TestErrorResponseSnapshots:
    """Snapshot tests for error responses"""

    @pytest.mark.unit
    def test_unauthorized_error_structure(self, client, snapshot):
        """Snapshot: Unauthorized error response (missing API key)"""
        response = client.get("/v1/models")  # No API key

        snapshot_data = {
            "status_code": response.status_code,
            "has_detail": "detail" in response.json() if response.text else False,
            "response_structure": list(response.json().keys()) if response.text else []
        }

        assert snapshot_data == snapshot

    @pytest.mark.unit
    def test_invalid_api_key_structure(self, client, snapshot):
        """Snapshot: Invalid API key error structure"""
        with patch("src.security.deps.get_supabase_client", return_value=create_test_db_fixture()):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "invalid_key"}
                response = client.get("/v1/models", headers=headers)

                snapshot_data = {
                    "status_code": response.status_code,
                    "response_keys": list(response.json().keys()) if response.text else []
                }

                assert snapshot_data == snapshot

    @pytest.mark.unit
    def test_rate_limit_error_structure(self, client, snapshot):
        """Snapshot: Rate limit exceeded error structure"""
        # Mock rate limiter that denies request
        rate_limited = mock_rate_limiter(allowed=False)

        with patch("src.security.deps.get_supabase_client", return_value=create_test_db_fixture()):
            with patch("src.security.deps.rate_limiter_manager", rate_limited):
                headers = {"X-API-Key": "gw_live_test123"}
                response = client.get("/v1/models", headers=headers)

                snapshot_data = {
                    "status_code": response.status_code,
                    "has_retry_after": "Retry-After" in response.headers,
                    "has_error_message": bool(response.text),
                    "response_keys": list(response.json().keys()) if response.text else []
                }

                assert snapshot_data == snapshot

    @pytest.mark.unit
    def test_not_found_error_structure(self, client, snapshot):
        """Snapshot: 404 Not Found error structure"""
        response = client.get("/v1/nonexistent-endpoint")

        snapshot_data = {
            "status_code": response.status_code,
            "response_keys": list(response.json().keys()) if response.text else []
        }

        assert snapshot_data == snapshot


# ============================================================================
# Admin Endpoint Snapshots
# ============================================================================

class TestAdminEndpointSnapshots:
    """Snapshot tests for admin endpoints"""

    @pytest.mark.unit
    def test_admin_stats_response_structure(self, client, snapshot):
        """Snapshot: Admin statistics response structure"""
        with patch("src.security.deps.get_supabase_client", return_value=create_test_db_fixture()):
            headers = {"X-Admin-Key": os.getenv("ADMIN_KEY", "test-admin-key")}

            response = client.get("/admin/stats", headers=headers)

            if response.status_code == 200:
                snapshot_data = {
                    "status_code": response.status_code,
                    "response_keys": sorted(list(response.json().keys())),
                    "has_metrics": any(
                        key in response.json()
                        for key in ["total_users", "total_requests", "active_keys"]
                    )
                }
            else:
                snapshot_data = {"status_code": response.status_code}

            assert snapshot_data == snapshot


# ============================================================================
# Streaming Response Snapshots
# ============================================================================

class TestStreamingResponseSnapshots:
    """Snapshot tests for streaming responses"""

    @pytest.mark.unit
    def test_streaming_chat_completion_structure(self, client, snapshot):
        """Snapshot: Streaming chat completion event structure"""
        with patch("src.security.deps.get_supabase_client", return_value=create_test_db_fixture()):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test123"}
                payload = {
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "test"}],
                    "stream": True
                }

                response = client.post("/v1/chat/completions", headers=headers, json=payload)

                # Capture streaming response metadata
                snapshot_data = {
                    "status_code": response.status_code,
                    "is_streaming": response.headers.get("content-type", "").startswith("text/event-stream"),
                    "has_transfer_encoding": "transfer-encoding" in response.headers
                }

                assert snapshot_data == snapshot


# ============================================================================
# Pagination Response Snapshots
# ============================================================================

class TestPaginationSnapshots:
    """Snapshot tests for paginated responses"""

    @pytest.mark.unit
    def test_paginated_response_structure(self, client, mock_db_with_models, snapshot):
        """Snapshot: Paginated list response structure"""
        with patch("src.security.deps.get_supabase_client", return_value=mock_db_with_models):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test123"}

                # Request with pagination parameters
                response = client.get("/v1/models?limit=10&offset=0", headers=headers)

                if response.status_code == 200:
                    data = response.json()
                    snapshot_data = {
                        "status_code": response.status_code,
                        "has_data": "data" in data,
                        "has_pagination": any(
                            key in data
                            for key in ["limit", "offset", "total", "has_more"]
                        ),
                        "response_keys": sorted(list(data.keys()))
                    }
                else:
                    snapshot_data = {"status_code": response.status_code}

                assert snapshot_data == snapshot


# ============================================================================
# Metadata and Headers Snapshots
# ============================================================================

class TestResponseMetadataSnapshots:
    """Snapshot tests for response metadata and headers"""

    @pytest.mark.unit
    def test_response_headers_structure(self, client, mock_db_with_models, snapshot):
        """Snapshot: Important response headers"""
        with patch("src.security.deps.get_supabase_client", return_value=mock_db_with_models):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test123"}
                response = client.get("/v1/models", headers=headers)

                # Capture important headers
                important_headers = [
                    "content-type",
                    "x-request-id",
                    "x-ratelimit-limit",
                    "x-ratelimit-remaining",
                    "x-ratelimit-reset"
                ]

                snapshot_data = {
                    "status_code": response.status_code,
                    "present_headers": [
                        h for h in important_headers
                        if h in response.headers
                    ],
                    "content_type": response.headers.get("content-type", "")
                }

                assert snapshot_data == snapshot


# ============================================================================
# API Versioning Snapshots
# ============================================================================

class TestAPIVersioningSnapshots:
    """Snapshot tests for API versioning"""

    @pytest.mark.unit
    def test_api_version_in_response(self, client, snapshot):
        """Snapshot: API version information in responses"""
        with patch("src.security.deps.get_supabase_client", return_value=create_test_db_fixture()):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test123"}
                response = client.get("/v1/models", headers=headers)

                snapshot_data = {
                    "api_version_in_path": "/v1/" in client.app.url_path_for("get_models", **{}) if hasattr(client.app, 'url_path_for') else True,
                    "has_version_header": "x-api-version" in response.headers,
                    "version_header": response.headers.get("x-api-version", "")
                }

                assert snapshot_data == snapshot
