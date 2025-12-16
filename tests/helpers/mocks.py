"""
Reusable mock patterns for Gatewayz backend tests.

This module provides standardized mocks for common services and dependencies:
- Supabase client mocking
- Rate limiting mocking
- User/API key mocking
- External service mocking (OpenAI, Anthropic, etc.)
- Database query result mocking

Usage:
    from tests.helpers.mocks import MockSupabaseClient, mock_rate_limiter

    def test_my_function(monkeypatch):
        sb_mock = MockSupabaseClient()
        monkeypatch.setattr("src.config.supabase_config.get_supabase_client", lambda: sb_mock)
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock, MagicMock


# ============================================================================
# Supabase Client Mocks
# ============================================================================

class MockQueryResult:
    """Mock Supabase query result"""

    def __init__(self, data: List[Dict] = None, count: int = None, error: Optional[Dict] = None):
        self.data = data if data is not None else []
        self.count = count if count is not None else len(self.data)
        self.error = error

    def execute(self):
        """Execute returns self for chaining"""
        return self


class MockTableQuery:
    """Mock Supabase table query builder"""

    def __init__(self, store: Dict, table_name: str):
        self.store = store
        self.table_name = table_name
        self._filters = []
        self._select_fields = "*"
        self._update_data = None
        self._insert_data = None
        self._limit_value = None
        self._order_field = None
        self._order_desc = False

    def select(self, fields: str = "*"):
        """Mock select operation"""
        self._select_fields = fields
        return self

    def insert(self, data: Dict | List[Dict]):
        """Mock insert operation"""
        self._insert_data = data
        return self

    def update(self, data: Dict):
        """Mock update operation"""
        self._update_data = data
        return self

    def eq(self, field: str, value: Any):
        """Mock equality filter"""
        self._filters.append(("eq", field, value))
        return self

    def neq(self, field: str, value: Any):
        """Mock not equal filter"""
        self._filters.append(("neq", field, value))
        return self

    def in_(self, field: str, values: List):
        """Mock IN filter"""
        self._filters.append(("in", field, values))
        return self

    def gt(self, field: str, value: Any):
        """Mock greater than filter"""
        self._filters.append(("gt", field, value))
        return self

    def gte(self, field: str, value: Any):
        """Mock greater than or equal filter"""
        self._filters.append(("gte", field, value))
        return self

    def lt(self, field: str, value: Any):
        """Mock less than filter"""
        self._filters.append(("lt", field, value))
        return self

    def lte(self, field: str, value: Any):
        """Mock less than or equal filter"""
        self._filters.append(("lte", field, value))
        return self

    def limit(self, count: int):
        """Mock limit"""
        self._limit_value = count
        return self

    def order(self, field: str, desc: bool = False):
        """Mock order by"""
        self._order_field = field
        self._order_desc = desc
        return self

    def delete(self):
        """Mock delete operation"""
        return self

    def _match_row(self, row: Dict) -> bool:
        """Check if row matches all filters"""
        for op, field, value in self._filters:
            row_value = row.get(field)

            if op == "eq" and row_value != value:
                return False
            elif op == "neq" and row_value == value:
                return False
            elif op == "in" and row_value not in value:
                return False
            elif op == "gt" and not (row_value is not None and row_value > value):
                return False
            elif op == "gte" and not (row_value is not None and row_value >= value):
                return False
            elif op == "lt" and not (row_value is not None and row_value < value):
                return False
            elif op == "lte" and not (row_value is not None and row_value <= value):
                return False

        return True

    def execute(self):
        """Execute the query"""
        # Ensure table exists
        if self.table_name not in self.store:
            self.store[self.table_name] = []

        rows = self.store[self.table_name]

        # Handle INSERT
        if self._insert_data is not None:
            data_list = self._insert_data if isinstance(self._insert_data, list) else [self._insert_data]
            self.store[self.table_name].extend(data_list)
            return MockQueryResult(data=data_list)

        # Handle UPDATE
        if self._update_data is not None:
            updated = []
            for row in rows:
                if self._match_row(row):
                    row.update(self._update_data)
                    updated.append(row)
            return MockQueryResult(data=updated)

        # Handle DELETE
        if hasattr(self, '_is_delete'):
            original_count = len(rows)
            self.store[self.table_name] = [r for r in rows if not self._match_row(r)]
            deleted_count = original_count - len(self.store[self.table_name])
            return MockQueryResult(data=[], count=deleted_count)

        # Handle SELECT
        matched = [r for r in rows if self._match_row(r)]

        # Apply ordering
        if self._order_field:
            matched = sorted(matched, key=lambda x: x.get(self._order_field, 0), reverse=self._order_desc)

        # Apply limit
        if self._limit_value:
            matched = matched[:self._limit_value]

        return MockQueryResult(data=matched)


class MockSupabaseClient:
    """
    Mock Supabase client with in-memory data store.

    Example:
        sb = MockSupabaseClient()
        sb.add_test_data("users", [{"id": 1, "username": "test"}])
        result = sb.table("users").select("*").eq("id", 1).execute()
        assert result.data[0]["username"] == "test"
    """

    def __init__(self):
        self.store = {}
        self.rpc_calls = []

    def table(self, name: str):
        """Get table reference"""
        return MockTableQuery(self.store, name)

    def rpc(self, function_name: str, params: Dict = None):
        """Mock RPC call"""
        self.rpc_calls.append((function_name, params))
        return MockQueryResult(data=[])

    def add_test_data(self, table_name: str, data: List[Dict]):
        """Helper to add test data to a table"""
        if table_name not in self.store:
            self.store[table_name] = []
        self.store[table_name].extend(data)

    def clear_table(self, table_name: str):
        """Helper to clear table data"""
        self.store[table_name] = []

    def clear_all(self):
        """Helper to clear all data"""
        self.store = {}


# ============================================================================
# Rate Limiting Mocks
# ============================================================================

class MockRateLimitResult:
    """Mock rate limit check result"""

    def __init__(
        self,
        allowed: bool = True,
        remaining_requests: int = 9999,
        remaining_tokens: int = 999999,
        reset_time: Optional[int] = None,
        retry_after: Optional[int] = None,
        reason: str = "",
        ratelimit_limit_requests: int = 10000,
        ratelimit_limit_tokens: int = 1000000,
    ):
        self.allowed = allowed
        self.remaining_requests = remaining_requests
        self.remaining_tokens = remaining_tokens
        self.reset_time = reset_time or int(datetime.now(timezone.utc).timestamp()) + 60
        self.retry_after = retry_after
        self.reason = reason
        self.ratelimit_limit_requests = ratelimit_limit_requests
        self.ratelimit_limit_tokens = ratelimit_limit_tokens
        self.ratelimit_reset_requests = self.reset_time
        self.ratelimit_reset_tokens = self.reset_time
        self.burst_window_description = "100 per 60 seconds"


def mock_rate_limiter(allowed: bool = True, **kwargs):
    """
    Create a mock rate limiter.

    Usage:
        limiter = mock_rate_limiter(allowed=True, remaining_requests=50)
        monkeypatch.setattr("src.services.rate_limiting.get_rate_limit_manager", lambda: limiter)
    """
    result = MockRateLimitResult(allowed=allowed, **kwargs)

    mock_mgr = Mock()
    mock_mgr.check_rate_limit = AsyncMock(return_value=result)
    mock_mgr.increment_request = AsyncMock()
    mock_mgr.get_rate_limit_status = AsyncMock(return_value={
        "requests_remaining": result.remaining_requests,
        "tokens_remaining": result.remaining_tokens,
        "reset_time": result.reset_time,
    })

    return mock_mgr


# ============================================================================
# User & API Key Mocks
# ============================================================================

def mock_user(
    user_id: str = "test-user-123",
    username: str = "testuser",
    email: str = "test@example.com",
    credits: float = 100.0,
    role: str = "user",
    is_admin: bool = False,
    **kwargs
) -> Dict[str, Any]:
    """
    Create a mock user object.

    Usage:
        user = mock_user(credits=50.0, is_admin=True)
    """
    user_data = {
        "id": user_id,
        "username": username,
        "email": email,
        "credits": credits,
        "role": role,
        "is_admin": is_admin,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    user_data.update(kwargs)
    return user_data


def mock_api_key(
    api_key: str = "gw_test_key_123",
    user_id: str = "test-user-123",
    is_active: bool = True,
    environment_tag: str = "test",
    **kwargs
) -> Dict[str, Any]:
    """
    Create a mock API key object.

    Usage:
        key = mock_api_key(environment_tag="live", is_active=True)
    """
    key_data = {
        "id": 1,
        "api_key": api_key,
        "user_id": user_id,
        "key_name": "Test API Key",
        "environment_tag": environment_tag,
        "is_active": is_active,
        "is_primary": False,
        "scope_permissions": {"read": ["*"], "write": ["api_keys"]},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    key_data.update(kwargs)
    return key_data


# ============================================================================
# External Service Mocks (OpenAI, Anthropic, etc.)
# ============================================================================

def mock_openai_response(
    content: str = "Hello! How can I help you?",
    model: str = "gpt-3.5-turbo",
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
    **kwargs
) -> Dict[str, Any]:
    """
    Create a mock OpenAI chat completion response.

    Usage:
        response = mock_openai_response(content="Test response", model="gpt-4")
    """
    response = {
        "id": f"chatcmpl-{datetime.now().timestamp()}",
        "object": "chat.completion",
        "created": int(datetime.now(timezone.utc).timestamp()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    response.update(kwargs)
    return response


def mock_anthropic_response(
    content: str = "Hello! How can I help you?",
    model: str = "claude-3-5-sonnet",
    input_tokens: int = 10,
    output_tokens: int = 20,
    **kwargs
) -> Dict[str, Any]:
    """
    Create a mock Anthropic messages response.

    Usage:
        response = mock_anthropic_response(content="Test response")
    """
    response = {
        "id": f"msg_{datetime.now().timestamp()}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": content}],
        "model": model,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }
    response.update(kwargs)
    return response


def mock_httpx_response(
    status_code: int = 200,
    json_data: Optional[Dict] = None,
    text: Optional[str] = None,
    headers: Optional[Dict] = None,
) -> Mock:
    """
    Create a mock httpx response.

    Usage:
        response = mock_httpx_response(status_code=200, json_data={"result": "ok"})
        monkeypatch.setattr("httpx.AsyncClient.post", AsyncMock(return_value=response))
    """
    mock_response = Mock()
    mock_response.status_code = status_code
    mock_response.headers = headers or {}

    if json_data is not None:
        mock_response.json = Mock(return_value=json_data)

    if text is not None:
        mock_response.text = text

    mock_response.raise_for_status = Mock()

    return mock_response


# ============================================================================
# Database Helpers
# ============================================================================

def create_test_db_fixture(tables: Optional[List[str]] = None) -> MockSupabaseClient:
    """
    Create a test database fixture with common tables pre-initialized.

    Usage:
        db = create_test_db_fixture(tables=["users", "api_keys_new"])
        db.add_test_data("users", [mock_user()])
    """
    db = MockSupabaseClient()

    # Initialize common tables
    default_tables = tables or [
        "users",
        "api_keys_new",
        "chat_sessions",
        "chat_messages",
        "rate_limit_usage",
        "user_plans",
        "subscription_products",
        "credit_transactions",
    ]

    for table in default_tables:
        db.store[table] = []

    return db


# ============================================================================
# FastAPI Testing Helpers
# ============================================================================

def mock_fastapi_dependency(return_value: Any):
    """
    Create a mock FastAPI dependency.

    Usage:
        mock_user_dep = mock_fastapi_dependency(mock_user())
        app.dependency_overrides[get_current_user] = mock_user_dep
    """
    async def _mock_dependency():
        return return_value
    return _mock_dependency


# ============================================================================
# Convenience Functions
# ============================================================================

def setup_standard_test_env(monkeypatch, include_user: bool = True, include_rate_limit: bool = True):
    """
    Set up a standard test environment with common mocks.

    Usage:
        def test_my_endpoint(monkeypatch):
            sb, user = setup_standard_test_env(monkeypatch)
            # Test using sb and user
    """
    # Create and configure Supabase mock
    sb = create_test_db_fixture()

    user = None
    if include_user:
        user = mock_user()
        sb.add_test_data("users", [user])
        sb.add_test_data("api_keys_new", [mock_api_key(user_id=user["id"])])

    # Patch Supabase client
    monkeypatch.setattr("src.config.supabase_config.get_supabase_client", lambda: sb)

    # Patch rate limiter
    if include_rate_limit:
        limiter = mock_rate_limiter(allowed=True)
        monkeypatch.setattr("src.services.rate_limiting.get_rate_limit_manager", lambda: limiter)

    return sb, user
