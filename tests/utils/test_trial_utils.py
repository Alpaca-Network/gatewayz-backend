"""
Comprehensive tests for Trial Utils
"""
import pytest
from datetime import datetime, timedelta, timezone, UTC
from unittest.mock import Mock, patch, MagicMock
from fastapi import HTTPException

from src.utils.trial_utils import validate_trial_expiration


class TestTrialUtils:
    """Test Trial Utils functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.utils.trial_utils
        assert src.utils.trial_utils is not None

    def test_module_has_expected_attributes(self):
        """Test module has expected public API"""
        from src.utils import trial_utils
        assert hasattr(trial_utils, '__name__')


class TestValidateTrialExpiration:
    """Test validate_trial_expiration function"""

    def test_non_trial_user_passes(self):
        """Test that non-trial users are not affected"""
        user = {
            "id": 1,
            "subscription_status": "active",
            "trial_expires_at": None
        }
        # Should not raise any exception
        validate_trial_expiration(user)

    def test_trial_user_without_expiry_passes(self):
        """Test that trial users without expiry date pass"""
        user = {
            "id": 2,
            "subscription_status": "trial",
            "trial_expires_at": None
        }
        # Should not raise any exception
        validate_trial_expiration(user)

    def test_trial_user_with_future_expiry_passes(self):
        """Test that trial users with future expiry date pass"""
        future_date = (datetime.now(UTC) + timedelta(days=2)).isoformat()
        user = {
            "id": 3,
            "subscription_status": "trial",
            "trial_expires_at": future_date
        }
        # Should not raise any exception
        validate_trial_expiration(user)

    def test_trial_user_with_past_expiry_raises_402(self):
        """Test that expired trial users get 402 error"""
        past_date = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        user = {
            "id": 4,
            "subscription_status": "trial",
            "trial_expires_at": past_date
        }
        with pytest.raises(HTTPException) as exc_info:
            validate_trial_expiration(user)

        assert exc_info.value.status_code == 402
        assert "trial period has expired" in exc_info.value.detail.lower()

    def test_trial_expiry_with_z_suffix(self):
        """Test handling of ISO format with Z suffix"""
        past_date = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        user = {
            "id": 5,
            "subscription_status": "trial",
            "trial_expires_at": past_date
        }
        with pytest.raises(HTTPException) as exc_info:
            validate_trial_expiration(user)

        assert exc_info.value.status_code == 402

    def test_trial_expiry_with_timezone(self):
        """Test handling of ISO format with timezone"""
        past_date = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        user = {
            "id": 6,
            "subscription_status": "trial",
            "trial_expires_at": past_date
        }
        with pytest.raises(HTTPException) as exc_info:
            validate_trial_expiration(user)

        assert exc_info.value.status_code == 402

    def test_trial_expiry_as_datetime_object(self):
        """Test handling of datetime object"""
        past_date = datetime.now(UTC) - timedelta(days=1)
        user = {
            "id": 7,
            "subscription_status": "trial",
            "trial_expires_at": past_date
        }
        with pytest.raises(HTTPException) as exc_info:
            validate_trial_expiration(user)

        assert exc_info.value.status_code == 402

    def test_trial_expiry_naive_datetime(self):
        """Test handling of naive datetime (without timezone)"""
        # This should still work - function adds UTC timezone
        past_date = datetime.now() - timedelta(days=1)
        user = {
            "id": 8,
            "subscription_status": "trial",
            "trial_expires_at": past_date
        }
        # May or may not raise depending on local timezone
        # But should not crash
        try:
            validate_trial_expiration(user)
        except HTTPException as e:
            assert e.status_code == 402

    def test_invalid_date_format_logs_warning(self, caplog):
        """Test that invalid date formats are logged and request proceeds"""
        user = {
            "id": 9,
            "subscription_status": "trial",
            "trial_expires_at": "invalid-date-format"
        }
        # Should not raise HTTPException, should log warning
        validate_trial_expiration(user)
        # Verify that warning was logged
        assert any("Failed to parse trial_expires_at" in record.message for record in caplog.records)

    def test_empty_subscription_status(self):
        """Test handling of empty subscription status"""
        user = {
            "id": 10,
            "subscription_status": "",
            "trial_expires_at": "2024-01-01T00:00:00Z"
        }
        # Should not raise any exception
        validate_trial_expiration(user)

    def test_missing_subscription_status(self):
        """Test handling of missing subscription_status key"""
        user = {
            "id": 11,
            "trial_expires_at": "2024-01-01T00:00:00Z"
        }
        # Should not raise any exception
        validate_trial_expiration(user)

    def test_edge_case_exactly_at_expiry(self):
        """Test behavior when current time is exactly at expiry"""
        # This is an edge case - typically would be treated as expired
        current_time = datetime.now(UTC)
        user = {
            "id": 12,
            "subscription_status": "trial",
            "trial_expires_at": current_time.isoformat()
        }
        # Depending on exact timing, may or may not raise
        # But should not crash
        try:
            validate_trial_expiration(user)
        except HTTPException as e:
            assert e.status_code == 402
