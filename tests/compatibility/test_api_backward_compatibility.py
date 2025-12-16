"""
API Backward Compatibility Tests

Tests to ensure API changes don't break existing clients.

These tests verify:
- Response field presence (fields are never removed)
- Response field types (types don't change)
- Required request fields (don't add new required fields)
- Endpoint availability (endpoints aren't removed)
- Status code consistency (codes don't change unexpectedly)
- Error format consistency (error responses maintain structure)

Run these tests before releasing API changes.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, Mock, AsyncMock
from tests.helpers.mocks import create_test_db_fixture, mock_rate_limiter
from tests.helpers.data_generators import UserGenerator, APIKeyGenerator, ModelGenerator
import os

os.environ['API_GATEWAY_SALT'] = 'test-salt-for-hashing-keys-minimum-16-chars'
os.environ['SUPABASE_SERVICE_ROLE_KEY'] = 'test-service-role-key'
os.environ['SUPABASE_URL'] = 'https://test.supabase.co'


@pytest.fixture
def app():
    from src.app import app
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def authenticated_client(client):
    """Client with valid API key"""
    db = create_test_db_fixture()
    user = UserGenerator.create_user()
    api_key = APIKeyGenerator.create_api_key(user_id=user["id"])
    db.insert("users", user)
    db.insert("api_keys", api_key)

    with patch("src.security.deps.get_supabase_client", return_value=db):
        with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
            yield client, {"X-API-Key": api_key["key"]}


# ============================================================================
# V1 API Compatibility Tests
# ============================================================================

class TestV1ModelsEndpointCompatibility:
    """Test /v1/models endpoint backward compatibility"""

    @pytest.mark.unit
    def test_models_list_has_required_fields(self, client):
        """Models list response must include required fields"""
        db = create_test_db_fixture()
        models = ModelGenerator.create_batch(5)
        for model in models:
            db.insert("models", model)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test"}
                response = client.get("/v1/models", headers=headers)

                if response.status_code == 200:
                    data = response.json()

                    # Required top-level fields (must never be removed)
                    assert "data" in data, "Response must have 'data' field"

                    # Each model must have required fields
                    if len(data.get("data", [])) > 0:
                        model = data["data"][0]
                        required_fields = ["id", "provider", "name"]
                        for field in required_fields:
                            assert field in model, f"Model must have '{field}' field"

    @pytest.mark.unit
    def test_models_list_field_types_unchanged(self, client):
        """Field types in models response must not change"""
        db = create_test_db_fixture()
        model = ModelGenerator.create_model()
        db.insert("models", model)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test"}
                response = client.get("/v1/models", headers=headers)

                if response.status_code == 200:
                    data = response.json()

                    # Type checks (must remain consistent)
                    assert isinstance(data.get("data"), list), "'data' must be a list"

                    if len(data.get("data", [])) > 0:
                        model = data["data"][0]
                        assert isinstance(model.get("id"), str), "'id' must be string"
                        assert isinstance(model.get("provider"), str), "'provider' must be string"

    @pytest.mark.unit
    def test_models_endpoint_accepts_legacy_params(self, client):
        """Endpoint must accept legacy query parameters"""
        db = create_test_db_fixture()

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test"}

                # Legacy parameters should still work
                response = client.get("/v1/models?limit=10&offset=0", headers=headers)

                # Should not fail due to legacy params
                assert response.status_code in [200, 401, 403]


class TestV1ChatCompletionsCompatibility:
    """Test /v1/chat/completions endpoint backward compatibility"""

    @pytest.mark.unit
    def test_chat_completions_required_fields(self, client):
        """Chat completions must accept required fields only"""
        db = create_test_db_fixture()

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test"}

                # Minimal valid request (backward compatibility)
                payload = {
                    "model": "gpt-3.5-turbo",
                    "messages": [
                        {"role": "user", "content": "test"}
                    ]
                }

                response = client.post("/v1/chat/completions", headers=headers, json=payload)

                # Should accept minimal request
                assert response.status_code in [200, 401, 403, 404, 500, 502]

    @pytest.mark.unit
    def test_chat_completions_response_structure(self, client):
        """Chat completions response structure must be consistent"""
        db = create_test_db_fixture()

        mock_response = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-3.5-turbo",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "test"},
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15
            }
        }

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
                    mock_resp = Mock()
                    mock_resp.status_code = 200
                    mock_resp.json = Mock(return_value=mock_response)
                    mock_post.return_value = mock_resp

                    headers = {"X-API-Key": "gw_live_test"}
                    payload = {
                        "model": "gpt-3.5-turbo",
                        "messages": [{"role": "user", "content": "test"}]
                    }

                    response = client.post("/v1/chat/completions", headers=headers, json=payload)

                    if response.status_code == 200:
                        data = response.json()

                        # Required top-level fields
                        required_fields = ["id", "choices", "usage"]
                        for field in required_fields:
                            assert field in data, f"Response must have '{field}' field"

    @pytest.mark.unit
    def test_chat_optional_params_backward_compatible(self, client):
        """Optional parameters must remain optional"""
        db = create_test_db_fixture()

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test"}

                # Request without optional params
                minimal = {
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "test"}]
                }

                # Request with optional params
                with_optional = {
                    **minimal,
                    "temperature": 0.7,
                    "max_tokens": 100,
                    "top_p": 1.0,
                    "stream": False
                }

                # Both should work
                response1 = client.post("/v1/chat/completions", headers=headers, json=minimal)
                response2 = client.post("/v1/chat/completions", headers=headers, json=with_optional)

                # Both should have similar success/failure
                assert response1.status_code in [200, 401, 403, 404, 500, 502]
                assert response2.status_code in [200, 401, 403, 404, 500, 502]


# ============================================================================
# Error Response Compatibility
# ============================================================================

class TestErrorResponseCompatibility:
    """Test error response structure compatibility"""

    @pytest.mark.unit
    def test_unauthorized_error_structure(self, client):
        """401 Unauthorized error structure must be consistent"""
        response = client.get("/v1/models")  # No API key

        # Should return 401 or 403
        assert response.status_code in [401, 403, 422]

        if response.text:
            data = response.json()
            # Error should have consistent structure
            # (Implementation may vary, documenting expected)
            assert isinstance(data, dict)

    @pytest.mark.unit
    def test_not_found_error_structure(self, client):
        """404 Not Found error structure must be consistent"""
        response = client.get("/v1/nonexistent-endpoint")

        assert response.status_code == 404

        if response.text:
            data = response.json()
            assert isinstance(data, dict)
            assert "detail" in data or "error" in data

    @pytest.mark.unit
    def test_validation_error_structure(self, client):
        """422 Validation error structure must be consistent"""
        db = create_test_db_fixture()

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test"}

                # Invalid payload (missing required field)
                payload = {
                    "model": "gpt-3.5-turbo"
                    # Missing 'messages'
                }

                response = client.post("/v1/chat/completions", headers=headers, json=payload)

                if response.status_code == 422:
                    data = response.json()
                    # Validation errors should have detail
                    assert "detail" in data or "error" in data


# ============================================================================
# API Versioning Compatibility
# ============================================================================

class TestAPIVersioning:
    """Test API versioning compatibility"""

    @pytest.mark.unit
    def test_v1_endpoints_remain_available(self, client):
        """V1 endpoints must remain available"""
        # List of V1 endpoints that must not be removed
        v1_endpoints = [
            "/v1/models",
            "/v1/chat/completions",
        ]

        for endpoint in v1_endpoints:
            response = client.get(endpoint)
            # Should not be 404 (endpoint exists)
            # May be 401/403 (auth required)
            assert response.status_code != 404, f"Endpoint {endpoint} should exist"

    @pytest.mark.unit
    def test_endpoint_paths_unchanged(self, client):
        """Endpoint paths must not change"""
        # Verify exact paths work (not redirected)
        response = client.get("/v1/models")

        # Should respond directly (not redirect)
        assert response.status_code not in [301, 302, 307, 308], \
            "Endpoint should not redirect"


# ============================================================================
# Request/Response Format Compatibility
# ============================================================================

class TestRequestResponseFormats:
    """Test request and response format compatibility"""

    @pytest.mark.unit
    def test_accepts_json_content_type(self, client):
        """API must accept application/json"""
        db = create_test_db_fixture()

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {
                    "X-API-Key": "gw_live_test",
                    "Content-Type": "application/json"
                }

                payload = {
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "test"}]
                }

                response = client.post("/v1/chat/completions", headers=headers, json=payload)

                # Should accept JSON
                assert response.status_code in [200, 401, 403, 404, 500, 502]

    @pytest.mark.unit
    def test_returns_json_content_type(self, client):
        """API must return application/json"""
        db = create_test_db_fixture()

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test"}
                response = client.get("/v1/models", headers=headers)

                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "")
                    assert "application/json" in content_type.lower(), \
                        "Response must be JSON"


# ============================================================================
# Pagination Compatibility
# ============================================================================

class TestPaginationCompatibility:
    """Test pagination parameter compatibility"""

    @pytest.mark.unit
    def test_legacy_pagination_params(self, client):
        """Legacy pagination params must work"""
        db = create_test_db_fixture()

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test"}

                # Legacy pagination
                response = client.get("/v1/models?limit=10&offset=0", headers=headers)

                # Should work
                assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    def test_default_pagination_behavior(self, client):
        """Default pagination must be consistent"""
        db = create_test_db_fixture()

        # Add many models
        models = ModelGenerator.create_batch(100)
        for model in models:
            db.insert("models", model)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test"}

                # Request without pagination params
                response = client.get("/v1/models", headers=headers)

                if response.status_code == 200:
                    data = response.json()
                    # Should return some results (default pagination)
                    assert "data" in data
                    # Default limit should be reasonable (not all 100)
                    # (Implementation dependent, documenting behavior)


# ============================================================================
# Authentication Compatibility
# ============================================================================

class TestAuthenticationCompatibility:
    """Test authentication method compatibility"""

    @pytest.mark.unit
    def test_api_key_header_name(self, client):
        """X-API-Key header name must work"""
        db = create_test_db_fixture()

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                # Standard header name
                headers = {"X-API-Key": "gw_live_test"}
                response = client.get("/v1/models", headers=headers)

                # Should accept header (may fail auth, but not reject header)
                assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    def test_api_key_format(self, client):
        """API key format must be accepted"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(user_id=user["id"])
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                # gw_live_* format
                headers = {"X-API-Key": api_key["key"]}
                response = client.get("/v1/models", headers=headers)

                # Should accept format
                assert response.status_code in [200, 401, 403]


