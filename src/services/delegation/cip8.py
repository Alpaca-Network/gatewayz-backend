"""Cardano stake-key ownership proof: CIP-30 ``signData`` / CIP-8 COSE_Sign1.

A Cardano wallet proves it controls a stake address by signing a message we
issued with that address's stake key. CIP-30's ``api.signData(addr, payload)``
returns ``{signature, key}``: ``signature`` is a COSE_Sign1 (RFC 9052,
profiled by CIP-8) and ``key`` is the COSE_Key holding the Ed25519 public key.
:func:`verify_stake_signature` accepts the proof only when ALL of these hold:

1. the stake address is a bech32 ``stake1...`` key-hash reward address
   (``stake_test1...`` only when testnet is allowed), its header byte says
   "reward address, key hash" and its network nibble matches the prefix;
2. the COSE_Key is an Ed25519 OKP key;
3. the COSE_Sign1 protected header says EdDSA and its ``address`` header is
   byte-for-byte this stake address (CIP-30 always sets it);
4. the signed payload is exactly the message the server issued (or its
   blake2b-224 when the unprotected ``hashed`` flag is set, which CIP-8
   allows);
5. the Ed25519 signature verifies over the RFC 9052 ``Sig_structure``
   ``["Signature1", protected, h'', payload]``;
6. blake2b-224 of the public key equals the stake credential inside the
   address. Without this last check anyone could sign with their own key and
   claim somebody else's address.

No Cardano or CBOR library is in requirements.txt and adding one trips the CI
wheelhouse cache (see the holdings decision note), so this module carries the
two small codecs it needs -- BIP-173 bech32 and a strict definite-length CBOR
subset -- and uses ``cryptography`` (already a dependency) for Ed25519 and
``hashlib`` for blake2b. The CBOR decoder rejects anything COSE does not need:
indefinite lengths, floats, tags other than COSE_Sign1's 18, trailing bytes.
"""

from __future__ import annotations

import hashlib
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# -- errors -------------------------------------------------------------------


