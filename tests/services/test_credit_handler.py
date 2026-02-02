"""
Tests for the unified credit handler service.

These tests verify that the credit deduction logic works correctly, including:
- Retry logic for transient failures
- Proper error handling and alerting
- Metrics recording
- Trial vs paid user handling
- Streaming vs non-streaming request handling
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from src.services.credit_handler import (
    CREDIT_DEDUCTION_MAX_RETRIES,
    CREDIT_DEDUCTION_RETRY_DELAYS,
    handle_credits_and_usage,
    handle_credits_and_usage_with_fallback,
    _record_credit_metrics,
    _record_missed_deduction,
    _record_background_task_failure,
    _send_critical_billing_alert,
)


# Test fixtures
@pytest.fixture
def mock_user():
    """Create a mock paid user."""
    return {
        "id": 123,
        "tier": "pro",
        "subscription_status": "active",
        "stripe_subscription_id": "sub_abc123",
        "credits": 100.0,
    }


@pytest.fixture
def mock_trial_user():
    """Create a mock trial user."""
    return {
        "id": 456,
        "tier": "trial",
        "subscription_status": None,
        "stripe_subscription_id": None,
        "credits": 0.0,
    }


@pytest.fixture
def mock_trial_active():
    """Create an active trial status."""
    return {
        "is_trial": True,
        "is_expired": False,
    }


@pytest.fixture
def mock_trial_inactive():
    """Create an inactive trial status."""
    return {
        "is_trial": False,
        "is_expired": False,
    }


class TestRecordCreditMetrics:
    """Test Prometheus metrics recording for credit deductions."""

    @patch("src.services.prometheus_metrics.credit_deduction_total")
    @patch("src.services.prometheus_metrics.credit_deduction_amount_usd")
    @patch("src.services.prometheus_metrics.credit_deduction_latency")
    @patch("src.services.prometheus_metrics.credit_deduction_retry_count")
    def test_records_success_metrics(self, mock_retry, mock_latency, mock_amount, mock_total):
        """Test that success metrics are recorded correctly."""
        mock_total.labels.return_value.inc = MagicMock()
        mock_amount.labels.return_value.inc = MagicMock()
        mock_latency.labels.return_value.observe = MagicMock()

        _record_credit_metrics(
            status="success",
            cost=0.05,
            endpoint="/v1/chat/completions",
            is_streaming=False,
            latency_seconds=0.5,
            attempt_number=1,
        )

        mock_total.labels.assert_called_once_with(
            status="success",
            endpoint="/v1/chat/completions",
            is_streaming="false",
        )
        mock_amount.labels.assert_called_once_with(
            status="success",
            endpoint="/v1/chat/completions",
        )
        mock_latency.labels.assert_called_once_with(
            endpoint="/v1/chat/completions",
            is_streaming="false",
        )

    @patch("src.services.prometheus_metrics.credit_deduction_total")
    @patch("src.services.prometheus_metrics.credit_deduction_amount_usd")
    @patch("src.services.prometheus_metrics.credit_deduction_latency")
    @patch("src.services.prometheus_metrics.credit_deduction_retry_count")
    def test_records_retry_metrics(self, mock_retry, mock_latency, mock_amount, mock_total):
        """Test that retry metrics are recorded for attempt > 1."""
        mock_total.labels.return_value.inc = MagicMock()
        mock_amount.labels.return_value.inc = MagicMock()
        mock_retry.labels.return_value.inc = MagicMock()

        _record_credit_metrics(
            status="retried",
            cost=0.05,
            endpoint="/v1/chat/completions",
            is_streaming=True,
            latency_seconds=None,
            attempt_number=2,
        )

        mock_retry.labels.assert_called_once_with(
            attempt_number="2",
            endpoint="/v1/chat/completions",
        )


class TestRecordMissedDeduction:
    """Test metrics recording for missed credit deductions."""

    @patch("src.services.prometheus_metrics.missed_credit_deductions_usd")
    def test_records_missed_deduction(self, mock_metric):
        """Test that missed deduction is recorded."""
        mock_metric.labels.return_value.inc = MagicMock()

        _record_missed_deduction(cost=0.10, reason="retry_exhausted")

        mock_metric.labels.assert_called_once_with(reason="retry_exhausted")
        mock_metric.labels.return_value.inc.assert_called_once_with(0.10)

    @patch("src.services.prometheus_metrics.missed_credit_deductions_usd")
    def test_skips_zero_cost(self, mock_metric):
        """Test that zero cost doesn't record metric."""
        _record_missed_deduction(cost=0.0, reason="retry_exhausted")

        mock_metric.labels.assert_not_called()


