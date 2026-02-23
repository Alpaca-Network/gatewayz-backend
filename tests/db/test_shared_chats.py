"""
Tests for the shared_chats database functions.

Tests cover:
- create_shared_chat
- get_shared_chat_by_token
- get_user_shared_chats
- delete_shared_chat
- verify_session_ownership
- check_share_rate_limit
- generate_share_token
"""

import importlib
import pytest
from datetime import datetime, timezone, timedelta, UTC
from unittest.mock import patch, MagicMock

# Import the module
MODULE_PATH = "src.db.shared_chats"

try:
    db_module = importlib.import_module(MODULE_PATH)
except ModuleNotFoundError as e:
    pytest.skip(f"Missing optional dependency: {e}", allow_module_level=True)


# =========================
# Tests: generate_share_token
# =========================


def test_generate_share_token_is_unique():
    """Test that generate_share_token produces unique tokens"""
    token1 = db_module.generate_share_token()
    token2 = db_module.generate_share_token()

    assert token1 != token2
    assert len(token1) > 20  # Should be reasonably long
    assert len(token2) > 20


def test_generate_share_token_is_url_safe():
    """Test that generated tokens are URL-safe"""
    token = db_module.generate_share_token()

    # URL-safe characters: alphanumeric, dash, underscore
    for char in token:
        assert char.isalnum() or char in '-_'


# =========================
# Tests: create_shared_chat
# =========================


@patch("src.db.shared_chats.get_supabase_client")
def test_create_shared_chat_success(mock_get_client):
    """Test successfully creating a shared chat"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    expected_share = {
        "id": 1,
        "session_id": 100,
        "share_token": "test_token_123",
        "created_by_user_id": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": None,
        "view_count": 0,
        "is_active": True,
    }

    mock_result = MagicMock()
    mock_result.data = [expected_share]
    mock_client.table.return_value.insert.return_value.execute.return_value = mock_result

    result = db_module.create_shared_chat(session_id=100, user_id=1)

    assert result["session_id"] == 100
    assert result["created_by_user_id"] == 1
    assert result["is_active"] is True


@patch("src.db.shared_chats.get_supabase_client")
def test_create_shared_chat_with_expiry(mock_get_client):
    """Test creating shared chat with expiration date"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    expires_at = datetime.now(UTC) + timedelta(days=7)
    expected_share = {
        "id": 1,
        "session_id": 100,
        "share_token": "test_token_123",
        "created_by_user_id": 1,
        "expires_at": expires_at.isoformat(),
        "view_count": 0,
        "is_active": True,
    }

    mock_result = MagicMock()
    mock_result.data = [expected_share]
    mock_client.table.return_value.insert.return_value.execute.return_value = mock_result

    result = db_module.create_shared_chat(
        session_id=100, user_id=1, expires_at=expires_at
    )

    assert result["expires_at"] == expires_at.isoformat()


# =========================
# Tests: get_shared_chat_by_token
# =========================


@patch("src.db.shared_chats.get_supabase_client")
def test_get_shared_chat_by_token_success(mock_get_client):
    """Test getting shared chat by token"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    mock_share = {
        "id": 1,
        "session_id": 100,
        "share_token": "test_token",
        "created_by_user_id": 1,
        "expires_at": None,
        "view_count": 5,
        "is_active": True,
    }

    mock_session = {
        "id": 100,
        "user_id": 1,
        "title": "Test Session",
        "model": "openai/gpt-4o",
        "created_at": datetime.now(UTC).isoformat(),
        "is_active": True,
    }

    mock_messages = [
        {"id": 1, "session_id": 100, "role": "user", "content": "Hello"},
        {"id": 2, "session_id": 100, "role": "assistant", "content": "Hi!"},
    ]

    # Mock the chain of calls
    share_result = MagicMock()
    share_result.data = [mock_share]

    session_result = MagicMock()
    session_result.data = [mock_session]

    messages_result = MagicMock()
    messages_result.data = mock_messages

    update_result = MagicMock()
    update_result.data = [{"view_count": 6}]

    # Set up the mock to return different results based on table name
    def table_side_effect(table_name):
        mock_table = MagicMock()
        if table_name == "shared_chats":
            mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = share_result
            mock_table.update.return_value.eq.return_value.execute.return_value = update_result
        elif table_name == "chat_sessions":
            mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value = session_result
        elif table_name == "chat_messages":
            mock_table.select.return_value.eq.return_value.order.return_value.execute.return_value = messages_result
        return mock_table

    mock_client.table.side_effect = table_side_effect

    result = db_module.get_shared_chat_by_token("test_token")

    assert result is not None
    assert result["session_id"] == 100
    assert result["title"] == "Test Session"
    assert len(result["messages"]) == 2


@patch("src.db.shared_chats.get_supabase_client")
def test_get_shared_chat_by_token_not_found(mock_get_client):
    """Test getting non-existent shared chat"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    mock_result = MagicMock()
    mock_result.data = []
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = mock_result

    result = db_module.get_shared_chat_by_token("invalid_token")

    assert result is None


