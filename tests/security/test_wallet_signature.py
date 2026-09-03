"""Tests for src.security.wallet_signature -- real eth_account, no mocks
(gatewayz-backend#2249)."""

from eth_account import Account
from eth_account.messages import encode_defunct

from src.security.wallet_signature import recover_wallet_address, verify_wallet_signature


def _sign(account, message: str) -> str:
    sig = account.sign_message(encode_defunct(text=message)).signature.hex()
    return sig if sig.startswith("0x") else f"0x{sig}"


def test_verify_wallet_signature_accepts_valid_signature():
    account = Account.create()
    message = "Sign in to Gatewayz."
    signature = _sign(account, message)

    assert verify_wallet_signature(account.address, message, signature) is True


def test_verify_wallet_signature_is_case_insensitive_on_address():
    account = Account.create()
    message = "Sign in to Gatewayz."
    signature = _sign(account, message)

    assert verify_wallet_signature(account.address.lower(), message, signature) is True
    assert verify_wallet_signature(account.address.upper().replace("0X", "0x"), message, signature) is True


def test_verify_wallet_signature_rejects_wrong_signer():
    signer = Account.create()
    claimed = Account.create()
    message = "Sign in to Gatewayz."
    signature = _sign(signer, message)

    assert verify_wallet_signature(claimed.address, message, signature) is False


def test_verify_wallet_signature_rejects_tampered_message():
    account = Account.create()
    signature = _sign(account, "Sign in to Gatewayz.")

    assert verify_wallet_signature(account.address, "Sign in to Evil.", signature) is False


def test_verify_wallet_signature_rejects_garbage_signature():
    account = Account.create()

    assert verify_wallet_signature(account.address, "Sign in to Gatewayz.", "0xnotasignature") is False


def test_verify_wallet_signature_rejects_empty_signature():
    account = Account.create()

    assert verify_wallet_signature(account.address, "Sign in to Gatewayz.", "") is False


def test_recover_wallet_address_returns_the_real_signer():
    account = Account.create()
    message = "Sign in to Gatewayz."
    signature = _sign(account, message)

    recovered = recover_wallet_address(message, signature)

    assert recovered is not None
    assert recovered.lower() == account.address.lower()


def test_recover_wallet_address_returns_none_on_garbage_signature():
    assert recover_wallet_address("Sign in to Gatewayz.", "0xnotasignature") is None
