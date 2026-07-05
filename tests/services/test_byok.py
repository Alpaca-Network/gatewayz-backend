"""Tests for BYOK (Phase 5): crypto round-trip, fee logic, key resolution.

See docs/BUSINESS_PIVOT_DIRECT_SUPPLY.md Phase 5.
"""

import importlib

import pytest
from cryptography.fernet import Fernet

from src.services.byok import byok_routing_fee, resolve_provider_key

# --- routing fee -----------------------------------------------------------


def test_fee_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BYOK_ROUTING_FEE_RATE", raising=False)
    assert byok_routing_fee(1.0) == 0.0


def test_five_percent_routing_fee(monkeypatch):
    monkeypatch.setenv("BYOK_ROUTING_FEE_RATE", "0.05")
    assert byok_routing_fee(2.0) == 0.1


def test_fee_zero_for_nonpositive_cost(monkeypatch):
    monkeypatch.setenv("BYOK_ROUTING_FEE_RATE", "0.05")
    assert byok_routing_fee(0.0) == 0.0
    assert byok_routing_fee(-5.0) == 0.0


def test_fee_rate_clamped(monkeypatch):
    monkeypatch.setenv("BYOK_ROUTING_FEE_RATE", "9.0")
    assert byok_routing_fee(1.0) == 0.5  # clamped to 0.5


def test_fee_malformed_falls_back_to_zero(monkeypatch):
    monkeypatch.setenv("BYOK_ROUTING_FEE_RATE", "abc")
    assert byok_routing_fee(1.0) == 0.0


# --- key resolution priority ----------------------------------------------


def test_byok_key_preferred_over_platform(monkeypatch):
    monkeypatch.setattr(
        "src.db.user_provider_keys.get_decrypted_provider_key",
        lambda uid, slug: "customer-key",
    )
    key, is_byok = resolve_provider_key(42, "deepinfra")
    assert key == "customer-key"
    assert is_byok is True


def test_falls_back_to_platform_key(monkeypatch):
    monkeypatch.setattr(
        "src.db.user_provider_keys.get_decrypted_provider_key",
        lambda uid, slug: None,
    )
    monkeypatch.setattr(
        "src.services.gateway_registry.get_provider_api_key",
        lambda slug: "platform-key",
    )
    key, is_byok = resolve_provider_key(42, "deepinfra")
    assert key == "platform-key"
    assert is_byok is False


def test_anonymous_user_uses_platform_key(monkeypatch):
    monkeypatch.setattr(
        "src.services.gateway_registry.get_provider_api_key",
        lambda slug: "platform-key",
    )
    key, is_byok = resolve_provider_key(None, "deepinfra")
    assert key == "platform-key"
    assert is_byok is False


# --- crypto round-trip (BYOK requires REVERSIBLE encryption) ---------------


def test_encrypt_decrypt_round_trip(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("KEY_VERSION", "1")
    monkeypatch.setenv("KEYRING_1", key)
    # reload crypto so it picks up the keyring from env
    import src.utils.crypto as crypto

    importlib.reload(crypto)
    try:
        secret = "sk-provider-abc123456789"
        token, version = crypto.encrypt_api_key(secret)
        assert token != secret  # actually encrypted
        assert crypto.decrypt_api_key(token, version) == secret
    finally:
        importlib.reload(crypto)  # restore module state for other tests


def test_decrypt_bad_token_raises(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("KEY_VERSION", "1")
    monkeypatch.setenv("KEYRING_1", key)
    import src.utils.crypto as crypto

    importlib.reload(crypto)
    try:
        with pytest.raises(ValueError):
            crypto.decrypt_api_key("not-a-valid-token", 1)
    finally:
        importlib.reload(crypto)
