"""Tests for src.services.delegation.cip8 -- the CIP-30 signData / CIP-8
COSE_Sign1 proof that a user controls a Cardano stake address.

Every proof here is produced with a real Ed25519 keypair, the way a wallet
would. The COSE_Key and protected-header prefixes are hand-written CBOR
bytes (RFC 8949), not produced by the module's own encoder, so a bug in
that encoder cannot hide behind a test that only round-trips through it.
"""

from __future__ import annotations

import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.services.delegation import cip8
from src.services.delegation.cip8 import Cip8VerificationError, verify_stake_signature

MESSAGE = "Link this Cardano stake address to Gatewayz account 42.\nNonce: abc123"


def _raw_public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _cose_key_hex(public: bytes) -> str:
    # {1: 1 (OKP), 3: -8 (EdDSA), -1: 6 (Ed25519), -2: h'<32 bytes>'}
    return ("a4" "01" "01" "03" "27" "20" "06" "21" "5820") + public.hex()


def _protected(address_bytes: bytes) -> bytes:
    # {1: -8, "address": h'<29 bytes>'}
    return bytes.fromhex("a2" "01" "27" "67") + b"address" + bytes([0x58, 29]) + address_bytes


def _sign1_hex(
    private: Ed25519PrivateKey,
    address_bytes: bytes,
    payload: bytes,
    hashed: bool = False,
    protected: bytes | None = None,
    tagged: bool = False,
) -> str:
    protected = protected if protected is not None else _protected(address_bytes)
    # Sig_structure = ["Signature1", protected, h'', payload], hand-encoded.
    sig_structure = (
        bytes([0x84, 0x6A])
        + b"Signature1"
        + bytes([0x58, len(protected)])
        + protected
        + b"\x40"
        + _bstr(payload)
    )
    signature = private.sign(sig_structure)
    unprotected = bytes.fromhex("a1" "66") + b"hashed" + (b"\xf5" if hashed else b"\xf4")
    body = (
        b"\x84"
        + bytes([0x58, len(protected)])
        + protected
        + unprotected
        + _bstr(payload)
        + bytes([0x58, 64])
        + signature
    )
    return ((b"\xd2" if tagged else b"") + body).hex()


def _bstr(data: bytes) -> bytes:
    if len(data) < 24:
        return bytes([0x40 | len(data)]) + data
    if len(data) < 256:
        return bytes([0x58, len(data)]) + data
    return bytes([0x59]) + len(data).to_bytes(2, "big") + data


@pytest.fixture
def wallet():
    private = Ed25519PrivateKey.generate()
    public = _raw_public(private)
    address = cip8.stake_address_from_public_key(public)
    raw = cip8.parse_stake_address(address)
    return private, public, address, raw


class TestBech32:
    def test_known_cip19_reward_address_vector(self):
        # CIP-19 test vector: reward address, key hash, mainnet.
        address = "stake1uyehkck0lajq8gr28t9uxnuvgcqrc6070x3k9r8048z8y5gh6ffgw"
        hrp, raw = cip8.bech32_decode(address)
        assert hrp == "stake"
        assert raw.hex() == "e1337b62cfff6403a06a3acbc34f8c46003c69fe79a3628cefa9c47251"
        assert cip8.bech32_encode(hrp, raw) == address

    def test_bad_checksum_is_rejected(self):
        bad = "stake1uyehkck0lajq8gr28t9uxnuvgcqrc6070x3k9r8048z8y5gh6ffgx"
        with pytest.raises(ValueError):
            cip8.bech32_decode(bad)

    def test_mixed_case_is_rejected(self):
        with pytest.raises(ValueError):
            cip8.bech32_decode("Stake1uyehkck0lajq8gr28t9uxnuvgcqrc6070x3k9r8048z8y5gh6ffgw")

    def test_derived_address_matches_blake2b_224_of_key(self, wallet):
        _, public, _, raw = wallet
        assert raw[0] == 0xE1
        assert raw[1:] == hashlib.blake2b(public, digest_size=28).digest()


