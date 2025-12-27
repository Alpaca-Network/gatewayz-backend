"""
Database State Verification Helpers

Provides assertion helpers for verifying database state in integration tests.
Makes it easier to verify that database operations completed correctly.

Usage:
    from tests.helpers.db_assertions import DatabaseAssertion

    db_assert = DatabaseAssertion(supabase_client)

    # Assert record exists
    db_assert.assert_record_exists("users", {"email": "test@example.com"})

    # Assert record has expected values
    db_assert.assert_record_has_values("users", user_id, {
        "email_verified": True,
        "status": "active"
    })

    # Assert count
    db_assert.assert_record_count("api_keys", 3, {"user_id": user_id})
"""

from typing import Dict, Any, Optional, List
from tests.helpers.mocks import MockSupabaseClient


class DatabaseAssertion:
    """Helper class for database state assertions"""

    def __init__(self, db_client):
        """
        Initialize with database client

        Args:
            db_client: Supabase client or MockSupabaseClient
        """
        self.db = db_client

    def assert_record_exists(
        self,
        table: str,
        filters: Dict[str, Any],
        error_msg: Optional[str] = None
    ):
        """
        Assert that a record exists with given filters

        Args:
            table: Table name
            filters: Dict of column->value filters
            error_msg: Custom error message

        Raises:
            AssertionError: If record doesn't exist
        """
        query = self.db.table(table).select("*")

        for key, value in filters.items():
            query = query.eq(key, value)

        result = query.execute()

        if not error_msg:
            error_msg = f"Record not found in {table} with filters {filters}"

        assert len(result.data) > 0, error_msg

    def assert_record_not_exists(
        self,
        table: str,
        filters: Dict[str, Any],
        error_msg: Optional[str] = None
    ):
        """
        Assert that a record does NOT exist with given filters

        Args:
            table: Table name
            filters: Dict of column->value filters
            error_msg: Custom error message

        Raises:
            AssertionError: If record exists
        """
        query = self.db.table(table).select("*")

        for key, value in filters.items():
            query = query.eq(key, value)

        result = query.execute()

        if not error_msg:
            error_msg = f"Record should not exist in {table} with filters {filters}, but found {len(result.data)} records"

        assert len(result.data) == 0, error_msg

    def assert_record_count(
        self,
        table: str,
        expected_count: int,
        filters: Optional[Dict[str, Any]] = None,
        error_msg: Optional[str] = None
    ):
        """
        Assert that table has expected number of records

        Args:
            table: Table name
            expected_count: Expected number of records
            filters: Optional filters to apply
            error_msg: Custom error message

        Raises:
            AssertionError: If count doesn't match
        """
        query = self.db.table(table).select("*")

        if filters:
            for key, value in filters.items():
                query = query.eq(key, value)

        result = query.execute()
        actual_count = len(result.data)

        if not error_msg:
            filter_str = f" with filters {filters}" if filters else ""
            error_msg = f"Expected {expected_count} records in {table}{filter_str}, but found {actual_count}"

        assert actual_count == expected_count, error_msg

    def assert_record_has_values(
        self,
        table: str,
        record_id: str,
        expected_values: Dict[str, Any],
        id_column: str = "id",
        error_msg: Optional[str] = None
    ):
        """
        Assert that a record has expected field values

        Args:
            table: Table name
            record_id: Record ID
            expected_values: Dict of column->expected_value
            id_column: Name of ID column (default: "id")
            error_msg: Custom error message

        Raises:
            AssertionError: If values don't match or record not found
        """
        result = self.db.table(table).select("*").eq(id_column, record_id).execute()

        assert len(result.data) > 0, f"Record {record_id} not found in {table}"

        record = result.data[0]

        for field, expected_value in expected_values.items():
            actual_value = record.get(field)

            if not error_msg:
                error_msg = f"Field '{field}' in {table}[{record_id}] expected {expected_value}, got {actual_value}"

            assert actual_value == expected_value, error_msg

    def assert_record_field_in(
        self,
        table: str,
        record_id: str,
        field: str,
        allowed_values: List[Any],
        id_column: str = "id",
        error_msg: Optional[str] = None
    ):
        """
        Assert that a record's field value is in allowed list

        Args:
            table: Table name
            record_id: Record ID
            field: Field name
            allowed_values: List of allowed values
            id_column: Name of ID column (default: "id")
            error_msg: Custom error message

        Raises:
            AssertionError: If value not in allowed list
        """
        result = self.db.table(table).select("*").eq(id_column, record_id).execute()

        assert len(result.data) > 0, f"Record {record_id} not found in {table}"

        record = result.data[0]
        actual_value = record.get(field)

        if not error_msg:
            error_msg = f"Field '{field}' in {table}[{record_id}] has value {actual_value}, expected one of {allowed_values}"

        assert actual_value in allowed_values, error_msg

    def assert_table_empty(self, table: str, error_msg: Optional[str] = None):
        """
        Assert that table is empty

        Args:
            table: Table name
            error_msg: Custom error message

        Raises:
            AssertionError: If table has records
        """
        result = self.db.table(table).select("*").execute()
        count = len(result.data)

        if not error_msg:
            error_msg = f"Table {table} should be empty, but has {count} records"

        assert count == 0, error_msg

    def assert_table_not_empty(self, table: str, error_msg: Optional[str] = None):
        """
        Assert that table has at least one record

        Args:
            table: Table name
            error_msg: Custom error message

        Raises:
            AssertionError: If table is empty
        """
        result = self.db.table(table).select("*").execute()

        if not error_msg:
            error_msg = f"Table {table} should not be empty"

        assert len(result.data) > 0, error_msg

    def get_record(
        self,
        table: str,
        record_id: str,
        id_column: str = "id"
    ) -> Dict[str, Any]:
        """
        Get a record by ID (helper for custom assertions)

        Args:
            table: Table name
            record_id: Record ID
            id_column: Name of ID column (default: "id")

        Returns:
            Record dict

        Raises:
            AssertionError: If record not found
        """
        result = self.db.table(table).select("*").eq(id_column, record_id).execute()

        assert len(result.data) > 0, f"Record {record_id} not found in {table}"

        return result.data[0]

    def get_records(
        self,
        table: str,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get records with optional filters (helper for custom assertions)

        Args:
            table: Table name
            filters: Optional filters to apply

        Returns:
            List of record dicts
        """
        query = self.db.table(table).select("*")

        if filters:
            for key, value in filters.items():
                query = query.eq(key, value)

        result = query.execute()
        return result.data


# ============================================================================
# Specialized Assertion Helpers
# ============================================================================

class UserAssertions(DatabaseAssertion):
    """Specialized assertions for users table"""

    def assert_user_exists(self, email: str):
        """Assert user exists by email"""
        self.assert_record_exists("users", {"email": email})

    def assert_user_verified(self, user_id: str):
        """Assert user email is verified"""
        self.assert_record_has_values("users", user_id, {"email_verified": True})

    def assert_user_not_verified(self, user_id: str):
        """Assert user email is not verified"""
        self.assert_record_has_values("users", user_id, {"email_verified": False})

    def assert_user_active(self, user_id: str):
        """Assert user is active"""
        user = self.get_record("users", user_id)
        status = user.get("status", "active")
        assert status == "active", f"User {user_id} status is {status}, expected active"


class APIKeyAssertions(DatabaseAssertion):
    """Specialized assertions for api_keys table"""

    def assert_api_key_exists(self, user_id: str, key_name: str):
        """Assert API key exists for user"""
        self.assert_record_exists("api_keys", {
            "user_id": user_id,
            "name": key_name
        })

    def assert_api_key_active(self, key_id: str):
        """Assert API key is active"""
        self.assert_record_has_values("api_keys", key_id, {"status": "active"})

    def assert_api_key_revoked(self, key_id: str):
        """Assert API key is revoked"""
        self.assert_record_has_values("api_keys", key_id, {"status": "revoked"})

    def assert_user_has_n_keys(self, user_id: str, expected_count: int):
        """Assert user has expected number of API keys"""
        self.assert_record_count("api_keys", expected_count, {"user_id": user_id})


class TransactionAssertions(DatabaseAssertion):
    """Specialized assertions for transactions table"""

    def assert_transaction_completed(self, transaction_id: str):
        """Assert transaction completed successfully"""
        self.assert_record_has_values("transactions", transaction_id, {"status": "completed"})

    def assert_transaction_failed(self, transaction_id: str):
        """Assert transaction failed"""
        self.assert_record_has_values("transactions", transaction_id, {"status": "failed"})

    def assert_user_has_transaction(
        self,
        user_id: str,
        amount: float,
        transaction_type: str
    ):
        """Assert user has transaction with amount and type"""
        self.assert_record_exists("transactions", {
            "user_id": user_id,
            "amount": amount,
            "type": transaction_type
        })


class UsageAssertions(DatabaseAssertion):
    """Specialized assertions for usage tracking"""

    def assert_usage_recorded(
        self,
        user_id: str,
        model: str,
        min_tokens: int = 0
    ):
        """Assert usage was recorded for user and model"""
        records = self.get_records("usage_records", {
            "user_id": user_id,
            "model": model
        })

        assert len(records) > 0, f"No usage records found for user {user_id} and model {model}"

        if min_tokens > 0:
            total_tokens = sum(r.get("total_tokens", 0) for r in records)
            assert total_tokens >= min_tokens, f"Total tokens {total_tokens} < minimum {min_tokens}"

    def assert_cost_calculated(self, usage_id: str):
        """Assert usage record has cost calculated"""
        record = self.get_record("usage_records", usage_id)

        assert "cost" in record, "Usage record missing cost field"
        assert record["cost"] > 0, "Usage cost should be positive"


# ============================================================================
# Combined Assertion Helper
# ============================================================================

class AllAssertions:
    """Combined helper providing all specialized assertions"""

    def __init__(self, db_client):
        """
        Initialize with database client

        Args:
            db_client: Supabase client or MockSupabaseClient
        """
        self.db = DatabaseAssertion(db_client)
        self.users = UserAssertions(db_client)
        self.api_keys = APIKeyAssertions(db_client)
        self.transactions = TransactionAssertions(db_client)
        self.usage = UsageAssertions(db_client)


# ============================================================================
# Convenience Factory
# ============================================================================

def create_db_assertions(db_client) -> AllAssertions:
    """
    Create AllAssertions instance

    Args:
        db_client: Supabase client or MockSupabaseClient

    Returns:
        AllAssertions instance with all specialized helpers

    Example:
        db_assert = create_db_assertions(supabase_client)

        # Use general assertions
        db_assert.db.assert_record_exists("users", {"id": user_id})

        # Use specialized assertions
        db_assert.users.assert_user_verified(user_id)
        db_assert.api_keys.assert_user_has_n_keys(user_id, 3)
    """
    return AllAssertions(db_client)
