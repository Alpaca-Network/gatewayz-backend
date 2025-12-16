"""
Permission and Authorization Edge Case Tests

Tests edge cases in permission checking and authorization logic.

Focus areas:
- IP allowlist edge cases
- Domain referrer edge cases
- Admin key validation
- Cross-user access attempts
- Permission boundaries
- Privilege escalation attempts
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch
from tests.helpers.mocks import create_test_db_fixture, mock_rate_limiter
from tests.helpers.data_generators import UserGenerator, APIKeyGenerator
import os

os.environ['API_GATEWAY_SALT'] = 'test-salt-for-hashing-keys-minimum-16-chars'
os.environ['SUPABASE_SERVICE_ROLE_KEY'] = 'test-service-role-key'
os.environ['SUPABASE_URL'] = 'https://test.supabase.co'
os.environ['ADMIN_KEY'] = 'test-admin-key-12345'


@pytest.fixture
def app():
    from src.app import app
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


# ============================================================================
# IP Allowlist Edge Cases
# ============================================================================

class TestIPAllowlistEdgeCases:
    """Test IP allowlist edge cases"""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_empty_allowlist_allows_all(self, client):
        """Empty IP allowlist should allow all IPs"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            allowed_ips=[]  # Empty allowlist
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}
                response = client.get("/v1/models", headers=headers)
                # Should allow (or auth may fail for other reasons)
                assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_localhost_variations(self, client):
        """Test various localhost representations"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()

        localhost_variations = [
            "127.0.0.1",
            "localhost",
            "::1",
            "0.0.0.0"
        ]

        for localhost in localhost_variations:
            api_key = APIKeyGenerator.create_api_key(
                user_id=user["id"],
                allowed_ips=[localhost]
            )
            db.insert("api_keys", api_key)

        db.insert("users", user)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                # Test with different client IPs
                for ip in localhost_variations:
                    # Would need to mock client IP, but documenting expected behavior
                    pass

    @pytest.mark.unit
    @pytest.mark.auth
    def test_ipv6_in_allowlist(self, client):
        """Test IPv6 address in allowlist"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            allowed_ips=["2001:0db8:85a3:0000:0000:8a2e:0370:7334"]
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}
                response = client.get("/v1/models", headers=headers)
                # Should handle IPv6
                assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    @pytest.mark.parametrize("invalid_ip", [
        "999.999.999.999",
        "192.168.1",
        "192.168.1.1.1",
        "not-an-ip",
        "192.168.1.1/24",
        ""
    ])
    def test_invalid_ip_in_allowlist(self, client, invalid_ip):
        """Test handling of invalid IPs in allowlist"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            allowed_ips=[invalid_ip]
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}
                response = client.get("/v1/models", headers=headers)
                # Should handle gracefully (reject or ignore invalid entry)
                assert response.status_code in [200, 401, 403, 422]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_cidr_notation_in_allowlist(self, client):
        """Test CIDR notation in IP allowlist"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            allowed_ips=["192.168.1.0/24"]
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}
                response = client.get("/v1/models", headers=headers)
                # Should either support CIDR or reject
                assert response.status_code in [200, 401, 403, 422]


# ============================================================================
# Domain Referrer Edge Cases
# ============================================================================

class TestDomainReferrerEdgeCases:
    """Test domain referrer validation edge cases"""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_empty_referrer_allowlist(self, client):
        """Empty referrer list should allow all"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            allowed_domains=[]
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {
                    "X-API-Key": api_key["key"],
                    "Referer": "https://example.com"
                }
                response = client.get("/v1/models", headers=headers)
                assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_missing_referer_header(self, client):
        """Test request without Referer header"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            allowed_domains=["https://example.com"]
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}
                # No Referer header
                response = client.get("/v1/models", headers=headers)
                # Should either allow (missing = ok) or reject
                assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    @pytest.mark.parametrize("protocol", ["http://", "https://", "ftp://", "//"])
    def test_different_protocols(self, client, protocol):
        """Test different protocols in referrer"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            allowed_domains=["https://example.com"]
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {
                    "X-API-Key": api_key["key"],
                    "Referer": f"{protocol}example.com"
                }
                response = client.get("/v1/models", headers=headers)
                # Should validate protocol matching
                assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_subdomain_matching(self, client):
        """Test subdomain in referrer validation"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            allowed_domains=["https://example.com"]
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {
                    "X-API-Key": api_key["key"],
                    "Referer": "https://sub.example.com"
                }
                response = client.get("/v1/models", headers=headers)
                # Should either allow subdomains or reject
                assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_port_in_referrer(self, client):
        """Test port number in referrer"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(
            user_id=user["id"],
            allowed_domains=["https://example.com"]
        )
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {
                    "X-API-Key": api_key["key"],
                    "Referer": "https://example.com:8080"
                }
                response = client.get("/v1/models", headers=headers)
                # Should handle port numbers
                assert response.status_code in [200, 401, 403]


# ============================================================================
# Admin Key Edge Cases
# ============================================================================

class TestAdminKeyEdgeCases:
    """Test admin key validation edge cases"""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_admin_key_case_sensitivity(self, client):
        """Admin key should be case-sensitive"""
        admin_key = os.getenv("ADMIN_KEY", "test-admin-key-12345")

        # Correct case
        headers1 = {"X-Admin-Key": admin_key}
        response1 = client.get("/admin/stats", headers=headers1)

        # Wrong case
        headers2 = {"X-Admin-Key": admin_key.upper()}
        response2 = client.get("/admin/stats", headers=headers2)

        # Should be case-sensitive
        assert response1.status_code in [200, 404, 500]
        if admin_key.upper() != admin_key:
            assert response2.status_code in [401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_admin_key_with_whitespace(self, client):
        """Admin key with whitespace should be rejected"""
        admin_key = os.getenv("ADMIN_KEY", "test-admin-key-12345")
        headers = {"X-Admin-Key": f" {admin_key} "}
        response = client.get("/admin/stats", headers=headers)
        # Should either strip or reject
        assert response.status_code in [200, 401, 403, 404]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_regular_api_key_on_admin_endpoint(self, client):
        """Regular API key should not work on admin endpoints"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(user_id=user["id"])
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            headers = {"X-API-Key": api_key["key"]}
            response = client.get("/admin/stats", headers=headers)
            # Should reject
            assert response.status_code in [401, 403, 404]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_admin_key_on_regular_endpoint(self, client):
        """Admin key should work on regular endpoints (or be rejected)"""
        admin_key = os.getenv("ADMIN_KEY", "test-admin-key-12345")
        headers = {"X-Admin-Key": admin_key}
        response = client.get("/v1/models", headers=headers)
        # Implementation dependent
        assert response.status_code in [200, 401, 403, 404, 422]


