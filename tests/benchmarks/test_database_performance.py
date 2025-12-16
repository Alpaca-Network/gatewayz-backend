"""
Database Performance Benchmarks

Measures database query performance to identify slow queries and indexing issues.

Run with:
    pytest tests/benchmarks/test_database_performance.py --benchmark-only

Performance Targets:
    - Single record query: < 10ms
    - Batch insert (100 records): < 100ms
    - Complex join query: < 50ms
    - Aggregation query: < 75ms
"""

import pytest
from tests.helpers.mocks import create_test_db_fixture, MockSupabaseClient
from tests.helpers.data_generators import (
    UserGenerator,
    APIKeyGenerator,
    TransactionGenerator,
    ModelGenerator,
    create_complete_test_scenario
)


# ============================================================================
# Basic Query Benchmarks
# ============================================================================

class TestBasicQueryPerformance:
    """Benchmark basic database operations"""

    @pytest.fixture
    def populated_db(self):
        """Create database with test data"""
        db = create_test_db_fixture()

        # Insert test data
        users = UserGenerator.create_batch(100)
        for user in users:
            db.insert("users", user)

        api_keys = []
        for user in users:
            keys = APIKeyGenerator.create_batch(3, user_id=user["id"])
            api_keys.extend(keys)

        for key in api_keys:
            db.insert("api_keys", key)

        return db

    @pytest.mark.benchmark(group="queries-basic")
    def test_select_all_performance(self, benchmark, populated_db):
        """Benchmark SELECT * query"""

        def query():
            return populated_db.table("users").select("*").execute()

        result = benchmark(query)
        assert len(result.data) == 100

    @pytest.mark.benchmark(group="queries-basic")
    def test_select_single_record_performance(self, benchmark, populated_db):
        """Benchmark single record lookup by ID"""
        user_id = populated_db.store["users"][0]["id"]

        def query():
            return populated_db.table("users").select("*").eq("id", user_id).execute()

        result = benchmark(query)
        assert len(result.data) == 1

    @pytest.mark.benchmark(group="queries-basic")
    def test_select_with_filter_performance(self, benchmark, populated_db):
        """Benchmark filtered SELECT query"""

        def query():
            return (
                populated_db.table("users")
                .select("*")
                .eq("email_verified", True)
                .execute()
            )

        result = benchmark(query)
        # Some users should be verified
        assert isinstance(result.data, list)

    @pytest.mark.benchmark(group="queries-basic")
    def test_count_query_performance(self, benchmark, populated_db):
        """Benchmark COUNT query"""

        def query():
            result = populated_db.table("users").select("*").execute()
            return len(result.data)

        count = benchmark(query)
        assert count == 100


# ============================================================================
# Insert Performance Benchmarks
# ============================================================================

class TestInsertPerformance:
    """Benchmark insert operations"""

    @pytest.mark.benchmark(group="inserts")
    def test_single_insert_performance(self, benchmark):
        """Benchmark single record insert"""
        db = create_test_db_fixture()

        def insert():
            user = UserGenerator.create_user()
            db.insert("users", user)
            return user

        result = benchmark(insert)
        assert "id" in result

    @pytest.mark.benchmark(group="inserts")
    def test_batch_insert_small_performance(self, benchmark):
        """Benchmark small batch insert (10 records)"""
        db = create_test_db_fixture()

        def insert_batch():
            users = UserGenerator.create_batch(10)
            for user in users:
                db.insert("users", user)
            return len(users)

        count = benchmark(insert_batch)
        assert count == 10

    @pytest.mark.benchmark(group="inserts")
    def test_batch_insert_medium_performance(self, benchmark):
        """Benchmark medium batch insert (100 records)"""
        db = create_test_db_fixture()

        def insert_batch():
            users = UserGenerator.create_batch(100)
            for user in users:
                db.insert("users", user)
            return len(users)

        count = benchmark(insert_batch)
        assert count == 100

    @pytest.mark.benchmark(group="inserts")
    def test_batch_insert_large_performance(self, benchmark):
        """Benchmark large batch insert (1000 records)"""
        db = create_test_db_fixture()

        def insert_batch():
            users = UserGenerator.create_batch(1000)
            for user in users:
                db.insert("users", user)
            return len(users)

        count = benchmark(insert_batch)
        assert count == 1000


# ============================================================================
# Update Performance Benchmarks
# ============================================================================

