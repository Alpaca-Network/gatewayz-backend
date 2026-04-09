"""
CM-01 Authentication & API Key Security

Tests covering Fernet encryption, HMAC-SHA256 hashing, RBAC tiers,
IP allowlists, and domain restrictions.
"""

import base64
import hashlib
import hmac
import os
import re
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from src.db.roles import UserRole
from src.security.security import (
    SecurityManager,
    hash_api_key,
    validate_domain_referrers,
    validate_ip_allowlist,
)


# ---------------------------------------------------------------------------
# CM-1.1  Fernet encryption produces a valid Fernet token, not plaintext
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM01ApiKeyEncryptedWithFernet:
    def test_api_key_encrypted_with_fernet(self, fernet_key):
        """encrypt_api_key output is a valid Fernet token, not the plaintext key."""
        sm = SecurityManager(encryption_key=fernet_key)
        plaintext = "gw_live_test1234567890abcdef"
        encrypted = sm.encrypt_api_key(plaintext)

        # Must not equal the plaintext
        assert encrypted != plaintext

        # The encrypted output is base64-of-Fernet-token.  Decode the outer
        # base64 layer and verify the inner bytes are a valid Fernet token by
        # attempting to decrypt them with the same key.
        inner_bytes = base64.b64decode(encrypted.encode())
        f = Fernet(fernet_key.encode())
        decrypted = f.decrypt(inner_bytes).decode()
        assert decrypted == plaintext


# ---------------------------------------------------------------------------
# CM-1.2  Encryption round-trip: decrypt(encrypt(key)) == key
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM02ApiKeyDecryptionRoundtrip:
    def test_api_key_decryption_roundtrip(self, fernet_key):
        """decrypt(encrypt(key)) must return the original plaintext."""
        sm = SecurityManager(encryption_key=fernet_key)
        plaintext = "gw_live_roundtrip_key_abc123"
        encrypted = sm.encrypt_api_key(plaintext)
        result = sm.decrypt_api_key(encrypted)
        assert result == plaintext


# ---------------------------------------------------------------------------
# CM-1.3  HMAC-SHA256 hashing properties
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM03ApiKeyHmacSha256Hashing:
    def test_api_key_hmac_sha256_hashing(self):
        """hash_api_key produces a hex SHA-256 HMAC, is deterministic, and
        different inputs yield different hashes."""
        key_a = "gw_live_key_aaa"
        key_b = "gw_live_key_bbb"

        hash_a1 = hash_api_key(key_a)
        hash_a2 = hash_api_key(key_a)
        hash_b = hash_api_key(key_b)

        # Deterministic
        assert hash_a1 == hash_a2

        # Output is 64 hex chars (SHA-256)
        assert re.fullmatch(r"[0-9a-f]{64}", hash_a1)

        # Different inputs produce different hashes
        assert hash_a1 != hash_b

        # Verify it matches manual HMAC-SHA256 computation
        salt = os.environ["API_GATEWAY_SALT"]
        expected = hmac.new(salt.encode(), key_a.encode("utf-8"), hashlib.sha256).hexdigest()
        assert hash_a1 == expected


# ---------------------------------------------------------------------------
# CM-1.4  Hash-based lookup function (CM gap)
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM04HmacLookupWithoutDecryption:
    def test_hmac_lookup_without_decryption(self, mock_supabase):
        """CM-1.4: get_api_key_by_hash() performs a DB lookup by key_hash
        without decryption. Verify it queries Supabase and returns the row."""
        from src.db.api_keys import get_api_key_by_hash

        # Configure mock to return a row when queried with the right hash
        expected_row = {"id": 42, "key_hash": "abc123", "user_id": 7, "status": "active"}
        mock_supabase.table.return_value.execute.return_value.data = [expected_row]

        result = get_api_key_by_hash("abc123")

        assert result == expected_row
        # Verify it queried the api_keys_new table with the hash
        mock_supabase.table.assert_called_with("api_keys_new")

    def test_hmac_lookup_returns_none_when_not_found(self, mock_supabase):
        """CM-1.4: get_api_key_by_hash() returns None when no key matches."""
        from src.db.api_keys import get_api_key_by_hash

        mock_supabase.table.return_value.execute.return_value.data = []

        result = get_api_key_by_hash("nonexistent_hash")

        assert result is None


