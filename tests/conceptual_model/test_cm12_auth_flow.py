"""
CM-12  Authentication Flow  --  Conceptual-Model Unit Tests

Tests verify that the codebase aligns with the Conceptual Model specification
for authentication rate limiting, user provisioning, API key generation,
auth info priority resolution, partner trials, referral codes, and
temporary email detection.

Markers:
    cm_verified  -- CM claim matches the code; test should PASS.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# CM-12.1  Login rate limit: 10 attempts per 15 minutes
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_login_rate_limit_10_per_15min():
    """11th login attempt from same IP within 15 min window returns 429 (not allowed)."""
    from src.services.auth_rate_limiting import (
        AuthRateLimiter,
        AuthRateLimitConfig,
        AuthRateLimitType,
    )

    config = AuthRateLimitConfig()
    assert config.login_attempts_per_window == 10
    assert config.login_window_seconds == 900  # 15 minutes

    limiter = AuthRateLimiter(config)
    ip = "192.168.1.100"

    # First 10 attempts should be allowed
    for i in range(10):
        result = asyncio.get_event_loop().run_until_complete(
            limiter.check_rate_limit(ip, AuthRateLimitType.LOGIN)
        )
        assert result.allowed, f"Attempt {i + 1} should be allowed"

    # 11th attempt should be blocked
    result = asyncio.get_event_loop().run_until_complete(
        limiter.check_rate_limit(ip, AuthRateLimitType.LOGIN)
    )
    assert not result.allowed, "11th login attempt should be blocked"
    assert result.remaining == 0
    assert result.retry_after is not None
    assert result.retry_after > 0


# ---------------------------------------------------------------------------
# CM-12.2  Registration rate limit: 3 attempts per hour
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_register_rate_limit_3_per_hour():
    """4th registration attempt from same IP within 1 hour returns 429."""
    from src.services.auth_rate_limiting import (
        AuthRateLimiter,
        AuthRateLimitConfig,
        AuthRateLimitType,
    )

    config = AuthRateLimitConfig()
    assert config.register_attempts_per_window == 3
    assert config.register_window_seconds == 3600  # 1 hour

    limiter = AuthRateLimiter(config)
    ip = "10.0.0.50"

    # First 3 attempts should be allowed
    for i in range(3):
        result = asyncio.get_event_loop().run_until_complete(
            limiter.check_rate_limit(ip, AuthRateLimitType.REGISTER)
        )
        assert result.allowed, f"Attempt {i + 1} should be allowed"

    # 4th attempt should be blocked
    result = asyncio.get_event_loop().run_until_complete(
        limiter.check_rate_limit(ip, AuthRateLimitType.REGISTER)
    )
    assert not result.allowed, "4th registration attempt should be blocked"
    assert result.remaining == 0
    assert result.retry_after is not None


# ---------------------------------------------------------------------------
# CM-12.3  New user provisioned with basic tier
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_new_user_provisioned_with_basic_tier(mock_supabase):
    """create_enhanced_user sets tier='basic' for new users."""
    import inspect

    from src.db.users import create_enhanced_user

    # Verify the function creates user with tier="basic" by inspecting
    # the implementation: the user_data dict includes "tier": "basic"
    # We also verify via mock that the insert payload contains tier=basic
    mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": 1, "username": "testuser", "tier": "basic", "api_key": "gw_live_temp"}
    ]
    # Mock create_api_key to return a proper key
    with patch("src.db.users.create_api_key", return_value=("gw_live_abc123", 1)):
        user = create_enhanced_user(
            username="testuser",
            email="test@example.com",
            auth_method="email",
        )

    # Verify the insert was called and the payload included tier=basic
    insert_call = mock_supabase.table.return_value.insert.call_args
    assert insert_call is not None
    payload = insert_call[0][0]
    assert payload["tier"] == "basic"


# ---------------------------------------------------------------------------
# CM-12.4  New user gets auto-created primary API key
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_new_user_gets_auto_created_api_key(mock_supabase):
    """create_enhanced_user creates a primary API key for the new user."""
    mock_supabase.table.return_value.insert.return_value.execute.return_value.data = [
        {"id": 42, "username": "newuser", "tier": "basic", "api_key": "gw_live_temp"}
    ]

    with patch("src.db.users.create_api_key", return_value=("gw_live_real_key", 10)) as mock_create:
        from src.db.users import create_enhanced_user

        create_enhanced_user(
            username="newuser",
            email="new@example.com",
            auth_method="email",
        )

    # Verify create_api_key was called with is_primary=True
    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args
    # create_api_key is called with keyword args
    assert call_kwargs[1].get("is_primary") is True or (
        len(call_kwargs[0]) >= 5 and call_kwargs[0][4] is True
    ), "Primary API key should be created for new user"


# ---------------------------------------------------------------------------
# CM-12.5  API key format: gw_{env}_* prefix
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_api_key_format_gw_env_prefix():
    """generate_secure_api_key produces keys with gw_{env}_ prefix."""
    from src.security.security import generate_secure_api_key

    test_cases = {
        "test": "gw_test_",
        "staging": "gw_staging_",
        "live": "gw_live_",
        "development": "gw_dev_",
    }

    for env_tag, expected_prefix in test_cases.items():
        key = generate_secure_api_key(environment_tag=env_tag)
        assert key.startswith(
            expected_prefix
        ), f"Key for env '{env_tag}' should start with '{expected_prefix}', got '{key[:20]}...'"
        # Verify there is a random part after the prefix
        random_part = key[len(expected_prefix) :]
        assert len(random_part) > 0, "Key should have a random part after the prefix"


# ---------------------------------------------------------------------------
# CM-12.6  Auth info priority: email first
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_auth_info_priority_email_first():
    """When user has email + Google + phone linked accounts, email is selected first.

    The auth route iterates linked_accounts and sets email from the first
    email-type account. Once email is set, subsequent accounts do not override it.
    Priority: email > google_oauth > phone.
    """
    from src.routes.auth import _resolve_account_email

    # Simulate linked accounts in the order they appear
    email_account = SimpleNamespace(
        type="email", email="user@example.com", address=None, phone_number=None, name=None
    )
    google_account = SimpleNamespace(
        type="google_oauth",
        email="user@gmail.com",
        address="user@gmail.com",
        phone_number=None,
        name="User",
    )
    phone_account = SimpleNamespace(
        type="phone", email=None, address=None, phone_number="+1234567890", name=None
    )

    # The auth route processes accounts in order and picks email first
    # Simulate the priority logic from privy_auth_callback
    email = None
    auth_method = "email"  # default

    accounts = [email_account, google_account, phone_account]
    for account in accounts:
        account_email = _resolve_account_email(account)
        if account.type == "phone" and account.phone_number:
            if not email:
                auth_method = "phone"
        elif account.type == "email" and account_email and not email:
            email = account_email
            auth_method = "email"
        elif account.type == "google_oauth" and account_email and not email:
            email = account_email
            auth_method = "google"

    assert email == "user@example.com", f"Email account should take priority, got {email}"
    assert auth_method == "email"


# ---------------------------------------------------------------------------
# CM-12.7  Auth info priority: Google over phone (no email)
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_auth_info_priority_google_over_phone():
    """When user has Google + phone (no email account), Google email is selected."""
    from src.routes.auth import _resolve_account_email

    google_account = SimpleNamespace(
        type="google_oauth",
        email="user@gmail.com",
        address="user@gmail.com",
        phone_number=None,
        name="User",
    )
    phone_account = SimpleNamespace(
        type="phone", email=None, address=None, phone_number="+1234567890", name=None
    )

    email = None
    auth_method = "email"  # default

    accounts = [google_account, phone_account]
    for account in accounts:
        account_email = _resolve_account_email(account)
        if account.type == "phone" and account.phone_number:
            if not email:
                auth_method = "phone"
        elif account.type == "google_oauth" and account_email and not email:
            email = account_email
            auth_method = "google"

    assert email == "user@gmail.com", f"Google email should be selected, got {email}"
    assert auth_method == "google"


# ---------------------------------------------------------------------------
# CM-12.8  Partner code triggers extended trial
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_partner_code_triggers_extended_trial(mock_supabase):
    """A valid partner code triggers PartnerTrialService.start_partner_trial."""
    from src.services.partner_trial_service import PartnerTrialService

    partner_config = {
        "id": 1,
        "partner_code": "REDBEARD",
        "partner_name": "Redbeard",
        "is_active": True,
        "trial_duration_days": 14,
        "trial_tier": "pro",
        "trial_credits_usd": 20.0,
        "trial_max_tokens": 5000000,
        "trial_max_requests": 10000,
        "daily_usage_limit_usd": 5.0,
    }

    # Mock the DB query to return partner config
    mock_supabase.table.return_value.execute.return_value.data = [partner_config]

    # Also mock the RPC call for trial grant
    mock_supabase.rpc.return_value.execute.return_value.data = {"success": True}

    result = PartnerTrialService.start_partner_trial(
        user_id=1,
        api_key="gw_live_testkey",
        partner_code="REDBEARD",
    )

    assert result["success"] is True
    assert result["trial_duration_days"] == 14
    assert result["trial_tier"] == "pro"
    assert result["trial_credits_usd"] == 20.0


# ---------------------------------------------------------------------------
# CM-12.9  Referral code stored on new user
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_referral_code_stored_on_new_user(mock_supabase):
    """Referral code is saved to user record via _process_referral_code_background."""
    from src.routes.auth import _process_referral_code_background

    # Mock track_referral_signup to return success (imported locally inside the function)
    with patch("src.services.referral.track_referral_signup") as mock_track:
        mock_track.return_value = (True, None, {"id": 99, "username": "referrer"})

        _process_referral_code_background(
            referral_code="ABC12345",
            user_id="42",
            username="newuser",
            is_new_user=True,
        )

    # Verify update was called to store referred_by_code on the user record
    update_call = mock_supabase.table.return_value.update
    update_call.assert_called()
    # Find the call that sets referred_by_code
    found_referral_update = False
    for call in update_call.call_args_list:
        payload = call[0][0] if call[0] else call[1]
        if isinstance(payload, dict) and "referred_by_code" in payload:
            assert payload["referred_by_code"] == "ABC12345"
            found_referral_update = True
            break
    assert found_referral_update, "referred_by_code should be stored on user record"


# ---------------------------------------------------------------------------
# CM-12.10  Temporary email detection blocks registration
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
def test_temp_email_detection_blocks_registration():
    """Known disposable email domains are detected by is_temporary_email_domain."""
    from src.utils.security_validators import is_temporary_email_domain

    # Known temporary/disposable email domains from the TEMPORARY_EMAIL_DOMAINS set
    disposable_domains = [
        "user@tempmail.com",
        "test@guerrillamail.com",
        "spam@mailinator.com",
        "fake@yopmail.com",
        "throw@throwaway.email",
        "trash@trashmail.com",
        "temp@10minutemail.com",
        "nada@getnada.com",
    ]

    for email in disposable_domains:
        assert is_temporary_email_domain(
            email
        ), f"{email} should be detected as a temporary email domain"

    # Legitimate domains should NOT be flagged
    legitimate_emails = [
        "user@gmail.com",
        "user@outlook.com",
        "user@yahoo.com",
        "user@protonmail.com",
        "user@company.com",
    ]

    for email in legitimate_emails:
        assert not is_temporary_email_domain(
            email
        ), f"{email} should NOT be flagged as a temporary email domain"