class TestUpdatePerformance:
    """Benchmark update operations"""

    @pytest.fixture
    def db_with_users(self):
        """Create database with users"""
        db = create_test_db_fixture()
        users = UserGenerator.create_batch(100)
        for user in users:
            db.insert("users", user)
        return db, users

    @pytest.mark.benchmark(group="updates")
    def test_single_update_performance(self, benchmark, db_with_users):
        """Benchmark single record update"""
        db, users = db_with_users
        user_id = users[0]["id"]

        def update():
            return (
                db.table("users")
                .update({"full_name": "Updated Name"})
                .eq("id", user_id)
                .execute()
            )

        result = benchmark(update)

    @pytest.mark.benchmark(group="updates")
    def test_bulk_update_performance(self, benchmark, db_with_users):
        """Benchmark bulk update operation"""
        db, users = db_with_users

        def update():
            return (
                db.table("users")
                .update({"email_verified": True})
                .eq("email_verified", False)
                .execute()
            )

        result = benchmark(update)


# ============================================================================
# Delete Performance Benchmarks
# ============================================================================

class TestDeletePerformance:
    """Benchmark delete operations"""

    @pytest.mark.benchmark(group="deletes")
    def test_single_delete_performance(self, benchmark):
        """Benchmark single record delete"""

        def delete_operation():
            db = create_test_db_fixture()
            user = UserGenerator.create_user()
            db.insert("users", user)

            # Delete the user
            result = db.table("users").delete().eq("id", user["id"]).execute()
            return result

        result = benchmark(delete_operation)

    @pytest.mark.benchmark(group="deletes")
    def test_batch_delete_performance(self, benchmark):
        """Benchmark batch delete"""

        def delete_operation():
            db = create_test_db_fixture()
            users = UserGenerator.create_batch(50)
            for user in users:
                db.insert("users", user)

            # Delete all users
            result = db.table("users").delete().execute()
            return result

        result = benchmark(delete_operation)


# ============================================================================
# Complex Query Benchmarks
# ============================================================================

class TestComplexQueryPerformance:
    """Benchmark complex queries with joins and filters"""

    @pytest.fixture
    def complex_db(self):
        """Create database with relational test data"""
        db = create_test_db_fixture()

        # Create complete scenario with users, keys, transactions
        scenario = create_complete_test_scenario(num_users=50, num_api_keys_per_user=3)

        for user in scenario["users"]:
            db.insert("users", user)

        for key in scenario["api_keys"]:
            db.insert("api_keys", key)

        for transaction in scenario["transactions"]:
            db.insert("transactions", transaction)

        for usage in scenario["usage_records"]:
            db.insert("usage_records", usage)

        for model in scenario["models"]:
            db.insert("models", model)

        return db, scenario

    @pytest.mark.benchmark(group="queries-complex")
    def test_join_query_performance(self, benchmark, complex_db):
        """Benchmark query with join-like operation"""
        db, scenario = complex_db

        def query():
            # Get all API keys for first user
            user_id = scenario["users"][0]["id"]
            return db.table("api_keys").select("*").eq("user_id", user_id).execute()

        result = benchmark(query)
        assert len(result.data) > 0

    @pytest.mark.benchmark(group="queries-complex")
    def test_multi_filter_query_performance(self, benchmark, complex_db):
        """Benchmark query with multiple filters"""
        db, scenario = complex_db

        def query():
            return (
                db.table("api_keys")
                .select("*")
                .eq("status", "active")
                .limit(10)
                .execute()
            )

        result = benchmark(query)

    @pytest.mark.benchmark(group="queries-complex")
    def test_aggregation_simulation_performance(self, benchmark, complex_db):
        """Benchmark aggregation-like operation"""
        db, scenario = complex_db

        def query():
            # Simulate aggregation by fetching and processing
            user_id = scenario["users"][0]["id"]
            transactions = db.table("transactions").select("*").eq("user_id", user_id).execute()

            # Calculate total
            total = sum(t.get("amount", 0) for t in transactions.data)
            return total

        result = benchmark(query)
        assert isinstance(result, (int, float))


# ============================================================================
# Pagination Performance Benchmarks
# ============================================================================

class TestPaginationPerformance:
    """Benchmark pagination queries"""

    @pytest.fixture
    def large_db(self):
        """Create database with many records"""
        db = create_test_db_fixture()
        users = UserGenerator.create_batch(1000)
        for user in users:
            db.insert("users", user)
        return db

    @pytest.mark.benchmark(group="pagination")
    def test_first_page_performance(self, benchmark, large_db):
        """Benchmark first page query"""

        def query():
            return large_db.table("users").select("*").limit(20).execute()

        result = benchmark(query)
        assert len(result.data) == 20

    @pytest.mark.benchmark(group="pagination")
    def test_middle_page_performance(self, benchmark, large_db):
        """Benchmark middle page query (offset)"""

        def query():
            return large_db.table("users").select("*").limit(20).offset(500).execute()

        result = benchmark(query)
        assert len(result.data) == 20

    @pytest.mark.benchmark(group="pagination")
    def test_last_page_performance(self, benchmark, large_db):
        """Benchmark last page query"""

        def query():
            return large_db.table("users").select("*").limit(20).offset(980).execute()

        result = benchmark(query)
        assert len(result.data) == 20