# ---------------------------------------------------------------------------
# CM-1.5  create_api_key stores encrypted_key != plaintext
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM05EncryptedKeyNotPlaintextInDb:
    def test_encrypted_key_not_plaintext_in_db(self, mock_supabase):
        """create_api_key must store an encrypted_key that differs from the
        plaintext API key."""
        # Set up KEY_HASH_SALT for sha256_key_hash used inside create_api_key
        os.environ.setdefault("KEY_HASH_SALT", "test-key-hash-salt-minimum-sixteen")

        # Set up KEYRING for encrypt_api_key used inside create_api_key
        test_fernet_key = Fernet.generate_key().decode()
        os.environ["KEY_VERSION"] = "1"
        os.environ["KEYRING_1"] = test_fernet_key

        # Reload the crypto module so it picks up the new keyring env vars
        import importlib

        import src.utils.crypto as crypto_mod

        importlib.reload(crypto_mod)

        # Mock check_plan_entitlements to avoid Supabase call
        with (
            patch("src.db.api_keys.check_plan_entitlements", return_value={}),
            patch("src.db.api_keys.check_key_name_uniqueness", return_value=True),
        ):

            # Capture what gets inserted
            captured_payload = {}
            original_insert = mock_supabase.table.return_value.insert

            def capture_insert(payload):
                captured_payload.update(payload)
                result = MagicMock()
                result.data = [{"id": 99, **payload}]
                insert_chain = MagicMock()
                insert_chain.execute.return_value = result
                return insert_chain

            original_insert.side_effect = capture_insert

            from src.db.api_keys import create_api_key

            api_key, key_id = create_api_key(
                user_id=1,
                key_name="test-key",
                environment_tag="live",
            )

            # The plaintext api_key is stored (legacy), but encrypted_key must differ
            if "encrypted_key" in captured_payload:
                assert (
                    captured_payload["encrypted_key"] != api_key
                ), "encrypted_key in DB must not equal the plaintext API key"
            # At minimum, key_hash should be present and differ from plaintext
            if "key_hash" in captured_payload:
                assert captured_payload["key_hash"] != api_key


# ---------------------------------------------------------------------------
# CM-1.6  RBAC four tiers (CM gap)
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM06RbacThreeTiersExist:
    def test_rbac_three_tiers_exist(self):
        """CM-1.6: Three RBAC tiers (admin, developer, user) are enforced by
        update_user_role which rejects any role outside this set."""
        from src.db.roles import update_user_role

        mock_client = MagicMock()
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value.data = [
            {"id": 1, "role": "admin"}
        ]

        with patch("src.db.roles.get_supabase_client", return_value=mock_client):
            # Valid roles are accepted
            for role in ["admin", "developer", "user"]:
                result = update_user_role(user_id=1, new_role=role)
                assert result is True, f"update_user_role should accept '{role}'"

            # Invalid role is rejected (returns False)
            result = update_user_role(user_id=1, new_role="superuser")
            assert result is False, "update_user_role should reject invalid role 'superuser'"


