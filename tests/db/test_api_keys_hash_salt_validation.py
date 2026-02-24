"""
Tests for KEY_HASH_SALT validation in API key creation.

This test module ensures that the mandatory KEY_HASH_SALT check cannot be bypassed
by encryption failures, addressing the security bug identified in PR #749.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from src.db.api_keys import create_api_key


class TestKeyHashSaltValidation:
    """Test suite for KEY_HASH_SALT validation"""

    def test_create_api_key_fails_without_key_hash_salt(self, monkeypatch):
        """Test that API key creation fails when KEY_HASH_SALT is missing"""
        # Remove KEY_HASH_SALT from environment
        monkeypatch.delenv("KEY_HASH_SALT", raising=False)

        # Mock Supabase client
        with patch("src.db.api_keys.get_supabase_client") as mock_client:
            mock_client.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = (
                []
            )

            # Attempt to create API key
            with pytest.raises(ValueError, match="KEY_HASH_SALT"):
                create_api_key(user_id=1, key_name="test_key")

    def test_create_api_key_fails_with_short_key_hash_salt(self, monkeypatch):
        """Test that API key creation fails when KEY_HASH_SALT is too short"""
        # Set KEY_HASH_SALT to less than 16 characters
        monkeypatch.setenv("KEY_HASH_SALT", "short")

        # Mock Supabase client
        with patch("src.db.api_keys.get_supabase_client") as mock_client:
            mock_client.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = (
                []
            )

            # Attempt to create API key
            with pytest.raises(ValueError, match="KEY_HASH_SALT"):
                create_api_key(user_id=1, key_name="test_key")

    def test_hash_salt_check_not_bypassed_by_encryption_failure(self, monkeypatch):
        """
        Test that KEY_HASH_SALT validation happens BEFORE encryption.

        This is the critical test for the security bug fix. Even if encryption fails,
        the KEY_HASH_SALT check should still execute and fail if the salt is missing.
        """
        # Remove KEY_HASH_SALT from environment
        monkeypatch.delenv("KEY_HASH_SALT", raising=False)

        # Also remove encryption keys to simulate encryption failure scenario
        monkeypatch.delenv("KEYRING_1", raising=False)
        monkeypatch.delenv("KEY_VERSION", raising=False)

        # Mock Supabase client
        with patch("src.db.api_keys.get_supabase_client") as mock_client:
            mock_client.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = (
                []
            )

            # Even with encryption missing, KEY_HASH_SALT validation should fail FIRST
            with pytest.raises(ValueError, match="KEY_HASH_SALT"):
                create_api_key(user_id=1, key_name="test_key")

    def test_create_api_key_succeeds_with_valid_salt_and_no_encryption(self, monkeypatch):
        """Test that API key creation succeeds with valid KEY_HASH_SALT but no encryption"""
        # Set valid KEY_HASH_SALT
        monkeypatch.setenv("KEY_HASH_SALT", "a" * 32)  # 32 character salt

        # Remove encryption keys (encryption is optional)
        monkeypatch.delenv("KEYRING_1", raising=False)
        monkeypatch.delenv("KEY_VERSION", raising=False)

        # Mock Supabase client
        with patch("src.db.api_keys.get_supabase_client") as mock_client:
            # Mock name uniqueness check
            mock_client.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = (
                []
            )

            # Mock plan entitlements check
            with patch("src.db.api_keys.check_plan_entitlements") as mock_entitlements:
                mock_entitlements.return_value = {"monthly_request_limit": 10000}

                # Mock insert
                mock_result = MagicMock()
                mock_result.data = [{"id": 123, "user_id": 1}]
                mock_client.return_value.table.return_value.insert.return_value.execute.return_value = (
                    mock_result
                )

                # Create API key
                api_key, key_id = create_api_key(user_id=1, key_name="test_key")

                # Verify key was created
                assert api_key.startswith("gw_live_")
                assert key_id == 123

    def test_create_api_key_succeeds_with_salt_and_encryption(self, monkeypatch):
        """Test that API key creation succeeds with both KEY_HASH_SALT and encryption"""
        # Set valid KEY_HASH_SALT
        monkeypatch.setenv("KEY_HASH_SALT", "a" * 32)

        # Set valid encryption keys
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        monkeypatch.setenv("KEYRING_1", key.decode())
        monkeypatch.setenv("KEY_VERSION", "1")

        # Mock Supabase client
        with patch("src.db.api_keys.get_supabase_client") as mock_client:
            # Mock name uniqueness check
            mock_client.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = (
                []
            )

            # Mock plan entitlements check
            with patch("src.db.api_keys.check_plan_entitlements") as mock_entitlements:
                mock_entitlements.return_value = {"monthly_request_limit": 10000}

                # Mock insert
                mock_result = MagicMock()
                mock_result.data = [{"id": 456, "user_id": 1}]
                mock_client.return_value.table.return_value.insert.return_value.execute.return_value = (
                    mock_result
                )

                # Create API key
                api_key, key_id = create_api_key(user_id=1, key_name="test_key_encrypted")

                # Verify key was created with encryption
                assert api_key.startswith("gw_live_")
                assert key_id == 456

                # Verify that insert was called with encrypted fields
                insert_call_args = mock_client.return_value.table.return_value.insert.call_args
                inserted_data = insert_call_args[0][0]

                # Should have encrypted fields
                assert "key_hash" in inserted_data
                assert "encrypted_key" in inserted_data
                assert "key_version" in inserted_data
                assert inserted_data["key_version"] == 1

    def test_error_message_includes_generation_command(self, monkeypatch):
        """Test that error message includes the command to generate KEY_HASH_SALT"""
        # Remove KEY_HASH_SALT
        monkeypatch.delenv("KEY_HASH_SALT", raising=False)

        # Mock Supabase client
        with patch("src.db.api_keys.get_supabase_client") as mock_client:
            mock_client.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = (
                []
            )

            # Verify error message contains helpful generation command
            with pytest.raises(ValueError) as exc_info:
                create_api_key(user_id=1, key_name="test_key")

            error_message = str(exc_info.value)
            assert "secrets.token_hex(32)" in error_message
            assert "python" in error_message.lower()

    def test_hash_validation_order_before_encryption(self, monkeypatch):
        """
        Test that hash validation happens strictly before encryption.

        This verifies the fix for the bypass vulnerability where encryption
        failures could skip hash validation.
        """
        # Set valid KEY_HASH_SALT
        monkeypatch.setenv("KEY_HASH_SALT", "b" * 32)

        # Track call order
        call_order = []

        # Mock sha256_key_hash to track when it's called
        original_sha256 = __import__(
            "src.utils.crypto", fromlist=["sha256_key_hash"]
        ).sha256_key_hash

        def tracked_sha256(plaintext):
            call_order.append("sha256_key_hash")
            return original_sha256(plaintext)

        # Mock encrypt_api_key to track when it's called and force it to fail
        def tracked_encrypt(plaintext):
            call_order.append("encrypt_api_key")
            raise RuntimeError("Encryption failed (simulated)")

        with patch("src.utils.crypto.sha256_key_hash", side_effect=tracked_sha256):
            with patch("src.utils.crypto.encrypt_api_key", side_effect=tracked_encrypt):
                with patch("src.db.api_keys.get_supabase_client") as mock_client:
                    # Mock name uniqueness check
                    mock_client.return_value.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = (
                        []
                    )

                    # Mock plan entitlements
                    with patch("src.db.api_keys.check_plan_entitlements") as mock_entitlements:
                        mock_entitlements.return_value = {"monthly_request_limit": 10000}

                        # Mock insert
                        mock_result = MagicMock()
                        mock_result.data = [{"id": 789, "user_id": 1}]
                        mock_client.return_value.table.return_value.insert.return_value.execute.return_value = (
                            mock_result
                        )

                        # Create API key - encryption will fail but hash should succeed
                        api_key, key_id = create_api_key(user_id=1, key_name="test_order")

                        # Verify sha256_key_hash was called BEFORE encrypt_api_key
                        assert call_order == ["sha256_key_hash", "encrypt_api_key"]

                        # Verify key was still created despite encryption failure
                        assert api_key.startswith("gw_live_")
                        assert key_id == 789
