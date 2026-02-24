"""
Tests for credit reservation error handling.

Verifies that insufficient credits errors for pre-flight checks
provide detailed, actionable information to users.
"""

import pytest
from fastapi import HTTPException

from src.utils.error_factory import DetailedErrorFactory
from src.utils.exceptions import APIExceptions


class TestInsufficientCreditsForReservation:
    """Test the detailed insufficient credits error for credit reservation."""

    def test_basic_error_creation(self):
        """Test basic error creation with all required parameters."""
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=0.05,
            max_cost=0.20,
            model_id="gpt-4o",
            max_tokens=4096,
        )

        assert error_response.error.status == 402
        assert error_response.error.code == "INSUFFICIENT_CREDITS"
        assert "0.0500" in error_response.error.message  # Current credits
        assert "0.2000" in error_response.error.message  # Max cost
        assert "0.1500" in error_response.error.message  # Shortfall

    def test_context_includes_all_details(self):
        """Test that context includes all reservation details."""
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=1.00,
            max_cost=5.00,
            model_id="gpt-4",
            max_tokens=8000,
            input_tokens=150,
        )

        context = error_response.error.context
        assert context.current_credits == 1.00
        assert context.required_credits == 5.00
        assert context.credit_deficit == 4.00
        assert context.requested_model == "gpt-4"
        assert context.requested_max_tokens == 8000
        assert context.input_tokens == 150

    def test_additional_info_includes_check_type(self):
        """Test that additional_info includes check type metadata."""
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=0.10,
            max_cost=0.50,
            model_id="claude-3-opus",
            max_tokens=2000,
        )

        additional_info = error_response.error.context.additional_info
        assert additional_info["reason"] == "pre_flight_check"
        assert additional_info["check_type"] == "credit_reservation"
        assert additional_info["max_possible_cost"] == 0.50
        assert "conservative estimate" in additional_info["note"].lower()

    def test_suggestions_include_exact_shortfall(self):
        """Test that suggestions include the exact shortfall amount."""
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=0.25,
            max_cost=0.75,
            model_id="gpt-4o-mini",
            max_tokens=1000,
        )

        suggestions = error_response.error.suggestions
        # Should suggest adding exact shortfall amount
        assert any("0.5000" in s for s in suggestions)  # $0.75 - $0.25 = $0.50

    def test_suggestions_include_reduce_max_tokens(self):
        """Test that suggestions include reducing max_tokens."""
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=0.10,
            max_cost=0.40,
            model_id="gpt-4o",
            max_tokens=4096,
        )

        suggestions = error_response.error.suggestions
        # Should suggest reducing max_tokens
        assert any("reduce max_tokens" in s.lower() for s in suggestions)
        assert any("4096" in s for s in suggestions)

    def test_calculated_max_tokens_suggestion(self):
        """Test that calculated max_tokens suggestion is provided."""
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=0.10,
            max_cost=0.40,  # User has 25% of needed credits
            model_id="gpt-4o",
            max_tokens=4000,
        )

        suggestions = error_response.error.suggestions
        # Should calculate recommended max_tokens (roughly 25% of 4000 = 1000)
        # Look for a numeric suggestion
        has_calculated_suggestion = any(
            s
            for s in suggestions
            if "max_tokens to" in s.lower() and any(char.isdigit() for char in s)
        )
        assert has_calculated_suggestion

    def test_detail_message_explains_scenario(self):
        """Test that detail message fully explains the scenario."""
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=0.02,
            max_cost=0.08,
            model_id="gpt-3.5-turbo",
            max_tokens=500,
        )

        detail = error_response.error.detail
        assert "gpt-3.5-turbo" in detail
        assert "0.0800" in detail  # Max cost
        assert "0.0200" in detail  # Current credits
        assert "max_tokens=500" in detail

    def test_docs_url_points_to_credits_page(self):
        """Test that docs URL points to relevant documentation."""
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=0.01,
            max_cost=0.10,
            model_id="gpt-4o",
            max_tokens=1000,
        )

        assert "credits" in error_response.error.docs_url.lower()

    def test_support_url_is_included(self):
        """Test that support URL is included."""
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=0.01,
            max_cost=0.10,
            model_id="gpt-4o",
            max_tokens=1000,
        )

        assert error_response.error.support_url is not None
        assert "gatewayz.ai" in error_response.error.support_url


