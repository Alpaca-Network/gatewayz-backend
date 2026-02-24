"""
Playwright E2E test configuration and fixtures.

This module provides reusable fixtures for end-to-end testing
of API endpoints using HTTP requests.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

# Import db and service modules for mocking
import src.config.supabase_config
import src.db.api_keys as api_keys_module
import src.db.plans as plans_module
import src.db.users as users_module
import src.services.rate_limiting as rate_limiting_module
import src.services.trial_validation as trial_module
from src.main import create_app


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def sb():
    """Create in-memory Supabase stub for testing."""

    # Minimal in-memory database
    class _Result:
        def __init__(self, data=None, count=None):
            self.data = data if data is not None else []
            self.count = count if count is not None else len(self.data) if data else 0

        def execute(self):
            return self

    class _BaseQuery:
        def __init__(self, store, table):
            self.store = store
            self.table = table
            self._filters = []

        def eq(self, field, value):
            self._filters.append(("eq", field, value))
            return self

        def _match(self, row):
            for op, f, v in self._filters:
                rv = row.get(f)
                if op == "eq" and rv != v:
                    return False
            return True

        def execute(self):
            rows = self.store.tables.get(self.table, [])
            matched = [r for r in rows if self._match(r)]
            return _Result(matched, len(matched))

    class _SelectQuery(_BaseQuery):
        pass

    class _InsertQuery:
        def __init__(self, store, table, data):
            self.store = store
            self.table = table
            self.data = data

        def execute(self):
            if not isinstance(self.data, list):
                self.data = [self.data]
            if self.table not in self.store.tables:
                self.store.tables[self.table] = []
            self.store.tables[self.table].extend(self.data)
            return _Result(self.data)

    class _UpdateQuery(_BaseQuery):
        def __init__(self, store, table, data):
            super().__init__(store, table)
            self.update_data = data

        def execute(self):
            rows = self.store.tables.get(self.table, [])
            updated = []
            for row in rows:
                if self._match(row):
                    row.update(self.update_data)
                    updated.append(row)
            return _Result(updated, len(updated))

    class _Store:
        def __init__(self):
            self.tables = {}

        def table(self, name):
            return _TableRef(self, name)

    class _TableRef:
        def __init__(self, store, table):
            self.store = store
            self.table = table

        def select(self, *args):
            return _SelectQuery(self.store, self.table)

        def insert(self, data):
            return _InsertQuery(self.store, self.table, data)

        def update(self, data):
            return _UpdateQuery(self.store, self.table, data)

    store = _Store()

    # Add default test user
    store.table("users").insert(
        {
            "id": 1,
            "api_key": "test-api-key-123",
            "credits": 1000.0,
            "is_trial": False,
            "email": "test@example.com",
        }
    ).execute()

    return store


@pytest.fixture
def client(sb, monkeypatch):
    """Create TestClient with proper mocking for E2E tests."""
    # Mock supabase client
    monkeypatch.setattr(src.config.supabase_config, "get_supabase_client", lambda: sb)

    # Mock get_user
    def mock_get_user(api_key):
        users = sb.table("users").select("*").eq("api_key", api_key).execute()
        if users.data:
            return users.data[0]
        return None

    monkeypatch.setattr(users_module, "get_user", mock_get_user)

    # Mock deduct_credits
    def mock_deduct_credits(api_key, amount, description="", metadata=None):
        users = sb.table("users").select("*").eq("api_key", api_key).execute()
        if not users.data:
            return
        user = users.data[0]
        new_credits = max(0, user.get("credits", 0.0) - amount)
        sb.table("users").update({"credits": new_credits}).eq("api_key", api_key).execute()

    monkeypatch.setattr(users_module, "deduct_credits", mock_deduct_credits)

    # Mock trial validation
    def mock_validate_trial(api_key):
        return {"is_valid": True, "is_trial": False, "is_expired": False}

    monkeypatch.setattr(trial_module, "validate_trial_access", mock_validate_trial)

    # Mock plan limits
    def mock_enforce_plan_limits(user_id, tokens_used=0, environment_tag="live"):
        return {"allowed": True}

    monkeypatch.setattr(plans_module, "enforce_plan_limits", mock_enforce_plan_limits)

    # Mock rate limiting
    mock_rl_result = Mock()
    mock_rl_result.allowed = True
    mock_rl_result.reason = ""
    mock_rl_result.retry_after = None
    mock_rl_result.remaining_requests = 9999
    mock_rl_result.remaining_tokens = 999999

    def mock_check_rate_limit(api_key, tokens_used=0):
        return mock_rl_result

    mock_rate_mgr = Mock()
    mock_rate_mgr.check_rate_limit = AsyncMock(return_value=mock_rl_result)
    monkeypatch.setattr(rate_limiting_module, "get_rate_limit_manager", lambda: mock_rate_mgr)

    # Create app and return test client
    app = create_app()
    return TestClient(app)


@pytest.fixture
def api_key():
    """Get test API key from environment or use default."""
    return os.getenv("TEST_API_KEY", "test-api-key-123")


@pytest.fixture
def auth_headers(api_key):
    """Return authorization headers for requests."""
    return {"Authorization": f"Bearer {api_key}"}


@pytest.fixture
def base_chat_payload():
    """Base chat completion payload."""
    return {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is 2+2?"},
        ],
    }


@pytest.fixture
def base_messages_payload():
    """Base Anthropic messages payload."""
    return {
        "model": "claude-3.5-sonnet",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "What is 2+2?"},
        ],
    }


@pytest.fixture
def base_responses_payload():
    """Base unified responses payload."""
    return {
        "model": "gpt-3.5-turbo",
        "input": [
            {"role": "user", "content": "What is 2+2?"},
        ],
    }


@pytest.fixture
def base_image_payload():
    """Base image generation payload."""
    return {
        "prompt": "A beautiful sunset over the ocean",
        "model": "stable-diffusion-3.5-large",
        "n": 1,
        "size": "1024x1024",
    }
