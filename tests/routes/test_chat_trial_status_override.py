"""
Tests for trial status defense-in-depth override in chat endpoint.

This test suite validates the fix for revenue leak caused by stale is_trial=TRUE flags
on paid users due to webhook delays or failures.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.routes.chat import router


@pytest.fixture(scope="function")
def client():
    """Create test client with chat router"""
    from src.security.deps import get_api_key

    app = FastAPI()
    app.include_router(router, prefix="/v1")

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
def payload_basic():
    """Basic OpenAI-compatible request payload"""
    return {
        "model": "openrouter/auto",
        "messages": [{"role": "user", "content": "Hello"}],
    }


class TestTrialStatusDefenseInDepth:
    """Tests for trial status override to prevent revenue leak"""

    @patch("src.db.users.record_usage")
    @patch("src.db.users.deduct_credits")
    @patch("src.services.pricing.calculate_cost")
    @patch("src.routes.chat.process_openrouter_response")
    @patch("src.routes.chat.make_openrouter_request_openai")
    @patch("src.services.trial_validation.validate_trial_access")
    @patch("src.services.trial_validation.track_trial_usage")
    @patch("src.db.users.get_user")
    def test_trial_override_with_active_stripe_subscription(
        self,
        mock_get_user,
        mock_track_trial,
        mock_validate_trial,
        mock_make_request,
        mock_process,
        mock_calculate_cost,
        mock_deduct,
        mock_record,
        client,
        payload_basic,
        auth_headers,
    ):
        """Test that user with is_trial=TRUE but active Stripe subscription is charged"""
        # Setup: User has is_trial=TRUE (stale) BUT active Stripe subscription
        mock_get_user.return_value = {
            "id": 1,
            "email": "user@example.com",
            "credits": 100.0,
            "stripe_subscription_id": "sub_123456",
            "subscription_status": "active",
            "tier": "free",
        }

        # Trial validation returns is_trial=TRUE (stale state)
        mock_validate_trial.return_value = {
            "is_valid": True,
            "is_trial": True,  # STALE FLAG
            "is_expired": False,
        }

        # Mock successful OpenRouter response
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(role="assistant", content="Hello!"),
                finish_reason="stop",
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        mock_make_request.return_value = mock_response
        mock_process.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        mock_calculate_cost.return_value = 0.01

        response = client.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)

        # Should succeed
        assert response.status_code == 200

        # CRITICAL: Credits should be deducted (not trial usage tracked)
        mock_deduct.assert_called_once()
        mock_record.assert_called_once()
        # Trial usage should NOT be tracked
        mock_track_trial.assert_not_called()

    @patch("src.db.users.record_usage")
    @patch("src.db.users.deduct_credits")
    @patch("src.services.pricing.calculate_cost")
    @patch("src.routes.chat.process_openrouter_response")
    @patch("src.routes.chat.make_openrouter_request_openai")
    @patch("src.services.trial_validation.validate_trial_access")
    @patch("src.services.trial_validation.track_trial_usage")
    @patch("src.db.users.get_user")
    def test_trial_override_with_pro_tier(
        self,
        mock_get_user,
        mock_track_trial,
        mock_validate_trial,
        mock_make_request,
        mock_process,
        mock_calculate_cost,
        mock_deduct,
        mock_record,
        client,
        payload_basic,
        auth_headers,
    ):
        """Test that user with is_trial=TRUE but pro tier is charged"""
        # Setup: User has is_trial=TRUE (stale) BUT pro tier
        mock_get_user.return_value = {
            "id": 1,
            "email": "user@example.com",
            "credits": 100.0,
            "tier": "pro",  # Pro tier
            "stripe_subscription_id": None,
            "subscription_status": None,
        }

        mock_validate_trial.return_value = {
            "is_valid": True,
            "is_trial": True,  # STALE FLAG
            "is_expired": False,
        }

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(role="assistant", content="Hello!"),
                finish_reason="stop",
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        mock_make_request.return_value = mock_response
        mock_process.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        mock_calculate_cost.return_value = 0.01

        response = client.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)

        assert response.status_code == 200
        # CRITICAL: Credits should be deducted
        mock_deduct.assert_called_once()
        mock_record.assert_called_once()
        # Trial usage should NOT be tracked
        mock_track_trial.assert_not_called()

    @patch("src.db.users.record_usage")
    @patch("src.db.users.deduct_credits")
    @patch("src.services.pricing.calculate_cost")
    @patch("src.routes.chat.process_openrouter_response")
    @patch("src.routes.chat.make_openrouter_request_openai")
    @patch("src.services.trial_validation.validate_trial_access")
    @patch("src.services.trial_validation.track_trial_usage")
    @patch("src.db.users.get_user")
    def test_trial_override_with_max_tier(
        self,
        mock_get_user,
        mock_track_trial,
        mock_validate_trial,
        mock_make_request,
        mock_process,
        mock_calculate_cost,
        mock_deduct,
        mock_record,
        client,
        payload_basic,
        auth_headers,
    ):
        """Test that user with is_trial=TRUE but max tier is charged"""
        mock_get_user.return_value = {
            "id": 1,
            "email": "user@example.com",
            "credits": 100.0,
            "tier": "max",  # Max tier
            "stripe_subscription_id": None,
            "subscription_status": None,
        }

        mock_validate_trial.return_value = {
            "is_valid": True,
            "is_trial": True,  # STALE FLAG
            "is_expired": False,
        }

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(role="assistant", content="Hello!"),
                finish_reason="stop",
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        mock_make_request.return_value = mock_response
        mock_process.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        mock_calculate_cost.return_value = 0.01

        response = client.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)

        assert response.status_code == 200
        mock_deduct.assert_called_once()
        mock_record.assert_called_once()
        mock_track_trial.assert_not_called()

    @patch("src.db.users.record_usage")
    @patch("src.db.users.deduct_credits")
    @patch("src.services.pricing.calculate_cost")
    @patch("src.routes.chat.process_openrouter_response")
    @patch("src.routes.chat.make_openrouter_request_openai")
    @patch("src.services.trial_validation.validate_trial_access")
    @patch("src.services.trial_validation.track_trial_usage")
    @patch("src.db.users.get_user")
    def test_trial_override_with_admin_tier(
        self,
        mock_get_user,
        mock_track_trial,
        mock_validate_trial,
        mock_make_request,
        mock_process,
        mock_calculate_cost,
        mock_deduct,
        mock_record,
        client,
        payload_basic,
        auth_headers,
    ):
        """Test that user with is_trial=TRUE but admin tier is charged"""
        mock_get_user.return_value = {
            "id": 1,
            "email": "admin@example.com",
            "credits": 100.0,
            "tier": "admin",  # Admin tier
            "stripe_subscription_id": None,
            "subscription_status": None,
        }

        mock_validate_trial.return_value = {
            "is_valid": True,
            "is_trial": True,  # STALE FLAG
            "is_expired": False,
        }

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(role="assistant", content="Hello!"),
                finish_reason="stop",
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        mock_make_request.return_value = mock_response
        mock_process.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        mock_calculate_cost.return_value = 0.01

        response = client.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)

        assert response.status_code == 200
        mock_deduct.assert_called_once()
        mock_record.assert_called_once()
        mock_track_trial.assert_not_called()

    @patch("src.db.users.record_usage")
    @patch("src.db.users.deduct_credits")
    @patch("src.services.pricing.calculate_cost")
    @patch("src.routes.chat.process_openrouter_response")
    @patch("src.routes.chat.make_openrouter_request_openai")
    @patch("src.services.trial_validation.validate_trial_access")
    @patch("src.services.trial_validation.track_trial_usage")
    @patch("src.db.users.get_user")
    def test_legitimate_trial_user_not_charged(
        self,
        mock_get_user,
        mock_track_trial,
        mock_validate_trial,
        mock_make_request,
        mock_process,
        mock_calculate_cost,
        mock_deduct,
        mock_record,
        client,
        payload_basic,
        auth_headers,
    ):
        """Test that legitimate trial users are NOT charged"""
        # Setup: User has is_trial=TRUE and NO active subscription
        mock_get_user.return_value = {
            "id": 1,
            "email": "trial@example.com",
            "credits": 0.0,
            "tier": "free",
            "stripe_subscription_id": None,
            "subscription_status": None,
        }

        mock_validate_trial.return_value = {
            "is_valid": True,
            "is_trial": True,  # Legitimate trial
            "is_expired": False,
        }

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(role="assistant", content="Hello!"),
                finish_reason="stop",
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        mock_make_request.return_value = mock_response
        mock_process.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        mock_calculate_cost.return_value = 0.01

        response = client.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)

        assert response.status_code == 200
        # Trial usage should be tracked
        mock_track_trial.assert_called_once()
        # Credits should NOT be deducted for legitimate trial
        mock_deduct.assert_not_called()
        mock_record.assert_not_called()

    @patch("src.db.users.record_usage")
    @patch("src.db.users.deduct_credits")
    @patch("src.services.pricing.calculate_cost")
    @patch("src.routes.chat.process_openrouter_response")
    @patch("src.routes.chat.make_openrouter_request_openai")
    @patch("src.services.trial_validation.validate_trial_access")
    @patch("src.services.trial_validation.track_trial_usage")
    @patch("src.db.users.get_user")
    def test_paid_user_with_correct_is_trial_false(
        self,
        mock_get_user,
        mock_track_trial,
        mock_validate_trial,
        mock_make_request,
        mock_process,
        mock_calculate_cost,
        mock_deduct,
        mock_record,
        client,
        payload_basic,
        auth_headers,
    ):
        """Test that paid users with correct is_trial=FALSE are charged normally"""
        # Setup: User has is_trial=FALSE (correct) and active subscription
        mock_get_user.return_value = {
            "id": 1,
            "email": "paid@example.com",
            "credits": 100.0,
            "tier": "pro",
            "stripe_subscription_id": "sub_123456",
            "subscription_status": "active",
        }

        mock_validate_trial.return_value = {
            "is_valid": True,
            "is_trial": False,  # Correct state
            "is_expired": False,
        }

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(role="assistant", content="Hello!"),
                finish_reason="stop",
            )
        ]
        mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        mock_make_request.return_value = mock_response
        mock_process.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        mock_calculate_cost.return_value = 0.01

        response = client.post("/v1/chat/completions", json=payload_basic, headers=auth_headers)

        assert response.status_code == 200
        # Credits should be deducted
        mock_deduct.assert_called_once()
        mock_record.assert_called_once()
        # Trial usage should NOT be tracked
        mock_track_trial.assert_not_called()
