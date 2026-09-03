"""Tests for src.utils.wallet_address (gatewayz-backend#2249)."""

import pytest

from src.utils.wallet_address import normalize_wallet_address


def test_normalize_wallet_address_lowercases_valid_address():
    addr = "0x" + "A" * 40
    assert normalize_wallet_address(addr) == "0x" + "a" * 40


def test_normalize_wallet_address_accepts_already_lowercase():
    addr = "0x" + "1" * 40
    assert normalize_wallet_address(addr) == addr


def test_normalize_wallet_address_rejects_missing_prefix():
    with pytest.raises(ValueError):
        normalize_wallet_address("1" * 42)


def test_normalize_wallet_address_rejects_wrong_length():
    with pytest.raises(ValueError):
        normalize_wallet_address("0x" + "1" * 39)


def test_normalize_wallet_address_rejects_non_hex_chars():
    with pytest.raises(ValueError):
        normalize_wallet_address("0x" + "g" * 40)


def test_normalize_wallet_address_rejects_non_string():
    with pytest.raises(ValueError):
        normalize_wallet_address(None)