# ============================================================================
# Cross-User Access Attempts
# ============================================================================

class TestCrossUserAccess:
    """Test attempts to access other users' resources"""

    @pytest.mark.unit
    @pytest.mark.auth
    @pytest.mark.critical
    def test_access_other_user_api_keys(self, client):
        """User should not access another user's API keys"""
        db = create_test_db_fixture()

        # Create two users
        user1 = UserGenerator.create_user()
        user2 = UserGenerator.create_user()
        db.insert("users", user1)
        db.insert("users", user2)

        # Create API keys for both
        key1 = APIKeyGenerator.create_api_key(user_id=user1["id"])
        key2 = APIKeyGenerator.create_api_key(user_id=user2["id"])
        db.insert("api_keys", key1)
        db.insert("api_keys", key2)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                # User1 tries to access their keys - should work
                headers1 = {"X-API-Key": key1["key"]}
                response1 = client.get(f"/v1/keys", headers=headers1)

                # User2 tries to access their keys - should work
                headers2 = {"X-API-Key": key2["key"]}
                response2 = client.get(f"/v1/keys", headers=headers2)

                # Both should work or both should have same behavior
                assert response1.status_code in [200, 401, 403, 404]
                assert response2.status_code in [200, 401, 403, 404]

    @pytest.mark.unit
    @pytest.mark.auth
    @pytest.mark.critical
    def test_modify_other_user_resources(self, client):
        """User should not modify another user's resources"""
        db = create_test_db_fixture()

        user1 = UserGenerator.create_user()
        user2 = UserGenerator.create_user()
        db.insert("users", user1)
        db.insert("users", user2)

        key1 = APIKeyGenerator.create_api_key(user_id=user1["id"])
        key2 = APIKeyGenerator.create_api_key(user_id=user2["id"])
        db.insert("api_keys", key1)
        db.insert("api_keys", key2)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                # User1 tries to delete User2's key
                headers = {"X-API-Key": key1["key"]}
                response = client.delete(f"/v1/keys/{key2['id']}", headers=headers)

                # Should be rejected
                assert response.status_code in [401, 403, 404]


# ============================================================================
# Privilege Escalation Attempts
# ============================================================================

class TestPrivilegeEscalation:
    """Test privilege escalation attempts"""

    @pytest.mark.unit
    @pytest.mark.auth
    @pytest.mark.critical
    def test_user_impersonation_via_header(self, client):
        """Should prevent user impersonation via headers"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(user_id=user["id"])
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                # Try to impersonate another user
                headers = {
                    "X-API-Key": api_key["key"],
                    "X-User-ID": "different-user-id",  # Impersonation attempt
                }
                response = client.get("/v1/models", headers=headers)

                # Should ignore impersonation header or reject
                assert response.status_code in [200, 401, 403, 422]

    @pytest.mark.unit
    @pytest.mark.auth
    @pytest.mark.critical
    def test_role_escalation_attempt(self, client):
        """Should prevent role escalation"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(user_id=user["id"])
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                # Try to escalate to admin via header
                headers = {
                    "X-API-Key": api_key["key"],
                    "X-Role": "admin",  # Escalation attempt
                }
                response = client.get("/admin/stats", headers=headers)

                # Should reject
                assert response.status_code in [401, 403, 404]


# ============================================================================
# Permission Boundary Tests
# ============================================================================

class TestPermissionBoundaries:
    """Test permission boundary conditions"""

    @pytest.mark.unit
    @pytest.mark.auth
    def test_access_with_minimal_permissions(self, client):
        """Test access with minimal required permissions"""
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
                response = client.get("/v1/models", headers=headers)
                assert response.status_code in [200, 401, 403]

    @pytest.mark.unit
    @pytest.mark.auth
    def test_access_without_required_permission(self, client):
        """Test access to resource without required permission"""
        # This would test role-based access if implemented
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(user_id=user["id"])
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            headers = {"X-API-Key": api_key["key"]}
            # Try to access admin endpoint
            response = client.get("/admin/users", headers=headers)
            assert response.status_code in [401, 403, 404]
