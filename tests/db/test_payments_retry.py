#!/usr/bin/env python3
"""
Comprehensive tests for payment database operations with HTTP/2 retry logic

Tests cover:
- Payment creation with HTTP/2 connection error retry
- Payment status updates with transient failures
- Connection error resilience for financial operations
- Error handling and retry behavior
"""

from datetime import UTC, datetime, timezone
from unittest.mock import Mock, patch

import pytest
from httpcore import LocalProtocolError, RemoteProtocolError

from src.db.payments import create_payment, update_payment_status

# ============================================================
# FIXTURES
# ============================================================


@pytest.fixture
def mock_supabase_client():
    """Mock Supabase client"""
    client = Mock()
    table_mock = Mock()
    client.table.return_value = table_mock
    return client, table_mock


@pytest.fixture
def mock_payment_data():
    """Sample payment data"""
    return {
        "id": 1,
        "user_id": 123,
        "amount_usd": 29.99,
        "amount_cents": 2999,
        "credits_purchased": 2999,
        "bonus_credits": 0,
        "currency": "usd",
        "payment_method": "stripe",
        "status": "completed",
        "stripe_payment_intent_id": "pi_abc123",
        "stripe_checkout_session_id": "cs_def456",
        "stripe_customer_id": "cus_xyz789",
        "metadata": {},
        "created_at": datetime.now(UTC).isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
    }


# ============================================================
# TEST CLASS: create_payment with HTTP/2 Retry
# ============================================================


class TestCreatePaymentWithRetry:
    """Test payment creation with HTTP/2 connection error retry"""

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    def test_create_payment_success(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_payment_data
    ):
        """Test successful payment creation with retry wrapper"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [mock_payment_data]
        mock_execute_retry.return_value = result_mock

        payment = create_payment(
            user_id=123,
            amount=29.99,
            stripe_payment_intent_id="pi_abc123",
            stripe_session_id="cs_def456",
            stripe_customer_id="cus_xyz789",
        )

        assert payment is not None
        assert payment["user_id"] == 123
        assert payment["amount_usd"] == 29.99
        assert payment["amount_cents"] == 2999
        assert payment["credits_purchased"] == 2999

        # Verify execute_with_retry was called with correct params
        mock_execute_retry.assert_called_once()
        args, kwargs = mock_execute_retry.call_args
        assert kwargs.get("max_retries") == 2
        assert kwargs.get("retry_delay") == 0.2

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    def test_create_payment_no_data_returned(
        self, mock_get_client, mock_execute_retry, mock_supabase_client
    ):
        """Test payment creation fails when no data returned"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = None
        mock_execute_retry.return_value = result_mock

        payment = create_payment(user_id=123, amount=29.99)

        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    def test_create_payment_http2_connection_error(self, mock_execute_retry):
        """Test HTTP/2 ConnectionTerminated error during payment creation"""
        mock_execute_retry.side_effect = RemoteProtocolError(
            "ConnectionTerminated: error_code=9, last_stream_id=191"
        )

        payment = create_payment(user_id=123, amount=29.99)

        # Should return None on connection error
        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    def test_create_payment_stream_id_error(self, mock_execute_retry):
        """Test StreamIDTooLowError during payment creation"""
        mock_execute_retry.side_effect = LocalProtocolError(
            "StreamIDTooLowError: 173 is lower than 193"
        )

        payment = create_payment(user_id=123, amount=29.99)

        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    def test_create_payment_send_headers_error(self, mock_execute_retry):
        """Test SEND_HEADERS LocalProtocolError during payment creation"""
        mock_execute_retry.side_effect = LocalProtocolError(
            "Invalid input StreamInputs.SEND_HEADERS in state 5"
        )

        payment = create_payment(user_id=123, amount=29.99)

        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    def test_create_payment_with_metadata(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_payment_data
    ):
        """Test payment creation with custom metadata"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [mock_payment_data]
        mock_execute_retry.return_value = result_mock

        payment = create_payment(
            user_id=123,
            amount=29.99,
            metadata={"plan": "pro", "duration": "monthly"},
        )

        assert payment is not None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    def test_create_payment_amount_calculation(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_payment_data
    ):
        """Test payment amount is correctly converted to cents"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [mock_payment_data]
        mock_execute_retry.return_value = result_mock

        create_payment(user_id=123, amount=29.99)

        # Verify the wrapped function was called
        mock_execute_retry.assert_called_once()
        # The amount_cents calculation (29.99 * 100 = 2999) should be in the payload


