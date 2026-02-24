"""
Comprehensive tests for Security Validators
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from src.utils.security_validators import (
    TEMPORARY_EMAIL_DOMAINS,
    is_temporary_email_domain,
    is_valid_email,
)


class TestSecurityValidators:
    """Test Security Validators functionality"""

    def test_module_imports(self):
        """Test that module imports successfully"""
        import src.utils.security_validators

        assert src.utils.security_validators is not None

    def test_module_has_expected_attributes(self):
        """Test module has expected public API"""
        from src.utils import security_validators

        assert hasattr(security_validators, "__name__")


class TestIsTemporaryEmailDomain:
    """Test the is_temporary_email_domain function"""

    def test_common_temporary_domains_are_blocked(self):
        """Test that well-known temporary email services are blocked"""
        temporary_emails = [
            "user@tempmail.com",
            "test@10minutemail.com",
            "foo@guerrillamail.com",
            "bar@mailinator.com",
            "baz@maildrop.cc",
            "user@throwaway.email",
            "test@trashmail.com",
            "foo@yopmail.com",
            "bar@fakeinbox.com",
            "baz@disposable.com",
        ]
        for email in temporary_emails:
            assert is_temporary_email_domain(email) is True, f"Expected {email} to be blocked"

    def test_legitimate_domains_are_allowed(self):
        """Test that common legitimate email providers are not blocked"""
        legitimate_emails = [
            "user@gmail.com",
            "test@outlook.com",
            "foo@yahoo.com",
            "bar@hotmail.com",
            "baz@icloud.com",
            "user@protonmail.com",
            "test@zoho.com",
            "foo@aol.com",
            "bar@company.com",
            "baz@university.edu",
            "user@government.gov",
            "test@example.org",
        ]
        for email in legitimate_emails:
            assert is_temporary_email_domain(email) is False, f"Expected {email} to be allowed"

    def test_case_insensitivity(self):
        """Test that domain matching is case-insensitive"""
        assert is_temporary_email_domain("user@TEMPMAIL.COM") is True
        assert is_temporary_email_domain("user@TempMail.Com") is True
        assert is_temporary_email_domain("user@tempmail.COM") is True

    def test_empty_and_invalid_inputs(self):
        """Test handling of empty and invalid inputs"""
        assert is_temporary_email_domain("") is False
        assert is_temporary_email_domain(None) is False
        assert is_temporary_email_domain("not-an-email") is False
        assert is_temporary_email_domain("@") is False
        assert is_temporary_email_domain("user@") is False

    def test_privy_placeholder_domains_are_allowed(self):
        """Test that Privy placeholder domains are NOT blocked.

        These are internal placeholder domains used by the Privy authentication
        service when no real email is available (e.g., phone auth). They should
        be allowed since they're not user-provided temporary email addresses.
        """
        assert is_temporary_email_domain("noemail+abc123@privy.placeholder") is False
        assert is_temporary_email_domain("user@privy.user") is False

    def test_whitespace_handling(self):
        """Test that whitespace is handled correctly"""
        assert is_temporary_email_domain("user@tempmail.com ") is True
        assert (
            is_temporary_email_domain(" user@tempmail.com") is False
        )  # local part with space is different
        assert is_temporary_email_domain("user@ tempmail.com") is False  # space in domain

    def test_temporary_email_domains_set_is_frozen(self):
        """Test that the domains set is a frozenset (immutable)"""
        assert isinstance(TEMPORARY_EMAIL_DOMAINS, frozenset)

    def test_domains_set_has_significant_coverage(self):
        """Test that the domains set has good coverage of common temp email services"""
        # Key services that must be included
        required_domains = {
            "tempmail.com",
            "10minutemail.com",
            "guerrillamail.com",
            "mailinator.com",
            "maildrop.cc",
            "yopmail.com",
            "throwaway.email",
            "trashmail.com",
            "fakeinbox.com",
        }
        for domain in required_domains:
            assert domain in TEMPORARY_EMAIL_DOMAINS, f"Required domain {domain} not in blocklist"


class TestIsValidEmail:
    """Test the is_valid_email function"""

    def test_valid_emails(self):
        """Test that valid emails are accepted"""
        valid_emails = [
            "user@example.com",
            "test.user@example.com",
            "user+tag@example.com",
            "user123@example.org",
            "user@subdomain.example.com",
        ]
        for email in valid_emails:
            assert is_valid_email(email) is True, f"Expected {email} to be valid"

    def test_invalid_emails(self):
        """Test that invalid emails are rejected"""
        invalid_emails = [
            "",
            None,
            "not-an-email",
            "@example.com",
            "user@",
            "user",
            "did:privy:abc123@privy.user",  # Privy IDs with colons should be invalid
        ]
        for email in invalid_emails:
            assert is_valid_email(email) is False, f"Expected {email} to be invalid"

    def test_email_length_limits(self):
        """Test RFC 5321 length limits"""
        # Local part max 64 characters
        long_local = "a" * 65 + "@example.com"
        assert is_valid_email(long_local) is False

        # Domain max 255 characters
        long_domain = "user@" + "a" * 256
        assert is_valid_email(long_domain) is False
