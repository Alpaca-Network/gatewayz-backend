"""
Conceptual Model Unit Test Suite - Shared Configuration & Fixtures

Fully isolated from the root tests/conftest.py.
Sets its own environment variables and mocks all external I/O.
"""

import os
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

# === Environment setup (BEFORE any src imports) ===
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("SUPABASE_URL", "http://fake-supabase.localhost")
os.environ.setdefault("SUPABASE_KEY", "fake-supabase-key-for-testing")
os.environ.setdefault("OPENROUTER_API_KEY", "fake-openrouter-key")
os.environ.setdefault("API_GATEWAY_SALT", "test-salt-minimum-sixteen-chars-long")
os.environ.setdefault("ADMIN_API_KEY", "fake-admin-api-key")
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_fake")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_test_fake")
os.environ.setdefault("PROMETHEUS_ENABLED", "false")
os.environ.setdefault("TEMPO_ENABLED", "false")
os.environ.setdefault("SENTRY_DSN", "")
os.environ.setdefault("ARIZE_API_KEY", "")
os.environ.setdefault("BRAINTRUST_API_KEY", "")

# Generate a valid Fernet key for encryption tests
from cryptography.fernet import Fernet

_TEST_FERNET_KEY = Fernet.generate_key().decode()
os.environ.setdefault("ENCRYPTION_KEY", _TEST_FERNET_KEY)

import pytest


# === Marker Registration ===

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "cm_verified: CM claim matches code - test should PASS"
    )
    config.addinivalue_line(
        "markers", "cm_gap: CM claim differs from code - test asserts spec, expected to fail"
    )


# === Frozen Time Helper ===

class FrozenTime:
    """Helper for controlling time in tests."""

    def __init__(self, start: float):
        self._current = start

    @property
    def current(self) -> float:
        return self._current

    def advance(self, seconds: float) -> float:
        self._current += seconds
        return self._current

    def now(self, tz=None):
        return datetime.fromtimestamp(self._current, tz=UTC)


# === Fixtures ===

@pytest.fixture
def fernet_key():
    """A valid Fernet encryption key for crypto tests."""
    return _TEST_FERNET_KEY


@pytest.fixture
def frozen_time():
    """
    Patches time.time() and datetime.now() to return a controllable value.
    Use .advance(seconds) to move time forward.
    """
    start = 1700000000.0  # Fixed epoch timestamp
    ft = FrozenTime(start)

    with patch("time.time", side_effect=lambda: ft.current), \
         patch("time.monotonic", side_effect=lambda: ft.current):
        yield ft


@pytest.fixture
def mock_supabase():
    """
    Mock Supabase client with configurable query chains.

    Usage:
        mock_supabase.table("users").select("*").eq("id", "123").execute.return_value.data = [{"id": "123"}]
    """
    mock = MagicMock()

    # Make chained calls return the same mock for fluent API
    table_mock = MagicMock()
    mock.table.return_value = table_mock

    # Support all PostgREST chain methods
    for method in ["select", "insert", "update", "delete", "upsert",
                    "eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike",
                    "is_", "in_", "not_", "or_", "and_",
                    "order", "limit", "offset", "range", "single",
                    "filter", "match", "contains", "contained_by",
                    "text_search"]:
        getattr(table_mock, method).return_value = table_mock

    # Default execute response
    execute_result = MagicMock()
    execute_result.data = []
    execute_result.count = 0
    table_mock.execute.return_value = execute_result

    # Patch both the source module AND all common import locations
    # since many modules do: from src.config.supabase_config import get_supabase_client
    patches = [
        patch("src.config.supabase_config.get_supabase_client", return_value=mock),
        patch("src.config.supabase_config._supabase_client", mock),
    ]
    # Dynamically patch any db/service modules that have already imported the function
    import sys
    for mod_path in [
        "src.db.users", "src.db.api_keys", "src.db.plans", "src.db.trials",
        "src.db.credit_transactions", "src.db.chat_history", "src.db.activity",
        "src.db.roles", "src.db.rate_limits", "src.db.coupons", "src.db.referral",
        "src.services.partner_trial_service",
    ]:
        # Only patch if the module is loaded AND has the attribute
        mod = sys.modules.get(mod_path)
        if mod and hasattr(mod, "get_supabase_client"):
            patches.append(patch(f"{mod_path}.get_supabase_client", return_value=mock))

    for p in patches:
        p.start()
    yield mock
    for p in patches:
        p.stop()


@pytest.fixture
def mock_redis():
    """
    Mock Redis client with common operations.
    """
    mock = MagicMock()
    mock.get.return_value = None
    mock.set.return_value = True
    mock.setex.return_value = True
    mock.incr.return_value = 1
    mock.expire.return_value = True
    mock.delete.return_value = 1
    mock.exists.return_value = 0
    mock.ttl.return_value = -2
    mock.scan.return_value = (0, [])

    # Pipeline support
    pipe_mock = MagicMock()
    pipe_mock.execute.return_value = []
    for method in ["get", "set", "setex", "incr", "expire", "delete"]:
        getattr(pipe_mock, method).return_value = pipe_mock
    mock.pipeline.return_value = pipe_mock

    with patch("src.config.redis_config.get_redis_client", return_value=mock):
        yield mock


@pytest.fixture
def mock_redis_unavailable():
    """Simulates Redis being completely unavailable (returns None)."""
    with patch("src.config.redis_config.get_redis_client", return_value=None):
        yield


@pytest.fixture
def mock_redis_error():
    """Simulates Redis raising ConnectionError on any operation."""
    import redis as redis_lib

    mock = MagicMock()
    error = redis_lib.ConnectionError("Redis connection refused")
    mock.get.side_effect = error
    mock.set.side_effect = error
    mock.setex.side_effect = error
    mock.incr.side_effect = error
    mock.pipeline.side_effect = error

    with patch("src.config.redis_config.get_redis_client", return_value=mock):
        yield mock


@pytest.fixture
def mock_provider_response():
    """
    Factory fixture for creating mock HTTP responses.

    Usage:
        response = mock_provider_response(status_code=200, json_data={"choices": [...]})
    """
    def _make_response(
        status_code: int = 200,
        json_data: dict | None = None,
        text: str = "",
        headers: dict | None = None,
    ):
        mock = MagicMock()
        mock.status_code = status_code
        mock.json.return_value = json_data or {}
        mock.text = text or str(json_data or "")
        mock.headers = headers or {"content-type": "application/json"}
        mock.is_success = 200 <= status_code < 300
        mock.is_error = status_code >= 400
        mock.raise_for_status = MagicMock()
        if status_code >= 400:
            mock.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
        return mock

    return _make_response


@pytest.fixture(scope="session")
def sample_messages():
    """Standard OpenAI-format messages for reuse across tests."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, how are you?"},
    ]


@pytest.fixture(scope="session")
def sample_model_catalog_entry():
    """A model catalog entry with all required fields."""
    return {
        "id": "meta-llama/Llama-3.3-70B-Instruct",
        "name": "Llama 3.3 70B Instruct",
        "provider_slug": "fireworks",
        "context_length": 131072,
        "modality": "text→text",
        "pricing": {
            "prompt": "0.00000055",
            "completion": "0.00000055",
        },
        "supports_streaming": True,
        "supports_function_calling": True,
        "supports_vision": False,
        "health_status": "healthy",
        "source_gateway": "fireworks",
    }