class TestAPIExceptionsReservation:
    """Test the APIExceptions convenience method for credit reservation."""

    def test_raises_http_exception(self):
        """Test that the method raises an HTTPException."""
        with pytest.raises(HTTPException) as exc_info:
            raise APIExceptions.insufficient_credits_for_reservation(
                current_credits=0.05,
                max_cost=0.20,
                model_id="gpt-4o",
                max_tokens=1000,
            )

        assert exc_info.value.status_code == 402

    def test_detail_is_structured(self):
        """Test that detail is a structured dictionary."""
        with pytest.raises(HTTPException) as exc_info:
            raise APIExceptions.insufficient_credits_for_reservation(
                current_credits=0.10,
                max_cost=0.50,
                model_id="gpt-4",
                max_tokens=2000,
                input_tokens=100,
            )

        detail = exc_info.value.detail
        assert isinstance(detail, dict)
        assert "error" in detail

    def test_error_includes_all_fields(self):
        """Test that error includes all expected fields."""
        with pytest.raises(HTTPException) as exc_info:
            raise APIExceptions.insufficient_credits_for_reservation(
                current_credits=0.25,
                max_cost=1.00,
                model_id="claude-3-opus",
                max_tokens=4096,
                input_tokens=200,
                request_id="req_test123",
            )

        detail = exc_info.value.detail
        error = detail["error"]

        assert error["type"] == "insufficient_credits"
        assert error["code"] == "INSUFFICIENT_CREDITS"
        assert error["status"] == 402
        assert error["message"]
        assert error["detail"]
        assert error["suggestions"]
        assert error["context"]
        assert error["request_id"] == "req_test123"

    def test_context_has_reservation_metadata(self):
        """Test that context includes reservation-specific metadata."""
        with pytest.raises(HTTPException) as exc_info:
            raise APIExceptions.insufficient_credits_for_reservation(
                current_credits=0.15,
                max_cost=0.60,
                model_id="gpt-4o",
                max_tokens=3000,
                input_tokens=50,
            )

        context = exc_info.value.detail["error"]["context"]
        additional_info = context["additional_info"]

        assert additional_info["reason"] == "pre_flight_check"
        assert additional_info["check_type"] == "credit_reservation"


class TestErrorScenarios:
    """Test various real-world error scenarios."""

    def test_scenario_user_with_tiny_balance(self):
        """Scenario: User with $0.01 trying expensive request."""
        with pytest.raises(HTTPException) as exc_info:
            raise APIExceptions.insufficient_credits_for_reservation(
                current_credits=0.01,
                max_cost=10.00,
                model_id="gpt-4",
                max_tokens=8000,
            )

        detail = exc_info.value.detail
        suggestions = detail["error"]["suggestions"]

        # Should suggest adding $9.99
        assert any("9.9900" in s for s in suggestions)

    def test_scenario_user_close_to_enough(self):
        """Scenario: User almost has enough credits."""
        with pytest.raises(HTTPException) as exc_info:
            raise APIExceptions.insufficient_credits_for_reservation(
                current_credits=0.95,
                max_cost=1.00,
                model_id="gpt-4o",
                max_tokens=1000,
            )

        detail = exc_info.value.detail
        suggestions = detail["error"]["suggestions"]

        # Should suggest adding small amount
        assert any("0.0500" in s for s in suggestions)

    def test_scenario_large_max_tokens(self):
        """Scenario: User requesting very large max_tokens."""
        with pytest.raises(HTTPException) as exc_info:
            raise APIExceptions.insufficient_credits_for_reservation(
                current_credits=0.50,
                max_cost=5.00,
                model_id="gpt-4",
                max_tokens=128000,  # Very large
            )

        detail = exc_info.value.detail
        suggestions = detail["error"]["suggestions"]

        # Should suggest reducing max_tokens
        assert any("128000" in s for s in suggestions)
        # Should include calculated suggestion
        has_calculated = any("max_tokens to" in s.lower() for s in suggestions)
        assert has_calculated

    def test_scenario_small_max_tokens(self):
        """Scenario: User requesting small max_tokens but still can't afford."""
        # Small max_tokens shouldn't trigger the calculated suggestion
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=0.001,
            max_cost=0.010,
            model_id="gpt-3.5-turbo",
            max_tokens=50,  # Very small
        )

        suggestions = error_response.error.suggestions
        # Basic suggestions should still be there
        assert len(suggestions) >= 3
        assert any("add" in s.lower() and "credit" in s.lower() for s in suggestions)


class TestErrorMessageClarity:
    """Test that error messages are clear and user-friendly."""

    def test_message_is_concise(self):
        """Test that main message is concise and clear."""
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=0.10,
            max_cost=0.50,
            model_id="gpt-4o",
            max_tokens=2000,
        )

        message = error_response.error.message
        # Should be one or two sentences
        assert len(message.split(".")) <= 5
        # Should mention the key numbers
        assert "0.1000" in message or "0.10" in message
        assert "0.5000" in message or "0.50" in message

    def test_detail_is_explanatory(self):
        """Test that detail provides full explanation."""
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=0.25,
            max_cost=1.00,
            model_id="claude-3-sonnet",
            max_tokens=3000,
        )

        detail = error_response.error.detail
        # Should explain what's needed
        assert "claude-3-sonnet" in detail
        assert "max_tokens=3000" in detail
        assert "1.0000" in detail
        assert "0.2500" in detail

    def test_suggestions_are_actionable(self):
        """Test that suggestions are specific and actionable."""
        error_response = DetailedErrorFactory.insufficient_credits_for_reservation(
            current_credits=0.15,
            max_cost=0.60,
            model_id="gpt-4o",
            max_tokens=4000,
        )

        suggestions = error_response.error.suggestions
        # Each suggestion should be actionable
        for suggestion in suggestions:
            # Should either mention a specific action or have a specific number
            is_actionable = any(
                action in suggestion.lower()
                for action in ["add", "reduce", "visit", "use", "try", "set"]
            ) or any(char.isdigit() for char in suggestion)
            assert is_actionable, f"Suggestion not actionable: {suggestion}"