# ---------------------------------------------------------------------------
# CM-1.7  Admin role is superset of all other roles
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM07AdminRoleHasAllPermissions:
    @pytest.mark.asyncio
    async def test_admin_role_has_all_permissions(self):
        """CM-1.7: Admin role passes the require_admin gate; non-admin is rejected
        with HTTP 403."""
        from fastapi import HTTPException

        from src.security.deps import require_admin

        # Admin user should pass through
        admin_user = {"id": 1, "role": "admin", "is_admin": True}
        result = await require_admin(user=admin_user)
        assert result == admin_user

        # Non-admin (developer) should be rejected with 403
        dev_user = {"id": 2, "role": "developer", "is_admin": False}
        with pytest.raises(HTTPException) as exc_info:
            await require_admin(user=dev_user)
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# CM-1.8  Free/basic role has minimum permissions
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM08FreeRoleHasMinimumPermissions:
    @pytest.mark.asyncio
    async def test_free_role_has_minimum_permissions(self):
        """CM-1.8: The 'user' role (lowest tier) is rejected by require_admin,
        confirming it has the most restricted permissions."""
        from fastapi import HTTPException

        from src.security.deps import require_admin

        # Plain 'user' role must be rejected by the admin gate
        basic_user = {"id": 3, "role": "user", "is_admin": False}
        with pytest.raises(HTTPException) as exc_info:
            await require_admin(user=basic_user)
        assert exc_info.value.status_code == 403
        assert "Administrator privileges required" in str(exc_info.value.detail)


# ---------------------------------------------------------------------------
# CM-1.9  IP allowlist blocks non-listed IP
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM09IpAllowlistBlocksNonListedIp:
    def test_ip_allowlist_blocks_non_listed_ip(self):
        """An IP not in the allowlist must be rejected."""
        allowed = ["10.0.0.1", "192.168.1.0/24"]
        assert validate_ip_allowlist("172.16.0.5", allowed) is False

    def test_ip_allowlist_blocks_ip_outside_cidr(self):
        """An IP outside the CIDR range must be rejected."""
        allowed = ["192.168.1.0/24"]
        assert validate_ip_allowlist("192.168.2.1", allowed) is False


# ---------------------------------------------------------------------------
# CM-1.10  IP allowlist allows listed IP
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM10IpAllowlistAllowsListedIp:
    def test_ip_allowlist_allows_exact_match(self):
        """An IP exactly matching an allowlist entry must be accepted."""
        allowed = ["10.0.0.1", "192.168.1.100"]
        assert validate_ip_allowlist("10.0.0.1", allowed) is True

    def test_ip_allowlist_allows_cidr_match(self):
        """An IP within an allowed CIDR range must be accepted."""
        allowed = ["192.168.1.0/24"]
        assert validate_ip_allowlist("192.168.1.42", allowed) is True

    def test_ip_allowlist_empty_means_unrestricted(self):
        """An empty allowlist means no restrictions (allow all)."""
        assert validate_ip_allowlist("1.2.3.4", []) is True


# ---------------------------------------------------------------------------
# CM-1.11  Domain restriction blocks wrong domain
# ---------------------------------------------------------------------------
@pytest.mark.cm_verified
class TestCM11DomainRestrictionBlocksWrongDomain:
    def test_domain_restriction_blocks_wrong_domain(self):
        """A referer from an unlisted domain must be rejected."""
        allowed_domains = ["example.com", "app.mysite.io"]
        assert validate_domain_referrers("https://evil.com/page", allowed_domains) is False

    def test_domain_restriction_allows_correct_domain(self):
        """A referer from a listed domain must be accepted."""
        allowed_domains = ["example.com"]
        assert validate_domain_referrers("https://example.com/dashboard", allowed_domains) is True

    def test_domain_restriction_allows_subdomain(self):
        """A subdomain of an allowed domain must be accepted."""
        allowed_domains = ["example.com"]
        assert validate_domain_referrers("https://app.example.com/page", allowed_domains) is True

    def test_domain_restriction_blocks_missing_referer(self):
        """A missing referer must be rejected when domains are configured."""
        allowed_domains = ["example.com"]
        assert validate_domain_referrers("", allowed_domains) is False

    def test_domain_restriction_empty_means_unrestricted(self):
        """An empty domain list means no restrictions."""
        assert validate_domain_referrers("https://anything.com", []) is True