class Cip8VerificationError(Exception):
    """The proof is not acceptable. ``code`` is a stable machine-readable
    reason the route returns as its error detail; the message never carries
    key material."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# -- bech32 (BIP-173; Cardano uses bech32, not bech32m) -----------------------

_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_CONST = 1
# Cardano lifts BIP-173's 90-character limit; a stake address is 59 chars
# (64 on testnet). Anything far past that is not an address.
_BECH32_MAX_LEN = 130


def _polymod(values: list[int]) -> int:
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for value in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            chk ^= generator[i] if ((top >> i) & 1) else 0
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convert_bits(data: list[int], from_bits: int, to_bits: int, pad: bool) -> list[int] | None:
    acc = 0
    bits = 0
    out: list[int] = []
    maxv = (1 << to_bits) - 1
    for value in data:
        if value < 0 or value >> from_bits:
            return None
        acc = (acc << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            out.append((acc >> bits) & maxv)
    if pad:
        if bits:
            out.append((acc << (to_bits - bits)) & maxv)
    elif bits >= from_bits or ((acc << (to_bits - bits)) & maxv):
        return None
    return out


def bech32_decode(text: str) -> tuple[str, bytes]:
    """``(hrp, payload bytes)`` for a bech32 string. Raises ValueError on a
    bad checksum, mixed case, a bad character, or bad padding."""
    if not isinstance(text, str) or len(text) > _BECH32_MAX_LEN:
        raise ValueError("bech32: bad length")
    if text.lower() != text and text.upper() != text:
        raise ValueError("bech32: mixed case")
    text = text.lower()
    sep = text.rfind("1")
    if sep < 1 or sep + 7 > len(text):
        raise ValueError("bech32: bad separator position")
    hrp, rest = text[:sep], text[sep + 1 :]
    if any(ord(c) < 33 or ord(c) > 126 for c in hrp):
        raise ValueError("bech32: bad hrp")
    try:
        values = [_BECH32_CHARSET.index(c) for c in rest]
    except ValueError as exc:
        raise ValueError("bech32: bad character") from exc
    if _polymod(_hrp_expand(hrp) + values) != _BECH32_CONST:
        raise ValueError("bech32: bad checksum")
    decoded = _convert_bits(values[:-6], 5, 8, pad=False)
    if decoded is None:
        raise ValueError("bech32: bad padding")
    return hrp, bytes(decoded)


def bech32_encode(hrp: str, payload: bytes) -> str:
    """Inverse of :func:`bech32_decode` (used by tests and diagnostics)."""
    data = _convert_bits(list(payload), 8, 5, pad=True) or []
    values = _hrp_expand(hrp) + data
    polymod = _polymod(values + [0] * 6) ^ _BECH32_CONST
    checksum = [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_BECH32_CHARSET[d] for d in data + checksum)


# -- stake addresses (CIP-19) ---------------------------------------------------

_MAINNET_HRP = "stake"
_TESTNET_HRP = "stake_test"
# CIP-19 header high nibble: 0b1110 = reward address with a KEY-hash
# credential, 0b1111 = SCRIPT-hash credential. A script cannot sign
# signData, so only the key-hash form can ever prove ownership.
_REWARD_KEY_HASH = 0b1110
_REWARD_SCRIPT_HASH = 0b1111
_CREDENTIAL_LEN = 28


def parse_stake_address(stake_address: str, allow_testnet: bool = False) -> bytes:
    """The 29 raw address bytes (header + 28-byte stake key hash) of a
    bech32 stake address, after checking it is a mainnet (or, if allowed,
    testnet) key-hash reward address. Raises Cip8VerificationError."""
    try:
        hrp, raw = bech32_decode(stake_address)
    except ValueError as exc:
        raise Cip8VerificationError("invalid_stake_address") from exc

    if hrp == _TESTNET_HRP:
        if not allow_testnet:
            raise Cip8VerificationError("testnet_not_allowed")
        expected_network = 0
    elif hrp == _MAINNET_HRP:
        expected_network = 1
    else:
        raise Cip8VerificationError("not_a_stake_address")

    if len(raw) != 1 + _CREDENTIAL_LEN:
        raise Cip8VerificationError("invalid_stake_address")
    header_type, network = raw[0] >> 4, raw[0] & 0x0F
    if header_type == _REWARD_SCRIPT_HASH:
        raise Cip8VerificationError("script_stake_address_unsupported")
    if header_type != _REWARD_KEY_HASH:
        raise Cip8VerificationError("not_a_stake_address")
    if network != expected_network:
        raise Cip8VerificationError("network_mismatch")
    return raw


def stake_address_from_public_key(public_key: bytes, testnet: bool = False) -> str:
    """The stake address an Ed25519 stake public key controls (tests,
    diagnostics)."""
    header = (_REWARD_KEY_HASH << 4) | (0 if testnet else 1)
    credential = hashlib.blake2b(public_key, digest_size=_CREDENTIAL_LEN).digest()
    return bech32_encode(_TESTNET_HRP if testnet else _MAINNET_HRP, bytes([header]) + credential)


# -- strict CBOR subset (RFC 8949) ----------------------------------------------

_COSE_SIGN1_TAG = 18
_MAX_DEPTH = 8
_MAX_ITEMS = 64


class _CborError(ValueError):
    pass


def _read_argument(data: bytes, pos: int, info: int) -> tuple[int, int]:
    if info < 24:
        return info, pos
    sizes = {24: 1, 25: 2, 26: 4, 27: 8}
    size = sizes.get(info)
    if size is None:  # 28-30 reserved, 31 indefinite length
        raise _CborError("unsupported length encoding")
    if pos + size > len(data):
        raise _CborError("truncated")
    return int.from_bytes(data[pos : pos + size], "big"), pos + size


def _decode_item(data: bytes, pos: int, depth: int) -> tuple[Any, int]:
    if depth > _MAX_DEPTH:
        raise _CborError("nested too deeply")
    if pos >= len(data):
        raise _CborError("truncated")
    initial = data[pos]
    major, info = initial >> 5, initial & 0x1F
    pos += 1

    if major == 7:
        simple = {20: False, 21: True, 22: None}
        if info not in simple:
            raise _CborError("unsupported simple value")
        return simple[info], pos

    value, pos = _read_argument(data, pos, info)
    if major == 0:
        return value, pos
    if major == 1:
        return -1 - value, pos
    if major in (2, 3):
        end = pos + value
        if end > len(data):
            raise _CborError("truncated")
        chunk = data[pos:end]
        if major == 2:
            return chunk, end
        try:
            return chunk.decode("utf-8"), end
        except UnicodeDecodeError as exc:
            raise _CborError("bad utf-8") from exc
    if major == 4:
        if value > _MAX_ITEMS:
            raise _CborError("array too long")
        items = []
        for _ in range(value):
            item, pos = _decode_item(data, pos, depth + 1)
            items.append(item)
        return items, pos
    if major == 5:
        if value > _MAX_ITEMS:
            raise _CborError("map too long")
        mapping: dict[Any, Any] = {}
        for _ in range(value):
            key, pos = _decode_item(data, pos, depth + 1)
            if not isinstance(key, int | str) or isinstance(key, bool):
                raise _CborError("unsupported map key")
            if key in mapping:
                raise _CborError("duplicate map key")
            mapping[key], pos = _decode_item(data, pos, depth + 1)
        return mapping, pos
    # major == 6: a tag. COSE_Sign1 may arrive tagged 18; nothing else may.
    if value != _COSE_SIGN1_TAG or depth != 0:
        raise _CborError("unsupported tag")
    return _decode_item(data, pos, depth + 1)


def cbor_decode(data: bytes) -> Any:
    """Decode exactly one CBOR item that spans all of ``data``."""
    item, pos = _decode_item(data, 0, 0)
    if pos != len(data):
        raise _CborError("trailing bytes")
    return item


def _encode_head(major: int, value: int) -> bytes:
    if value < 24:
        return bytes([(major << 5) | value])
    for info, size in ((24, 1), (25, 2), (26, 4), (27, 8)):
        if value < 1 << (8 * size):
            return bytes([(major << 5) | info]) + value.to_bytes(size, "big")
    raise _CborError("integer too large")


def cbor_encode(value: Any) -> bytes:
    """Encode the CBOR subset above. Used to rebuild the Sig_structure the
    signature covers (never to re-encode a header we received -- the
    protected header is verified as the exact bytes the wallet signed)."""
    if value is None:
        return b"\xf6"
    if value is True:
        return b"\xf5"
    if value is False:
        return b"\xf4"
    if isinstance(value, int):
        return _encode_head(0, value) if value >= 0 else _encode_head(1, -1 - value)
    if isinstance(value, bytes):
        return _encode_head(2, len(value)) + value
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return _encode_head(3, len(raw)) + raw
    if isinstance(value, list | tuple):
        return _encode_head(4, len(value)) + b"".join(cbor_encode(v) for v in value)
    if isinstance(value, dict):
        return _encode_head(5, len(value)) + b"".join(
            cbor_encode(k) + cbor_encode(v) for k, v in value.items()
        )
    raise _CborError(f"cannot encode {type(value).__name__}")


# -- COSE -----------------------------------------------------------------------

_COSE_ALG_EDDSA = -8
_COSE_HDR_ALG = 1
_COSE_KEY_KTY = 1
_COSE_KEY_ALG = 3
_COSE_KEY_CRV = -1
_COSE_KEY_X = -2
_COSE_KTY_OKP = 1
_COSE_CRV_ED25519 = 6


def _from_hex(value: str, code: str) -> bytes:
    try:
        text = value[2:] if value.startswith(("0x", "0X")) else value
        return bytes.fromhex(text)
    except (AttributeError, ValueError) as exc:
        raise Cip8VerificationError(code) from exc


def public_key_from_cose_key(key_hex: str) -> bytes:
    """The raw 32-byte Ed25519 public key inside a COSE_Key."""
    try:
        key = cbor_decode(_from_hex(key_hex, "invalid_cose_key"))
    except _CborError as exc:
        raise Cip8VerificationError("invalid_cose_key") from exc
    if not isinstance(key, dict):
        raise Cip8VerificationError("invalid_cose_key")
    if key.get(_COSE_KEY_KTY) != _COSE_KTY_OKP or key.get(_COSE_KEY_CRV) != _COSE_CRV_ED25519:
        raise Cip8VerificationError("unsupported_key_type")
    if _COSE_KEY_ALG in key and key[_COSE_KEY_ALG] != _COSE_ALG_EDDSA:
        raise Cip8VerificationError("unsupported_key_type")
    x = key.get(_COSE_KEY_X)
    if not isinstance(x, bytes) or len(x) != 32:
        raise Cip8VerificationError("invalid_cose_key")
    return x


def verify_stake_signature(
    stake_address: str,
    signature_hex: str,
    key_hex: str,
    expected_message: str,
    allow_testnet: bool = False,
) -> None:
    """Return None iff the CIP-30 signData proof shows the holder of
    ``stake_address``'s stake key signed ``expected_message``. Raises
    :class:`Cip8VerificationError` otherwise -- see the module docstring for
    the exact checks."""
    address_bytes = parse_stake_address(stake_address, allow_testnet=allow_testnet)
    public_key = public_key_from_cose_key(key_hex)

    try:
        sign1 = cbor_decode(_from_hex(signature_hex, "invalid_cose_sign1"))
    except _CborError as exc:
        raise Cip8VerificationError("invalid_cose_sign1") from exc
    if not isinstance(sign1, list) or len(sign1) != 4:
        raise Cip8VerificationError("invalid_cose_sign1")
    protected_bytes, unprotected, payload, signature = sign1
    if (
        not isinstance(protected_bytes, bytes)
        or not isinstance(unprotected, dict)
        or not isinstance(signature, bytes)
    ):
        raise Cip8VerificationError("invalid_cose_sign1")
    if payload is None:
        # A detached payload is legal COSE but CIP-30 always attaches it, and
        # accepting one would mean trusting the client to tell us what it
        # signed.
        raise Cip8VerificationError("detached_payload_unsupported")
    if not isinstance(payload, bytes) or len(signature) != 64:
        raise Cip8VerificationError("invalid_cose_sign1")

    try:
        protected = cbor_decode(protected_bytes) if protected_bytes else {}
    except _CborError as exc:
        raise Cip8VerificationError("invalid_cose_sign1") from exc
    if not isinstance(protected, dict) or protected.get(_COSE_HDR_ALG) != _COSE_ALG_EDDSA:
        raise Cip8VerificationError("unsupported_algorithm")
    if protected.get("address") != address_bytes:
        raise Cip8VerificationError("address_mismatch")

    hashed = unprotected.get("hashed", False)
    if not isinstance(hashed, bool):
        raise Cip8VerificationError("invalid_cose_sign1")
    message_bytes = expected_message.encode("utf-8")
    expected_payload = (
        hashlib.blake2b(message_bytes, digest_size=_CREDENTIAL_LEN).digest()
        if hashed
        else message_bytes
    )
    if payload != expected_payload:
        raise Cip8VerificationError("payload_mismatch")

    sig_structure = cbor_encode(["Signature1", protected_bytes, b"", payload])
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, sig_structure)
    except (InvalidSignature, ValueError) as exc:
        raise Cip8VerificationError("invalid_signature") from exc

    credential = hashlib.blake2b(public_key, digest_size=_CREDENTIAL_LEN).digest()
    if credential != address_bytes[1:]:
        raise Cip8VerificationError("key_address_mismatch")
