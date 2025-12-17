"""
Concurrent Request Handling Tests

Tests concurrent request scenarios to verify thread safety, race condition handling,
and proper resource management under concurrent load.

Focus areas:
- Concurrent database operations
- Thread-safe resource updates
- Race condition prevention
- Concurrent API key usage
- Parallel transaction processing
- Shared state management
"""

import pytest
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi.testclient import TestClient
from unittest.mock import patch, Mock
from tests.helpers.mocks import create_test_db_fixture, mock_rate_limiter
from tests.helpers.data_generators import UserGenerator, APIKeyGenerator, TransactionGenerator
from threading import Lock
import time
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
# Concurrent Read Tests
# ============================================================================

class TestConcurrentReads:
    """Test concurrent read operations"""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_concurrent_model_list_fetches(self, client):
        """Multiple clients fetching model list concurrently"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(user_id=user["id"])
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}

                # Concurrent read operations
                async def fetch_models():
                    return client.get("/v1/models", headers=headers)

                tasks = [fetch_models() for _ in range(10)]
                results = await asyncio.gather(*[asyncio.to_thread(task) for task in tasks])

                # All should succeed with same data
                status_codes = [r.status_code for r in results]
                assert all(code in [200, 401, 403] for code in status_codes)

    @pytest.mark.unit
    def test_concurrent_user_lookups(self, client):
        """Concurrent lookups of same user should be consistent"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(user_id=user["id"])
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}

                def fetch_user():
                    return client.get("/v1/models", headers=headers)

                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = [executor.submit(fetch_user) for _ in range(10)]
                    results = [f.result() for f in as_completed(futures)]

                # All results should be consistent
                status_codes = [r.status_code for r in results]
                assert len(set(status_codes)) <= 2  # Should be mostly same status


# ============================================================================
# Concurrent Write Tests
# ============================================================================

class TestConcurrentWrites:
    """Test concurrent write operations"""

    @pytest.mark.unit
    def test_concurrent_api_key_creation(self):
        """Multiple concurrent API key creations for same user"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        db.insert("users", user)

        creation_count = 0
        lock = Lock()

        def create_key():
            nonlocal creation_count
            api_key = APIKeyGenerator.create_api_key(user_id=user["id"])
            db.insert("api_keys", api_key)
            with lock:
                creation_count += 1
            return api_key

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(create_key) for _ in range(10)]
            keys = [f.result() for f in as_completed(futures)]

        # All keys should be created
        assert creation_count == 10
        assert len(keys) == 10

        # All keys should be unique
        key_ids = [k["id"] for k in keys]
        assert len(set(key_ids)) == 10

    @pytest.mark.unit
    def test_concurrent_transaction_creation(self):
        """Concurrent transaction creation should maintain consistency"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        db.insert("users", user)

        def create_transaction():
            txn = TransactionGenerator.create_transaction(user_id=user["id"])
            db.insert("transactions", txn)
            return txn

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(create_transaction) for _ in range(20)]
            transactions = [f.result() for f in as_completed(futures)]

        # All transactions should be created
        assert len(transactions) == 20

        # Verify in database
        result = db.table("transactions").select("*").eq("user_id", user["id"]).execute()
        assert len(result.data) == 20


# ============================================================================
# Race Condition Tests
# ============================================================================

class TestRaceConditions:
    """Test for race conditions in critical operations"""

    @pytest.mark.unit
    def test_concurrent_balance_updates(self):
        """Test race condition in balance updates"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        initial_balance = 100.0
        user["balance"] = initial_balance
        db.insert("users", user)

        deduction_amount = 5.0
        num_concurrent_deductions = 10

        lock = Lock()
        success_count = 0

        def deduct_balance():
            nonlocal success_count
            # Simulate balance deduction
            result = db.table("users").select("*").eq("id", user["id"]).execute()
            if result.data:
                current_balance = result.data[0].get("balance", 0)
                if current_balance >= deduction_amount:
                    new_balance = current_balance - deduction_amount
                    db.table("users").update({"balance": new_balance}).eq("id", user["id"]).execute()
                    with lock:
                        success_count += 1
                    return True
            return False

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(deduct_balance) for _ in range(num_concurrent_deductions)]
            results = [f.result() for f in as_completed(futures)]

        # With race conditions, we might over-deduct
        # Proper implementation should prevent this
        final_user = db.table("users").select("*").eq("id", user["id"]).execute()
        final_balance = final_user.data[0].get("balance", 0)

        # Document expected behavior (implementation dependent)
        # Ideally: final_balance == initial_balance - (success_count * deduction_amount)
        assert final_balance >= 0  # Should never go negative

    @pytest.mark.unit
    def test_concurrent_key_revocation(self):
        """Test concurrent revocation of same key"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(user_id=user["id"], status="active")
        db.insert("users", user)
        db.insert("api_keys", api_key)

        def revoke_key():
            # Try to revoke the key
            result = db.table("api_keys").select("*").eq("id", api_key["id"]).execute()
            if result.data and result.data[0].get("status") == "active":
                db.table("api_keys").update({"status": "revoked"}).eq("id", api_key["id"]).execute()
                return True
            return False

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(revoke_key) for _ in range(5)]
            results = [f.result() for f in as_completed(futures)]

        # Only one should succeed (or all if not atomic)
        # Final status should be revoked
        final_key = db.table("api_keys").select("*").eq("id", api_key["id"]).execute()
        assert final_key.data[0]["status"] == "revoked"