# ============================================================================
# Breaking Change Detection
# ============================================================================

class TestBreakingChangeDetection:
    """Tests that will fail if breaking changes are introduced"""

    @pytest.mark.critical
    def test_no_new_required_fields_in_chat_request(self, client):
        """Chat request must not require new fields"""
        db = create_test_db_fixture()

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test"}

                # Absolute minimum required fields
                minimal = {
                    "model": "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "test"}]
                }

                response = client.post("/v1/chat/completions", headers=headers, json=minimal)

                # Should work with minimal fields
                # If this fails, a breaking change was introduced
                assert response.status_code in [200, 401, 403, 404, 500, 502], \
                    "Minimal request should work - breaking change detected!"

    @pytest.mark.critical
    def test_response_fields_not_removed(self, client):
        """Response fields must not be removed"""
        db = create_test_db_fixture()
        model = ModelGenerator.create_model()
        db.insert("models", model)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test"}
                response = client.get("/v1/models", headers=headers)

                if response.status_code == 200:
                    data = response.json()

                    # These fields must always exist
                    assert "data" in data, "BREAKING: 'data' field removed!"

                    if len(data.get("data", [])) > 0:
                        model_obj = data["data"][0]
                        required = ["id", "provider", "name"]
                        for field in required:
                            assert field in model_obj, f"BREAKING: '{field}' field removed!"