class TestRecordBackgroundTaskFailure:
    """Test metrics recording for background task failures."""

    @patch("src.services.prometheus_metrics.streaming_background_task_failures")
    def test_records_failure(self, mock_metric):
        """Test that background task failure is recorded."""
        mock_metric.labels.return_value.inc = MagicMock()

        _record_background_task_failure(
            failure_type="credit_deduction",
            endpoint="/v1/chat/completions",
        )

        mock_metric.labels.assert_called_once_with(
            failure_type="credit_deduction",
            endpoint="/v1/chat/completions",
        )


class TestSendCriticalBillingAlert:
    """Test Sentry alerting for billing failures."""

    @patch("sentry_sdk.add_breadcrumb")
    @patch("src.utils.sentry_context.capture_payment_error")
    def test_sends_alert_for_significant_cost(self, mock_capture, mock_add_breadcrumb):
        """Test that Sentry alert is sent for costs >= $0.01."""
        error = RuntimeError("Database error")

        _send_critical_billing_alert(
            error=error,
            user_id=123,
            cost=0.05,
            model="gpt-4",
            endpoint="/v1/chat/completions",
            attempt_number=3,
            is_streaming=True,
        )

        mock_capture.assert_called_once()
        mock_add_breadcrumb.assert_called_once()
        call_kwargs = mock_add_breadcrumb.call_args[1]
        assert call_kwargs["category"] == "billing"
        assert "123" in call_kwargs["message"]
        assert call_kwargs["data"]["cost_usd"] == 0.05

    @patch("sentry_sdk.add_breadcrumb")
    @patch("src.utils.sentry_context.capture_payment_error")
    def test_skips_message_for_small_cost(self, mock_capture, mock_add_breadcrumb):
        """Test that Sentry breadcrumb and alert are sent for all costs."""
        error = RuntimeError("Database error")

        _send_critical_billing_alert(
            error=error,
            user_id=123,
            cost=0.001,
            model="gpt-4",
            endpoint="/v1/chat/completions",
            attempt_number=3,
            is_streaming=True,
        )

        # capture_payment_error should be called for all costs
        mock_capture.assert_called_once()
        # Breadcrumb should be added for all costs
        mock_add_breadcrumb.assert_called_once()


