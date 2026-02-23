"""
Tests for the chat share API endpoints.

Tests cover:
- POST /v1/chat/share - Create share link
- GET /v1/chat/share - Get user's share links
- GET /v1/chat/share/{token} - Get shared chat by token (public)
- DELETE /v1/chat/share/{token} - Delete share link
"""

import importlib
import pytest
from datetime import datetime, timezone, UTC
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch

# Import the share module
MODULE_PATH = "src.routes.share"

try:
    api = importlib.import_module(MODULE_PATH)
except ModuleNotFoundError as e:
    pytest.skip(f"Missing optional dependency: {e}", allow_module_level=True)


@pytest.fixture(scope="function")
def client():
    from src.security.deps import get_api_key

    app = FastAPI()
    app.include_router(api.router)

    # Override the get_api_key dependency to bypass authentication
    async def mock_get_api_key() -> str:
        return "test_api_key"

    app.dependency_overrides[get_api_key] = mock_get_api_key
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Provide authorization headers for test requests"""
    return {"Authorization": "Bearer test_api_key"}


@pytest.fixture
def mock_user():
    """Return a mock user object"""
    return {"id": 1, "email": "test@example.com", "credits": 100.0}


@pytest.fixture
def mock_share():
    """Return a mock shared chat object"""
    return {
        "id": 1,
        "session_id": 100,
        "share_token": "abc123def456",
        "created_by_user_id": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "expires_at": None,
        "view_count": 0,
        "last_viewed_at": None,
        "is_active": True,
    }


@pytest.fixture
def mock_shared_chat_data():
    """Return mock shared chat view data"""
    return {
        "session_id": 100,
        "title": "Test Chat Session",
        "model": "openai/gpt-4o",
        "created_at": datetime.now(UTC).isoformat(),
        "messages": [
            {
                "id": 1,
                "session_id": 100,
                "role": "user",
                "content": "Hello!",
                "model": None,
                "tokens": 5,
                "created_at": datetime.now(UTC).isoformat(),
            },
            {
                "id": 2,
                "session_id": 100,
                "role": "assistant",
                "content": "Hi there! How can I help you?",
                "model": "openai/gpt-4o",
                "tokens": 15,
                "created_at": datetime.now(UTC).isoformat(),
            },
        ],
    }


# =========================
# Tests: Create Share Link
# =========================


@patch("src.routes.share.create_shared_chat")
@patch("src.routes.share.verify_session_ownership")
@patch("src.routes.share.check_share_rate_limit")
@patch("src.routes.share.get_user")
def test_create_share_link_success(
    mock_get_user,
    mock_rate_limit,
    mock_verify_ownership,
    mock_create_share,
    client,
    auth_headers,
    mock_user,
    mock_share,
):
    """Test successfully creating a share link"""
    mock_get_user.return_value = mock_user
    mock_rate_limit.return_value = True
    mock_verify_ownership.return_value = True
    mock_create_share.return_value = mock_share

    response = client.post(
        "/v1/chat/share",
        json={"session_id": 100},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["session_id"] == 100
    assert data["share_token"] == "abc123def456"
    assert data["is_active"] is True


@patch("src.routes.share.get_user")
def test_create_share_link_unauthenticated(mock_get_user, client, auth_headers):
    """Test creating share link without authentication"""
    mock_get_user.return_value = None

    response = client.post(
        "/v1/chat/share",
        json={"session_id": 100},
        headers=auth_headers,
    )

    assert response.status_code == 401
    assert "Invalid API key" in response.json()["detail"]


@patch("src.routes.share.check_share_rate_limit")
@patch("src.routes.share.get_user")
def test_create_share_link_rate_limited(
    mock_get_user, mock_rate_limit, client, auth_headers, mock_user
):
    """Test creating share link when rate limited"""
    mock_get_user.return_value = mock_user
    mock_rate_limit.return_value = False

    response = client.post(
        "/v1/chat/share",
        json={"session_id": 100},
        headers=auth_headers,
    )

    assert response.status_code == 429
    assert "Rate limit exceeded" in response.json()["detail"]


@patch("src.routes.share.verify_session_ownership")
@patch("src.routes.share.check_share_rate_limit")
@patch("src.routes.share.get_user")
def test_create_share_link_session_not_found(
    mock_get_user,
    mock_rate_limit,
    mock_verify_ownership,
    client,
    auth_headers,
    mock_user,
):
    """Test creating share link for non-existent session"""
    mock_get_user.return_value = mock_user
    mock_rate_limit.return_value = True
    mock_verify_ownership.return_value = False

    response = client.post(
        "/v1/chat/share",
        json={"session_id": 999},
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


@patch("src.routes.share.create_shared_chat")
@patch("src.routes.share.verify_session_ownership")
@patch("src.routes.share.check_share_rate_limit")
@patch("src.routes.share.get_user")
def test_create_share_link_with_expiry(
    mock_get_user,
    mock_rate_limit,
    mock_verify_ownership,
    mock_create_share,
    client,
    auth_headers,
    mock_user,
    mock_share,
):
    """Test creating share link with expiration date"""
    mock_get_user.return_value = mock_user
    mock_rate_limit.return_value = True
    mock_verify_ownership.return_value = True
    mock_share["expires_at"] = "2026-12-31T23:59:59+00:00"
    mock_create_share.return_value = mock_share

    response = client.post(
        "/v1/chat/share",
        json={"session_id": 100, "expires_at": "2026-12-31T23:59:59+00:00"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["expires_at"] == "2026-12-31T23:59:59+00:00"


# =========================
# Tests: Get User's Share Links
# =========================


@patch("src.routes.share.get_user_shared_chats")
@patch("src.routes.share.get_user")
def test_get_my_share_links_success(
    mock_get_user, mock_get_shares, client, auth_headers, mock_user, mock_share
):
    """Test getting user's share links"""
    mock_get_user.return_value = mock_user
    mock_get_shares.return_value = [mock_share]

    response = client.get("/v1/chat/share", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["count"] == 1
    assert len(data["data"]) == 1


@patch("src.routes.share.get_user_shared_chats")
@patch("src.routes.share.get_user")
def test_get_my_share_links_empty(
    mock_get_user, mock_get_shares, client, auth_headers, mock_user
):
    """Test getting share links when none exist"""
    mock_get_user.return_value = mock_user
    mock_get_shares.return_value = []

    response = client.get("/v1/chat/share", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["count"] == 0
    assert len(data["data"]) == 0


@patch("src.routes.share.get_user")
def test_get_my_share_links_unauthenticated(mock_get_user, client, auth_headers):
    """Test getting share links without authentication"""
    mock_get_user.return_value = None

    response = client.get("/v1/chat/share", headers=auth_headers)

    assert response.status_code == 401


# =========================
# Tests: Get Shared Chat (Public)
# =========================


@patch("src.routes.share.get_shared_chat_by_token")
def test_get_shared_chat_success(
    mock_get_shared, client, mock_shared_chat_data
):
    """Test getting shared chat by token (public endpoint)"""
    mock_get_shared.return_value = mock_shared_chat_data

    response = client.get("/v1/chat/share/abc123def456")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["session_id"] == 100
    assert data["title"] == "Test Chat Session"
    assert len(data["messages"]) == 2


@patch("src.routes.share.get_shared_chat_by_token")
def test_get_shared_chat_not_found(mock_get_shared, client):
    """Test getting non-existent shared chat"""
    mock_get_shared.return_value = None

    response = client.get("/v1/chat/share/invalid_token")

    assert response.status_code == 404
    assert "not found or has expired" in response.json()["detail"].lower()


@patch("src.routes.share.get_shared_chat_by_token")
def test_get_shared_chat_expired(mock_get_shared, client):
    """Test getting expired shared chat"""
    mock_get_shared.return_value = None  # Expired chats return None

    response = client.get("/v1/chat/share/expired_token")

    assert response.status_code == 404


# =========================
# Tests: Delete Share Link
# =========================


@patch("src.routes.share.delete_shared_chat")
@patch("src.routes.share.get_user")
def test_delete_share_link_success(
    mock_get_user, mock_delete_share, client, auth_headers, mock_user
):
    """Test deleting a share link"""
    mock_get_user.return_value = mock_user
    mock_delete_share.return_value = True

    response = client.delete("/v1/chat/share/abc123def456", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "deleted" in data["message"].lower()


@patch("src.routes.share.delete_shared_chat")
@patch("src.routes.share.get_user")
def test_delete_share_link_not_found(
    mock_get_user, mock_delete_share, client, auth_headers, mock_user
):
    """Test deleting non-existent share link"""
    mock_get_user.return_value = mock_user
    mock_delete_share.return_value = False

    response = client.delete("/v1/chat/share/invalid_token", headers=auth_headers)

    assert response.status_code == 404


@patch("src.routes.share.get_user")
def test_delete_share_link_unauthenticated(mock_get_user, client, auth_headers):
    """Test deleting share link without authentication"""
    mock_get_user.return_value = None

    response = client.delete("/v1/chat/share/abc123def456", headers=auth_headers)

    assert response.status_code == 401


# =========================
# Tests: Edge Cases
# =========================


def test_create_share_link_missing_session_id(client, auth_headers):
    """Test creating share link without session_id"""
    response = client.post(
        "/v1/chat/share",
        json={},
        headers=auth_headers,
    )

    # Pydantic returns 422 for validation errors
    assert response.status_code == 422


@patch("src.routes.share.verify_session_ownership")
@patch("src.routes.share.check_share_rate_limit")
@patch("src.routes.share.get_user")
def test_create_share_link_invalid_expiry_format(
    mock_get_user,
    mock_rate_limit,
    mock_verify_ownership,
    client,
    auth_headers,
    mock_user,
):
    """Test creating share link with invalid expiry format"""
    mock_get_user.return_value = mock_user
    mock_rate_limit.return_value = True
    mock_verify_ownership.return_value = True

    response = client.post(
        "/v1/chat/share",
        json={"session_id": 100, "expires_at": "invalid-date"},
        headers=auth_headers,
    )

    assert response.status_code == 400
    assert "Invalid expires_at format" in response.json()["detail"]
