"""Tests for src.security.siwe (gatewayz-backend#2249)."""

import re
from datetime import UTC, datetime

from eth_account import Account
from eth_utils import to_checksum_address

from src.security.siwe import build_siwe_message

_ADDRESS = "0x" + "a" * 40
_ISSUED_AT = datetime(2026, 9, 3, 12, 0, 0, tzinfo=UTC)

_MESSAGE_RE = re.compile(
    r"^(?P<domain>.+) wants you to sign in with your Ethereum account:\n"
    r"(?P<address>0x[0-9a-fA-F]{40})\n"
    r"\n"
    r"(?P<statement>.+)\n"
    r"\n"
    r"URI: (?P<uri>.+)\n"
    r"Version: 1\n"
    r"Chain ID: (?P<chain_id>\d+)\n"
    r"Nonce: (?P<nonce>[0-9a-f]+)\n"
    r"Issued At: (?P<issued_at>[\d\-T:Z]+)\n"
    r"Expiration Time: (?P<expiration_time>[\d\-T:Z]+)$"
)


def test_build_siwe_message_exact_layout():
    message = build_siwe_message(
        address=_ADDRESS,
        nonce="deadbeef",
        chain_id=43113,
        statement="Sign in to Gatewayz.",
        issued_at=_ISSUED_AT,
    )

    expected = (
        "gatewayz.ai wants you to sign in with your Ethereum account:\n"
        f"{to_checksum_address(_ADDRESS)}\n"
        "\n"
        "Sign in to Gatewayz.\n"
        "\n"
        "URI: https://gatewayz.ai\n"
        "Version: 1\n"
        "Chain ID: 43113\n"
        "Nonce: deadbeef\n"
        "Issued At: 2026-09-03T12:00:00Z\n"
        "Expiration Time: 2026-09-03T12:05:00Z"
    )
    assert message == expected


def test_build_siwe_message_matches_eip4361_field_layout_via_regex():
    message = build_siwe_message(
        address=_ADDRESS,
        nonce="cafebabe",
        chain_id=43114,
        statement="Link this wallet to Gatewayz account 7.",
        issued_at=_ISSUED_AT,
    )

    match = _MESSAGE_RE.match(message)
    assert match is not None
    groups = match.groupdict()
    assert groups["address"] == to_checksum_address(_ADDRESS)
    assert groups["chain_id"] == "43114"
    assert groups["nonce"] == "cafebabe"
    assert groups["statement"] == "Link this wallet to Gatewayz account 7."


def test_build_siwe_message_checksums_a_lowercase_address():
    message = build_siwe_message(
        address=_ADDRESS,
        nonce="n0nce",
        chain_id=43113,
        statement="Sign in to Gatewayz.",
        issued_at=_ISSUED_AT,
    )
    assert to_checksum_address(_ADDRESS) in message
    # The checksummed form must differ from the plain lower-case input for
    # this to be a meaningful assertion (all-`a` addresses checksum to
    # mixed case under EIP-55).
    assert to_checksum_address(_ADDRESS) != _ADDRESS


def test_build_siwe_message_expiration_is_300s_after_issued_at():
    message = build_siwe_message(
        address=_ADDRESS,
        nonce="n0nce",
        chain_id=43113,
        statement="Sign in to Gatewayz.",
        issued_at=_ISSUED_AT,
    )
    assert "Issued At: 2026-09-03T12:00:00Z" in message
    assert "Expiration Time: 2026-09-03T12:05:00Z" in message


def test_build_siwe_message_is_the_exact_text_recoverable_by_eth_account():
    """End-to-end sanity: the message this builder produces round-trips
    through the real eth_account signing/recovery path."""
    from eth_account.messages import encode_defunct

    account = Account.create()
    message = build_siwe_message(
        address=account.address,
        nonce="deadbeef",
        chain_id=43113,
        statement="Sign in to Gatewayz.",
        issued_at=_ISSUED_AT,
    )
    signature = account.sign_message(encode_defunct(text=message)).signature.hex()
    signature = signature if signature.startswith("0x") else f"0x{signature}"
    recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
    assert recovered.lower() == account.address.lower()