class TestHandleCreditsAndUsage:
    """Test the main credit handling function."""

    @pytest.mark.asyncio
    @patch("src.services.pricing.calculate_cost_async")
    @patch("src.db.trials.track_trial_usage_for_key")
    @patch("src.db.users.log_api_usage_transaction")
    @patch("src.services.credit_handler._record_credit_metrics")
    async def test_trial_user_no_deduction(
        self,
        mock_metrics,
        mock_log_tx,
        mock_track_trial,
        mock_calc_cost,
        mock_trial_user,
        mock_trial_active,
    ):
        """Test that trial users don't get credits deducted."""
        mock_calc_cost.return_value = 0.05

        cost = await handle_credits_and_usage(
            api_key="test_key",
            user=mock_trial_user,
            model="gpt-4",
            trial=mock_trial_active,
            total_tokens=1000,
            prompt_tokens=500,
            completion_tokens=500,
            elapsed_ms=1000,
        )

        assert cost == 0.05
        mock_track_trial.assert_called_once()
        mock_log_tx.assert_called_once()
        # Verify the transaction was logged with $0 cost
        call_args = mock_log_tx.call_args
        assert call_args[0][1] == 0.0  # Cost should be 0

    @pytest.mark.asyncio
    @patch("src.services.pricing.calculate_cost_async")
    @patch("src.db.users.deduct_credits")
    @patch("src.db.users.record_usage")
    @patch("src.db.rate_limits.update_rate_limit_usage")
    @patch("src.services.credit_handler._record_credit_metrics")
    async def test_paid_user_deduction_success(
        self,
        mock_metrics,
        mock_rate_limit,
        mock_record_usage,
        mock_deduct,
        mock_calc_cost,
        mock_user,
        mock_trial_inactive,
    ):
        """Test that paid users get credits deducted successfully."""
        mock_calc_cost.return_value = 0.05

        cost = await handle_credits_and_usage(
            api_key="test_key",
            user=mock_user,
            model="gpt-4",
            trial=mock_trial_inactive,
            total_tokens=1000,
            prompt_tokens=500,
            completion_tokens=500,
            elapsed_ms=1000,
        )

        assert cost == 0.05
        mock_deduct.assert_called_once()
        mock_record_usage.assert_called_once()
        mock_rate_limit.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.services.pricing.calculate_cost_async")
    @patch("src.db.users.deduct_credits")
    @patch("src.db.users.record_usage")
    @patch("src.db.rate_limits.update_rate_limit_usage")
    @patch("src.services.credit_handler._record_credit_metrics")
    @patch("asyncio.sleep")
    async def test_retry_on_transient_failure(
        self,
        mock_sleep,
        mock_metrics,
        mock_rate_limit,
        mock_record_usage,
        mock_deduct,
        mock_calc_cost,
        mock_user,
        mock_trial_inactive,
    ):
        """Test that credit deduction retries on transient failure."""
        mock_calc_cost.return_value = 0.05
        # Fail first attempt, succeed on second
        mock_deduct.side_effect = [RuntimeError("DB connection error"), None]

        cost = await handle_credits_and_usage(
            api_key="test_key",
            user=mock_user,
            model="gpt-4",
            trial=mock_trial_inactive,
            total_tokens=1000,
            prompt_tokens=500,
            completion_tokens=500,
            elapsed_ms=1000,
        )

        assert cost == 0.05
        assert mock_deduct.call_count == 2
        mock_sleep.assert_called_once_with(CREDIT_DEDUCTION_RETRY_DELAYS[0])

    @pytest.mark.asyncio
    @patch("src.services.pricing.calculate_cost_async")
    @patch("src.db.users.deduct_credits")
    @patch("src.services.credit_handler._record_credit_metrics")
    @patch("src.utils.sentry_context.capture_payment_error")
    async def test_no_retry_on_validation_error(
        self,
        mock_capture,
        mock_metrics,
        mock_deduct,
        mock_calc_cost,
        mock_user,
        mock_trial_inactive,
    ):
        """Test that ValueError (insufficient credits) doesn't retry."""
        mock_calc_cost.return_value = 0.05
        mock_deduct.side_effect = ValueError("Insufficient credits")

        with pytest.raises(ValueError, match="Insufficient credits"):
            await handle_credits_and_usage(
                api_key="test_key",
                user=mock_user,
                model="gpt-4",
                trial=mock_trial_inactive,
                total_tokens=1000,
                prompt_tokens=500,
                completion_tokens=500,
                elapsed_ms=1000,
            )

        # Should only try once for validation errors
        assert mock_deduct.call_count == 1

    @pytest.mark.asyncio
    @patch("src.services.pricing.calculate_cost_async")
    @patch("src.db.users.deduct_credits")
    @patch("src.services.credit_handler._record_credit_metrics")
    @patch("src.services.credit_handler._record_missed_deduction")
    @patch("src.services.credit_handler._send_critical_billing_alert")
    @patch("asyncio.sleep")
    async def test_all_retries_exhausted(
        self,
        mock_sleep,
        mock_alert,
        mock_missed,
        mock_metrics,
        mock_deduct,
        mock_calc_cost,
        mock_user,
        mock_trial_inactive,
    ):
        """Test behavior when all retries are exhausted."""
        mock_calc_cost.return_value = 0.05
        mock_deduct.side_effect = RuntimeError("Persistent DB error")

        with pytest.raises(RuntimeError, match="Credit deduction failed after"):
            await handle_credits_and_usage(
                api_key="test_key",
                user=mock_user,
                model="gpt-4",
                trial=mock_trial_inactive,
                total_tokens=1000,
                prompt_tokens=500,
                completion_tokens=500,
                elapsed_ms=1000,
            )

        assert mock_deduct.call_count == CREDIT_DEDUCTION_MAX_RETRIES
        mock_missed.assert_called_once_with(0.05, "retry_exhausted")
        mock_alert.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.services.pricing.calculate_cost_async")
    @patch("src.db.trials.track_trial_usage_for_key")
    @patch("src.db.users.deduct_credits")
    @patch("src.db.users.record_usage")
    @patch("src.db.rate_limits.update_rate_limit_usage")
    @patch("src.services.credit_handler._record_credit_metrics")
    async def test_trial_override_for_paid_user_with_stale_flag(
        self,
        mock_metrics,
        mock_rate_limit,
        mock_record_usage,
        mock_deduct,
        mock_track_trial,
        mock_calc_cost,
        mock_user,
    ):
        """Test that paid users with stale is_trial flag still get charged."""
        mock_calc_cost.return_value = 0.05
        # User has active subscription but stale trial flag
        stale_trial = {"is_trial": True, "is_expired": False}

        cost = await handle_credits_and_usage(
            api_key="test_key",
            user=mock_user,  # Has active subscription
            model="gpt-4",
            trial=stale_trial,  # Stale trial flag
            total_tokens=1000,
            prompt_tokens=500,
            completion_tokens=500,
            elapsed_ms=1000,
        )

        assert cost == 0.05
        # Should NOT track trial usage
        mock_track_trial.assert_not_called()
        # SHOULD deduct credits
        mock_deduct.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.services.pricing.calculate_cost_async")
    @patch("src.db.users.deduct_credits")
    @patch("src.db.users.record_usage")
    @patch("src.db.rate_limits.update_rate_limit_usage")
    @patch("src.services.credit_handler._record_credit_metrics")
    async def test_no_duplicate_deduction_when_usage_logging_fails(
        self,
        mock_metrics,
        mock_rate_limit,
        mock_record_usage,
        mock_deduct,
        mock_calc_cost,
        mock_user,
        mock_trial_inactive,
    ):
        """Test that deduct_credits is only called once even if record_usage fails.

        This is a critical test for the fix: if deduct_credits succeeds but
        record_usage fails, we should NOT retry the deduction (which would
        cause duplicate charges).
        """
        mock_calc_cost.return_value = 0.05
        # record_usage fails, but deduct_credits succeeds
        mock_record_usage.side_effect = RuntimeError("DB connection error")

        # Should complete successfully (usage logging failure is logged, not raised)
        cost = await handle_credits_and_usage(
            api_key="test_key",
            user=mock_user,
            model="gpt-4",
            trial=mock_trial_inactive,
            total_tokens=1000,
            prompt_tokens=500,
            completion_tokens=500,
            elapsed_ms=1000,
        )

        assert cost == 0.05
        # CRITICAL: deduct_credits should only be called ONCE
        mock_deduct.assert_called_once()
        # record_usage was attempted
        mock_record_usage.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.services.pricing.calculate_cost_async")
    @patch("src.db.users.deduct_credits")
    @patch("src.db.users.record_usage")
    @patch("src.db.rate_limits.update_rate_limit_usage")
    @patch("src.services.credit_handler._record_credit_metrics")
    async def test_no_duplicate_deduction_when_rate_limit_update_fails(
        self,
        mock_metrics,
        mock_rate_limit,
        mock_record_usage,
        mock_deduct,
        mock_calc_cost,
        mock_user,
        mock_trial_inactive,
    ):
        """Test that deduct_credits is only called once even if rate limit update fails."""
        mock_calc_cost.return_value = 0.05
        # rate limit update fails, but deduct_credits succeeds
        mock_rate_limit.side_effect = RuntimeError("Redis connection error")

        # Should complete successfully
        cost = await handle_credits_and_usage(
            api_key="test_key",
            user=mock_user,
            model="gpt-4",
            trial=mock_trial_inactive,
            total_tokens=1000,
            prompt_tokens=500,
            completion_tokens=500,
            elapsed_ms=1000,
        )

        assert cost == 0.05
        # CRITICAL: deduct_credits should only be called ONCE
        mock_deduct.assert_called_once()
        # rate limit update was attempted
        mock_rate_limit.assert_called_once()