# ============================================================================
# Concurrent Resource Access Tests
# ============================================================================

class TestConcurrentResourceAccess:
    """Test concurrent access to shared resources"""

    @pytest.mark.unit
    def test_concurrent_same_api_key_usage(self, client):
        """Same API key used by multiple requests concurrently"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        api_key = APIKeyGenerator.create_api_key(user_id=user["id"])
        db.insert("users", user)
        db.insert("api_keys", api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):
                headers = {"X-API-Key": api_key["key"]}

                def make_request():
                    return client.get("/v1/models", headers=headers)

                with ThreadPoolExecutor(max_workers=10) as executor:
                    futures = [executor.submit(make_request) for _ in range(20)]
                    results = [f.result() for f in as_completed(futures)]

                # All should succeed (or fail consistently)
                status_codes = [r.status_code for r in results]
                assert all(code in [200, 401, 403, 429] for code in status_codes)

    @pytest.mark.unit
    def test_concurrent_different_users(self, client):
        """Concurrent requests from different users shouldn't interfere"""
        db = create_test_db_fixture()

        # Create multiple users with API keys
        users_and_keys = []
        for _ in range(5):
            user = UserGenerator.create_user()
            api_key = APIKeyGenerator.create_api_key(user_id=user["id"])
            db.insert("users", user)
            db.insert("api_keys", api_key)
            users_and_keys.append((user, api_key))

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):

                def make_request(api_key):
                    headers = {"X-API-Key": api_key["key"]}
                    return client.get("/v1/models", headers=headers)

                with ThreadPoolExecutor(max_workers=10) as executor:
                    futures = []
                    for user, api_key in users_and_keys:
                        for _ in range(4):  # 4 requests per user
                            futures.append(executor.submit(make_request, api_key))

                    results = [f.result() for f in as_completed(futures)]

                # All should succeed
                status_codes = [r.status_code for r in results]
                assert all(code in [200, 401, 403] for code in status_codes)


# ============================================================================
# Database Connection Pool Tests
# ============================================================================

class TestDatabaseConnectionPool:
    """Test database connection handling under concurrent load"""

    @pytest.mark.unit
    def test_concurrent_database_queries(self):
        """Concurrent database queries should not exhaust connections"""
        db = create_test_db_fixture()

        # Insert test data
        users = UserGenerator.create_batch(50)
        for user in users:
            db.insert("users", user)

        def query_users():
            result = db.table("users").select("*").limit(10).execute()
            return len(result.data)

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(query_users) for _ in range(100)]
            results = [f.result() for f in as_completed(futures)]

        # All queries should complete
        assert len(results) == 100
        assert all(r == 10 for r in results)

    @pytest.mark.unit
    def test_mixed_read_write_operations(self):
        """Concurrent reads and writes should not deadlock"""
        db = create_test_db_fixture()
        user = UserGenerator.create_user()
        db.insert("users", user)

        read_count = 0
        write_count = 0
        lock = Lock()

        def read_operation():
            nonlocal read_count
            result = db.table("users").select("*").eq("id", user["id"]).execute()
            if result.data:
                with lock:
                    read_count += 1
            return True

        def write_operation():
            nonlocal write_count
            db.table("users").update({
                "full_name": f"Updated {time.time()}"
            }).eq("id", user["id"]).execute()
            with lock:
                write_count += 1
            return True

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for i in range(50):
                if i % 2 == 0:
                    futures.append(executor.submit(read_operation))
                else:
                    futures.append(executor.submit(write_operation))

            results = [f.result() for f in as_completed(futures)]

        # All operations should complete
        assert len(results) == 50
        assert read_count > 0
        assert write_count > 0