class TestStakeAddressParsing:
    def test_script_stake_address_cannot_prove_ownership(self):
        raw = bytes([0xF1]) + b"\x01" * 28
        address = cip8.bech32_encode("stake", raw)
        with pytest.raises(Cip8VerificationError) as exc:
            cip8.parse_stake_address(address)
        assert exc.value.code == "script_stake_address_unsupported"

    def test_payment_address_is_not_a_stake_address(self):
        # Header 0x61 = enterprise (payment) address, mainnet.
        address = cip8.bech32_encode("stake", bytes([0x61]) + b"\x02" * 28)
        with pytest.raises(Cip8VerificationError) as exc:
            cip8.parse_stake_address(address)
        assert exc.value.code == "not_a_stake_address"

    def test_addr_prefix_is_rejected(self):
        address = cip8.bech32_encode("addr", bytes([0xE1]) + b"\x02" * 28)
        with pytest.raises(Cip8VerificationError) as exc:
            cip8.parse_stake_address(address)
        assert exc.value.code == "not_a_stake_address"

    def test_testnet_rejected_unless_allowed(self):
        address = cip8.bech32_encode("stake_test", bytes([0xE0]) + b"\x03" * 28)
        with pytest.raises(Cip8VerificationError) as exc:
            cip8.parse_stake_address(address)
        assert exc.value.code == "testnet_not_allowed"
        assert cip8.parse_stake_address(address, allow_testnet=True)[0] == 0xE0

    def test_network_nibble_must_match_prefix(self):
        # A "stake1" prefix carrying a testnet (0) network nibble.
        address = cip8.bech32_encode("stake", bytes([0xE0]) + b"\x03" * 28)
        with pytest.raises(Cip8VerificationError) as exc:
            cip8.parse_stake_address(address)
        assert exc.value.code == "network_mismatch"


class TestValidProof:
    def test_valid_signature_verifies(self, wallet):
        private, public, address, raw = wallet
        sig = _sign1_hex(private, raw, MESSAGE.encode())
        verify_stake_signature(address, sig, _cose_key_hex(public), MESSAGE)

    def test_tagged_cose_sign1_verifies(self, wallet):
        private, public, address, raw = wallet
        sig = _sign1_hex(private, raw, MESSAGE.encode(), tagged=True)
        verify_stake_signature(address, sig, _cose_key_hex(public), MESSAGE)

    def test_hashed_payload_verifies_against_blake2b_224_of_message(self, wallet):
        private, public, address, raw = wallet
        digest = hashlib.blake2b(MESSAGE.encode(), digest_size=28).digest()
        sig = _sign1_hex(private, raw, digest, hashed=True)
        verify_stake_signature(address, sig, _cose_key_hex(public), MESSAGE)

    def test_long_message_uses_two_byte_length(self, wallet):
        private, public, address, raw = wallet
        message = "x" * 300
        sig = _sign1_hex(private, raw, message.encode())
        verify_stake_signature(address, sig, _cose_key_hex(public), message)

    def test_encoder_agrees_with_hand_written_sig_structure(self):
        protected = _protected(b"\xe1" + b"\x00" * 28)
        hand = bytes([0x84, 0x6A]) + b"Signature1" + bytes([0x58, len(protected)]) + protected
        hand += b"\x40" + b"\x42hi"
        assert cip8.cbor_encode(["Signature1", protected, b"", b"hi"]) == hand