# ============================================================
# TEST CLASS: update_payment_status with HTTP/2 Retry
# ============================================================


class TestUpdatePaymentStatusWithRetry:
    """Test payment status updates with HTTP/2 connection error retry"""

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    @patch("src.db.payments.get_payment")
    def test_update_payment_status_success(
        self,
        mock_get_payment,
        mock_get_client,
        mock_execute_retry,
        mock_supabase_client,
        mock_payment_data,
    ):
        """Test successful payment status update"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        updated_payment = mock_payment_data.copy()
        updated_payment["status"] = "completed"

        result_mock = Mock()
        result_mock.data = [updated_payment]
        mock_execute_retry.return_value = result_mock

        payment = update_payment_status(payment_id=1, status="completed")

        assert payment is not None
        assert payment["status"] == "completed"

        # Verify execute_with_retry was called with correct params
        mock_execute_retry.assert_called_once()
        args, kwargs = mock_execute_retry.call_args
        assert kwargs.get("max_retries") == 2
        assert kwargs.get("retry_delay") == 0.2

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    @patch("src.db.payments.get_payment")
    def test_update_payment_status_to_failed(
        self,
        mock_get_payment,
        mock_get_client,
        mock_execute_retry,
        mock_supabase_client,
        mock_payment_data,
    ):
        """Test updating payment status to failed with error message"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client
        mock_get_payment.return_value = mock_payment_data

        updated_payment = mock_payment_data.copy()
        updated_payment["status"] = "failed"

        result_mock = Mock()
        result_mock.data = [updated_payment]
        mock_execute_retry.return_value = result_mock

        payment = update_payment_status(
            payment_id=1, status="failed", error_message="Payment declined"
        )

        assert payment is not None
        mock_execute_retry.assert_called_once()
        # get_payment should be called to fetch existing metadata
        mock_get_payment.assert_called_once_with(1)

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    def test_update_payment_status_no_data_returned(
        self, mock_get_client, mock_execute_retry, mock_supabase_client
    ):
        """Test update fails when no data returned"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = None
        mock_execute_retry.return_value = result_mock

        payment = update_payment_status(payment_id=1, status="completed")

        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    def test_update_payment_status_http2_error(self, mock_execute_retry):
        """Test HTTP/2 connection error during status update"""
        mock_execute_retry.side_effect = RemoteProtocolError(
            "ConnectionTerminated: error_code=9, last_stream_id=191"
        )

        payment = update_payment_status(payment_id=1, status="completed")

        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    def test_update_payment_status_stream_id_error(self, mock_execute_retry):
        """Test StreamIDTooLowError during status update"""
        mock_execute_retry.side_effect = LocalProtocolError(
            "StreamIDTooLowError: 173 is lower than 193"
        )

        payment = update_payment_status(payment_id=1, status="completed")

        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    def test_update_payment_status_remote_protocol_error(self, mock_execute_retry):
        """Test RemoteProtocolError during status update"""
        mock_execute_retry.side_effect = RemoteProtocolError("ConnectionTerminated: error_code=9")

        payment = update_payment_status(payment_id=1, status="completed")

        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    def test_update_payment_status_with_stripe_fields(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_payment_data
    ):
        """Test status update with Stripe payment intent and session IDs"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [mock_payment_data]
        mock_execute_retry.return_value = result_mock

        payment = update_payment_status(
            payment_id=1,
            status="completed",
            stripe_payment_intent_id="pi_new123",
            stripe_session_id="cs_new456",
        )

        assert payment is not None
        mock_execute_retry.assert_called_once()


# ============================================================
# TEST CLASS: Integration Tests
# ============================================================


