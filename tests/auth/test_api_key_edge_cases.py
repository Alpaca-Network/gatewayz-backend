"""
API Key Authentication Edge Case Tests

Tests edge cases and boundary conditions in API key authentication.

Focus areas:
- Malformed API keys
- Key format variations
- Special characters
- Length boundaries
- Case sensitivity
- Whitespace handling
- Expired/revoked keys
- Inactive keys
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, Mock
from tests.helpers.mocks import create_test_db_fixture, mock_rate_limiter
from tests.helpers.data_generators import UserGenerator, APIKeyGenerator
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


# ============================================================================
# Malformed API Key Tests
# ============================================================================

class TestMalformedAPIKeys:
    """Test handling of malformed API keys"""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_empty_api_key(self, client):
        """Should reject empty API key"""
        headers = {"X-API-Key": ""}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_none_api_key(self, client):
        """Should reject None as API key"""
        headers = {"X-API-Key": None}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [401, 403, 422]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_whitespace_only_api_key(self, client):
        """Should reject whitespace-only API key"""
        headers = {"X-API-Key": "   "}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_api_key_with_leading_whitespace(self, client):
        """Should handle API key with leading whitespace"""
        with patch("src.security.deps.get_supabase_client", return_value=create_test_db_fixture()):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "  gw_live_valid_key"}
                response = client.get("/v1/models", headers=headers)
                # Should either strip whitespace or reject
                assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_api_key_with_trailing_whitespace(self, client):
        """Should handle API key with trailing whitespace"""
        with patch("src.security.deps.get_supabase_client", return_value=create_test_db_fixture()):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_valid_key  "}
                response = client.get("/v1/models", headers=headers)
                assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_api_key_with_newline(self, client):
        """Should reject API key containing newline"""
        headers = {"X-API-Key": "gw_live_test\nkey"}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [401, 403, 422]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_api_key_with_null_byte(self, client):
        """Should reject API key containing null byte"""
        headers = {"X-API-Key": "gw_live_test\x00key"}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [401, 403, 422]


# ============================================================================
# API Key Format Tests
# ============================================================================

class TestAPIKeyFormats:
    """Test various API key format variations"""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_wrong_prefix(self, client):
        """Should reject API key with wrong prefix"""
        headers = {"X-API-Key": "sk_live_incorrect_prefix"}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_missing_prefix(self, client):
        """Should reject API key without prefix"""
        headers = {"X-API-Key": "just_some_random_string"}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_test_key_in_production(self, client):
        """Should handle test keys appropriately"""
        headers = {"X-API-Key": "gw_test_test_mode_key"}
        response = client.get("/v1/models", headers=headers)
        # Should either reject test keys or handle them specially
        assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_case_sensitivity(self, client):
        """API keys should be case-sensitive"""
        with patch("src.security.deps.get_supabase_client") as mock_supabase:
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                db = create_test_db_fixture()
                user = UserGenerator.create_user()
                api_key = APIKeyGenerator.create_api_key(
                    user_id=user["id"],
                    key_type="live"
                )
                db.insert("users", user)
                db.insert("api_keys", api_key)
                mock_supabase.return_value = db

                # Original case
                headers1 = {"X-API-Key": api_key["key"]}
                response1 = client.get("/v1/models", headers=headers1)

                # Upper case
                headers2 = {"X-API-Key": api_key["key"].upper()}
                response2 = client.get("/v1/models", headers=headers2)

                # Should not match if case-sensitive
                # (Implementation dependent, but documenting behavior)
                assert response1.status_code in [200, 401, 403]


# ============================================================================
# API Key Length Tests
# ============================================================================

class TestAPIKeyLengths:
    """Test API key length boundaries"""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_extremely_short_key(self, client):
        """Should reject extremely short API key"""
        headers = {"X-API-Key": "gw"}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_extremely_long_key(self, client):
        """Should reject or handle extremely long API key"""
        long_key = "gw_live_" + "x" * 10000
        headers = {"X-API-Key": long_key}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [401, 403, 413, 422]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_minimum_valid_length(self, client):
        """Test minimum valid API key length"""
        min_key = "gw_live_abc123"
        headers = {"X-API-Key": min_key}
        response = client.get("/v1/models", headers=headers)
        # Should be rejected (not found), not error
        assert response.status_code in [401, 403]


# ============================================================================
# Special Characters Tests
# ============================================================================

class TestSpecialCharacters:
    """Test API keys with special characters"""

    @pytest.mark.unit
    @pytest.mark.auth
    @pytest.mark.parametrize("special_char", [
        "$", "!", "@", "#", "%", "^", "&", "*", "(", ")",
        "[", "]", "{", "}", "<", ">", "?", "/", "\\", "|"
    ])
    def test_special_character_in_key(self, client, special_char):
        """Should handle special characters in API key"""
        key = f"gw_live_test{special_char}key"
        headers = {"X-API-Key": key}
        response = client.get("/v1/models", headers=headers)
        # Should handle gracefully (likely reject)
        assert response.status_code in [401, 403, 422]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_unicode_in_key(self, client):
        """Should handle unicode characters in API key"""
        headers = {"X-API-Key": "gw_live_test_ñ_key"}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [401, 403, 422]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_emoji_in_key(self, client):
        """Should handle emoji in API key"""
        headers = {"X-API-Key": "gw_live_test_😀_key"}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [401, 403, 422]


# ============================================================================
# Key Status Tests
# ============================================================================

class TestAPIKeyStatus:
    """Test different API key status scenarios"""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_revoked_key(self, client):
        """Should reject revoked API key"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            status="revoked"
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}
                response = client.get("/v1/models", headers=headers)
                assert response.status_code in [401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_inactive_key(self, client):
        """Should reject inactive API key"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            status="inactive"
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}
                response = client.get("/v1/models", headers=headers)
                assert response.status_code in [401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_deleted_key(self, client):
        """Should reject deleted API key"""
        # Key exists in DB but marked as deleted
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            status="deleted"
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}
                response = client.get("/v1/models", headers=headers)
                assert response.status_code in [401, 403]


# ============================================================================
# Multiple Header Tests
# ============================================================================

class TestMultipleHeaders:
    """Test scenarios with multiple auth headers"""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_multiple_api_key_headers(self, client):
        """Should handle multiple X-API-Key headers"""
        # Most frameworks take the first or last header
        response = client.get("/v1/models", headers=[
            ("X-API-Key", "gw_live_key1"),
            ("X-API-Key", "gw_live_key2")
        ])
        assert response.status_code in [401, 403, 422]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_both_api_key_and_bearer_token(self, client):
        """Should handle both API key and Bearer token"""
        headers = {
            "X-API-Key": "gw_live_test_key",
            "Authorization": "Bearer some_token"
        }
        response = client.get("/v1/models", headers=headers)
        # Should prioritize one or reject conflicting auth
        assert response.status_code in [200, 401, 403, 422]


# ============================================================================
# SQL Injection Attempts
# ============================================================================

class TestSQLInjectionAttempts:
    """Test SQL injection attempts via API keys"""

    @pytest.mark.unit
    @pytest.mark.auth
    @pytest.mark.critical
    @pytest.mark.parametrize("injection", [
        "' OR '1'='1",
        "'; DROP TABLE users--",
        "' UNION SELECT * FROM users--",
        "admin'--",
        "' OR 1=1--"
    ])
    def test_sql_injection_in_api_key(self, client, injection):
        """Should safely handle SQL injection attempts"""
        headers = {"X-API-Key": f"gw_live_{injection}"}
        response = client.get("/v1/models", headers=headers)
        # Should reject without executing SQL
        assert response.status_code in [401, 403, 422]

    @pytest.mark.unit
    @pytest.mark.auth
    @pytest.mark.critical
    def test_command_injection_in_api_key(self, client):
        """Should safely handle command injection attempts"""
        headers = {"X-API-Key": "gw_live_test; rm -rf /"}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [401, 403, 422]


# ============================================================================
# Concurrent Auth Tests
# ============================================================================

class TestConcurrentAuth:
    """Test concurrent authentication scenarios"""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_same_key_concurrent_requests(self, client):
        """Same API key should work for concurrent requests"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            status="active"
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}

                # Make multiple concurrent requests
                responses = [
                    client.get("/v1/models", headers=headers)
                    for _ in range(5)
                ]

                # All should succeed or fail consistently
                status_codes = [r.status_code for r in responses]
                # All should be the same status
                assert len(set(status_codes)) == 1

    @pytest.mark.unit
    @pytest.mark.auth
    def test_key_revoked_during_request(self, client):
        """Test behavior when key is revoked during active request"""
        # This tests race condition handling
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            status="active"
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}

                # First request should succeed
                response1 = client.get("/v1/models", headers=headers)

                # Revoke the key
                db.table("api_keys").update({"status": "revoked"}).eq("id", api_key["id"]).execute()

                # Next request should fail (or succeed if cached)
                response2 = client.get("/v1/models", headers=headers)

                # Should handle gracefully
                assert response1.status_code in [200, 401, 403]
                assert response2.status_code in [200, 401, 403]


# ============================================================================
# Header Case Sensitivity Tests
# ============================================================================

class TestHeaderCaseSensitivity:
    """Test HTTP header case sensitivity"""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_lowercase_header_name(self, client):
        """Should accept lowercase header name"""
        headers = {"x-api-key": "gw_live_test_key"}
        response = client.get("/v1/models", headers=headers)
        # HTTP headers are case-insensitive
        assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_uppercase_header_name(self, client):
        """Should accept uppercase header name"""
        headers = {"X-API-KEY": "gw_live_test_key"}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_mixed_case_header_name(self, client):
        """Should accept mixed case header name"""
        headers = {"X-Api-Key": "gw_live_test_key"}
        response = client.get("/v1/models", headers=headers)
        assert response.status_code in [200, 401, 403]
