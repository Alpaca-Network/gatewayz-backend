"""
Integration tests for chat history routes with performance optimizations
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from src.main import create_app


@pytest.fixture
def app():
    """Create test FastAPI app"""
    return create_app()


@pytest.fixture
def client(app):
    """Create test client"""
    return TestClient(app)


class TestChatHistoryPerformance:
    """Test chat history endpoints with performance optimizations"""

    def test_create_session_uses_cached_user_lookup(self, client):
        """create_session should use cached user lookup"""
        with patch("src.routes.chat_history.get_user") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "email": "test@example.com",
                "credits": 100,
            }

            with patch("src.routes.chat_history.create_chat_session") as mock_create_session:
                mock_create_session.return_value = {
                    "id": 1,
                    "user_id": 1,
                    "title": "Test Session",
                    "model": "gpt-4",
                }

                with patch("src.routes.chat_history.log_activity_background") as mock_log:
                    response = client.post(
                        "/v1/chat/sessions",
                        json={"title": "Test Session", "model": "gpt-4"},
                        headers={"Authorization": "Bearer test_api_key"},
                    )

                    # Should use cached get_user
                    mock_get_user.assert_called_with("test_api_key")

    def test_create_session_uses_background_logging(self, client):
        """create_session should use background activity logging"""
        with patch("src.routes.chat_history.get_user") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "email": "test@example.com",
                "credits": 100,
            }

            with patch("src.routes.chat_history.create_chat_session") as mock_create_session:
                mock_create_session.return_value = {
                    "id": 1,
                    "user_id": 1,
                    "title": "Test Session",
                    "model": "gpt-4",
                }

                with patch("src.routes.chat_history.log_activity_background") as mock_log:
                    response = client.post(
                        "/v1/chat/sessions",
                        json={"title": "Test Session", "model": "gpt-4"},
                        headers={"Authorization": "Bearer test_api_key"},
                    )

                    # Should use background logging
                    mock_log.assert_called_once()

    def test_create_session_logs_performance_metrics(self, client):
        """create_session should log performance metrics"""
        with patch("src.routes.chat_history.get_user") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "email": "test@example.com",
                "credits": 100,
            }

            with patch("src.routes.chat_history.create_chat_session") as mock_create_session:
                mock_create_session.return_value = {
                    "id": 1,
                    "user_id": 1,
                    "title": "Test Session",
                    "model": "gpt-4",
                }

                with patch("src.routes.chat_history.logger") as mock_logger:
                    with patch("src.routes.chat_history.log_activity_background"):
                        response = client.post(
                            "/v1/chat/sessions",
                            json={"title": "Test Session", "model": "gpt-4"},
                            headers={"Authorization": "Bearer test_api_key"},
                        )

                        # Should log performance metrics
                        assert mock_logger.info.called or mock_logger.debug.called

    def test_create_session_returns_success_response(self, client):
        """create_session should return successful response"""
        with patch("src.routes.chat_history.get_user") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "email": "test@example.com",
                "credits": 100,
            }

            with patch("src.routes.chat_history.create_chat_session") as mock_create_session:
                mock_create_session.return_value = {
                    "id": 1,
                    "user_id": 1,
                    "title": "Test Session",
                    "model": "gpt-4",
                    "created_at": "2024-01-01T00:00:00",
                    "updated_at": "2024-01-01T00:00:00",
                    "is_active": True,
                }

                with patch("src.routes.chat_history.log_activity_background"):
                    response = client.post(
                        "/v1/chat/sessions",
                        json={"title": "Test Session", "model": "gpt-4"},
                        headers={"Authorization": "Bearer test_api_key"},
                    )

                    assert response.status_code == 200
                    data = response.json()
                    assert data["success"] is True
                    assert data["data"]["id"] == 1

    def test_create_session_invalid_api_key(self, client):
        """create_session should return 401 for invalid API key"""
        with patch("src.routes.chat_history.get_user") as mock_get_user:
            mock_get_user.return_value = None

            response = client.post(
                "/v1/chat/sessions",
                json={"title": "Test Session", "model": "gpt-4"},
                headers={"Authorization": "Bearer invalid_key"},
            )

            assert response.status_code == 401

    def test_create_session_handles_background_logging_failure(self, client):
        """create_session should handle background logging failures gracefully"""
        with patch("src.routes.chat_history.get_user") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "email": "test@example.com",
                "credits": 100,
            }

            with patch("src.routes.chat_history.create_chat_session") as mock_create_session:
                mock_create_session.return_value = {
                    "id": 1,
                    "user_id": 1,
                    "title": "Test Session",
                    "model": "gpt-4",
                }

                with patch("src.routes.chat_history.log_activity_background") as mock_log:
                    mock_log.side_effect = Exception("Logging error")

                    # Should not fail the request
                    response = client.post(
                        "/v1/chat/sessions",
                        json={"title": "Test Session", "model": "gpt-4"},
                        headers={"Authorization": "Bearer test_api_key"},
                    )

                    # Request should still succeed
                    assert response.status_code == 200

    def test_create_session_background_logging_includes_metadata(self, client):
        """create_session should pass metadata to background logging"""
        with patch("src.routes.chat_history.get_user") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "email": "test@example.com",
                "credits": 100,
            }

            with patch("src.routes.chat_history.create_chat_session") as mock_create_session:
                mock_create_session.return_value = {
                    "id": 42,
                    "user_id": 1,
                    "title": "My Session",
                    "model": "gpt-4",
                }

                with patch("src.routes.chat_history.log_activity_background") as mock_log:
                    response = client.post(
                        "/v1/chat/sessions",
                        json={"title": "My Session", "model": "gpt-4"},
                        headers={"Authorization": "Bearer test_api_key"},
                    )

                    # Verify metadata includes session info
                    call_kwargs = mock_log.call_args[1]
                    assert call_kwargs["metadata"]["action"] == "create_session"
                    assert call_kwargs["metadata"]["session_id"] == 42
                    assert call_kwargs["metadata"]["session_title"] == "My Session"

    def test_create_session_with_default_model(self, client):
        """create_session should work without explicit model"""
        with patch("src.routes.chat_history.get_user") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "email": "test@example.com",
                "credits": 100,
            }

            with patch("src.routes.chat_history.create_chat_session") as mock_create_session:
                mock_create_session.return_value = {
                    "id": 1,
                    "user_id": 1,
                    "title": "Test",
                    "model": "openai/gpt-3.5-turbo",
                }

                with patch("src.routes.chat_history.log_activity_background"):
                    response = client.post(
                        "/v1/chat/sessions",
                        json={"title": "Test"},
                        headers={"Authorization": "Bearer test_api_key"},
                    )

                    assert response.status_code == 200

    def test_create_session_with_empty_title(self, client):
        """create_session should generate title if not provided"""
        with patch("src.routes.chat_history.get_user") as mock_get_user:
            mock_get_user.return_value = {
                "id": 1,
                "email": "test@example.com",
                "credits": 100,
            }

            with patch("src.routes.chat_history.create_chat_session") as mock_create_session:
                mock_create_session.return_value = {
                    "id": 1,
                    "user_id": 1,
                    "title": "Chat 2024-01-01 12:00",
                    "model": "gpt-4",
                }

                with patch("src.routes.chat_history.log_activity_background"):
                    response = client.post(
                        "/v1/chat/sessions",
                        json={"model": "gpt-4"},
                        headers={"Authorization": "Bearer test_api_key"},
                    )

                    assert response.status_code == 200