class TestRejections:
    def _code(self, *args, **kwargs) -> str:
        with pytest.raises(Cip8VerificationError) as exc:
            verify_stake_signature(*args, **kwargs)
        return exc.value.code

    def test_tampered_signature(self, wallet):
        private, public, address, raw = wallet
        sig = bytearray.fromhex(_sign1_hex(private, raw, MESSAGE.encode()))
        sig[-1] ^= 0x01
        assert self._code(address, sig.hex(), _cose_key_hex(public), MESSAGE) == (
            "invalid_signature"
        )

    def test_different_message_than_issued(self, wallet):
        private, public, address, raw = wallet
        sig = _sign1_hex(private, raw, b"some other dapp's message")
        assert self._code(address, sig, _cose_key_hex(public), MESSAGE) == "payload_mismatch"

    def test_wrong_key_for_the_address(self, wallet):
        # Attacker signs correctly with THEIR key but claims the victim's
        # address. The signature is valid; the key hash is not the address.
        _, _, victim_address, victim_raw = wallet
        attacker = Ed25519PrivateKey.generate()
        sig = _sign1_hex(attacker, victim_raw, MESSAGE.encode())
        code = self._code(victim_address, sig, _cose_key_hex(_raw_public(attacker)), MESSAGE)
        assert code == "key_address_mismatch"

    def test_key_swapped_after_signing(self, wallet):
        private, _, address, raw = wallet
        sig = _sign1_hex(private, raw, MESSAGE.encode())
        other = _raw_public(Ed25519PrivateKey.generate())
        assert self._code(address, sig, _cose_key_hex(other), MESSAGE) == "invalid_signature"

    def test_protected_address_header_must_be_this_address(self, wallet):
        private, public, address, _ = wallet
        sig = _sign1_hex(private, b"\xe1" + b"\x09" * 28, MESSAGE.encode())
        assert self._code(address, sig, _cose_key_hex(public), MESSAGE) == "address_mismatch"

    def test_wrong_algorithm(self, wallet):
        private, public, address, raw = wallet
        protected = bytes.fromhex("a2" "01" "26" "67") + b"address" + bytes([0x58, 29]) + raw
        sig = _sign1_hex(private, raw, MESSAGE.encode(), protected=protected)
        assert self._code(address, sig, _cose_key_hex(public), MESSAGE) == ("unsupported_algorithm")

    def test_testnet_address_rejected_by_default(self):
        private = Ed25519PrivateKey.generate()
        public = _raw_public(private)
        address = cip8.stake_address_from_public_key(public, testnet=True)
        raw = cip8.parse_stake_address(address, allow_testnet=True)
        sig = _sign1_hex(private, raw, MESSAGE.encode())
        assert self._code(address, sig, _cose_key_hex(public), MESSAGE) == "testnet_not_allowed"
        verify_stake_signature(address, sig, _cose_key_hex(public), MESSAGE, allow_testnet=True)

    def test_non_ed25519_key(self, wallet):
        private, public, address, raw = wallet
        sig = _sign1_hex(private, raw, MESSAGE.encode())
        bad_key = ("a4" "01" "02" "03" "27" "20" "06" "21" "5820") + public.hex()  # kty EC2
        assert self._code(address, sig, bad_key, MESSAGE) == "unsupported_key_type"

    def test_garbage_and_trailing_bytes(self, wallet):
        private, public, address, raw = wallet
        sig = _sign1_hex(private, raw, MESSAGE.encode())
        assert self._code(address, "zz", _cose_key_hex(public), MESSAGE) == "invalid_cose_sign1"
        assert self._code(address, sig + "00", _cose_key_hex(public), MESSAGE) == (
            "invalid_cose_sign1"
        )
        assert self._code(address, sig, "a1", MESSAGE) == "invalid_cose_key"

    def test_indefinite_length_is_rejected(self):
        with pytest.raises(ValueError):
            cip8.cbor_decode(bytes.fromhex("9f01ff"))

    def test_detached_payload_is_rejected(self, wallet):
        private, public, address, raw = wallet
        protected = _protected(raw)
        body = (
            b"\x84"
            + bytes([0x58, len(protected)])
            + protected
            + b"\xa0"
            + b"\xf6"
            + bytes([0x58, 64])
            + b"\x00" * 64
        )
        assert self._code(address, body.hex(), _cose_key_hex(public), MESSAGE) == (
            "detached_payload_unsupported"
        )