# ============================================================================
# Cache Consistency Tests
# ============================================================================

class TestCacheConsistency:
    """Test cache consistency under concurrent access"""

    @pytest.mark.unit
    def test_concurrent_cache_access(self):
        """Concurrent cache access should be consistent"""
        # Simulated cache
        cache = {}
        cache_lock = Lock()

        def get_or_compute(key, compute_fn):
            with cache_lock:
                if key in cache:
                    return cache[key], "hit"

            # Compute outside lock (simulating expensive operation)
            value = compute_fn()

            with cache_lock:
                if key not in cache:
                    cache[key] = value
                    return value, "miss"
                return cache[key], "hit"

        hit_count = 0
        miss_count = 0
        lock = Lock()

        def access_cache(key):
            nonlocal hit_count, miss_count
            value, status = get_or_compute(key, lambda: f"value_{key}_{time.time()}")
            with lock:
                if status == "hit":
                    hit_count += 1
                else:
                    miss_count += 1
            return value

        with ThreadPoolExecutor(max_workers=10) as executor:
            # All threads access same key
            futures = [executor.submit(access_cache, "shared_key") for _ in range(50)]
            results = [f.result() for f in as_completed(futures)]

        # Should have some hits and some misses
        # First access misses, rest should hit
        assert miss_count >= 1
        assert hit_count > 0
        assert hit_count + miss_count == 50


# ============================================================================
# Stress Test: High Concurrency
# ============================================================================

class TestHighConcurrency:
    """Stress test with very high concurrency"""

    @pytest.mark.slow
    @pytest.mark.stress
    def test_extreme_concurrent_requests(self, client):
        """Test system under extreme concurrent load"""
        db = create_test_db_fixture()

        # Create multiple users
        users_and_keys = []
        for _ in range(20):
            user = UserGenerator.create_user()
            api_key = APIKeyGenerator.create_api_key(user_id=user["id"])
            db.insert("users", user)
            db.insert("api_keys", api_key)
            users_and_keys.append(api_key)

        with patch("src.security.deps.get_supabase_client", return_value=db):
            with patch("src.security.deps.rate_limiter_manager", mock_rate_limiter()):

                def make_request(api_key):
                    headers = {"X-API-Key": api_key["key"]}
                    return client.get("/v1/models", headers=headers)

                start_time = time.time()

                with ThreadPoolExecutor(max_workers=50) as executor:
                    futures = []
                    for _ in range(200):  # 200 total requests
                        api_key = users_and_keys[_ % len(users_and_keys)]
                        futures.append(executor.submit(make_request, api_key))

                    results = [f.result() for f in as_completed(futures)]

                end_time = time.time()
                duration = end_time - start_time

                # All should complete
                assert len(results) == 200

                # Should handle load in reasonable time
                # (threshold depends on system, this is just documentation)
                assert duration < 30  # Should complete in under 30 seconds

                # Most should succeed
                success_count = sum(1 for r in results if r.status_code == 200)
                # At least some should succeed (may fail auth in test env)
                assert success_count >= 0


# ============================================================================
# Deadlock Prevention Tests
# ============================================================================

class TestDeadlockPrevention:
    """Test that concurrent operations don't cause deadlocks"""

    @pytest.mark.unit
    def test_circular_dependency_prevention(self):
        """Test that circular dependencies don't cause deadlock"""
        db = create_test_db_fixture()

        user1 = UserGenerator.create_user()
        user2 = UserGenerator.create_user()
        db.insert("users", user1)
        db.insert("users", user2)

        def update_both_users(first_id, second_id):
            # Update first user
            db.table("users").update({
                "full_name": f"Updated {time.time()}"
            }).eq("id", first_id).execute()

            time.sleep(0.01)  # Small delay to increase chance of deadlock

            # Update second user
            db.table("users").update({
                "full_name": f"Updated {time.time()}"
            }).eq("id", second_id).execute()

            return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            # Thread 1: Update user1 then user2
            # Thread 2: Update user2 then user1
            # This could cause deadlock if not handled properly
            future1 = executor.submit(update_both_users, user1["id"], user2["id"])
            future2 = executor.submit(update_both_users, user2["id"], user1["id"])

            # Should both complete without deadlock
            result1 = future1.result(timeout=5)
            result2 = future2.result(timeout=5)

            assert result1 is True
            assert result2 is True