class TestHandleCreditsAndUsageWithFallback:
    """Test the fallback wrapper for streaming requests."""

    @pytest.mark.asyncio
    @patch("src.services.credit_handler.handle_credits_and_usage")
    async def test_returns_success_on_normal_operation(
        self, mock_handler, mock_user, mock_trial_inactive
    ):
        """Test successful operation returns (cost, True)."""
        mock_handler.return_value = 0.05

        cost, success = await handle_credits_and_usage_with_fallback(
            api_key="test_key",
            user=mock_user,
            model="gpt-4",
            trial=mock_trial_inactive,
            total_tokens=1000,
            prompt_tokens=500,
            completion_tokens=500,
            elapsed_ms=1000,
        )

        assert cost == 0.05
        assert success is True

    @pytest.mark.asyncio
    @patch("src.services.credit_handler.handle_credits_and_usage")
    @patch("src.services.pricing.calculate_cost_async")
    @patch("src.services.credit_handler._record_background_task_failure")
    @patch("src.services.credit_handler._record_missed_deduction")
    @patch("src.services.credit_handler._log_failed_deduction_for_reconciliation")
    async def test_returns_failure_and_logs_on_error(
        self,
        mock_log_recon,
        mock_missed,
        mock_bg_fail,
        mock_calc_cost,
        mock_handler,
        mock_user,
        mock_trial_inactive,
    ):
        """Test that failures return (cost, False) and log for reconciliation."""
        mock_handler.side_effect = RuntimeError("All retries exhausted")
        mock_calc_cost.return_value = 0.05

        cost, success = await handle_credits_and_usage_with_fallback(
            api_key="test_key",
            user=mock_user,
            model="gpt-4",
            trial=mock_trial_inactive,
            total_tokens=1000,
            prompt_tokens=500,
            completion_tokens=500,
            elapsed_ms=1000,
        )

        assert cost == 0.05
        assert success is False
        mock_bg_fail.assert_called_once()
        mock_missed.assert_called_once()
        mock_log_recon.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.services.credit_handler.handle_credits_and_usage")
    @patch("src.services.pricing.calculate_cost_async")
    @patch("src.services.credit_handler._record_background_task_failure")
    @patch("src.services.credit_handler._record_missed_deduction")
    @patch("src.services.credit_handler._log_failed_deduction_for_reconciliation")
    async def test_uses_fallback_cost_if_pricing_fails(
        self,
        mock_log_recon,
        mock_missed,
        mock_bg_fail,
        mock_calc_cost,
        mock_handler,
        mock_user,
        mock_trial_inactive,
    ):
        """Test that fallback cost estimation is used if pricing lookup fails."""
        mock_handler.side_effect = RuntimeError("All retries exhausted")
        mock_calc_cost.side_effect = RuntimeError("Pricing API down")

        cost, success = await handle_credits_and_usage_with_fallback(
            api_key="test_key",
            user=mock_user,
            model="gpt-4",
            trial=mock_trial_inactive,
            total_tokens=1000,
            prompt_tokens=500,
            completion_tokens=500,
            elapsed_ms=1000,
        )

        # Fallback cost: (500 + 500) * 0.00002 = 0.02
        assert cost == 0.02
        assert success is False


class TestRetryConfiguration:
    """Test that retry configuration is correct."""

    def test_max_retries_is_reasonable(self):
        """Test that max retries is between 1 and 5."""
        assert 1 <= CREDIT_DEDUCTION_MAX_RETRIES <= 5

    def test_retry_delays_are_exponential(self):
        """Test that retry delays increase."""
        for i in range(len(CREDIT_DEDUCTION_RETRY_DELAYS) - 1):
            assert CREDIT_DEDUCTION_RETRY_DELAYS[i] < CREDIT_DEDUCTION_RETRY_DELAYS[i + 1]

    def test_retry_delays_match_max_retries(self):
        """Test that we have enough delay values for all retries."""
        assert len(CREDIT_DEDUCTION_RETRY_DELAYS) >= CREDIT_DEDUCTION_MAX_RETRIES - 1