@patch("src.db.shared_chats.get_supabase_client")
def test_get_shared_chat_by_token_expired(mock_get_client):
    """Test getting expired shared chat returns None"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    # Expired share
    mock_share = {
        "id": 1,
        "session_id": 100,
        "share_token": "test_token",
        "expires_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "is_active": True,
    }

    mock_result = MagicMock()
    mock_result.data = [mock_share]
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = mock_result

    result = db_module.get_shared_chat_by_token("test_token")

    assert result is None


# =========================
# Tests: get_user_shared_chats
# =========================


@patch("src.db.shared_chats.get_supabase_client")
def test_get_user_shared_chats_success(mock_get_client):
    """Test getting user's shared chats"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    mock_shares = [
        {"id": 1, "session_id": 100, "share_token": "token1"},
        {"id": 2, "session_id": 101, "share_token": "token2"},
    ]

    mock_result = MagicMock()
    mock_result.data = mock_shares
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.range.return_value.execute.return_value = mock_result

    result = db_module.get_user_shared_chats(user_id=1)

    assert len(result) == 2


@patch("src.db.shared_chats.get_supabase_client")
def test_get_user_shared_chats_empty(mock_get_client):
    """Test getting shared chats when user has none"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    mock_result = MagicMock()
    mock_result.data = []
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.range.return_value.execute.return_value = mock_result

    result = db_module.get_user_shared_chats(user_id=1)

    assert result == []


# =========================
# Tests: delete_shared_chat
# =========================


@patch("src.db.shared_chats.get_supabase_client")
def test_delete_shared_chat_success(mock_get_client):
    """Test deleting a shared chat"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    mock_result = MagicMock()
    mock_result.data = [{"id": 1, "is_active": False}]
    mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = mock_result

    result = db_module.delete_shared_chat(token="test_token", user_id=1)

    assert result is True


@patch("src.db.shared_chats.get_supabase_client")
def test_delete_shared_chat_not_found(mock_get_client):
    """Test deleting non-existent shared chat"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    mock_result = MagicMock()
    mock_result.data = []
    mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = mock_result

    result = db_module.delete_shared_chat(token="invalid_token", user_id=1)

    assert result is False


# =========================
# Tests: verify_session_ownership
# =========================


@patch("src.db.shared_chats.get_supabase_client")
def test_verify_session_ownership_true(mock_get_client):
    """Test verifying session ownership returns True for owner"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    mock_result = MagicMock()
    mock_result.data = [{"id": 100}]
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = mock_result

    result = db_module.verify_session_ownership(session_id=100, user_id=1)

    assert result is True


@patch("src.db.shared_chats.get_supabase_client")
def test_verify_session_ownership_false(mock_get_client):
    """Test verifying session ownership returns False for non-owner"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    mock_result = MagicMock()
    mock_result.data = []
    mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = mock_result

    result = db_module.verify_session_ownership(session_id=100, user_id=999)

    assert result is False


# =========================
# Tests: check_share_rate_limit
# =========================


@patch("src.db.shared_chats.get_supabase_client")
def test_check_share_rate_limit_within_limit(mock_get_client):
    """Test rate limit check when within limit"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    # User has created 5 shares in the last hour (limit is 10)
    mock_result = MagicMock()
    mock_result.data = [{"id": i} for i in range(5)]
    mock_client.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value = mock_result

    result = db_module.check_share_rate_limit(user_id=1)

    assert result is True


@patch("src.db.shared_chats.get_supabase_client")
def test_check_share_rate_limit_exceeded(mock_get_client):
    """Test rate limit check when limit exceeded"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    # User has created 10 shares in the last hour (at limit)
    mock_result = MagicMock()
    mock_result.data = [{"id": i} for i in range(10)]
    mock_client.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value = mock_result

    result = db_module.check_share_rate_limit(user_id=1)

    assert result is False


@patch("src.db.shared_chats.get_supabase_client")
def test_check_share_rate_limit_custom_limit(mock_get_client):
    """Test rate limit check with custom limit"""
    mock_client = MagicMock()
    mock_get_client.return_value = mock_client

    # User has created 3 shares, custom limit is 5
    mock_result = MagicMock()
    mock_result.data = [{"id": i} for i in range(3)]
    mock_client.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value = mock_result

    result = db_module.check_share_rate_limit(user_id=1, max_shares_per_hour=5)

    assert result is True
