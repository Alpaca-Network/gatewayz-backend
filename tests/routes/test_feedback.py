"""
Tests for the message feedback API endpoints.

Tests cover:
- POST /v1/chat/feedback - Submit feedback
- GET /v1/chat/feedback - Get user's feedback history
- GET /v1/chat/feedback/stats - Get feedback statistics
- GET /v1/chat/sessions/{session_id}/feedback - Get session feedback
- PUT /v1/chat/feedback/{feedback_id} - Update feedback
- DELETE /v1/chat/feedback/{feedback_id} - Delete feedback
"""

import importlib
from datetime import UTC, datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import the chat_history module which contains feedback endpoints
MODULE_PATH = "src.routes.chat_history"

try:
    api = importlib.import_module(MODULE_PATH)
except ModuleNotFoundError as e:
    pytest.skip(f"Missing optional dependency: {e}", allow_module_level=True)


@pytest.fixture(scope="function")
def client():
    from src.security.deps import get_api_key

    app = FastAPI()
    app.include_router(api.router, prefix="/v1")

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
def mock_feedback():
    """Return a mock feedback object"""
    return {
        "id": 1,
        "user_id": 1,
        "feedback_type": "thumbs_up",
        "session_id": 100,
        "message_id": 200,
        "model": "openai/gpt-4o",
        "rating": 5,
        "comment": "Great response!",
        "metadata": {"response_time_ms": 450},
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }


# =========================
# Tests: Submit Feedback
# =========================


