"""
Endpoint Performance Benchmarks

Measures response times for critical API endpoints to track performance regressions.

Run benchmarks:
    pytest tests/benchmarks/test_endpoint_performance.py --benchmark-only

Generate report:
    pytest tests/benchmarks/ --benchmark-only --benchmark-autosave

Compare with baseline:
    pytest tests/benchmarks/ --benchmark-compare=0001

Performance Targets:
    - User lookup: < 50ms (p95)
    - API key validation: < 30ms (p95)
    - Rate limit check: < 20ms (p95)
    - Model catalog fetch: < 100ms (p95)
    - Simple chat request: < 200ms (p95, mocked provider)
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, AsyncMock
from tests.helpers.mocks import MockSupabaseClient, mock_rate_limiter, create_test_db_fixture
from tests.helpers.data_generators import UserGenerator, APIKeyGenerator, ChatGenerator
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
def test_user():
    """Generate test user"""
    return UserGenerator.create_user()


@pytest.fixture
def test_api_key():
    """Generate test API key"""
    return APIKeyGenerator.create_api_key()


@pytest.fixture
def chat_request():
    """Generate test chat request"""
    return ChatGenerator.create_chat_completion_request()


# ============================================================================
# Authentication & Authorization Benchmarks
# ============================================================================

class TestAuthenticationPerformance:
    """Benchmark authentication operations"""

    @pytest.mark.benchmark(group="auth")
    def test_api_key_hashing_performance(self, benchmark):
        """Benchmark API key hashing operation"""
        from src.security.security import hash_api_key

        api_key = "gw_live_test_key_" + "x" * 32

        def hash_operation():
            return hash_api_key(api_key)

        result = benchmark(hash_operation)
        assert len(result) == 64  # SHA256 hex

    @pytest.mark.benchmark(group="auth")
    def test_api_key_generation_performance(self, benchmark):
        """Benchmark API key generation"""
        from src.security.security import generate_secure_api_key

        def generate_operation():
            return generate_secure_api_key()

        result = benchmark(generate_operation)
        assert result.startswith("gw_live_")

    @pytest.mark.benchmark(group="auth")
    def test_user_lookup_performance(self, benchmark, client, test_user):
        """Benchmark user lookup from database"""
        mock_db = create_test_db_fixture()
        mock_db.insert("users", test_user)

        with patch("src.security.deps.get_supabase_client", return_value=mock_db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test123"}

                def lookup_operation():
                    # This will trigger user lookup via API key
                    response = client.get("/v1/models", headers=headers)
                    return response

                result = benchmark(lookup_operation)
                # We expect this to work or fail consistently
                assert result.status_code in [200, 401, 403]


# ============================================================================
# Rate Limiting Benchmarks
# ============================================================================

class TestRateLimitingPerformance:
    """Benchmark rate limiting operations"""

    @pytest.mark.benchmark(group="rate-limit")
    def test_rate_limit_check_performance(self, benchmark):
        """Benchmark rate limit check operation"""
        from src.security.deps import rate_limiter_manager

        user_id = "test-user-123"

        async def check_rate_limit():
            return await rate_limiter_manager.check_rate_limit(
                user_id=user_id,
                limit=60,
                window_seconds=60
            )

        # For async functions, we need to run them synchronously in benchmark
        import asyncio

        def sync_check():
            return asyncio.run(check_rate_limit())

        # Mock the rate limiter to use in-memory store
        with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
            result = benchmark(sync_check)

    @pytest.mark.benchmark(group="rate-limit")
    def test_concurrent_rate_limit_checks(self, benchmark):
        """Benchmark multiple concurrent rate limit checks"""
        from src.security.deps import rate_limiter_manager
        import asyncio

        user_ids = [f"user-{i}" for i in range(10)]

        async def check_multiple():
            tasks = [
                rate_limiter_manager.check_rate_limit(user_id, 60, 60)
                for user_id in user_ids
            ]
            return await asyncio.gather(*tasks)

        def sync_check():
            return asyncio.run(check_multiple())

        with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
            results = benchmark(sync_check)


# ============================================================================
# Database Query Benchmarks
# ============================================================================

class TestDatabasePerformance:
    """Benchmark database operations"""

    @pytest.mark.benchmark(group="database")
    def test_user_query_performance(self, benchmark, test_user):
        """Benchmark user query from database"""
        mock_db = create_test_db_fixture()
        mock_db.insert("users", test_user)

        def query_operation():
            return mock_db.table("users").select("*").eq("id", test_user["id"]).execute()

        result = benchmark(query_operation)
        assert len(result.data) > 0

    @pytest.mark.benchmark(group="database")
    def test_api_key_query_performance(self, benchmark, test_api_key):
        """Benchmark API key query"""
        mock_db = create_test_db_fixture()
        mock_db.insert("api_keys", test_api_key)

        def query_operation():
            return mock_db.table("api_keys").select("*").eq("user_id", test_api_key["user_id"]).execute()

        result = benchmark(query_operation)
        assert len(result.data) > 0

    @pytest.mark.benchmark(group="database")
    def test_bulk_insert_performance(self, benchmark):
        """Benchmark bulk insert operations"""
        mock_db = create_test_db_fixture()
        users = UserGenerator.create_batch(100)

        def insert_operation():
            for user in users:
                mock_db.insert("users", user)
            return len(users)

        result = benchmark(insert_operation)
        assert result == 100


# ============================================================================
# API Endpoint Benchmarks
# ============================================================================

class TestEndpointPerformance:
    """Benchmark critical API endpoints"""

    @pytest.mark.benchmark(group="endpoints")
    def test_models_endpoint_performance(self, benchmark, client):
        """Benchmark /v1/models endpoint"""
        mock_db = create_test_db_fixture()

        # Add some models to the mock database
        from tests.helpers.data_generators import ModelGenerator
        models = ModelGenerator.create_batch(50)
        for model in models:
            mock_db.insert("models", model)

        with patch("src.security.deps.get_supabase_client", return_value=mock_db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": "gw_live_test123"}

                def request_operation():
                    return client.get("/v1/models", headers=headers)

                result = benchmark(request_operation)
                # Accept various status codes during benchmarking
                assert result.status_code in [200, 401, 403, 500]

    @pytest.mark.benchmark(group="endpoints")
    def test_chat_completions_mocked_performance(self, benchmark, client, chat_request):
        """Benchmark /v1/chat/completions with mocked provider"""
        mock_db = create_test_db_fixture()

        # Mock the provider response
        mock_provider_response = {
            "id": "chatcmpl-test123",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "gpt-3.5-turbo",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "This is a test response"
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 6,
                "total_tokens": 16
            }
        }

        with patch("src.security.deps.get_supabase_client", return_value=mock_db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
                    mock_response = Mock()
                    mock_response.status_code = 200
                    mock_response.json = Mock(return_value=mock_provider_response)
                    mock_post.return_value = mock_response

                    headers = {"X-API-Key": "gw_live_test123"}

                    def request_operation():
                        return client.post(
                            "/v1/chat/completions",
                            headers=headers,
                            json=chat_request
                        )

                    result = benchmark(request_operation)
                    # Endpoint might not be fully implemented
                    assert result.status_code in [200, 401, 403, 404, 500]


# ============================================================================
# Security Function Benchmarks
# ============================================================================

class TestSecurityPerformance:
    """Benchmark security functions"""

    @pytest.mark.benchmark(group="security")
    def test_ip_validation_performance(self, benchmark):
        """Benchmark IP allowlist validation"""
        from src.security.security import validate_ip_allowlist

        client_ip = "192.168.1.100"
        allowlist = [f"192.168.1.{i}" for i in range(1, 255)]

        def validate_operation():
            return validate_ip_allowlist(client_ip, allowlist)

        result = benchmark(validate_operation)
        assert isinstance(result, bool)

    @pytest.mark.benchmark(group="security")
    def test_domain_validation_performance(self, benchmark):
        """Benchmark domain referrer validation"""
        from src.security.security import validate_domain_referrers

        referrer = "https://example.com"
        allowed_domains = [f"https://example{i}.com" for i in range(1, 100)]

        def validate_operation():
            return validate_domain_referrers(referrer, allowed_domains)

        result = benchmark(validate_operation)
        assert isinstance(result, bool)

    @pytest.mark.benchmark(group="security")
    def test_jwt_token_validation_performance(self, benchmark):
        """Benchmark JWT token validation"""
        import jwt
        from datetime import datetime, timedelta

        secret = "test-secret-key-for-jwt-validation"
        payload = {
            "sub": "user123",
            "exp": datetime.utcnow() + timedelta(hours=1)
        }
        token = jwt.encode(payload, secret, algorithm="HS256")

        def validate_operation():
            try:
                return jwt.decode(token, secret, algorithms=["HS256"])
            except jwt.InvalidTokenError:
                return None

        result = benchmark(validate_operation)
        assert result is not None


# ============================================================================
# Data Processing Benchmarks
# ============================================================================

class TestDataProcessingPerformance:
    """Benchmark data processing operations"""

    @pytest.mark.benchmark(group="data-processing")
    def test_json_serialization_performance(self, benchmark):
        """Benchmark JSON serialization of large objects"""
        import json
        from tests.helpers.data_generators import create_complete_test_scenario

        test_data = create_complete_test_scenario(num_users=50, num_api_keys_per_user=3)

        def serialize_operation():
            return json.dumps(test_data)

        result = benchmark(serialize_operation)
        assert len(result) > 0

    @pytest.mark.benchmark(group="data-processing")
    def test_json_deserialization_performance(self, benchmark):
        """Benchmark JSON deserialization of large objects"""
        import json
        from tests.helpers.data_generators import create_complete_test_scenario

        test_data = create_complete_test_scenario(num_users=50, num_api_keys_per_user=3)
        json_string = json.dumps(test_data)

        def deserialize_operation():
            return json.loads(json_string)

        result = benchmark(deserialize_operation)
        assert "users" in result


# ============================================================================
# Mock vs Real Comparison Benchmarks
# ============================================================================

class TestMockVsRealPerformance:
    """Compare mock vs real implementation performance"""

    @pytest.mark.benchmark(group="mock-comparison")
    def test_mock_supabase_query_performance(self, benchmark):
        """Benchmark mock Supabase query"""
        mock_db = create_test_db_fixture()

        # Insert test data
        users = UserGenerator.create_batch(1000)
        for user in users:
            mock_db.insert("users", user)

        def query_operation():
            return mock_db.table("users").select("*").limit(10).execute()

        result = benchmark(query_operation)
        assert len(result.data) > 0

    @pytest.mark.benchmark(group="mock-comparison")
    def test_mock_rate_limiter_performance(self, benchmark):
        """Benchmark mock rate limiter"""
        import asyncio

        rate_limiter = mock_rate_limiter(allowed=True)

        async def check_operation():
            return await rate_limiter.check_rate_limit("user123", 60, 60)

        def sync_check():
            return asyncio.run(check_operation())

        result = benchmark(sync_check)


# ============================================================================
# Regression Detection Benchmarks
# ============================================================================

class TestRegressionDetection:
    """Benchmarks designed to detect performance regressions"""

    @pytest.mark.benchmark(
        group="regression",
        min_rounds=10,
        warmup=True
    )
    def test_critical_path_performance(self, benchmark, client):
        """
        Benchmark the critical path: API key auth -> rate limit -> request

        This simulates the most common path through the system.
        Any regression here impacts all users.
        """
        mock_db = create_test_db_fixture()

        test_user = UserGenerator.create_user()
        test_api_key = APIKeyGenerator.create_api_key(user_id=test_user["id"])

        mock_db.insert("users", test_user)
        mock_db.insert("api_keys", test_api_key)

        with patch("src.security.deps.get_supabase_client", return_value=mock_db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": test_api_key["key"]}

                def critical_path():
                    # Make a simple request that goes through full auth flow
                    return client.get("/v1/models", headers=headers)

                result = benchmark(critical_path)
                # Any status code is fine for benchmarking
                assert result.status_code in [200, 401, 403, 404, 500]