# ============================================================================
# Search Performance Benchmarks
# ============================================================================

class TestSearchPerformance:
    """Benchmark search operations"""

    @pytest.fixture
    def searchable_db(self):
        """Create database with searchable content"""
        db = create_test_db_fixture()

        # Create users with predictable email patterns
        users = []
        for i in range(100):
            user = UserGenerator.create_user(
                email=f"user{i}@example{i % 10}.com"
            )
            users.append(user)
            db.insert("users", user)

        return db, users

    @pytest.mark.benchmark(group="search")
    def test_exact_match_search_performance(self, benchmark, searchable_db):
        """Benchmark exact match search"""
        db, users = searchable_db
        target_email = users[0]["email"]

        def query():
            return db.table("users").select("*").eq("email", target_email).execute()

        result = benchmark(query)
        assert len(result.data) == 1

    @pytest.mark.benchmark(group="search")
    def test_prefix_search_simulation_performance(self, benchmark, searchable_db):
        """Benchmark prefix search (simulated with filtering)"""
        db, users = searchable_db

        def query():
            # Simulate prefix search by fetching and filtering
            all_users = db.table("users").select("*").execute()
            matches = [u for u in all_users.data if u["email"].startswith("user1")]
            return matches

        result = benchmark(query)
        assert len(result) > 0


# ============================================================================
# Transaction Simulation Benchmarks
# ============================================================================

class TestTransactionPerformance:
    """Benchmark transaction-like operations"""

    @pytest.mark.benchmark(group="transactions")
    def test_multi_table_insert_performance(self, benchmark):
        """Benchmark inserting related records across tables"""

        def transaction():
            db = create_test_db_fixture()

            # Create user
            user = UserGenerator.create_user()
            db.insert("users", user)

            # Create API keys for user
            keys = APIKeyGenerator.create_batch(3, user_id=user["id"])
            for key in keys:
                db.insert("api_keys", key)

            # Create transactions for user
            transactions = TransactionGenerator.create_batch(5, user_id=user["id"])
            for txn in transactions:
                db.insert("transactions", txn)

            return user["id"]

        result = benchmark(transaction)
        assert result is not None

    @pytest.mark.benchmark(group="transactions")
    def test_multi_table_query_performance(self, benchmark):
        """Benchmark querying across multiple tables"""
        db = create_test_db_fixture()

        # Setup data
        user = UserGenerator.create_user()
        db.insert("users", user)

        keys = APIKeyGenerator.create_batch(3, user_id=user["id"])
        for key in keys:
            db.insert("api_keys", key)

        def query():
            # Get user
            user_result = db.table("users").select("*").eq("id", user["id"]).execute()

            # Get their API keys
            keys_result = db.table("api_keys").select("*").eq("user_id", user["id"]).execute()

            return {
                "user": user_result.data,
                "keys": keys_result.data
            }

        result = benchmark(query)
        assert len(result["user"]) == 1
        assert len(result["keys"]) == 3


# ============================================================================
# Stress Test Benchmarks
# ============================================================================

class TestStressScenarios:
    """Benchmark stress scenarios"""

    @pytest.mark.benchmark(
        group="stress",
        min_rounds=5,
        warmup=True
    )
    def test_high_volume_inserts(self, benchmark):
        """Benchmark high volume of inserts"""

        def stress_test():
            db = create_test_db_fixture()
            users = UserGenerator.create_batch(500)
            for user in users:
                db.insert("users", user)
            return len(users)

        result = benchmark(stress_test)
        assert result == 500

    @pytest.mark.benchmark(
        group="stress",
        min_rounds=5,
        warmup=True
    )
    def test_high_volume_queries(self, benchmark):
        """Benchmark high volume of queries"""
        db = create_test_db_fixture()
        users = UserGenerator.create_batch(100)
        for user in users:
            db.insert("users", user)

        def stress_test():
            results = []
            for user in users:
                result = db.table("users").select("*").eq("id", user["id"]).execute()
                results.append(result)
            return len(results)

        result = benchmark(stress_test)
        assert result == 100