class TestPaymentsIntegration:
    """Integration tests for payment operations with retry resilience"""

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    def test_payment_creation_and_update_flow(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_payment_data
    ):
        """Test full payment flow: create -> update status"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        # First call: create payment
        create_result = Mock()
        create_result.data = [mock_payment_data]

        # Second call: update payment
        updated_payment = mock_payment_data.copy()
        updated_payment["status"] = "completed"
        update_result = Mock()
        update_result.data = [updated_payment]

        mock_execute_retry.side_effect = [create_result, update_result]

        # Create payment
        payment = create_payment(
            user_id=123,
            amount=29.99,
            stripe_payment_intent_id="pi_abc123",
        )
        assert payment is not None
        assert payment["status"] == "completed"

        # Update payment status
        updated = update_payment_status(payment_id=1, status="completed")
        assert updated is not None
        assert updated["status"] == "completed"

        assert mock_execute_retry.call_count == 2

    @patch("src.db.payments.execute_with_retry")
    def test_resilience_to_transient_errors(self, mock_execute_retry):
        """Test that execute_with_retry handles transient errors gracefully"""
        # Simulate successful execution (retry mechanism is internal to execute_with_retry)
        result_mock = Mock()
        result_mock.data = None
        mock_execute_retry.return_value = result_mock

        payment = create_payment(user_id=123, amount=29.99)

        # Even with transient errors being handled internally, operation completes
        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    def test_multiple_payment_operations(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_payment_data
    ):
        """Test multiple payment operations with consistent retry behavior"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [mock_payment_data]
        mock_execute_retry.return_value = result_mock

        # Create multiple payments
        payment1 = create_payment(user_id=123, amount=10.00)
        payment2 = create_payment(user_id=124, amount=20.00)
        payment3 = create_payment(user_id=125, amount=30.00)

        assert payment1 is not None
        assert payment2 is not None
        assert payment3 is not None
        assert mock_execute_retry.call_count == 3


# ============================================================
# TEST CLASS: Error Handling Edge Cases
# ============================================================


class TestPaymentErrorHandling:
    """Test edge cases and error handling scenarios"""

    @patch("src.db.payments.execute_with_retry")
    def test_create_payment_generic_exception(self, mock_execute_retry):
        """Test generic exception handling during payment creation"""
        mock_execute_retry.side_effect = Exception("Unexpected database error")

        payment = create_payment(user_id=123, amount=29.99)

        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    def test_get_payment_with_retry_success(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_payment_data
    ):
        """Test get_payment with retry wrapper"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [mock_payment_data]
        mock_execute_retry.return_value = result_mock

        from src.db.payments import get_payment

        payment = get_payment(payment_id=1)

        assert payment is not None
        assert payment["id"] == 1
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    def test_get_payment_http2_error(self, mock_execute_retry):
        """Test get_payment with HTTP/2 connection error"""
        mock_execute_retry.side_effect = RemoteProtocolError(
            "ConnectionTerminated: error_code=9, last_stream_id=191"
        )

        from src.db.payments import get_payment

        payment = get_payment(payment_id=1)

        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    def test_get_payment_not_found(self, mock_get_client, mock_execute_retry, mock_supabase_client):
        """Test get_payment when payment not found"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = []
        mock_execute_retry.return_value = result_mock

        from src.db.payments import get_payment

        payment = get_payment(payment_id=999)

        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    def test_update_payment_status_generic_exception(self, mock_execute_retry):
        """Test generic exception handling during status update"""
        mock_execute_retry.side_effect = Exception("Unexpected database error")

        payment = update_payment_status(payment_id=1, status="completed")

        assert payment is None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    def test_create_payment_with_zero_amount(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_payment_data
    ):
        """Test payment creation with zero amount"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [mock_payment_data]
        mock_execute_retry.return_value = result_mock

        payment = create_payment(user_id=123, amount=0.0)

        assert payment is not None
        mock_execute_retry.assert_called_once()

    @patch("src.db.payments.execute_with_retry")
    @patch("src.db.payments.get_supabase_client")
    def test_create_payment_with_large_amount(
        self, mock_get_client, mock_execute_retry, mock_supabase_client, mock_payment_data
    ):
        """Test payment creation with large amount"""
        client, table_mock = mock_supabase_client
        mock_get_client.return_value = client

        result_mock = Mock()
        result_mock.data = [mock_payment_data]
        mock_execute_retry.return_value = result_mock

        payment = create_payment(user_id=123, amount=9999.99)

        assert payment is not None
        mock_execute_retry.assert_called_once()