@patch("src.routes.chat_history.log_activity_background")
@patch("src.routes.chat_history.save_message_feedback")
@patch("src.routes.chat_history.get_user")
def test_submit_thumbs_up_feedback(
    mock_get_user,
    mock_save_feedback,
    mock_log_activity,
    client,
    auth_headers,
    mock_user,
    mock_feedback,
):
    """Test submitting thumbs up feedback"""
    mock_get_user.return_value = mock_user
    mock_save_feedback.return_value = mock_feedback

    response = client.post(
        "/v1/chat/feedback",
        json={
            "feedback_type": "thumbs_up",
            "session_id": 100,
            "message_id": 200,
            "model": "openai/gpt-4o",
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["feedback_type"] == "thumbs_up"
    assert "thumbs_up" in data["message"]


@patch("src.routes.chat_history.log_activity_background")
@patch("src.routes.chat_history.save_message_feedback")
@patch("src.routes.chat_history.get_user")
def test_submit_thumbs_down_with_comment(
    mock_get_user, mock_save_feedback, mock_log_activity, client, auth_headers, mock_user
):
    """Test submitting thumbs down feedback with comment"""
    mock_get_user.return_value = mock_user
    mock_save_feedback.return_value = {
        "id": 2,
        "user_id": 1,
        "feedback_type": "thumbs_down",
        "comment": "Not helpful",
        "rating": 2,
        "created_at": datetime.now(UTC).isoformat(),
    }

    response = client.post(
        "/v1/chat/feedback",
        json={
            "feedback_type": "thumbs_down",
            "comment": "Not helpful",
            "rating": 2,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["feedback_type"] == "thumbs_down"


@patch("src.routes.chat_history.log_activity_background")
@patch("src.routes.chat_history.save_message_feedback")
@patch("src.routes.chat_history.get_user")
def test_submit_regenerate_feedback(
    mock_get_user, mock_save_feedback, mock_log_activity, client, auth_headers, mock_user
):
    """Test submitting regenerate feedback"""
    mock_get_user.return_value = mock_user
    mock_save_feedback.return_value = {
        "id": 3,
        "user_id": 1,
        "feedback_type": "regenerate",
        "metadata": {"original_response": "Some text"},
        "created_at": datetime.now(UTC).isoformat(),
    }

    response = client.post(
        "/v1/chat/feedback",
        json={
            "feedback_type": "regenerate",
            "metadata": {"original_response": "Some text"},
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["feedback_type"] == "regenerate"


def test_submit_feedback_invalid_type(client, auth_headers):
    """Test submitting feedback with invalid type returns 422 (Pydantic validation)"""
    response = client.post(
        "/v1/chat/feedback",
        json={"feedback_type": "invalid_type"},
        headers=auth_headers,
    )

    # Pydantic returns 422 for validation errors
    assert response.status_code == 422


def test_submit_feedback_invalid_rating(client, auth_headers):
    """Test submitting feedback with invalid rating returns 422 (Pydantic validation)"""
    response = client.post(
        "/v1/chat/feedback",
        json={"feedback_type": "thumbs_up", "rating": 6},
        headers=auth_headers,
    )

    # Pydantic returns 422 for validation errors
    assert response.status_code == 422


@patch("src.routes.chat_history.get_chat_session")
@patch("src.routes.chat_history.get_user")
def test_submit_feedback_session_not_found(
    mock_get_user, mock_get_session, client, auth_headers, mock_user
):
    """Test submitting feedback with non-existent session returns 404"""
    mock_get_user.return_value = mock_user
    mock_get_session.return_value = None

    response = client.post(
        "/v1/chat/feedback",
        json={"feedback_type": "thumbs_up", "session_id": 99999},
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert "Chat session not found" in response.json()["detail"]


@patch("src.routes.chat_history.validate_message_ownership")
@patch("src.routes.chat_history.get_user")
def test_submit_feedback_message_not_owned(
    mock_get_user, mock_validate_ownership, client, auth_headers, mock_user
):
    """Test submitting feedback with message_id not owned by user returns 404"""
    mock_get_user.return_value = mock_user
    mock_validate_ownership.return_value = False

    response = client.post(
        "/v1/chat/feedback",
        json={"feedback_type": "thumbs_up", "message_id": 99999},
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert "Message not found" in response.json()["detail"]


@patch("src.routes.chat_history.validate_message_ownership")
@patch("src.routes.chat_history.get_chat_session")
@patch("src.routes.chat_history.get_user")
def test_submit_feedback_message_not_in_session(
    mock_get_user, mock_get_session, mock_validate_ownership, client, auth_headers, mock_user
):
    """Test submitting feedback with message_id not in specified session returns 404"""
    mock_get_user.return_value = mock_user
    mock_get_session.return_value = {"id": 123, "user_id": 1}  # Session exists
    mock_validate_ownership.return_value = False  # But message doesn't belong to it

    response = client.post(
        "/v1/chat/feedback",
        json={"feedback_type": "thumbs_up", "session_id": 123, "message_id": 456},
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert "Message not found" in response.json()["detail"]


def test_submit_feedback_unauthorized(client):
    """Test submitting feedback without auth returns 401"""
    # Don't use the auth override
    app = FastAPI()
    app.include_router(api.router, prefix="/v1")

    with patch("src.routes.chat_history.get_user") as mock_get_user:
        mock_get_user.return_value = None
        test_client = TestClient(app)

        # Mock the get_api_key to return empty/invalid
        from src.security.deps import get_api_key

        app.dependency_overrides[get_api_key] = lambda: "invalid_key"

        response = test_client.post(
            "/v1/chat/feedback",
            json={"feedback_type": "thumbs_up"},
            headers={"Authorization": "Bearer invalid_key"},
        )

        # Should return 401 because get_user returns None
        assert response.status_code == 401


# =========================
# Tests: Get Feedback
# =========================


@patch("src.routes.chat_history.get_user_feedback")
@patch("src.routes.chat_history.get_user")
def test_get_user_feedback_all(
    mock_get_user, mock_get_feedback, client, auth_headers, mock_user, mock_feedback
):
    """Test getting all user feedback"""
    mock_get_user.return_value = mock_user
    mock_get_feedback.return_value = [mock_feedback, mock_feedback]

    response = client.get("/v1/chat/feedback", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["count"] == 2
    assert len(data["data"]) == 2


@patch("src.routes.chat_history.get_user_feedback")
@patch("src.routes.chat_history.get_user")
def test_get_user_feedback_filtered_by_type(
    mock_get_user, mock_get_feedback, client, auth_headers, mock_user, mock_feedback
):
    """Test getting user feedback filtered by type"""
    mock_get_user.return_value = mock_user
    mock_get_feedback.return_value = [mock_feedback]

    response = client.get("/v1/chat/feedback?feedback_type=thumbs_up", headers=auth_headers)

    assert response.status_code == 200
    mock_get_feedback.assert_called_once()
    # Verify filter was passed
    call_kwargs = mock_get_feedback.call_args[1]
    assert call_kwargs["feedback_type"] == "thumbs_up"


@patch("src.routes.chat_history.get_user_feedback")
@patch("src.routes.chat_history.get_user")
def test_get_user_feedback_filtered_by_session(
    mock_get_user, mock_get_feedback, client, auth_headers, mock_user, mock_feedback
):
    """Test getting user feedback filtered by session"""
    mock_get_user.return_value = mock_user
    mock_get_feedback.return_value = [mock_feedback]

    response = client.get("/v1/chat/feedback?session_id=100", headers=auth_headers)

    assert response.status_code == 200
    call_kwargs = mock_get_feedback.call_args[1]
    assert call_kwargs["session_id"] == 100


@patch("src.routes.chat_history.get_user_feedback")
@patch("src.routes.chat_history.get_user")
def test_get_user_feedback_pagination(
    mock_get_user, mock_get_feedback, client, auth_headers, mock_user
):
    """Test getting user feedback with pagination"""
    mock_get_user.return_value = mock_user
    mock_get_feedback.return_value = []

    response = client.get("/v1/chat/feedback?limit=10&offset=20", headers=auth_headers)

    assert response.status_code == 200
    call_kwargs = mock_get_feedback.call_args[1]
    assert call_kwargs["limit"] == 10
    assert call_kwargs["offset"] == 20


# =========================
# Tests: Get Feedback Stats
# =========================


@patch("src.routes.chat_history.get_feedback_stats")
@patch("src.routes.chat_history.get_user")
def test_get_feedback_stats(mock_get_user, mock_get_stats, client, auth_headers, mock_user):
    """Test getting feedback statistics"""
    mock_get_user.return_value = mock_user
    mock_get_stats.return_value = {
        "total_feedback": 100,
        "thumbs_up": 70,
        "thumbs_down": 20,
        "regenerate": 10,
        "thumbs_up_rate": 70.0,
        "thumbs_down_rate": 20.0,
        "average_rating": 4.2,
        "by_model": {
            "gpt-4": {"thumbs_up": 50, "thumbs_down": 10, "total": 60},
            "claude-3": {"thumbs_up": 20, "thumbs_down": 10, "total": 30},
        },
        "period_days": 30,
    }

    response = client.get("/v1/chat/feedback/stats", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["stats"]["total_feedback"] == 100
    assert data["stats"]["thumbs_up_rate"] == 70.0


@patch("src.routes.chat_history.get_feedback_stats")
@patch("src.routes.chat_history.get_user")
def test_get_feedback_stats_with_model_filter(
    mock_get_user, mock_get_stats, client, auth_headers, mock_user
):
    """Test getting feedback statistics filtered by model"""
    mock_get_user.return_value = mock_user
    mock_get_stats.return_value = {"total_feedback": 50}

    response = client.get("/v1/chat/feedback/stats?model=gpt-4&days=7", headers=auth_headers)

    assert response.status_code == 200
    call_kwargs = mock_get_stats.call_args[1]
    assert call_kwargs["model"] == "gpt-4"
    assert call_kwargs["days"] == 7


# =========================
# Tests: Get Session Feedback
# =========================


@patch("src.routes.chat_history.get_feedback_by_session")
@patch("src.routes.chat_history.get_chat_session")
@patch("src.routes.chat_history.get_user")
def test_get_session_feedback(
    mock_get_user,
    mock_get_session,
    mock_get_feedback,
    client,
    auth_headers,
    mock_user,
    mock_feedback,
):
    """Test getting feedback for a specific session"""
    mock_get_user.return_value = mock_user
    mock_get_session.return_value = {"id": 100, "user_id": 1}
    mock_get_feedback.return_value = [mock_feedback]

    response = client.get("/v1/chat/sessions/100/feedback", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["count"] == 1


@patch("src.routes.chat_history.get_chat_session")
@patch("src.routes.chat_history.get_user")
def test_get_session_feedback_not_found(
    mock_get_user, mock_get_session, client, auth_headers, mock_user
):
    """Test getting feedback for non-existent session returns 404"""
    mock_get_user.return_value = mock_user
    mock_get_session.return_value = None

    response = client.get("/v1/chat/sessions/99999/feedback", headers=auth_headers)

    assert response.status_code == 404
    assert "Chat session not found" in response.json()["detail"]


# =========================
# Tests: Update Feedback
# =========================


@patch("src.routes.chat_history.update_feedback")
@patch("src.routes.chat_history.get_user")
def test_update_feedback_success(
    mock_get_user, mock_update, client, auth_headers, mock_user, mock_feedback
):
    """Test updating feedback successfully"""
    mock_get_user.return_value = mock_user
    updated_feedback = mock_feedback.copy()
    updated_feedback["feedback_type"] = "thumbs_down"
    mock_update.return_value = updated_feedback

    response = client.put(
        "/v1/chat/feedback/1",
        json={"feedback_type": "thumbs_down"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["data"]["feedback_type"] == "thumbs_down"


@patch("src.routes.chat_history.update_feedback")
@patch("src.routes.chat_history.get_user")
def test_update_feedback_not_found(mock_get_user, mock_update, client, auth_headers, mock_user):
    """Test updating non-existent feedback returns 404"""
    mock_get_user.return_value = mock_user
    mock_update.return_value = None

    response = client.put(
        "/v1/chat/feedback/99999",
        json={"feedback_type": "thumbs_down"},
        headers=auth_headers,
    )

    assert response.status_code == 404
    assert "Feedback not found" in response.json()["detail"]


def test_update_feedback_invalid_type(client, auth_headers):
    """Test updating feedback with invalid type returns 422 (Pydantic validation)"""
    response = client.put(
        "/v1/chat/feedback/1",
        json={"feedback_type": "invalid"},
        headers=auth_headers,
    )

    # Pydantic returns 422 for validation errors
    assert response.status_code == 422


# =========================
# Tests: Delete Feedback
# =========================


@patch("src.routes.chat_history.delete_feedback")
@patch("src.routes.chat_history.get_user")
def test_delete_feedback_success(mock_get_user, mock_delete, client, auth_headers, mock_user):
    """Test deleting feedback successfully"""
    mock_get_user.return_value = mock_user
    mock_delete.return_value = True

    response = client.delete("/v1/chat/feedback/1", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "deleted" in data["message"].lower()


@patch("src.routes.chat_history.delete_feedback")
@patch("src.routes.chat_history.get_user")
def test_delete_feedback_not_found(mock_get_user, mock_delete, client, auth_headers, mock_user):
    """Test deleting non-existent feedback returns 404"""
    mock_get_user.return_value = mock_user
    mock_delete.return_value = False

    response = client.delete("/v1/chat/feedback/99999", headers=auth_headers)

    assert response.status_code == 404
    assert "Feedback not found" in response.json()["detail"]
